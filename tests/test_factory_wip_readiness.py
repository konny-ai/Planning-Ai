"""Read-only WIP adapter evidence diagnostics. Not executed in this PR."""
import unittest

from .test_factory_export_safety import load_tool


def source():
    return {
        'source': {'database': 'CLONE'},
        'planning': {'start': '2026-10-20T00:00:00Z'},
        'operations': [
            {'orderId': 'O1', 'lotId': 'L1', 'phaseId': 'F1',
             'executionCode': 'I', 'actualWorkMachineSec': 1200,
             'actualWorkOperatorSec': 0, 'actualSetupMachineSec': 0,
             'actualSetupOperatorSec': 0,
             'fixedStartFlag': True, 'currentMachineCode': 'MAC1'},
            {'orderId': 'O1', 'lotId': 'L1', 'phaseId': 'F2',
             'executionCode': 'I', 'actualWorkMachineSec': 0,
             'fixedStartFlag': False},
        ],
    }


class FactoryWipReadinessTests(unittest.TestCase):
    def test_only_reports_hints_and_never_generates_wip(self):
        engine = {'operations': [{'id': 'O1/L1/F1'}, {'id': 'O1/L1/F2'}]}
        result = load_tool('wip_adapter_readiness').diagnose(source(), engine)
        self.assertEqual(result['counters']['operations_with_positive_actual_time'], 1)
        self.assertEqual(result['counters']['fixed_start_hints'], 1)
        self.assertFalse(result['wip_contract_generated'])
        self.assertFalse(result['machine_observations_attested'])
        self.assertFalse(result['active_holds_attested'])

    def test_rejects_unmatched_or_duplicated_identity(self):
        with self.assertRaisesRegex(ValueError, 'do not match'):
            load_tool('wip_adapter_readiness').diagnose(source(), {'operations': [{'id': 'not-a-factory-phase'}]})
        with self.assertRaisesRegex(ValueError, 'duplicate converted'):
            load_tool('wip_adapter_readiness').diagnose(source(), {'operations': [
                {'id': 'O1/L1/F1'}, {'id': 'O1/L1/F1'}]})


if __name__ == '__main__':
    unittest.main()
