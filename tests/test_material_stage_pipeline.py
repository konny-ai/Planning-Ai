"""End-to-end synthetic contract cases (authored only, not executed)."""
import json
from decimal import Decimal
import unittest

from planning_factory.domain.production_flow import MaterialCommitment, MaterialNeed, MaterialSupply
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.material_stage_pipeline import (
    plan_materials_and_setup_stages, plan_residual_wip_materials_and_setup,
)
from planning_factory.engine.verification import verify_plan
from planning_factory.engine.setup_capacity import StageAllocation, StageBooking, audit_stage_capacity
from planning_factory.domain.models import Interval
from planning_factory.domain.wip_snapshot import MachineObservation, PausedRunProgress
from .helpers import dt, op
from .test_setup_stage_scheduler import base, spec


class MaterialStagedPipelineTests(unittest.TestCase):
    def setUp(self):
        self.source = base([op('a'), op('b')])
        self.contract = load_setup_contract(json.dumps(spec()))
        self.sequence = ('job:a', 'job:b')
        self.needs = (MaterialNeed('job:a', 'SHEET', Decimal('2')),)
        self.priority = self.sequence

    def run_pipeline(self, supplies, **kwargs):
        return plan_materials_and_setup_stages(
            self.source, self.contract, self.sequence, supplies,
            self.needs, self.priority, **kwargs)

    def test_rejects_unattested_inventory_snapshot(self):
        with self.assertRaisesRegex(ValidationError, 'exactly one'):
            self.run_pipeline((MaterialSupply(
                'sheet', 'SHEET', Decimal('2'), dt(20, 9)),))

    def test_material_ready_run_does_not_delay_setup(self):
        result = self.run_pipeline(
            (MaterialSupply('sheet', 'SHEET', Decimal('2'), dt(20, 9)),),
            supply_quantities_net_of_external_commitments=True)
        setup = next(b for b in result.staged.bookings
                     if b.phase_id == 'job:a' and b.stage == 'setup')
        run = next(b for b in result.staged.bookings
                   if b.phase_id == 'job:a' and b.stage == 'run')
        self.assertLess(setup.segments[0].start, dt(20, 9))
        self.assertGreaterEqual(run.segments[0].start, dt(20, 9))
        self.assertEqual(verify_plan(result.staged.scenario,
                                     result.staged.plan), ())
        self.assertIsNone(self.source.operations[0].release)

    def test_gross_stock_with_commitments_uses_remaining_free_quantity(self):
        result = self.run_pipeline(
            (MaterialSupply('sheet', 'SHEET', Decimal('5'), dt(20, 9)),),
            external_commitments=(MaterialCommitment('sheet', Decimal('3')),))
        self.assertEqual(sum(p.quantity for p in result.allocation.pegs),
                         Decimal('2'))
        self.assertEqual(result.allocation.shortages, ())

    def test_conflicting_or_absent_stock_conventions_fail_closed(self):
        supplies = (MaterialSupply('sheet', 'SHEET', Decimal('5'), dt(20, 9)),)
        with self.assertRaisesRegex(ValidationError, 'exactly one'):
            self.run_pipeline(
                supplies, supply_quantities_net_of_external_commitments=True,
                external_commitments=())
        with self.assertRaisesRegex(ValidationError, 'exactly one'):
            self.run_pipeline(supplies)
        with self.assertRaisesRegex(ValidationError, 'material shortages'):
            self.run_pipeline(
                supplies,
                external_commitments=(MaterialCommitment('sheet', Decimal('4')),))

    def test_frozen_stage_survives_complete_material_pipeline(self):
        # No materials are consumed here: the snapshot is explicitly net.
        frozen = (
            StageBooking('job:a', 'setup',
                         (Interval(dt(hour=8), dt(hour=8, minute=30)),),
                         (StageAllocation('m', 10000), StageAllocation('op', 5000),
                          StageAllocation('tool', 10000))),
            StageBooking('job:a', 'run',
                         (Interval(dt(hour=8, minute=30), dt(hour=9, minute=30)),),
                         (StageAllocation('m', 10000), StageAllocation('op', 2500))),
        )
        result = plan_materials_and_setup_stages(
            self.source, self.contract, self.sequence, (), (),
            self.priority,
            supply_quantities_net_of_external_commitments=True,
            frozen_bookings=frozen)
        bookings = {(b.phase_id, b.stage): b for b in result.staged.bookings}
        self.assertEqual(
            tuple(bookings[(b.phase_id, b.stage)] for b in frozen), frozen)

    def test_frozen_run_before_material_release_fails_closed(self):
        frozen = StageBooking(
            'job:a', 'run',
            (Interval(dt(hour=8, minute=30), dt(hour=9, minute=30)),),
            (StageAllocation('m', 10000), StageAllocation('op', 2500)))
        with self.assertRaises(ValidationError):
            self.run_pipeline(
                (MaterialSupply('sheet', 'SHEET', Decimal('2'), dt(20, 9)),),
                supply_quantities_net_of_external_commitments=True,
                frozen_bookings=(frozen,))

    def test_paused_wip_residual_run_uses_confirmed_machine_state(self):
        observation = (
            MachineObservation('m', self.source.start, 'BLUE',
                               'MES:machine:r2', True),)
        progress = (
            PausedRunProgress('job:a', 'm', 3600, 1200, 2400,
                              self.source.start, 'MES:run:r2'),)
        result = plan_residual_wip_materials_and_setup(
            self.source, self.contract, self.sequence, observation, progress,
            (MaterialSupply('sheet', 'SHEET', Decimal('2'), dt(20, 9)),),
            self.needs, self.priority,
            residual_material_requirements_confirmed=True,
            supply_quantities_net_of_external_commitments=True)
        runs = {b.phase_id: b for b in result.staged.bookings if b.stage == 'run'}
        self.assertEqual(sum(seg.seconds for seg in runs['job:a'].segments), 2400)
        self.assertFalse(any(b.phase_id == 'job:a' and b.stage == 'setup'
                             for b in result.staged.bookings))

    def test_wip_gross_materials_need_explicit_residual_confirmation(self):
        observation = (
            MachineObservation('m', self.source.start, 'BLUE',
                               'MES:machine:r2', True),)
        progress = (
            PausedRunProgress('job:a', 'm', 3600, 1200, 2400,
                              self.source.start, 'MES:run:r2'),)
        with self.assertRaisesRegex(ValidationError, 'explicitly confirmed as residual'):
            plan_residual_wip_materials_and_setup(
                self.source, self.contract, self.sequence, observation, progress,
                (), self.needs, self.priority,
                supply_quantities_net_of_external_commitments=True)

    def test_insufficient_inventory_blocks_all_stages(self):
        with self.assertRaisesRegex(ValidationError, 'material shortages'):
            self.run_pipeline(
                (MaterialSupply('sheet', 'SHEET', Decimal('1'), dt(20, 9)),),
                supply_quantities_net_of_external_commitments=True)


