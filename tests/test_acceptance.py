"""End-to-end reference scenarios and adversarial result checks."""
from dataclasses import replace
from pathlib import Path
from threading import Barrier, Thread
from tempfile import TemporaryDirectory
import unittest
from planning_factory.adapters.json_io import dumps, loads, plan_loads
from planning_factory.adapters.store import SnapshotStore, ConflictError
from planning_factory.domain.models import Dependency, Interval
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op, scenario


class AcceptanceTests(unittest.TestCase):
    def test_workshop_reference_plan(self):
        root = Path(__file__).resolve().parents[1]
        s = loads((root / 'examples/workshop.json').read_text())
        result = plan(s)
        expected = plan_loads((root / 'examples/workshop-plan.json').read_text())
        self.assertEqual(result, expected)
        self.assertEqual(len(result.assignments), 7)
        self.assertEqual(len(result.unscheduled), 0)
        nesting = next(a for a in result.assignments if a.task_id == 'nest:NEST-001')
        self.assertEqual(sum(i.seconds for i in nesting.segments), 12600)
        self.assertEqual(len(nesting.operation_ids), 2)
        self.assertTrue(all(o.complete for o in result.orders))

    def test_audit_rejects_forged_precedence(self):
        s = scenario([op('a'), op('b', predecessors=(Dependency('a'),))])
        result = plan(s)
        a, b = result.assignments
        forged = replace(result, assignments=(a, replace(b, segments=a.segments)))
        self.assertTrue(any('precedence' in e for e in verify_plan(s, forged)))

    def test_audit_rejects_calendar_violation(self):
        s = scenario([op()])
        result = plan(s)
        a = replace(result.assignments[0], segments=(Interval(dt(hour=18), dt(hour=19)),))
        self.assertTrue(any('calendar' in e for e in verify_plan(s, replace(result, assignments=(a,)))))

    def test_audit_rejects_wrong_nesting_members(self):
        s = scenario([op()])
        result = plan(s)
        a = replace(result.assignments[0], operation_ids=('wrong',))
        self.assertTrue(any('members' in e for e in verify_plan(s, replace(result, assignments=(a,)))))

    def test_audit_rejects_dropped_fixed_start(self):
        s = scenario([op(fixed_start=dt())])
        result = plan(s)
        a = replace(result.assignments[0], fixed=False)
        self.assertTrue(any('fixed start' in e for e in verify_plan(s, replace(result, assignments=(a,)))))

    def test_concurrent_snapshot_updates_have_one_winner(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / 'planning.sqlite'
            store = SnapshotStore(path)
            s = scenario([op()])
            store.save(s)
            barrier = Barrier(2)
            outcomes = []
            def update():
                barrier.wait(timeout=2)
                try:
                    outcomes.append(store.save(s, expected_revision=1))
                except ConflictError:
                    outcomes.append('conflict')
            threads = [Thread(target=update) for _ in range(2)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=3)
            self.assertCountEqual(outcomes, [2, 'conflict'])

    def test_null_plan_interval_rejected(self):
        text = dumps(plan(scenario([op()]))).replace('"start": "2026-10-19T08:00:00Z"', '"start": null')
        with self.assertRaises(ValidationError):
            plan_loads(text)

    def test_phase_priority_is_additive(self):
        p = plan(scenario([op('a', phase_priority=1), op('b', phase_priority=-1)]))
        self.assertEqual(p.assignments[1].start, dt())
