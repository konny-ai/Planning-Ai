"""#82: file connector (CSV, Excel) for every canonical domain."""
import json
import tempfile
import unittest
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from planning_factory import connectors
from planning_factory.adapters.json_io import dumps, loads, scenario_digest
from planning_factory.canonical.binding import load_binding, parse_binding
from planning_factory.canonical.builder import build_scenario, read_sources
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.canonical.entities import domain_entities
from planning_factory.canonical.model import ReadRequest
from planning_factory.connectors.files import FileConnector, write_csv_tables
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / 'examples' / 'canonical-files'
WORKSHOP = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
NOW = datetime(2026, 10, 19, 7, tzinfo=UTC)


def request(domain):
    return ReadRequest(domain, domain_entities(domain), NOW, NOW, NOW)


def read(folder, domain, **options):
    options.setdefault('path', '.')
    return FileConnector.from_options('files', options, Path(folder)).read(request(domain))


def without_digest(p):
    data = json.loads(dumps(p))
    data.pop('scenario_digest')
    return data


def xlsx(path: Path, sheets: dict):
    """Minimal workbook writer for tests: str -> shared string, int/float -> number, bool -> boolean."""
    shared, xml_sheets = [], {}
    for name, rows in sheets.items():
        out = []
        for r, row in enumerate(rows, 1):
            cells = []
            for c, value in enumerate(row):
                ref = f'{chr(65 + c)}{r}'
                if value is None:
                    continue
                if isinstance(value, bool):
                    cells.append(f'<c r="{ref}" t="b"><v>{int(value)}</v></c>')
                elif isinstance(value, (int, float)):
                    cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                else:
                    shared.append(value)
                    cells.append(f'<c r="{ref}" t="s"><v>{len(shared) - 1}</v></c>')
            out.append(f'<row r="{r}">{"".join(cells)}</row>')
        xml_sheets[name] = ('<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                            f'<sheetData>{"".join(out)}</sheetData></worksheet>')
    main = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    rel = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr('xl/workbook.xml', f'<workbook xmlns="{main}" xmlns:r="{rel}"><sheets>' + ''.join(
            f'<sheet name="{escape(n)}" sheetId="{i}" r:id="rId{i}"/>' for i, n in enumerate(xml_sheets, 1)) + '</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(
            f'<Relationship Id="rId{i}" Type="{rel}/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, len(xml_sheets) + 1))
                   + '</Relationships>')
        for i, body in enumerate(xml_sheets.values(), 1):
            z.writestr(f'xl/worksheets/sheet{i}.xml', body)
        z.writestr('xl/sharedStrings.xml', f'<sst xmlns="{main}">' + ''.join(f'<si><t>{escape(s)}</t></si>' for s in shared) + '</sst>')


class ExampleTests(unittest.TestCase):
    def test_workshop_is_rebuilt_from_files_only(self):
        binding = load_binding(EXAMPLE / 'binding.json', connectors.available())
        now = datetime.now(UTC).replace(microsecond=0)  # file mtimes are the extraction instants
        result = build_scenario(binding, read_sources(binding, connectors.create, now), now)
        self.assertEqual((), result.issues)
        self.assertEqual(without_digest(plan(WORKSHOP)), without_digest(plan(result.scenario)))
        self.assertTrue(all(s['connector'] == 'file' for d in result.manifest['domains'].values() for s in d['sources']))

    def test_files_and_canonical_json_give_the_same_scenario(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'bundle.json').write_text(json.dumps(scenario_to_bundle(WORKSHOP)), encoding='utf-8')
            data = json.loads((EXAMPLE / 'binding.json').read_text(encoding='utf-8'))
            data['sources'] = {'json': {'connector': 'canonical-json', 'options': {'path': 'bundle.json'}}}
            data['domains'] = {d: {'sources': ['json']} for d in data['domains']}
            via_json = parse_binding(data, Path(tmp), connectors.available())
            via_files = load_binding(EXAMPLE / 'binding.json', connectors.available())
            a = build_scenario(via_json, read_sources(via_json, connectors.create, NOW), NOW)
            b = build_scenario(via_files, read_sources(via_files, connectors.create, NOW), NOW)
            self.assertEqual(scenario_digest(a.scenario), scenario_digest(b.scenario))

    def test_committed_example_tables_are_up_to_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_csv_tables(scenario_to_bundle(WORKSHOP), Path(tmp))
            for path in Path(tmp).glob('*.csv'):
                self.assertEqual(path.read_text(encoding='utf-8'), (EXAMPLE / path.name).read_text(encoding='utf-8'), path.name)


class CsvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, name, text):
        (self.folder / name).write_text(text, encoding='utf-8')

    def test_customer_headers_units_delimiter_and_extra_columns(self):
        self.write('fasi.csv', 'CODICE;COMMESSA;ORE;SETUP_MIN;NOTE\nF1;C1;1,5;;x\n'.replace('1,5', '1.5'))
        batch = read(self.folder, 'routings', tables={'operation': 'fasi.csv'}, delimiter=';', extra_columns='ignore',
                     columns={'operation': {'id': 'CODICE', 'order_id': 'COMMESSA', 'duration_seconds': 'ORE',
                                            'setup_seconds': 'SETUP_MIN'}},
                     units={'operation.duration_seconds': 'hours', 'operation.setup_seconds': 'minutes'})
        self.assertEqual((), batch.issues)
        self.assertEqual({'id': 'F1', 'order_id': 'C1', 'duration_seconds': 5400}, batch.records[0].values)
        self.assertEqual('fasi.csv:2', batch.records[0].original_key)
        self.assertTrue(batch.fingerprint.startswith('sha256:'))

    def test_errors_point_to_file_row_and_column(self):
        self.write('operation.csv', 'id,order_id,duration_seconds,setup_seconds,colore\nF1,C1,abc,0,rosso\n')
        batch = read(self.folder, 'routings', tables={'operation': 'operation.csv'})
        self.assertEqual([('invalid_header', 'operation.csv:1')], [(i.code, i.original_key) for i in batch.issues])
        self.write('operation.csv', 'id,order_id,duration_seconds,setup_seconds\nF1,C1,abc,0\nF2,C1,0.5,0\n')
        batch = read(self.folder, 'routings', tables={'operation': 'operation.csv'})
        self.assertEqual([('invalid_value', 'operation.csv:2:C'), ('invalid_value', 'operation.csv:3:C')],
                         [(i.code, i.original_key) for i in batch.issues])
        self.assertEqual((), batch.records)
        batch = read(self.folder, 'routings', tables={'operation': 'operation.csv'},
                     units={'operation.duration_seconds': 'hours'})
        self.assertEqual(['operation.csv:2:C'], [i.original_key for i in batch.issues])

    def test_null_only_for_nullable_fields(self):
        self.write('resource.csv', 'id,calendar_id,capacity\nR1,c,null\nR2,null,1\n')
        batch = read(self.folder, 'resources', tables={'resource': 'resource.csv'})
        self.assertEqual({'id': 'R1', 'calendar_id': 'c', 'capacity': None}, batch.records[0].values)
        self.assertEqual(['resource.csv:3:B'], [i.original_key for i in batch.issues])

    def test_local_times_need_a_timezone_and_dst_is_never_guessed(self):
        self.write('order.csv', 'id,due\nA,2026-10-22 17:00\nB,2026-10-25 02:30\nC,2026-03-29 02:30\nD,2026-10-22T17:00:00+02:00\n')
        batch = read(self.folder, 'orders', tables={'order': 'order.csv'})
        self.assertEqual(['order.csv:2:B', 'order.csv:3:B', 'order.csv:4:B'], [i.original_key for i in batch.issues])
        batch = read(self.folder, 'orders', tables={'order': 'order.csv'}, timezone='Europe/Rome')
        self.assertIn('ambiguous', batch.issues[0].detail)
        self.assertIn('does not exist', batch.issues[1].detail)
        self.assertEqual([('A', '2026-10-22T15:00:00Z'), ('D', '2026-10-22T17:00:00+02:00')],
                         [(r.values['id'], r.values['due']) for r in batch.records])

    def test_domain_without_tables_and_bad_options(self):
        self.write('order.csv', 'id\nA\n')
        batch = read(self.folder, 'routings', tables={'order': 'order.csv'})
        self.assertEqual(['no_tables'], [i.code for i in batch.issues])
        for options, text in (({'tables': {'order': 'o.csv'}, 'colour': 1}, 'unknown file options'),
                              ({'tables': {'weather': 'w.csv'}}, 'unknown entity'),
                              ({'tables': {'order': 'o.csv'}, 'units': {'order.due': 'hours'}}, '*_seconds'),
                              ({'tables': {'order': 'o.csv'}, 'timezone': 'Mars/Base'}, 'unknown IANA'),
                              ({}, 'tables')):
            with self.assertRaises(ValidationError) as ctx:
                FileConnector.from_options('files', options, self.folder)
            self.assertIn(text, str(ctx.exception))

    def test_manual_file_corrects_one_field_by_declared_precedence(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'correzioni.csv').write_text('operation_id,completed_at\nord-a/materiale,2026-10-18T16:00:00+02:00\n',
                                                   encoding='utf-8')
            data = json.loads((EXAMPLE / 'binding.json').read_text(encoding='utf-8'))
            data['sources']['files']['options']['path'] = str(EXAMPLE)
            data['sources']['manual'] = {'connector': 'file', 'options': {'tables': {'operation_state': 'correzioni.csv'}}}
            data['domains']['progress'] = {'sources': ['files', 'manual']}
            now = datetime.now(UTC).replace(microsecond=0)
            b = parse_binding(data, Path(tmp), connectors.available())
            conflict = build_scenario(b, read_sources(b, connectors.create, now), now)
            self.assertEqual(['source_conflict'], [i.code for i in conflict.issues])
            data['domains']['progress']['precedence'] = {'operation_state.completed_at': ['manual', 'files']}
            b = parse_binding(data, Path(tmp), connectors.available())
            result = build_scenario(b, read_sources(b, connectors.create, now), now)
            self.assertEqual([('warn', 'source_override', 'manual')], [(i.severity, i.code, i.source) for i in result.issues])
            done = {o.id: o.completed_at for o in result.scenario.operations}
            self.assertEqual(datetime(2026, 10, 18, 14, tzinfo=UTC), done['ord-a/materiale'])


