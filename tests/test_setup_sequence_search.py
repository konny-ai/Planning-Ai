"""Bounded setup sequence search: true phase-specific setup and full audit."""
import json
import unittest
from planning_factory.domain.models import Dependency, Nesting, Resource
from planning_factory.domain.validation import ValidationError
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.engine.setup_stage_scheduler import schedule_setup_stages
from planning_factory.engine.setup_sequence_search import (
    default_machine_sequence, optimize_setup_sequence, sequence_metrics,
    best_start_sequence, state_grouped_sequence,
)
from planning_factory.engine.setup_capacity import audit_stage_capacity
from planning_factory.engine.verification import verify_plan
from .test_setup_stage_scheduler import base, spec
from .helpers import dt, op, scenario


def three():
    data = spec()
    data['phases'][1]['target_state'] = 'RED'
    extra = dict(data['phases'][0], id='job:c')
    data['phases'].append(extra)
    return load_setup_contract(json.dumps(data))


class SetupSequenceSearchTests(unittest.TestCase):
    def test_fewer_machine_setups_without_changing_baseline(self):
        s = base([op('a'), op('b'), op('c')])
        c = three()
        search = optimize_setup_sequence(s, c, ('job:a', 'job:b', 'job:c'),
                                         objective='setup', max_evaluations=12)
        self.assertTrue(search.improved)
        self.assertEqual(search.baseline.setup_seconds, 5400)
        self.assertEqual(search.selected.setup_seconds, 1800)
        self.assertEqual(search.sequence, ('job:b', 'job:a', 'job:c'))
        self.assertEqual(verify_plan(search.winner.scenario, search.winner.plan), ())
        self.assertEqual(audit_stage_capacity(c, s, search.sequence,
                                              search.winner.bookings), ())
        self.assertEqual(search, optimize_setup_sequence(
            s, c, ('job:a', 'job:b', 'job:c'), objective='setup',
            max_evaluations=12))

    def test_budget_one_retains_initial_solution(self):
        s = base([op('a'), op('b'), op('c')])
        result = optimize_setup_sequence(s, three(), ('job:a', 'job:b', 'job:c'),
                                         objective='delivery', max_evaluations=1)
        self.assertFalse(result.improved)
        self.assertEqual(result.evaluated, 1)
        self.assertEqual(result.sequence, ('job:a', 'job:b', 'job:c'))

    def test_forbidden_reorder_rejected_not_returned(self):
        # B depends on A, so moving B before A creates a cycle
        # with the required per-machine order, and is rejected.
        s = base([op('a'), op('b', predecessors=(Dependency('a', 'FS'),))])
        data = spec()
        c = load_setup_contract(json.dumps(data))
        result = optimize_setup_sequence(s, c, ('job:a', 'job:b'),
                                         objective='setup', max_evaluations=8)
        self.assertEqual(result.sequence, ('job:a', 'job:b'))
        self.assertEqual(result.rejected, 1)
        self.assertEqual(result.evaluated, 2)

    def test_rejects_ambiguous_objectives_and_noninteger_budget(self):
        s = base([op('a'), op('b')])
        c = load_setup_contract(json.dumps(spec()))
        for invalid in ('weighted', '', 'optimal'):
            with self.assertRaises(ValueError):
                optimize_setup_sequence(s, c, ('job:a', 'job:b'),
                                        objective=invalid)
        with self.assertRaises(ValueError):
            optimize_setup_sequence(s, c, ('job:a', 'job:b'),
                                    objective='setup', max_evaluations=True)

    def test_interleaved_machines_do_not_change_unrelated_dispatch(self):
        data = spec()
        data['resources'].append({'id': 'm2', 'kind': 'machine'})
        data['initial_machine_states']['m2'] = 'RED'
        data['phases'][1]['machine_id'] = 'm2'
        data['phases'][1]['setup_requirements'][0]['candidate_ids'] = ['m2']
        data['phases'][1]['run_requirements'][0]['candidate_ids'] = ['m2']
        data['phases'].append(dict(data['phases'][0], id='job:c'))
        s = scenario([op('a'), op('b', resource='m2'), op('c')],
                     resources=(Resource('m', 'work'), Resource('m2', 'work'),
                                Resource('op', 'work', kind='operator'),
                                Resource('tool', 'work', kind='tool')))
        c = load_setup_contract(json.dumps(data))
        first = schedule_setup_stages(s, c, ('job:a', 'job:b', 'job:c'))
        other = schedule_setup_stages(s, c, ('job:b', 'job:a', 'job:c'))
        normalize = lambda result: {
            (x.phase_id, x.stage): (x.segments, x.allocations)
            for x in result.bookings}
        self.assertEqual(normalize(first), normalize(other))
        self.assertEqual(sequence_metrics(s, first), sequence_metrics(s, other))

    def test_empty_scenario_has_zero_kpis_and_no_search_failure(self):
        s = scenario([])
        contract = load_setup_contract(json.dumps({
            'schema_version': 1, 'resources': [],
            'initial_machine_states': {}, 'phases': []}))
        result = optimize_setup_sequence(s, contract, (), objective='delivery')
        self.assertEqual(result.baseline.as_dict(), dict(
            late_orders=0, tardiness_seconds=0, setup_seconds=0, makespan_seconds=0))
        self.assertEqual(result.sequence, ())
        self.assertEqual(result.evaluated, 1)

    def test_nesting_counts_all_original_order_owners(self):
        data = spec()
        data['phases'] = [dict(data['phases'][0], id='nest:shared')]
        s = base([op('a', order_id='A', due=dt(hour=8, minute=40)),
                  op('b', order_id='B', due=dt(hour=13))],
                 nestings=(Nesting('shared', ('a', 'b'), 3600),))
        c = load_setup_contract(json.dumps(data))
        search = optimize_setup_sequence(s, c, ('nest:shared',),
                                         objective='delivery', max_evaluations=8)
        measured = sequence_metrics(s, search.winner)
        self.assertEqual(measured.late_orders, 1)
        self.assertEqual(measured.setup_seconds, 1800)
        self.assertEqual(search.evaluated, 1)


