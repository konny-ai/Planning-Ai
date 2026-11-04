"""Independent property: every precedence declared on the ORIGINAL operations holds in the staged plan.

It reads the scenario's own `predecessors` (with lags, FS and SS, completed and supplier predecessors) and the
bookings only: it does not use compile_tasks edges or the stage audit, so it can catch what both could drop.
"""
import json
import random
import unittest
from datetime import timedelta

from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.validation import ValidationError, compile_tasks
from planning_factory.engine.setup_partial import plan_setup_stages_partial
from planning_factory.engine.setup_sequence_search import (
    backward_setup_before_run, default_machine_sequence, plan_setup_stages_by_mode,
)
from planning_factory.engine.setup_stage_scheduler import schedule_setup_stages
from .test_setup_stage_fuzz import build


def precedence_violations(s, bookings):
    """Violations of the original operations' own predecessors, computed from scratch."""
    tasks, _, _ = compile_tasks(s)
    owner = {oid: tid for tid, t in tasks.items() for oid in t.operation_ids}
    runs = {}
    for b in bookings:
        if b.stage == 'run' and b.segments:
            runs[b.phase_id] = (b.segments[0].start, b.segments[-1].end)
    def window(op_id):
        task = tasks[owner[op_id]]
        if task.completed_at is not None:
            return task.completed_at, task.completed_at
        if task.external_end is not None:
            return None, task.external_end                # a supplier milestone has a stated end only
        return runs.get(owner[op_id], (None, None))
    found = []
    for o in s.operations:
        task = tasks[owner[o.id]]
        if task.completed_at is not None or task.external_end is not None:
            continue
        start = window(o.id)[0]
        if start is None:
            continue                                      # not planned (partial): nothing to check
        for dep in o.predecessors:
            if owner[dep.predecessor_id] == owner[o.id]:
                continue                                  # members of one nesting run together
            pred_start, pred_end = window(dep.predecessor_id)
            anchor = pred_end if dep.kind == 'FS' else pred_start
            if anchor is None:
                continue                                  # unplanned predecessor, or SS from a supplier
            needed = anchor + timedelta(seconds=dep.lag_seconds)
            if start < needed:
                found.append(f'{o.id}: starts {start} before {dep.kind} {dep.predecessor_id} + {dep.lag_seconds}s = {needed}')
    return found


def capacity_violations(s, bookings):
    """Where the booked share of a resource exceeds its capacity (in basis points), from the bookings alone."""
    capacity = {r.id: (r.capacity * 10000 if r.capacity is not None else None) for r in s.resources}
    events = {}
    for b in bookings:
        for seg in b.segments:
            for a in b.allocations:
                events.setdefault(a.resource_id, []).extend(((seg.start, a.occupancy_bps), (seg.end, -a.occupancy_bps)))
    found = []
    for rid, items in events.items():
        if capacity.get(rid) is None:
            continue
        load = 0
        for instant, delta in sorted(items, key=lambda x: (x[0], x[1])):      # releases before takes at one instant
            load += delta
            if load > capacity[rid]:
                found.append(f'{rid}: {load}/{capacity[rid]} bps at {instant}')
    return found