class MaterialBackwardPipelineTests(unittest.TestCase):
    def setUp(self):
        self.source = base([op('a', due=dt(23, 17)), op('b', due=dt(23, 17))])
        self.contract = load_setup_contract(json.dumps(spec()))
        self.sequence = ('job:a', 'job:b')
        self.needs = (MaterialNeed('job:a', 'SHEET', Decimal('2')),)
        self.supplies = (MaterialSupply('sheet', 'SHEET', Decimal('2'), dt(20, 9)),)

    def run_pipeline(self, **kwargs):
        return plan_materials_and_setup_stages(
            self.source, self.contract, self.sequence, self.supplies, self.needs,
            self.sequence, supply_quantities_net_of_external_commitments=True, **kwargs)

    def test_backward_keeps_material_gate_and_only_delays(self):
        forward = self.run_pipeline()
        result = self.run_pipeline(backward=True)
        self.assertIsNotNone(result.backward)
        self.assertEqual(result.machine_sequence, self.sequence)
        f = {(b.phase_id, b.stage): b for b in forward.staged.bookings}
        r = {(b.phase_id, b.stage): b for b in result.staged.bookings}
        self.assertGreaterEqual(r[('job:a', 'run')].segments[0].start, dt(20, 9))
        for key, booking in r.items():
            self.assertGreaterEqual(booking.segments[0].start, f[key].segments[0].start)
        self.assertGreater(r[('job:b', 'run')].segments[0].start,
                           f[('job:b', 'run')].segments[0].start)
        self.assertEqual(verify_plan(result.staged.scenario, result.staged.plan), ())
        self.assertEqual(audit_stage_capacity(
            self.contract, result.material_scenario, result.machine_sequence,
            result.staged.bookings), ())
        self.assertIsNone(self.source.operations[0].release)

    def test_backward_with_sequence_objective_returns_used_sequence(self):
        result = self.run_pipeline(backward=True, sequence_objective='setup')
        self.assertEqual(sorted(result.machine_sequence), ['job:a', 'job:b'])
        self.assertEqual(audit_stage_capacity(
            self.contract, result.material_scenario, result.machine_sequence,
            result.staged.bookings), ())

    def test_backward_rejects_frozen_bookings_and_lone_objective(self):
        frozen = StageBooking(
            'job:a', 'run', (Interval(dt(hour=8, minute=30), dt(hour=9, minute=30)),),
            (StageAllocation('m', 10000), StageAllocation('op', 2500)))
        with self.assertRaisesRegex(ValidationError, 'frozen'):
            self.run_pipeline(backward=True, frozen_bookings=(frozen,))
        with self.assertRaisesRegex(ValidationError, 'requires backward'):
            self.run_pipeline(sequence_objective='setup')

    def test_material_shortage_still_blocks_backward(self):
        self.supplies = (MaterialSupply('sheet', 'SHEET', Decimal('1'), dt(20, 9)),)
        with self.assertRaisesRegex(ValidationError, 'shortages'):
            self.run_pipeline(backward=True)


if __name__ == '__main__':
    unittest.main()
