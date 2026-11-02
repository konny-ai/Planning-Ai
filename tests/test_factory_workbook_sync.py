"""Source refresh preserves edits, stops conflicts and atomically installs an audited plan."""
from copy import deepcopy
from datetime import UTC,datetime
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from planning_factory.adapters.dashboard_api import DashboardState
from planning_factory.adapters.factory_workbook_sync import FactoryWorkbookSync
from planning_factory.adapters.factory_extraction import FactoryExtraction,_DEFAULT_VIEWS
from planning_factory.adapters.workbook_merge import merge_tables,json_bytes
from planning_factory.adapters.workbook_io import write_tables
from planning_factory.adapters.planning_workbook import load_workbook,summary
from planning_factory.adapters.json_io import dumps,scenario_digest
from planning_factory.domain.validation import ValidationError
from tools.build_workbook_demo import bending_tables


class MergeTests(unittest.TestCase):
    def test_local_and_source_edits_merge_without_reapplying_old_corrections(self):
        base=bending_tables();local=deepcopy(base);remote=deepcopy(base)
        local['operation'][0]['declared_work_minutes']=6
        local['profile'][0]['priority_weight']=99
        remote['operation'][0]['description']='New ERP description'
        merged,report=merge_tables(base,local,remote)
        self.assertFalse(report['conflicts'])
        self.assertEqual(merged['operation'][0]['declared_work_minutes'],6)
        self.assertEqual(merged['operation'][0]['description'],'New ERP description')
        self.assertEqual(merged['profile'][0]['priority_weight'],99)

    def test_dual_change_and_delete_update_conflict_need_an_explicit_choice(self):
        base=bending_tables();local=deepcopy(base);remote=deepcopy(base)
        local['operation'][0]['declared_work_minutes']=6
        remote['operation'][0]['declared_work_minutes']=7
        _,report=merge_tables(base,local,remote)
        self.assertEqual(report['conflict_count'],1)
        for choice,expected in (('excel',6),('factory',7)):
            merged,_=merge_tables(base,local,remote,resolution=choice)
            self.assertEqual(merged['operation'][0]['declared_work_minutes'],expected)
        local['operation']=[]
        _,report=merge_tables(base,local,remote)
        self.assertTrue(any(c['field']=='*' and c['excel_absent'] for c in report['conflicts']))

    def test_missing_null_and_zero_are_not_conflated(self):
        base=bending_tables();local=deepcopy(base);remote=deepcopy(base)
        local['operation'][0]['priority']=0
        remote['operation'][0]['priority']=1
        _,report=merge_tables(base,local,remote)
        self.assertTrue(any(c['field']=='priority' for c in report['conflicts']))

    def test_repeated_refresh_keeps_an_existing_manual_change(self):
        base=bending_tables();local=deepcopy(base);remote=deepcopy(base)
        local['operation'][0]['declared_work_minutes']=6
        remote['operation'][0]['description']='Source 1'
        first,_=merge_tables(base,local,remote)
        next_remote=deepcopy(remote);next_remote['operation'][0]['description']='Source 2'
        second,report=merge_tables(remote,first,next_remote)
        self.assertFalse(report['conflicts'])
        self.assertEqual(second['operation'][0]['declared_work_minutes'],6)
        self.assertEqual(second['operation'][0]['description'],'Source 2')


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.input=self.root/'input.xlsx'
        write_tables(self.input,bending_tables(unattended=True));self.original=self.input.read_bytes()
        self.state=DashboardState(self.input)
        self.manager=FactoryWorkbookSync(self.root,self.state,lambda:None)
        self.run=self.root/'candidate';self.run.mkdir()
        changed=bending_tables(unattended=True);changed['operation'][0]['declared_work_minutes']=6
        write_tables(self.run/'candidate.xlsx',changed)
        wb=load_workbook(self.run/'candidate.xlsx');scenario,plan,evidence=wb.calculate()
        (self.run/'scenario.json').write_text(dumps(scenario),encoding='utf-8')
        (self.run/'plan.json').write_text(dumps(plan),encoding='utf-8')
        content=json_bytes(dict(tables=changed))
        (self.run/'baseline.json').write_bytes(content)
        self.report=dict(workbook=summary(wb),input_scenario_digest=scenario_digest(wb.source),
            baseline_hash=hashlib.sha256(content).hexdigest(),evidence=evidence)
        self.request=dict(expected_input_hash=hashlib.sha256(self.original).hexdigest())

    def test_publication_preserves_previous_file_and_installs_verified_cache(self):
        self.assertTrue(self.manager._publish(self.run,self.request,self.report))
        self.assertNotEqual(self.input.read_bytes(),self.original)
        self.assertIsNotNone(self.state._workbook_result)
        self.assertEqual(self.state.path,self.input)
        self.assertEqual((self.root/'factory-sync/history'/(self.request['expected_input_hash']+'.xlsx')).read_bytes(),self.original)

    def test_changed_input_is_not_overwritten(self):
        self.input.write_bytes(b'new user file')
        with self.assertRaisesRegex(ValidationError,'cambiato'):
            self.manager._publish(self.run,self.request,self.report)
        self.assertEqual(self.input.read_bytes(),b'new user file')

    def test_installation_failure_rolls_back_the_workbook(self):
        with patch.object(self.state,'adopt_workbook',side_effect=RuntimeError('test failure')):
            with self.assertRaises(RuntimeError):self.manager._publish(self.run,self.request,self.report)
        self.assertEqual(self.input.read_bytes(),self.original)

    def test_cancel_before_publication_keeps_the_current_file(self):
        self.manager._cancelled=True
        self.assertFalse(self.manager._publish(self.run,self.request,self.report))
        self.assertEqual(self.input.read_bytes(),self.original)


class ConnectionConfigTests(unittest.TestCase):
    def test_config_never_accepts_password_storage_or_sql_identifiers(self):
        with tempfile.TemporaryDirectory() as folder:
            target=FactoryExtraction(folder,folder,folder,lambda:None)
            c=dict(server='HOST\\SQL',database='Factory_Test',user='',views=dict(_DEFAULT_VIEWS))
            self.assertEqual(target.save_config(c)['driver'],'ODBC Driver 17 for SQL Server')
            with self.assertRaises(ValidationError):target.save_config({**c,'password':'not persisted'})
            bad=deepcopy(c);bad['views']['16_stati']='dbo.view; DROP TABLE X'
            with self.assertRaises(ValidationError):target.save_config(bad)
            self.assertNotIn('password',json.loads(target.config_path.read_text()))