class StagedPrecedenceProperty(unittest.TestCase):
    def cases(self, count, offset):
        for seed in range(count):
            rng = random.Random(offset + seed)
            try:
                s, data = build(rng)
                if not data['phases']:
                    continue
                yield seed, s, load_setup_contract(json.dumps(data))
            except ValidationError:
                continue

    def test_forward_plans_honour_every_declared_precedence(self):
        checked = 0
        for seed, s, contract in self.cases(500, 31000):
            try:
                result = schedule_setup_stages(s, contract, default_machine_sequence(s))
            except ValidationError:
                continue
            with self.subTest(seed=seed):
                self.assertEqual(precedence_violations(s, result.bookings), [])
            checked += 1
        self.assertGreater(checked, 100)

    def test_backward_plans_honour_every_declared_precedence(self):
        checked = 0
        for seed, s, contract in self.cases(300, 32000):
            try:
                sequence = default_machine_sequence(s)
                result = backward_setup_before_run(s, contract, sequence)
            except ValidationError:
                continue
            with self.subTest(seed=seed):
                self.assertEqual(precedence_violations(result.scenario, result.solution.bookings), [])
            checked += 1
        self.assertGreater(checked, 60)

    def test_partial_plans_honour_every_declared_precedence(self):
        checked = 0
        for seed, s, contract in self.cases(300, 33000):
            ids = [o.id for o in s.operations if o.completed_at is None and o.external_end is None and o.alternatives]
            rng = random.Random(seed)
            missing = tuple(rng.sample(ids, min(len(ids), rng.randint(0, 2))))
            try:
                result = plan_setup_stages_partial(s, contract, extra_without_candidates=missing)
            except ValidationError:
                continue
            if result.staged is None:
                continue
            with self.subTest(seed=seed):
                self.assertEqual(precedence_violations(result.scenario, result.staged.bookings), [])
            checked += 1
        self.assertGreater(checked, 40)

    def test_per_task_mode_plans_honour_every_declared_precedence(self):
        from dataclasses import replace
        checked = 0
        for seed, s, contract in self.cases(300, 34000):
            rng = random.Random(seed)
            s = replace(s, operations=tuple(replace(o, mode=rng.choice(('forward', 'backward', 'auto')))
                                            if o.due is not None else o for o in s.operations))
            try:
                result = plan_setup_stages_by_mode(s, contract, default_machine_sequence(s))
            except ValidationError:
                continue
            with self.subTest(seed=seed):
                self.assertEqual(precedence_violations(result.result.scenario, result.result.solution.bookings), [])
            checked += 1
        self.assertGreater(checked, 40)

    def test_no_plan_ever_overbooks_a_resource(self):
        checked = 0
        for seed, s, contract in self.cases(400, 35000):
            sequence = default_machine_sequence(s)
            try:
                forward = schedule_setup_stages(s, contract, sequence)
                backward = backward_setup_before_run(s, contract, sequence)
                partial = plan_setup_stages_partial(s, contract)
            except ValidationError:
                continue
            with self.subTest(seed=seed):
                self.assertEqual(capacity_violations(s, forward.bookings), [])
                self.assertEqual(capacity_violations(backward.scenario, backward.solution.bookings), [])
                if partial.staged is not None:
                    self.assertEqual(capacity_violations(partial.scenario, partial.staged.bookings), [])
            checked += 1
        self.assertGreater(checked, 80)

    def test_the_capacity_checker_detects_overbooking(self):
        from dataclasses import replace
        for seed, s, contract in self.cases(300, 35000):
            try:
                result = schedule_setup_stages(s, contract, default_machine_sequence(s))
            except ValidationError:
                continue
            runs = [b for b in result.bookings if b.stage == 'run']
            if not runs:
                continue
            twin = replace(runs[0], phase_id='job:twin')                     # the same booking twice
            self.assertTrue(capacity_violations(s, result.bookings + (twin,)))
            return
        self.fail('no plan to overbook')

    def test_the_checker_itself_detects_a_violation(self):
        # A negative control: shift a successor's run before its predecessor's end and the checker must complain.
        from dataclasses import replace
        for seed, s, contract in self.cases(300, 31000):
            try:
                result = schedule_setup_stages(s, contract, default_machine_sequence(s))
            except ValidationError:
                continue
            tasks, _, _ = compile_tasks(s)
            owner = {oid: tid for tid, t in tasks.items() for oid in t.operation_ids}
            target = next((o for o in s.operations for d in o.predecessors
                           if d.kind == 'FS' and owner[d.predecessor_id] != owner[o.id]
                           and tasks[owner[o.id]].completed_at is None and tasks[owner[o.id]].external_end is None
                           and tasks[owner[d.predecessor_id]].completed_at is None
                           and tasks[owner[d.predecessor_id]].external_end is None), None)
            if target is None:
                continue
            tid = owner[target.id]
            early = tuple(replace(b, segments=tuple(type(sg)(sg.start - timedelta(days=30), sg.end - timedelta(days=30))
                                                    for sg in b.segments))
                          if (b.phase_id, b.stage) == (tid, 'run') else b for b in result.bookings)
            self.assertTrue(precedence_violations(s, early), seed)
            return
        self.fail('no scenario with a machine-to-machine precedence was generated')


if __name__ == '__main__':
    unittest.main()
