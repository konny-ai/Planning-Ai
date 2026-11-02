"""Installation AI settings: provider and model per role, daily call limits, key status.

Keys stay on the server, in the process environment or in the instance ``.env`` file.
The settings page can hand a key to the server (pasted, or obtained with the OpenRouter
one-click login): the server verifies it with the provider and only then writes it to
the ``.env`` file. The page never reads a key back, only whether it is present and its
last four characters. Provider, models and limits are an explicit choice of an
administrator, stored with author and instant; nothing is enabled by default and no
model is preselected (the guided setup shows a suggestion that the administrator confirms).
"""
import base64
from contextlib import closing
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
from secrets import token_urlsafe
import sqlite3
import threading
import time
from urllib.parse import urlencode

from planning_factory.adapters.claude_assistant import AssistantUnavailable, CallQuota, ClaudeAssistant
from planning_factory.adapters.claude_subscription import LABEL as SUBSCRIPTION_LABEL, MODELS as SUBSCRIPTION_MODELS
from planning_factory.adapters.claude_subscription import PROVIDER as SUBSCRIPTION
from planning_factory.adapters.llm_providers import (DEFAULT_ANTHROPIC_VERSION, OPENROUTER_AUTH, PROVIDERS, ProviderError,
                                                     exchange_openrouter_code, list_models, probe, recommended,
                                                     transport_for, verify_key)
from planning_factory.domain.validation import ValidationError

ROLES = {
    'assistant': {'label': 'Assistente del piano', 'description': 'Risponde alle domande sul piano con strumenti in sola lettura.'},
    'rules': {'label': 'Bozze di regole', 'description': 'Traduce una richiesta in una bozza di regola da confermare.'},
    'simulation': {'label': 'Proposte di simulazione', 'description': 'Propone fermi o spostamenti da simulare, mai pubblicati.'},
}
_MODEL = re.compile(r'^[A-Za-z0-9._:/@+\-]{1,200}$')
_VERSION = re.compile(r'^\d{4}-\d{2}-\d{2}$')
EMPTY = {'enabled': False, 'provider': None, 'roles': {r: {'provider': None, 'model': None} for r in ROLES},
         'quota_per_user': None, 'quota_total': None, 'api_version': DEFAULT_ANTHROPIC_VERSION}
KEY_FORMAT = {'anthropic': (re.compile(r'^sk-ant-[A-Za-z0-9_\-]{20,400}$'), 'una chiave Anthropic inizia per sk-ant-'),
              'openrouter': (re.compile(r'^sk-or-[A-Za-z0-9_\-]{20,400}$'), 'una chiave OpenRouter inizia per sk-or-')}
GATEWAY_KEY = (re.compile(r'^[A-Za-z0-9_\-.]{8,400}$'), 'la chiave del gateway contiene caratteri non ammessi')
_WORKSPACE = re.compile(r'^wrkspc_[A-Za-z0-9]{4,120}$')
OAUTH_SECONDS = 600
ENV_HEADER = "# Chiavi AI di questa istanza, scritte dalla pagina Impostazioni AI. Restano sul server, mai in git."


def parse_env_file(path: Path) -> dict:
    """Minimal KEY=VALUE reader (comments, export, quotes). Values are never logged."""
    values = {}
    try:
        text = path.read_text(encoding='utf-8-sig')
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        name, _, value = line.partition('=')
        name = name.strip().removeprefix('export ').strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        elif ' #' in value:
            value = value.split(' #', 1)[0].rstrip()
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            values[name] = value
    return values


def _env_name(line):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        return None
    return line.partition('=')[0].strip().removeprefix('export ').strip()


