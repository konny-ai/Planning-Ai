"""Control Room data screens: local corrections, sources page, tokenless local access, AI settings."""
import base64
import hashlib
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from planning_factory.adapters.ai_settings import AIManager, parse_env_file, update_env_file
from planning_factory.adapters.claude_subscription import Subscription
from planning_factory.adapters.dashboard_api import DashboardState
from planning_factory.adapters.data_workspace import DataWorkspace, italian
from planning_factory.adapters.json_io import loads
from planning_factory.adapters.llm_providers import ProviderError, explain, from_openai, recommended, to_openai
from planning_factory.adapters.secure_dashboard import make_secure_dashboard_server
from planning_factory.canonical.corrections import CorrectionStore, check
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.domain.validation import ValidationError

ROOT = Path(__file__).resolve().parents[1]
WORKSHOP = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
ALL = ('resources', 'calendars', 'orders', 'routings', 'nesting', 'subcontract', 'progress', 'rules')


def write_binding(folder):
    bundle = scenario_to_bundle(WORKSHOP, datetime(2026, 10, 19, 6, tzinfo=UTC))
    Path(folder, 'bundle.json').write_text(json.dumps(bundle), encoding='utf-8')
    data = {'schema_version': 1, 'installation': 'demo',
            'scenario': {'id': 'officina-demo', 'start': '2026-10-19T00:00:00Z', 'end': '2026-10-26T00:00:00Z'},
            'sources': {'files': {'connector': 'canonical-json', 'options': {'path': 'bundle.json'}}},
            'domains': {d: {'sources': ['files']} for d in ALL}}
    path = Path(folder, 'binding.json')
    path.write_text(json.dumps(data), encoding='utf-8')
    return path


class CorrectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.binding = write_binding(self.tmp.name)
        self.store = Path(self.tmp.name, 'instance.sqlite')
        self.state = DashboardState(self.binding)
        self.data = DataWorkspace(self.state, self.store)

    def operation(self, op_id):
        return next(o for o in self.state.source.operations if o.id == op_id)

    def test_update_is_validated_applied_recorded_and_revoked(self):
        before = self.operation('ord-a/taglio').duration_seconds
        result = self.data.correct('operation', 'update', ['ord-a/taglio'], {'duration_seconds': 7200},
                                   'tempo misurato in reparto', 'planner')
        self.assertEqual(7200, self.operation('ord-a/taglio').duration_seconds)
        self.assertEqual(1, result['summary']['corrections_active'])
        rows = self.data.rows('operation', status='updated')['rows']
        self.assertEqual([['ord-a/taglio']], [r['key'] for r in rows])
        self.assertEqual(['files'], rows[0]['sources'])
        history = self.data.history('operation', ['ord-a/taglio'])
        self.assertEqual({'duration_seconds': before}, history[0]['previous'])
        self.assertEqual('planner', history[0]['actor'])
        self.assertEqual(1, self.state.manifest['corrections']['active'])
        self.data.revoke([result['correction']['id']], 'misura errata', 'planner')
        self.assertEqual(before, self.operation('ord-a/taglio').duration_seconds)
        self.assertFalse(self.data.history('operation', ['ord-a/taglio'])[0]['active'])
        self.assertEqual('source', self.data.rows('operation', q='ord-a/taglio')['rows'][0]['status'])

    def test_corrections_survive_a_restart_and_a_reread_of_the_files(self):
        self.data.correct('order', 'update', ['A'], {'priority': 5}, 'cliente strategico', 'planner')
        state = DashboardState(self.binding)
        DataWorkspace(state, self.store)
        self.assertEqual(1, len(state.corrections))
        self.assertEqual('updated', DataWorkspace(state, self.store).rows('order', q='A')['rows'][0]['status'])
        summary = self.data.reload()
        self.assertEqual(1, summary['corrections']['active'])

    def test_a_correction_that_breaks_the_data_is_refused_and_not_stored(self):
        with self.assertRaises(ValidationError) as caught:
            self.data.correct('resource', 'exclude', ['LASER-1'], {}, 'macchina dismessa', 'planner')
        self.assertTrue(any('non sarebbero più calcolabili' in e for e in italian(caught.exception.errors)))
        self.assertEqual((), CorrectionStore(self.store).active())
        self.assertEqual((), self.state.corrections)

    def test_insert_and_exclude_show_in_the_data_screens(self):
        self.data.correct('resource', 'insert', None, {'id': 'LASER-3', 'calendar_id': 'shop', 'capacity': 1},
                          'nuova macchina in arrivo', 'planner')
        self.assertIn('LASER-3', {r.id for r in self.state.source.resources})
        self.assertEqual('inserted', self.data.rows('resource', q='LASER-3')['rows'][0]['status'])
        self.data.correct('calendar_closure', 'exclude', ['shop', '2026-10-20T06:00:00Z', '2026-10-20T08:00:00Z'], {},
                          'fermo annullato', 'planner')
        excluded = self.data.rows('calendar_closure', status='excluded')
        self.assertEqual(1, excluded['total'])
        catalog = {e['name']: e for e in self.data.catalog()['entities']}
        self.assertEqual(0, catalog['calendar_closure']['records'])
        self.assertEqual(1, catalog['calendar_closure']['excluded'])

    def test_requests_are_checked_before_any_build(self):
        for args, message in ((('operation', 'update', ['ord-a/taglio'], {'id': 'x'}), 'part of the key'),
                              (('operation', 'update', ['ord-a/taglio'], {'duration_seconds': -1}), 'integer >= 0'),
                              (('operation', 'update', ['ord-a/taglio'], {'nope': 1}), 'unknown field'),
                              (('identity_map', 'insert', None, {}), 'cannot be corrected'),
                              (('order', 'insert', None, {'priority': 1}), 'missing field id')):
            with self.assertRaisesRegex(ValidationError, message):
                check(*args)
        with self.assertRaisesRegex(ValidationError, 'reason'):
            self.data.correct('order', 'update', ['A'], {'priority': 1}, '', 'planner')
        with self.assertRaisesRegex(ValidationError, 'no field changes'):
            self.data.correct('order', 'update', ['B'], {'priority': None}, 'niente', 'planner')

    def test_orphan_corrections_are_reported_not_hidden(self):
        store = CorrectionStore(self.store)
        store.add(store.candidate('order', 'update', ['ZZZ'], {'priority': 3}, 'commessa chiusa', 'planner'))
        state = DashboardState(self.binding)
        DataWorkspace(state, self.store)
        codes = {i.code for i in state.build_issues}
        self.assertIn('correction_orphan', codes)

    def test_sources_page_describes_files_domains_and_issues(self):
        sources = self.data.sources()
        self.assertEqual('demo', sources['installation'])
        files = sources['sources'][0]['files']
        self.assertTrue(files[0]['exists'])
        self.assertGreater(files[0]['bytes'], 0)
        self.assertIn('routings', {d['domain'] for d in sources['domains']})
        self.assertEqual(8, sources['sources'][0]['domains']['routings']['operation'])

    def test_rows_paginate_filter_and_sort_on_the_server(self):
        page = self.data.rows('operation_demand', limit=5, offset=0, sort='resource_id', descending=True)
        self.assertEqual(16, page['total'])
        self.assertEqual(5, len(page['rows']))
        ids = [r['values']['resource_id'] for r in page['rows']]
        self.assertEqual(sorted(ids, reverse=True), ids)
        self.assertEqual(16, self.data.rows('operation_demand', filters={'operation_id': 'ord'}, limit=50)['total'])
        with self.assertRaises(ValidationError):
            self.data.rows('operation', limit=1000)
        self.assertIn('LASER-1', self.data.ids('resource', 'laser'))

    def test_customer_column_sorts_by_company_name_not_code(self):
        bundle_path = Path(self.tmp.name, 'bundle.json')
        bundle = json.loads(bundle_path.read_text(encoding='utf-8'))
        codes = {'A': 'C1', 'B': 'C2', 'C': 'C0'}
        for order in bundle['records']['order']:
            order['customer_id'] = codes[order['id']]
        bundle_path.write_text(json.dumps(bundle), encoding='utf-8')
        Path(self.tmp.name, 'descrizioni.json').write_text(json.dumps({
            'schema': 'planning-factory/descrizioni-v1',
            'orders': {'A': {'customer_code': 'C1', 'customer': 'Zeta Srl'},
                       'B': {'customer_code': 'C2', 'customer': 'alfa spa'}}}), encoding='utf-8')
        data = DataWorkspace(DashboardState(self.binding), Path(self.tmp.name, 'other.sqlite'))
        ids = lambda **kw: [r['key'][0] for r in data.rows('order', sort='customer_id', **kw)['rows']]
        self.assertEqual(['B', 'A', 'C'], ids(), 'named customers by name, case-insensitive, then codes without a name')
        self.assertEqual(['C', 'A', 'B'], ids(descending=True))


class LocalControlRoomTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        binding = write_binding(folder)
        (folder / '.env').write_text('OPENROUTER_API_KEY=sk-or-test-secret-value\n', encoding='utf-8')
        self.server = make_secure_dashboard_server(binding, None, folder / 'store.sqlite', folder / 'audit.jsonl',
                                                   port=0, local_user='collaudo')
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(5); self.tmp.cleanup()

    def request(self, method, path, body=None, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=30)
        headers = {'Host': host or f'127.0.0.1:{self.server.server_port}'}
        raw = None
        if body is not None:
            raw = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, raw, headers)
        response = connection.getresponse()
        data = response.read()
        connection.close()
        return response.status, data

    def test_local_access_needs_no_token_and_never_leaves_loopback(self):
        status, page = self.request('GET', '/')
        self.assertEqual(200, status)
        self.assertNotIn(b'Token personale', page)
        status, data = self.request('GET', '/api/session')
        self.assertEqual({'name': 'collaudo', 'role': 'admin', 'local': True, 'data_screens': True}, json.loads(data))
        status, _ = self.request('GET', '/api/session', host='demo.app.github.dev')
        self.assertEqual(403, status)

    def test_material_detail_serves_needs_and_supplies_of_the_active_data(self):
        status, data = self.request('GET', '/api/materials')
        self.assertEqual(200, status, data)
        detail = json.loads(data)
        self.assertTrue(detail['available'])
        self.assertIsInstance(detail['needs'], list)
        self.assertIsInstance(detail['supplies'], list)
        status, _ = self.request('GET', '/api/materials', host='demo.app.github.dev')
        self.assertEqual(403, status)

    def test_corrections_through_the_api_are_audited(self):
        status, data = self.request('POST', '/api/data/corrections', {
            'entity': 'operation', 'action': 'update', 'key': ['ord-a/taglio'],
            'values': {'setup_seconds': 600}, 'reason': 'attrezzaggio misurato'})
        self.assertEqual(200, status, data)
        status, data = self.request('POST', '/api/data/corrections', {
            'entity': 'operation', 'action': 'update', 'key': ['ord-a/taglio'],
            'values': {'setup_seconds': -5}, 'reason': 'valore sbagliato'})
        self.assertEqual(422, status)
        self.assertIn('serve un numero intero non negativo', json.loads(data)['errors'][0])
        audit = Path(self.tmp.name, 'audit.jsonl').read_text(encoding='utf-8')
        self.assertIn('"data_correction"', audit)
        self.assertIn('"user": "collaudo"', audit)
        status, data = self.request('GET', '/api/data/rows?entity=operation&status=updated')
        self.assertEqual(1, json.loads(data)['total'])
        status, data = self.request('GET', '/api/sources')
        self.assertEqual(1, json.loads(data)['corrections']['active'])

    def test_ai_settings_show_key_presence_never_the_key(self):
        status, data = self.request('GET', '/api/settings/ai')
        self.assertEqual(200, status)
        self.assertNotIn(b'sk-or-test-secret-value', data)
        status_json = json.loads(data)
        self.assertTrue(status_json['keys']['openrouter']['present'])
        if not (os.environ.get('ANTHROPIC_API_KEY') or os.environ.get('PLANNING_CLAUDE_API_KEY')):
            self.assertFalse(status_json['keys']['anthropic']['present'])
        self.assertFalse(status_json['active'])
        status, data = self.request('POST', '/api/settings/ai', {'enabled': True})
        self.assertEqual(422, status)
        status, data = self.request('POST', '/api/settings/ai', {
            'enabled': True, 'provider': 'openrouter', 'quota_per_user': 5, 'quota_total': 20,
            'roles': {'assistant': {'provider': 'openrouter', 'model': 'anthropic/claude-sonnet-5.5'},
                      'rules': {'provider': None, 'model': None}, 'simulation': {'provider': None, 'model': None}}})
        self.assertEqual(200, status, data)
        saved = json.loads(data)
        self.assertTrue(saved['active'])
        self.assertTrue(all(r['ready'] for r in saved['roles'].values()))
        self.assertNotIn(b'sk-or-test-secret-value', data)
        self.assertEqual('openrouter', self.server.application.assistant.assistants['rules'].provider)

    def test_a_pasted_key_is_verified_then_written_by_the_server_and_never_echoed(self):
        key = 'sk-ant-api03-' + 'k' * 40
        with patch('planning_factory.adapters.ai_settings.verify_key', return_value=list(MODELS)) as verify:
            status, data = self.request('POST', '/api/settings/ai/connect', {'provider': 'anthropic', 'key': key})
        self.assertEqual(200, status, data)
        self.assertEqual(key, verify.call_args.args[1].key('anthropic'), 'the candidate key is verified before writing')
        self.assertNotIn(key.encode(), data)
        body = json.loads(data)
        self.assertTrue(body['keys']['anthropic']['present'])
        self.assertEqual('…kkkk', body['keys']['anthropic']['hint'])
        self.assertEqual('claude-sonnet-5-5', body['connected']['recommended'])
        env = parse_env_file(Path(self.tmp.name, '.env'))
        self.assertEqual(key, env['ANTHROPIC_API_KEY'])
        self.assertEqual('sk-or-test-secret-value', env['OPENROUTER_API_KEY'], 'other keys are kept')
        audit = Path(self.tmp.name, 'audit.jsonl').read_text(encoding='utf-8')
        self.assertIn('"ai_connect"', audit)
        self.assertNotIn(key, audit)
        status, data = self.request('POST', '/api/settings/ai/disconnect', {'provider': 'anthropic'})
        self.assertEqual(200, status, data)
        self.assertNotIn('ANTHROPIC_API_KEY', parse_env_file(Path(self.tmp.name, '.env')))

    def test_a_key_the_provider_refuses_or_a_malformed_one_is_never_written(self):
        before = Path(self.tmp.name, '.env').read_text(encoding='utf-8')
        status, data = self.request('POST', '/api/settings/ai/connect', {'provider': 'anthropic', 'key': 'chiave a caso'})
        self.assertEqual(422, status)
        self.assertIn('sk-ant-', json.loads(data)['error'])
        refused = ProviderError('Chiave Anthropic rifiutata')
        with patch('planning_factory.adapters.ai_settings.verify_key', side_effect=refused):
            status, data = self.request('POST', '/api/settings/ai/connect', {'provider': 'anthropic', 'key': 'sk-ant-' + 'x' * 30})
        self.assertEqual(502, status)
        self.assertEqual(before, Path(self.tmp.name, '.env').read_text(encoding='utf-8'))

    def test_openrouter_one_click_login_comes_back_cross_site_and_stores_the_key(self):
        status, data = self.request('POST', '/api/settings/ai/openrouter', {})
        self.assertEqual(200, status, data)
        url = urlsplit(json.loads(data)['url'])
        query = parse_qs(url.query)
        self.assertEqual('openrouter.ai', url.hostname)
        self.assertEqual(['S256'], query['code_challenge_method'])
        self.assertEqual([f'http://127.0.0.1:{self.server.server_port}/ai/openrouter/callback'], query['callback_url'])
        state = query['state'][0]
        new_key = 'sk-or-v1-' + 'n' * 64
        with patch('planning_factory.adapters.ai_settings.exchange_openrouter_code', return_value=new_key) as exchange, \
                patch('planning_factory.adapters.ai_settings.verify_key', return_value=list(MODELS)):
            status, headers = self.navigate(f'/ai/openrouter/callback?code=codice-di-prova&state={state}')
            self.assertEqual(303, status)
            self.assertEqual('/?ai=ok#impostazioni', headers['Location'])
            verifier = exchange.call_args.args[1]
            self.assertEqual(query['code_challenge'][0],
                             base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode())
            self.assertEqual(new_key, parse_env_file(Path(self.tmp.name, '.env'))['OPENROUTER_API_KEY'])
            status, headers = self.navigate(f'/ai/openrouter/callback?code=codice-di-prova&state={state}')
            self.assertEqual('/?ai=errore#impostazioni', headers['Location'], 'the state is single use')
        status, data = self.request('GET', '/api/settings/ai')
        self.assertIn('scaduto', json.loads(data)['last_connect']['message'])

    def navigate(self, path):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=30)
        connection.request('GET', path, headers={'Host': f'127.0.0.1:{self.server.server_port}', 'Sec-Fetch-Site': 'cross-site'})
        response = connection.getresponse()
        response.read()
        connection.close()
        return response.status, dict(response.getheaders())


