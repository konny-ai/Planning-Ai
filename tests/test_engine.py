from dataclasses import replace
from datetime import timedelta
import random
import unittest
from planning_factory.adapters.json_io import dumps, loads, plan_loads
from planning_factory.domain.models import Dependency, Demand, Interval, Nesting, Resource, Settings
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from planning_factory.engine.simulation import compare, freeze
from .helpers import alt, dt, op, scenario


class SchedulingTests(unittest.TestCase):
    def assertValid(self, s, p):
        self.assertEqual(verify_plan(s, p), ())

    def test_single(self):
        s = scenario([op()])
        p = plan(s)
        self.assertEqual((p.assignments[0].start, p.assignments[0].end), (dt(), dt(hour=9)))
        self.assertValid(s, p)

    def test_no_overlap_and_tie_breaker(self):
        p = plan(scenario([op('b'), op('a')]))
        self.assertEqual([a.start for a in p.assignments], [dt(), dt(hour=9)])

    def test_priority_smaller_first(self):
        p = plan(scenario([op('a', priority=5), op('b', priority=1)]))
        self.assertEqual(next(a for a in p.assignments if a.task_id == 'job:b').start, dt())

    def test_customer_priority(self):
        p = plan(scenario([op('a', customer_priority=10), op('b')]))
        self.assertEqual(next(a for a in p.assignments if a.task_id == 'job:b').start, dt())

    def test_earlier_due_first(self):
        p = plan(scenario([op('a', due=dt(22)), op('b', due=dt(20))]))
        self.assertEqual(next(a for a in p.assignments if a.task_id == 'job:b').start, dt())

    def test_fs_lag(self):
        s = scenario([op('a'), op('b', resource='n', predecessors=(Dependency('a', lag_seconds=1800),))],
                     resources=(Resource('m', 'work'), Resource('n', 'work')))
        p = plan(s)
        self.assertEqual(p.assignments[1].start, dt(hour=9, minute=30))

    def test_ss_lag_allows_overlap(self):
        p = plan(scenario([op('a', 3), op('b', resource='n', predecessors=(Dependency('a', 'SS', 1800),))],
                          resources=(Resource('m', 'work'), Resource('n', 'work'))))
        self.assertEqual(p.assignments[1].start, dt(minute=30))

    def test_release_and_material_ready(self):
        p = plan(scenario([op(release=dt(20, 10))]))
        self.assertEqual(p.assignments[0].start, dt(20, 10))

    def test_non_interruptible_skips_lunch(self):
        p = plan(scenario([op(hours=3, release=dt(hour=11))]))
        self.assertEqual((p.assignments[0].start, p.assignments[0].end), (dt(hour=13), dt(hour=16)))

    def test_split_across_lunch_and_next_day(self):
        p = plan(scenario([op(hours=7, release=dt(hour=11), splittable=True)]))
        self.assertEqual([(i.start, i.end) for i in p.assignments[0].segments],
                         [(dt(hour=11), dt(hour=12)), (dt(hour=13), dt(hour=17)), (dt(20), dt(20, 10))])

    def test_setup_once_when_split(self):
        p = plan(scenario([op(hours=4, release=dt(hour=11), setup_seconds=3600, splittable=True)]))
        self.assertEqual(sum(i.seconds for i in p.assignments[0].segments), 18000)

    def test_simultaneous_machine_and_operator(self):
        resources = (Resource('m', 'work'), Resource('person', 'work'), Resource('n', 'work'))
        s = scenario([op('a', 2, alternatives=(alt('m', 'person'),)),
                      op('b', alternatives=(alt('n', 'person'),))], resources=resources)
        p = plan(s)
        self.assertEqual(p.assignments[1].start, dt(hour=10))

    def test_alternative_avoids_busy_resource(self):
        s = scenario([op('a', 3, fixed_start=dt()), op('b', alternatives=(alt('m'), alt('n')))],
                     resources=(Resource('m', 'work'), Resource('n', 'work')))
        p = plan(s)
        self.assertEqual(p.assignments[1].demands, (Demand('n'),))
        self.assertEqual(p.assignments[1].start, dt())

    def test_capacity_two_and_demand_units(self):
        s = scenario([op('a'), op('b'), op('c')], resources=(Resource('m', 'work', 2),))
        p = plan(s)
        self.assertEqual([a.start for a in p.assignments], [dt(), dt(), dt(hour=9)])
        self.assertValid(s, p)

    def test_capacity_aggregate_demand(self):
        from planning_factory.domain.models import Alternative
        s = scenario([op('a', alternatives=(Alternative((Demand('m', 2),)),)), op('b')],
                     resources=(Resource('m', 'work', 2),))
        self.assertEqual(plan(s).assignments[1].start, dt(hour=9))

    def test_infinite_still_observes_calendar(self):
        p = plan(scenario([op('a'), op('b')], resources=(Resource('m', 'work', None),)))
        self.assertEqual([a.start for a in p.assignments], [dt(), dt()])
        self.assertIsNone(p.resources[0].utilization)

    def test_insufficient_capacity_is_explicit(self):
        from planning_factory.domain.models import Alternative
        p = plan(scenario([op(alternatives=(Alternative((Demand('m', 2),)),))]))
        self.assertEqual(p.unscheduled[0].code, 'NO_FEASIBLE_SLOT')

    def test_unscheduled_propagates(self):
        s = scenario([op('a', 20), op('b', predecessors=(Dependency('a'),))])
        p = plan(s)
        self.assertEqual([u.code for u in p.unscheduled], ['NO_FEASIBLE_SLOT', 'PREDECESSOR_UNSCHEDULED'])
        self.assertIsNone(p.orders[0].end)

    def test_nesting_shared_duration_and_downstream(self):
        s = scenario([op('cut1', 2, order_id='O1'), op('cut2', 3, order_id='O2'),
                      op('bend', resource='n', predecessors=(Dependency('cut2'),))],
                     resources=(Resource('m', 'work'), Resource('n', 'work')),
                     nestings=(Nesting('N1', ('cut1', 'cut2'), 7200),))
        p = plan(s)
        nest = next(a for a in p.assignments if a.task_id == 'nest:N1')
        self.assertEqual(nest.operation_ids, ('cut1', 'cut2'))
        self.assertEqual(nest.end, dt(hour=10))
        self.assertEqual(p.resources[0].booked_unit_seconds, 7200)
        self.assertEqual(next(a for a in p.assignments if a.task_id == 'job:bend').start, dt(hour=10))

    def test_nesting_intersects_alternatives(self):
        p = plan(scenario([op('a', alternatives=(alt('m'), alt('n'))), op('b')],
                          resources=(Resource('m', 'work'), Resource('n', 'work')),
                          nestings=(Nesting('N', ('a', 'b'), 3600),)))
        self.assertEqual(p.assignments[0].demands, (Demand('m'),))

    def test_nesting_respects_latest_member_release(self):
        p = plan(scenario([op('a'), op('b', release=dt(20))], nestings=(Nesting('N', ('a', 'b'), 3600),)))
        self.assertEqual(p.assignments[0].start, dt(20))

    def test_pinned_reserved_before_flexible(self):
        p = plan(scenario([op('a', 2), op('z', 2, fixed_start=dt(hour=9))]))
        self.assertEqual(p.assignments[0].start, dt(hour=13))
        self.assertEqual(p.assignments[1].start, dt(hour=9))

    def test_pinned_successor_limits_predecessor(self):
        p = plan(scenario([op('a'), op('b', fixed_start=dt(hour=9), predecessors=(Dependency('a'),))]))
        self.assertEqual(p.assignments[0].end, dt(hour=9))

    def test_pinned_conflicts_rejected(self):
        with self.assertRaises(ValidationError):
            plan(scenario([op('a', fixed_start=dt()), op('b', fixed_start=dt())]))

    def test_pinned_before_predecessor_rejected(self):
        with self.assertRaises(ValidationError):
            plan(scenario([op('a', release=dt(20)), op('b', fixed_start=dt(), predecessors=(Dependency('a'),))]))

    def test_backward_just_in_time(self):
        p = plan(scenario([op(due=dt(20, 17), mode='backward')]))
        self.assertEqual((p.assignments[0].start, p.assignments[0].end), (dt(20, 16), dt(20, 17)))

    def test_backward_propagates_successor_due(self):
        p = plan(scenario([op('a', mode='backward'), op('b', due=dt(20, 17), mode='backward', predecessors=(Dependency('a'),))]))
        self.assertEqual([a.start for a in p.assignments], [dt(20, 15), dt(20, 16)])

    def test_backward_ss_with_different_resources(self):
        s = scenario([op('a', 3, mode='backward'), op('b', due=dt(20, 17), mode='backward', resource='n', predecessors=(Dependency('a', 'SS', 3600),))],
                     resources=(Resource('m', 'work'), Resource('n', 'work')))
        p = plan(s)
        self.assertLessEqual(p.assignments[0].start + timedelta(hours=1), p.assignments[1].start)
        self.assertValid(s, p)

    def test_backward_split(self):
        p = plan(scenario([op(hours=6, due=dt(20, 17), mode='backward', splittable=True)]))
        self.assertEqual([i.seconds for i in p.assignments[0].segments], [7200, 14400])
        self.assertEqual(p.assignments[0].start, dt(20, 10))

    def test_backward_infeasible_falls_forward_and_reports_late(self):
        p = plan(scenario([op(due=dt(19, 7), mode='backward')]))
        a = p.assignments[0]
        self.assertEqual(a.effective_mode, 'forward')
        self.assertTrue(any('fell back' in x for x in a.explanation))
        self.assertEqual(p.orders[0].tardiness_seconds, 7200)

    def test_backward_fallback_pulls_shared_predecessor_earlier(self):
        # Two parallel lines share predecessor p and the machines m, k, with the same due date.
        # Capacity-free targets put p right before the day both lines want: the second line
        # does not fit, falls forward from p's end and misses the due date. The repair moves p
        # earlier so that both lines fit.
        due = dt(23, 17)
        ops = [op('p', resource='n', order_id='o', due=due, mode='backward')]
        for line in 'ab':
            ops.append(op(f'{line}1', hours=8, resource='m', order_id='o', due=due, mode='backward', splittable=True,
                          predecessors=(Dependency('p'),)))
            ops.append(op(f'{line}2', hours=8, resource='k', order_id='o', due=due, mode='backward', splittable=True,
                          predecessors=(Dependency(f'{line}1'),)))
        s = scenario(ops, end=dt(31, 0), resources=(Resource('m', 'work'), Resource('n', 'work'), Resource('k', 'work')))
        p = plan(s)
        self.assertValid(s, p)
        self.assertEqual(p.orders[0].tardiness_seconds, 0)

    def test_repair_keeps_auto_slack_classification(self):
        import planning_factory.engine.scheduler as scheduler
        due = dt(23, 17)
        ops = [op('p', resource='n', order_id='o', due=due, mode='auto')]
        for line in 'ab':
            ops.append(op(f'{line}1', hours=8, resource='m', order_id='o', due=due, mode='backward', splittable=True,
                          predecessors=(Dependency('p'),)))
            ops.append(op(f'{line}2', hours=8, resource='k', order_id='o', due=due, mode='backward', splittable=True,
                          predecessors=(Dependency(f'{line}1'),)))
        s = scenario(ops, end=dt(31, 0), settings=Settings(mode='auto', auto_rule='slack', auto_slack_seconds=0),
                     resources=(Resource('m', 'work'), Resource('n', 'work'), Resource('k', 'work')))
        original = scheduler.MAX_BACKWARD_REPAIRS
        scheduler.MAX_BACKWARD_REPAIRS = 0
        try:
            before = next(a for a in plan(s).assignments if a.task_id == 'job:p')
        finally:
            scheduler.MAX_BACKWARD_REPAIRS = original
        repaired = plan(s)
        after = next(a for a in repaired.assignments if a.task_id == 'job:p')
        picked = lambda a: next(x for x in a.explanation if x.startswith('auto by slack')).rsplit('->', 1)[1]
        self.assertEqual(picked(before), picked(after))
        self.assertEqual(0, repaired.orders[0].tardiness_seconds)

    def test_repair_never_accepts_more_unscheduled_work(self):
        from planning_factory.engine.scheduler import _rank
        s = scenario([op('a')])
        p = plan(s)
        worse = replace(p, unscheduled=p.unscheduled + (object(),))
        self.assertGreater(_rank(worse), _rank(p))

    def test_auto_selects_per_operation(self):
        p = plan(scenario([op('a', due=dt(19, 17)), op('b', due=dt(23, 17))], settings=Settings(mode='auto', auto_backward_days=2)))
        self.assertEqual([a.effective_mode for a in p.assignments], ['forward', 'backward'])

    def test_completed_history_releases_precedence(self):
        history = op('a', hours=0, alternatives=(), completed_at=dt(18))
        p = plan(scenario([history, op('b', predecessors=(Dependency('a'),))]))
        self.assertEqual(p.assignments[0].effective_mode, 'completed')
        self.assertEqual(p.resources[0].booked_unit_seconds, 3600)

    def test_input_not_mutated_repeatable(self):
        s = scenario([op('b'), op('a')])
        original = dumps(s)
        self.assertEqual(dumps(plan(s)), dumps(plan(s)))
        self.assertEqual(dumps(s), original)

    def test_json_round_trip(self):
        s = scenario([op()])
        self.assertEqual(loads(dumps(s)), s)
        p = plan(s)
        self.assertEqual(plan_loads(dumps(p)), p)

    def test_freeze_atomic_nesting(self):
        s = scenario([op('a'), op('b')], nestings=(Nesting('N', ('a', 'b'), 3600),))
        frozen = freeze(s, plan(s), ['a'])
        self.assertTrue(all(o.fixed_start == dt() and o.fixed_alternative == 0 for o in frozen.operations))
        self.assertEqual(plan(frozen).assignments[0].start, dt())

    def test_compare_change(self):
        a = plan(scenario([op()]))
        b = plan(scenario([op(release=dt(20))]))
        self.assertEqual(compare(a, b)['changed_operations'][0]['shift_seconds'], 86400)

    def test_compare_detects_changed_internal_fragments_without_start_shift(self):
        s = scenario([op('a', hours=7, splittable=True)])
        p = plan(s)
        assignment, = p.assignments
        self.assertEqual(len(assignment.segments), 2)
        alternate = replace(assignment, segments=(
            assignment.segments[0],
            Interval(dt(hour=13), dt(hour=14)),
            Interval(dt(hour=14), dt(hour=16)),
        ))
        modified = replace(p, assignments=(alternate,))
        self.assertEqual(verify_plan(s, modified), ())
        report = compare(p, modified)
        self.assertEqual(report['assignment_operations_compared'], 1)
        self.assertEqual(report['changed_assignment_operations'], 1)
        self.assertEqual(report['operations_with_fragment_change'], 1)
        self.assertEqual(report['operations_with_resource_change'], 0)
        self.assertEqual(report['total_absolute_start_shift_seconds'], 0)
        self.assertEqual(report['changed_operations'][0]['shift_seconds'], 0)
        self.assertTrue(report['changed_operations'][0]['segments_changed'])

    def test_compare_counts_resource_swap_with_identical_schedule_times(self):
        s = scenario([op('a', alternatives=(alt('m'), alt('n')))],
                     resources=(Resource('m', 'work'), Resource('n', 'work')))
        first = plan(s)
        alternative = replace(s, operations=(replace(s.operations[0], fixed_start=dt(), fixed_alternative=1),))
        second = plan(alternative)
        self.assertEqual(verify_plan(s, first), ())
        self.assertEqual(verify_plan(alternative, second), ())
        report = compare(first, second)
        self.assertEqual(report['changed_assignment_operations'], 1)
        self.assertEqual(report['operations_with_resource_change'], 1)
        self.assertEqual(report['operations_with_fragment_change'], 0)
        self.assertEqual(report['total_absolute_start_shift_seconds'], 0)

    def test_random_dags_are_valid_and_repeatable(self):
        for seed in range(30):
            rng = random.Random(seed)
            ops = []
            for i in range(16):
                deps = tuple(Dependency(str(j), rng.choice(['FS', 'SS']), rng.choice([0, 900]))
                             for j in range(i) if rng.random() < .09)
                ops.append(op(str(i), hours=rng.choice([.5, 1, 2, 5]),
                              alternatives=(alt('m'), alt('n')), predecessors=deps,
                              priority=rng.randrange(3), splittable=rng.choice([True, False]),
                              mode=rng.choice(['forward', 'backward', 'auto']), due=dt(rng.randrange(20, 24), 17)))
            s = scenario(ops, resources=(Resource('m', 'work', 2), Resource('n', 'work')))
            p = plan(s)
            self.assertValid(s, p)
            self.assertEqual(dumps(p), dumps(plan(s)))


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.s = scenario([op('a'), op('b')])
        self.p = plan(self.s)

    def test_reject_changed_digest(self):
        self.assertTrue(verify_plan(self.s, replace(self.p, scenario_digest='bad')))

    def test_reject_capacity_overlap(self):
        a, b = self.p.assignments
        forged = replace(b, segments=a.segments)
        errors = verify_plan(self.s, replace(self.p, assignments=(a, forged)))
        self.assertTrue(any('capacity exceeded' in x for x in errors))

    def test_reject_wrong_duration(self):
        a, b = self.p.assignments
        segment = replace(a.segments[0], end=dt(hour=10))
        self.assertTrue(any('duration' in x for x in verify_plan(self.s, replace(self.p, assignments=(replace(a, segments=(segment,)), b)))))

    def test_reject_dropped_unit(self):
        self.assertTrue(any('coverage' in x for x in verify_plan(self.s, replace(self.p, assignments=self.p.assignments[:1]))))

    def test_reject_forged_load(self):
        metric = replace(self.p.resources[0], booked_unit_seconds=0)
        self.assertTrue(any('metrics' in x for x in verify_plan(self.s, replace(self.p, resources=(metric,)))))

    def test_reject_forged_delivery(self):
        metric = replace(self.p.orders[0], complete=False)
        self.assertTrue(any('delivery' in x for x in verify_plan(self.s, replace(self.p, orders=(metric,)))))