class XlsxTests(unittest.TestCase):
    def test_excel_numbers_dates_times_and_sheets(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'dati.xlsx')
            xlsx(path, {'Commesse': [['id', 'due', 'priority'], [12345, 46317.75, 3], ['B', None, None]],
                        'Turni': [['calendar_id', 'weekday', 'start', 'end'], ['shop', 0, 0.25, 0.5]],
                        'Festivi': [['calendar_id', 'day'], ['shop', 46350]]})
            orders = read(tmp, 'orders', tables={'order': 'dati.xlsx#Commesse'}, timezone='Europe/Rome')
            self.assertEqual((), orders.issues)
            self.assertEqual([{'id': '12345', 'due': '2026-10-22T16:00:00Z', 'priority': 3}, {'id': 'B'}],
                             [r.values for r in orders.records])
            self.assertEqual('dati.xlsx#Commesse:2', orders.records[0].original_key)
            calendars = read(tmp, 'calendars', tables={'calendar_shift': 'dati.xlsx#Turni', 'calendar_holiday': 'dati.xlsx#Festivi'})
            self.assertEqual([{'calendar_id': 'shop', 'weekday': 0, 'start': '06:00:00', 'end': '12:00:00'},
                              {'calendar_id': 'shop', 'day': '2026-11-24'}], [r.values for r in calendars.records])
            with self.assertRaises(ValidationError):
                read(tmp, 'orders', tables={'order': 'dati.xlsx#Nessuno'})
            first = read(tmp, 'orders', tables={'order': 'dati.xlsx'}, timezone='Europe/Rome')
            self.assertEqual(2, len(first.records))

    def test_excel_serial_instant_needs_the_source_timezone(self):
        with tempfile.TemporaryDirectory() as tmp:
            xlsx(Path(tmp, 'o.xlsx'), {'S': [['id', 'due'], ['A', 46317.75]]})
            batch = read(tmp, 'orders', tables={'order': 'o.xlsx'})
            self.assertIn('declare the source timezone', batch.issues[0].detail)


if __name__ == '__main__':
    unittest.main()
