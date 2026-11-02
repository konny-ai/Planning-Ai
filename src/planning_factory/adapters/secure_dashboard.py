"""Optional authenticated loopback dashboard with atomic snapshot persistence.

No production hosting claim: TLS/reverse proxy, browser QA and industrial
acceptance remain separate. Existing file-only demo stays unchanged.
"""
from datetime import UTC, datetime
from dataclasses import replace
import hashlib
import hmac
from http.cookies import CookieError, SimpleCookie
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from planning_factory.adapters.dashboard_api import (DashboardState, LocalDashboardServer, make_handler, _settings_override,
                                                     PlannerBusyError, evidence_manifest)
from planning_factory.adapters.json_io import _no_duplicates, dumps, plan_loads, primitive, scenario_digest, scenario_from_dict
from planning_factory.adapters.service import ROLES, load_credentials
from planning_factory.adapters.store import ConflictError, SnapshotStore
from planning_factory.adapters.ai_settings import AIManager
from planning_factory.adapters.assistant_actions import AssistantActions, UndoRegistry
from planning_factory.adapters.regeneration import Regeneration
from planning_factory.adapters.claude_assistant import AssistantUnavailable
from planning_factory.adapters.claude_subscription import Subscription
from planning_factory.adapters.data_workspace import DataWorkspace, italian
from planning_factory.adapters.llm_providers import ProviderError
from planning_factory.adapters.rule_memory import RuleMemory
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.verification import verify_plan

COOKIE = 'planning_session'
MAX_BODY = 8 * 1024 * 1024
OPENROUTER_CALLBACK = '/ai/openrouter/callback'
LOGIN_HTML = b'''<!doctype html><html lang="it"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Accesso Planning Factory</title>
<link rel="stylesheet" href="/styles.css"><main style="max-width:480px;margin:10vh auto;padding:24px">
<h1>Accedi al pianificatore</h1><p>Usa il token personale fornito dall'amministratore.</p>
<form id="login-form"><label for="token">Token personale</label>
<input id="token" name="token" type="password" required autocomplete="off">
<button type="submit">Accedi</button><p id="login-error" role="alert"></p></form></main>
<script src="/login.js"></script></html>'''
LOGIN_JS = b'''document.querySelector('#login-form').addEventListener('submit',async event=>{
event.preventDefault();const field=document.querySelector('#token');
try{const response=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({token:field.value})});if(!response.ok)throw Error('Accesso non riuscito');location.assign('/');}
catch(error){document.querySelector('#login-error').textContent=error.message;}finally{field.value='';}});'''