def update_env_file(path: Path, updates: dict):
    """Sets (value) or removes (None) KEY=VALUE lines, keeping comments and the other keys.

    Values are validated by the caller (no spaces, quotes or line breaks); the file is
    replaced atomically so a crash never leaves half a key.
    """
    try:
        lines = path.read_text(encoding='utf-8-sig').splitlines()
    except FileNotFoundError:
        lines = [ENV_HEADER]
    out, done = [], set()
    for line in lines:
        name = _env_name(line)
        if name in updates:
            if name not in done and updates[name] is not None:
                out.append(f'{name}={updates[name]}')
            done.add(name)
            continue
        out.append(line)
    out += [f'{n}={v}' for n, v in updates.items() if n not in done and v is not None]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text('\n'.join(out) + '\n', encoding='utf-8')
    os.replace(temporary, path)


class Secrets:
    """Keys from the process environment, then from the instance .env file (re-read on demand)."""

    def __init__(self, env_file=None, environ=None):
        self.env_file = Path(env_file) if env_file else None
        self.environ = os.environ if environ is None else environ
        self.file_values = {}
        self.reload()

    def reload(self):
        self.file_values = parse_env_file(self.env_file) if self.env_file else {}

    def _get(self, names):
        for name in names:
            value = self.environ.get(name) or self.file_values.get(name)
            if value and value.strip() and not value.strip().endswith('...'):
                return value.strip(), name, 'ambiente' if self.environ.get(name) else 'file .env'
        return None, None, None

    def key(self, provider):
        return self._get(PROVIDERS[provider]['key_env'])[0]

    def value(self, name):
        """A non-secret setting such as OPENROUTER_URL."""
        return self._get((name,))[0]

    def workspace(self, provider):
        return self._get(PROVIDERS[provider]['workspace_env'])[0] if PROVIDERS[provider]['workspace_env'] else None

    def describe(self):
        result = {}
        for name, provider in PROVIDERS.items():
            value, variable, origin = self._get(provider['key_env'])
            workspace = self._get(provider['workspace_env']) if provider['workspace_env'] else (None, None, None)
            result[name] = {'label': provider['label'], 'present': bool(value), 'variable': variable or provider['key_env'][0],
                            'origin': origin, 'workspace': bool(workspace[0]),
                            'hint': '…' + value[-4:] if value and len(value) >= 12 else None,
                            'workspace_variable': provider['workspace_env'][0] if provider['workspace_env'] else None,
                            'gateway': self.value('OPENROUTER_URL') if name == 'openrouter' else None,
                            'keys_page': provider['keys_page'], 'credit_page': provider['credit_page']}
        return result

    def from_environment(self, provider):
        """The process variable that holds this provider's key, if any: it wins over the file."""
        return next((n for n in PROVIDERS[provider]['key_env'] if (self.environ.get(n) or '').strip()), None)


class _Candidate:
    """The installation secrets with one provider's key replaced: used to verify a key before writing it."""

    def __init__(self, base, provider, key, workspace):
        self.base, self.provider, self._key, self._workspace = base, provider, key, workspace

    def key(self, provider):
        return self._key if provider == self.provider else self.base.key(provider)

    def value(self, name):
        return self.base.value(name)

    def workspace(self, provider):
        return self._workspace if provider == self.provider else self.base.workspace(provider)


class AISettingsStore:
    def __init__(self, database):
        self.database = str(database)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS app_settings (name TEXT PRIMARY KEY, payload TEXT NOT NULL, '
                       'updated_by TEXT NOT NULL, updated_at TEXT NOT NULL)')

    def load(self) -> dict:
        with closing(sqlite3.connect(self.database)) as db:
            row = db.execute("SELECT payload, updated_by, updated_at FROM app_settings WHERE name='ai'").fetchone()
        if row is None:
            return {**json.loads(json.dumps(EMPTY)), 'updated_by': None, 'updated_at': None}
        value = json.loads(row[0])
        return {**json.loads(json.dumps(EMPTY)), **value, 'updated_by': row[1], 'updated_at': row[2]}

    def save(self, patch, actor) -> dict:
        current = self.load()
        merged = {k: current[k] for k in EMPTY}
        if not isinstance(patch, dict) or set(patch) - set(EMPTY):
            raise ValidationError(['impostazioni AI: campi ammessi ' + ', '.join(EMPTY)])
        merged.update(patch)
        clean = validate(merged)
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.execute("INSERT INTO app_settings VALUES ('ai', ?, ?, ?) ON CONFLICT(name) DO UPDATE SET "
                       'payload=excluded.payload, updated_by=excluded.updated_by, updated_at=excluded.updated_at',
                       (json.dumps(clean, sort_keys=True), actor, now))
        return self.load()


