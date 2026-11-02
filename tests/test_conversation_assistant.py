"""Conversational assistant v2: plan analyses, read tools with arguments, history, proposals, digest endpoints."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest

from planning_factory.adapters.claude_assistant import (MAX_PROPOSALS, CallQuota, ClaudeAssistant,
                                                        clean_history)
from planning_factory.adapters.dashboard_api import (MAX_INDEXED_PLANS, DashboardState, analysis,
                                                     evidence_manifest, make_dashboard_server)
from planning_factory.adapters.json_io import dumps
from planning_factory.adapters.plan_insights import PlanInsights
from planning_factory.adapters.planning_tools import PlanningTools
from planning_factory.adapters.secure_dashboard import checked_proposals, make_secure_dashboard_server
from planning_factory.adapters.service import new_token
from planning_factory.domain.models import Dependency, Resource
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from .helpers import dt, op, scenario


def workshop():
    # o1: a (2 h) then b (1 h) on m; o2: c (3 h) on m, lower priority, waits for the machine.
    return scenario((op('a', 2, order_id='o1'), op('b', 1, order_id='o1', predecessors=(Dependency('a'),)),
                     op('c', 3, order_id='o2', priority=5, due=dt(19, 9))),
                    resources=(Resource('m', 'work'), Resource('free', 'work', capacity=None, kind='tool')))


class PlanInsightsTests(unittest.TestCase):
    def setUp(self):
        self.s = workshop()
        self.p = plan(self.s)
        self.insights = PlanInsights(self.s, self.p)

    def test_explain_order_names_the_occupant_of_the_machine(self):
        result = self.insights.explain_order('o2')
        self.assertTrue(result['late'])
        first = result['critical_path'][0]
        self.assertEqual(('job:c', 'resource', 'm'), (first['unit'], first['cause'], first['resource']))
        self.assertIn('occupata da job:b', first['text'])
        self.assertEqual(self.p.scenario_digest, result['scenario_digest'])
        self.assertIn('In ritardo', result['summary'])
        precedence = self.insights.explain_order('o1')['critical_path']
        self.assertEqual(['job:b', 'job:a'], [x['unit'] for x in precedence])
        self.assertEqual('predecessor', precedence[0]['cause'])

    def test_explain_order_reports_the_backward_fallback(self):
        s = scenario([op('x', order_id='ox', due=dt(19, 7), mode='backward')])
        result = PlanInsights(s, plan(s)).explain_order('ox')
        first = result['critical_path'][0]
        self.assertTrue(first['backward_fallback'])
        self.assertIn('ripiegato', first['text'])
        self.assertIn('ha ripiegato dal vincolo a ritroso', result['summary'])
        self.assertNotIn('fasi a monte', result['summary'])
        self.assertNotIn('backward_fallback', self.insights.explain_order('o2')['critical_path'][0])

    def test_delays_take_the_cause_from_the_critical_path(self):
        result = self.insights.delays()
        row = next(r for r in result['rows'] if r['order_id'] == 'o2')
        self.assertEqual(('late', 'capacity', 'm', 'job:b'), (row['status'], row['cause'], row['resource'], row['blocker']))
        self.assertGreater(row['wait_seconds'], 0)
        self.assertEqual(result['counts'].get('capacity'), 1)
        self.assertNotIn('o1', [r['order_id'] for r in result['rows']])
        with self.assertRaises(ValidationError):
            self.insights.delays(-1)

    def test_daily_saturation_uses_engine_calendar(self):
        loads = self.insights.daily_loads('2026-10-19', 2, 'all')
        row = next(r for r in loads['rows'] if r['resource_id'] == 'm')
        self.assertEqual([6.0, 0.0], row['booked_hours'])
        self.assertEqual([8.0, 8.0], row['available_hours'])
        self.assertEqual([75.0, 0.0], row['saturation_percent'])
        infinite = next(r for r in loads['rows'] if r['resource_id'] == 'free')
        self.assertIsNone(infinite['available_hours'])
        self.assertIsNone(infinite['saturation_percent'])
        self.assertEqual(['m'], [r['resource_id'] for r in self.insights.daily_loads('2026-10-19', 1)['rows']])

    def test_order_resource_search_nesting_and_quality(self):
        order = self.insights.order_detail('o1')
        self.assertEqual(['job:a', 'job:b'], [t['task_id'] for t in order['tasks']])
        resource = self.insights.resource_detail('m', '2026-10-19', 1)
        self.assertEqual(3, resource['total_bookings'])
        self.assertEqual(75.0, resource['daily'][0]['saturation_percent'])
        self.assertEqual(['o2'], self.insights.search(' o2 ')['orders']['ids'])
        self.assertEqual(0, self.insights.nesting_detail()['total'])
        self.assertIn('service_level_percent', self.insights.quality()['kpi']['orders'])

    def test_bookings_carry_material_and_thickness_only_when_the_source_gives_them(self):
        bare = self.insights.resource_detail('m', '2026-10-19', 1)['bookings']
        self.assertTrue(all('material' not in b and 'thickness_mm' not in b for b in bare))
        attributes = {'operations': {'a': {'material': 'S355', 'thickness_mm': '15'},
                                     'b': {'material': 'S355', 'thickness_mm': '8'}}}
        rows = {b['task_id']: b for b in PlanInsights(self.s, self.p, attributes).resource_detail('m', '2026-10-19', 1)['bookings']}
        self.assertEqual(('S355', '15'), (rows['job:a']['material'], rows['job:a']['thickness_mm']))
        self.assertEqual('8', rows['job:b']['thickness_mm'])
        self.assertNotIn('thickness_mm', rows['job:c'])

    def test_invalid_requests_fail_closed(self):
        for call in (lambda: self.insights.explain_order('missing'), lambda: self.insights.order_detail(''),
                     lambda: self.insights.resource_detail('nope'), lambda: self.insights.daily_loads(days=63),
                     lambda: self.insights.daily_loads('19/10/2026'), lambda: self.insights.daily_loads(kind='x'),
                     lambda: self.insights.search('x'), lambda: self.insights.nesting_detail('n')):
            with self.assertRaises(ValidationError):
                call()

    def test_analyses_never_change_the_plan(self):
        before = dumps(self.p)
        self.insights.explain_order('o2'), self.insights.daily_loads(), self.insights.quality()
        self.assertEqual(before, dumps(self.p))


class ReadToolArgumentTests(unittest.TestCase):
    def setUp(self):
        s = workshop()
        self.snapshot = {'scenario': json.loads(dumps(s)), 'plan': json.loads(dumps(plan(s))), 'meta': {}}

    def test_tools_with_arguments_from_snapshot(self):
        tools = PlanningTools(self.snapshot)
        self.assertEqual('job:c', tools.call('explain_order', {'order_id': 'o2'})['critical_path'][0]['unit'])
        self.assertEqual(2, len(tools.call('get_order', {'order_id': 'o1'})['tasks']))
        self.assertEqual(1, len(tools.call('get_resource', {'resource_id': 'm', 'start': '2026-10-19', 'days': 1})['daily']))
        self.assertEqual(['free'], tools.call('search_plan', {'text': 'fr'})['resources']['ids'])

    def test_arguments_are_strictly_checked(self):
        tools = PlanningTools(self.snapshot)
        for name, args in (('explain_order', {}), ('explain_order', {'order_id': 'o1', 'sql': 'x'}),
                           ('explain_order', {'order_id': 7}), ('get_resource', {'resource_id': 'm', 'days': 0}),
                           ('get_resource', {'resource_id': 'm', 'days': True}), ('search_plan', {'text': 'x' * 121}),
                           ('get_plan_summary', {'order_id': 'o1'}), ('get_order', None)):
            with self.assertRaises(ValidationError):
                tools.call(name, args)

    def test_scenario_tools_degrade_without_scenario(self):
        tools = PlanningTools({'plan': self.snapshot['plan'], 'meta': {}})
        self.assertFalse(tools.call('explain_order', {'order_id': 'o1'})['available'])
        self.assertEqual(3, tools.call('get_plan_summary', {})['assignments'])

    TEXTS = {'o1': {'code': 'C-1', 'customer_code': 'K1', 'customer': 'Acme Srl'},
             'o2': {'code': 'C-2', 'customer_code': 'K2', 'customer': 'Beta SpA'}}

    def test_orders_carry_code_and_customer(self):
        tools = PlanningTools({**self.snapshot, 'order_texts': self.TEXTS})
        row = tools.call('get_deliveries', {})['rows'][0]
        self.assertEqual(('o2', 'C-2', 'K2', 'Beta SpA'), (row['order_id'], row['order_code'], row['customer_code'], row['customer']))
        self.assertEqual('Beta SpA', tools.call('explain_order', {'order_id': 'o2'})['customer'])
        self.assertEqual('C-1', tools.call('get_order', {'order_id': 'o1'})['order_code'])
        found = tools.call('search_plan', {'text': 'beta'})
        self.assertEqual([{'customer_code': 'K2', 'customer': 'Beta SpA', 'order_ids': ['o2']}], found['customers']['rows'])
        self.assertEqual(['o1'], tools.call('search_plan', {'text': 'c-1'})['orders']['ids'])
        # Texts of orders outside this plan are never reported.
        stale = PlanningTools({**self.snapshot, 'order_texts': {**self.TEXTS, 'o9': {'code': 'C-9', 'customer_code': 'K2',
                                                                                   'customer': 'Beta SpA'}}})
        self.assertEqual([['o2']], [r['order_ids'] for r in stale.call('search_plan', {'text': 'beta'})['customers']['rows']])
        self.assertEqual(0, stale.call('search_plan', {'text': 'c-9'})['orders']['total'])

    def test_search_counts_every_matching_order(self):
        many = scenario(tuple(op(f'x{i}', 1, order_id=f'ord{i:02d}') for i in range(35)), resources=(Resource('m', 'work'),))
        snapshot = {'scenario': json.loads(dumps(many)), 'plan': json.loads(dumps(plan(many))), 'meta': {}}
        found = PlanningTools(snapshot).call('search_plan', {'text': 'ord'})['orders']
        self.assertEqual((35, 30), (found['total'], len(found['ids'])))

    def test_customers_are_grouped_by_tardiness(self):
        result = PlanningTools({**self.snapshot, 'order_texts': self.TEXTS}).call('get_customers', {})
        self.assertEqual(['K2', 'K1'], [r['customer_code'] for r in result['rows']])
        worst = result['rows'][0]
        self.assertEqual(('Beta SpA', 1, 1, 0, 0, 'o2', 'C-2'),
                         (worst['customer'], worst['orders'], worst['late'], worst['incomplete'],
                          worst['late_due_before_start'], worst['worst_order_id'], worst['worst_order_code']))
        self.assertGreater(worst['tardiness_seconds'], 0)
        self.assertEqual(0, result['rows'][1]['tardiness_seconds'])
        self.assertFalse(PlanningTools(self.snapshot).call('get_customers', {})['available'])

    def test_insights_of_another_plan_are_refused(self):
        other = scenario((op('z'),))
        tools = PlanningTools(self.snapshot, PlanInsights(other, plan(other)))
        with self.assertRaises(ValidationError):
            tools.call('get_plan_quality', {})


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        s = workshop()
        self.s, self.p = s, plan(s)
        self.snapshot = {'plan': json.loads(dumps(self.p)), 'meta': {}}
        self.insights = PlanInsights(s, self.p)
        self.quota = CallQuota(Path(self.tmp.name) / 'quota.db', 20, 20)

    def test_history_is_sent_and_tools_with_arguments_are_executed(self):
        requests = []
        def provider(payload):
            requests.append(json.loads(json.dumps(payload)))
            if len(requests) == 1:
                return {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'explain_order', 'input': {'order_id': 'o2'}}]}
            return {'content': [{'type': 'text', 'text': 'o2 attende m, occupata da job:b.'}]}
        history = [{'role': 'user', 'text': 'Ciao'}, {'role': 'assistant', 'text': 'Come posso aiutarti?'}]
        result = ClaudeAssistant('k', 'model', self.quota, transport=provider).answer(
            'Perché o2 è in ritardo?', self.snapshot, 'planner', history=history, insights=self.insights)
        self.assertEqual(['explain_order'], result['tools_used'])
        self.assertEqual([], result['proposals'])
        self.assertEqual(['user', 'assistant', 'user'], [m['role'] for m in requests[0]['messages']])
        self.assertEqual('Ciao', requests[0]['messages'][0]['content'])
        tool_result = json.loads(requests[1]['messages'][-1]['content'][0]['content'])
        self.assertEqual('job:c', tool_result['critical_path'][0]['unit'])
        self.assertIn('propose_simulation', [t['name'] for t in requests[0]['tools']])

    def test_proposals_are_collected_never_applied_and_bounded(self):
        edit = {'fixed_tasks': [], 'closures': [{'resource_id': 'm', 'start': '2026-10-19T08:00:00Z',
                                                 'end': '2026-10-19T10:00:00Z', 'reason': 'manutenzione'}]}
        calls = []
        def provider(payload):
            calls.append(payload)
            if len(calls) == 1:
                return {'content': [{'type': 'tool_use', 'id': f'p{i}', 'name': 'propose_simulation',
                                     'input': {'title': f'Fermo {i}', 'edits': edit}} for i in range(MAX_PROPOSALS + 1)]}
            self.assertTrue(payload['messages'][-1]['content'][-1]['is_error'])
            return {'content': []}
        before = dumps(self.p)
        result = ClaudeAssistant('k', 'model', self.quota, transport=provider).answer(
            'Simula un fermo', self.snapshot, 'planner', insights=self.insights)
        self.assertEqual(MAX_PROPOSALS, len(result['proposals']))
        self.assertEqual('Ho preparato le proposte qui sotto.', result['answer'])
        self.assertEqual(before, dumps(self.p))
        checked = checked_proposals(self.s, result['proposals'] + [{'title': 'x', 'edits': {
            'fixed_tasks': [{'task_id': 'job:nope', 'start': '2026-10-19T08:00:00Z', 'alternative': 0}], 'closures': []}}])
        self.assertTrue(all(c['valid'] for c in checked[:MAX_PROPOSALS]))
        self.assertFalse(checked[-1]['valid'])
        self.assertIn('unknown or duplicate fixed task', checked[-1]['error'])

    def test_invalid_tool_arguments_are_explained_to_the_model(self):
        calls = []
        def provider(payload):
            calls.append(payload)
            if len(calls) == 1:
                return {'content': [{'type': 'tool_use', 'id': 'x', 'name': 'get_order', 'input': {'order_id': 'zzz'}}]}
            self.assertIn('order not found', payload['messages'][-1]['content'][0]['content'])
            return {'content': [{'type': 'text', 'text': 'La commessa non esiste.'}]}
        ClaudeAssistant('k', 'model', self.quota, transport=provider).answer('zzz?', self.snapshot, 'p', insights=self.insights)

    def test_history_validation_and_alternation(self):
        self.assertEqual([], clean_history([]))
        self.assertEqual([{'role': 'user', 'content': 'a\nb'}, {'role': 'assistant', 'content': '(nessuna risposta)'}],
                         clean_history([{'role': 'user', 'text': 'a'}, {'role': 'user', 'text': 'b'}]))
        self.assertEqual([], clean_history([{'role': 'assistant', 'text': 'benvenuto'}]))
        for bad in ([{'role': 'system', 'text': 'x'}], [{'role': 'user', 'text': ''}],
                    [{'role': 'user', 'text': 'x', 'extra': 1}], [{'role': 'user', 'text': 'x' * 4001}],
                    [{'role': 'user', 'text': 'x'}] * 21, 'text'):
            with self.assertRaises(ValidationError):
                clean_history(bad)


class DigestEndpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'scenario.json'
        self.path.write_text(dumps(workshop()))

    def test_descriptions_are_display_only_and_optional(self):
        state = DashboardState(self.path)
        self.assertIsNone(state.descriptions())
        sidecar = self.path.with_name('descrizioni.json')
        sidecar.write_text(json.dumps({'schema': 'other', 'resources': {}}))
        self.assertIsNone(state.descriptions())
        sidecar.write_text(json.dumps({'schema': 'planning-factory/descrizioni-v1', 'resources': {'m': {'name': 'Laser'}},
                                       'orders': {'o2': {'code': 'C26-1', 'customer': 'Cliente'}}, 'extra': 1}))
        texts = state.descriptions()
        self.assertEqual('Laser', texts['resources']['m']['name'])
        self.assertNotIn('extra', texts)
        before = state.bootstrap()['plan']['scenario_digest']
        sidecar.write_text('{not json')
        self.assertIsNone(state.descriptions())
        self.assertEqual(before, DashboardState(self.path).bootstrap()['plan']['scenario_digest'])

    def test_order_texts_name_the_customer_by_code(self):
        state = DashboardState(self.path)
        self.assertEqual({}, state.order_texts())
        self.path.with_name('descrizioni.json').write_text(json.dumps({
            'schema': 'planning-factory/descrizioni-v1',
            'orders': {'o1': {'code': 'C-1', 'customer_code': 'K1', 'customer': 'Acme'}, 'o2': {'code': 'C-2'}}}))
        self.assertEqual({'o1': {'code': 'C-1', 'customer_code': 'K1', 'customer': 'Acme'}, 'o2': {'code': 'C-2'}},
                         state.order_texts())
        # A canonical customer_id wins over the texts (e.g. after a correction); the name follows the code.
        state.manifest = {'rule_scope': {'orders': [{'id': 'o1'}, {'id': 'o2', 'customer_id': 'K1'}]}}
        self.assertEqual({'o1': {'code': 'C-1'}, 'o2': {'code': 'C-2', 'customer_code': 'K1', 'customer': 'Acme'}},
                         state.order_texts())
        # An archived plan reads the manifest recorded with it; without one, no customer from today's data.
        archived = {'rule_scope': {'orders': [{'id': 'o1', 'customer_id': 'K1'}, {'id': 'o2'}]}}
        self.assertEqual('Acme', state.order_texts(archived)['o1']['customer'])
        self.assertEqual({'o1': {'code': 'C-1'}, 'o2': {'code': 'C-2'}}, state.order_texts(False))
        self.assertIs(archived, evidence_manifest({'source': 'archived_whatif', 'parent': {'evidence': {
            'source_manifest': archived}}}))
        self.assertIsNone(evidence_manifest({'source_manifest': None}))

    def test_index_holds_computed_plans_and_evicts_oldest(self):
        state = DashboardState(self.path)
        digest = state.bootstrap()['plan']['scenario_digest']
        self.assertIsNotNone(state.insights(digest))
        self.assertEqual(200, analysis(state, '/api/explain', {'scenario_digest': digest, 'order_id': 'o2'})[0])
        code, delays = analysis(state, '/api/delays', {'scenario_digest': digest})
        self.assertEqual((200, 'capacity'), (code, delays['rows'][0]['cause']))
        for weight in range(1, MAX_INDEXED_PLANS + 1):
            state.bootstrap({'due_weight': weight + 1})
        self.assertIsNone(state.insights(digest))
        code, body = analysis(state, '/api/explain', {'scenario_digest': digest, 'order_id': 'o2'})
        self.assertEqual((409, 'plan_not_indexed'), (code, body['code']))
        with self.assertRaises(ValidationError):
            analysis(state, '/api/explain', {'scenario_digest': digest})
        with self.assertRaises(ValidationError):
            analysis(state, '/api/loads', {'scenario_digest': digest, 'scenario': {}})

    def test_simulation_is_indexed_for_analysis(self):
        state = DashboardState(self.path)
        result = state.simulate({'fixed_tasks': [], 'closures': [{'resource_id': 'm', 'start': '2026-10-19T08:00:00Z',
                                                                 'end': '2026-10-19T12:00:00Z', 'reason': 'fermo'}]})
        code, loads = analysis(state, '/api/loads', {'scenario_digest': result['plan']['scenario_digest'],
                                                     'start': '2026-10-19', 'days': 1})
        self.assertEqual(200, code)
        self.assertEqual([4.0], loads['rows'][0]['available_hours'])

    def test_http_routes_on_the_local_dashboard(self):
        server = make_dashboard_server(self.path, port=0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(5)))
        def post(path, body):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            connection.request('POST', path, json.dumps(body), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            value = (response.status, json.loads(response.read()))
            connection.close()
            return value
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
        connection.request('GET', '/api/bootstrap')
        digest = json.loads(connection.getresponse().read())['plan']['scenario_digest']
        connection.close()
        status, body = post('/api/explain', {'scenario_digest': digest, 'order_id': 'o2'})
        self.assertEqual((200, 'job:c'), (status, body['critical_path'][0]['unit']))
        status, body = post('/api/loads', {'scenario_digest': digest, 'start': '2026-10-19', 'days': 2})
        self.assertEqual((200, 2), (status, len(body['days'])))
        status, body = post('/api/quality', {'scenario_digest': digest})
        self.assertEqual((200, 2), (status, body['kpi']['orders']['total']))
        self.assertEqual(409, post('/api/explain', {'scenario_digest': '0' * 64, 'order_id': 'o2'})[0])
        self.assertEqual(422, post('/api/explain', {'scenario_digest': digest, 'order_id': 'missing'})[0])


class SecureConversationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.tokens, users = {}, []
        for role in ('planner', 'reparto'):
            token, entry = new_token(role + '-user', role)
            self.tokens[role] = token
            users.append(entry)
        (folder / 'scenario.json').write_text(dumps(workshop()))
        (folder / 'users.json').write_text(json.dumps({'users': users}))
        self.server = make_secure_dashboard_server(folder / 'scenario.json', folder / 'users.json',
                                                    folder / 'snapshots.db', folder / 'audit.jsonl', port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close(), self.thread.join(5), self.tmp.cleanup()))

    def request(self, method, path, body=None, cookie=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        headers = {'Cookie': cookie} if cookie else {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        value = (response.status, dict(response.getheaders()), response.read())
        connection.close()
        return value

    def login(self, role):
        status, headers, _ = self.request('POST', '/api/login', {'token': self.tokens[role]})
        self.assertEqual(200, status)
        return headers['Set-Cookie'].split(';')[0]

    def test_reparto_converses_by_digest_and_receives_checked_proposals(self):
        cookie = self.login('reparto')
        boot = json.loads(self.request('GET', '/api/bootstrap', cookie=cookie)[2])
        digest = boot['plan']['scenario_digest']
        status, _, raw = self.request('POST', '/api/explain', {'scenario_digest': digest, 'order_id': 'o2'}, cookie)
        self.assertEqual(200, status, raw)
        requests = []
        def provider(payload):
            requests.append(json.loads(json.dumps(payload)))
            if len(requests) == 1:
                return {'content': [
                    {'type': 'tool_use', 'id': 'a', 'name': 'propose_simulation', 'input': {'title': 'Fermo m', 'edits': {
                        'fixed_tasks': [], 'closures': [{'resource_id': 'm', 'start': '2026-10-19T08:00:00Z',
                                                         'end': '2026-10-19T09:00:00Z', 'reason': 'manutenzione'}]}}},
                    {'type': 'tool_use', 'id': 'b', 'name': 'propose_simulation', 'input': {'title': 'Fuori orizzonte', 'edits': {
                        'fixed_tasks': [], 'closures': [{'resource_id': 'm', 'start': '2030-01-01T08:00:00Z',
                                                         'end': '2030-01-01T09:00:00Z', 'reason': 'x'}]}}}]}
            return {'content': [{'type': 'text', 'text': 'Due proposte.'}]}
        app = self.server.application
        app.assistant = ClaudeAssistant('k', 'model', CallQuota(app.store.path, 10, 20), transport=provider)
        body = {'question': 'Simula un fermo di m', 'scenario_digest': digest,
                'history': [{'role': 'user', 'text': 'Ciao'}, {'role': 'assistant', 'text': 'Ciao!'}]}
        status, _, raw = self.request('POST', '/api/assistant', body, cookie)
        self.assertEqual(200, status, raw)
        answer = json.loads(raw)
        self.assertEqual([True, False], [p['valid'] for p in answer['proposals']])
        self.assertEqual(3, len(requests[0]['messages']))
        audit = app.audit_path.read_text()
        self.assertIn('"proposals": 2', audit)
        self.assertEqual(409, self.request('POST', '/api/assistant', {'question': 'x', 'scenario_digest': 'f' * 64}, cookie)[0])
        self.assertEqual(422, self.request('POST', '/api/assistant', {'question': 'x', 'scenario_digest': digest,
                                                                      'scenario': {}}, cookie)[0])
        self.assertEqual(422, self.request('POST', '/api/assistant', {'question': 'x', 'scenario_digest': digest,
                                                                      'history': [{'role': 'system', 'text': 'x'}]}, cookie)[0])

    def test_analysis_routes_need_a_session(self):
        self.assertEqual(401, self.request('POST', '/api/explain', {'scenario_digest': 'x', 'order_id': 'o'})[0])


if __name__ == '__main__':
    unittest.main()
