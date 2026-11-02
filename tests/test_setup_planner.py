"""Bounded setup-aware planning is opt-in, monotonic and independently audited."""
import unittest
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.setup_audit import audit_setups
from planning_factory.engine.setup_planner import plan_with_setups
from .helpers import op, scenario


class SetupPlannerTests(unittest.TestCase):
    def setUp(self):
        self.s = scenario([op('a'), op('b')])
        self.rules = {'schema_version': 1, 'families': {'a': 'A', 'b': 'B'},
                      'machines': [{'resource_id': 'm', 'initial_family': 'A',
                                    'transitions': [{'from_family': 'A', 'to_family': 'B', 'seconds': 3600}]}]}

    def test_setup_plan_repairs_and_reaudits(self):
        baseline = plan(self.s)
        self.assertFalse(audit_setups(self.s, baseline, self.rules).valid)
        result = plan_with_setups(self.s, self.rules, budget=8)
        self.assertTrue(result.setup_audit.valid)
        self.assertGreater(result.iterations, 0)
        self.assertEqual(result.plan.scenario_digest, __import__('planning_factory.adapters.json_io', fromlist=['scenario_digest']).scenario_digest(result.scenario))
        self.assertEqual(result.plan.assignments[0].task_id, baseline.assignments[0].task_id)
        self.assertTrue(any(o.release is not None for o in result.scenario.operations))

    def test_repairs_changeover_after_noninterleaved_split(self):
        s = scenario([op('a', hours=7, splittable=True), op('b')])
        baseline = plan(s)
        self.assertGreater(len(next(a for a in baseline.assignments
                                    if a.task_id == 'job:a').segments), 1)
        self.assertFalse(audit_setups(s, baseline, self.rules).valid)
        result = plan_with_setups(s, self.rules, budget=6)
        self.assertTrue(result.setup_audit.valid)
        self.assertEqual(result.plan, plan(result.scenario))
        self.assertEqual(audit_setups(result.scenario, result.plan, self.rules).violations, ())
        self.assertEqual(plan(s), baseline)

    def test_default_planner_is_unchanged(self):
        before = plan(self.s)
        plan_with_setups(self.s, self.rules)
        self.assertEqual(plan(self.s), before)

    def test_budget_and_infeasible_setup_fail_closed(self):
        with self.assertRaises(ValueError):
            plan_with_setups(self.s, self.rules, budget=0)
        rules = {'schema_version': 1, 'families': {'a': 'A', 'b': 'B'},
                 'machines': [{'resource_id': 'm', 'initial_family': 'A',
                               'transitions': [{'from_family': 'A', 'to_family': 'B', 'seconds': 999999999}]}]}
        with self.assertRaises(ValidationError):
            plan_with_setups(self.s, rules, budget=2)

    def test_nested_task_gets_shared_release(self):
        from planning_factory.domain.models import Nesting
        s = scenario([op('a'), op('b'), op('c')], nestings=(Nesting('ab', ('a','b'), 3600),))
        rules = {'schema_version': 1, 'families': {'a': 'A', 'b': 'A', 'c': 'B'},
                 'machines': [{'resource_id': 'm', 'initial_family': 'A',
                               'transitions': [{'from_family': 'A', 'to_family': 'B', 'seconds': 1800},
                                             {'from_family': 'B', 'to_family': 'A', 'seconds': 0}]}]}
        result = plan_with_setups(s, rules, budget=8)
        self.assertTrue(result.setup_audit.valid)
        self.assertEqual(len([o for o in result.scenario.operations if o.release]), 1)
