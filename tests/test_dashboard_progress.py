"""Progress evidence is recorded source data, not an inferred MES state.

These are regression specifications only: NOT executed in this delivery.
"""
import unittest

from planning_factory.adapters.dashboard_api import progress_evidence
from planning_factory.canonical.model import Record, SourceBatch
from .helpers import dt, op, scenario


class DashboardProgressTests(unittest.TestCase):
    def setUp(self):
        self.source=scenario((op('a',hours=2),))
        self.batch=SourceBatch(
            'hf_csv','file','progress',dt(),
            records=(
                Record('operation_progress',{
                    'operation_id':'a','status':'started',
                    'recorded_work_seconds':1800,'recorded_setup_seconds':90,
                    'resource_id':'m'},'progress.csv:2'),
                Record('operation_progress',{
                    'operation_id':'unmapped','status':'started',
                    'recorded_work_seconds':180},'progress.csv:3'),
            ),
        )

    def test_evidence_keeps_raw_consumed_time_and_marks_unmatched(self):
        report=progress_evidence({'progress':[self.batch]},self.source)
        self.assertEqual(report['total_rows'],2)
        self.assertTrue(report['not_machine_occupancy_evidence'])
        matched,unknown=report['rows']
        self.assertEqual(matched['source'],'hf_csv')
        self.assertEqual(matched['recorded_work_seconds'],1800)
        self.assertEqual(matched['planned_remaining_run_seconds'],7200)
        self.assertEqual(matched['declared_resource_id'],'m')
        self.assertTrue(matched['canonical_id_matched'])
        self.assertFalse(unknown['canonical_id_matched'])
        self.assertIsNone(unknown['planned_remaining_run_seconds'])

    def test_produced_decimal_quantities_stay_json_serializable(self):
        import json
        from decimal import Decimal
        from planning_factory.canonical.model import Record, SourceBatch
        sample = SourceBatch(
            'csv', 'file', 'progress', dt(),
            records=(Record('operation_progress', {
                'operation_id': 'a', 'status': 'started',
                'produced_quantity': Decimal('1.250'),
            }, 'progress.csv:2'),),
        )
        snapshot = progress_evidence({'progress': [sample]}, self.source)
        self.assertEqual(snapshot['rows'][0]['produced_quantity'], '1.250')
        self.assertIn('1.250', json.dumps(snapshot))

    def test_report_limit_and_no_fabricated_records(self):
        report=progress_evidence({'progress':[self.batch]},self.source,limit=1)
        self.assertEqual(report['total_rows'],2)
        self.assertTrue(report['truncated'])
        self.assertEqual(len(report['rows']),1)
        none=progress_evidence({},self.source)
        self.assertEqual(none['rows'],[])
        self.assertFalse(none['truncated'])


if __name__=='__main__':
    unittest.main()
