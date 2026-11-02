"""Factory setup source fields: static/dynamic distinctions and resource-demand gaps."""
import unittest

from .test_factory_export_safety import load_tool, scenario


class FactorySetupCoverageTests(unittest.TestCase):
    def setUp(self):
        self.diagnostic = load_tool('analyze_setup_coverage')

    def test_static_duration_and_declared_resources_are_separate(self):
        raw = scenario()
        op = raw['operations'][0]
        op.update(setupSec=3600, setupMachines=1, setupOperators=2,
                  runMachines=1, runOperators=1)
        result = self.diagnostic.analyze(raw, {'operations': [{'id': '1/1/11'}]})
        self.assertEqual(result['matched_schedulable_operations'], 1)
        self.assertEqual(result['positive_static_setup_operations'], 1)
        self.assertEqual(result['declared_setup_machine_operations'], 1)
        self.assertEqual(result['declared_setup_operator_operations'], 1)
        self.assertEqual(result['positive_static_setup_with_no_declared_setup_resources'], 0)
        self.assertEqual(result['positive_static_setup_with_different_setup_vs_run_resource_counts'], 1)
        self.assertEqual(result['raw_nominal_static_setup_seconds'], 3600)
        self.assertFalse(result['directional_changeover_matrix_present'])

    def test_positive_static_setup_without_demands_is_diagnostic_not_invention(self):
        raw = scenario()
        raw['operations'][0]['setupSec'] = 5400
        result = self.diagnostic.analyze(raw, {'operations': [{'id': '1/1/11'}]})
        self.assertEqual(result['positive_static_setup_with_no_declared_setup_resources'], 1)
        self.assertEqual(result['positive_setup_seconds_p90'], 5400)
        self.assertEqual(result['declared_setup_operator_operations'], 0)

    def test_internal_external_setup_are_separate_groups(self):
        raw = scenario()
        raw['operations'][0].update(setupSec=3600, setupOperators=1)
        external = dict(raw['operations'][0], operationId=102, phaseId=12,
                        executionCode='E', setupSec=230400,
                        setupOperators=0, setupMachines=0)
        raw['operations'].append(external)
        result = self.diagnostic.analyze(raw, {
            'operations': [{'id': '1/1/11'}, {'id': '1/1/12'}]})
        self.assertEqual(result['positive_static_setup_operations'], 2)
        self.assertEqual(result['by_execution_code']['I']['positive_static_setup_operations'], 1)
        self.assertEqual(result['by_execution_code']['E']['positive_static_setup_operations'], 1)
        self.assertEqual(result['by_execution_code']['I']['raw_static_setup_seconds'], 3600)
        self.assertEqual(result['by_execution_code']['E']['raw_static_setup_seconds'], 230400)
        self.assertEqual(
            result['by_execution_code']['E']['positive_without_declared_setup_resources'], 1)
        self.assertEqual(result['by_execution_code']['I']['declared_setup_operator_operations'], 1)

    def test_incomplete_mapping_rejected(self):
        raw = scenario()
        with self.assertRaisesRegex(ValueError, 'cannot match'):
            self.diagnostic.analyze(raw, {'operations': [{'id': 'ORD1/1/11'}]})

    def test_zero_setup_without_matrix_is_not_claimed_as_no_changeovers(self):
        raw = scenario()
        report = self.diagnostic.analyze(raw, {'operations': [{'id': '1/1/11'}]})
        self.assertEqual(report['positive_static_setup_operations'], 0)
        self.assertFalse(report['directional_changeover_matrix_present'])


if __name__ == '__main__':
    unittest.main()
