"""File connector (#82): one CSV or Excel table per canonical entity, read-only.

Headers are the canonical field names, or are renamed per entity with ``columns``.
An empty cell means "not provided"; the text ``null`` is an explicit null and is
accepted only by nullable fields (e.g. ``capacity`` null = infinite). Instants need an
offset unless the source declares ``timezone`` for local wall-clock values; ambiguous
or nonexistent local times (DST) are rejected, never guessed. Durations may be given in
minutes or hours with ``units`` but must convert to whole seconds.
"""
import csv
import hashlib
import io
import re
import zipfile
from datetime import UTC, date, datetime, timedelta, time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from planning_factory.canonical.entities import ENTITIES
from planning_factory.canonical.model import Issue, ReadRequest, Record, SourceBatch
from planning_factory.domain.validation import ValidationError
from .canonical_json import resolve

OPTIONS = {'path', 'tables', 'delimiter', 'encoding', 'timezone', 'columns', 'units', 'extra_columns'}
UNITS = {'seconds': 1, 'minutes': 60, 'hours': 3600}
EXCEL_EPOCH = datetime(1899, 12, 30)
MAX_XLSX_PART = 64 * 1024 * 1024
_ABSENT = object()
_M = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
_R = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _column_letters(ref: str) -> int:
    letters = re.match(r'[A-Z]+', ref).group(0)
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - 64
    return index - 1


def _letters(index: int) -> str:
    name = ''
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def read_xlsx(path: Path, sheet: str | None) -> list[tuple[int, list]]:
    """Minimal stdlib reader: values only (shared/inline strings, numbers, booleans). Styles are not read."""
    with zipfile.ZipFile(path) as z:
        def part(name):
            info = z.getinfo(name)
            if info.file_size > MAX_XLSX_PART:
                raise ValidationError([f'{path.name}: part {name} too large'])
            return ElementTree.fromstring(z.read(name))
        names = set(z.namelist())
        workbook = part('xl/workbook.xml')
        targets = {r.get('Id'): r.get('Target') for r in part('xl/_rels/workbook.xml.rels')}
        sheets = [(s.get('name'), s.get(_R + 'id')) for s in workbook.find(_M + 'sheets')]
        if not sheets:
            raise ValidationError([f'{path.name}: workbook without sheets'])
        chosen = sheets[0] if sheet is None else next((s for s in sheets if s[0] == sheet), None)
        if chosen is None:
            raise ValidationError([f'{path.name}: sheet {sheet!r} not found, available {[s[0] for s in sheets]}'])
        target = targets[chosen[1]]
        target = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        shared = []
        if 'xl/sharedStrings.xml' in names:
            shared = [''.join(t.text or '' for t in si.iter(_M + 't')) for si in part('xl/sharedStrings.xml')]
        rows = []
        for row in part(target).iter(_M + 'row'):
            cells = {}
            for c in row.iter(_M + 'c'):
                kind, v = c.get('t', 'n'), c.find(_M + 'v')
                if kind == 'inlineStr':
                    value = ''.join(t.text or '' for t in c.iter(_M + 't'))
                elif v is None or v.text is None:
                    continue
                elif kind == 's':
                    value = shared[int(v.text)]
                elif kind == 'str':
                    value = v.text
                elif kind == 'b':
                    value = v.text == '1'
                elif kind == 'e':
                    value = ValueError(f'Excel error {v.text}')
                else:
                    number = Decimal(v.text)
                    value = int(number) if number == number.to_integral_value() else number
                cells[_column_letters(c.get('r'))] = value
            if cells:
                rows.append((int(row.get('r')), [cells.get(i) for i in range(max(cells) + 1)]))
        return rows


def _local_instant(naive: datetime, zone: ZoneInfo | None) -> str:
    if zone is None:
        raise ValueError('instant without offset: declare the source timezone')
    first, second = naive.replace(tzinfo=zone, fold=0), naive.replace(tzinfo=zone, fold=1)
    if first.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValueError(f'{naive} does not exist in {zone.key} (DST change)')
    if first.utcoffset() != second.utcoffset():
        raise ValueError(f'{naive} is ambiguous in {zone.key} (DST change)')
    return first.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _serial(value) -> datetime:
    seconds = (Decimal(value) * 86400).to_integral_value()
    return EXCEL_EPOCH + timedelta(seconds=int(seconds))


