"""#88: constraint chain per unit and finite-capacity critical path, reconstructed without changing the plan."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads
from planning_factory.cli import main
from planning_factory.domain.models import Dependency, Resource
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.constraint_chain import constraint_chain
from planning_factory.engine.scheduler import plan
from tests.helpers import dt, op, scenario

ROOT = Path(__file__).resolve().parents[1]


def explain(s, **kw):
    p = plan(s)
    before = dumps(p)
    result = constraint_chain(s, p, **kw)
    assert dumps(p) == before  # analysis only
    return p, result


class ConstraintChainTests(unittest.TestCase):
    def test_precedence_delay(self):
        s = scenario([op('a', 2, order_id='o'), op('b', 1, order_id='o', predecessors=(Dependency('a'),))])
        _, r = explain(s)
        self.assertEqual(('predecessor', 'job:a'), (r['units']['job:b']['cause'], r['units']['job:b']['ref']))
        self.assertEqual(['job:b', 'job:a'], [x['unit'] for x in r['orders']['o']['critical_path']])
        self.assertEqual('horizon', r['units']['job:a']['cause'])

    def test_resource_delay_names_the_occupant_and_the_resource(self):
        s = scenario([op('first', 2, order_id='x', priority=0), op('second', 1, order_id='y', priority=1)])
        _, r = explain(s)
        unit = r['units']['job:second']
        self.assertEqual(('resource', 'job:first', 'm', 7200), (unit['cause'], unit['ref'], unit['resource'],
                                                              unit['capacity_wait_seconds']))
        self.assertEqual(['job:second', 'job:first'], [x['unit'] for x in r['orders']['y']['critical_path']])

    def test_release_and_material_with_calendar_wait(self):
        s = scenario([op('cut', 1, release=dt(hour=12, minute=30))])  # lunch break 12-13 in the test calendar
        _, r = explain(s)
        self.assertEqual(('release', 1800), (r['units']['job:cut']['cause'], r['units']['job:cut']['calendar_wait_seconds']))
        _, r = explain(s, release_reasons={'job:cut': 'material'})
        self.assertEqual('material', r['units']['job:cut']['cause'])

    def test_non_splittable_unit_waits_for_a_long_enough_window(self):
        s = scenario([op('long', 3, release=dt(hour=10))])  # 10-12 is too short: starts at 13
        p, r = explain(s)
        self.assertEqual(dt(hour=13), p.assignments[0].start)
        self.assertEqual(('release', 10800), (r['units']['job:long']['cause'], r['units']['job:long']['calendar_wait_seconds']))

    def test_fixed_and_completed_units(self):
        s = scenario([op('pinned', 1, fixed_start=dt(hour=9)),
                      op('done', 0, alternatives=(), completed_at=dt(day=16, hour=10))])
        _, r = explain(s)
        self.assertEqual(('fixed', 'completed'), (r['units']['job:pinned']['cause'], r['units']['job:done']['cause']))

    def test_capacity_two_machine_reports_one_of_the_occupants(self):
        s = scenario([op('a', 1, priority=0), op('b', 1, priority=1), op('c', 1, priority=2)],
                     resources=(Resource('m', 'work', capacity=2),))
        _, r = explain(s)
        unit = r['units']['job:c']
        self.assertEqual('resource', unit['cause'])
        self.assertIn(unit.get('ref'), ('job:a', 'job:b'))  # both release the machine at the same instant

    def test_workshop_explains_every_unit_and_rejects_foreign_plans(self):
        workshop = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
        p, r = explain(workshop)
        self.assertEqual({a.task_id for a in p.assignments}, set(r['units']))
        self.assertTrue(all('cause' in u for u in r['units'].values()))
        self.assertEqual(('predecessor', 'nest:NEST-001'), (r['units']['job:ord-a/piega']['cause'],
                                                           r['units']['job:ord-a/piega']['ref']))
        self.assertEqual(('resource', 'job:ord-a/piega', 'PIEGA-1'),
                         tuple(r['units']['job:ord-b/piega'][k] for k in ('cause', 'ref', 'resource')))
        with self.assertRaises(ValidationError):
            constraint_chain(replace(workshop, id='altro'), p)

    def test_cli_with_manifest_material_labels(self):
        workshop = ROOT / 'examples' / 'workshop.json'
        with tempfile.TemporaryDirectory() as tmp:
            plan_path, manifest, out = Path(tmp, 'p.json'), Path(tmp, 'm.json'), Path(tmp, 'x.json')
            plan_path.write_text(dumps(plan(loads(workshop.read_text(encoding='utf-8')))), encoding='utf-8')
            manifest.write_text(json.dumps({'materials': {'pegs': [{'unit': 'nest:NEST-001'}]}}), encoding='utf-8')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(['explain', str(workshop), str(plan_path), '--manifest', str(manifest),
                                          '-o', str(out)]))
            self.assertEqual('material', json.loads(out.read_text(encoding='utf-8'))['units']['nest:NEST-001']['cause'])


if __name__ == '__main__':
    unittest.main()