if __name__ == '__main__':
    unittest.main()


class DefaultSequenceAndInsertionTests(unittest.TestCase):
    def test_default_sequence_is_edd_and_precedence_safe(self):
        s = base([op('a', due=dt(day=29)), op('b', due=dt(day=22)),
                  op('c', due=dt(day=21), predecessors=(Dependency('a', 'FS'),))])
        self.assertEqual(default_machine_sequence(s), ('job:b', 'job:a', 'job:c'))
        self.assertEqual(default_machine_sequence(s), default_machine_sequence(s))

    def test_default_sequence_puts_completed_history_first_and_searches(self):
        s = base([op('a', due=dt(day=20)), op('b', hours=0, completed_at=dt(hour=0)),
                  op('c')])
        seq = default_machine_sequence(s)
        self.assertEqual(seq[0], 'job:b')
        auto = optimize_setup_sequence(s, three(), objective='setup', max_evaluations=12)
        self.assertEqual(auto.sequence[0], 'job:b')
        self.assertEqual(verify_plan(auto.winner.scenario, auto.winner.plan), ())

    def test_search_without_initial_sequence_uses_default(self):
        s = base([op('a'), op('b'), op('c')])
        auto = optimize_setup_sequence(s, three(), objective='setup', max_evaluations=12)
        self.assertEqual(len(auto.sequence), 3)
        self.assertEqual(verify_plan(auto.winner.scenario, auto.winner.plan), ())
        self.assertLessEqual(auto.selected.setup_seconds, auto.baseline.setup_seconds)

    def test_insertion_neighbourhood_reaches_nonadjacent_moves(self):
        from planning_factory.engine.setup_sequence_search import _neighbours
        seq = ('x', 'y', 'z')
        m = {t: 'm' for t in seq}
        swaps = set(_neighbours(seq, m))
        ins = set(_neighbours(seq, m, 'insertion'))
        self.assertTrue(swaps < ins)
        self.assertIn(('y', 'z', 'x'), ins)
        self.assertIn(('z', 'x', 'y'), ins)

    def test_rejects_unknown_neighbourhood(self):
        s = base([op('a'), op('b'), op('c')])
        with self.assertRaises(ValueError):
            optimize_setup_sequence(s, three(), objective='setup', neighbourhood='x')


class StateGroupedStartTests(unittest.TestCase):
    def test_groups_equal_states_and_cuts_setups(self):
        s = base([op('a'), op('b'), op('c')])
        c = three()  # a BLUE, b RED, c BLUE; machine starts RED
        seq = state_grouped_sequence(s, c, ('job:a', 'job:b', 'job:c'))
        self.assertEqual(seq, ('job:b', 'job:a', 'job:c'))
        solution = schedule_setup_stages(s, c, seq)
        self.assertEqual(sequence_metrics(s, solution).setup_seconds, 1800)

    def test_is_a_permutation_and_deterministic(self):
        s = base([op('a'), op('b'), op('c')])
        c = three()
        seq = state_grouped_sequence(s, c)
        self.assertEqual(sorted(seq), ['job:a', 'job:b', 'job:c'])
        self.assertEqual(seq, state_grouped_sequence(s, c))

    def test_best_start_prefers_grouped_when_it_scores_better(self):
        s = base([op('a'), op('b'), op('c')])
        self.assertEqual(best_start_sequence(s, three(), objective='setup'),
                         ('job:b', 'job:a', 'job:c'))

    def test_best_start_requires_explicit_objective(self):
        with self.assertRaises(ValueError):
            best_start_sequence(base([op('a'), op('b'), op('c')]), three(), objective='x')


