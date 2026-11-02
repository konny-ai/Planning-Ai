"""Bounded setup alternative search validates every returned candidate."""
import unittest
from planning_factory.domain.models import Alternative, Demand, Resource, Nesting
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_search import search_setup_alternatives
from planning_factory.engine.verification import verify_plan
from planning_factory.adapters.json_io import dumps
from .helpers import dt, op, scenario


class SetupAlternativeSearchTests(unittest.TestCase):
    def setUp(self):
        self.s = scenario([op('a', resource='m1'), op('b', resource='m2'),
                           op('c', alternatives=(Alternative((Demand('m1'),)), Alternative((Demand('m2'),))))],
                          resources=(Resource('m1','work'), Resource('m2','work')))
        self.rules = {'schema_version':1,'families':{'a':'A','b':'A','c':'B'},
                      'machines':[{'resource_id':'m1','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':1800}]},
                                  {'resource_id':'m2','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':3600}]}]}

    def test_returns_independently_audited_setup_plan(self):
        original = dumps(self.s)
        result = search_setup_alternatives(self.s, self.rules, budget=8, repair_budget=12)
        self.assertTrue(result.setup_audit.valid)
        self.assertEqual(verify_plan(result.scenario, result.plan), ())
        self.assertLessEqual(result.candidates_evaluated, 8)
        self.assertEqual(dumps(self.s), original)

    def test_budgets_are_strict(self):
        for budget in (0, 33, True, 1.5):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                search_setup_alternatives(self.s, self.rules, budget=budget)

    def test_infeasible_matrix_fails_closed(self):
        rules = {'schema_version':1,'families':{'a':'A','b':'B','c':'C'},
                 'machines':[{'resource_id':'m1','initial_family':'A','transitions':[]},
                             {'resource_id':'m2','initial_family':'A','transitions':[]}]}
        with self.assertRaises(ValidationError):
            search_setup_alternatives(self.s, rules, budget=4, repair_budget=2)

    def test_search_includes_combined_rotations_for_multiple_operations(self):
        s = scenario([
            op('a', alternatives=(Alternative((Demand('m1'),)), Alternative((Demand('m2'),)))),
            op('b', alternatives=(Alternative((Demand('m1'),)), Alternative((Demand('m2'),)))),
        ], resources=(Resource('m1','work'), Resource('m2','work')))
        rules = {'schema_version':1,'families':{'a':'A','b':'B'},
                 'machines':[{'resource_id':'m1','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':0}]},
                             {'resource_id':'m2','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':0}]}]}
        result = search_setup_alternatives(s, rules, budget=4, repair_budget=8)
        self.assertLessEqual(result.candidates_evaluated, 4)
        self.assertTrue(result.setup_audit.valid)
        self.assertEqual(verify_plan(result.scenario, result.plan), ())
        self.assertEqual(result, search_setup_alternatives(s, rules, budget=4, repair_budget=8))

    def test_candidate_forces_selected_alternative_instead_of_only_reordering(self):
        s = scenario([op('x', alternatives=(Alternative((Demand('m1'),)), Alternative((Demand('m2'),))))],
                     resources=(Resource('m1','work'), Resource('m2','work')))
        rules = {'schema_version':1,'families':{'x':'B'},
                 'machines':[{'resource_id':'m1','initial_family':'A','transitions':[]},
                             {'resource_id':'m2','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':0}]}]}
        result = search_setup_alternatives(s, rules, budget=8, repair_budget=4)
        self.assertEqual(len(result.scenario.operations[0].alternatives), 1)
        self.assertEqual(result.scenario.operations[0].alternatives[0].demands[0].resource_id, 'm2')
        self.assertTrue(result.setup_audit.valid)

    def test_small_budget_tries_a_single_switch_before_cartesian_forcing(self):
        # The old enumeration evaluated only baseline + both first alternatives
        # with budget=2, missing the one necessary machine switch.
        alts = (Alternative((Demand('m1'),)), Alternative((Demand('m2'),)))
        other = (Alternative((Demand('m3'),)), Alternative((Demand('m4'),)))
        s = scenario([op('x', alternatives=alts), op('y', alternatives=other)],
                     resources=tuple(Resource(f'm{i}', 'work') for i in range(1, 5)))
        rules = {'schema_version': 1, 'families': {'x': 'B', 'y': 'A'},
                 'machines': [
                     {'resource_id': 'm1', 'initial_family': 'A', 'transitions': []},
                     {'resource_id': 'm2', 'initial_family': 'A', 'transitions': [
                         {'from_family': 'A', 'to_family': 'B', 'seconds': 0}]},
                     {'resource_id': 'm3', 'initial_family': 'A', 'transitions': []},
                     {'resource_id': 'm4', 'initial_family': 'A', 'transitions': []}]}
        result = search_setup_alternatives(s, rules, budget=2, repair_budget=4)
        self.assertEqual(result.candidates_evaluated, 2)
        self.assertEqual(result.winner_order, (('x', ('m2',)),))
        self.assertEqual(verify_plan(result.scenario, result.plan), ())
        self.assertEqual(result, search_setup_alternatives(s, rules, budget=2, repair_budget=4))

    def test_two_simultaneous_machine_switches_fit_budget_four(self):
        # Neither independent switch is sufficient; baseline and two singles
        # must be rejected before the fourth, jointly feasible candidate.
        s = scenario([
            op('x', alternatives=(Alternative((Demand('m1'),)), Alternative((Demand('m2'),)))),
            op('y', alternatives=(Alternative((Demand('m3'),)), Alternative((Demand('m4'),)))),
        ], resources=tuple(Resource(f'm{i}', 'work') for i in range(1, 5)))
        rules = {'schema_version': 1, 'families': {'x': 'B', 'y': 'B'},
                 'machines': [
                     {'resource_id': 'm1', 'initial_family': 'A', 'transitions': []},
                     {'resource_id': 'm2', 'initial_family': 'A', 'transitions': [
                         {'from_family': 'A', 'to_family': 'B', 'seconds': 0}]},
                     {'resource_id': 'm3', 'initial_family': 'A', 'transitions': []},
                     {'resource_id': 'm4', 'initial_family': 'A', 'transitions': [
                         {'from_family': 'A', 'to_family': 'B', 'seconds': 0}]}]}
        with self.assertRaises(ValidationError):
            search_setup_alternatives(s, rules, budget=3, repair_budget=4)
        result = search_setup_alternatives(s, rules, budget=4, repair_budget=4)
        self.assertEqual(result.candidates_evaluated, 4)
        self.assertEqual(result.winner_order, (('x', ('m2',)), ('y', ('m4',))))
        self.assertEqual(len(result.rejected), 3)
        self.assertIn('baseline', result.rejected[0])
        self.assertTrue(result.setup_audit.valid)
        self.assertEqual(verify_plan(result.scenario, result.plan), ())

    def test_nesting_is_atomic_and_simultaneous_bundle_survives_switch(self):
        # Two orders share one nesting: its duration is booked once, on one
        # machine and a mandatory operator together, even after a machine swap.
        one = Alternative((Demand('m1'), Demand('worker')))
        two = Alternative((Demand('m2'), Demand('worker')))
        s = scenario([
            op('part-a', order_id='order-a', alternatives=(one, two)),
            op('part-b', order_id='order-b', alternatives=(one, two)),
        ], nestings=(Nesting('nest1', ('part-a', 'part-b'), 3600),),
           resources=(Resource('m1', 'work'), Resource('m2', 'work'),
                      Resource('worker', 'work', kind='operator')))
        rules = {'schema_version': 1, 'families': {'part-a': 'B', 'part-b': 'B'},
                 'machines': [
                     {'resource_id': 'm1', 'initial_family': 'A', 'transitions': []},
                     {'resource_id': 'm2', 'initial_family': 'A', 'transitions': [
                         {'from_family': 'A', 'to_family': 'B', 'seconds': 0}]}]}
        before = dumps(s)
        result = search_setup_alternatives(s, rules, budget=2, repair_budget=4)
        self.assertTrue(result.setup_audit.valid)
        self.assertEqual(verify_plan(result.scenario, result.plan), ())
        assignment, = result.plan.assignments
        self.assertEqual(assignment.task_id, 'nest:nest1')
        self.assertEqual(set(assignment.operation_ids), {'part-a', 'part-b'})
        self.assertEqual({d.resource_id for d in assignment.demands}, {'m2', 'worker'})
        self.assertEqual(dumps(s), before)

    def test_pinned_alternative_is_kept_while_other_operations_are_forced(self):
        two = (Alternative((Demand('m1'),)), Alternative((Demand('m2'),)))
        s = scenario([op('x', alternatives=two),
                      op('p', alternatives=(Alternative((Demand('m3'),)), Alternative((Demand('m4'),))),
                         fixed_start=dt(hour=8), fixed_alternative=1)],
                     resources=(Resource('m1','work'), Resource('m2','work'), Resource('m3','work'), Resource('m4','work')))
        rules = {'schema_version':1,'families':{'x':'B','p':'A'},
                 'machines':[{'resource_id':'m1','initial_family':'A','transitions':[]},
                             {'resource_id':'m2','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':0}]},
                             {'resource_id':'m3','initial_family':'A','transitions':[]},
                             {'resource_id':'m4','initial_family':'A','transitions':[]}]}
        result = search_setup_alternatives(s, rules, budget=8, repair_budget=4)
        ops = {o.id: o for o in result.scenario.operations}
        self.assertEqual(ops['x'].alternatives[0].demands[0].resource_id, 'm2')
        self.assertEqual(ops['p'].alternatives, s.operations[1].alternatives)
        self.assertEqual(ops['p'].fixed_alternative, 1)
        self.assertEqual(verify_plan(result.scenario, result.plan), ())
