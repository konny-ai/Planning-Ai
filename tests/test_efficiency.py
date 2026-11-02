"""#78 P9: per-resource efficiency stretches planned time; history estimates never invent a value."""
import json
import unittest
from dataclasses import replace
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads, scenario_digest
from planning_factory.canonical.builder import build_scenario
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.domain.models import Alternative, Demand, Interval, Resource
from planning_factory.domain.validation import ValidationError, compile_tasks
from planning_factory.engine.efficiency import apply_history, history_efficiency_bp
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tests.helpers import alt, dt, op, scenario
from tests.test_canonical import ALL, NOW, batches_from, binding

ROOT = Path(__file__).resolve().parents[1]


def booked(p, task_id):
    a = next(x for x in p.assignments if x.task_id == task_id)
    return a, sum(seg.seconds for seg in a.segments)


class EfficiencyTests(unittest.TestCase):
    def test_default_keeps_digests_and_goldens(self):
        workshop = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
        self.assertEqual('d904b098f8f108db3efa5a0dfa315a054d67bf7d7f42b3676e2025415fdf11ad', scenario_digest(workshop))
        self.assertEqual(dumps(plan(workshop)), (ROOT / 'examples' / 'workshop-plan.json').read_text(encoding='utf-8'))
        self.assertNotIn('efficiency_bp', dumps(scenario([op('a', 1)])))
        self.assertFalse(any(e.startswith('efficiency') for a in plan(workshop).assignments for e in a.explanation))

    def test_planned_time_is_standard_time_over_efficiency(self):
        s = scenario([op('a', 1)], resources=(Resource('m', 'work', efficiency_bp=8000),))
        p = plan(s)
        a, seconds = booked(p, 'job:a')
        self.assertEqual(4500, seconds)
        self.assertIn('efficiency 8000bp: 3600s planned as 4500s', a.explanation)
        self.assertEqual(scenario_digest(s), scenario_digest(loads(dumps(s))))
        self.assertNotEqual(scenario_digest(s), scenario_digest(scenario([op('a', 1)])))

    def test_a_bundle_runs_at_its_least_efficient_resource(self):
        s = scenario([op('a', 1, alternatives=(Alternative((Demand('m'), Demand('w'))),))],
                     resources=(Resource('m', 'work', efficiency_bp=12000), Resource('w', 'work', kind='operator',
                                                                                     efficiency_bp=9000)))
        self.assertEqual(4000, booked(plan(s), 'job:a')[1])
        fast = scenario([op('a', 1)], resources=(Resource('m', 'work', efficiency_bp=12000),))
        self.assertEqual(3000, booked(plan(fast), 'job:a')[1])

    def test_efficiency_drives_the_alternative_choice(self):
        machines = (Resource('m', 'work'), Resource('x', 'work', efficiency_bp=5000))
        s = scenario([op('busy', 1, resource='m', priority=0),
                      op('job', 2, priority=1, alternatives=(alt('m'), alt('x')))], resources=machines)
        a, seconds = booked(plan(s), 'job:job')
        self.assertEqual(('m', 7200), (a.demands[0].resource_id, seconds))  # x would take 4 h and finish later
        same = replace(s, resources=(Resource('m', 'work'), Resource('x', 'work')))
        self.assertEqual('x', booked(plan(same), 'job:job')[0].demands[0].resource_id)

    def test_backward_and_splittable_units_use_the_planned_time(self):
        s = scenario([op('a', 6, due=dt(day=20, hour=17), mode='backward', splittable=True)],
                     resources=(Resource('m', 'work', efficiency_bp=7500),))
        p = plan(s)
        a, seconds = booked(p, 'job:a')
        self.assertEqual(28800, seconds)
        self.assertEqual(('backward', dt(day=20, hour=17)), (a.effective_mode, a.end))

    def test_verifier_checks_the_planned_time(self):
        s = scenario([op('a', 1)], resources=(Resource('m', 'work', efficiency_bp=8000),))
        p = plan(s)
        a = p.assignments[0]
        standard = replace(a, segments=(Interval(a.start, a.start.replace(hour=a.start.hour + 1)),))
        errors = verify_plan(s, replace(p, assignments=(standard,)))
        self.assertIn('job:a: booked duration mismatch', errors)

    def test_validation(self):
        for value in (0, 100001, 1.5):
            with self.assertRaises(ValidationError, msg=value):
                compile_tasks(scenario([op('a', 1)], resources=(Resource('m', 'work', efficiency_bp=value),)))


class HistoryTests(unittest.TestCase):
    def test_too_few_samples_keep_one_hundred_percent(self):
        estimate = history_efficiency_bp('m', [(3600, 4000), (3600, 0), (0, 10)], min_samples=2)
        self.assertIsNone(estimate.efficiency_bp)
        self.assertTrue(estimate.detail.startswith('INSUFFICIENT_HISTORY: 1 usable samples < 2'))

    def test_ratio_of_totals(self):
        estimate = history_efficiency_bp('m', [(3600, 4500)] * 4 + [(7200, 9000)], min_samples=5)
        self.assertEqual((5, 8000), (estimate.samples, estimate.efficiency_bp))

    def test_apply_history_changes_only_measured_resources(self):
        s = scenario([op('a', 1), op('b', 1, resource='x')], resources=(Resource('m', 'work'), Resource('x', 'work')))
        updated, estimates = apply_history(s, {'m': [(3600, 4500)] * 5, 'x': [(3600, 3600)]})
        self.assertEqual({'m': 8000, 'x': 10000}, {r.id: r.efficiency_bp for r in updated.resources})
        self.assertEqual(['m', 'x'], [e.resource_id for e in estimates])
        self.assertIsNone(estimates[1].efficiency_bp)
        self.assertEqual(4500, booked(plan(updated), 'job:a')[1])


class CanonicalTests(unittest.TestCase):
    def test_efficiency_round_trips_through_canonical_data(self):
        s = scenario([op('a', 1, order_id='o1'), op('b', 1, order_id='o2', resource='x')],
                     resources=(Resource('m', 'work', efficiency_bp=8000), Resource('x', 'work')),
                     id='officina-demo', start=dt(day=19, hour=0), end=dt(day=26, hour=0))
        b = binding(['erp'], {d: {'sources': ['erp']} for d in ALL})
        bundle = scenario_to_bundle(s)
        self.assertEqual([8000, None], [r.get('efficiency_bp') for r in bundle['records']['resource']])
        result = build_scenario(b, batches_from({'erp': bundle}, b), NOW)
        self.assertEqual({'m': 8000, 'x': 10000}, {r.id: r.efficiency_bp for r in result.scenario.resources})
        original, rebuilt = json.loads(dumps(plan(s))), json.loads(dumps(plan(result.scenario)))
        original.pop('scenario_digest'), rebuilt.pop('scenario_digest')
        self.assertEqual(original, rebuilt)


if __name__ == '__main__':
    unittest.main()