class JustInTimeTailTests(unittest.TestCase):
    def _s(self, due_day):
        return base([op('a', due=dt(day=due_day, hour=17)), op('b', due=dt(day=due_day, hour=17))])

    def test_terminal_run_is_delayed_by_slack_and_audited(self):
        from planning_factory.engine.setup_sequence_search import just_in_time_tail
        s = self._s(23)
        c = load_setup_contract(json.dumps(spec()))
        seq = ('job:a', 'job:b')
        result = just_in_time_tail(s, c, seq)
        self.assertEqual({pid for pid, _ in result.delayed}, {'job:a', 'job:b'})
        before = {(b.phase_id, b.stage): b for b in schedule_setup_stages(s, c, seq).bookings}
        after = {(b.phase_id, b.stage): b for b in result.solution.bookings}
        # setup stays early, both runs moved later, machine order preserved
        self.assertEqual(before[('job:a', 'setup')], after[('job:a', 'setup')])
        self.assertGreater(after[('job:b', 'run')].segments[0].start,
                           before[('job:b', 'run')].segments[0].start)
        self.assertGreater(after[('job:a', 'run')].segments[0].start,
                           before[('job:a', 'run')].segments[0].start)
        self.assertLessEqual(after[('job:a', 'run')].segments[-1].end,
                             after[('job:b', 'run')].segments[0].start)
        self.assertEqual(result.selected.late_orders, 0)
        self.assertEqual(audit_stage_capacity(c, result.scenario, seq,
                                              result.solution.bookings), ())
        # input scenario untouched
        self.assertTrue(all(o.release is None for o in s.operations))

    def test_no_slack_means_no_change(self):
        from planning_factory.engine.setup_sequence_search import just_in_time_tail
        s = base([op('a'), op('b')])  # no due dates
        c = load_setup_contract(json.dumps(spec()))
        result = just_in_time_tail(s, c, ('job:a', 'job:b'))
        self.assertEqual(result.delayed, ())
        self.assertEqual(result.scenario, s)

    def test_task_with_successor_on_other_machine_is_delayed_within_gap(self):
        from planning_factory.engine.setup_sequence_search import just_in_time_tail
        from planning_factory.domain.models import Dependency as Dep
        data = spec()
        data['resources'].append({'id': 'm2', 'kind': 'machine'})
        data['initial_machine_states']['m2'] = 'RED'
        data['phases'][1] = dict(data['phases'][1], machine_id='m2',
                                 setup_requirements=[dict(r, candidate_ids=['m2']) if r['kind'] == 'machine' else r
                                                     for r in data['phases'][1]['setup_requirements']],
                                 run_requirements=[dict(r, candidate_ids=['m2']) if r['kind'] == 'machine' else r
                                                   for r in data['phases'][1]['run_requirements']])
        c = load_setup_contract(json.dumps(data))
        from planning_factory.domain.models import Resource
        s = scenario([op('a'), op('b', resource='m2', predecessors=(Dep('a', 'FS'),),
                          release=dt(day=21, hour=8), due=dt(day=23, hour=17))],
                     resources=(Resource('m', 'work', kind='machine'), Resource('m2', 'work', kind='machine'),
                                Resource('op', 'work', kind='operator'), Resource('tool', 'work', kind='tool')))
        seq = ('job:a', 'job:b')
        result = just_in_time_tail(s, c, seq)
        self.assertIn('job:a', [p for p, _ in result.delayed])
        runs = {b.phase_id: b for b in result.solution.bookings if b.stage == 'run'}
        self.assertLessEqual(runs['job:a'].segments[-1].end, runs['job:b'].segments[0].start)
        self.assertEqual(result.selected.late_orders, 0)
        self.assertEqual(audit_stage_capacity(c, result.scenario, seq, result.solution.bookings), ())