MODELS = ({'id': 'claude-haiku-5-5', 'name': 'Claude Haiku 5.5', 'tools': True, 'created': '2026-05-01T00:00:00Z'},
          {'id': 'claude-sonnet-4-5', 'name': 'Claude Sonnet 4.5', 'tools': True, 'created': '2025-09-29T00:00:00Z'},
          {'id': 'claude-sonnet-5-5', 'name': 'Claude Sonnet 5.5', 'tools': True, 'created': '2026-05-01T00:00:00Z'},
          {'id': 'claude-opus-5-5', 'name': 'Claude Opus 5.5', 'tools': True, 'created': '2026-05-01T00:00:00Z'})


FAKE_CLI = '''import json, os, sys
marker = os.path.join(os.environ['CLAUDE_CONFIG_DIR'], 'logged-in')
args = sys.argv[1:]
if args[:2] == ['auth', 'status']:
    print(json.dumps({'loggedIn': os.path.exists(marker), 'authMethod': 'claude.ai', 'subscriptionType': 'max'}))
elif args[:2] == ['auth', 'login']:
    print('If the browser did not open, visit: https://claude.com/cai/oauth/authorize?code=true&state=s', flush=True)
    if sys.stdin.readline().strip() == 'codice-valido-123':
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, 'w').close()
    else:
        print('Invalid code'); sys.exit(1)
elif args[:2] == ['auth', 'logout'] and os.path.exists(marker):
    os.remove(marker)
'''