def convert(field, raw, zone: ZoneInfo | None, unit: int):
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return _ABSENT
    if isinstance(raw, Exception):
        raise ValueError(str(raw))
    if isinstance(raw, str):
        raw = raw.strip()
        if raw == 'null':
            if not field.nullable:
                raise ValueError('null not allowed')
            return None
    if isinstance(raw, float):  # database drivers
        raw = Decimal(repr(raw))
    kind = field.type
    if isinstance(raw, datetime):  # database drivers: aware values keep their offset, naive ones are local
        if kind != 'instant':
            raise ValueError(f'timestamp not valid for a {kind} field')
        if raw.tzinfo is None:
            return _local_instant(raw, zone)
        return raw.isoformat()
    if isinstance(raw, (date, time)):
        if kind not in ('date', 'time') or (kind == 'date') != isinstance(raw, date):
            raise ValueError(f'{type(raw).__name__} not valid for a {kind} field')
        return raw.isoformat()
    numeric = isinstance(raw, (int, Decimal)) and not isinstance(raw, bool)
    if kind == 'decimal':
        if isinstance(raw, bool):
            raise ValueError('number required')
        return format(raw.normalize(), 'f') if isinstance(raw, Decimal) else str(raw) if numeric else raw
    if kind in ('int', 'nonneg', 'positive', 'weekday'):
        try:
            number = Decimal(raw) if isinstance(raw, str) else raw
        except InvalidOperation:
            raise ValueError(f'number required, found {raw!r}') from None
        if isinstance(number, bool) or not isinstance(number, (int, Decimal)):
            raise ValueError(f'number required, found {raw!r}')
        number = Decimal(number) * unit
        if not number.is_finite():
            raise ValueError(f'finite number required, found {raw!r}')
        if number != number.to_integral_value():
            raise ValueError(f'{raw} does not convert to whole seconds' if unit > 1 else f'integer required, found {raw}')
        return int(number)
    if kind == 'bool':
        if isinstance(raw, bool):
            return raw
        lowered = str(raw).lower()
        if lowered in ('true', '1'):
            return True
        if lowered in ('false', '0'):
            return False
        raise ValueError(f'true/false required, found {raw!r}')
    if kind == 'instant':
        if numeric:
            return _local_instant(_serial(raw), zone)
        parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return _local_instant(parsed, zone)
        return parsed.isoformat()
    if kind == 'date':
        if numeric:
            if Decimal(raw) != Decimal(raw).to_integral_value():
                raise ValueError('date cell must not contain a time')
            return _serial(raw).date().isoformat()
        return raw
    if kind == 'time':
        if numeric:
            if not 0 <= Decimal(raw) < 1:
                raise ValueError('time cell must be a fraction of a day')
            return _serial(raw).time().isoformat()
        return raw
    if numeric:  # identifiers typed as numbers in Excel, e.g. order 12345
        return str(raw)
    return raw