class BackwardOptionATests(unittest.TestCase):
    def test_setup_is_pulled_against_its_run_and_runs_are_late(self):
        from planning_factory.engine.setup_sequence_search import backward_setup_before_run
        s = base([op('a', due=dt(day=23, hour=17)), op('b', due=dt(day=23, hour=17)),
                  op('c', due=dt(day=23, hour=17))])
        c = three()
        seq = ('job:a', 'job:b', 'job:c')
        result = backward_setup_before_run(s, c, seq)
        forward = {(b.phase_id, b.stage): b for b in schedule_setup_stages(s, c, seq).bookings}
        after = {(b.phase_id, b.stage): b for b in result.solution.bookings}
        gaps = dict(result.setup_gaps)
        self.assertTrue(result.delayed_runs)
        self.assertTrue(any(g == 0 for g in gaps.values()))
        for pid in ('job:a', 'job:b', 'job:c'):
            self.assertGreaterEqual(after[(pid, 'run')].segments[0].start,
                                    forward[(pid, 'run')].segments[0].start)
        for (pid, stage), b in after.items():
            if stage == 'setup':
                self.assertGreaterEqual(b.segments[0].start, forward[(pid, 'setup')].segments[0].start)
        self.assertEqual(result.selected.late_orders, 0)
        self.assertEqual(result.selected.setup_seconds, result.baseline.setup_seconds)
        self.assertEqual(audit_stage_capacity(c, result.scenario, seq, result.solution.bookings), ())
        self.assertEqual(result, backward_setup_before_run(s, c, seq))

    def test_without_due_dates_runs_stay_and_setups_are_tightened(self):
        from planning_factory.engine.setup_sequence_search import backward_setup_before_run
        s = base([op('a'), op('b'), op('c')])
        c = three()
        seq = ('job:a', 'job:b', 'job:c')
        result = backward_setup_before_run(s, c, seq)
        forward = {(b.phase_id, b.stage): b for b in schedule_setup_stages(s, c, seq).bookings}
        after = {(b.phase_id, b.stage): b for b in result.solution.bookings}
        self.assertEqual(result.delayed_runs, ())
        for pid in seq:
            self.assertEqual(after[(pid, 'run')], forward[(pid, 'run')])
        # c's run starts after the lunch break: its setup is pulled to the latest slot
        self.assertGreater(after[('job:c', 'setup')].segments[0].start,
                           forward[('job:c', 'setup')].segments[0].start)
        self.assertEqual(dict(result.setup_gaps)['job:c'], 3600)  # lunch break cannot host a setup
        self.assertEqual(audit_stage_capacity(c, result.scenario, seq, result.solution.bookings), ())


class BackwardSequenceSearchTests(unittest.TestCase):
    def _s(self):
        return base([op(n, due=dt(day=23, hour=17)) for n in 'abc'])

    def test_search_scores_the_backward_plan_and_cuts_setup(self):
        from planning_factory.engine.setup_sequence_search import optimize_backward_sequence
        s, c = self._s(), three()
        found = optimize_backward_sequence(s, c, ('job:a', 'job:b', 'job:c'),
                                           objective='setup', max_evaluations=12,
                                           neighbourhood='insertion')
        self.assertTrue(found.improved)
        self.assertEqual(found.baseline.setup_seconds, 5400)
        self.assertEqual(found.selected.setup_seconds, 1800)
        self.assertEqual(found.selected.late_orders, 0)
        self.assertEqual(audit_stage_capacity(c, found.result.scenario, found.sequence,
                                              found.result.solution.bookings), ())
        self.assertEqual(found, optimize_backward_sequence(
            s, c, ('job:a', 'job:b', 'job:c'), objective='setup', max_evaluations=12,
            neighbourhood='insertion'))

    def test_budget_one_keeps_initial_order_and_default_start_works(self):
        from planning_factory.engine.setup_sequence_search import optimize_backward_sequence
        s, c = self._s(), three()
        one = optimize_backward_sequence(s, c, ('job:a', 'job:b', 'job:c'),
                                         objective='delivery', max_evaluations=1)
        self.assertFalse(one.improved)
        self.assertEqual(one.sequence, ('job:a', 'job:b', 'job:c'))
        auto = optimize_backward_sequence(s, c, objective='gap', max_evaluations=6)
        self.assertEqual(sorted(auto.sequence), ['job:a', 'job:b', 'job:c'])
        self.assertLessEqual(auto.selected_gap_seconds, auto.baseline_gap_seconds)

    def test_rejects_flow_and_bad_arguments(self):
        from planning_factory.engine.setup_sequence_search import optimize_backward_sequence
        s, c = self._s(), three()
        with self.assertRaises(ValueError):
            optimize_backward_sequence(s, c, objective='flow')
        with self.assertRaises(ValueError):
            optimize_backward_sequence(s, c, objective='setup', max_evaluations=100)
        with self.assertRaises(ValueError):
            optimize_backward_sequence(s, c, objective='setup', neighbourhood='x')