def fake_cli(folder):
    script = Path(folder, 'fake_claude.py')
    script.write_text(FAKE_CLI, encoding='utf-8')
    if os.name == 'nt':
        launcher = Path(folder, 'fakeclaude.cmd')
        launcher.write_text(f'@"{sys.executable}" "{script}" %*\n', encoding='utf-8')
    else:
        launcher = Path(folder, 'fakeclaude')
        launcher.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding='utf-8')
        launcher.chmod(0o755)
    return str(launcher)


class SubscriptionTests(unittest.TestCase):
    def test_guided_login_with_the_code_then_logout(self):
        with tempfile.TemporaryDirectory() as tmp:
            subscription = Subscription(Path(tmp, 'claude-abbonamento'), executable=fake_cli(tmp))
            self.assertFalse(subscription.status(fresh=True)['logged_in'])
            self.assertTrue(subscription.start_login().startswith('https://claude.com/cai/oauth/authorize'))
            self.assertTrue(subscription.status()['login_running'])
            with self.assertRaisesRegex(ProviderError, 'Codice non valido'):
                subscription.submit_code('x y')
            status = subscription.submit_code('codice-valido-123')
            self.assertTrue(status['logged_in'])
            self.assertEqual('max', status['subscription'])
            self.assertFalse(status['login_running'])
            subscription.logout()
            self.assertFalse(subscription.status(fresh=True)['logged_in'])
            self.assertFalse(Path(tmp, 'claude-abbonamento').exists())

    def test_a_wrong_code_is_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            subscription = Subscription(Path(tmp, 'claude-abbonamento'), executable=fake_cli(tmp))
            subscription.start_login()
            with self.assertRaisesRegex(ProviderError, 'Invalid code'):
                subscription.submit_code('codice-sbagliato-9')

    def test_the_subscription_serves_only_the_local_single_user(self):
        settings = {'enabled': True, 'provider': 'abbonamento', 'quota_per_user': 5, 'quota_total': 20,
                    'roles': {'assistant': {'provider': 'abbonamento', 'model': 'sonnet'},
                              'rules': {'provider': None, 'model': None}, 'simulation': {'provider': None, 'model': None}}}
        with tempfile.TemporaryDirectory() as tmp:
            shared = AIManager(Path(tmp, 'a.sqlite'), Path(tmp, '.env'), environ={})
            status = shared.save(settings, 'admin')
            self.assertFalse(status['active'])
            self.assertEqual('Solo con accesso locale', status['roles']['assistant']['problem'])
            self.assertNotIn('abbonamento', status['providers'])
            with self.assertRaisesRegex(ValidationError, 'accesso locale'):
                shared.models('abbonamento')
            subscription = Subscription(Path(tmp, 'claude-abbonamento'), executable=fake_cli(tmp))
            local = AIManager(Path(tmp, 'b.sqlite'), Path(tmp, '.env'), environ={}, subscription=subscription)
            status = local.save(settings, 'collaudo')
            self.assertTrue(status['active'])
            self.assertEqual('abbonamento', local.assistant.assistants['assistant'].provider)
            self.assertEqual('Accedi con l\'abbonamento', status['roles']['assistant']['problem'])
            self.assertEqual(['sonnet', 'opus', 'haiku'], [m['id'] for m in local.models('abbonamento')])
            reply = {'content': [{'type': 'text', 'text': 'OK'}], 'usage': {}}
            with patch.object(Subscription, 'transport', return_value=lambda payload: reply):
                self.assertEqual('OK', local.test('abbonamento', 'sonnet')['reply'])


