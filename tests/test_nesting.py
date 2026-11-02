"""#84: nesting domain from a CAM, machine assigned by the CAM, status filter and change events."""
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from planning_factory import connectors
from planning_factory.adapters.json_io import dumps
from planning_factory.canonical.binding import parse_binding
from planning_factory.canonical.builder import build_scenario, read_sources
from planning_factory.canonical.changes import manifest_changes
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.cli import main
from planning_factory.engine.scheduler import plan
from planning_factory.engine.simulation import compare
from planning_factory.engine.verification import verify_plan
from tests.test_canonical import ALL, NOW, WORKSHOP, batches_from, binding, codes


class NestingDomainTests(unittest.TestCase):
    def setUp(self):
        self.bundle = scenario_to_bundle(WORKSHOP)
        self.nest = self.bundle['records']['nest'][0]

    def build(self, **nesting):
        domains = {d: {'sources': ['erp']} for d in ALL}
        domains['nesting'].update(nesting)
        b = binding(['erp'], domains)
        return build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)

    def test_multi_order_program_is_one_unit_on_the_machine_chosen_by_the_cam(self):
        self.nest.update(resource_id='LASER-2', status='released', version='3', material='S275', thickness_mm='10',
                         sheets=2, preview='cam/PL-1.png')
        for member in self.bundle['records']['nest_member']:
            member['quantity'] = '4'
        result = self.build()
        self.assertEqual([], codes(result))
        p = plan(result.scenario)
        self.assertFalse(verify_plan(result.scenario, p))
        unit = next(a for a in p.assignments if a.task_id == 'nest:NEST-001')
        self.assertEqual(('ord-a/taglio', 'ord-b/taglio'), unit.operation_ids)
        self.assertEqual(10800 + 1800, sum(s.seconds for s in unit.segments))  # shared time once
        self.assertIn('LASER-2', {d.resource_id for d in unit.demands})
        self.assertEqual({'A', 'B'}, {o.order_id for o in result.scenario.operations if o.id in unit.operation_ids})

    def test_machine_not_allowed_for_a_member_is_an_error(self):
        self.nest['resource_id'] = 'PIEGA-1'
        result = self.build()
        self.assertIsNone(result.scenario)
        self.assertEqual({('error', 'nest_resource_conflict')}, set(codes(result)))
        self.nest['resource_id'] = 'PRESSA-9'
        self.assertIn(('error', 'unknown_reference'), codes(self.build()))

    def test_status_filter_plans_members_one_by_one(self):
        self.nest['status'] = 'draft'
        result = self.build(include_statuses=['released'])
        self.assertEqual([('warn', 'nest_excluded')], codes(result))
        self.assertEqual((), result.scenario.nestings)
        tasks = {a.task_id for a in plan(result.scenario).assignments}
        self.assertTrue({'job:ord-a/taglio', 'job:ord-b/taglio'} <= tasks)
        self.assertEqual([], codes(self.build(include_statuses=['draft', 'released'])))
        self.nest['status'] = 'nested'
        self.assertIn(('error', 'invalid_record'), codes(self.build()))


class ChangeEventTests(unittest.TestCase):
    def manifests(self, mutate=None):
        bundle = scenario_to_bundle(WORKSHOP)
        b = binding(['erp'], {d: {'sources': ['erp']} for d in ALL})
        first = build_scenario(b, batches_from({'erp': bundle}, b), NOW)
        if mutate:
            mutate(bundle)
        later = datetime(2026, 10, 19, 8, tzinfo=UTC)
        second = build_scenario(b, batches_from({'erp': bundle}, b, later), later)
        return first, second

    def test_rereading_the_same_data_is_not_an_event(self):
        first, second = self.manifests()
        changes = manifest_changes(first.manifest, second.manifest)
        self.assertEqual(([], False), (changes['changed_domains'], changes['recompute']))

    def test_a_modified_program_is_an_event_and_plans_are_comparable(self):
        first, second = self.manifests(lambda b: b['records']['nest'][0].update(duration_seconds=14400))
        changes = manifest_changes(first.manifest, second.manifest)
        self.assertEqual((['nesting'], True), (changes['changed_domains'], changes['recompute']))
        diff = compare(plan(first.scenario), plan(second.scenario))
        self.assertTrue(diff)

    def test_changes_cli_exit_codes(self):
        first, second = self.manifests(lambda b: b['records']['nest'][0].update(duration_seconds=14400))
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp, 'a.json'), Path(tmp, 'b.json')
            a.write_text(dumps(first.manifest), encoding='utf-8')
            b.write_text(dumps(second.manifest), encoding='utf-8')
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(3, main(['changes', str(a), str(b)]))
            self.assertEqual(['nesting'], json.loads(out.getvalue())['changed_domains'])
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(['changes', str(a), str(a)]))


