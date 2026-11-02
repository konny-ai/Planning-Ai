"""Personal Claude subscription through the local `claude` CLI (Claude Code), for the single local user only.

Anthropic allows a Pro/Max subscription for the subscriber's own use, in its apps and in Claude Code.
This provider exists only when the Control Room runs with ``--local-user`` on loopback: one person, on
their own PC, using their own subscription. An installation used by other people needs an API key.

The CLI keeps its login in a folder of the instance (``CLAUDE_CONFIG_DIR``), separate from the user's own
Claude Code settings; logging out removes it. The transport speaks the same Anthropic-style payload as the
API transports: conversation, tool catalogue and tool results are rendered as text and the CLI returns
the next turn as structured output (``--json-schema``). Claude Code's own tools and MCP servers are off.
"""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

from planning_factory.adapters.llm_providers import ProviderError

PROVIDER = 'abbonamento'
LABEL = 'Abbonamento Claude'
# Aliases resolved by the CLI to the newest model of each family.
MODELS = ({'id': 'sonnet', 'name': 'Claude Sonnet (il più recente)', 'context': None, 'tools': True, 'images': True,
           'price_in': None, 'price_out': None, 'free': False, 'created': None},
          {'id': 'opus', 'name': 'Claude Opus (il più recente)', 'context': None, 'tools': True, 'images': True,
           'price_in': None, 'price_out': None, 'free': False, 'created': None},
          {'id': 'haiku', 'name': 'Claude Haiku (il più recente)', 'context': None, 'tools': True, 'images': True,
           'price_in': None, 'price_out': None, 'free': False, 'created': None})
_URL = re.compile(r'https://\S+/oauth/authorize\?\S+')
_CODE = re.compile(r'^[A-Za-z0-9_\-#.~]{8,1024}$')
LOGIN_SECONDS = 900

TURN = {'type': 'object', 'additionalProperties': False, 'required': ['text', 'tool_calls'],
        'properties': {'text': {'type': 'string'},
                       'tool_calls': {'type': 'array', 'maxItems': 6, 'items': {
                           'type': 'object', 'additionalProperties': False, 'required': ['name', 'input'],
                           'properties': {'name': {'type': 'string'}, 'input': {'type': 'object'}}}}}}


def _render(payload):
    lines = []
    for message in payload.get('messages', []):
        content = message.get('content')
        who = 'UTENTE' if message['role'] == 'user' else 'ASSISTENTE'
        if isinstance(content, str):
            lines.append(f'{who}: {content}')
            continue
        for block in content:
            if block.get('type') == 'text':
                lines.append(f'{who}: {block.get("text", "")}')
            elif block.get('type') == 'tool_use':
                lines.append(f'ASSISTENTE chiama {block["name"]} {json.dumps(block.get("input") or {}, ensure_ascii=False)}')
            elif block.get('type') == 'tool_result':
                status = 'ERRORE' if block.get('is_error') else 'RISULTATO'
                lines.append(f'{status} strumento ({block["tool_use_id"]}): {block.get("content")}')
    return '\n'.join(lines)


def _system(payload):
    tools = payload.get('tools') or []
    catalog = '\n'.join(f'- {t["name"]}: {t.get("description", "")} Argomenti (JSON Schema): '
                        f'{json.dumps(t.get("input_schema", {}), ensure_ascii=False)}' for t in tools)
    choice = payload.get('tool_choice')
    forced = (f'\nDevi rispondere chiamando esattamente una volta lo strumento {choice["name"]} e lasciare text vuoto.'
              if isinstance(choice, dict) and choice.get('type') == 'tool' else '')
    return (str(payload.get('system') or '') +
            '\n\nPROTOCOLLO: rispondi solo con l\'oggetto JSON richiesto. Per usare strumenti elencali in tool_calls '
            '(nome e argomenti) e lascia text vuoto: riceverai i risultati nel turno successivo. Quando hai la '
            'risposta finale per l\'utente mettila in text e lascia tool_calls vuoto. Strumenti disponibili:\n' +
            catalog + forced)


