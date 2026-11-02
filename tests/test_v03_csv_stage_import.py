"""Regression contracts for strict CSV -> v0.3 source specs (NOT executed)."""
from pathlib import Path
import tempfile
import unittest

from planning_factory.adapters.json_io import scenario_digest
from planning_factory.adapters.staged_file import load_staged_file
from planning_factory.domain.validation import ValidationError
from tools.build_v03_spec_from_csv import transform
from tools.generate_v03_stage_demo import make_example, write_example_csv


class CsvV03ImporterTests(unittest.TestCase):
    def setUp(self):
        self.scenario,self.spec=make_example()

    def synthetic_tables(self,folder):
        destination=Path(folder)/'csv'
        write_example_csv(destination,self.scenario,self.spec)
        return destination

    def test_synthetic_csv_roundtrip_contract(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.synthetic_tables(folder)
            payload,manifest=transform(
                self.scenario,root,mode='normal',
                stock_accounting='net',
                residual_materials_confirmed=False)
            self.assertEqual(payload['source_scenario_digest'],
                             scenario_digest(self.scenario))
            self.assertEqual(payload['setup_contract']['initial_machine_states'],
                             {'WORKCELL_1':'PROGRAM_RED'})
            self.assertEqual(payload['machine_sequence'],['job:CUT-A','job:CUT-B'])
            self.assertEqual(len(payload['needs']),2)
            self.assertEqual(len(payload['setup_contract']['phases']),2)
            self.assertEqual(len(manifest['input_csv_sha256']),8)
            self.assertIn('WORKCELL_1',manifest['machine_state_sources'])
            self.assertTrue(manifest['read_only'])

    def test_gross_accounting_requires_commitments_file(self):
        with tempfile.TemporaryDirectory() as folder:
            source=self.synthetic_tables(folder)
            with self.assertRaisesRegex(ValidationError,'requires commitments.csv'):
                transform(self.scenario,source,mode='normal',
                          stock_accounting='gross',
                          residual_materials_confirmed=False)
            (source/'commitments.csv').write_text('supply_id,quantity\n',encoding='utf-8')
            payload,_=transform(self.scenario,source,mode='normal',
                                stock_accounting='gross',
                                residual_materials_confirmed=False)
            self.assertEqual(payload['stock']['external_commitments'],[])

    def test_rejects_unobserved_machine_state_and_unknown_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.synthetic_tables(folder)
            state=root/'machine_states.csv'
            state.write_text(state.read_text(encoding='utf-8').replace(
                '2026-10-19T00:00:00+00:00','2026-10-18T00:00:00+00:00'),
                encoding='utf-8')
            with self.assertRaisesRegex(ValidationError,'must equal Scenario.start'):
                transform(self.scenario,root,mode='normal',
                          stock_accounting='net',
                          residual_materials_confirmed=False)
            (root/'extra-unknown.csv').write_text('id\nX\n',encoding='utf-8')
            with self.assertRaisesRegex(ValidationError,'unrecognized CSV'):
                transform(self.scenario,root,mode='normal',
                          stock_accounting='net',
                          residual_materials_confirmed=False)

    def test_refuses_silent_paused_wip_without_certified_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=self.synthetic_tables(folder)
            with self.assertRaisesRegex(ValidationError,'paused WIP requires'):
                transform(self.scenario,root,mode='paused',
                          stock_accounting='net',
                          residual_materials_confirmed=False)


if __name__=='__main__':
    unittest.main()
