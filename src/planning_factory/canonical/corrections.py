"""Local data corrections: a layer applied on top of the merged canonical records.

Sources stay read-only (ADR 004). A planner who needs to fix, add or exclude a record
does it here, with author, instant and reason; every correction can be revoked and it
survives a new extraction because it is stored apart from the source files. Corrections
are keyed by the canonical key, so they apply after identity translation and merge.
A correction whose record no longer exists is reported, never silently dropped.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import sqlite3

from planning_factory.domain.validation import ValidationError
from .entities import ENTITIES, NORMALIZERS, key_of, normalize
from .model import Issue, MergedRecord

SOURCE = 'correzioni'
ACTIONS = ('update', 'insert', 'exclude')
EDITABLE_DOMAINS = frozenset(e.domain for e in ENTITIES.values()) - {'identity'}
MAX_REASON = 500


@dataclass(frozen=True)
class Correction:
    id: int
    entity: str
    key: tuple
    action: str
    values: dict
    reason: str
    actor: str
    created_at: str

    def to_dict(self) -> dict:
        return {'id': self.id, 'entity': self.entity, 'key': list(self.key), 'action': self.action,
                'values': self.values, 'reason': self.reason, 'actor': self.actor, 'created_at': self.created_at}


def _normalize_key(spec, key) -> tuple:
    if not isinstance(key, (list, tuple)) or len(key) != len(spec.key):
        raise ValidationError([f'{spec.name}: key with fields {list(spec.key)} required'])
    result = []
    for name, value in zip(spec.key, key):
        try:
            result.append(NORMALIZERS[spec.field(name).type](value))
        except (ValueError, TypeError) as exc:
            raise ValidationError([f'{spec.name}.{name}: {exc}']) from None
    return tuple(result)


def check(entity, action, key, values) -> tuple[tuple, dict]:
    """Validate one correction request; return the normalized key and values."""
    spec = ENTITIES.get(entity)
    if spec is None or spec.domain not in EDITABLE_DOMAINS:
        raise ValidationError([f'entity {entity!r} cannot be corrected'])
    if action not in ACTIONS:
        raise ValidationError([f'action must be one of {list(ACTIONS)}'])
    if not isinstance(values, dict):
        raise ValidationError(['values must be an object'])
    if action == 'insert':
        normalized, problems = normalize(spec, values)
        if problems:
            raise ValidationError([f'{entity}: {p}' for p in problems])
        return key_of(spec, normalized), normalized
    normalized_key = _normalize_key(spec, key)
    if action == 'exclude':
        if values:
            raise ValidationError(['an exclusion carries no values'])
        return normalized_key, {}
    if not values:
        raise ValidationError(['an update needs at least one field'])
    changed = {}
    problems = []
    for name, value in values.items():
        field = spec.field(name)
        if field is None:
            problems.append(f'unknown field {name}')
        elif name in spec.key:
            problems.append(f'{name} is part of the key: exclude the record and insert a new one')
        elif value is None:
            if field.nullable:
                changed[name] = None
            elif field.required:
                problems.append(f'{name}: required, cannot be emptied')
            else:
                changed[name] = None  # optional field removed: the builder applies its documented default
        else:
            try:
                changed[name] = NORMALIZERS[field.type](value)
            except (ValueError, TypeError) as exc:
                problems.append(f'{name}: {exc}')
    if problems:
        raise ValidationError([f'{entity}: {p}' for p in problems])
    return normalized_key, changed


def _key_text(key: tuple) -> str:
    return '|'.join(str(k) for k in key)


def apply_corrections(domain: str, merged: tuple[MergedRecord, ...], corrections) -> tuple[tuple[MergedRecord, ...], list[Issue], tuple[MergedRecord, ...]]:
    """Apply the active corrections of ``domain`` in creation order.

    Returns the corrected records, the issues and the records removed by an exclusion
    (kept apart so that the data screens can still show and restore them).
    """
    mine = [c for c in corrections if ENTITIES[c.entity].domain == domain]
    if not mine:
        return merged, [], ()
    rows = {(r.entity, r.key): r for r in merged}
    excluded = {}
    issues = []
    for c in sorted(mine, key=lambda item: item.id):
        spec = ENTITIES[c.entity]
        target = (c.entity, c.key)
        tag = (SOURCE, f'#{c.id}')
        current = rows.get(target)
        if c.action == 'exclude':
            if current is None:
                issues.append(Issue('warn', 'correction_orphan', domain,
                                    f'correction #{c.id} excludes a record that the sources no longer contain',
                                    SOURCE, c.entity, _key_text(c.key)))
                continue
            excluded[target] = MergedRecord(c.entity, c.key, current.values, current.provenance + (tag,))
            del rows[target]
            continue
        if c.action == 'insert':
            if current is not None:
                issues.append(Issue('warn', 'correction_insert_exists', domain,
                                    f'correction #{c.id} adds a record that the sources now contain: its values win',
                                    SOURCE, c.entity, _key_text(c.key)))
                base = dict(current.values)
                base.update(c.values)
                provenance = current.provenance + (tag,)
            else:
                base, provenance = dict(c.values), (tag,)
            excluded.pop(target, None)
        else:
            if current is None:
                issues.append(Issue('warn', 'correction_orphan', domain,
                                    f'correction #{c.id} changes a record that the sources no longer contain',
                                    SOURCE, c.entity, _key_text(c.key)))
                continue
            base = {k: v for k, v in current.values.items() if k not in c.values or c.values[k] is not None
                    or spec.field(k).nullable}
            base.update({k: v for k, v in c.values.items() if v is not None or spec.field(k).nullable})
            provenance = current.provenance + (tag,)
        values, problems = normalize(spec, base)
        if problems:
            issues.extend(Issue('error', 'correction_invalid', domain, f'correction #{c.id}: {p}', SOURCE,
                                c.entity, _key_text(c.key)) for p in problems)
            continue
        rows[target] = MergedRecord(c.entity, c.key, values, provenance)
    ordered = tuple(sorted(rows.values(), key=lambda r: (r.entity, _sortable(r.key))))
    return ordered, issues, tuple(excluded.values())


def _sortable(key: tuple) -> tuple:
    return tuple((0, k, '') if isinstance(k, int) else (1, 0, str(k)) for k in key)


def digest(corrections) -> str:
    payload = [c.to_dict() for c in sorted(corrections, key=lambda c: c.id)]
    for item in payload:
        item.pop('created_at')
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


class CorrectionStore:
    """SQLite history of corrections. Revoking never deletes: it closes the row with author and instant."""

    def __init__(self, database):
        self.database = str(database)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS data_corrections ('
                       'id INTEGER PRIMARY KEY AUTOINCREMENT, entity TEXT NOT NULL, key TEXT NOT NULL, '
                       'action TEXT NOT NULL, payload TEXT NOT NULL, reason TEXT NOT NULL, actor TEXT NOT NULL, '
                       'created_at TEXT NOT NULL, revoked_at TEXT, revoked_by TEXT, revoke_reason TEXT, previous TEXT)')

    @staticmethod
    def _row(row) -> Correction:
        return Correction(row[0], row[1], tuple(json.loads(row[2])), row[3], json.loads(row[4]), row[5], row[6], row[7])

    def active(self) -> tuple[Correction, ...]:
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute('SELECT id, entity, key, action, payload, reason, actor, created_at FROM data_corrections '
                              'WHERE revoked_at IS NULL ORDER BY id').fetchall()
        return tuple(self._row(r) for r in rows)

    def history(self, entity=None, key=None, limit=500) -> list[dict]:
        sql = ('SELECT id, entity, key, action, payload, reason, actor, created_at, revoked_at, revoked_by, revoke_reason, '
               'previous FROM data_corrections')
        args, where = [], []
        if entity is not None:
            where.append('entity = ?')
            args.append(entity)
        if key is not None:
            where.append('key = ?')
            args.append(json.dumps(list(key), ensure_ascii=False))
        if where:
            sql += ' WHERE ' + ' AND '.join(where)
        sql += ' ORDER BY id DESC LIMIT ?'
        args.append(int(limit))
        with closing(sqlite3.connect(self.database)) as db:
            rows = db.execute(sql, args).fetchall()
        result = []
        for r in rows:
            item = self._row(r[:8]).to_dict()
            item.update({'revoked_at': r[8], 'revoked_by': r[9], 'revoke_reason': r[10], 'active': r[8] is None,
                         'previous': json.loads(r[11]) if r[11] else None})
            result.append(item)
        return result

    def candidate(self, entity, action, key, values, reason, actor) -> Correction:
        """Validated, not yet stored: the caller rebuilds with it before calling ``add``."""
        return self.candidates([{'entity': entity, 'action': action, 'key': key, 'values': values,
                                'reason': reason}], actor)[0]

    def candidates(self, items, actor) -> tuple[Correction, ...]:
        """Validate a batch without storing it; assign IDs in one read and preserve batch order."""
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        normalized = []
        for item in items:
            reason = item.get('reason')
            if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= MAX_REASON:
                raise ValidationError([f'a reason of 3..{MAX_REASON} characters is required'])
            key, values = check(item.get('entity'), item.get('action'), item.get('key'), item.get('values') or {})
            normalized.append((item['entity'], key, item['action'], values, reason.strip()))
        with closing(sqlite3.connect(self.database)) as db:
            next_id = db.execute(
                "SELECT MAX(COALESCE((SELECT MAX(id) FROM data_corrections), 0), "
                "COALESCE((SELECT seq FROM sqlite_sequence WHERE name='data_corrections'), 0)) + 1").fetchone()[0]
        return tuple(Correction(next_id + index, entity, key, action, values, reason, actor, now)
                     for index, (entity, key, action, values, reason) in enumerate(normalized))

    def add(self, correction: Correction, previous: dict | None = None) -> Correction:
        """``previous``: the values before the correction (audit only, never applied)."""
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            cursor = db.execute(
                'INSERT INTO data_corrections (entity, key, action, payload, reason, actor, created_at, previous) '
                'VALUES (?,?,?,?,?,?,?,?)',
                (correction.entity, json.dumps(list(correction.key), ensure_ascii=False), correction.action,
                 json.dumps(correction.values, ensure_ascii=False, sort_keys=True), correction.reason,
                 correction.actor, correction.created_at,
                 json.dumps(previous, ensure_ascii=False, sort_keys=True) if previous is not None else None))
            stored = cursor.lastrowid
        return Correction(stored, correction.entity, correction.key, correction.action, correction.values,
                          correction.reason, correction.actor, correction.created_at)

    def add_many(self, items) -> tuple[Correction, ...]:
        """Store a validated correction batch in one SQLite transaction."""
        items = tuple(items)
        if not items:
            return ()
        stored = []
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            for correction, previous in items:
                cursor = db.execute(
                    'INSERT INTO data_corrections (entity, key, action, payload, reason, actor, created_at, previous) '
                    'VALUES (?,?,?,?,?,?,?,?)',
                    (correction.entity, json.dumps(list(correction.key), ensure_ascii=False), correction.action,
                     json.dumps(correction.values, ensure_ascii=False, sort_keys=True), correction.reason,
                     correction.actor, correction.created_at,
                     json.dumps(previous, ensure_ascii=False, sort_keys=True) if previous is not None else None))
                if cursor.lastrowid != correction.id:
                    raise sqlite3.IntegrityError('correction history changed while validating the batch')
                stored.append(Correction(cursor.lastrowid, correction.entity, correction.key, correction.action,
                                         correction.values, correction.reason, correction.actor, correction.created_at))
        return tuple(stored)

    def revoke(self, ids, actor, reason) -> int:
        if not isinstance(reason, str) or not 3 <= len(reason.strip()) <= MAX_REASON:
            raise ValidationError([f'a reason of 3..{MAX_REASON} characters is required'])
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            count = 0
            for item in ids:
                count += db.execute('UPDATE data_corrections SET revoked_at = ?, revoked_by = ?, revoke_reason = ? '
                                    'WHERE id = ? AND revoked_at IS NULL', (now, actor, reason.strip(), int(item))).rowcount
        return count