class GuidedSetupTests(unittest.TestCase):
    def test_env_file_update_keeps_comments_and_other_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp, 'istanza', '.env')
            update_env_file(env, {'ANTHROPIC_API_KEY': 'sk-ant-uno'})
            self.assertTrue(env.read_text(encoding='utf-8').startswith('# Chiavi AI'))
            env.write_text(env.read_text(encoding='utf-8') + '# nota mia\nOPENROUTER_URL=https://gw.example/v1\n', encoding='utf-8')
            update_env_file(env, {'ANTHROPIC_API_KEY': 'sk-ant-due', 'ANTHROPIC_WORKSPACE_ID': None})
            text = env.read_text(encoding='utf-8')
            self.assertIn('# nota mia', text)
            self.assertEqual({'ANTHROPIC_API_KEY': 'sk-ant-due', 'OPENROUTER_URL': 'https://gw.example/v1'}, parse_env_file(env))
            update_env_file(env, {'ANTHROPIC_API_KEY': None})
            self.assertEqual({'OPENROUTER_URL': 'https://gw.example/v1'}, parse_env_file(env))

    def test_a_key_from_the_server_environment_is_not_managed_by_the_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = AIManager(Path(tmp, 's.sqlite'), Path(tmp, '.env'), environ={'ANTHROPIC_API_KEY': 'sk-ant-' + 'e' * 30})
            with self.assertRaisesRegex(ValidationError, 'variabile'):
                manager.connect('anthropic', 'sk-ant-' + 'p' * 30)
            self.assertFalse(Path(tmp, '.env').exists())

    def test_suggestion_is_the_newest_sonnet_with_tools(self):
        self.assertEqual('claude-sonnet-5-5', recommended('anthropic', list(MODELS)))
        routed = [{'id': 'openai/gpt-x', 'tools': True, 'created': 1800000000},
                  {'id': 'anthropic/claude-sonnet-5.5', 'tools': True, 'created': 1777000000},
                  {'id': 'anthropic/claude-sonnet-6', 'tools': False, 'created': 1790000000}]
        self.assertEqual('anthropic/claude-sonnet-5.5', recommended('openrouter', routed))
        self.assertIsNone(recommended('anthropic', []))


