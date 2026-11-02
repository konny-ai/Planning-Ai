"""#85: materials domain through the existing pegging, and the generic SQL connector."""
import json
import sqlite3
import tempfile
import unittest
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from planning_factory import connectors
from planning_factory.adapters.json_io import scenario_digest
from planning_factory.canonical.binding import parse_binding
from planning_factory.canonical.builder import build_scenario, read_sources
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.canonical.entities import ENTITIES, normalize
from planning_factory.connectors.files import convert
from planning_factory.connectors.sql import SqlConnector
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tests.test_canonical import ALL, NOW, WORKSHOP, batches_from, binding, codes

SHEET = 'LAM-S275-10'
SUPPLIES = [{'id': 'MAG-60', 'item_id': SHEET, 'quantity': '2', 'available_at': '2026-10-19T00:00:00Z', 'kind': 'stock'},
            {'id': 'PO-4711', 'item_id': SHEET, 'quantity': '3.5', 'available_at': '2026-10-21T08:00:00Z',
             'kind': 'purchase_order', 'reference': 'ODF 4711/2'}]
NEEDS = [{'operation_id': 'ord-a/piega', 'item_id': SHEET, 'quantity': '2'},
         {'operation_id': 'ord-b/piega', 'item_id': SHEET, 'quantity': '3'}]


class MaterialsTests(unittest.TestCase):
    def setUp(self):
        self.bundle = scenario_to_bundle(WORKSHOP)
        self.bundle['records'].update(material_supply=[dict(s) for s in SUPPLIES], material_need=[dict(n) for n in NEEDS])

    def build(self, order='due'):
        domains = {d: {'sources': ['erp']} for d in ALL}
        domains['materials'] = {'sources': ['erp'], 'allocation_order': order}
        b = binding(['erp'], domains)
        return build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)

    def ops(self, result):
        return {o.id: o for o in result.scenario.operations}

    def test_supplies_become_material_ready_releases_with_pegs(self):
        result = self.build()
        self.assertEqual([], codes(result))
        ops = self.ops(result)
        self.assertEqual(WORKSHOP.operations[3].release, ops['ord-a/piega'].release)  # stock: ready at start
        self.assertEqual(datetime(2026, 10, 21, 8, tzinfo=UTC), ops['ord-b/piega'].release)
        self.assertEqual([{'unit': 'job:ord-a/piega', 'item_id': SHEET, 'supply_id': 'MAG-60', 'quantity': '2'},
                          {'unit': 'job:ord-b/piega', 'item_id': SHEET, 'supply_id': 'PO-4711', 'quantity': '3'}],
                         result.manifest['materials']['pegs'])
        p = plan(result.scenario)
        self.assertFalse(verify_plan(result.scenario, p))
        start = next(a.start for a in p.assignments if 'ord-b/piega' in a.operation_ids)
        self.assertGreaterEqual(start, datetime(2026, 10, 21, 8, tzinfo=UTC))

    def test_shortage_is_reported_and_the_unit_is_not_planned(self):
        self.bundle['records']['material_need'][1]['quantity'] = '6'
        result = self.build()
        self.assertFalse(result.publishable)
        issue = next(i for i in result.issues if i.code == 'material_shortage')
        self.assertEqual(('block', ('job:ord-b/piega',)), (issue.severity, issue.units))
        self.assertIn('missing 2.5', issue.detail)  # needs 6, only 3.5 left after ord-a/piega
        self.assertEqual([{'unit': 'job:ord-b/piega', 'item_id': SHEET, 'missing': '2.5'}], result.manifest['materials']['shortages'])
        unscheduled = {u.task_id: u.code for u in plan(result.scenario).unscheduled}
        self.assertEqual('NO_FEASIBLE_SLOT', unscheduled['job:ord-b/piega'])

    def test_supply_without_date_is_not_usable(self):
        self.bundle['records']['material_supply'][1]['available_at'] = None
        result = self.build()
        self.assertEqual({('warn', 'supply_without_date'), ('block', 'material_shortage')}, set(codes(result)))

    def test_rank_order_is_explicit_and_complete(self):
        self.bundle['records']['allocation_rank'] = [{'operation_id': 'ord-b/piega', 'rank': 1}]
        missing = self.build('rank')
        self.assertIsNone(missing.scenario)
        self.assertEqual([('error', 'missing_allocation_rank')], codes(missing))
        self.bundle['records']['allocation_rank'].append({'operation_id': 'ord-a/piega', 'rank': 2})
        result = self.build('rank')
        pegs = {(p['unit'], p['supply_id']): p['quantity'] for p in result.manifest['materials']['pegs']}
        self.assertEqual({('job:ord-b/piega', 'MAG-60'): '2', ('job:ord-b/piega', 'PO-4711'): '1',
                          ('job:ord-a/piega', 'PO-4711'): '2'}, pegs)

    def test_nest_members_share_one_unit_and_closed_operations_reserve_nothing(self):
        self.bundle['records']['material_need'] = [
            {'operation_id': 'ord-a/taglio', 'item_id': SHEET, 'quantity': '1'},
            {'operation_id': 'ord-b/taglio', 'item_id': SHEET, 'quantity': '1'},
            {'operation_id': 'ord-a/materiale', 'item_id': SHEET, 'quantity': '1'}]
        result = self.build()
        self.assertEqual([('warn', 'need_on_closed_operation')], codes(result))
        self.assertEqual([('nest:NEST-001', '2')], [(p['unit'], p['quantity']) for p in result.manifest['materials']['pegs']])

    def test_binding_requires_an_explicit_allocation_order(self):
        domains = {d: {'sources': ['erp']} for d in ALL}
        domains['materials'] = {'sources': ['erp'], 'allocation_order': 'whatever'}
        with self.assertRaises(ValidationError) as ctx:
            binding(['erp'], domains)
        self.assertIn('allocation_order', str(ctx.exception))

    def test_quantities_are_exact_decimals(self):
        need = ENTITIES['material_need']
        normalized, problems = normalize(need, {'operation_id': 'x', 'item_id': 'y', 'quantity': '2.50'})
        self.assertEqual(('2.5', []), (normalized['quantity'], problems))
        for bad in ('0', '-1', 'NaN', 1.5, True):
            self.assertTrue(normalize(need, {'operation_id': 'x', 'item_id': 'y', 'quantity': bad})[1], bad)


class SqlConnectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = Path(self.tmp.name)
        db = sqlite3.connect(self.folder / 'as400.db')
        db.executescript('''
            CREATE TABLE ORDFOR (NUMERO TEXT, ARTICOLO TEXT, QTA NUMERIC, DATA_CONF TEXT, TIPO TEXT, RIF TEXT);
            INSERT INTO ORDFOR VALUES ('MAG-60', 'LAM-S275-10', 2, '2026-10-19 02:00:00', 'stock', NULL);
            INSERT INTO ORDFOR VALUES ('PO-4711', 'LAM-S275-10', 3.5, '2026-10-21 10:00:00', 'purchase_order', 'ODF 4711/2');
            CREATE TABLE FABB (FASE TEXT, ARTICOLO TEXT, QTA INTEGER);
            INSERT INTO FABB VALUES ('ord-a/piega', 'LAM-S275-10', 2), ('ord-b/piega', 'LAM-S275-10', 3);
        ''')
        db.commit()
        db.close()
        self.options = {'driver': 'sqlite3', 'database': 'as400.db', 'timezone': 'Europe/Rome', 'queries': {
            'material_supply': 'SELECT NUMERO AS ID, ARTICOLO AS item_id, QTA AS quantity, DATA_CONF AS available_at, '
                               'TIPO AS kind, RIF AS reference FROM ORDFOR ORDER BY NUMERO',
            'material_need': '-- fabbisogni per fase\nSELECT FASE AS operation_id, ARTICOLO AS item_id, QTA AS quantity FROM FABB'}}

    def tearDown(self):
        self.tmp.cleanup()

    def binding(self, materials_source):
        bundle = scenario_to_bundle(WORKSHOP)
        Path(self.folder, 'erp.json').write_text(json.dumps(bundle), encoding='utf-8')
        data = {'schema_version': 1, 'installation': 'test',
                'scenario': {'id': 'officina-demo', 'start': '2026-10-19T00:00:00Z', 'end': '2026-10-26T00:00:00Z'},
                'sources': {'erp': {'connector': 'canonical-json', 'options': {'path': 'erp.json'}},
                            'mat': materials_source},
                'domains': {d: {'sources': ['erp']} for d in ALL}}
        data['domains']['materials'] = {'sources': ['mat'], 'allocation_order': 'due'}
        return parse_binding(data, self.folder, connectors.available())

    def test_same_material_data_from_sql_and_from_files_gives_the_same_scenario(self):
        now = datetime.now(UTC).replace(microsecond=0)  # files written by the test carry the current mtime
        via_sql = self.binding({'connector': 'sql', 'options': self.options})
        sql_result = build_scenario(via_sql, read_sources(via_sql, connectors.create, now), now)
        self.assertEqual([], codes(sql_result))
        Path(self.folder, 'supply.csv').write_text(
            'id,item_id,quantity,available_at,kind,reference\n'
            'MAG-60,LAM-S275-10,2,2026-10-19T00:00:00Z,stock,\n'
            'PO-4711,LAM-S275-10,3.50,2026-10-21T08:00:00Z,purchase_order,ODF 4711/2\n', encoding='utf-8')
        Path(self.folder, 'need.csv').write_text('operation_id,item_id,quantity\nord-a/piega,LAM-S275-10,2\n'
                                                 'ord-b/piega,LAM-S275-10,3\n', encoding='utf-8')
        via_files = self.binding({'connector': 'file', 'options': {'tables': {'material_supply': 'supply.csv',
                                                                              'material_need': 'need.csv'}}})
        file_result = build_scenario(via_files, read_sources(via_files, connectors.create, now), now)
        self.assertEqual(scenario_digest(sql_result.scenario), scenario_digest(file_result.scenario))
        self.assertEqual(sql_result.manifest['domains']['materials']['digest'],
                         file_result.manifest['domains']['materials']['digest'])
        self.assertEqual(sql_result.manifest['materials'], file_result.manifest['materials'])
        source = sql_result.manifest['domains']['materials']['sources'][0]
        self.assertEqual(('sql', 0), (source['connector'], source['age_seconds']))

    def test_only_single_read_only_selects_are_accepted(self):
        for query in ('DELETE FROM FABB', 'SELECT 1; DROP TABLE FABB', 'UPDATE FABB SET QTA = 0',
                      'WITH x AS (SELECT 1) INSERT INTO FABB SELECT * FROM x', 'PRAGMA table_info(FABB)', ''):
            options = dict(self.options, queries={'material_need': query})
            with self.assertRaises(ValidationError, msg=query):
                SqlConnector.from_options('mat', options, self.folder)

    def test_credentials_stay_out_and_missing_driver_or_env_is_reported(self):
        options = {'driver': 'pyodbc', 'connection_env': 'PF_TEST_UNSET_DSN', 'queries': self.options['queries']}
        b = self.binding({'connector': 'sql', 'options': options})
        result = build_scenario(b, read_sources(b, connectors.create, NOW), NOW)
        self.assertIsNone(result.scenario)
        issue = next(i for i in result.issues if i.code == 'source_unavailable')
        self.assertTrue('PF_TEST_UNSET_DSN' in issue.detail or 'pyodbc' in issue.detail)
        with self.assertRaises(ValidationError):
            self.binding({'connector': 'sql', 'options': dict(options, connection_env=None,
                                                              connection='DRIVER=x;UID=u;PWD=secret')})

    def test_columns_must_map_to_fields_and_bad_values_point_to_the_row(self):
        options = dict(self.options, queries={'material_need': 'SELECT FASE AS operation_id, ARTICOLO, QTA AS quantity FROM FABB'})
        b = self.binding({'connector': 'sql', 'options': options})
        self.assertIn(('error', 'invalid_header'), codes(build_scenario(b, read_sources(b, connectors.create, NOW), NOW)))
        options = dict(self.options, queries={'material_need': "SELECT FASE AS operation_id, ARTICOLO AS item_id, "
                                                               "CASE WHEN FASE = 'ord-b/piega' THEN -1 ELSE QTA END AS quantity FROM FABB"})
        b = self.binding({'connector': 'sql', 'options': options})
        result = build_scenario(b, read_sources(b, connectors.create, NOW), NOW)
        issue = next(i for i in result.issues if i.code == 'invalid_record')
        self.assertEqual('sql:material_need:2[ord-b/piega/LAM-S275-10]', issue.original_key)

    def test_database_values_convert_without_guessing(self):
        rome = ZoneInfo('Europe/Rome')
        instant = ENTITIES['material_supply'].field('available_at')
        self.assertEqual('2026-10-21T08:00:00Z', convert(instant, datetime(2026, 10, 21, 10), rome, 1))
        self.assertEqual('2026-10-21T10:00:00+00:00', convert(instant, datetime(2026, 10, 21, 10, tzinfo=UTC), None, 1))
        with self.assertRaises(ValueError):
            convert(instant, datetime(2026, 10, 25, 2, 30), rome, 1)
        with self.assertRaises(ValueError):
            convert(instant, datetime(2026, 10, 21, 10), None, 1)
        self.assertEqual('2.5', convert(ENTITIES['material_need'].field('quantity'), Decimal('2.50'), None, 1))
        self.assertEqual('0.1', convert(ENTITIES['material_need'].field('quantity'), 0.1, None, 1))
        self.assertEqual('2026-11-24', convert(ENTITIES['calendar_holiday'].field('day'), date(2026, 11, 24), None, 1))
        self.assertEqual('06:00:00', convert(ENTITIES['calendar_shift'].field('start'), time(6), None, 1))
        with self.assertRaises(ValueError):
            convert(ENTITIES['calendar_shift'].field('start'), date(2026, 1, 1), None, 1)


if __name__ == '__main__':
    unittest.main()
