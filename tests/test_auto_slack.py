"""#78 P4: the automatic mode can choose backward by estimated margin instead of distance to the due date."""
import json
import unittest
from dataclasses import replace
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads, scenario_digest
from planning_factory.canonical.builder import build_scenario
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.domain.models import Dependency, Settings
from planning_factory.domain.validation import ValidationError, compile_tasks
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tests.helpers import dt, op, scenario
from tests.test_canonical import ALL, NOW, batches_from, binding

ROOT = Path(__file__).resolve().parents[1]


def chain(**settings):
    """Twelve chained 4 h operations due in eleven days: far for the date rule, tight for the slack rule."""
    ops = [op(f's{i}', 4, order_id='o', due=dt(day=30, hour=17), mode='auto',
              predecessors=(Dependency(f's{i - 1}'),) if i else ()) for i in range(12)]
    return scenario(ops, id='officina-demo', end=dt(day=31, hour=0), settings=Settings(**settings))


def first(p):
    return next(a for a in p.assignments if a.task_id == 'job:s0')


def note(a):
    return next((e for e in a.explanation if e.startswith('auto by slack')), None)


class AutoSlackTests(unittest.TestCase):
    def test_default_rule_is_unchanged(self):
        workshop = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
        self.assertEqual('d904b098f8f108db3efa5a0dfa315a054d67bf7d7f42b3676e2025415fdf11ad', scenario_digest(workshop))
        self.assertEqual(dumps(plan(workshop)), (ROOT / 'examples' / 'workshop-plan.json').read_text(encoding='utf-8'))
        text = dumps(chain())
        self.assertNotIn('auto_rule', text)
        self.assertNotIn('auto_slack_seconds', text)
        p = plan(chain())
        self.assertEqual('backward', first(p).effective_mode)  # due date beyond auto_backward_days
        self.assertIsNone(note(first(p)))

    def test_slack_rule_goes_forward_when_the_margin_is_below_the_threshold(self):
        s = chain(auto_rule='slack', auto_slack_seconds=5 * 86400)
        p = plan(s)
        self.assertFalse(verify_plan(s, p))
        a = first(p)
        self.assertEqual('forward', a.effective_mode)
        self.assertRegex(note(a), r'^auto by slack: margin \d+s vs 432000s -> forward$')
        self.assertEqual(dt(day=19, hour=8), a.start)
        self.assertTrue(all(o.tardiness_seconds == 0 for o in p.orders))

    def test_slack_rule_goes_backward_when_the_margin_is_large(self):
        s = chain(auto_rule='slack', auto_slack_seconds=3600)
        p = plan(s)
        self.assertFalse(verify_plan(s, p))
        a = first(p)
        self.assertEqual('backward', a.effective_mode)
        self.assertTrue(note(a).endswith('vs 3600s -> backward'))
        self.assertGreater(a.start, dt(day=19, hour=8))
        self.assertTrue(all(o.tardiness_seconds == 0 for o in p.orders))

    def test_only_auto_units_are_affected(self):
        s = scenario([op('f', 1, due=dt(day=30, hour=17), mode='forward')],
                     end=dt(day=31, hour=0), settings=Settings(auto_rule='slack'))
        a = plan(s).assignments[0]
        self.assertEqual('forward', a.effective_mode)
        self.assertIsNone(note(a))

    def test_validation(self):
        with self.assertRaises(ValidationError):
            compile_tasks(chain(auto_rule='nearest'))
        with self.assertRaises(ValidationError):
            compile_tasks(chain(auto_rule='slack', auto_slack_seconds=-1))
        s = chain(auto_rule='slack', auto_slack_seconds=7200)
        self.assertEqual(scenario_digest(s), scenario_digest(loads(dumps(s))))
        self.assertNotEqual(scenario_digest(chain()), scenario_digest(s))

    def test_options_round_trip_through_canonical_data(self):
        s = chain(auto_rule='slack', auto_slack_seconds=7200)
        b = binding(['erp'], {d: {'sources': ['erp']} for d in ALL})
        result = build_scenario(b, batches_from({'erp': scenario_to_bundle(s)}, b), NOW)
        self.assertEqual(('slack', 7200), (result.scenario.settings.auto_rule, result.scenario.settings.auto_slack_seconds))
        original, rebuilt = json.loads(dumps(plan(s))), json.loads(dumps(plan(replace(result.scenario, end=s.end))))  # builder horizon is 7 days
        original.pop('scenario_digest'), rebuilt.pop('scenario_digest')
        self.assertEqual(original, rebuilt)
        default = build_scenario(b, batches_from({'erp': scenario_to_bundle(chain())}, b), NOW)
        self.assertEqual('due_distance', default.scenario.settings.auto_rule)


if __name__ == '__main__':
    unittest.main()
