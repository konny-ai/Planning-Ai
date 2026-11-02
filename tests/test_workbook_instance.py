"""A workbook is the only input even when the instance has old rules/corrections."""
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from planning_factory.adapters.secure_dashboard import make_secure_dashboard_server
from planning_factory.adapters.workbook_io import write_tables
from planning_factory.adapters.rule_memory import RuleMemory
from planning_factory.adapters.assistant_actions import AssistantActions
from tools.build_workbook_demo import bending_tables


class WorkbookInstanceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.path=self.root/'input.xlsx';self.tables=bending_tables(unattended=True)
        write_tables(self.path,self.tables)
        self.store=self.root/'plans.sqlite'
        legacy=RuleMemory(self.store)
        draft=legacy.create_draft(dict(action='set_priority',value=-100,order_id='DEMO-ORDER'),'old')
        legacy.decide(draft['id'],1,'confirm','old')
        self.server=make_secure_dashboard_server(self.path,None,self.store,self.root/'audit.jsonl',local_user='test',port=0)
        self.app=self.server.application
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown();self.server.server_close();self.thread.join(5);self.tmp.cleanup()

    def request(self,path,body=None):
        con=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=15)
        con.request('GET' if body is None else 'POST',path,None if body is None else json.dumps(body),
                    {} if body is None else {'Content-Type':'application/json'})
        response=con.getresponse();content=response.read();status=response.status
        con.close()
        return status,content if path.endswith('.xlsx') else json.loads(content)

    def test_inputs_and_rules_come_only_from_the_workbook(self):
        code,data=self.request('/api/bootstrap');self.assertEqual(code,200)
        self.assertEqual(data['meta']['source'],'planning_workbook')
        self.assertTrue(all(o['priority']==0 for o in data['scenario']['operations']))
        self.assertEqual(self.request('/api/rules')[1]['rules'],[])
        self.assertFalse(self.request('/api/data/catalog')[1]['editable'])
        self.assertEqual(self.request('/api/data/rows?entity=operation')[1]['total'],1)
        self.assertEqual(len(self.request('/api/sources')[1]['sources']),1)
        self.assertEqual(self.request('/api/rules',{'rule':{'action':'note','note':'not a second source'}})[0],422)
        self.assertEqual(self.request('/api/data/corrections',dict(entity='operation',action='update',
            key=['BEND'],values={'priority':-99},reason='must edit Excel'))[0],422)
        actions=AssistantActions(self.app,('test','admin'),{})
        self.assertNotIn('create_rule',actions.names)
        self.assertNotIn('correct_data',actions.names)
        self.assertEqual(len(RuleMemory(self.store).list()),1)

    def test_reload_is_atomic_and_changed_file_blocks_the_old_snapshot(self):
        before=self.app.state.workbook_input.fingerprint
        self.path.write_bytes(b'half saved Excel')
        self.assertEqual(self.request('/api/bootstrap')[0],422)
        self.assertEqual(self.request('/api/sources/reload',{})[0],422)
        self.assertEqual(self.app.state.workbook_input.fingerprint,before)
        changed=deepcopy(self.tables);changed['operation'][0]['declared_work_minutes']=6
        write_tables(self.path,changed)
        self.assertEqual(self.request('/api/sources/reload',{})[0],200)
        self.assertNotEqual(self.app.state.workbook_input.fingerprint,before)
        code,data=self.request('/api/bootstrap');self.assertEqual(code,200)
        self.assertEqual(next(o for o in data['scenario']['operations'] if o['id']=='BEND')['duration_seconds'],72000)

    def test_open_uses_only_the_active_workbook_and_rejects_arbitrary_paths(self):
        with patch('planning_factory.adapters.secure_dashboard.os.startfile',create=True) as opened:
            self.assertTrue(self.request('/api/session')[1]['workbook_open_available'])
            self.assertEqual(self.request('/api/sources/open-workbook',{'path':'other.xlsx'})[0],422)
            opened.assert_not_called()
            self.assertEqual(self.request('/api/sources/open-workbook',{})[0],200)
            opened.assert_called_once_with(str(self.path.resolve()),'open')

    def test_open_is_unavailable_for_an_authenticated_remote_session(self):
        with patch('planning_factory.adapters.secure_dashboard.os.startfile',create=True) as opened:
            with patch.object(self.app,'session',return_value=('session',('test','admin'))):
                self.app.local_user=None
                self.assertFalse(self.request('/api/session')[1]['workbook_open_available'])
                self.assertEqual(self.request('/api/sources/open-workbook',{})[0],403)
            opened.assert_not_called()

    def test_saved_revision_contains_the_exact_input_and_staged_scale(self):
        _,data=self.request('/api/bootstrap')
        code,saved=self.request('/api/snapshots',dict(scenario=data['scenario'],plan=data['plan'],
            expected_revision=0,evidence_id=data['meta']['evidence_id']))
        self.assertEqual(code,200)
        self.assertTrue(self.request('/api/snapshots')[1]['revisions'][0]['has_workbook'])
        path=f"/api/snapshots/{saved['scenario_id']}/revisions/1"
        code,content=self.request(path+'/input.xlsx');self.assertEqual(code,200)
        self.assertEqual(hashlib.sha256(content).hexdigest(),hashlib.sha256(self.path.read_bytes()).hexdigest())
        self.app.state._plans.clear();self.app.state._workbook_result=None
        self.assertEqual(self.request(path)[0],200)
        insight=self.app.state.insights(data['plan']['scenario_digest'])
        self.assertEqual(insight.capacity_scale,10000)
        evidence=self.app.store.load_evidence(saved['scenario_id'],1)['evidence']
        self.assertEqual(evidence['workbook']['workbook_sha256'],hashlib.sha256(content).hexdigest())
