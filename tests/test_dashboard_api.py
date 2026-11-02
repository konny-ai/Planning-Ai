"""Dashboard API contract regression tests."""
import json
from pathlib import Path
import tempfile
import unittest

from planning_factory.adapters.dashboard_api import (
    DashboardState, DEFAULT_FIXTURE, PlannerBusyError, make_dashboard_server,
)
from planning_factory.domain.validation import ValidationError


class DashboardApiTests(unittest.TestCase):
    def test_baseline_and_recalculation_do_not_mutate_input(self):
        state = DashboardState(DEFAULT_FIXTURE)
        settings = state.source.settings
        original = state.bootstrap()
        recalculated = state.bootstrap({'mode':'forward'})
        self.assertTrue(original['meta']['engine_verified'])
        self.assertTrue(recalculated['meta']['engine_verified'])
        self.assertFalse(original['meta']['erp_connected'])
        self.assertEqual(state.source.settings, settings)
        self.assertEqual(state.bootstrap()['plan']['scenario_digest'],
                         original['plan']['scenario_digest'])

    def test_bad_settings_fail_closed(self):
        state = DashboardState(DEFAULT_FIXTURE)
        for edits in ({'mode':'invalid'}, {'priority_weight':True},
                      {'unknown':1}, {'auto_backward_days':3661}):
            with self.subTest(edits=edits), self.assertRaises(ValidationError):
                state.bootstrap(edits)

    def test_file_binding_forbids_sql_and_unsafe_connectors(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory)/'binding.json'
            for connector in ('sql','factory-export','https'):
                with self.subTest(connector=connector):
                    filename.write_text(json.dumps({
                        'installation':'test',
                        'sources':{'unsafe':{'connector':connector,'options':{}}},
                        'domains':{'routings':{'sources':['unsafe']}},
                    }), encoding='utf-8')
                    with self.assertRaisesRegex(ValueError, 'only permit local file'):
                        DashboardState(filename)

    def test_demo_compute_rejects_parallel_planning(self):
        state = DashboardState(DEFAULT_FIXTURE)
        self.assertTrue(state._compute_slot.acquire(blocking=False))
        try:
            with self.assertRaises(PlannerBusyError):
                state.calculate()
        finally:
            state._compute_slot.release()

    def test_demo_rejects_large_fixture_before_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory)/'oversized.json'
            with filename.open('wb') as stream:
                stream.truncate(64*1024*1024+1)  # sparse: no 64 MiB write
            with self.assertRaisesRegex(ValueError,'64 MiB'):
                DashboardState(filename)

    def test_duplicate_or_nonfinite_file_payloads_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            filename = Path(directory)/'scenario.json'
            for content in (
                '{"schema_version":1,"schema_version":2}',
                '{"id":"malformed","duration_seconds":NaN}',
            ):
                with self.subTest(payload=content), self.assertRaises(ValueError):
                    self._read_payload(filename, content)

    @staticmethod
    def _read_payload(filename, content):
        filename.write_text(content,encoding='utf-8')
        return DashboardState(filename)

    def test_nonpublishable_manifest_still_exposes_blocking_issues(self):
        from planning_factory.canonical.model import Issue
        state = DashboardState(DEFAULT_FIXTURE)
        state.manifest = {
            'publishable': False,
            'scenario_digest': 'scenario',
            'domains': {'materials': {'digest': 'materials', 'records': 3}},
            'materials': {'allocation_order': 'due', 'pegs': [],
                          'shortages': [{'unit': 'job:sample', 'item_id': 'S',
                                         'missing': '2.5'}]},
        }
        state.build_issues = (Issue('block', 'material_shortage',
                                    'materials', '2.5 missing'),)
        context = state.canonical_context()
        self.assertFalse(context['publishable'])
        self.assertEqual(context['issues'][0]['code'], 'material_shortage')
        self.assertEqual(context['materials']['shortages'][0]['missing'], '2.5')

    def test_demo_host_anti_rebinding_policy(self):
        from planning_factory.adapters.dashboard_api import make_handler
        handler = object.__new__(make_handler(None))
        handler.headers = {"Host": "untrusted.example:8766"}
        self.assertFalse(handler._trusted_host())
        handler.headers = {"Host": "127.0.0.1:8766"}
        self.assertTrue(handler._trusted_host())
        handler.headers = {"Host": "workspace-8766.app.github.dev"}
        self.assertTrue(handler._trusted_host())

    def test_server_binds_loopback_only(self):
        with self.assertRaisesRegex(ValueError,'loopback'):
            make_dashboard_server(DEFAULT_FIXTURE,host='0.0.0.0',port=0)


if __name__ == '__main__':
    unittest.main()
