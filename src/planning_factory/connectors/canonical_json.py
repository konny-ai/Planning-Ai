"""Reference connector: a canonical bundle in JSON (contracts/canonical-v1.schema.json).

One file may feed several domains; each read returns only the entities of the
requested domain. Provenance is ``file#/records/<entity>/<index>`` plus the file hash.
"""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from planning_factory.canonical.entities import ENTITIES, SCHEMA_VERSION
from planning_factory.canonical.model import ReadRequest, Record, SourceBatch
from planning_factory.domain.validation import ValidationError


def _no_duplicates(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f'duplicate JSON key: {key}')
        data[key] = value
    return data


def resolve(base_dir: Path, raw) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValidationError(['option path: non-empty string required'])
    path = Path(raw)
    return path if path.is_absolute() else base_dir / path


def file_instant(path: Path, declared) -> datetime:
    if declared is None:
        return datetime.fromtimestamp(int(path.stat().st_mtime), UTC)
    parsed = datetime.fromisoformat(str(declared).replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValidationError([f'{path.name}: extracted_at needs a timezone offset'])
    return parsed.astimezone(UTC)


class CanonicalJsonConnector:
    kind = 'canonical-json'

    def __init__(self, source: str, path: Path):
        self.source, self.path = source, path
        self._loaded = None

    @classmethod
    def from_options(cls, source: str, options: dict, base_dir: Path):
        unknown = set(options) - {'path'}
        if unknown:
            raise ValidationError([f'{source}: unknown canonical-json options {sorted(unknown)}'])
        return cls(source, resolve(base_dir, options.get('path')))

    def _load(self):
        if self._loaded is None:
            raw = self.path.read_bytes()
            try:
                data = json.loads(raw.decode('utf-8'), object_pairs_hook=_no_duplicates,
                                  parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
            except ValueError as exc:
                raise ValidationError([f'{self.path.name}: invalid JSON: {exc}']) from None
            if not isinstance(data, dict) or set(data) - {'schema_version', 'extracted_at', 'records'} \
                    or data.get('schema_version') != SCHEMA_VERSION or not isinstance(data.get('records'), dict):
                raise ValidationError([f'{self.path.name}: canonical bundle v{SCHEMA_VERSION} with records object required'])
            unknown = set(data['records']) - set(ENTITIES)
            if unknown:
                raise ValidationError([f'{self.path.name}: unknown entities {sorted(unknown)}'])
            for entity, rows in data['records'].items():
                if not isinstance(rows, list):
                    raise ValidationError([f'{self.path.name}: records.{entity} must be an array'])
            self._loaded = (data, file_instant(self.path, data.get('extracted_at')), hashlib.sha256(raw).hexdigest())
        return self._loaded

    def read(self, request: ReadRequest) -> SourceBatch:
        data, extracted_at, digest = self._load()
        records = tuple(Record(entity, row, f'{self.path.name}#/records/{entity}/{index}')
                        for entity in request.entities for index, row in enumerate(data['records'].get(entity, [])))
        return SourceBatch(self.source, self.kind, request.domain, extracted_at, records, fingerprint=f'sha256:{digest}')
