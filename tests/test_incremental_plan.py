"""Exact incremental replanning: plan(resume=...) must equal the full plan, always."""
import random
import unittest
from dataclasses import replace
from datetime import timedelta

from planning_factory.domain.models import Dependency, Nesting, Operation, Resource
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