class SecureDashboard:
    def __init__(self, state, credentials, store, audit, *, secure_cookie=False, ttl_seconds=28800,
                 local_user=None, env_file=None):
        self.state = state
        # Local single-user access: one person on this PC, loopback only, no token. Every
        # action is still recorded with that user's name in the revisions and the audit log.
        self.local_user = (local_user, 'admin') if local_user else None
        if self.local_user is None and credentials is None:
            raise ValidationError(['credentials required unless local single-user access is requested'])
        self.credentials = load_credentials(credentials) if credentials is not None else {}
        self.store = SnapshotStore(store)
        self.audit_path = Path(audit)
        self.secure_cookie = secure_cookie
        self.ttl_seconds = ttl_seconds
        # The personal Claude subscription is offered only to the single local user, on their own PC.
        subscription = Subscription(Path(store).parent / 'claude-abbonamento') if self.local_user else None
        self.ai = AIManager(store, env_file if env_file is not None else Path(store).parent / '.env',
                            subscription=subscription)
        self.assistant = self.ai.build()
        if state.workbook_input is not None:
            from planning_factory.adapters.workbook_workspace import WorkbookMemory, WorkbookWorkspace
            self.rules = WorkbookMemory(state)
            self.data = WorkbookWorkspace(state)
        else:
            self.rules = RuleMemory(store, source=state.source,
                                    scope_context=(state.manifest or {}).get('rule_scope'), material_free=state.material_free)
            self.data = DataWorkspace(state, store) if state.binding is not None else None
        self.state.company_memory = self.rules
        self.undo = UndoRegistry()
        # Rebuild from the ERP with another start date: only where the administrator configured the command.
        self.regeneration = Regeneration.load(Path(store).parent, self._after_regeneration) if state.binding is not None else None
        self.factory_sync=None
        if state.workbook_input is not None and (Path(store).parent/'factory-source.json').is_file():
            from planning_factory.adapters.factory_workbook_sync import FactoryWorkbookSync
            self.factory_sync=FactoryWorkbookSync(Path(store).parent,state,self._after_factory_sync)
        self.sessions = {}
        self.evidence = {}
        self.lock = threading.Lock()

    def _after_regeneration(self):
        previous = self.state.binding
        try:
            self.state.reread_binding()
            self.data.reload()
        except Exception:
            # A rebuild that cannot be read leaves the live source as it was, binding included.
            self.state.binding = previous
            raise
        with self.state._lock:
            self.state._default = None
            self.state._stage_cache = None

    def _after_factory_sync(self):
        self.data._rows.clear()
        self.data._filled=(None,{})

    def save_snapshot(self, source, result, *, expected_revision, evidence=None, actor=None):
        content = None
        if isinstance(evidence,dict) and evidence.get('workbook'):
            content = self.state.path.read_bytes()
            if hashlib.sha256(content).hexdigest() != evidence['workbook'].get('workbook_sha256'):
                raise ValidationError(['Excel cambiato: rileggi la fonte e ricalcola prima di salvare la revisione'])
        return self.store.save_snapshot(source,result,expected_revision=expected_revision,
            evidence=evidence,actor=actor,workbook_bytes=content)

    def remember_evidence(self, digest, evidence):
        # Deep copy so later memory/source mutations cannot rewrite history.
        record = json.loads(json.dumps(evidence, allow_nan=False))
        key = secrets.token_urlsafe(24)
        with self.lock:
            self.evidence[key] = (digest, record)
            while len(self.evidence) > 128:
                self.evidence.pop(next(iter(self.evidence)))
        return key

    def get_evidence(self, key, digest):
        if key is None:
            return None
        if not isinstance(key, str):
            raise ValidationError(['invalid evidence reference'])
        with self.lock:
            record = self.evidence.get(key)
        if record is None or record[0] != digest:
            raise ConflictError('source evidence expired or does not match; reopen the revision or recalculate')
        return json.loads(json.dumps(record[1]))

    def login(self, token):
        if not isinstance(token, str) or not 1 <= len(token) <= 256:
            return None
        digest = hashlib.sha256(token.encode()).hexdigest()
        user = next((u for d, u in self.credentials.items() if hmac.compare_digest(d, digest)), None)
        if user is None:
            return None
        session = secrets.token_urlsafe(32)
        with self.lock:
            now = time.monotonic()
            self.sessions = {k: v for k, v in self.sessions.items() if v[1] > now}
            if len(self.sessions) >= 256:
                return None
            self.sessions[hashlib.sha256(session.encode()).hexdigest()] = (user, now + self.ttl_seconds)
        return session, user

    def session(self, cookie):
        if self.local_user is not None:
            return 'local', self.local_user
        try:
            parsed = SimpleCookie(cookie or '')
            value = parsed[COOKIE].value
        except (CookieError, KeyError, ValueError):
            return None, None
        digest = hashlib.sha256(value.encode()).hexdigest()
        with self.lock:
            entry = self.sessions.get(digest)
            if entry and entry[1] > time.monotonic():
                return digest, entry[0]
            self.sessions.pop(digest, None)
        return None, None

    def audit(self, user, action, status, **metadata):
        entry = {'at': datetime.now(UTC).isoformat(), 'user': user[0] if user else None,
                 'role': user[1] if user else None, 'action': action, 'status': status, **metadata}
        try:
            with self.lock, self.audit_path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry, sort_keys=True) + '\n')
            return True
        except OSError:
            # A failed secondary log must never disguise a committed snapshot
            # as a rolled-back transaction. The revision stores its actor/time.
            return False


def checked_proposals(scenario, proposals):
    """Pre-check assistant proposals against the Scenario of the plan they refer to; nothing is applied."""
    from planning_factory.adapters.dashboard_simulation import apply_whatif_edits
    checked = []
    for item in proposals:
        try:
            apply_whatif_edits(scenario, item['edits'])
            checked.append({**item, 'valid': True})
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            reason = '; '.join(exc.errors) if isinstance(exc, ValidationError) else str(exc)
            checked.append({**item, 'valid': False, 'error': reason[:500]})
    return checked


