"""Factory ADR003 mapping evidence, never unapproved setup assignment."""
import unittest
from .test_factory_export_safety import load_tool, scenario


def converted():
    return {
        'resources': [
            {'id': 'MAC:MAC-A', 'kind': 'machine'},
            {'id': 'OP:OP-A', 'kind': 'operator'},
        ],
        'operations': [
            {'id': '1/1/11', 'alternatives': [{'demands': [
                {'resource_id': 'MAC:MAC-A', 'units': 1},
                {'resource_id': 'OP:OP-A', 'units': 1}]}]},
        ],
    }


class FactorySetupAdapterReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tool = load_tool('setup_adapter_readiness')

    def test_positive_setup_requires_explicit_operator_percentage_and_state(self):
        source = scenario()
        source['operations'][0].update(setupSec=1800, setupMachines=0,
                                       setupOperators=1, setupOperatorPct=0,
                                       setupClassId=12, setupClassGroup='G', setupClassValue='V')
        report = self.tool.diagnose(source, converted())
        self.assertEqual(report['counters']['internal_positive_setup'], 1)
        self.assertFalse(report['canonical_contract_generated'])
        blockers = report['candidate_details'][0]['reasons']
        self.assertIn('setupOperators_percentage_unknown', blockers)
        self.assertIn('machine_setup_state_unverified', blockers)
        self.assertEqual(report['candidate_details'][0]['run_machine_candidates'],
                         ['MAC:MAC-A'])

    def test_even_fully_populated_percentages_are_not_automatic_authorization(self):
        source = scenario()
        source['operations'][0].update(setupSec=3000, setupMachines=1,
                                       setupOperators=1, setupMachinePct=100,
                                       setupOperatorPct=50)
        report = self.tool.diagnose(source, converted())
        self.assertFalse(report['canonical_contract_generated'])
        self.assertNotIn('setupOperators_percentage_unknown',
                         report['candidate_details'][0]['reasons'])
        self.assertEqual(report['candidate_details'][0]['declared_setup_operators'], 1)
        self.assertIn('setup_resources_not_authoritative',
                      report['candidate_details'][0]['reasons'])

    def test_external_supplier_time_is_not_internal_machine_setup(self):
        source = scenario()
        source['operations'][0].update(executionCode='E', setupSec=230400)
        report = self.tool.diagnose(source, converted())
        self.assertEqual(report['counters']['phase_execution_E'], 1)
        self.assertNotIn('internal_positive_setup', report['counters'])
        self.assertEqual(report['candidate_details'], [])

    def test_anonymized_or_unmatched_conversion_is_rejected(self):
        source = scenario()
        p = converted()
        p['operations'][0]['id'] = 'HASHED-KEY'
        with self.assertRaisesRegex(ValueError, 'anonymized'):
            self.tool.diagnose(source, p)

    def test_frozen_work_requires_snapshot_and_no_invented_machine(self):
        source = scenario()
        source['operations'][0].update(setupSec=60, setupOperators=1,
                                       setupOperatorPct=100, fixedStartFlag=1)
        p = converted()
        p['operations'][0]['alternatives'] = []
        report = self.tool.diagnose(source, p)
        blockers = report['candidate_details'][0]['reasons']
        self.assertIn('frozen_or_active_stage_requires_snapshot', blockers)
        self.assertIn('no_unambiguous_run_machine_candidate', blockers)


if __name__ == '__main__':
    unittest.main()
