"""Regression: when multiple machine constraints affect a task, keep the latest release."""
import unittest
from planning_factory.domain.models import Alternative, Demand, Resource
from planning_factory.engine.setup_planner import plan_with_setups
from planning_factory.engine.setup_audit import audit_setups
from .helpers import op, scenario


class SetupPlannerMultiMachineTests(unittest.TestCase):
    def test_release_constraint_is_monotonic_across_machine_rules(self):
        s = scenario([op('a', resource='m1'), op('b', resource='m2'),
                      op('c', alternatives=(Alternative((Demand('m1'),)), Alternative((Demand('m2'),))))],
                     resources=(Resource('m1','work'), Resource('m2','work')))
        rules = {'schema_version': 1, 'families': {'a':'A','b':'A','c':'B'},
                 'machines': [
                     {'resource_id':'m1','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':3600}]},
                     {'resource_id':'m2','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':7200}]},
                 ]}
        result = plan_with_setups(s, rules, budget=12)
        self.assertTrue(result.setup_audit.valid)
        self.assertTrue(audit_setups(result.scenario, result.plan, rules).valid)
        self.assertGreaterEqual(result.iterations, 1)
