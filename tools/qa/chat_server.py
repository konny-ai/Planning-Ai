"""Browser QA of the conversational assistant (optional, not part of run_free_ci).

Starts the plain `web` dashboard (no AI) on 8766 and a local-user `secure-web` with a
SIMULATED provider on 8767, both on the anonymized banco A fixture. No network call,
no key, no Factory access. Usage:

    PYTHONPATH=src python tools/qa/chat_server.py out/qa-chat-01 &
    node tools/qa/chat_browser.js out/qa-chat-01      # needs Node + Playwright with Chromium

With --claude-cli the 8767 instance uses the developer's OWN Claude subscription through the local
`claude` CLI (tools/qa/claude_cli_transport.py) instead of the simulated provider. Personal test only:
never for an installation used by other people, which needs the customer's API key.

    PYTHONPATH=src python tools/qa/chat_server.py out/qa-sub-01 --claude-cli [--token-file F] [--config-dir D] [--model M]
"""
import argparse, json, sys, threading
from pathlib import Path
from planning_factory.adapters.dashboard_api import DEFAULT_FIXTURE, make_dashboard_server
from planning_factory.adapters.secure_dashboard import make_secure_dashboard_server
from planning_factory.adapters.claude_assistant import CallQuota, ClaudeAssistant

parser = argparse.ArgumentParser()
parser.add_argument('out')
parser.add_argument('--claude-cli', action='store_true', help='use your own subscription via the claude CLI')
parser.add_argument('--token-file', help='file with a CLAUDE_CODE_OAUTH_TOKEN from `claude setup-token`')
parser.add_argument('--config-dir', help='CLAUDE_CONFIG_DIR for the CLI (isolates it from other logins)')
parser.add_argument('--model', help='model for the CLI; default: the subscription default')
args = parser.parse_args()
work = Path(args.out) / 'instance'
work.mkdir(parents=True, exist_ok=True)
plain = make_dashboard_server(DEFAULT_FIXTURE, port=8766)
secure = make_secure_dashboard_server(DEFAULT_FIXTURE, None, work / 'plans.sqlite', work / 'audit.jsonl',
                                      port=8767, local_user='qa')
app = secure.application
calls = []

def provider(payload):
    """Simulated model: explains with explain_order, then proposes a downtime on the first machine."""
    calls.append(payload)
    last = payload['messages'][-1]
    question = last['content'] if isinstance(last['content'], str) else ''
    if isinstance(last['content'], list):
        results = [json.loads(b['content']) for b in last['content'] if b.get('type') == 'tool_result' and not b.get('is_error')]
        chain = next((r for r in results if 'critical_path' in r), None)
        text = ('Secondo il percorso critico: ' + chain['summary']) if chain else 'Ho preparato una proposta di simulazione.'
        return {'content': [{'type': 'text', 'text': '**Analisi**\n' + text + '\n- verificare la proposta prima di applicarla'}],
                'usage': {'input_tokens': 10, 'output_tokens': 10}}
    if 'fermo' in question.lower():
        machine = next(r['id'] for r in json.loads(open(DEFAULT_FIXTURE).read())['resources'] if r.get('kind', 'machine') == 'machine')
        return {'content': [{'type': 'tool_use', 'id': 'p1', 'name': 'propose_simulation', 'input': {
            'title': 'Fermo ' + machine, 'edits': {'fixed_tasks': [], 'closures': [
                {'resource_id': machine, 'start': '2026-10-05T06:00:00Z', 'end': '2026-10-05T10:00:00Z', 'reason': 'manutenzione QA'}]}}}]}
    order = next((w.strip('?.,') for w in question.split() if w.startswith('ORD')), 'ORD011')
    return {'content': [{'type': 'tool_use', 'id': 't1', 'name': 'explain_order', 'input': {'order_id': order}}]}

if args.claude_cli:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from claude_cli_transport import claude_cli_transport
    transport = claude_cli_transport(model=args.model, token_file=args.token_file, config_dir=args.config_dir)
    app.assistant = ClaudeAssistant('subscription-via-cli', args.model or 'abbonamento Claude (CLI)',
                                    CallQuota(work / 'quota.db', 60, 60), transport=transport, provider='claude-cli')
else:
    app.assistant = ClaudeAssistant('qa-key', 'simulated-model', CallQuota(work / 'quota.db', 200, 200), transport=provider)
for server in (plain, secure):
    threading.Thread(target=server.serve_forever, daemon=True).start()
print('ready', flush=True)
threading.Event().wait()
