"""Exact incremental replanning: plan(resume=...) must equal the full plan, always."""
import random
import unittest
from unittest import mock
from dataclasses import replace
from datetime import timedelta

from datetime import time
from planning_factory.domain.models import Calendar, Dependency, Nesting, Operation, Resource, Shift
from planning_factory.domain.validation import compile_tasks
from planning_factory.engine import scheduler
from planning_factory.engine.scheduler import _reusable, plan
from .helpers import alt, dt, scenario


def random_scenario(rng):
    machines = ['m1', 'm2', 'm3'][:rng.randint(1, 3)]
    n = rng.randint(3, 12)
    names = [f'p{i}' for i in range(n)]
    ops = []
    for i, name in enumerate(names):
        preds = tuple(Dependency(names[j], rng.choice(['FS', 'FS', 'SS']), rng.choice([0, 0, 1800]))
                      for j in range(i) if rng.random() < 0.2)
        resource = rng.choice(machines)
        shared = ('shared',) if rng.random() < 0.5 else ()
        ops.append(Operation(
            name, f'o{i % 4}', rng.choice([1800, 3600, 7200, 14400]), (alt(resource, *shared),),
            predecessors=preds, due=dt(day=rng.randint(20, 30), hour=17) if rng.random() < 0.6 else None,
            priority=rng.randint(0, 2), splittable=rng.random() < 0.5,
            release=dt(day=rng.randint(19, 21), hour=rng.choice([8, 9, 13])) if rng.random() < 0.4 else None))
    resources = tuple(Resource(m, 'work') for m in machines) + (Resource('shared', 'work'),)
    return scenario(ops, resources=resources, end=dt(month=12, day=30, hour=0))


def with_release(s, op_id, release):
    return replace(s, operations=tuple(replace(o, release=release) if o.id == op_id else o
                                       for o in s.operations))


