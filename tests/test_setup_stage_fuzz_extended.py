"""Seeded fuzz over the whole staged path: frozen bookings, unplannable phases, task modes.

Contract: every plan that is returned passes the independent audit and keeps its
promises; anything else must fail closed with a ValidationError (never another error).
"""
import json
import random
import unittest
from dataclasses import replace

from planning_factory.domain.models import Operation
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_capacity import audit_stage_capacity
from planning_factory.engine.setup_partial import (
    NO_CANDIDATE, PREDECESSOR_UNPLANNED, plan_setup_stages_partial,
)
from planning_factory.engine.setup_sequence_search import (
    default_machine_sequence, plan_setup_stages_by_mode,
)
from planning_factory.engine.setup_stage_scheduler import schedule_setup_stages
from planning_factory.engine.verification import verify_plan
from .test_setup_stage_fuzz import build


def usable(seed, offset):
    """A fuzz scenario with only plain active work (no history, supplier or pinned phases)."""
    rng = random.Random(offset + seed)
    s, data = build(rng)
    if not data['phases']:
        return None
    if any(o.completed_at is not None or o.external_end is not None or o.fixed_start is not None
           for o in s.operations):
        return None
    return rng, s, load_setup_contract(json.dumps(data))


class ExtendedStageFuzzTests(unittest.TestCase):
    def test_frozen_bookings_survive_exactly_or_fail_closed(self):
        kept = 0
        for seed in range(900):
            case = usable(seed, 7000)
            if case is None:
                continue
            rng, s, contract = case
            try:
                sequence = default_machine_sequence(s)
                first = schedule_setup_stages(s, contract, sequence)
            except ValidationError:
                continue
            singles = [b for b in first.bookings if len(b.segments) == 1]
            if not singles:
                continue
            frozen = tuple(rng.sample(singles, min(len(singles), rng.randint(1, 3))))
            try:
                again = schedule_setup_stages(s, contract, sequence, frozen_bookings=frozen)
            except ValidationError:
                continue          # fail-closed is allowed; any other exception fails the test
            got = {(b.phase_id, b.stage): b for b in again.bookings}
            with self.subTest(seed=seed):
                for record in frozen:
                    self.assertEqual(got[(record.phase_id, record.stage)], record)
                self.assertEqual(audit_stage_capacity(contract, s, sequence, again.bookings,
                                                      frozen_bookings=frozen), ())
                self.assertEqual(verify_plan(again.scenario, again.plan), ())
                kept += 1
        self.assertGreater(kept, 30)

    def test_partial_planning_keeps_its_closure_and_audit(self):
        checked = 0
        for seed in range(900):
            case = usable(seed, 9000)
            if case is None:
                continue
            rng, s, contract = case
            ids = [o.id for o in s.operations]
            missing = tuple(rng.sample(ids, rng.randint(0, min(2, len(ids)))))
            try:
                result = plan_setup_stages_partial(s, contract, extra_without_candidates=missing)
            except ValidationError:
                continue
            reasons = {u.operation_id: u.code for u in result.unplanned}
            with self.subTest(seed=seed):
                self.assertEqual({k for k, v in reasons.items() if v == NO_CANDIDATE}, set(missing))
                for o in s.operations:      # closure: a successor of an unplanned phase is unplanned
                    if any(d.predecessor_id in reasons for d in o.predecessors):
                        self.assertIn(o.id, reasons)
                if result.staged is not None:
                    planned = {b.phase_id for b in result.staged.bookings}
                    self.assertFalse(planned & {'job:' + x for x in reasons})
                    self.assertEqual(audit_stage_capacity(
                        result.contract, result.scenario, result.machine_sequence,
                        result.staged.bookings), ())
                else:
                    self.assertEqual(len(reasons), len(ids))
                checked += 1
        self.assertGreater(checked, 50)

    def test_task_modes_plans_are_audited(self):
        checked = backward_seen = 0
        for seed in range(900):
            case = usable(seed, 11000)
            if case is None:
                continue
            rng, s, contract = case
            modes = ('forward', 'backward', 'auto')
            s = replace(s, operations=tuple(
                replace(o, mode=rng.choice(modes)) for o in s.operations))
            try:
                sequence = default_machine_sequence(s)
                result = plan_setup_stages_by_mode(s, contract, sequence)
            except ValidationError:
                continue
            with self.subTest(seed=seed):
                self.assertEqual(audit_stage_capacity(contract, result.result.scenario, sequence,
                                                      result.result.solution.bookings), ())
                self.assertEqual(verify_plan(result.result.solution.scenario,
                                             result.result.solution.plan), ())
                backward_seen += any(m == 'backward' for _, m in result.modes)
                checked += 1
        self.assertGreater(checked, 30)
        self.assertGreater(backward_seen, 5)


if __name__ == '__main__':
    unittest.main()