class ProviderTests(unittest.TestCase):
    def test_openai_translation_round_trips_tool_calls(self):
        payload = {'model': 'm', 'max_tokens': 50, 'system': 'sys',
                   'tools': [{'name': 'get_plan_summary', 'description': 'd', 'input_schema': {'type': 'object'}}],
                   'tool_choice': {'type': 'tool', 'name': 'get_plan_summary'},
                   'messages': [{'role': 'user', 'content': 'domanda'},
                                {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 't1', 'name': 'get_plan_summary', 'input': {}}]},
                                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 't1', 'content': '{"ok": 1}'}]}]}
        request = to_openai(payload)
        self.assertEqual(['system', 'user', 'assistant', 'tool'], [m['role'] for m in request['messages']])
        self.assertEqual({'type': 'function', 'function': {'name': 'get_plan_summary'}}, request['tool_choice'])
        self.assertEqual('t1', request['messages'][3]['tool_call_id'])
        response = from_openai({'choices': [{'message': {'content': None, 'tool_calls': [
            {'id': 'c9', 'type': 'function', 'function': {'name': 'suggest_rule', 'arguments': '{"supported": false}'}}]}}],
            'usage': {'prompt_tokens': 10, 'completion_tokens': 3}})
        self.assertEqual([{'type': 'tool_use', 'id': 'c9', 'name': 'suggest_rule', 'input': {'supported': False}}], response['content'])
        self.assertEqual({'input_tokens': 10, 'output_tokens': 3}, response['usage'])

    def test_provider_errors_are_explained_in_italian(self):
        self.assertIn('workspace', explain('anthropic', 400, 'anthropic-workspace-id is required'))
        self.assertIn('ANTHROPIC_API_KEY', explain('anthropic', 401, 'invalid x-api-key'))
        self.assertIn('Credito', explain('anthropic', 400, 'Your credit balance is too low'))
        self.assertIn('saturo', explain('openrouter', 429, ''))
        self.assertIn('non supporta gli strumenti', explain('openrouter', 404, 'No endpoints found that support tool use', 'x/y'))

    def test_gateway_url_custom_but_never_plain_http_off_this_pc(self):
        from planning_factory.adapters.ai_settings import Secrets
        from planning_factory.adapters.llm_providers import OPENROUTER_URL, ProviderError, openrouter_base
        self.assertEqual(OPENROUTER_URL, openrouter_base(Secrets(environ={})))
        self.assertEqual('http://127.0.0.1:4000/v1',
                         openrouter_base(Secrets(environ={'OPENROUTER_URL': 'http://127.0.0.1:4000/v1/'})))
        self.assertEqual('https://gw.example/v1', openrouter_base(Secrets(environ={'OPENROUTER_URL': 'https://gw.example/v1'})))
        for bad in ('http://192.168.1.5:4000/v1', 'ftp://x/v1', 'https://user:pw@gw.example/v1'):
            with self.assertRaises(ProviderError):
                openrouter_base(Secrets(environ={'OPENROUTER_URL': bad}))

    def test_env_file_and_manager_without_settings_keep_ai_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            env = Path(tmp, '.env')
            env.write_text('# chiavi\nexport ANTHROPIC_API_KEY="sk-ant-reale"\nOPENROUTER_API_KEY=sk-or-...\n', encoding='utf-8')
            self.assertEqual('sk-ant-reale', parse_env_file(env)['ANTHROPIC_API_KEY'])
            manager = AIManager(Path(tmp, 's.sqlite'), env, environ={})
            self.assertIsNone(manager.build())
            keys = manager.status()['keys']
            self.assertTrue(keys['anthropic']['present'])
            self.assertFalse(keys['openrouter']['present'], 'a placeholder ending in ... is not a key')


if __name__ == '__main__':
    unittest.main()