def make_secure_handler(app):
    base = make_handler(app.state)

    class Handler(base):
        max_body = MAX_BODY

        def _request_allowed(self):
            if not self._trusted_host() or self.headers.get('Sec-Fetch-Site') not in (None, 'none', 'same-origin'):
                return False
            if app.local_user is not None and self.headers.get('Host', '').split(':', 1)[0].lower() not in ('127.0.0.1', 'localhost'):
                return False  # tokenless access never leaves this PC, not even through a port proxy
            origin = self.headers.get('Origin')
            return origin is None or origin in (f'http://{self.headers.get("Host")}', f'https://{self.headers.get("Host")}')

        def _body(self):
            try:
                length = int(self.headers.get('Content-Length', '0'))
            except ValueError:
                length = 0
            if self.headers.get('Transfer-Encoding') or not 0 < length <= MAX_BODY or self.headers.get_content_type() != 'application/json':
                raise ValidationError(['bounded application/json body required'])
            self.connection.settimeout(20)
            text = self.rfile.read(length)
            self.body_unread = False
            data = json.loads(text.decode('utf-8'), object_pairs_hook=_no_duplicates,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
            if not isinstance(data, dict):
                raise ValidationError(['JSON object required'])
            return data

        def _raw(self, data, mime):
            self._headers(200, mime, len(data))
            self.wfile.write(data)

        def _user(self, role='reparto'):
            digest, user = app.session(self.headers.get('Cookie'))
            if not user:
                self.respond(401, {'error': 'authentication required'})
                return None
            if ROLES[user[1]] < ROLES[role]:
                self.respond(403, {'error': f'role {role} required'})
                return None
            self.current_user = user
            return user

        def _metadata(self, result, user, revision=None):
            result['meta'].update({'authenticated': True, 'role': user[1],
                                   'snapshot_storage': True, 'saved_revision': revision,
                                   'llm_available': app.assistant is not None})
            if result['meta'].get('source') not in ('stored_snapshot', 'archived_whatif', 'llm_whatif_preview'):
                result['meta']['rule_scope'] = app.rules.scope_context
            return result

        def respond(self, code, obj):
            if code == 200 and isinstance(obj, dict) and {'scenario', 'plan', 'meta'} <= set(obj) and getattr(self, 'current_user', None):
                self._metadata(obj, self.current_user, obj['meta'].get('saved_revision'))
                meta = obj['meta']
                evidence = meta.pop('_archive_evidence', None)
                if evidence is None:
                    evidence = {'canonical': meta.get('canonical'),
                                'source_manifest': app.state.manifest if meta.get('canonical') else None,
                                'company_memory': meta.get('company_memory'),
                                'lineage': meta.get('lineage'), 'source': meta.get('source'),
                                'workbook': meta.get('workbook'),
                                'recorded_at': datetime.now(UTC).isoformat(),
                                'source_certified': False}
                meta['evidence_id'] = app.remember_evidence(obj['plan']['scenario_digest'], evidence)
            return super().respond(code, obj)

        def _fail(self, code, errors):
            errors = italian(list(errors))
            return self.respond(code, {'error': '; '.join(errors)[:4000], 'errors': errors[:50]})

        def _admin_get(self, path, user):
            query = parse_qs(urlsplit(self.path).query, keep_blank_values=False)
            one = lambda name, default='': (query.get(name) or [default])[0]  # noqa: E731
            try:
                if path.startswith('/api/settings/'):
                    if ROLES[user[1]] < ROLES['planner']:
                        return self.respond(403, {'error': 'role planner required'})
                    if path == '/api/settings/ai':
                        status = app.ai.status(user[0])
                        status['active'] = app.assistant is not None
                        status['can_edit'] = user[1] == 'admin'
                        return self.respond(200, status)
                    if path == '/api/settings/ai/models':
                        if user[1] != 'admin':
                            return self.respond(403, {'error': 'role admin required'})
                        provider = one('provider', 'anthropic')
                        models = app.ai.models(provider)
                        return self.respond(200, {'provider': provider, 'models': models,
                                                  'recommended': app.ai.suggestion(provider, models)})
                    return self.respond(404, {'error': 'not found'})
                if app.data is None:
                    return self.respond(404, {'error': 'Le maschere dati richiedono un binding canonico (sorgenti file).'})
                if path == '/api/sources':
                    return self.respond(200, app.data.sources())
                if path == '/api/data/catalog':
                    return self.respond(200, app.data.catalog())
                if path == '/api/data/rows':
                    filters = {k[2:]: v[0] for k, v in query.items() if k.startswith('f.') and v and v[0]}
                    return self.respond(200, app.data.rows(
                        one('entity'), q=one('q')[:200], status=one('status')[:60], filters=filters,
                        sort=one('sort') or None, descending=one('dir') == 'desc',
                        offset=int(one('offset', '0')), limit=int(one('limit', '50'))))
                if path == '/api/data/ids':
                    return self.respond(200, {'ids': app.data.ids(one('ref'), one('q')[:100])})
                if path == '/api/data/history':
                    key = json.loads(one('key')) if one('key') else None
                    entity = one('entity') or None
                    return self.respond(200, {'history': app.data.history(entity, key)})
                return self.respond(404, {'error': 'not found'})
            except ProviderError as exc:
                return self.respond(502, {'error': str(exc)})
            except (ValueError, ValidationError) as exc:
                return self._fail(422, getattr(exc, 'errors', None) or [str(exc)])

        def _admin_post(self, path, user):
            try:
                data = self._body()
                if path.startswith('/api/factory-source/'):
                    if user[1]!='admin':return self.respond(403,{'error':'Ruolo amministratore richiesto'})
                    if app.factory_sync is None:return self.respond(404,{'error':'Sincronizzazione Factory non configurata per questa istanza Excel'})
                    sync=app.factory_sync
                    if path=='/api/factory-source/config':result=sync.save_config(data)
                    elif path=='/api/factory-source/test':result=sync.test_connection(data)
                    elif path=='/api/factory-source/run':result=sync.start(data,user[0])
                    elif path=='/api/factory-source/resolve':result=sync.resolve(data,user[0])
                    elif path=='/api/factory-source/cancel':
                        if data:raise ValidationError(['Nessun parametro richiesto'])
                        result=sync.cancel(user[0])
                    else:return self.respond(404,{'error':'not found'})
                    app.audit(user,'factory_sync_'+path.rsplit('/',1)[-1],200,
                              job_id=result.get('job_id'),state=result.get('state'),ok=result.get('ok'))
                    return self.respond(202 if path.endswith(('/run','/resolve')) else 200,result)
                if path.startswith('/api/settings/'):
                    if path == '/api/settings/ai':
                        status = app.ai.save(data, user[0])
                        app.assistant = app.ai.assistant
                        status['active'] = app.assistant is not None
                        status['can_edit'] = True
                        app.audit(user, 'ai_settings', 200, enabled=status['settings']['enabled'],
                                  roles={r: [v['provider'], v['model']] for r, v in status['roles'].items()})
                        return self.respond(200, status)
                    if path == '/api/settings/ai/reload':
                        app.assistant = app.ai.build()
                        status = app.ai.status(user[0])
                        status['active'] = app.assistant is not None
                        status['can_edit'] = True
                        return self.respond(200, status)
                    if path == '/api/settings/ai/test':
                        if set(data) != {'provider', 'model'}:
                            raise ValidationError(['provider e model richiesti'])
                        result = app.ai.test(data['provider'], data['model'])
                        app.audit(user, 'ai_test', 200, provider=data['provider'], model=data['model'])
                        return self.respond(200, result)
                    if path in ('/api/settings/ai/connect', '/api/settings/ai/disconnect'):
                        connect = path.endswith('/connect')
                        allowed = {'provider', 'key', 'workspace'} if connect else {'provider'}
                        if set(data) - allowed or 'provider' not in data or (connect and 'key' not in data):
                            raise ValidationError(['provider' + (' e key' if connect else '') + ' richiesti'])
                        result = (app.ai.connect(data['provider'], data['key'], data.get('workspace')) if connect
                                  else app.ai.disconnect(data['provider']))
                        app.assistant = app.ai.assistant
                        # The key itself never reaches the audit log.
                        app.audit(user, 'ai_connect' if connect else 'ai_disconnect', 200, provider=data['provider'])
                        status = app.ai.status(user[0])
                        status.update(active=app.assistant is not None, can_edit=True, connected=result)
                        return self.respond(200, status)
                    if path.startswith('/api/settings/ai/subscription/'):
                        step = path.rsplit('/', 1)[-1]
                        if step not in ('login', 'code', 'logout') or set(data) != ({'code'} if step == 'code' else set()):
                            raise ValidationError(['richiesta non valida per l\'abbonamento'])
                        if step == 'login':
                            return self.respond(200, {'url': app.ai.subscription_login()})
                        if step == 'code':
                            app.ai.subscription_code(data['code'])
                        else:
                            app.ai.subscription_logout()
                        app.assistant = app.ai.assistant
                        app.audit(user, 'ai_connect' if step == 'code' else 'ai_disconnect', 200, provider='abbonamento')
                        status = app.ai.status(user[0])
                        status.update(active=app.assistant is not None, can_edit=True)
                        return self.respond(200, status)
                    if path == '/api/settings/ai/openrouter':
                        if data:
                            raise ValidationError(['nessun campo richiesto'])
                        scheme = 'https' if app.secure_cookie else 'http'
                        url = app.ai.openrouter_start(f'{scheme}://{self.headers.get("Host")}{OPENROUTER_CALLBACK}', user[0])
                        return self.respond(200, {'url': url})
                    return self.respond(404, {'error': 'not found'})
                if app.data is None:
                    return self.respond(404, {'error': 'Le maschere dati richiedono un binding canonico (sorgenti file).'})
                if path == '/api/data/exclude-orders-before':
                    if set(data) != {'before', 'reason'}:
                        raise ValidationError(['before and reason required'])
                    from planning_factory.adapters.assistant_actions import AssistantActions
                    actions = AssistantActions(app, user, {'view': 'deliveries'})
                    result = actions.call('exclude_orders_before', data)
                    return self.respond(200, {**result, 'actions': actions.client, 'undo': actions.undo})
                if path == '/api/data/corrections':
                    if set(data) - {'entity', 'action', 'key', 'values', 'reason'} or not {'entity', 'action', 'reason'} <= set(data):
                        raise ValidationError(['entity, action, reason richiesti'])
                    result = app.data.correct(data['entity'], data['action'], data.get('key'), data.get('values') or {},
                                              data['reason'], user[0])
                    app.audit(user, 'data_correction', 200, correction=result['correction']['id'],
                              entity=data['entity'], correction_action=data['action'])
                    return self.respond(200, result)
                if path == '/api/data/corrections/revoke':
                    if set(data) != {'ids', 'reason'} or not isinstance(data['ids'], list):
                        raise ValidationError(['ids e reason richiesti'])
                    result = app.data.revoke(data['ids'], data['reason'], user[0])
                    app.audit(user, 'data_correction_revoke', 200, corrections=data['ids'])
                    return self.respond(200, result)
                if path == '/api/sources/reload':
                    result = app.data.reload()
                    app.audit(user, 'sources_reload', 200, scenario_digest=result.get('scenario_digest'))
                    return self.respond(200, result)
                if path == '/api/sources/open-workbook':
                    if data:raise ValidationError(['Nessun parametro richiesto'])
                    if app.local_user is None or app.state.workbook_input is None:
                        return self.respond(403, {'error': 'Apertura disponibile solo per l’Excel dell’istanza locale'})
                    if not hasattr(os, 'startfile'):
                        return self.respond(503, {'error': 'Apertura del file disponibile su Windows'})
                    try:
                        os.startfile(str(app.state.path.resolve()), 'open')
                    except OSError:
                        return self.respond(503, {'error': 'Impossibile aprire l’Excel. Verifica che il file esista e che un’applicazione sia associata ai file .xlsx.'})
                    app.audit(user, 'workbook_open', 200)
                    return self.respond(200, {'opened': True})
                return self.respond(404, {'error': 'not found'})
            except ProviderError as exc:
                app.audit(user, 'ai_' + path.rsplit('/', 1)[-1], 502)
                return self.respond(502, {'error': str(exc)})
            except OSError:
                return self.respond(503, {'error': 'file dell\'istanza non accessibile; nessuna modifica salvata'})
            except sqlite3.Error:
                return self.respond(503, {'error': 'archivio dell\'istanza non disponibile; nessuna modifica salvata'})
            except (ValueError, TypeError, KeyError, ValidationError) as exc:
                return self._fail(422, getattr(exc, 'errors', None) or [str(exc)])

        def _openrouter_callback(self):
            # Top-level navigation back from openrouter.ai: cross-site by nature and, with
            # tokens, without the SameSite=Strict cookie. The single-use state started by an
            # administrator authorizes it; the browser only ever sees a redirect.
            if not self._trusted_host() or (app.local_user is not None and
                                            self.headers.get('Host', '').split(':', 1)[0].lower() not in ('127.0.0.1', 'localhost')):
                return self.respond(403, {'error': 'untrusted origin or host'})
            query = parse_qs(urlsplit(self.path).query)
            one = lambda name: (query.get(name) or [None])[0]  # noqa: E731
            try:
                actor, _ = app.ai.openrouter_finish(one('code'), one('state'))
                app.assistant = app.ai.assistant
                app.audit((actor, 'admin'), 'ai_connect', 200, provider='openrouter', via='oauth')
                outcome = 'ok'
            except (ValidationError, ProviderError, OSError):
                app.audit(None, 'ai_connect', 400, provider='openrouter', via='oauth')
                outcome = 'errore'
            self.send_response(303)
            self.send_header('Location', f'/?ai={outcome}#impostazioni')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Length', '0')
            self.end_headers()

        def do_GET(self):
            if urlsplit(self.path).path == OPENROUTER_CALLBACK:
                return self._openrouter_callback()
            if not self._request_allowed():
                return self.respond(403, {'error': 'untrusted origin or host'})
            path = urlsplit(self.path).path
            if path == '/login.js':
                return self._raw(LOGIN_JS, 'application/javascript; charset=utf-8')
            if path == '/styles.css':
                return super().do_GET()
            if path == '/' and app.session(self.headers.get('Cookie'))[1] is None:
                return self._raw(LOGIN_HTML, 'text/html; charset=utf-8')
            user = self._user()
            if not user:
                return
            try:
                if path == '/api/session':
                    return self.respond(200, {'name': user[0], 'role': user[1], 'local': app.local_user is not None,
                                              **({'input_editable': False, 'source_kind': 'planning_workbook',
                                                  'workbook_open_available': app.local_user is not None and hasattr(os, 'startfile')} if app.state.workbook_input else {}),
                                              'data_screens': app.data is not None})
                if path.startswith('/api/data/') or path == '/api/sources' or path.startswith('/api/settings/'):
                    return self._admin_get(path, user)
                if path == '/api/bootstrap':
                    return self.respond(200, self._metadata(app.state.bootstrap(), user))
                if path == '/api/materials':
                    return self.respond(200, app.data.materials() if app.data is not None
                                        else {'available': False, 'needs': [], 'supplies': []})
                if path == '/api/snapshots':
                    return self.respond(200, {'revisions': app.store.list_revisions()})
                if path == '/api/rules':
                    return self.respond(200, {'rules': app.rules.list(), 'editable': app.state.workbook_input is None})
                if path == '/api/regeneration':
                    if app.regeneration is None:
                        return self.respond(200, {'available': False})
                    return self.respond(200, {'available': True, **app.regeneration.status()})
                if path == '/api/factory-source':
                    return self.respond(200,app.factory_sync.config() if app.factory_sync else {'available':False})
                if path == '/api/factory-source/report':
                    if app.factory_sync is None:return self.respond(404,{'error':'Sincronizzazione non disponibile'})
                    return self.respond(200,app.factory_sync.conflict_report())
                if path.startswith('/api/factory-source/sql/') and path.endswith('.sql'):
                    if user[1]!='admin':return self.respond(403,{'error':'Ruolo amministratore richiesto'})
                    if app.factory_sync is None:return self.respond(404,{'error':'Sincronizzazione non disponibile'})
                    content=app.factory_sync.reference_sql(path.rsplit('/',1)[-1][:-4]).encode('utf-8')
                    self._headers(200,'text/plain; charset=utf-8',len(content));self.wfile.write(content);return
                parts = path.split('/')
                if len(parts)==7 and parts[1:3]==['api','snapshots'] and parts[4]=='revisions' and parts[6]=='input.xlsx':
                    content=app.store.load_workbook(unquote(parts[3]),int(parts[5]))
                    self.send_response(200)
                    self.send_header('Content-Type','application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                    self.send_header('Content-Disposition','attachment; filename="input-pianificazione.xlsx"')
                    self.send_header('Content-Length',str(len(content)))
                    self.send_header('Cache-Control','no-store')
                    self.end_headers();self.wfile.write(content)
                    return
                if len(parts) == 6 and parts[1:3] == ['api', 'snapshots'] and parts[4] == 'revisions':
                    revision = int(parts[5])
                    source, plan = app.store.load_snapshot(unquote(parts[3]), revision)
                    archived = app.store.load_evidence(source.id, revision)
                    evidence = archived['evidence'] if archived else None
                    errors = verify_plan(source, plan)
                    if errors:
                        raise ValidationError(list(errors))
                    app.state.remember_plan(source, plan,capacity_scale=((evidence or {}).get('workbook') or {}).get('capacity_scale',1))
                    result = {'scenario': json.loads(dumps(source)), 'plan': json.loads(dumps(plan)),
                              'meta': {'source': 'stored_snapshot', 'dataset': 'Snapshot salvato',
                                       'engine_verified': True, 'erp_connected': False, 'read_only': True,
                                       'source_publishable': False, 'v03_available': False,
                                       'canonical': evidence.get('canonical') if evidence else None,
                                       'company_memory': evidence.get('company_memory') if evidence else None,
                                       '_archive_evidence': evidence or {'source': 'legacy_snapshot', 'source_certified': False},
                                       'evidence_available': evidence is not None,
                                       'note': 'Evidenze archiviate alla revisione; non costituiscono certificazione delle fonti o rilascio industriale.'}}
                    return self.respond(200, self._metadata(result, user, revision))
                return super().do_GET()
            except KeyError:
                return self.respond(404, {'error': 'snapshot not found'})
            except (ValueError, ValidationError) as exc:
                return self.respond(422, {'error': str(exc)})
            except sqlite3.Error:
                return self.respond(503, {'error': 'snapshot storage unavailable'})

        def do_POST(self):
            self.close_connection = True  # early denials never leave unread request bytes on a reused socket
            self.body_unread = True
            if not self._request_allowed():
                return self.respond(403, {'error': 'untrusted origin or host'})
            path = urlsplit(self.path).path
            if path == '/api/login':
                try:
                    data = self._body()
                    result = app.login(data['token']) if set(data) == {'token'} else None
                except (ValueError, ValidationError):
                    result = None
                if result is None:
                    app.audit(None, 'login', 401)
                    return self.respond(401, {'error': 'invalid credentials or session capacity'})
                session, user = result
                content = json.dumps({'name': user[0], 'role': user[1]}).encode()
                # Audit before replying: a client that already holds the cookie
                # must never observe a log that does not yet record the login.
                app.audit(user, 'login', 200)
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Content-Length', str(len(content)))
                self.send_header('Set-Cookie', f'{COOKIE}={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age={app.ttl_seconds}' + ('; Secure' if app.secure_cookie else ''))
                self.end_headers()
                self.wfile.write(content)
                return
            user = self._user('admin' if path.startswith(('/api/settings/','/api/factory-source/')) else
                              'reparto' if path in ('/api/logout', '/api/assistant', '/api/explain', '/api/loads', '/api/quality', '/api/delays') else 'planner')
            if not user:
                return
            if path.startswith(('/api/data/','/api/sources/','/api/settings/','/api/factory-source/')):
                return self._admin_post(path, user)
            try:
                if app.state.workbook_input is not None and path.startswith('/api/rules'):
                    from planning_factory.adapters.workbook_workspace import edit_in_excel
                    edit_in_excel()
                if path == '/api/logout':
                    digest, _ = app.session(self.headers.get('Cookie'))
                    with app.lock:
                        app.sessions.pop(digest, None)
                    app.audit(user, 'logout', 200)
                    return self.respond(200, {'logged_out': True})
                if path == '/api/snapshots':
                    data = self._body()
                    if set(data) - {'scenario', 'plan', 'expected_revision', 'evidence_id'} or not {'scenario', 'plan', 'expected_revision'} <= set(data):
                        raise ValidationError(['scenario, plan and expected_revision required'])
                    source = scenario_from_dict(data['scenario'])
                    result = plan_loads(json.dumps(data['plan']))
                    evidence = app.get_evidence(data.get('evidence_id'), result.scenario_digest)
                    revision = app.save_snapshot(source, result, expected_revision=data['expected_revision'], evidence=evidence, actor=user[0])
                    audit_written = app.audit(user, 'save_snapshot', 200, scenario_id=source.id, revision=revision)
                    return self.respond(200, {'scenario_id': source.id, 'revision': revision,
                                              'audit_file_written': audit_written,
                                              'scenario_digest': result.scenario_digest})
                if path == '/api/snapshots/preview':
                    data = self._body()
                    if set(data) != {'scenario_id', 'revision', 'settings', 'edits'} or type(data['revision']) is not int or data['revision'] < 1 or not isinstance(data['scenario_id'], str):
                        raise ValidationError(['scenario_id, positive revision, settings and edits required'])
                    source, before = app.store.load_snapshot(data['scenario_id'], data['revision'])
                    archived = app.store.load_evidence(source.id, data['revision'])
                    from planning_factory.adapters.dashboard_simulation import apply_whatif_edits
                    from planning_factory.engine.simulation import compare
                    if isinstance(data['settings'], dict) and 'delivery_buffer_seconds' in data['settings']:
                        raise ValidationError(['delivery_buffer_seconds: le date promesse di una revisione archiviata non sono note'])
                    adjusted = apply_whatif_edits(source, data['edits'])
                    adjusted = replace(adjusted, settings=_settings_override(source.settings, data['settings']))
                    result = app.state._audited_plan(adjusted)
                    app.state.remember_plan(adjusted, result)
                    lineage = {'scenario_id': source.id, 'revision': data['revision'],
                               'scenario_digest': before.scenario_digest, 'edits': data['edits'],
                               'settings': data['settings']}
                    evidence = {'source': 'archived_whatif', 'parent': archived,
                                'canonical': None, 'company_memory': None,
                                'lineage': lineage, 'source_certified': False}
                    app.audit(user, 'archived_simulation', 200, scenario_id=source.id, revision=data['revision'])
                    return self.respond(200, {'scenario': json.loads(dumps(adjusted)), 'plan': json.loads(dumps(result)),
                        'comparison': primitive(compare(before, result)), 'meta': {
                            'source': 'archived_whatif', 'dataset': 'Simulazione da revisione archiviata',
                            'source_publishable': False, 'engine_verified': True, 'erp_connected': False,
                            'v03_available': False, 'partial_plan': bool(result.unscheduled), 'read_only': True,
                            'simulated': True, 'lineage': lineage, 'canonical': None,
                            '_archive_evidence': evidence, 'saved_revision': data['revision'],
                            'note': 'Simulazione dalla revisione indicata; nessuna regola corrente applicata retroattivamente.'}})
                if path == '/api/rules':
                    data = self._body()
                    if set(data) != {'rule'}:
                        raise ValidationError(['rule required'])
                    draft = app.rules.create_draft(data['rule'], user[0])
                    app.audit(user, 'rule_draft', 200, rule_id=draft['id'])
                    return self.respond(200, draft)
                if path == '/api/rules/decision':
                    data = self._body()
                    if set(data) != {'rule_id', 'expected_version', 'decision'}:
                        raise ValidationError(['rule_id, expected_version and decision required'])
                    current = next((r for r in app.rules.list() if r['id'] == data['rule_id']), None)
                    if current is None:
                        raise ValidationError(['rule not found'])
                    if current['version'] != data['expected_version']:
                        raise ConflictError('rule version changed; reload the rules before deciding')
                    # Validate under the memory transaction's writer lock so
                    # concurrent confirmations cannot race past each other.
                    def validate_confirmation():
                        adjusted, _ = app.rules.apply(app.state.source, preview_rule=data['rule_id'])
                        app.state._audited_plan(adjusted)
                    decided = app.rules.decide(data['rule_id'], data['expected_version'], data['decision'], user[0],
                                               validate=validate_confirmation)
                    with app.state._lock:
                        app.state._default = None
                        app.state._stage_cache = None
                    app.audit(user, 'rule_' + data['decision'], 200, rule_id=decided['id'], version=decided['version'])
                    return self.respond(200, decided)
                if path == '/api/rules/preview':
                    data = self._body()
                    if set(data) != {'rule_id'}:
                        raise ValidationError(['rule_id required'])
                    from planning_factory.engine.simulation import compare
                    adjusted, evidence = app.rules.apply(app.state.source, preview_rule=data['rule_id'])
                    result = app.state._audited_plan(adjusted)
                    _, before = app.state.calculate()
                    return self.respond(200, {'preview_only': True, 'evidence': evidence,
                                              'comparison': primitive(compare(before, result))})
                if path == '/api/rules/suggest':
                    if app.assistant is None:
                        return self.respond(503, {'error': 'Claude not configured; manual drafts remain available'})
                    data = self._body()
                    if set(data) != {'question'}:
                        raise ValidationError(['question required'])
                    suggested = app.assistant.suggest_rule(data['question'], {o.order_id for o in app.state.source.operations}, user[0],
                                                         scope_catalog=app.rules.scope_catalog())
                    if not suggested['supported']:
                        return self.respond(200, suggested)
                    draft = app.rules.create_draft(suggested['rule'], user[0])
                    app.audit(user, 'rule_suggested_draft', 200, rule_id=draft['id'])
                    return self.respond(200, {'supported': True, 'draft': draft})
                if path == '/api/assistant':
                    if app.assistant is None:
                        return self.respond(503, {'error': 'Claude not configured; deterministic analysis remains available'})
                    data = self._body()
                    short = 'scenario_digest' in data
                    allowed = {'question', 'history', 'evidence_id', 'ui'} | ({'scenario_digest'} if short else {'scenario', 'plan'})
                    if set(data) - allowed or not ({'question', 'scenario_digest'} if short else {'question', 'scenario', 'plan'}) <= set(data):
                        raise ValidationError(['question with scenario_digest, or question, scenario and plan required'])
                    if short:
                        insights = app.state.insights(data['scenario_digest'])
                        if insights is None:
                            return self.respond(409, {'error': 'plan not held by the server; send scenario and plan',
                                                      'code': 'plan_not_indexed'})
                        plan_data = json.loads(dumps(insights.plan))
                    else:
                        source = scenario_from_dict(data['scenario'])
                        result = plan_loads(json.dumps(data['plan']))
                        findings = verify_plan(source, result)
                        if findings:
                            raise ValidationError(list(findings))
                        insights, plan_data = app.state.remember_plan(source, result), data['plan']
                    digest = insights.plan.scenario_digest
                    # Provenance is server-owned, never accepted as an assertion from the browser.
                    evidence = app.get_evidence(data.get('evidence_id'), digest)
                    snapshot = {'plan': plan_data, 'meta': {
                        'canonical': evidence.get('canonical') if evidence else None,
                        'company_memory': evidence.get('company_memory') if evidence else None},
                        # Customers as recorded with this plan's evidence, never today's for an archived plan.
                        'order_texts': app.state.order_texts(evidence_manifest(evidence) or False)
                        if app.state.manifest is not None else app.state.order_texts()}
                    # The assistant acts with this user's role; an archived revision stays read-only.
                    actions = AssistantActions(app, user, data.get('ui'), insights, snapshot['meta'],
                                               order_texts=snapshot['order_texts'])
                    answer = app.assistant.answer(data['question'], snapshot, user[0],
                                                  history=data.get('history', []), insights=insights, actions=actions,
                                                  memory=app.rules.guidance())
                    answer['proposals'] = checked_proposals(insights.scenario, answer.get('proposals', []))
                    app.audit(user, 'assistant', 200, scenario_digest=digest,
                              tools_used=answer['tools_used'], usage=answer['usage'],
                              proposals=len(answer['proposals']), actions=len(answer.get('actions', [])),
                              undo=len(answer.get('undo', [])))
                    return self.respond(200, answer)
                if path == '/api/assistant/undo':
                    data = self._body()
                    if set(data) - {'token', 'ui'} or 'token' not in data:
                        raise ValidationError(['token required'])
                    return self.respond(200, AssistantActions(app, user, data.get('ui')).run_undo(data['token']))
                if path == '/api/simulations/suggest':
                    if app.assistant is None:
                        return self.respond(503, {'error': 'Claude not configured; manual simulations remain available'})
                    data = self._body()
                    if set(data) - {'question', 'scenario', 'plan', 'evidence_id'} or not {'question', 'scenario', 'plan'} <= set(data):
                        raise ValidationError(['question, scenario and plan required'])
                    source = scenario_from_dict(data['scenario'])
                    before = plan_loads(json.dumps(data['plan']))
                    errors = verify_plan(source, before)
                    if errors:
                        raise ValidationError(list(errors))
                    parent_evidence = app.get_evidence(data.get('evidence_id'), before.scenario_digest)
                    proposed = app.assistant.suggest_simulation(data['question'], data, user[0])
                    if not proposed['supported']:
                        return self.respond(200, {'supported': False, 'reason': proposed['reason'][:2000]})
                    from planning_factory.adapters.dashboard_simulation import apply_whatif_edits
                    from planning_factory.engine.simulation import compare
                    adjusted = apply_whatif_edits(source, proposed['edits'])
                    result = app.state._audited_plan(adjusted)
                    app.state.remember_plan(adjusted, result)
                    evidence_id = app.remember_evidence(result.scenario_digest, {
                        'source': 'llm_whatif_preview', 'parent': parent_evidence,
                        'canonical': None, 'company_memory': None, 'source_certified': False,
                        'lineage': {'source_digest': before.scenario_digest, 'edits': proposed['edits']}})
                    app.audit(user, 'assistant_simulation_preview', 200, scenario_digest=result.scenario_digest)
                    return self.respond(200, {'supported': True, 'source_digest': before.scenario_digest,
                        'edits': proposed['edits'], 'comparison': primitive(compare(before, result)),
                        'preview': {'scenario': json.loads(dumps(adjusted)), 'plan': json.loads(dumps(result)),
                            'meta': {'engine_verified': True, 'source_publishable': False, 'canonical': None,
                                     'company_memory': None, 'v03_available': False, 'read_only': True,
                                     'evidence_id': evidence_id, 'saved_revision': None,
                                     'erp_connected': False, 'source': 'llm_whatif_preview',
                                     'note': 'Simulazione proposta da Claude, calcolata e verificata dal motore; non pubblicata.'}}})
                return super().do_POST()
            except PlannerBusyError as exc:
                return self.respond(409, {'error': str(exc)})
            except AssistantUnavailable as exc:
                app.audit(user, 'assistant', 503)
                return self.respond(503, {'error': str(exc)})
            except ConflictError as exc:
                app.audit(user, 'save_snapshot', 409)
                return self.respond(409, {'error': str(exc)})
            except sqlite3.Error:
                app.audit(user, 'snapshot_storage', 503)
                return self.respond(503, {'error': 'snapshot storage unavailable; transaction rolled back'})
            except (ValueError, ValidationError, TypeError, KeyError) as exc:
                return self.respond(422, {'error': str(exc)})

    return Handler


def make_secure_dashboard_server(fixture, credentials, store, audit, *, staged_spec=None,
                                 port=8766, secure_cookie=False, local_user=None, env_file=None):
    state = DashboardState(Path(fixture), staged_spec=staged_spec)
    app = SecureDashboard(state, credentials, store, audit, secure_cookie=secure_cookie,
                          local_user=local_user, env_file=env_file)
    server = LocalDashboardServer(('127.0.0.1', port), make_secure_handler(app))
    server.application = app
    return server


def serve_secure_dashboard(fixture, credentials, store, audit, *, staged_spec=None,
                           port=8766, secure_cookie=False, local_user=None, env_file=None):
    with make_secure_dashboard_server(fixture, credentials, store, audit, staged_spec=staged_spec,
                                      port=port, secure_cookie=secure_cookie, local_user=local_user,
                                      env_file=env_file) as server:
        mode = f'accesso locale come {local_user}, senza token' if local_user else 'accesso con token'
        print(f'Planning Factory Control Room ({mode}): http://127.0.0.1:{server.server_port}', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
