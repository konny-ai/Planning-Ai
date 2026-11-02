"""Model providers for the optional assistant: Anthropic Messages API and OpenRouter.

The assistant speaks one language, Anthropic-style content blocks (text, tool_use,
tool_result); the OpenRouter transport translates them to and from the OpenAI-compatible
chat completions format. Keys come from the installation secrets on the server and are
never logged, returned to the browser or forwarded on a redirect.
"""
from datetime import datetime
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

ANTHROPIC_URL = 'https://api.anthropic.com/v1'
OPENROUTER_URL = 'https://openrouter.ai/api/v1'
DEFAULT_ANTHROPIC_VERSION = '2023-06-01'
MAX_REQUEST = 256000
MAX_RESPONSE = 1024 * 1024
MAX_CATALOG = 8 * 1024 * 1024

PROVIDERS = {
    'anthropic': {'label': 'Anthropic', 'key_env': ('ANTHROPIC_API_KEY', 'PLANNING_CLAUDE_API_KEY'),
                  'workspace_env': ('ANTHROPIC_WORKSPACE_ID', 'PLANNING_CLAUDE_WORKSPACE'),
                  'keys_page': 'https://platform.claude.com/settings/keys',
                  'credit_page': 'https://platform.claude.com/settings/billing'},
    'openrouter': {'label': 'OpenRouter', 'key_env': ('OPENROUTER_API_KEY',), 'workspace_env': (),
                   'keys_page': 'https://openrouter.ai/settings/keys',
                   'credit_page': 'https://openrouter.ai/settings/credits'},
}
OPENROUTER_AUTH = 'https://openrouter.ai/auth'