class FileConnector:
    kind = 'file'

    def __init__(self, source: str, folder: Path, options: dict):
        self.source, self.folder = source, folder
        self.tables = options.get('tables')
        self.delimiter = options.get('delimiter', ',')
        self.encoding = options.get('encoding', 'utf-8-sig')
        self.columns = options.get('columns', {})
        self.units = options.get('units', {})
        self.extra = options.get('extra_columns', 'error')
        self.zone = None
        errors = [f'{source}: unknown file options {sorted(set(options) - OPTIONS)}'] if set(options) - OPTIONS else []
        if not isinstance(self.tables, dict) or not self.tables:
            errors.append(f'{source}.tables: object entity -> file required')
        else:
            errors += [f'{source}.tables: unknown entity {e!r}' for e in sorted(set(self.tables) - set(ENTITIES))]
        if not isinstance(self.delimiter, str) or len(self.delimiter) != 1:
            errors.append(f'{source}.delimiter: one character required')
        if self.extra not in ('error', 'ignore'):
            errors.append(f'{source}.extra_columns: "error" or "ignore" required')
        if options.get('timezone') is not None:
            try:
                self.zone = ZoneInfo(options['timezone'])
            except (ZoneInfoNotFoundError, ValueError, TypeError):
                errors.append(f'{source}.timezone: unknown IANA timezone {options["timezone"]!r}')
        if not isinstance(self.columns, dict):
            errors.append(f'{source}.columns: object required')
            self.columns = {}
        for entity, mapping in self.columns.items():
            spec = ENTITIES.get(entity)
            if spec is None or not isinstance(mapping, dict):
                errors.append(f'{source}.columns.{entity}: entity with an object field -> header required')
                continue
            errors += [f'{source}.columns.{entity}.{f}: unknown field' for f in mapping if spec.field(f) is None]
            headers = list(mapping.values())
            if len(set(headers)) != len(headers):
                errors.append(f'{source}.columns.{entity}: the same header is mapped twice')
        if not isinstance(self.units, dict):
            errors.append(f'{source}.units: object required')
            self.units = {}
        for target, unit in self.units.items():
            entity, _, name = target.partition('.')
            spec = ENTITIES.get(entity)
            f = spec.field(name) if spec else None
            if f is None or not name.endswith('_seconds') or unit not in UNITS:
                errors.append(f'{source}.units.{target}: a *_seconds field and one of {list(UNITS)} required')
        if errors:
            raise ValidationError(errors)

    @classmethod
    def from_options(cls, source: str, options: dict, base_dir: Path):
        return cls(source, resolve(base_dir, options.get('path', '.')), options)

    def _rows(self, location: str) -> tuple[str, bytes, list[tuple[int, list]]]:
        name, _, sheet = location.partition('#')
        path = self.folder / name
        raw = path.read_bytes()
        if path.suffix.lower() == '.xlsx':
            return f'{name}#{sheet}' if sheet else name, raw, read_xlsx(path, sheet or None)
        if path.suffix.lower() != '.csv' or sheet:
            raise ValidationError([f'{location}: .csv or .xlsx[#sheet] required'])
        try:
            text = raw.decode(self.encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            raise ValidationError([f'{name}: cannot decode with {self.encoding}: {exc}']) from None
        return name, raw, [(i, row) for i, row in enumerate(csv.reader(io.StringIO(text), delimiter=self.delimiter), 1)]

    def read(self, request: ReadRequest) -> SourceBatch:
        records, issues, hashes, stamps = [], [], [], []
        entities = [e for e in request.entities if e in self.tables]
        if not entities:
            issues.append(Issue('error', 'no_tables', request.domain,
                                f'source bound to {request.domain} but declares none of {list(request.entities)}', self.source))
        for entity in entities:
            spec = ENTITIES[entity]
            label, raw, rows = self._rows(self.tables[entity])
            hashes.append(f'{label}:{hashlib.sha256(raw).hexdigest()}')
            stamps.append(int((self.folder / label.partition('#')[0]).stat().st_mtime))
            rows = [(n, r) for n, r in rows if any(c is not None and str(c).strip() for c in r)]
            if not rows:
                issues.append(Issue('error', 'empty_table', request.domain, 'header row required', self.source, entity,
                                    original_key=label))
                continue
            header_row, header = rows[0]
            by_header = {v: k for k, v in self.columns.get(entity, {}).items()}
            fields, problems = [], []
            for index, cell in enumerate(header):
                title = str(cell).strip() if cell is not None else ''
                name = by_header.get(title, title if spec.field(title) and title not in self.columns.get(entity, {}) else None)
                if name is None and title and self.extra == 'error':
                    problems.append(f'column {_letters(index)} {title!r} is not a field of {entity}')
                fields.append((name, title, index))
            names = [f for f, _, _ in fields if f]
            if len(set(names)) != len(names):
                problems.append('a field appears in more than one column')
            if problems:
                issues += [Issue('error', 'invalid_header', request.domain, p, self.source, entity,
                                 original_key=f'{label}:{header_row}') for p in problems]
                continue
            for number, row in rows[1:]:
                values, bad = {}, False
                for name, title, index in fields:
                    if name is None:
                        continue
                    cell = row[index] if index < len(row) else None
                    try:
                        value = convert(spec.field(name), cell, self.zone, UNITS[self.units.get(f'{entity}.{name}', 'seconds')])
                    except ValueError as exc:
                        bad = True
                        issues.append(Issue('error', 'invalid_value', request.domain, f'{title}: {exc}', self.source, entity,
                                            original_key=f'{label}:{number}:{_letters(index)}'))
                        continue
                    if value is not _ABSENT:
                        values[name] = value
                if not bad:
                    records.append(Record(entity, values, f'{label}:{number}'))
        extracted = datetime.fromtimestamp(max(stamps), UTC) if stamps else request.now
        fingerprint = 'sha256:' + hashlib.sha256('\n'.join(sorted(hashes)).encode()).hexdigest() if hashes else ''
        return SourceBatch(self.source, self.kind, request.domain, extracted, tuple(records), tuple(issues), fingerprint)


def write_csv_tables(bundle: dict, folder: Path) -> dict:
    """Write one CSV per entity with canonical headers. Returns the ``tables`` option for a binding."""
    folder.mkdir(parents=True, exist_ok=True)
    tables = {}
    for entity, rows in sorted(bundle['records'].items()):
        spec = ENTITIES[entity]
        headers = [f.name for f in spec.fields if any(f.name in r for r in rows)]
        out = io.StringIO()
        writer = csv.writer(out, lineterminator='\n')
        writer.writerow(headers)
        for r in rows:
            writer.writerow(['null' if r.get(h, '') is None else
                             str(r.get(h, '')).lower() if isinstance(r.get(h), bool) else r.get(h, '') for h in headers])
        (folder / f'{entity}.csv').write_text(out.getvalue(), encoding='utf-8')
        tables[entity] = f'{entity}.csv'
    return tables
