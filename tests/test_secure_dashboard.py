"""Authenticated HTTP sessions, roles, atomic revisions and audited reload."""
from contextlib import closing
from dataclasses import replace
import http.client
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from planning_factory.adapters.json_io import dumps
from planning_factory.adapters.claude_assistant import CallQuota, ClaudeAssistant
from planning_factory.adapters.secure_dashboard import make_secure_dashboard_server
from planning_factory.adapters.service import new_token
from planning_factory.engine.scheduler import plan
from .helpers import op, scenario


class SecureDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.tokens, users = {}, []
        for role in ('planner', 'reparto'):
            token, entry = new_token(role + '-user', role)
            self.tokens[role] = token
            users.append(entry)
        self.source = scenario((op('a'),))
        (folder / 'scenario.json').write_text(dumps(self.source))
        (folder / 'users.json').write_text(json.dumps({'users': users}))
        self.server = make_secure_dashboard_server(folder / 'scenario.json', folder / 'users.json',
                                                    folder / 'snapshots.db', folder / 'audit.jsonl', port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5); self.tmp.cleanup()

    def request(self, method, path, body=None, cookie=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        h = dict(headers or {})
        if cookie:
            h['Cookie'] = cookie
        raw = None
        if body is not None:
            raw = json.dumps(body).encode(); h['Content-Type'] = 'application/json'
        connection.request(method, path, raw, h)
        response = connection.getresponse()
        content = response.read()
        result = (response.status, dict(response.getheaders()), content)
        connection.close()
        return result

    def login(self, role='planner'):
        status, headers, body = self.request('POST', '/api/login', {'token': self.tokens[role]})
        self.assertEqual(200, status)
        self.assertIn('HttpOnly', headers['Set-Cookie'])
        self.assertIn('SameSite=Strict', headers['Set-Cookie'])
        return headers['Set-Cookie'].split(';')[0]

    def test_authentication_role_logout_and_audit_redaction(self):
        self.assertEqual(401, self.request('GET', '/api/bootstrap')[0])
        self.assertIn(b'login-form', self.request('GET', '/')[2])
        self.assertEqual(403, self.request('POST', '/api/login', {'token': self.tokens['planner']},
                                         headers={'Origin': 'https://evil.example'})[0])
        cookie = self.login('reparto')
        self.assertEqual(200, self.request('GET', '/api/bootstrap', cookie=cookie)[0])
        self.assertEqual(403, self.request('POST', '/api/recalculate', {'settings': {}}, cookie)[0])
        self.assertEqual(403, self.request('POST', '/api/snapshots', {}, cookie)[0])
        self.assertEqual(200, self.request('POST', '/api/logout', {}, cookie)[0])
        self.assertEqual(401, self.request('GET', '/api/bootstrap', cookie=cookie)[0])
        audit = self.server.application.audit_path.read_text()
        self.assertTrue(all(token not in audit for token in self.tokens.values()))
        self.assertNotIn(cookie.split('=', 1)[1], audit)

    def test_save_reopen_history_and_conflict_without_overwrite(self):
        cookie = self.login()
        source, result = json.loads(dumps(self.source)), json.loads(dumps(plan(self.source)))
        payload = {'scenario': source, 'plan': result, 'expected_revision': 0}
        self.assertEqual(200, self.request('POST', '/api/snapshots', payload, cookie)[0])
        self.assertEqual(409, self.request('POST', '/api/snapshots', payload, cookie)[0])
        newer = replace(self.source, id=self.source.id, settings=replace(self.source.settings, mode='backward'))
        payload = {'scenario': json.loads(dumps(newer)), 'plan': json.loads(dumps(plan(newer))), 'expected_revision': 1}
        self.assertEqual(200, self.request('POST', '/api/snapshots', payload, cookie)[0])
        history = json.loads(self.request('GET', '/api/snapshots', cookie=cookie)[2])
        self.assertEqual([2, 1], [r['revision'] for r in history['revisions']])
        for revision, expected in ((1, source), (2, payload['scenario'])):
            status, _, raw = self.request('GET', f'/api/snapshots/{self.source.id}/revisions/{revision}', cookie=cookie)
            self.assertEqual(200, status)
            self.assertEqual(expected, json.loads(raw)['scenario'])
            self.assertFalse(json.loads(raw)['meta']['source_publishable'])

    def test_invalid_plan_rolls_back_both_scenario_and_plan(self):
        cookie = self.login()
        invalid = json.loads(dumps(plan(self.source)))
        invalid['assignments'] = []
        payload = {'scenario': json.loads(dumps(self.source)), 'plan': invalid, 'expected_revision': 0}
        self.assertEqual(422, self.request('POST', '/api/snapshots', payload, cookie)[0])
        self.assertEqual([], self.server.application.store.list_revisions())

    def test_expired_session_is_rejected(self):
        cookie = self.login()
        with self.server.application.lock:
            self.server.application.sessions = {k: (v[0], 0) for k, v in self.server.application.sessions.items()}
        self.assertEqual(401, self.request('GET', '/api/bootstrap', cookie=cookie)[0])

    def test_database_write_failure_rolls_back_all_tables(self):
        cookie = self.login()
        with closing(sqlite3.connect(self.server.application.store.path)) as db, db:
            db.execute("CREATE TRIGGER fail_plan BEFORE INSERT ON plans BEGIN SELECT RAISE(ABORT, 'injected failure'); END")
        payload = {'scenario': json.loads(dumps(self.source)), 'plan': json.loads(dumps(plan(self.source))), 'expected_revision': 0}
        self.assertEqual(503, self.request('POST', '/api/snapshots', payload, cookie)[0])
        self.assertEqual([], self.server.application.store.list_revisions())
        with closing(sqlite3.connect(self.server.application.store.path)) as db, db:
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM scenarios').fetchone()[0])
            self.assertEqual(0, db.execute('SELECT COUNT(*) FROM snapshot_plans').fetchone()[0])

    def test_exact_historical_plan_survives_same_scenario_recalculation(self):
        store = self.server.application.store
        source = scenario((op('a'), op('b')))
        first = plan(source)
        # Independent audit permits another serialization order. Preserve the
        # exact result of each save even when the Scenario digest is identical.
        second = replace(first, assignments=tuple(reversed(first.assignments)))
        self.assertNotEqual(first, second)
        store.save_snapshot(source, first, expected_revision=0)
        store.save_snapshot(source, second, expected_revision=1)
        self.assertEqual(first, store.load_snapshot(source.id, 1)[1])
        self.assertEqual(second, store.load_snapshot(source.id, 2)[1])

    def test_assistant_is_opt_in_and_audits_the_current_plan(self):
        cookie = self.login('reparto')
        payload = {'question': 'Quali ritardi?', 'scenario': json.loads(dumps(self.source)),
                   'plan': json.loads(dumps(plan(self.source)))}
        self.assertEqual(503, self.request('POST', '/api/assistant', payload, cookie)[0])
        calls = []
        def provider(request):
            calls.append(request)
            return {'content': [{'type': 'text', 'text': 'Risposta di prova senza chiamate esterne.'}], 'usage': {'input_tokens': 10, 'output_tokens': 8}}
        app = self.server.application
        app.assistant = ClaudeAssistant('test-key', 'test-model', CallQuota(app.store.path, 10, 20), transport=provider)
        status, _, raw = self.request('POST', '/api/assistant', payload, cookie)
        self.assertEqual(200, status)
        self.assertEqual(payload['plan']['scenario_digest'], json.loads(raw)['scenario_digest'])
        self.assertEqual(1, len(calls))
        payload['plan']['assignments'] = []
        self.assertEqual(422, self.request('POST', '/api/assistant', payload, cookie)[0])
        self.assertEqual(1, len(calls))

    def test_rules_require_planner_confirmation_and_invalidate_cached_plan(self):
        planner = self.login()
        shop = self.login('reparto')
        before = json.loads(self.request('GET', '/api/bootstrap', cookie=planner)[2])
        payload = {'rule': {'action': 'set_priority', 'value': -10, 'order_id': self.source.operations[0].order_id}}
        self.assertEqual(403, self.request('POST', '/api/rules', payload, shop)[0])
        status, _, raw = self.request('POST', '/api/rules', payload, planner)
        self.assertEqual(200, status)
        draft = json.loads(raw)
        self.assertEqual(before['plan'], json.loads(self.request('GET', '/api/bootstrap', cookie=planner)[2])['plan'])
        self.assertEqual(200, self.request('POST', '/api/rules/preview', {'rule_id': draft['id']}, planner)[0])
        decision = {'rule_id': draft['id'], 'expected_version': 1, 'decision': 'confirm'}
        self.assertEqual(403, self.request('POST', '/api/rules/decision', decision, shop)[0])
        self.assertEqual(200, self.request('POST', '/api/rules/decision', decision, planner)[0])
        self.assertEqual(409, self.request('POST', '/api/rules/decision', decision, planner)[0])
        after = json.loads(self.request('GET', '/api/bootstrap', cookie=planner)[2])
        self.assertNotEqual(before['plan']['scenario_digest'], after['plan']['scenario_digest'])
        self.assertEqual(-10, after['scenario']['operations'][0]['priority'])
        self.assertTrue(after['meta']['company_memory']['applied'])

    def test_llm_simulation_is_audited_temporary_and_role_guarded(self):
        planner, shop = self.login(), self.login('reparto')
        app = self.server.application
        closure = {'resource_id': self.source.resources[0].id,
                   'start': '2026-10-19T08:00:00Z', 'end': '2026-10-19T09:00:00Z', 'reason': 'SYNTHETIC maintenance'}
        proposal = {'supported': True, 'reason': 'Explicit request', 'edits': {'fixed_tasks': [], 'closures': [closure]}}
        def provider(payload):
            return {'content': [{'type': 'tool_use', 'id': 'simulation', 'name': 'suggest_simulation', 'input': proposal}]}
        app.assistant = ClaudeAssistant('test', 'test-model', CallQuota(app.store.path, 10, 20), transport=provider)
        payload = {'question': 'Ferma la macchina per un’ora', 'scenario': json.loads(dumps(self.source)),
                   'plan': json.loads(dumps(plan(self.source)))}
        original = app.state.bootstrap()['plan']
        self.assertEqual(403, self.request('POST', '/api/simulations/suggest', payload, shop)[0])
        status, _, raw = self.request('POST', '/api/simulations/suggest', payload, planner)
        self.assertEqual(200, status, raw.decode())
        preview = json.loads(raw)['preview']
        self.assertTrue(preview['meta']['engine_verified'])
        self.assertFalse(preview['meta']['source_publishable'])
        self.assertEqual(original, app.state.bootstrap()['plan'])
        self.assertEqual([], app.store.list_revisions())
        proposal['edits']['closures'][0]['resource_id'] = 'UNKNOWN-RESOURCE'
        self.assertEqual(422, self.request('POST', '/api/simulations/suggest', payload, planner)[0])

    def test_server_evidence_is_immutable_archived_and_not_client_asserted(self):
        cookie = self.login()
        app = self.server.application
        # Synthetic provenance asserted by the fixture supplier, never certified.
        app.state.manifest = {'scenario_digest': 'source-v1', 'publishable': False,
                              'domains': {}, 'materials': {'shortages': [{'item_id': 'SHEET', 'missing': '2'}]}}
        view = json.loads(self.request('GET', '/api/bootstrap', cookie=cookie)[2])
        payload = {'scenario': view['scenario'], 'plan': view['plan'], 'expected_revision': 0,
                   'evidence_id': view['meta']['evidence_id']}
        app.state.manifest['materials']['shortages'][0]['missing'] = '999'
        self.assertEqual(200, self.request('POST', '/api/snapshots', payload, cookie)[0])
        evidence = app.store.load_evidence(self.source.id, 1)
        self.assertFalse(evidence['production_approved'])
        self.assertEqual('2', evidence['evidence']['source_manifest']['materials']['shortages'][0]['missing'])
        reopened = json.loads(self.request('GET', f'/api/snapshots/{self.source.id}/revisions/1', cookie=cookie)[2])
        self.assertEqual('2', reopened['meta']['canonical']['materials']['shortages'][0]['missing'])
        self.assertFalse(reopened['meta']['source_publishable'])
        payload['expected_revision'] = 1
        payload['evidence_id'] = 'FORGED-client-provenance'
        self.assertEqual(409, self.request('POST', '/api/snapshots', payload, cookie)[0])
        self.assertEqual(1, len(app.store.list_revisions()))
        payload.pop('evidence_id')
        payload['canonical'] = {'publishable': True}
        self.assertEqual(422, self.request('POST', '/api/snapshots', payload, cookie)[0])

    def test_archive_preview_uses_frozen_inputs_and_preserves_lineage(self):
        cookie = self.login()
        shop = self.login('reparto')
        app = self.server.application
        app.store.save_snapshot(self.source, plan(self.source), expected_revision=0,
                                evidence={'source': 'SYNTHETIC original', 'source_certified': False})
        draft = app.rules.create_draft({'action': 'add_setup_seconds', 'value': 600,
                                        'order_id': self.source.operations[0].order_id}, 'planner')
        app.rules.decide(draft['id'], 1, 'confirm', 'planner')
        data = {'scenario_id': self.source.id, 'revision': 1, 'settings': {},
                'edits': {'fixed_tasks': [], 'closures': [{
                    'resource_id': self.source.resources[0].id, 'start': '2026-10-19T08:00:00Z',
                    'end': '2026-10-19T09:00:00Z', 'reason': 'SYNTHETIC maintenance'}]}}
        self.assertEqual(403, self.request('POST', '/api/snapshots/preview', data, shop)[0])
        status, _, body = self.request('POST', '/api/snapshots/preview', data, cookie)
        self.assertEqual(200, status, body.decode())
        preview = json.loads(body)
        self.assertEqual(self.source.operations[0].setup_seconds, preview['scenario']['operations'][0]['setup_seconds'])
        self.assertNotEqual(plan(self.source).scenario_digest, preview['plan']['scenario_digest'])
        payload = {'scenario': preview['scenario'], 'plan': preview['plan'], 'expected_revision': 1,
                   'evidence_id': preview['meta']['evidence_id']}
        self.assertEqual(200, self.request('POST', '/api/snapshots', payload, cookie)[0])
        self.assertEqual((self.source, plan(self.source)), app.store.load_snapshot(self.source.id, 1))
        evidence = app.store.load_evidence(self.source.id, 2)['evidence']
        self.assertEqual(1, evidence['lineage']['revision'])
        self.assertEqual('SYNTHETIC original', evidence['parent']['evidence']['source'])
        self.assertEqual(409, self.request('POST', '/api/snapshots', payload, cookie)[0])

    def test_evidence_corruption_and_transaction_failure_are_detected(self):
        app = self.server.application
        with closing(sqlite3.connect(app.store.path)) as db, db:
            db.execute("CREATE TRIGGER fail_evidence BEFORE INSERT ON snapshot_evidence BEGIN SELECT RAISE(ABORT, 'injected'); END")
        with self.assertRaises(sqlite3.Error):
            app.store.save_snapshot(self.source, plan(self.source), expected_revision=0, evidence={'source': 'test'})
        self.assertEqual([], app.store.list_revisions())
        with closing(sqlite3.connect(app.store.path)) as db, db:
            db.execute('DROP TRIGGER fail_evidence')
        app.store.save_snapshot(self.source, plan(self.source), expected_revision=0, evidence={'source': 'test'})
        with closing(sqlite3.connect(app.store.path)) as db, db:
            db.execute("UPDATE snapshot_evidence SET payload='{}'")
        cookie = self.login()
        self.assertEqual(422, self.request('GET', f'/api/snapshots/{self.source.id}/revisions/1', cookie=cookie)[0])

    def test_audit_file_failure_does_not_disguise_a_committed_snapshot(self):
        cookie = self.login()
        app = self.server.application
        app.audit_path.unlink()
        app.audit_path.mkdir()  # opening a directory as an append log must fail
        payload = {'scenario': json.loads(dumps(self.source)), 'plan': json.loads(dumps(plan(self.source))), 'expected_revision': 0}
        status, _, raw = self.request('POST', '/api/snapshots', payload, cookie)
        self.assertEqual(200, status)
        self.assertFalse(json.loads(raw)['audit_file_written'])
        self.assertEqual('planner-user', app.store.load_evidence(self.source.id, 1)['recorded_by'])
        self.assertEqual((self.source, plan(self.source)), app.store.load_snapshot(self.source.id, 1))
        self.assertEqual(409, self.request('POST', '/api/snapshots', payload, cookie)[0])
