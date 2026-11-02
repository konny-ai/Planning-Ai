"""Loopback API tests for bounded setup alternative search."""
import json
import unittest
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from planning_factory.adapters.http_api import make_server
from planning_factory.adapters.json_io import dumps, loads, plan_loads
from planning_factory.engine.verification import verify_plan
from .helpers import op, scenario


class SetupSearchHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, payload):
        request = Request(self.base + '/v1/setup-search', data=dumps(payload).encode(),
                          headers={'Content-Type':'application/json'})
        with urlopen(request, timeout=4) as response:
            return response.status, json.load(response)

    def test_returns_scenario_paired_with_verified_plan(self):
        s = scenario([op('a'), op('b')])
        rules = {'schema_version':1,'families':{'a':'A','b':'B'},
                 'machines':[{'resource_id':'m','initial_family':'A',
                              'transitions':[{'from_family':'A','to_family':'B','seconds':1800}]}]}
        status, body = self.post({'scenario':s,'rules':rules,'budget':8,'repair_budget':8})
        adjusted = loads(json.dumps(body['scenario']))
        result = plan_loads(json.dumps(body['plan']))
        self.assertEqual(status, 200)
        self.assertEqual(verify_plan(adjusted, result), ())
        self.assertTrue(body['setup_audit']['valid'])
        self.assertLessEqual(body['candidates_evaluated'], 8)

    def test_bad_budget_is_rejected(self):
        s = scenario([op('a')])
        rules = {'schema_version':1,'families':{'a':'A'},'machines':[{'resource_id':'m','initial_family':'A','transitions':[]}]}
        request = Request(self.base+'/v1/setup-search',data=dumps({'scenario':s,'rules':rules,'budget':0}).encode(),headers={'Content-Type':'application/json'})
        with self.assertRaises(HTTPError) as error:
            urlopen(request, timeout=4)
        self.assertEqual(error.exception.code,422)
