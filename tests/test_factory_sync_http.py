"""The configured SQL bridge is reachable through the authenticated workbook UI."""
import json
import http.client
from unittest.mock import patch
import unittest

from planning_factory.adapters.factory_workbook_sync import FactoryWorkbookSync
from planning_factory.adapters.factory_extraction import _DEFAULT_VIEWS
from . import test_workbook_instance as workbook_tests


class FactorySyncHTTPTests(unittest.TestCase):
    request=workbook_tests.WorkbookInstanceTests.request
    cleanup=workbook_tests.WorkbookInstanceTests.cleanup

    def setUp(self):
        workbook_tests.WorkbookInstanceTests.setUp(self)
        self.app.factory_sync=FactoryWorkbookSync(self.root,self.app.state,lambda:None)
        self.config=dict(server='HOST\\SQL',database='Factory_Test',user='',driver='ODBC Driver 17 for SQL Server',views=dict(_DEFAULT_VIEWS))

    def test_config_and_connection_test_are_available_without_changing_the_source(self):
        code,result=self.request('/api/factory-source/config',self.config)
        self.assertEqual(code,200)
        self.assertEqual(result['mode'],'factory-excel-planning')
        self.assertEqual(len(result['view_contract']),8)
        with patch.object(self.app.factory_sync,'test_connection',return_value=dict(ok=True,checked_views=8)):
            self.assertEqual(self.request('/api/factory-source/test',{**self.config,'password':'private-test'})[1]['checked_views'],8)
        audit=(self.root/'audit.jsonl').read_text()
        self.assertNotIn('private-test',audit)
        self.assertNotIn('password',json.loads((self.root/'factory-source.json').read_text()))
        self.assertEqual(self.request('/api/bootstrap')[1]['meta']['source'],'planning_workbook')

    def test_non_admin_cannot_change_or_extract_factory(self):
        self.app.local_user=('reader','planner')
        self.assertEqual(self.request('/api/factory-source/config',self.config)[0],403)
        self.assertEqual(self.request('/api/factory-source/run',dict(start='2026-10-26',password=''))[0],403)
        self.assertEqual(self.request('/api/factory-source')[0],200)

    def test_first_sync_without_settings_starts_from_the_current_excel_and_keeps_it_intact(self):
        before=self.path.read_bytes()
        self.assertTrue(self.request('/api/factory-source')[1]['pipeline_configured'])
        with patch.object(FactoryWorkbookSync,'_run_worker'):
            code,result=self.request('/api/factory-source/run',dict(start='2026-10-26',password=''))
        self.assertEqual(code,202)
        self.assertEqual(self.path.read_bytes(),before)
        baseline=json.loads((self.root/'factory-sync/bootstrap-baseline.json').read_text(encoding='utf-8'))
        self.assertEqual(baseline['source']['database'],'Factory_Cliente')
        self.assertTrue(baseline['tables']['operation'])

    def test_documented_sql_is_available_only_for_known_areas_and_admin(self):
        folder=self.root/'integrations/cliente-export/views';folder.mkdir(parents=True)
        (folder/'01_Calendari.sql').write_text('-- Modello commentato\nSELECT 1;',encoding='utf-8')
        (self.root/'factory-sync.json').write_text(json.dumps(dict(pipeline_root=str(self.root),bootstrap_baseline='unused')),encoding='utf-8')
        def get(path):
            con=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
            con.request('GET',path);response=con.getresponse();content=response.read();status=response.status;con.close()
            return status,content
        status,content=get('/api/factory-source/sql/calendari.sql')
        self.assertEqual(status,200);self.assertIn(b'Modello commentato',content)
        self.assertEqual(get('/api/factory-source/sql/not-a-view.sql')[0],422)
        self.app.local_user=('reader','planner')
        self.assertEqual(get('/api/factory-source/sql/calendari.sql')[0],403)
