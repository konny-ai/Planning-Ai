"""Assistant acting for the user: interface actions, verified plans, undoable persistent changes, roles."""
from dataclasses import replace
from datetime import date, timedelta
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

from planning_factory.adapters.assistant_actions import clean_ui
from planning_factory.adapters.claude_assistant import AssistantUnavailable, CallQuota, ClaudeAssistant
from planning_factory.adapters.json_io import dumps
from planning_factory.adapters.regeneration import Regeneration
from planning_factory.adapters.secure_dashboard import make_secure_dashboard_server
from planning_factory.adapters.service import new_token
from planning_factory.domain.validation import ValidationError
from .test_conversation_assistant import workshop


def tool(call_id, name, arguments):
    return {'type': 'tool_use', 'id': call_id, 'name': name, 'input': arguments}


class ActingAssistantTests(unittest.TestCase):
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
        self.app = self.server.application

    def request(self, method, path, body=None, cookie=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        headers = {'Cookie': cookie} if cookie else {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        connection.request(method, path, json.dumps(body) if body is not None else None, headers)
        response = connection.getresponse()
        status, raw = response.status, response.read()
        connection.close()
        return status, json.loads(raw) if raw else None

    def login(self, role):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        connection.request('POST', '/api/login', json.dumps({'token': self.tokens[role]}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        response.read()
        connection.close()
        return response.getheader('Set-Cookie').split(';')[0]

    def converse(self, role, script, ui=None):
        """Run one assistant turn whose model replies are ``script`` (a list of content lists)."""
        cookie = self.login(role)
        digest = self.request('GET', '/api/bootstrap', cookie=cookie)[1]['plan']['scenario_digest']
        requests = []

        def provider(payload):
            requests.append(json.loads(json.dumps(payload)))
            reply = script[min(len(requests), len(script)) - 1]
            if isinstance(reply, Exception):
                raise reply
            return {'content': reply}
        self.app.assistant = ClaudeAssistant('k', 'model', CallQuota(self.app.store.path, 50, 100), transport=provider)
        body = {'question': 'fai', 'scenario_digest': digest, 'ui': ui or {'view': 'machines'}}
        status, answer = self.request('POST', '/api/assistant', body, cookie)
        return status, answer, requests, digest, cookie

    def test_interface_actions_and_recalculation_drive_the_next_reads(self):
        status, answer, requests, digest, _ = self.converse('planner', [
            [tool('a', 'show_view', {'view': 'deliveries'}), tool('b', 'filter_view', {'delay_cause': 'capacity', 'text': 'o2'})],
            [tool('c', 'recalculate', {'settings': {'mode': 'backward'}})],
            [tool('d', 'get_plan_summary', {})],
            [{'type': 'text', 'text': 'Fatto: piano a ritroso.'}]])
        self.assertEqual(200, status, answer)
        self.assertEqual(['view', 'filter', 'plan'], [a['type'] for a in answer['actions']])
        self.assertEqual({'mode': 'backward'}, answer['actions'][2]['settings'])
        self.assertNotEqual(digest, answer['scenario_digest'])
        summary = json.loads(requests[3]['messages'][-1]['content'][0]['content'])
        self.assertEqual(answer['scenario_digest'], summary['scenario_digest'])
        self.assertFalse(answer['read_only'])
        # The browser loads the same plan: the server reuses it instead of computing it again.
        status, again = self.request('POST', '/api/recalculate', {'settings': {'mode': 'backward'}}, self.login('planner'))
        self.assertEqual((200, answer['scenario_digest']), (status, again['plan']['scenario_digest']))
        self.assertIn('assistant_recalculate', self.app.audit_path.read_text())

    def test_delivery_margin_is_a_parameter_of_the_assistant_and_of_the_screen(self):
        margin = {'delivery_buffer_seconds': 2 * 86400}
        status, answer, _, digest, cookie = self.converse('planner', [
            [tool('a', 'recalculate', {'settings': dict(margin, mode='backward')})],
            [{'type': 'text', 'text': 'Fatto: a ritroso con due giorni di margine.'}]])
        self.assertEqual(200, status, answer)
        self.assertEqual(dict(margin, mode='backward'), answer['actions'][-1]['settings'])
        self.assertNotEqual(digest, answer['scenario_digest'])
        # The screen sends the same settings and gets the plan the assistant calculated.
        status, screen = self.request('POST', '/api/recalculate', {'settings': dict(margin, mode='backward')}, cookie)
        self.assertEqual((200, answer['scenario_digest']), (status, screen['plan']['scenario_digest']))
        status, plain = self.request('POST', '/api/recalculate', {'settings': {'mode': 'backward'}}, cookie)
        due = {o['id']: o['due'] for o in screen['scenario']['operations'] if o.get('due')}
        base = {o['id']: o['due'] for o in plain['scenario']['operations'] if o.get('due')}
        self.assertTrue(due)
        self.assertTrue(all(date.fromisoformat(base[k][:10]) - date.fromisoformat(due[k][:10]) == timedelta(days=2)
                            for k in due))
        status, refused = self.request('POST', '/api/recalculate', {'settings': {'delivery_buffer_seconds': -1}}, cookie)
        self.assertEqual(422, status, refused)

    def test_simulation_keeps_the_interface_edits(self):
        closure = {'resource_id': 'm', 'start': '2026-10-19T08:00:00Z', 'end': '2026-10-19T10:00:00Z', 'reason': 'guasto'}
        status, answer, *_ = self.converse('planner', [
            [tool('a', 'simulate', {'closures': [closure]})], [{'type': 'text', 'text': 'Simulato.'}]])
        self.assertEqual(200, status, answer)
        plan = answer['actions'][-1]
        self.assertEqual(([closure], []), (plan['edits']['closures'], plan['edits']['fixed_tasks']))
        status, simulated = self.request('POST', '/api/simulate', {'settings': {}, 'edits': plan['edits']}, self.login('planner'))
        self.assertEqual((200, answer['scenario_digest']), (status, simulated['plan']['scenario_digest']))

    def test_confirmed_rule_is_undone_from_the_chat(self):
        status, answer, _, digest, cookie = self.converse('planner', [
            [tool('a', 'create_rule', {'rule': {'action': 'set_priority', 'value': 9, 'order_id': 'o1'}, 'confirm': True})],
            [{'type': 'text', 'text': 'Regola confermata.'}]])
        self.assertEqual(200, status, answer)
        self.assertEqual(['confirmed'], [r['status'] for r in self.app.rules.list()])
        self.assertEqual(1, len(answer['undo']))
        self.assertIn('plan', [a['type'] for a in answer['actions']])
        status, undone = self.request('POST', '/api/assistant/undo', {'token': answer['undo'][0]['token'],
                                                                      'ui': {'view': 'memory'}}, cookie)
        self.assertEqual(200, status, undone)
        self.assertEqual(['revoked'], [r['status'] for r in self.app.rules.list()])
        self.assertEqual(digest, undone['scenario_digest'])
        self.assertEqual(422, self.request('POST', '/api/assistant/undo', {'token': answer['undo'][0]['token']}, cookie)[0])
        audit = self.app.audit_path.read_text()
        self.assertIn('assistant_rule_confirm', audit)
        self.assertIn('assistant_undo', audit)

    def test_saved_changes_come_back_when_the_model_fails_afterwards(self):
        status, answer, *_ = self.converse('planner', [
            [tool('a', 'create_rule', {'rule': {'action': 'set_priority', 'value': 9, 'order_id': 'o1'}, 'confirm': True})],
            AssistantUnavailable('provider down')])
        self.assertEqual(200, status, answer)
        self.assertIn('provider down', answer['answer'])
        self.assertEqual(1, len(answer['undo']))
        self.assertIn('plan', [a['type'] for a in answer['actions']])
        self.assertEqual(503, self.converse('planner', [AssistantUnavailable('down')])[0])

    def test_undo_refused_in_an_archived_revision_can_be_retried(self):
        status, answer, _, _, cookie = self.converse('planner', [
            [tool('a', 'create_rule', {'rule': {'action': 'set_priority', 'value': 9, 'order_id': 'o1'}})],
            [{'type': 'text', 'text': 'Bozza.'}]])
        token = answer['undo'][0]['token']
        self.assertEqual(422, self.request('POST', '/api/assistant/undo', {'token': token, 'ui': {'archived': True}}, cookie)[0])
        self.assertEqual(200, self.request('POST', '/api/assistant/undo', {'token': token}, cookie)[0])
        self.assertEqual(['revoked'], [r['status'] for r in self.app.rules.list()])

    def test_only_scheduler_plans_are_reused(self):
        from unittest.mock import patch
        import planning_factory.adapters.dashboard_api as api
        state, scenario = self.app.state, replace(self.app.state.source, id='altro')
        state.remember_plan(scenario, api.plan(scenario))  # indexed for analyses, e.g. uploaded by a browser
        with patch.object(api, 'plan', wraps=api.plan) as scheduler:
            state._audited_plan(scenario)
            state._audited_plan(scenario)
        self.assertEqual(1, scheduler.call_count)

    def test_department_role_cannot_change_the_plan(self):
        status, answer, requests, digest, _ = self.converse('reparto', [
            [tool('a', 'recalculate', {'settings': {'mode': 'backward'}}), tool('b', 'show_view', {'view': 'orders'})],
            [{'type': 'text', 'text': 'Non posso.'}]])
        self.assertEqual(200, status, answer)
        names = {t['name'] for t in requests[0]['tools']}
        self.assertIn('show_view', names)
        self.assertIn('propose_simulation', names)
        self.assertFalse({'recalculate', 'create_rule', 'correct_data', 'regenerate_from_source', 'regeneration_status'} & names)
        self.assertEqual((['view'], digest), ([a['type'] for a in answer['actions']], answer['scenario_digest']))
        refused = requests[1]['messages'][-1]['content'][0]
        self.assertTrue(refused['is_error'])

    def test_archived_revision_stays_read_only(self):
        status, answer, requests, *_ = self.converse('planner', [
            [tool('a', 'reset_simulation', {})], [{'type': 'text', 'text': 'Sola lettura.'}]], ui={'archived': True})
        self.assertEqual(200, status, answer)
        self.assertNotIn('simulate', {t['name'] for t in requests[0]['tools']})
        self.assertEqual([], answer['actions'])

    def test_interface_state_is_checked(self):
        for bad in ({'view': 'nowhere'}, {'edits': {'closures': []}}, {'settings': {'sql': 1}}, {'x': 1}, []):
            with self.assertRaises(ValidationError):
                clean_ui(bad)


class ActingOnInputDataTests(unittest.TestCase):
    def test_correction_from_the_chat_is_undone_and_restored(self):
        from .test_control_room_data import write_binding
        with tempfile.TemporaryDirectory() as tmp:
            server = make_secure_dashboard_server(write_binding(tmp), None, Path(tmp, 'instance.sqlite'),
                                                  Path(tmp, 'audit.jsonl'), port=0, local_user='Dario')
            app = server.application
            user = ('Dario', 'admin')
            digest = app.state.bootstrap()['plan']['scenario_digest']
            insights = app.state.insights(digest)
            replies = [[tool('a', 'get_data_rows', {'entity': 'operation', 'text': 'ord-a/taglio'})],
                       [tool('b', 'correct_data', {'entity': 'operation', 'action': 'update', 'key': ['ord-a/taglio'],
                                                   'values': {'duration_seconds': 7200}, 'reason': 'tempo misurato'})],
                       [{'type': 'text', 'text': 'Corretto.'}]]
            requests = []

            def provider(payload):
                requests.append(json.loads(json.dumps(payload)))
                return {'content': replies[len(requests) - 1]}
            assistant = ClaudeAssistant('k', 'model', CallQuota(Path(tmp, 'quota.sqlite'), 50, 100), transport=provider)
            from planning_factory.adapters.assistant_actions import AssistantActions
            actions = AssistantActions(app, user, {'view': 'dati-ordini'}, insights)
            answer = assistant.answer('correggi', {'plan': json.loads(dumps(insights.plan)), 'meta': {}}, 'Dario',
                                      insights=insights, actions=actions)
            rows = json.loads(requests[1]['messages'][-1]['content'][0]['content'])['rows']
            self.assertEqual([['ord-a/taglio']], [r['key'] for r in rows])
            operation = lambda: next(o for o in app.state.source.operations if o.id == 'ord-a/taglio')  # noqa: E731
            self.assertEqual(7200, operation().duration_seconds)
            self.assertEqual(['data', 'plan'], [a['type'] for a in answer['actions']])
            self.assertNotEqual(digest, answer['scenario_digest'])
            undone = AssistantActions(app, user, {}).run_undo(answer['undo'][0]['token'])
            self.assertNotEqual(7200, operation().duration_seconds)
            self.assertEqual(digest, undone['scenario_digest'])
            correction = app.data.history()[0]['id']
            revoke = AssistantActions(app, user, {}, app.state.insights(digest))
            app.data.correct('operation', 'update', ['ord-a/taglio'], {'duration_seconds': 9000}, 'nuovo tempo', 'Dario')
            active = app.data.history()[0]['id']
            self.assertNotEqual(correction, active)
            revoke.call('revoke_corrections', {'ids': [active], 'reason': 'prova'})
            self.assertNotEqual(9000, operation().duration_seconds)
            AssistantActions(app, user, {}).run_undo(revoke.undo[0]['token'])
            self.assertEqual(9000, operation().duration_seconds)
            # A rebuild whose files cannot be read leaves the binding in use as it was.
            binding = app.state.binding
            app.data.reload = lambda: (_ for _ in ()).throw(ValidationError(['file illeggibile']))
            with self.assertRaises(ValidationError):
                app._after_regeneration()
            self.assertIs(binding, app.state.binding)
            server.server_close()


class RegenerationTests(unittest.TestCase):
    def test_runs_the_configured_command_once_with_the_date(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            done = []
            (folder / 'regenerate.json').write_text(json.dumps({
                'command': [sys.executable, '-c', 'import sys; print("start", sys.argv[1])', '{start}'], 'timeout_seconds': 60}))
            job = Regeneration.load(folder, lambda: done.append(True))
            with self.assertRaises(ValidationError):
                job.start('04/05/2026', 'admin')
            self.assertEqual('running', job.start('2026-04-30', 'admin')['state'])
            for _ in range(100):
                if job.status()['state'] != 'running':
                    break
                time.sleep(0.05)
            status = job.status()
            self.assertEqual(('done', [True]), (status['state'], done))
            self.assertIn('start 2026-04-30', status['log_tail'])
            self.assertIsNone(Regeneration.load(folder / 'missing', lambda: None))
            with self.assertRaises(ValueError):
                Regeneration(['cmd'], folder / 'x.log', lambda: None)


if __name__ == '__main__':
    unittest.main()
