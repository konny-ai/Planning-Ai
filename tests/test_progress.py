"""#86: progress from any source -> remaining work with the explicit residual policies."""
import unittest
from datetime import UTC, datetime

from planning_factory.canonical.builder import build_scenario
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tests.test_canonical import ALL, NOW, WORKSHOP, batches_from, binding, codes


class ProgressTests(unittest.TestCase):
    def setUp(self):
        self.bundle = scenario_to_bundle(WORKSHOP)
        records = self.bundle['records']
        # ord-c/urgente: replace the pinned start of the example with recorded progress.
        records['operation_state'] = [s for s in records['operation_state'] if s['operation_id'] != 'ord-c/urgente']
        self.ops = {o['id']: o for o in records['operation']}
        self.ops['ord-a/piega']['quantity'] = '4'

    def build(self, policy, progress):
        self.bundle['records']['operation_progress'] = progress
        domains = {d: {'sources': ['erp']} for d in ALL}
        if policy:
            domains['progress']['residual_policy'] = policy
        b = binding(['erp'], domains)
        return build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)

    def ops_of(self, result):
        return {o.id: o for o in result.scenario.operations}

    def test_started_half_done_and_closed_operations_by_quantity(self):
        result = self.build('quantity', [
            {'operation_id': 'ord-a/piega', 'status': 'started', 'produced_quantity': '1', 'resource_id': 'PIEGA-1'},
            {'operation_id': 'ord-c/urgente', 'status': 'completed', 'completed_at': '2026-10-18T16:00:00+02:00'}])
        self.assertEqual([], codes(result))
        ops = self.ops_of(result)
        self.assertEqual((5400, 0), (ops['ord-a/piega'].duration_seconds, ops['ord-a/piega'].setup_seconds))  # 3 of 4 left
        urgent = ops['ord-c/urgente']
        self.assertEqual((0, 0, datetime(2026, 10, 18, 14, tzinfo=UTC)),
                         (urgent.duration_seconds, urgent.setup_seconds, urgent.completed_at))
        p = plan(result.scenario)
        self.assertFalse(verify_plan(result.scenario, p))
        self.assertEqual(5400, sum(s.seconds for a in p.assignments if a.task_id == 'job:ord-a/piega' for s in a.segments))

    def test_time_policy_deducts_recorded_work_and_setup(self):
        result = self.build('time', [{'operation_id': 'ord-a/saldatura', 'status': 'started',
                                      'recorded_work_seconds': 3600, 'recorded_setup_seconds': 1800},
                                     {'operation_id': 'ord-b/piega', 'status': 'started'}])
        self.assertEqual([], codes(result))
        ops = self.ops_of(result)
        self.assertEqual((14400, 0), (ops['ord-a/saldatura'].duration_seconds, ops['ord-a/saldatura'].setup_seconds))
        self.assertEqual(3600, ops['ord-b/piega'].duration_seconds)  # nothing recorded: full remaining time
        self.assertFalse(verify_plan(result.scenario, plan(result.scenario)))

    def test_none_policy_keeps_theoretical_time(self):
        result = self.build('none', [{'operation_id': 'ord-a/saldatura', 'status': 'started', 'recorded_work_seconds': 3600}])
        self.assertEqual(18000, self.ops_of(result)['ord-a/saldatura'].duration_seconds)

    def test_contradictions_are_errors_never_guesses(self):
        cases = [
            (None, [{'operation_id': 'ord-a/piega', 'status': 'started'}], 'missing_residual_policy'),
            ('quantity', [{'operation_id': 'ord-b/piega', 'status': 'started', 'produced_quantity': '1'}], 'missing_quantity'),
            ('time', [{'operation_id': 'ord-c/urgente', 'status': 'completed'}], 'completed_without_date'),
            ('time', [{'operation_id': 'ord-c/urgente', 'status': 'completed', 'completed_at': '2026-10-19T10:00:00Z'}],
             'completed_after_horizon_start'),
            ('time', [{'operation_id': 'ord-b/piega', 'status': 'started', 'recorded_work_seconds': 4000}], 'no_residual'),
            ('time', [{'operation_id': 'ord-b/piega', 'status': 'started', 'resource_id': 'SALDATURA-1'}],
             'progress_resource_conflict'),
            ('time', [{'operation_id': 'ord-a/materiale', 'status': 'started'}], 'progress_conflict'),
            ('time', [{'operation_id': 'ghost', 'status': 'started'}], 'unknown_reference'),
        ]
        for policy, progress, code in cases:
            with self.subTest(code=code):
                self.setUp()
                result = self.build(policy, progress)
                self.assertIsNone(result.scenario)
                self.assertIn(('error', code), codes(result))
        self.setUp()
        result = self.build('time', [{'operation_id': 'ord-b/piega', 'status': 'paused'}])
        self.assertIn(('error', 'invalid_record'), codes(result))

    def test_policy_value_is_checked_in_the_binding(self):
        domains = {d: {'sources': ['erp']} for d in ALL}
        domains['progress']['residual_policy'] = 'pieces'
        with self.assertRaises(ValidationError):
            binding(['erp'], domains)

    def test_units_point_to_the_operation(self):
        result = self.build('time', [{'operation_id': 'ord-b/piega', 'status': 'started', 'recorded_work_seconds': 3600}])
        issue = next(i for i in result.issues if i.code == 'no_residual')
        self.assertEqual(('job:ord-b/piega',), issue.units)


if __name__ == '__main__':
    unittest.main()
