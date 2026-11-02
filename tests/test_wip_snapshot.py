"""Synthetic paused WIP and machine observation cases; NOT executed."""
from dataclasses import replace
import json
import unittest

from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.wip_snapshot import (
    MachineObservation, PausedRunProgress, prepare_paused_wip,
)
from planning_factory.domain.validation import ValidationError, compile_tasks
from .helpers import dt, op
from .test_setup_stage_scheduler import base, spec


class PausedWipSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.scenario = base([op('a'), op('b')])
        self.contract = load_setup_contract(json.dumps(spec()))
        self.obs = (MachineObservation('m', self.scenario.start, 'BLUE',
                                       'MES:machine:rev1', True),)
        self.partial = (PausedRunProgress(
            'job:a', 'm', 3600, 1200, 2400, self.scenario.start,
            'MES:operation:a:rev1'),)
        self.seq = ('job:a', 'job:b')

    def test_paused_run_is_residual_and_state_is_explicit(self):
        result = prepare_paused_wip(self.scenario, self.contract,
                                    self.seq, self.obs, self.partial)
        tasks, _, _ = compile_tasks(result.scenario)
        self.assertEqual(tasks['job:a'].duration_seconds, 2400)
        self.assertEqual(dict(result.setup_contract.initial_machine_states),
                         {'m': 'BLUE'})
        self.assertEqual(self.scenario.operations[0].duration_seconds, 3600)

    def test_active_machine_cannot_be_silently_released(self):
        active = (replace(self.obs[0], idle_confirmed=False),)
        with self.assertRaisesRegex(ValidationError, 'active machine occupancy'):
            prepare_paused_wip(self.scenario, self.contract, self.seq,
                               active, self.partial)

    def test_unknown_or_stale_state_does_not_skip_setup(self):
        unknown = (replace(self.obs[0], state_id=None),)
        with self.assertRaisesRegex(ValidationError, 'state not confirmed'):
            prepare_paused_wip(self.scenario, self.contract, self.seq,
                               unknown, self.partial)
        stale = (replace(self.obs[0], observed_at=dt(19, 8)),)
        with self.assertRaisesRegex(ValidationError, 'must match horizon start'):
            prepare_paused_wip(self.scenario, self.contract, self.seq,
                               stale, self.partial)

    def test_remaining_duration_must_be_reconciled(self):
        broken = (replace(self.partial[0], remaining_seconds=2500),)
        with self.assertRaisesRegex(ValidationError, 'inconsistent'):
            prepare_paused_wip(self.scenario, self.contract, self.seq,
                               self.obs, broken)

    def test_resuming_after_another_machine_job_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, 'first on its machine'):
            prepare_paused_wip(self.scenario, self.contract,
                               ('job:b', 'job:a'), self.obs, self.partial)


if __name__ == '__main__':
    unittest.main()