class TaskModesInStagedPlanningTests(unittest.TestCase):
    def test_only_backward_tasks_are_delayed(self):
        from planning_factory.engine.setup_sequence_search import plan_setup_stages_by_mode
        due = dt(day=23, hour=17)
        s = base([op('a', due=due), op('b', due=due), op('c', due=due, mode='backward')])
        c = three()
        seq = ('job:a', 'job:b', 'job:c')
        found = plan_setup_stages_by_mode(s, c, seq)
        self.assertEqual(dict(found.modes), {'job:a': 'forward', 'job:b': 'forward', 'job:c': 'backward'})
        forward = {(b.phase_id, b.stage): b for b in schedule_setup_stages(
            base([op('a', due=due), op('b', due=due), op('c', due=due)]), c, seq).bookings}
        got = {(b.phase_id, b.stage): b for b in found.result.solution.bookings}
        for key in (('job:a', 'run'), ('job:a', 'setup'), ('job:b', 'run'), ('job:b', 'setup')):
            self.assertEqual(got[key], forward[key])      # forward tasks untouched
        self.assertGreater(got[('job:c', 'run')].segments[0].start,
                           forward[('job:c', 'run')].segments[0].start)
        self.assertEqual(found.result.selected.late_orders, 0)
        self.assertEqual(audit_stage_capacity(c, found.result.scenario, seq,
                                              found.result.solution.bookings), ())
        self.assertEqual(s.operations[2].mode, 'backward')   # input untouched
        self.assertEqual(s.settings.mode, 'forward')

    def test_auto_follows_the_due_distance_rule(self):
        from planning_factory.engine.setup_sequence_search import plan_setup_stages_by_mode
        near = dt(day=21, hour=17)       # 2 days from start: below the 10-day threshold
        far = dt(day=31, hour=17)        # 12 days: backward
        s = base([op('a', due=near, mode='auto'), op('b', due=far, mode='auto'), op('c')],
                 end=dt(day=31, hour=23))
        found = plan_setup_stages_by_mode(s, three(), ('job:a', 'job:b', 'job:c'))
        self.assertEqual(dict(found.modes)['job:a'], 'forward')
        self.assertEqual(dict(found.modes)['job:b'], 'backward')

    def test_fail_closed_cases(self):
        from planning_factory.domain.models import Settings
        from planning_factory.engine.setup_sequence_search import plan_setup_stages_by_mode
        seq = ('job:a', 'job:b', 'job:c')
        with self.assertRaisesRegex(ValidationError, 'needs a due date'):
            plan_setup_stages_by_mode(base([op('a'), op('b', mode='backward'), op('c')]), three(), seq)
        slack = base([op('a', mode='auto', due=dt(day=23)), op('b'), op('c')],
                     settings=Settings(auto_rule='slack'))
        with self.assertRaisesRegex(ValidationError, "'slack' is not supported"):
            plan_setup_stages_by_mode(slack, three(), seq)


class MultiStartTests(unittest.TestCase):
    def test_multistart_is_deterministic_and_never_worse_than_single_start(self):
        from planning_factory.engine.setup_sequence_search import optimize_setup_sequence_multistart
        s, c = base([op('a'), op('b'), op('c')]), three()
        single = optimize_setup_sequence(s, c, default_machine_sequence(s), objective='setup',
                                         max_evaluations=6)
        multi = optimize_setup_sequence_multistart(s, c, objective='setup', restarts=3, seed=7,
                                                   max_evaluations=6)
        self.assertLessEqual(multi.best.selected.setup_seconds, single.selected.setup_seconds)
        self.assertGreaterEqual(len(multi.starts), 2)
        self.assertEqual(multi.start_scores[multi.best_start_index], min(multi.start_scores))
        self.assertEqual(multi, optimize_setup_sequence_multistart(
            s, c, objective='setup', restarts=3, seed=7, max_evaluations=6))
        self.assertEqual(audit_stage_capacity(c, s, multi.best.sequence, multi.best.winner.bookings), ())

    def test_multistart_validates_arguments(self):
        from planning_factory.engine.setup_sequence_search import optimize_setup_sequence_multistart
        s, c = base([op('a'), op('b'), op('c')]), three()
        for bad in ({'restarts': 9}, {'restarts': -1}, {'restarts': 1.5}, {'seed': 'x'}, {'objective': 'gap'}):
            kwargs = {'objective': 'setup', 'restarts': 1, **bad}
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                optimize_setup_sequence_multistart(s, c, **kwargs)
