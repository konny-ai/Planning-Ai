"""#90: authenticated network service with roles, isolated workers, revisions and audit log."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads
from planning_factory.adapters.service import Service, load_credentials, make_service_server, new_token
from planning_factory.domain.validation import ValidationError

ROOT = Path(__file__).resolve().parents[1]
WORKSHOP = json.loads(dumps(loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))))


class ServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        folder = Path(cls.tmp.name)
        cls.tokens, users = {}, []
        for name, role in (('reparto1', 'reparto'), ('planner1', 'planner'), ('admin1', 'admin')):
            token, entry = new_token(name, role)
            cls.tokens[role] = token
            users.append(entry)
        (folder / 'users.json').write_text(json.dumps({'users': users}), encoding='utf-8')
        cls.audit = folder / 'audit.jsonl'
        cls.service = Service(folder / 'store.db', folder / 'users.json', cls.audit, workers=1, job_timeout=120)
        cls.server = make_service_server(cls.service, '127.0.0.1', 0)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def call(self, method, path, body=None, role=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_address[1], timeout=120)
        h = dict(headers or {})
        if role:
            h['Authorization'] = f'Bearer {self.tokens[role]}'
        payload = None
        if body is not None:
            payload = json.dumps(body).encode('utf-8')
            h['Content-Type'] = 'application/json'
        connection.request(method, path, payload, h)
        response = connection.getresponse()
        data = json.loads(response.read().decode('utf-8'))
        connection.close()
        return response.status, data

    def test_1_unauthenticated_and_wrong_role_are_rejected(self):
        self.assertEqual(200, self.call('GET', '/health')[0])
        self.assertEqual(401, self.call('GET', '/v1/scenarios/x')[0])
        self.assertEqual(401, self.call('GET', '/v1/scenarios/x', headers={'Authorization': 'Bearer nope'})[0])
        self.assertEqual(403, self.call('POST', '/v1/scenarios', {'scenario': WORKSHOP}, role='reparto')[0])
        self.assertEqual(403, self.call('GET', '/v1/audit', role='planner')[0])
        self.assertEqual(403, self.call('GET', '/health', headers={'Origin': 'https://evil.example'})[0])

    def test_1b_early_rejections_with_a_body_answer_with_their_status(self):
        # Answering before reading the body made Windows reset the socket (WinError 10053):
        # the client saw an abort instead of the status, as in the local HTTP API (PR #35).
        for _ in range(40):
            for role, headers, code in (('reparto', {}, 403), (None, {}, 401),
                                        ('planner', {'Origin': 'https://evil.example'}, 403)):
                self.assertEqual(code, self.call('POST', '/v1/scenarios', {'scenario': WORKSHOP, 'pad': 'x' * 300_000},
                                                 role=role, headers=headers)[0])

    def test_2_planner_saves_computes_and_the_shop_floor_reads(self):
        status, saved = self.call('POST', '/v1/scenarios', {'scenario': WORKSHOP, 'expected_revision': 0}, role='planner')
        self.assertEqual((200, 1), (status, saved['revision']))
        # A second writer that did not see revision 1 is refused, nothing is overwritten.
        self.assertEqual(409, self.call('POST', '/v1/scenarios', {'scenario': WORKSHOP, 'expected_revision': 0},
                                        role='planner')[0])
        self.assertEqual(409, self.call('POST', '/v1/scenarios/officina-demo/plan', {'expected_revision': 0},
                                        role='planner')[0])
        status, computed = self.call('POST', '/v1/scenarios/officina-demo/plan', {'expected_revision': 1}, role='planner')
        self.assertEqual(200, status)
        self.assertEqual(saved['scenario_digest'], computed['plan']['scenario_digest'])
        status, read = self.call('GET', '/v1/scenarios/officina-demo/plan', role='reparto')
        self.assertEqual((200, computed['plan']), (status, read['plan']))
        status, checked = self.call('POST', '/v1/verify', {'scenario': WORKSHOP, 'plan': read['plan']}, role='reparto')
        self.assertEqual((200, True), (status, checked['valid']))

    def test_3_audit_log_records_who_without_secrets(self):
        status, audit = self.call('GET', '/v1/audit', role='admin')
        self.assertEqual(200, status)
        text = self.audit.read_text(encoding='utf-8')
        self.assertTrue(all(token not in text for token in self.tokens.values()))
        computed = [e for e in audit['entries'] if e['path'].endswith('/plan') and e['method'] == 'POST' and e['status'] == 200]
        self.assertEqual('planner1', computed[-1]['user'])
        self.assertIn('scenario_digest', computed[-1])
        self.assertTrue(any(e['status'] == 401 and e['user'] is None for e in audit['entries']))

    def test_4_job_time_limit(self):
        original = self.service.job_timeout
        self.service.job_timeout = 0.001
        try:
            status, data = self.call('POST', '/v1/scenarios/officina-demo/plan', {'expected_revision': 1}, role='planner')
        finally:
            self.service.job_timeout = original
        self.assertEqual((504, 'compute time limit exceeded'), (status, data['error']))


class ServiceConfigurationTests(unittest.TestCase):
    def test_credentials_never_hold_plain_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'users.json')
            for users in ([{'name': 'a', 'role': 'planner', 'token': 'secret'}],
                          [{'name': 'a', 'role': 'boss', 'token_sha256': '0' * 64}],
                          [{'name': 'a', 'role': 'planner', 'token_sha256': '0' * 64},
                           {'name': 'a', 'role': 'reparto', 'token_sha256': '1' * 64}]):
                path.write_text(json.dumps({'users': users}), encoding='utf-8')
                with self.assertRaises(ValidationError):
                    load_credentials(path)
        with self.assertRaises(ValidationError):
            new_token('x', 'boss')

    def test_outside_loopback_requires_tls_or_a_declared_proxy(self):
        with tempfile.TemporaryDirectory() as tmp:
            token, entry = new_token('a', 'admin')
            Path(tmp, 'users.json').write_text(json.dumps({'users': [entry]}), encoding='utf-8')
            service = Service(Path(tmp, 's.db'), Path(tmp, 'users.json'), Path(tmp, 'a.jsonl'))
            with self.assertRaises(ValueError):
                make_service_server(service, '0.0.0.0', 0)
            server = make_service_server(service, '0.0.0.0', 0, behind_proxy=True)
            server.server_close()


if __name__ == '__main__':
    unittest.main()