class IncrementalPlanTests(unittest.TestCase):
    def test_incremental_plan_equals_full_plan_on_random_scenarios(self):
        reused_total = checked = 0
        for seed in range(250):
            rng = random.Random(seed)
            s = random_scenario(rng)
            trace = {}
            try:
                base = plan(s, trace=trace)
            except Exception:
                continue
            if base.unscheduled or 'order' not in trace:
                continue
            victim = rng.choice([o.id for o in s.operations])
            moved = with_release(s, victim, dt(day=rng.randint(19, 26), hour=rng.choice([8, 10, 14])))
            changed = frozenset({'job:' + victim})
            resume = dict(trace, changed=changed)
            with self.subTest(seed=seed):
                full = plan(moved)
                self.assertEqual(plan(moved, resume=resume), full)
                tasks, edges, _ = compile_tasks(moved)
                reused_total += len(_reusable(moved, tasks, edges, False, resume))
                checked += 1
        self.assertGreater(checked, 100)
        self.assertGreater(reused_total, 0, 'the incremental path must actually reuse work')

    def test_changed_task_late_in_the_order_reuses_the_prefix(self):
        s = scenario([Operation(f'p{i}', f'o{i}', 3600, (alt('m'),), due=dt(day=25 + i % 3, hour=17))
                      for i in range(8)])
        trace = {}
        plan(s, trace=trace)
        last = trace['order'][-1]
        moved = with_release(s, last[len('job:'):], dt(day=22))
        tasks, edges, _ = compile_tasks(moved)
        reuse = _reusable(moved, tasks, edges, False, dict(trace, changed=frozenset({last})))
        self.assertEqual(set(reuse), set(trace['order'][:-1]))

    def test_any_mismatch_falls_back_to_the_full_plan(self):
        s = scenario([Operation(f'p{i}', f'o{i}', 3600, (alt('m'),), due=dt(day=25, hour=17))
                      for i in range(5)])
        trace = {}
        plan(s, trace=trace)
        victim = trace['order'][2][len('job:'):]
        moved = with_release(s, victim, dt(day=22))
        good = dict(trace, changed=frozenset({'job:' + victim}))
        tasks, edges, _ = compile_tasks(moved)
        self.assertTrue(_reusable(moved, tasks, edges, False, good))
        # a different, UNDECLARED task also changed: the declaration is not trusted
        other = trace['order'][4][len('job:'):]
        both = with_release(moved, other, dt(day=23))
        tasks, edges, _ = compile_tasks(both)
        self.assertEqual(_reusable(both, tasks, edges, False, good), {})
        self.assertEqual(plan(both, resume=good), plan(both))
        # a changed field other than release is not accepted either
        due_changed = replace(s, operations=tuple(replace(o, due=dt(day=29)) if o.id == victim else o
                                                  for o in s.operations))
        tasks, edges, _ = compile_tasks(due_changed)
        self.assertEqual(_reusable(due_changed, tasks, edges, False, good), {})
        # different resources, settings or record_constraints: no reuse
        tasks, edges, _ = compile_tasks(moved)
        self.assertEqual(_reusable(moved, tasks, edges, True, good), {})
        self.assertEqual(_reusable(replace(moved, end=moved.end + timedelta(days=1)), tasks, edges,
                                   False, good), {})
        # unknown / undispatched changed task: replan in full
        self.assertEqual(_reusable(moved, tasks, edges, False,
                                   dict(trace, changed=frozenset({'job:nowhere'}))), {})

    def test_nesting_completed_and_fixed_tasks_stay_exact(self):
        ops = [Operation('n1', 'o1', 3600, (alt('m1'),), due=dt(day=26, hour=17)),
               Operation('n2', 'o2', 3600, (alt('m1'),), due=dt(day=27, hour=17)),
               Operation('done', 'o3', 0, (alt('m2'),), completed_at=dt(hour=0)),
               Operation('pinned', 'o4', 3600, (alt('m2'),), fixed_start=dt(day=21, hour=9)),
               Operation('a', 'o5', 3600, (alt('m1', 'shared'),), due=dt(day=24, hour=17)),
               Operation('b', 'o6', 7200, (alt('m2', 'shared'),), predecessors=(Dependency('a', 'FS'),)),
               Operation('c', 'o7', 3600, (alt('m1'),), predecessors=(Dependency('done', 'FS'),)),
               Operation('d', 'o8', 1800, (alt('m2'),), due=dt(day=25, hour=17))]
        s = scenario(ops, nestings=(Nesting('nest', ('n1', 'n2'), 5400),),
                     resources=(Resource('m1', 'work'), Resource('m2', 'work'), Resource('shared', 'work')),
                     end=dt(month=12, day=30, hour=0))
        trace = {}
        base = plan(s, trace=trace)
        self.assertFalse(base.unscheduled)
        for victim in ('a', 'b', 'c', 'd'):
            moved = with_release(s, victim, dt(day=23, hour=10))
            with self.subTest(victim=victim):
                self.assertEqual(plan(moved, resume=dict(trace, changed=frozenset({'job:' + victim}))),
                                 plan(moved))
        # a nesting member changes the collapsed task 'nest:nest'
        moved = with_release(s, 'n1', dt(day=22, hour=10))
        self.assertEqual(plan(moved, resume=dict(trace, changed=frozenset({'nest:nest'}))), plan(moved))

    def test_resume_is_refused_when_both_plans_have_backward_tasks(self):
        # Mutation testing showed no test covered this guard: with backward tasks in BOTH scenarios the
        # tasks are equal bar the release, so only the mode check stops a (wrong) reuse.
        ops = [Operation(f'p{i}', f'o{i}', 3600, (alt('m'),), due=dt(day=25 + i, hour=17), mode='backward')
               for i in range(4)]
        s = scenario(ops)
        trace = {}
        plan(s, trace=trace)
        self.assertIn('order', trace)
        moved = with_release(s, 'p3', dt(day=22))
        tasks, edges, _ = compile_tasks(moved)
        resume = dict(trace, changed=frozenset({'job:p3'}))
        self.assertEqual(_reusable(moved, tasks, edges, False, resume), {})
        self.assertEqual(plan(moved, resume=resume), plan(moved))

    def test_resume_is_refused_when_the_previous_plan_left_work_unscheduled(self):
        # An operation that cannot fit the horizon: the previous plan has unscheduled work, so the
        # (conservative) guard must refuse the reuse, and the plan must still equal the full one.
        ops = [Operation('big', 'o0', 400 * 3600, (alt('m'),), due=dt(day=21, hour=17)),
               Operation('a', 'o1', 3600, (alt('n'),), due=dt(day=22, hour=17)),
               Operation('b', 'o2', 3600, (alt('n'),), due=dt(day=23, hour=17))]
        s = scenario(ops, resources=(Resource('m', 'work'), Resource('n', 'work')), end=dt(day=24, hour=0))
        trace = {}
        base = plan(s, trace=trace)
        self.assertTrue(base.unscheduled)
        moved = with_release(s, 'b', dt(day=21))
        tasks, edges, _ = compile_tasks(moved)
        resume = dict(trace, changed=frozenset({'job:b'}))
        self.assertEqual(_reusable(moved, tasks, edges, False, resume), {})
        self.assertEqual(plan(moved, resume=resume), plan(moved))

    def test_other_policies_and_backward_tasks_never_resume(self):
        s = scenario([Operation(f'p{i}', f'o{i}', 3600, (alt('m'),), due=dt(day=25, hour=17))
                      for i in range(4)])
        trace = {}
        plan(s, trace=trace)
        moved = with_release(s, 'p3', dt(day=22))
        resume = dict(trace, changed=frozenset({'job:p3'}))
        self.assertEqual(plan(moved, dispatch_policy='earliest_due', resume=resume),
                         plan(moved, dispatch_policy='earliest_due'))
        backward = replace(moved, operations=tuple(replace(o, mode='backward') for o in moved.operations))
        self.assertEqual(plan(backward, resume=resume), plan(backward))
        tasks, edges, _ = compile_tasks(backward)
        self.assertEqual(_reusable(backward, tasks, edges, False, resume), {})   # modes differ


