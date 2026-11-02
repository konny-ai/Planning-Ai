"""Adversarial checks for setup repair fail-closed behavior and monotonic releases."""
import unittest
from planning_factory.domain.models import Dependency, Nesting
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_planner import plan_with_setups
from planning_factory.engine.setup_audit import audit_setups
from planning_factory.engine.scheduler import plan
from .helpers import dt, op, scenario


class SetupPlannerAdversarialTests(unittest.TestCase):
    def test_fixed_successor_is_not_silently_moved(self):
        s = scenario([op('a'), op('b', fixed_start=dt(hour=9))])
        rules = {'schema_version': 1, 'families': {'a':'A','b':'B'},
                 'machines': [{'resource_id':'m','initial_family':'A',
                               'transitions':[{'from_family':'A','to_family':'B','seconds':3600}]}]}
        with self.assertRaises(ValidationError):
            plan_with_setups(s, rules, budget=4)

    def test_standard_planner_and_original_scenario_unchanged(self):
        from planning_factory.adapters.json_io import dumps
        s = scenario([op('a'), op('b')])
        original = dumps(s)
        baseline = plan(s)
        rules = {'schema_version': 1, 'families': {'a':'A','b':'B'},
                 'machines': [{'resource_id':'m','initial_family':'A',
                               'transitions':[{'from_family':'A','to_family':'B','seconds':1800}]}]}
        result = plan_with_setups(s, rules, budget=8)
        self.assertTrue(audit_setups(result.scenario, result.plan, rules).valid)
        self.assertEqual(dumps(s), original)
        self.assertEqual(plan(s), baseline)

    def test_repairs_interleaved_fragments_using_actual_previous_run_end(self):
        # With fixed B in the middle of splittable A, its real machine end
        # is 14:00, not the later end of A's entire assignment.
        s = scenario([op('a', hours=7, splittable=True),
                      op('b', fixed_start=dt(hour=13))])
        baseline = plan(s)
        rules = {'schema_version': 1, 'families': {'a': 'A', 'b': 'B'},
                 'machines': [{'resource_id': 'm', 'initial_family': 'A',
                               'transitions': [
                                   {'from_family': 'A', 'to_family': 'B', 'seconds': 0},
                                   {'from_family': 'B', 'to_family': 'A', 'seconds': 3600}]}]}
        report = audit_setups(s, baseline, rules)
        self.assertFalse(report.valid)
        back_to_a = next(t for t in report.violations if t.successor_task == 'job:a')
        self.assertEqual(back_to_a.predecessor_end, dt(hour=14))
        result = plan_with_setups(s, rules, budget=8)
        self.assertTrue(result.setup_audit.valid)
        self.assertGreater(result.iterations, 0)
        self.assertEqual(next(a for a in result.plan.assignments if a.task_id == 'job:b').start,
                         dt(hour=13))
        self.assertEqual(audit_setups(result.scenario, result.plan, rules).violations, ())
        self.assertEqual(plan(s), baseline)

    def test_zero_duration_transition_must_be_explicit(self):
        s = scenario([op('a'), op('b')])
        rules = {'schema_version': 1, 'families': {'a':'A','b':'B'},
                 'machines': [{'resource_id':'m','initial_family':'A','transitions':[]}]}
        with self.assertRaises(ValidationError):
            plan_with_setups(s, rules, budget=2)
