"""Local SQLite snapshots with explicit revisions and optimistic concurrency."""
import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from contextlib import closing
from .json_io import dumps, loads, plan_loads, scenario_digest
from planning_factory.domain.validation import ValidationError, compile_tasks
from planning_factory.engine.verification import verify_plan


class ConflictError(ValueError):
    pass


class SnapshotStore:
    def __init__(self, path):
        self.path = str(path)
        with closing(self._connect()) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS scenarios (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS plans (scenario_id TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(scenario_id, digest))')
            db.execute('CREATE TABLE IF NOT EXISTS scenario_history (scenario_id TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(scenario_id, revision))')
            db.execute('INSERT OR IGNORE INTO scenario_history SELECT id, revision, digest, payload FROM scenarios')
            db.execute('CREATE TABLE IF NOT EXISTS snapshot_plans (scenario_id TEXT NOT NULL, revision INTEGER NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(scenario_id, revision))')
            db.execute('CREATE TABLE IF NOT EXISTS snapshot_evidence (scenario_id TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL, PRIMARY KEY(scenario_id, revision))')
            db.execute('CREATE TABLE IF NOT EXISTS workbook_inputs (digest TEXT PRIMARY KEY, payload BLOB NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS snapshot_inputs (scenario_id TEXT NOT NULL, revision INTEGER NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(scenario_id, revision))')

    def _connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, scenario, *, expected_revision: int = 0) -> int:
        compile_tasks(scenario)
        payload, digest = dumps(scenario), scenario_digest(scenario)
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT revision FROM scenarios WHERE id=?', (scenario.id,)).fetchone()
            revision = current[0] if current else 0
            if revision != expected_revision:
                raise ConflictError(f'expected revision {expected_revision}, found {revision}')
            revision += 1
            db.execute('INSERT INTO scenarios VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, digest=excluded.digest, payload=excluded.payload',
                       (scenario.id, revision, digest, payload))
            db.execute('INSERT INTO scenario_history VALUES (?, ?, ?, ?)', (scenario.id, revision, digest, payload))
            return revision

    def save_snapshot(self, scenario, result, *, expected_revision: int, evidence=None, actor=None, workbook_bytes=None) -> int:
        """Save scenario revision and audited plan in one atomic transaction."""
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValidationError(['expected_revision must be a nonnegative integer'])
        if actor is not None and (not isinstance(actor, str) or not 1 <= len(actor) <= 256):
            raise ValidationError(['invalid snapshot actor'])
        workbook_digest = None
        if workbook_bytes is not None:
            if not isinstance(workbook_bytes, bytes) or not 0 < len(workbook_bytes) <= 64*1024*1024:
                raise ValidationError(['bounded workbook bytes required'])
            workbook_digest = hashlib.sha256(workbook_bytes).hexdigest()
            if not isinstance(evidence,dict) or (evidence.get('workbook') or {}).get('workbook_sha256') != workbook_digest:
                raise ValidationError(['workbook does not match the saved calculation evidence'])
        errors = verify_plan(scenario, result)
        if errors:
            raise ValidationError(list(errors))
        source, digest = dumps(scenario), scenario_digest(scenario)
        envelope = {'schema_version': 1, 'scenario_digest': digest,
                    'production_approved': False, 'evidence': evidence,
                    'recorded_by': actor, 'recorded_at': datetime.now(UTC).isoformat()}
        evidence_payload = json.dumps(envelope, sort_keys=True, separators=(',', ':'), allow_nan=False)
        if len(evidence_payload.encode()) > 8 * 1024 * 1024:
            raise ValidationError(['snapshot evidence exceeds 8 MiB'])
        evidence_digest = hashlib.sha256(evidence_payload.encode()).hexdigest()
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT revision FROM scenarios WHERE id=?', (scenario.id,)).fetchone()
            current = row[0] if row else 0
            if current != expected_revision:
                raise ConflictError(f'expected revision {expected_revision}, found {current}')
            revision = current + 1
            db.execute('INSERT INTO scenarios VALUES (?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision, digest=excluded.digest, payload=excluded.payload',
                       (scenario.id, revision, digest, source))
            db.execute('INSERT INTO scenario_history VALUES (?, ?, ?, ?)', (scenario.id, revision, digest, source))
            db.execute('INSERT INTO plans VALUES (?, ?, ?) ON CONFLICT(scenario_id, digest) DO UPDATE SET payload=excluded.payload',
                       (scenario.id, digest, dumps(result)))
            db.execute('INSERT INTO snapshot_plans VALUES (?, ?, ?)', (scenario.id, revision, dumps(result)))
            db.execute('INSERT INTO snapshot_evidence VALUES (?, ?, ?, ?)',
                       (scenario.id, revision, evidence_digest, evidence_payload))
            if workbook_digest:
                db.execute('INSERT OR IGNORE INTO workbook_inputs VALUES (?,?)',(workbook_digest,workbook_bytes))
                db.execute('INSERT INTO snapshot_inputs VALUES (?,?,?)',(scenario.id,revision,workbook_digest))
            return revision

    def load_workbook(self, scenario_id, revision):
        with closing(self._connect()) as db:
            row=db.execute('SELECT w.digest,w.payload FROM snapshot_inputs s JOIN workbook_inputs w ON w.digest=s.digest WHERE s.scenario_id=? AND s.revision=?',
                           (scenario_id,revision)).fetchone()
        if row is None:
            raise KeyError((scenario_id,revision))
        if hashlib.sha256(row[1]).hexdigest()!=row[0]:
            raise ValidationError(['archived workbook checksum mismatch'])
        return row[1]

    def load_evidence(self, scenario_id, revision):
        """Read exact archived evidence; legacy revisions explicitly lack it.

        The checksum detects accidental corruption, not malicious DB edits or
        source certification. No provenance is backfilled from today's inputs.
        """
        with closing(self._connect()) as db:
            row = db.execute('SELECT e.digest,e.payload,h.digest FROM scenario_history h LEFT JOIN snapshot_evidence e ON h.scenario_id=e.scenario_id AND h.revision=e.revision WHERE h.scenario_id=? AND h.revision=?',
                             (scenario_id, revision)).fetchone()
        if row is None:
            raise KeyError((scenario_id, revision))
        checksum, payload, digest = row
        if payload is None:
            return None
        if hashlib.sha256(payload.encode()).hexdigest() != checksum:
            raise ValidationError(['snapshot evidence checksum mismatch'])
        envelope = json.loads(payload)
        if envelope['schema_version'] != 1 or envelope['scenario_digest'] != digest or envelope['production_approved'] is not False:
            raise ValidationError(['snapshot evidence envelope mismatch'])
        return envelope

    def load_snapshot(self, scenario_id, revision):
        with closing(self._connect()) as db:
            row = db.execute('SELECT h.payload, p.payload FROM scenario_history h JOIN snapshot_plans p ON h.scenario_id=p.scenario_id AND h.revision=p.revision WHERE h.scenario_id=? AND h.revision=?',
                             (scenario_id, revision)).fetchone()
        if row is None:
            raise KeyError((scenario_id, revision))
        source, result = loads(row[0]), plan_loads(row[1])
        errors = verify_plan(source, result)
        if errors:
            raise ValidationError(list(errors))
        return source, result

    def current_revision(self, scenario_id) -> int:
        """Latest saved revision of a Scenario, 0 when none: the expected revision of the next save."""
        with closing(self._connect()) as db:
            row = db.execute('SELECT revision FROM scenarios WHERE id=?', (scenario_id,)).fetchone()
        return row[0] if row else 0

    def list_revisions(self, *, limit=100):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValidationError(['history limit must be 1..100'])
        with closing(self._connect()) as db:
            rows = db.execute('SELECT h.scenario_id,h.revision,h.digest,s.digest FROM scenario_history h LEFT JOIN snapshot_inputs s ON s.scenario_id=h.scenario_id AND s.revision=h.revision ORDER BY h.scenario_id,h.revision DESC LIMIT ?', (limit,)).fetchall()
        return [{'scenario_id': sid, 'revision': revision, 'scenario_digest': digest,
                 **({'has_workbook':True} if workbook else {})} for sid, revision, digest, workbook in rows]

    def load_revision(self, scenario_id, revision):
        with closing(self._connect()) as db:
            row = db.execute('SELECT payload FROM scenario_history WHERE scenario_id=? AND revision=?', (scenario_id, revision)).fetchone()
        if row is None:
            raise KeyError((scenario_id, revision))
        return loads(row[0])

    def load(self, scenario_id):
        with closing(self._connect()) as db:
            row = db.execute('SELECT revision, payload FROM scenarios WHERE id=?', (scenario_id,)).fetchone()
        if row is None:
            raise KeyError(scenario_id)
        return row[0], loads(row[1])

    def save_plan(self, scenario, result):
        errors = verify_plan(scenario, result)
        if errors:
            raise ValidationError(list(errors))
        with closing(self._connect()) as db, db:
            db.execute('BEGIN IMMEDIATE')
            current = db.execute('SELECT digest FROM scenarios WHERE id=?', (scenario.id,)).fetchone()
            if current is None or current[0] != result.scenario_digest:
                raise ConflictError('save the matching scenario revision before saving its plan')
            db.execute('INSERT INTO plans VALUES (?, ?, ?) ON CONFLICT(scenario_id, digest) DO UPDATE SET payload=excluded.payload',
                       (scenario.id, result.scenario_digest, dumps(result)))

    def load_plan(self, scenario_id, digest):
        with closing(self._connect()) as db:
            row = db.execute('SELECT payload FROM plans WHERE scenario_id=? AND digest=?', (scenario_id, digest)).fetchone()
        if row is None:
            raise KeyError((scenario_id, digest))
        return plan_loads(row[0])