class ProviderError(RuntimeError):
    """A provider failure already explained in Italian for the planner."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        # Never forward customer authorization to a different endpoint.
        raise HTTPError(request.full_url, code, 'redirect refused', headers, fp)


def explain(provider: str, code: int | None, message: str = '', model: str | None = None) -> str:
    label = PROVIDERS.get(provider, {}).get('label', provider)
    variable = PROVIDERS.get(provider, {}).get('key_env', ('la chiave',))[0]
    text = (message or '').lower()
    if 'workspace' in text and 'required' in text:
        return ("La chiave Anthropic è legata a un'identità e richiede il workspace: ricollegala in Impostazioni AI "
                "indicando anche l'id del workspace (console Claude, Settings > Workspaces).")
    if 'workspace' in text and ('valid' in text or 'invalid' in text):
        return ("L'id del workspace Anthropic non è valido: copialo dalla console Claude, "
                "Settings > Workspaces (inizia per wrkspc_).")
    if code == 401 or 'authentication' in text or 'api-key' in text or 'api key' in text:
        return f'Chiave {label} rifiutata ({variable}): ricollegane una valida in Impostazioni AI.'
    if 'credit balance' in text:
        return (f'Credito {label} esaurito: la chiave è corretta ma il conto non ha credito. '
                'Ricaricalo dalla console del fornitore oppure sposta il ruolo su un altro fornitore.')
    if code == 402 or 'insufficient' in text or 'quota' in text:
        return f'Credito o quota esauriti su {label}.'
    if code == 429 or 'rate limit' in text or 'rate_limit' in text:
        return f'{label} è momentaneamente saturo (429): riprova fra un minuto o scegli un altro modello.'
    if code == 404 or ('model' in text and 'not' in text and 'found' in text) or 'no endpoints' in text:
        if 'tool' in text:
            return f'Il modello {model or ""} su {label} non supporta gli strumenti, che servono al pianificatore: scegline un altro.'.replace('  ', ' ')
        return f'Il modello {model or "selezionato"} non esiste su {label}: scegline un altro nelle Impostazioni AI.'
    if code == 400 and 'version' in text:
        return f'Versione API non accettata da {label}: ripristina quella predefinita nelle Impostazioni AI.'
    if code == 403:
        return f'{label} rifiuta la richiesta (403): la chiave non ha accesso a questo modello o all\'organizzazione.'
    if code is not None and code >= 500:
        return f'{label} ha un problema temporaneo ({code}): riprova più tardi. Il piano resta disponibile.'
    detail = (message or '').strip().replace('\n', ' ')[:200]
    return f'{label} ha risposto {code}' + (f': {detail}' if detail else '') if code else f'{label}: {detail or "errore sconosciuto"}'


def _call(provider, url, headers, payload=None, *, limit=MAX_RESPONSE, timeout=60, model=None, base=None):
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        if len(data) > MAX_REQUEST:
            raise ProviderError('Richiesta troppo grande per il limite configurato: restringi la domanda.')
    request = Request(url, data=data, headers=headers, method='POST' if payload is not None else 'GET')
    try:
        with build_opener(_NoRedirect()).open(request, timeout=timeout) as response:
            body = response.read(limit + 1)
    except HTTPError as exc:
        message = ''
        try:
            # Provider error text only (e.g. "credit balance is too low"); never logged.
            parsed = json.loads(exc.read(65536) or b'{}')
            error = parsed.get('error') if isinstance(parsed, dict) else None
            message = error.get('message', '') if isinstance(error, dict) else str(error or '')
        except (ValueError, OSError, AttributeError):
            pass
        raise ProviderError(explain(provider, exc.code, message, model)) from None
    except (URLError, TimeoutError, OSError):
        if base and base != OPENROUTER_URL and base != ANTHROPIC_URL:
            raise ProviderError(f'Il gateway {base} non risponde: avvialo (è quello indicato da OPENROUTER_URL) '
                                'e riprova. Il piano resta disponibile.') from None
        label = PROVIDERS[provider]['label']
        raise ProviderError(f'{label} non raggiungibile: controlla la connessione a internet o il proxy. '
                            'Il piano resta disponibile.') from None
    if len(body) > limit:
        raise ProviderError('Risposta del fornitore oltre il limite consentito.')
    try:
        value = json.loads(body)
    except ValueError:
        raise ProviderError('Risposta del fornitore non leggibile.') from None
    if isinstance(value, dict) and isinstance(value.get('error'), dict):
        # OpenRouter may answer 200 with an error object from the upstream provider.
        error = value['error']
        raise ProviderError(explain(provider, error.get('code') if type(error.get('code')) is int else None,
                                    str(error.get('message', '')), model))
    return value


def anthropic_headers(key, version=DEFAULT_ANTHROPIC_VERSION, workspace=None):
    headers = {'x-api-key': key, 'anthropic-version': version or DEFAULT_ANTHROPIC_VERSION,
               'Content-Type': 'application/json'}
    if workspace:
        headers['anthropic-workspace-id'] = workspace
    return headers


def anthropic_transport(key, *, version=DEFAULT_ANTHROPIC_VERSION, workspace=None):
    headers = anthropic_headers(key, version, workspace)

    def send(payload):
        return _call('anthropic', ANTHROPIC_URL + '/messages', headers, payload, model=payload.get('model'))
    return send


def openrouter_headers(key):
    return {'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
            'HTTP-Referer': 'http://localhost', 'X-Title': 'Planning Factory'}


def to_openai(payload: dict) -> dict:
    """Anthropic-style request -> OpenAI chat completions request."""
    messages = []
    if payload.get('system'):
        messages.append({'role': 'system', 'content': payload['system']})
    for message in payload.get('messages', []):
        content = message.get('content')
        if isinstance(content, str):
            messages.append({'role': message['role'], 'content': content})
            continue
        if message['role'] == 'assistant':
            text = '\n'.join(b.get('text', '') for b in content if b.get('type') == 'text')
            calls = [{'id': b['id'], 'type': 'function',
                      'function': {'name': b['name'], 'arguments': json.dumps(b.get('input') or {}, ensure_ascii=False)}}
                     for b in content if b.get('type') == 'tool_use']
            item = {'role': 'assistant', 'content': text or None}
            if calls:
                item['tool_calls'] = calls
            messages.append(item)
            continue
        texts = []
        for block in content:
            if block.get('type') == 'tool_result':
                value = block.get('content')
                messages.append({'role': 'tool', 'tool_call_id': block['tool_use_id'],
                                 'content': value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)})
            elif block.get('type') == 'text':
                texts.append(block.get('text', ''))
        if texts:
            messages.append({'role': 'user', 'content': '\n'.join(texts)})
    request = {'model': payload['model'], 'max_tokens': payload.get('max_tokens', 1024), 'messages': messages}
    if payload.get('tools'):
        request['tools'] = [{'type': 'function', 'function': {'name': t['name'], 'description': t.get('description', ''),
                                                               'parameters': t.get('input_schema', {'type': 'object'})}}
                            for t in payload['tools']]
    choice = payload.get('tool_choice')
    if isinstance(choice, dict) and choice.get('type') == 'tool':
        request['tool_choice'] = {'type': 'function', 'function': {'name': choice['name']}}
    elif isinstance(choice, dict) and choice.get('type') == 'any':
        request['tool_choice'] = 'required'
    return request


def from_openai(response) -> dict:
    """OpenAI chat completions response -> Anthropic-style response."""
    if not isinstance(response, dict) or not isinstance(response.get('choices'), list) or not response['choices']:
        raise ProviderError('Risposta OpenRouter senza contenuto.')
    message = response['choices'][0].get('message') or {}
    content = []
    if isinstance(message.get('content'), str) and message['content'].strip():
        content.append({'type': 'text', 'text': message['content']})
    for call in message.get('tool_calls') or []:
        function = call.get('function') or {}
        try:
            arguments = json.loads(function.get('arguments') or '{}')
        except ValueError:
            arguments = None
        content.append({'type': 'tool_use', 'id': call.get('id'), 'name': function.get('name'), 'input': arguments})
    usage = response.get('usage') if isinstance(response.get('usage'), dict) else {}
    return {'content': content, 'usage': {'input_tokens': usage.get('prompt_tokens', 0) if type(usage.get('prompt_tokens')) is int else 0,
                                          'output_tokens': usage.get('completion_tokens', 0) if type(usage.get('completion_tokens')) is int else 0}}


def openrouter_base(secrets) -> str:
    """OpenRouter, or an OpenAI-compatible gateway named by OPENROUTER_URL (as in a customer installation).

    Plain http is accepted only towards this PC: a key must never travel unencrypted on a network.
    """
    url = (secrets.value('OPENROUTER_URL') or OPENROUTER_URL).strip().rstrip('/')
    parts = urlsplit(url)
    local = (parts.hostname or '') in ('127.0.0.1', 'localhost', '::1')
    if parts.scheme not in ('http', 'https') or not parts.hostname or (parts.scheme == 'http' and not local) \
            or parts.username or parts.password or parts.query or parts.fragment:
        raise ProviderError('OPENROUTER_URL non valido: serve https, oppure http solo verso un gateway su questo PC.')
    return url


def openrouter_transport(key, base=OPENROUTER_URL):
    headers = openrouter_headers(key)

    def send(payload):
        return from_openai(_call('openrouter', base + '/chat/completions', headers, to_openai(payload),
                                 model=payload.get('model'), base=base))
    return send


def transport_for(provider, secrets, *, version=DEFAULT_ANTHROPIC_VERSION):
    key = secrets.key(provider)
    if not key:
        raise ProviderError(f'Manca la chiave {PROVIDERS[provider]["label"]}: collegala in Impostazioni AI.')
    if provider == 'anthropic':
        return anthropic_transport(key, version=version, workspace=secrets.workspace(provider))
    return openrouter_transport(key, openrouter_base(secrets))


def _price(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number * 1_000_000, 4) if number >= 0 else None


def list_models(provider, secrets, *, version=DEFAULT_ANTHROPIC_VERSION) -> list[dict]:
    """The provider's catalog, reduced to what the settings page needs."""
    if provider == 'anthropic':
        key = secrets.key('anthropic')
        if not key:
            raise ProviderError('Per leggere il catalogo Anthropic collega prima la chiave in Impostazioni AI.')
        value = _call('anthropic', ANTHROPIC_URL + '/models?limit=100',
                      anthropic_headers(key, version, secrets.workspace('anthropic')), limit=MAX_CATALOG, timeout=30)
        return [{'id': m.get('id'), 'name': m.get('display_name') or m.get('id'), 'context': None,
                 'tools': True, 'images': True, 'price_in': None, 'price_out': None, 'free': False,
                 'created': m.get('created_at')}
                for m in value.get('data', []) if isinstance(m, dict) and isinstance(m.get('id'), str)]
    if provider == 'openrouter':
        key = secrets.key('openrouter')
        base = openrouter_base(secrets)
        headers = {'Authorization': 'Bearer ' + key} if key else {}
        value = _call('openrouter', base + '/models', headers, limit=MAX_CATALOG, timeout=30, base=base)
        models = []
        for m in value.get('data', []):
            if not isinstance(m, dict) or not isinstance(m.get('id'), str):
                continue
            pricing = m.get('pricing') or {}
            params = m.get('supported_parameters')
            inputs = (m.get('architecture') or {}).get('input_modalities') or []
            price_in, price_out = _price(pricing.get('prompt')), _price(pricing.get('completion'))
            # A gateway (e.g. LiteLLM) lists aliases without capabilities: tool support is unknown, not absent.
            models.append({'id': m['id'], 'name': m.get('name') or m['id'], 'context': m.get('context_length'),
                           'tools': ('tools' in params) if isinstance(params, list) else None, 'images': 'image' in inputs,
                           'price_in': price_in, 'price_out': price_out,
                           'free': price_in == 0 and price_out == 0, 'created': m.get('created')})
        return models
    raise ProviderError(f'Fornitore {provider!r} non supportato.')