def _now():
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def validate(value) -> dict:
    errors = []
    if type(value.get('enabled')) is not bool:
        errors.append('enabled: vero o falso')
    allowed = (*PROVIDERS, SUBSCRIPTION)
    if value.get('provider') is not None and value['provider'] not in allowed:
        errors.append('provider: anthropic, openrouter o abbonamento')
    roles = value.get('roles')
    clean_roles = {}
    if not isinstance(roles, dict) or set(roles) - set(ROLES):
        errors.append('roles: ruoli ammessi ' + ', '.join(ROLES))
        roles = {}
    for role in ROLES:
        item = roles.get(role) or {'provider': None, 'model': None}
        if not isinstance(item, dict) or set(item) - {'provider', 'model'}:
            errors.append(f'{role}: provider e model')
            continue
        provider, model = item.get('provider'), item.get('model')
        if provider is not None and provider not in allowed:
            errors.append(f'{role}.provider: anthropic, openrouter o abbonamento')
        if model is not None and (not isinstance(model, str) or not _MODEL.match(model)):
            errors.append(f'{role}.model: identificativo del modello non valido')
        clean_roles[role] = {'provider': provider, 'model': model}
    per_user, total = value.get('quota_per_user'), value.get('quota_total')
    for name, number in (('quota_per_user', per_user), ('quota_total', total)):
        if number is not None and (type(number) is not int or not 1 <= number <= 10000):
            errors.append(f'{name}: intero da 1 a 10000')
    if type(per_user) is int and type(total) is int and per_user > total:
        errors.append('il tetto per utente non può superare il tetto totale')
    version = value.get('api_version') or DEFAULT_ANTHROPIC_VERSION
    if not isinstance(version, str) or not _VERSION.match(version):
        errors.append('api_version: data AAAA-MM-GG')
    if value.get('enabled') is True:
        if per_user is None or total is None:
            errors.append('per attivare l\'AI servono i tetti giornalieri di chiamate')
        if not clean_roles.get('assistant', {}).get('model'):
            errors.append('per attivare l\'AI serve almeno il modello dell\'assistente')
    if errors:
        raise ValidationError(errors)
    return {'enabled': value['enabled'], 'provider': value.get('provider'), 'roles': clean_roles,
            'quota_per_user': per_user, 'quota_total': total, 'api_version': version}


def effective_role(settings, role):
    """A role without its own choice uses the assistant's provider and model."""
    item = settings['roles'].get(role) or {}
    if item.get('model'):
        return item.get('provider') or settings.get('provider'), item['model'], True
    base = settings['roles'].get('assistant') or {}
    return base.get('provider') or settings.get('provider'), base.get('model'), False


class AssistantRouter:
    """Same interface as ClaudeAssistant; each method uses the model configured for its role."""

    def __init__(self, assistants: dict, problems: dict):
        self.assistants, self.problems = assistants, problems

    def _for(self, role):
        assistant = self.assistants.get(role)
        if assistant is None:
            raise AssistantUnavailable(self.problems.get(role) or 'Ruolo AI non configurato.')
        return assistant

    def answer(self, question, snapshot, actor, **kwargs):
        return self._for('assistant').answer(question, snapshot, actor, **kwargs)

    def suggest_rule(self, question, order_ids, actor, **kwargs):
        return self._for('rules').suggest_rule(question, order_ids, actor, **kwargs)

    def suggest_simulation(self, question, snapshot, actor):
        return self._for('simulation').suggest_simulation(question, snapshot, actor)


