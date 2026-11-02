"""#78 P10 minimum gain to leave the preferred alternative, and P6 waits over the declared limit."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads, scenario_digest
from planning_factory.canonical.builder import build_scenario
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.cli import main
from planning_factory.domain.models import Dependency, Nesting, Resource, Settings
from planning_factory.domain.validation import ValidationError, compile_tasks
from planning_factory.engine.diagnostics import waits_over_limit
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tests.helpers import alt, dt, op, scenario
from tests.test_canonical import ALL, NOW, batches_from, binding

ROOT = Path(__file__).resolve().parents[1]
MACHINES = (Resource('m', 'work'), Resource('x', 'work'))


def contested(gain=0, preferred=0):
    """'busy' holds m for 1 h; 'job' prefers m but x is free right away."""
    return scenario([op('busy', 1, resource='m', priority=0),
                     op('job', 2, priority=1, alternatives=(alt('m'), alt('x')), preferred_alternative=preferred)],
                    resources=MACHINES, settings=Settings(alternative_switch_min_gain_seconds=gain))


def job(p):
    return next(a for a in p.assignments if a.task_id == 'job:job')


class SwitchGainTests(unittest.TestCase):
    def test_default_takes_the_earliest_alternative_as_before(self):
        a = job(plan(contested()))
        self.assertEqual(('x', dt(hour=8)), (a.demands[0].resource_id, a.start))

    def test_small_gain_keeps_the_preferred_machine_and_explains_it(self):
        s = contested(gain=2 * 3600)
        p = plan(s)
        a = job(p)
        self.assertEqual(('m', dt(hour=9)), (a.demands[0].resource_id, a.start))
        self.assertIn('preferred alternative kept: another saves only 3600s < 7200s', a.explanation)
        self.assertFalse(verify_plan(s, p))

    def test_large_gain_leaves_the_preferred_machine(self):
        a = job(plan(contested(gain=1800)))
        self.assertEqual('x', a.demands[0].resource_id)
        self.assertIn('left preferred alternative: saves 3600s >= 1800s', a.explanation)

    def test_unused_options_keep_digests_and_json(self):
        s = scenario([op('a', 1)])
        text = dumps(s)
        self.assertNotIn('preferred_alternative', text)
        self.assertNotIn('max_lag_seconds', text)
        self.assertNotIn('alternative_switch_min_gain_seconds', text)
        workshop = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
        self.assertEqual('d904b098f8f108db3efa5a0dfa315a054d67bf7d7f42b3676e2025415fdf11ad', scenario_digest(workshop))
        self.assertEqual(scenario_digest(contested(7200)), scenario_digest(loads(dumps(contested(7200)))))

    def test_validation(self):
        with self.assertRaises(ValidationError):
            compile_tasks(contested(preferred=2))
        nest = scenario([op('n1', 1, alternatives=(alt('m'), alt('x')), preferred_alternative=0),
                         op('n2', 1, alternatives=(alt('m'), alt('x')), preferred_alternative=1)],
                        resources=MACHINES, nestings=(Nesting('N', ('n1', 'n2'), 3600),))
        with self.assertRaises(ValidationError):
            compile_tasks(nest)
        with self.assertRaises(ValidationError):
            compile_tasks(replace(contested(), settings=Settings(alternative_switch_min_gain_seconds=-1)))


class WaitTests(unittest.TestCase):
    def scenario(self, max_lag):
        # 'cut' ends at 9:00; 'blocker' keeps the bending machine until 12:00.
        return scenario([op('cut', 1, resource='m', order_id='o'),
                         op('blocker', 4, resource='x', priority=-1),
                         op('bend', 1, resource='x', order_id='o',
                            predecessors=(Dependency('cut', max_lag_seconds=max_lag),))],
                        resources=MACHINES)

    def test_waits_over_the_limit_are_reported(self):
        s = self.scenario(3600)
        result = waits_over_limit(s, plan(s))
        self.assertEqual((1, 1), (result['dependencies_checked'], result['over_limit']))
        wait = result['waits'][0]
        self.assertEqual(('cut', 'bend', 4 * 3600, 3 * 3600), (wait['predecessor_id'], wait['operation_id'],
                                                               wait['wait_seconds'], wait['excess_seconds']))
        self.assertEqual(0, waits_over_limit(self.scenario(5 * 3600), plan(self.scenario(5 * 3600)))['over_limit'])

    def test_max_lag_needs_fs_and_at_least_the_lag(self):
        for dependency in (Dependency('cut', 'SS', 0, 60), Dependency('cut', 'FS', 600, 60)):
            s = replace(self.scenario(None), operations=tuple(
                replace(o, predecessors=(dependency,)) if o.id == 'bend' else o for o in self.scenario(None).operations))
            with self.assertRaises(ValidationError):
                compile_tasks(s)

    def test_cli(self):
        s = self.scenario(3600)
        with tempfile.TemporaryDirectory() as tmp:
            sp, pp = Path(tmp, 's.json'), Path(tmp, 'p.json')
            sp.write_text(dumps(s), encoding='utf-8')
            pp.write_text(dumps(plan(s)), encoding='utf-8')
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, main(['waits', str(sp), str(pp)]))
            self.assertEqual(1, json.loads(out.getvalue())['over_limit'])


class CanonicalTests(unittest.TestCase):
    def test_options_round_trip_through_canonical_data(self):
        s = scenario([op('busy', 1, resource='m', order_id='o1'),
                      op('job', 2, order_id='o2', alternatives=(alt('m'), alt('x')), preferred_alternative=0,
                         predecessors=(Dependency('busy', max_lag_seconds=7200),))],
                     resources=MACHINES, settings=Settings(alternative_switch_min_gain_seconds=3600),
                     id='officina-demo', start=dt(day=19, hour=0), end=dt(day=26, hour=0))
        b = binding(['erp'], {d: {'sources': ['erp']} for d in ALL})
        result = build_scenario(b, batches_from({'erp': scenario_to_bundle(s)}, b), NOW)
        built = {o.id: o for o in result.scenario.operations}
        self.assertEqual(0, built['job'].preferred_alternative)
        self.assertEqual(7200, built['job'].predecessors[0].max_lag_seconds)
        self.assertEqual(3600, result.scenario.settings.alternative_switch_min_gain_seconds)
        original, rebuilt = json.loads(dumps(plan(s))), json.loads(dumps(plan(result.scenario)))
        original.pop('scenario_digest'), rebuilt.pop('scenario_digest')
        self.assertEqual(original, rebuilt)  # shift order differs, the plan does not


if __name__ == '__main__':
    unittest.main()