def verify_key(provider, secrets, *, version=DEFAULT_ANTHROPIC_VERSION) -> list[dict]:
    """Proves a key before it is stored and returns the catalog it opens.

    The OpenRouter catalog is public, so the key is checked on /key; a gateway checks it on /models.
    """
    if provider == 'openrouter' and openrouter_base(secrets) == OPENROUTER_URL:
        info = _call('openrouter', OPENROUTER_URL + '/key', openrouter_headers(secrets.key('openrouter')), timeout=30)
        if not isinstance(info, dict) or not isinstance(info.get('data'), dict):
            raise ProviderError('OpenRouter non ha riconosciuto la chiave.')
    return list_models(provider, secrets, version=version)


def _stamp(model) -> float:
    value = model.get('created')
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except ValueError:
        return 0.0


def recommended(provider, models) -> str | None:
    """What the guided setup proposes for the assistant: the newest Claude Sonnet with tools.

    Only a suggestion shown next to its name: the administrator confirms it before anything is saved.
    """
    usable = [m for m in models if m.get('tools') is not False and isinstance(m.get('id'), str)]
    claude = [m for m in usable if 'claude' in m['id'].lower() and (provider == 'anthropic' or m['id'].startswith('anthropic/'))]
    pool = [m for m in claude if 'sonnet' in m['id'].lower()] or claude or usable
    return max(pool, key=_stamp)['id'] if pool else None