if __name__ == '__main__':
    unittest.main()


class SearchWindowTests(unittest.TestCase):
    """The windowed first pass must never change a plan."""

    def long_scenario(self, rng):
        machines = ['m1', 'm2', 'sp'][:rng.randint(1, 3)]     # 'sp' has a sparse calendar
        ops = []
        names = [f'p{i}' for i in range(rng.randint(4, 12))]
        for i, name in enumerate(names):
            preds = tuple(Dependency(names[j], rng.choice(['FS', 'SS']), rng.choice([0, 3600]))
                          for j in range(i) if rng.random() < 0.2)
            hours = rng.choice([1, 8, 40, 80, 160])      # long tasks push work past the window
            choices = rng.sample(machines, rng.randint(1, len(machines)))     # one or several alternatives
            ops.append(Operation(name, f'o{i % 3}', hours * 3600, tuple(alt(m) for m in choices),
                                 predecessors=preds, splittable=rng.random() < 0.6,
                                 due=dt(month=12, day=rng.randint(1, 20)) if rng.random() < 0.5 else None,
                                 release=dt(day=rng.randint(19, 28)) if rng.random() < 0.3 else None))
        sparse = Calendar('sparse', 'UTC', (Shift(0, time(8), time(9)),))    # one hour every Monday
        work = scenario([]).calendars[0]
        return scenario(ops, calendars=(work, sparse),
                        resources=tuple(Resource(m, 'sparse' if m == 'sp' else 'work') for m in machines),
                        end=dt(month=12, day=30, hour=0))

    def test_windowed_search_equals_full_horizon_search(self):
        beyond = multi = 0
        for seed in range(400):
            s = self.long_scenario(random.Random(900 + seed))
            with self.subTest(seed=seed):
                windowed = plan(s)
                with mock.patch.object(scheduler, 'SEARCH_WINDOW', None):
                    full = plan(s)
                self.assertEqual(windowed, full)
                limit = s.start + timedelta(days=14)
                beyond += any(a.segments and a.segments[0].start > limit for a in full.assignments)
                multi += sum(len(o.alternatives) > 1 for o in s.operations)
        self.assertGreater(beyond, 20, 'some tasks must land beyond the window (fallback path)')
        self.assertGreater(multi, 100, 'tasks with several alternatives must be exercised')

    def test_the_window_never_reaches_past_the_upper_bound_of_the_search(self):
        # Mutation testing: with the window guard broken the first pass looked past a fixed successor and
        # placed the predecessor after it. The upper bound (here B's fixed start) must always hold.
        from planning_factory.domain.validation import ValidationError
        ops = [Operation('block', 'o0', 40 * 3600, (alt('m'),), fixed_start=dt(day=19, hour=8), splittable=True),
               Operation('A', 'o1', 3600, (alt('m'),)),
               Operation('B', 'o2', 3600, (alt('n'),), predecessors=(Dependency('A', 'FS'),),
                         fixed_start=dt(day=20, hour=9))]
        s = scenario(ops, resources=(Resource('m', 'work'), Resource('n', 'work')),
                     end=dt(month=12, day=30, hour=0))
        with self.assertRaisesRegex(ValidationError, 'unscheduled predecessor'):
            plan(s)

    def test_the_window_is_used_and_the_horizon_decides_when_an_alternative_does_not_fit(self):
        # Mutation testing: breaking the window logic left every plan correct but slow, so nothing failed.
        # A spy on bundle_free records how far each search looked.
        horizon = dt(month=12, day=30, hour=0)
        def reach(s, **patches):
            calls = []
            real = scheduler.bundle_free
            def spy(alternative, resources, calendars, reservations, lower, upper, cache=None):
                if reservations:                       # searches against real bookings, not estimates
                    calls.append((upper - lower).days)
                return real(alternative, resources, calendars, reservations, lower, upper, cache)
            with mock.patch.object(scheduler, 'bundle_free', spy):
                plan(s)
            return calls
        simple = scenario([Operation(f'p{i}', f'o{i}', 3600, (alt('m'),)) for i in range(5)], end=horizon)
        windowed = reach(simple)
        self.assertTrue(windowed and min(windowed) > 0 and max(windowed) <= 14, windowed)   # every search stays inside
        with mock.patch.object(scheduler, 'SEARCH_WINDOW', None):
            self.assertTrue(min(reach(simple)) > 14)                          # without it they span the horizon
        # one alternative (B, sparse calendar) cannot fit inside the window: the horizon must decide
        work = scenario([]).calendars[0]
        sparse = Calendar('sparse', 'UTC', (Shift(0, time(8), time(9)),))
        undecided = scenario([Operation('block', 'o0', 72 * 3600, (alt('A'),), fixed_start=dt(day=19, hour=8),
                                        splittable=True),
                              Operation('T', 'o1', 4 * 3600, (alt('A'), alt('B')), splittable=True)],
                             calendars=(work, sparse), end=horizon,
                             resources=(Resource('A', 'work'), Resource('B', 'sparse')))
        self.assertGreater(max(reach(undecided)), 14)

    def test_task_with_an_alternative_that_only_fits_later_keeps_the_full_search_choice(self):
        # B fits only beyond the window but starts earlier than A (earliest start wins): the
        # window must not turn this into A. Found by code review; reproduced before the fix.
        work = scenario([]).calendars[0]
        sparse = Calendar('sparse', 'UTC', (Shift(0, time(8), time(9)),))
        ops = [Operation('block', 'o0', 72 * 3600, (alt('A'),), fixed_start=dt(day=19, hour=8), splittable=True),
               Operation('T', 'o1', 4 * 3600, (alt('A'), alt('B')), splittable=True)]
        s = scenario(ops, calendars=(work, sparse), resources=(Resource('A', 'work'), Resource('B', 'sparse')),
                     end=dt(month=12, day=30, hour=0))
        windowed = plan(s)
        with mock.patch.object(scheduler, 'SEARCH_WINDOW', None):
            full = plan(s)
        self.assertEqual(windowed, full)
        chosen = next(a for a in full.assignments if a.task_id == 'job:T')
        self.assertEqual([d.resource_id for d in chosen.demands], ['B'])
        self.assertEqual(chosen.segments[0].start, dt(day=19, hour=8))