def cli_environment(config_dir=None, token_file=None):
    """The CLI must use only the subscription login: no API key or token inherited from the server."""
    env = {k: v for k, v in os.environ.items()
           if k not in ('ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'CLAUDE_CODE_OAUTH_TOKEN', 'CLAUDECODE')}
    if token_file:
        env['CLAUDE_CODE_OAUTH_TOKEN'] = Path(token_file).read_text(encoding='utf-8').strip()
    if config_dir:
        env['CLAUDE_CONFIG_DIR'] = str(config_dir)
    return env


def claude_cli_transport(*, executable='claude', model=None, token_file=None, config_dir=None, timeout=240):
    exe = shutil.which(executable)
    if exe is None:
        raise ProviderError('CLI claude non trovata: installare Claude Code e accedere con il proprio abbonamento.')
    env = cli_environment(config_dir, token_file)
    calls = 0

    def send(payload):
        nonlocal calls
        with tempfile.TemporaryDirectory(prefix='pf_claude_cli_') as cwd:
            system = Path(cwd) / 'system.txt'
            system.write_text(_system(payload), encoding='utf-8')
            command = [exe, '-p', '--output-format', 'json', '--max-turns', '3', '--tools', '', '--strict-mcp-config',
                       '--system-prompt-file', str(system), '--json-schema', json.dumps(TURN)]
            if payload.get('model') or model:
                command += ['--model', payload.get('model') or model]
            try:
                done = subprocess.run(command, input=_render(payload), capture_output=True, text=True,
                                      encoding='utf-8', timeout=timeout, cwd=cwd, env=env)
            except subprocess.TimeoutExpired:
                raise ProviderError('La CLI claude non ha risposto in tempo.') from None
        try:
            result = json.loads(done.stdout)
        except ValueError:
            raise ProviderError('Risposta della CLI claude non leggibile: ' + (done.stderr or done.stdout)[:200]) from None
        if result.get('is_error') or not isinstance(result.get('structured_output'), dict):
            message = str(result.get('result'))
            if 'login' in message.lower() or 'auth' in message.lower():
                raise ProviderError('Abbonamento Claude non collegato: accedi da Impostazioni AI.')
            raise ProviderError('La CLI claude ha restituito un errore: ' + message[:200])
        turn = result['structured_output']
        content = [{'type': 'text', 'text': turn['text']}] if turn.get('text') else []
        for call in turn.get('tool_calls') or []:
            calls += 1
            content.append({'type': 'tool_use', 'id': f'cli_{calls}', 'name': call.get('name'), 'input': call.get('input')})
        usage = result.get('usage') or {}
        return {'content': content, 'usage': {'input_tokens': int(usage.get('input_tokens') or 0),
                                              'output_tokens': int(usage.get('output_tokens') or 0)},
                'model': next(iter(result.get('modelUsage') or {}), model)}
    return send


def _close(process):
    for stream in (process.stdin, process.stdout):
        try:
            stream.close()
        except (OSError, ValueError, AttributeError):
            pass


class Subscription:
    """Login state and guided login of the instance's own `claude` CLI configuration."""

    def __init__(self, config_dir, executable='claude'):
        self.config_dir = Path(config_dir)
        self.executable = executable
        self.lock = threading.Lock()
        self._login = None          # (process, output lines, started)
        self._cache = (0.0, None)

    def exe(self):
        return shutil.which(self.executable)

    def _run(self, *args, timeout=30):
        return subprocess.run([self.exe(), *args], capture_output=True, text=True, encoding='utf-8', errors='replace',
                              timeout=timeout, env=cli_environment(self.config_dir), stdin=subprocess.DEVNULL)

    def status(self, fresh=False) -> dict:
        if self.exe() is None:
            return {'available': False, 'logged_in': False, 'login_running': False, 'login_url': None,
                    'problem': 'Claude Code non è installato su questo PC: installalo da claude.com/download e riprova.'}
        with self.lock:
            finished = self._login is not None and self._login[0].poll() == 0
            if finished:
                # signed in through the browser tab the CLI opened: the login is over
                _close(self._login[0])
                self._login = None
        stamp, cached = self._cache
        if fresh or finished or cached is None or time.monotonic() - stamp > 10:
            try:
                value = json.loads(self._run('auth', 'status', '--json').stdout or '{}')
            except (subprocess.TimeoutExpired, OSError, ValueError):
                value = {}
            cached = {'logged_in': bool(value.get('loggedIn')), 'method': value.get('authMethod'),
                      'subscription': value.get('subscriptionType')}
            self._cache = (time.monotonic(), cached)
        login = self._login_state()
        if cached['logged_in'] and login['running']:
            self._stop_login()
            login = self._login_state()
        return {'available': True, **cached, 'login_running': login['running'], 'login_url': login['url'],
                'login_error': login['error'], 'problem': None}

    def _login_state(self):
        with self.lock:
            if self._login is None:
                return {'running': False, 'url': None, 'error': None}
            process, lines, started = self._login
            running = process.poll() is None and time.monotonic() - started < LOGIN_SECONDS
            text = ''.join(lines)
            match = _URL.search(text)
            error = None
            if process.poll() not in (None, 0):
                error = 'Accesso non riuscito: ' + (text.strip().splitlines() or ['riprova'])[-1][:200]
            return {'running': running, 'url': match.group(0) if match and running else None, 'error': error}

    def _stop_login(self):
        with self.lock:
            if self._login and self._login[0].poll() is None:
                self._login[0].kill()
                self._login[0].wait(5)
            if self._login:
                _close(self._login[0])

    def start_login(self) -> str:
        """Starts `claude auth login` and returns the claude.ai page where the user signs in."""
        if self.exe() is None:
            raise ProviderError('Claude Code non è installato su questo PC: installalo da claude.com/download e riprova.')
        self._stop_login()
        self.config_dir.mkdir(parents=True, exist_ok=True)
        process = subprocess.Popen([self.exe(), 'auth', 'login', '--claudeai'], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8',
                                   errors='replace', env=cli_environment(self.config_dir))
        lines = []

        def read():
            try:
                for line in process.stdout:
                    lines.append(line)
            except (OSError, ValueError):
                pass  # pipe closed when the login ends
        threading.Thread(target=read, daemon=True).start()
        with self.lock:
            self._login = (process, lines, time.monotonic())
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = self._login_state()
            if state['url']:
                return state['url']
            if process.poll() is not None:
                break
            time.sleep(0.2)
        self._stop_login()
        raise ProviderError('Claude Code non ha proposto la pagina di accesso: riprova fra poco.')

    def submit_code(self, code):
        """The code shown by claude.ai after the authorization, typed back into the waiting CLI."""
        code = code.strip() if isinstance(code, str) else ''
        if not _CODE.match(code):
            raise ProviderError('Codice non valido: copia per intero il codice mostrato da claude.ai.')
        with self.lock:
            login = self._login
        if login is None or login[0].poll() is not None:
            raise ProviderError('Accesso non in corso: premi di nuovo "Accedi con il tuo abbonamento".')
        try:
            login[0].stdin.write(code + '\n')
            login[0].stdin.flush()
        except OSError:
            raise ProviderError('Claude Code non accetta il codice: riprova l\'accesso.') from None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and login[0].poll() is None:
            time.sleep(0.3)
        status = self.status(fresh=True)
        if not status['logged_in']:
            raise ProviderError(status.get('login_error') or 'Accesso non completato: il codice è scaduto o già usato, riprova.')
        return status

    def logout(self):
        self._stop_login()
        if self.exe() is not None:
            try:
                self._run('auth', 'logout')
            except (subprocess.TimeoutExpired, OSError):
                pass
        shutil.rmtree(self.config_dir, ignore_errors=True)
        self._cache = (0.0, None)

    def transport(self, model=None):
        return claude_cli_transport(executable=self.executable, model=model, config_dir=self.config_dir)