def exchange_openrouter_code(code, verifier) -> str:
    """OAuth PKCE, step 2: the code from the callback becomes the user's own OpenRouter key."""
    try:
        value = _call('openrouter', OPENROUTER_URL + '/auth/keys', {'Content-Type': 'application/json'},
                      {'code': code, 'code_verifier': verifier, 'code_challenge_method': 'S256'}, timeout=30)
    except ProviderError as exc:
        raise ProviderError(f'Accesso OpenRouter non completato ({exc}): ripeti "Accedi con OpenRouter" '
                            '(il codice vale 10 minuti e una sola volta).') from None
    key = value.get('key') if isinstance(value, dict) else None
    if not isinstance(key, str) or not key.strip():
        raise ProviderError('OpenRouter non ha restituito la chiave: ripeti l\'accesso.')
    return key.strip()


def probe(provider, model, secrets, *, version=DEFAULT_ANTHROPIC_VERSION, send=None) -> dict:
    """One short call with a tool declared: proves key, credit, model and tool support."""
    send = send or transport_for(provider, secrets, version=version)
    started = time.monotonic()
    response = send({'model': model, 'max_tokens': 32,
                     'system': 'Rispondi solo con la parola OK.',
                     'messages': [{'role': 'user', 'content': 'Verifica di collegamento: rispondi OK.'}],
                     'tools': [{'name': 'verifica', 'description': 'Strumento di prova, non usarlo.',
                                'input_schema': {'type': 'object', 'properties': {}}}]})
    blocks = response.get('content') if isinstance(response, dict) else None
    if not isinstance(blocks, list):
        raise ProviderError('Risposta del modello non valida.')
    text = ' '.join(b.get('text', '') for b in blocks if isinstance(b, dict) and b.get('type') == 'text').strip()
    return {'ok': True, 'provider': provider, 'model': model,
            'latency_ms': int((time.monotonic() - started) * 1000), 'reply': text[:80]}
