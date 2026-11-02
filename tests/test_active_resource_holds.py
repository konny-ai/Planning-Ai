"""Full resource-hold planning scenarios, authored but not executed."""
import json
from dataclasses import replace
from decimal import Decimal
import unittest

from planning_factory.domain.active_resource_holds import (
    ActiveResourceHold, apply_full_capacity_holds,
)
from planning_factory.domain.production_flow import MaterialNeed, MaterialSupply
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.material_stage_pipeline import plan_materials_with_active_holds
from .helpers import dt
from .test_setup_stage_scheduler import base, spec


class ActiveResourceHoldsTests(unittest.TestCase):
    def setUp(self):
        self.scenario = base()
        self.contract = load_setup_contract(json.dumps(spec()))
        self.hold = ActiveResourceHold(
            'm', self.scenario.start, dt(19, 10), 'MES:running:m:rev3',
            'job:in-progress-outside-scenario')

    def test_hold_creates_isolated_closure_and_unknown_state(self):
        result = apply_full_capacity_holds(
            self.scenario, self.contract, (self.hold,))
        original = {r.id: r for r in self.scenario.resources}
        adapted = {r.id: r for r in result.scenario.resources}
        self.assertNotEqual(adapted['m'].calendar_id, original['m'].calendar_id)
        self.assertEqual(adapted['op'].calendar_id, original['op'].calendar_id)
        self.assertIsNone(dict(result.setup_contract.initial_machine_states)['m'])
        self.assertEqual(dict(self.contract.initial_machine_states)['m'], 'RED')

    def test_stage_run_cannot_overlap_explicit_machine_hold(self):
        result = plan_materials_with_active_holds(
            self.scenario, self.contract, ('job:a', 'job:b'),
            (self.hold,), (), (), ('job:a', 'job:b'),
            supply_quantities_net_of_external_commitments=True)
        for booking in result.staged.bookings:
            if any(a.resource_id == 'm' for a in booking.allocations):
                self.assertGreaterEqual(booking.segments[0].start, dt(19, 10))

    def test_rejects_double_scheduling_of_task_occupying_machine(self):
        with self.assertRaisesRegex(ValidationError, 'must be excluded'):
            apply_full_capacity_holds(
                self.scenario, self.contract,
                (replace(self.hold, occupying_task_id='job:a'),))

    def test_rejects_stale_hold_and_missing_end(self):
        for bad in (
            replace(self.hold, observed_at=dt(19, 8)),
            replace(self.hold, reserved_until=self.scenario.start),
            replace(self.hold, source_ref=''),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                apply_full_capacity_holds(self.scenario, self.contract, (bad,))


if __name__ == '__main__':
    unittest.main()
