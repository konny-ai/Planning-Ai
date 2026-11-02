"""Generic SQL connector (#85, reusable by #84/#86): declared read-only queries, one per canonical entity.

The mapping lives in the configuration, not in code: each query aliases its columns to
canonical field names (case-insensitive, e.g. ``SELECT ORDFOR AS id, ...``). Supported
drivers: ``sqlite3`` (standard library, opened read-only) and ``pyodbc`` (DB2/AS400,
SQL Server, ...; optional dependency, opened with ``readonly=True``). The connection
string is read from an environment variable named in the binding, never from the binding.
Only single SELECT/WITH statements are accepted; the database account should be read-only too.
"""
import os
import re
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from planning_factory.canonical.entities import ENTITIES
from planning_factory.canonical.model import Issue, ReadRequest, Record, SourceBatch
from planning_factory.domain.validation import ValidationError
from .canonical_json import resolve
from .files import UNITS, _ABSENT, convert

OPTIONS = {'driver', 'database', 'connection_env', 'queries', 'timezone', 'units', 'timeout_seconds'}
_COMMENTS = re.compile(r'--[^\n]*|/\*.*?\*/', re.DOTALL)
_WRITE = re.compile(r'\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|call|exec|execute|attach|pragma)\b',
                    re.IGNORECASE)


def check_query(text, label: str) -> None:
    if not isinstance(text, str) or not text.strip():
        raise ValidationError([f'{label}: non-empty SQL text required'])
    body = _COMMENTS.sub(' ', text).strip().rstrip(';').strip()
    if ';' in body or not re.match(r'(select|with)\b', body, re.IGNORECASE) or _WRITE.search(body):
        raise ValidationError([f'{label}: a single read-only SELECT/WITH statement is required'])


class SqlConnector:
    kind = 'sql'

    def __init__(self, source: str, options: dict, base_dir: Path):
        unknown = set(options) - OPTIONS
        errors = [f'{source}: unknown sql options {sorted(unknown)}'] if unknown else []
        self.source = source
        self.driver = options.get('driver')
        self.queries = options.get('queries')
        self.timeout = options.get('timeout_seconds', 60)
        self.units = options.get('units', {})
        self.database = None
        if self.driver == 'sqlite3':
            self.database = resolve(base_dir, options.get('database'))
        elif self.driver == 'pyodbc':
            env = options.get('connection_env')
            if not isinstance(env, str) or not env.strip():
                errors.append(f'{source}.connection_env: name of the environment variable with the ODBC string required')
            self.database = env
        else:
            errors.append(f'{source}.driver: "sqlite3" or "pyodbc" required')
        if not isinstance(self.queries, dict) or not self.queries:
            errors.append(f'{source}.queries: object entity -> SELECT required')
            self.queries = {}
        for entity, query in self.queries.items():
            if entity not in ENTITIES:
                errors.append(f'{source}.queries: unknown entity {entity!r}')
                continue
            try:
                check_query(query, f'{source}.queries.{entity}')
            except ValidationError as exc:
                errors += exc.errors
        if type(self.timeout) is not int or not 1 <= self.timeout <= 3600:
            errors.append(f'{source}.timeout_seconds: integer 1..3600 required')
        if not isinstance(self.units, dict) or any(u not in UNITS for u in self.units.values()):
            errors.append(f'{source}.units: entity.field -> one of {list(UNITS)} required')
            self.units = {}
        self.zone = None
        if options.get('timezone') is not None:
            try:
                self.zone = ZoneInfo(options['timezone'])
            except (ZoneInfoNotFoundError, ValueError, TypeError):
                errors.append(f'{source}.timezone: unknown IANA timezone {options["timezone"]!r}')
        if errors:
            raise ValidationError(errors)

    @classmethod
    def from_options(cls, source: str, options: dict, base_dir: Path):
        return cls(source, options, base_dir)

    def _connect(self):
        if self.driver == 'sqlite3':
            import sqlite3
            if not Path(self.database).is_file():
                raise ValidationError([f'{self.source}: database file not found'])
            return sqlite3.connect(f'{Path(self.database).resolve().as_uri()}?mode=ro', uri=True, timeout=self.timeout)
        try:
            import pyodbc
        except ImportError:
            raise ValidationError([f'{self.source}: driver pyodbc is not installed']) from None
        dsn = os.environ.get(self.database)
        if not dsn:
            raise ValidationError([f'{self.source}: environment variable {self.database} is not set'])
        connection = pyodbc.connect(dsn, readonly=True, timeout=self.timeout)
        connection.timeout = self.timeout
        return connection

    def read(self, request: ReadRequest) -> SourceBatch:
        entities = [e for e in request.entities if e in self.queries]
        if not entities:
            return SourceBatch(self.source, self.kind, request.domain, request.now, issues=(Issue(
                'error', 'no_tables', request.domain,
                f'source bound to {request.domain} but has no query for {list(request.entities)}', self.source),))
        records, issues = [], []
        connection = self._connect()
        try:
            for entity in entities:
                spec = ENTITIES[entity]
                cursor = connection.cursor()
                cursor.execute(self.queries[entity])
                columns = [d[0] for d in cursor.description]
                fields = [spec.field(c.lower()) for c in columns]
                bad = [c for c, f in zip(columns, fields) if f is None]
                if bad or len({f.name for f in fields}) != len(columns):
                    issues.append(Issue('error', 'invalid_header', request.domain,
                                        f'query columns {bad or columns} do not map one-to-one to fields of {entity}',
                                        self.source, entity, original_key=f'sql:{entity}'))
                    continue
                for number, row in enumerate(cursor.fetchall(), 1):
                    values, failed = {}, False
                    for column, f, raw in zip(columns, fields, row):
                        try:
                            value = convert(f, raw, self.zone, UNITS[self.units.get(f'{entity}.{f.name}', 'seconds')])
                        except ValueError as exc:
                            failed = True
                            issues.append(Issue('error', 'invalid_value', request.domain, f'{column}: {exc}', self.source,
                                                entity, original_key=f'sql:{entity}:{number}'))
                            continue
                        if value is not _ABSENT:
                            values[f.name] = value
                    if not failed:
                        key = '/'.join(str(values.get(k, '')) for k in spec.key)
                        records.append(Record(entity, values, f'sql:{entity}:{number}[{key}]'))
        finally:
            connection.close()
        # Live source: the extraction instant is the read instant.
        return SourceBatch(self.source, self.kind, request.domain, request.now, tuple(records), tuple(issues))