class AIManager:
    """Owns settings, secrets and the live assistant of one Control Room instance."""

    def __init__(self, database, env_file=None, environ=None, subscription=None):
        self.database = str(database)
        self.store = AISettingsStore(database)
        self.secrets = Secrets(env_file, environ)
        # Personal Claude subscription: only given in local single-user mode (see claude_subscription).
        self.subscription = subscription
        self.environment_locked = (environ if environ is not None else os.environ).get('PLANNING_CLAUDE_ENABLED') == '1'
        self.lock = threading.Lock()
        self._catalog = {}
        self._oauth = {}
        self.last_connect = None
        self.assistant = None
        self.problems = {}

    def build(self):
        """(Re)create the assistant from the stored settings. Returns it, or None when AI is off."""
        if self.environment_locked:
            from planning_factory.adapters.claude_assistant import from_environment
            self.assistant = from_environment(self.database)
            return self.assistant
        settings = self.store.load()
        self.problems = {}
        if not settings['enabled']:
            self.assistant = None
            return None
        quota = CallQuota(self.database, settings['quota_per_user'], settings['quota_total'])
        assistants = {}
        for role in ROLES:
            provider, model, _ = effective_role(settings, role)
            if not provider or not model:
                self.problems[role] = f'{ROLES[role]["label"]}: scegli fornitore e modello nelle Impostazioni AI.'
                continue
            if provider == SUBSCRIPTION:
                if self.subscription is None:
                    self.problems[role] = ('L\'abbonamento Claude si usa solo con l\'accesso locale di una persona sul '
                                           'proprio PC: qui serve una chiave API.')
                    continue
                # no API key: the transport authenticates through the CLI login of this instance
                assistants[role] = ClaudeAssistant('abbonamento-via-cli', model, quota, transport=self.subscription.transport(),
                                                   provider=SUBSCRIPTION)
                continue
            try:
                transport = transport_for(provider, self.secrets, version=settings['api_version'])
            except ProviderError as exc:
                self.problems[role] = str(exc)
                continue
            assistants[role] = ClaudeAssistant(self.secrets.key(provider), model, quota, transport=transport,
                                               provider=provider, version=settings['api_version'],
                                               workspace=self.secrets.workspace(provider))
        self.assistant = AssistantRouter(assistants, self.problems) if 'assistant' in assistants else None
        if self.assistant is None and 'assistant' not in self.problems:
            self.problems['assistant'] = 'Assistente non configurato.'
        return self.assistant

    def status(self, actor=None) -> dict:
        self.secrets.reload()
        settings = self.store.load()
        keys = self.secrets.describe()
        subscription = self.subscription.status() if self.subscription else None
        connected = {p: k['present'] for p, k in keys.items()}
        connected[SUBSCRIPTION] = bool(subscription and subscription['logged_in'])
        roles = {}
        for role, info in ROLES.items():
            provider, model, own = effective_role(settings, role)
            ready = bool(settings['enabled'] and provider and model and connected.get(provider))
            roles[role] = {**info, 'provider': provider, 'model': model, 'own_choice': own,
                           'stored': settings['roles'].get(role), 'ready': ready,
                           'problem': None if ready else (
                               'AI spenta' if not settings['enabled'] else
                               'Scegli il modello' if not model else
                               'Scegli il fornitore' if not provider else
                               'Solo con accesso locale' if provider == SUBSCRIPTION and subscription is None else
                               'Accedi con l\'abbonamento' if provider == SUBSCRIPTION else
                               f'Manca la chiave {keys[provider]["variable"]}')}
        providers = {k: v['label'] for k, v in PROVIDERS.items()}
        if subscription is not None:
            providers[SUBSCRIPTION] = SUBSCRIPTION_LABEL
        used = None
        if settings['quota_total']:
            used = CallQuota(self.database, settings['quota_per_user'], settings['quota_total']).used_today(actor)
        return {'settings': {k: settings[k] for k in EMPTY}, 'updated_by': settings['updated_by'],
                'updated_at': settings['updated_at'], 'keys': keys, 'roles': roles,
                'providers': providers, 'subscription': subscription,
                'env_file': str(self.secrets.env_file) if self.secrets.env_file else None,
                'env_file_exists': bool(self.secrets.env_file and self.secrets.env_file.exists()),
                'environment_locked': self.environment_locked,
                'active': self.assistant is not None, 'used_today': used,
                'default_api_version': DEFAULT_ANTHROPIC_VERSION, 'last_connect': self.last_connect}

    def _writable(self, provider):
        if self.environment_locked:
            raise ValidationError(['AI configurata da variabili PLANNING_CLAUDE_*: modificale sul server e riavvia'])
        if provider not in PROVIDERS:
            raise ValidationError(['fornitore non supportato'])
        if self.secrets.env_file is None:
            raise ValidationError(['questa istanza non ha un file delle chiavi: avviala con --env-file'])
        variable = self.secrets.from_environment(provider)
        if variable:
            raise ValidationError([f'La chiave {PROVIDERS[provider]["label"]} arriva dalla variabile d\'ambiente {variable} '
                                   'del server e prevale: toglila da lì per gestirla da questa pagina.'])

    def connect(self, provider, key, workspace=None, via='chiave'):
        """Verifies a key with the provider, then writes it to the instance .env file."""
        self._writable(provider)
        key = key.strip() if isinstance(key, str) else ''
        gateway = provider == 'openrouter' and self.secrets.value('OPENROUTER_URL')
        pattern, hint = GATEWAY_KEY if gateway else KEY_FORMAT[provider]
        if not pattern.match(key):
            raise ValidationError([f'Chiave non valida: {hint}. Copiala intera dalla pagina del fornitore.'])
        workspace = workspace.strip() if isinstance(workspace, str) and workspace.strip() else None
        if workspace and (provider != 'anthropic' or not _WORKSPACE.match(workspace)):
            raise ValidationError(['id del workspace non valido: inizia per wrkspc_ (console Claude, Settings > Workspaces)'])
        self.secrets.reload()
        models = verify_key(provider, _Candidate(self.secrets, provider, key, workspace),
                            version=self.store.load()['api_version'])
        updates = {PROVIDERS[provider]['key_env'][0]: key}
        if provider == 'anthropic':
            updates[PROVIDERS[provider]['workspace_env'][0]] = workspace
        with self.lock:
            update_env_file(self.secrets.env_file, updates)
            self.secrets.reload()
            models.sort(key=lambda m: m['name'].lower())
            self._catalog[provider] = (time.monotonic(), models)
            self.build()
        suggestion = recommended(provider, models)
        self.last_connect = {'ok': True, 'provider': provider, 'via': via, 'at': _now(),
                             'message': f'{PROVIDERS[provider]["label"]} collegato: {len(models)} modelli disponibili.'}
        return {'provider': provider, 'models': len(models), 'recommended': suggestion,
                'recommended_name': next((m['name'] for m in models if m['id'] == suggestion), suggestion)}

    def disconnect(self, provider):
        """Removes the provider's key (and workspace) from the instance .env file."""
        self._writable(provider)
        names = PROVIDERS[provider]['key_env'] + PROVIDERS[provider]['workspace_env']
        with self.lock:
            update_env_file(self.secrets.env_file, dict.fromkeys(names))
            self.secrets.reload()
            self._catalog.pop(provider, None)
            self.build()
        self.last_connect = None

    def openrouter_start(self, callback_url, actor) -> str:
        """OAuth PKCE, step 1: the address of the OpenRouter page where the user authorizes a key."""
        self._writable('openrouter')
        if self.secrets.value('OPENROUTER_URL'):
            raise ValidationError(['con un gateway (OPENROUTER_URL) l\'accesso con un clic non serve: incolla la chiave del gateway'])
        verifier, state = token_urlsafe(48), token_urlsafe(24)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        with self.lock:
            now = time.monotonic()
            self._oauth = {k: v for k, v in self._oauth.items() if v[2] > now}
            while len(self._oauth) >= 16:
                self._oauth.pop(next(iter(self._oauth)))
            self._oauth[state] = (verifier, actor, now + OAUTH_SECONDS)
        return OPENROUTER_AUTH + '?' + urlencode({'callback_url': callback_url, 'code_challenge': challenge,
                                                 'code_challenge_method': 'S256', 'state': state,
                                                 'key_label': 'Planning Factory'})

    def openrouter_finish(self, code, state):
        """OAuth PKCE, step 2: a single-use state started by an administrator turns the code into a stored key.

        Returns the administrator who started the login and the connect result.
        """
        with self.lock:
            entry = self._oauth.pop(state, None) if isinstance(state, str) else None
        try:
            if entry is None or entry[2] <= time.monotonic():
                raise ValidationError(['Accesso OpenRouter scaduto o non riconosciuto: ripeti "Accedi con OpenRouter".'])
            if not isinstance(code, str) or not re.fullmatch(r'[A-Za-z0-9_\-.]{8,512}', code):
                raise ValidationError(['Accesso OpenRouter annullato: nessuna chiave è stata creata.'])
            return entry[1], self.connect('openrouter', exchange_openrouter_code(code, entry[0]), via='accesso OpenRouter')
        except (ValidationError, ProviderError) as exc:
            message = '; '.join(exc.errors) if isinstance(exc, ValidationError) else str(exc)
            self.last_connect = {'ok': False, 'provider': 'openrouter', 'via': 'accesso OpenRouter', 'at': _now(), 'message': message}
            raise

    def save(self, patch, actor):
        if self.environment_locked:
            raise ValidationError(['AI configurata da variabili PLANNING_CLAUDE_*: modificale sul server e riavvia'])
        with self.lock:
            self.store.save(patch, actor)
            self.build()
        return self.status(actor)

    def _subscription(self):
        if self.subscription is None:
            raise ValidationError(['l\'abbonamento Claude si usa solo con l\'accesso locale (--local-user) sul proprio PC'])
        return self.subscription

    def subscription_login(self):
        return self._subscription().start_login()

    def subscription_code(self, code):
        self._subscription().submit_code(code)
        with self.lock:
            self.build()
        self.last_connect = {'ok': True, 'provider': SUBSCRIPTION, 'via': 'accesso claude.ai', 'at': _now(),
                             'message': 'Abbonamento Claude collegato.'}

    def subscription_logout(self):
        self._subscription().logout()
        with self.lock:
            self.build()

    def models(self, provider):
        if provider == SUBSCRIPTION:
            self._subscription()
            return [dict(m) for m in SUBSCRIPTION_MODELS]
        if provider not in PROVIDERS:
            raise ValidationError(['fornitore non supportato'])
        self.secrets.reload()
        cached = self._catalog.get(provider)
        if cached and time.monotonic() - cached[0] < 600:
            return cached[1]
        models = list_models(provider, self.secrets, version=self.store.load()['api_version'])
        models.sort(key=lambda m: m['name'].lower())
        self._catalog[provider] = (time.monotonic(), models)
        return models

    def suggestion(self, provider, models):
        if provider == SUBSCRIPTION:
            return 'sonnet'
        return recommended(provider, models) if self.secrets.key(provider) else None

    def test(self, provider, model):
        if provider not in (*PROVIDERS, SUBSCRIPTION) or not isinstance(model, str) or not _MODEL.match(model):
            raise ValidationError(['fornitore e modello validi richiesti'])
        if provider == SUBSCRIPTION:
            return probe(provider, model, None, send=self._subscription().transport())
        self.secrets.reload()
        return probe(provider, model, self.secrets, version=self.store.load()['api_version'])