class CompileMemoTests(unittest.TestCase):
    def test_memoized_per_object_never_stale_and_callers_cannot_alter_it(self):
        from planning_factory.domain import validation
        s = scenario([Operation('a', 'o', 3600, (alt('m'),)), Operation('b', 'o', 3600, (alt('m'),))])
        first = compile_tasks(s)
        first[0].clear()                                   # a caller mutating its own copy
        again = compile_tasks(s)
        self.assertEqual(set(again[0]), {'job:a', 'job:b'})
        self.assertEqual(again[1:], compile_tasks(s)[1:])
        changed = replace(s, operations=s.operations[:1])  # a different object: a different result
        self.assertEqual(set(compile_tasks(changed)[0]), {'job:a'})
        for i in range(40):                                # eviction keeps results exact
            other = replace(s, id=f'x{i}')
            self.assertEqual(set(compile_tasks(other)[0]), {'job:a', 'job:b'})
        self.assertEqual(set(compile_tasks(s)[0]), {'job:a', 'job:b'})
        invalid = replace(s, operations=(Operation('z', 'o', 3600, ()),))
        for _ in range(2):                                 # failures are not cached
            with self.assertRaises(validation.ValidationError):
                compile_tasks(invalid)


class TraceAndLockTests(unittest.TestCase):
    def test_trace_describes_the_returned_plan_only_when_it_is_the_first_one(self):
        s = scenario([Operation(f'p{i}', f'o{i}', 3600, (alt('m'),), due=dt(day=25, hour=17)) for i in range(4)])
        trace = {}
        returned = plan(s, trace=trace)
        self.assertIs(trace['plan'], returned)

    def test_caches_survive_concurrent_use(self):
        import threading
        from planning_factory.adapters.json_io import scenario_digest
        base = scenario([Operation('a', 'o', 3600, (alt('m'),))])
        errors = []

        def worker(n):
            try:
                for i in range(1500):
                    s = replace(base, id=f'x{n}-{i}')
                    compile_tasks(s)
                    scenario_digest(s)
            except Exception as exc:       # pragma: no cover - the point is that none occurs
                errors.append(repr(exc))

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