class CamThroughSqlTests(unittest.TestCase):
    """A CAM database read with declared queries; part codes linked to ERP operations by an explicit map."""

    def test_cam_programs_feed_the_nesting_domain(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = sqlite3.connect(Path(tmp, 'cam.db'))
            db.executescript('''
                CREATE TABLE PROGRAMMI (CODICE TEXT, STATO TEXT, MACCHINA TEXT, MIN_TAGLIO NUMERIC, MIN_ATTR NUMERIC, LAMIERE INT);
                INSERT INTO PROGRAMMI VALUES ('PL-1', 'released', 'LASER-2', 180, 30, 2), ('PL-2', 'draft', NULL, 30, 0, 1);
                CREATE TABLE PEZZI (PROGRAMMA TEXT, PARTE TEXT, QTA INT);
                INSERT INTO PEZZI VALUES ('PL-1', 'P-100', 4), ('PL-1', 'P-200', 4), ('PL-2', 'P-999', 1);
            ''')
            db.commit()
            db.close()
            erp = scenario_to_bundle(WORKSHOP)
            for key in ('nest', 'nest_member'):
                erp['records'].pop(key)
            erp['records']['identity_map'] = [
                {'map': 'cam-parti', 'external_id': 'P-100', 'canonical_id': 'ord-a/taglio'},
                {'map': 'cam-parti', 'external_id': 'P-200', 'canonical_id': 'ord-b/taglio'}]
            Path(tmp, 'erp.json').write_text(json.dumps(erp), encoding='utf-8')
            cam = {'connector': 'sql', 'identity': {'operation': 'cam-parti'}, 'options': {
                'driver': 'sqlite3', 'database': 'cam.db',
                'units': {'nest.duration_seconds': 'minutes', 'nest.setup_seconds': 'minutes'},
                'queries': {
                    'nest': 'SELECT CODICE AS id, STATO AS status, MACCHINA AS resource_id, MIN_TAGLIO AS duration_seconds, '
                            'MIN_ATTR AS setup_seconds, LAMIERE AS sheets FROM PROGRAMMI',
                    'nest_member': 'SELECT PROGRAMMA AS nest_id, PARTE AS operation_id, QTA AS quantity FROM PEZZI'}}}
            data = {'schema_version': 1, 'installation': 'test',
                    'scenario': {'id': 'officina-demo', 'start': '2026-10-19T00:00:00Z', 'end': '2026-10-26T00:00:00Z'},
                    'sources': {'erp': {'connector': 'canonical-json', 'options': {'path': 'erp.json'}}, 'cam': cam},
                    'domains': {d: {'sources': ['cam' if d == 'nesting' else 'erp']} for d in ALL}}
            data['domains']['identity'] = {'sources': ['erp']}
            data['domains']['nesting']['include_statuses'] = ['released']
            b = parse_binding(data, Path(tmp), connectors.available())
            now = datetime.now(UTC).replace(microsecond=0)
            result = build_scenario(b, read_sources(b, connectors.create, now), now)
            # Keys are translated before the status filter: the unknown part of the draft PL-2 is reported too.
            found = {(i.severity, i.code, i.key or i.original_key) for i in result.issues}
            self.assertIn(('warn', 'nest_excluded', 'PL-2'), found)
            self.assertIn(('block', 'unmatched_identity', 'sql:nest_member:3[PL-2/P-999]'), found)
            self.assertEqual([('PL-1', ('ord-a/taglio', 'ord-b/taglio'), 10800, 1800)],
                             [(n.id, n.operation_ids, n.duration_seconds, n.setup_seconds) for n in result.scenario.nestings])


if __name__ == '__main__':
    unittest.main()
