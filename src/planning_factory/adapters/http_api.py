"""Loopback-only development API. No production authentication or ERP writes."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from .json_io import dumps, loads, plan_loads, scenario_from_dict
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.calendars import expand
from planning_factory.engine.scheduler import plan
from planning_factory.engine.optimization import optimize
from planning_factory.engine.verification import verify_plan
from planning_factory.engine.setup_audit import load_setup_rules
from planning_factory.engine.setup_planner import plan_with_setups
from planning_factory.engine.setup_search import search_setup_alternatives

MAX_BODY = 8 * 1024 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = 'PlanningFactory/0.2'

    def respond(self, status, payload):
        content = dumps(payload).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(content)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(content)

    def reject(self, status, payload):
        """Answer before reading the request: drain an acceptable body first, otherwise
        closing the socket with unread data resets the connection (seen on Windows) and
        the client gets an abort instead of the status."""
        try:
            length = int(self.headers.get('Content-Length', '0'))
        except ValueError:
            length = 0
        if not self.headers.get('Transfer-Encoding') and 0 < length <= MAX_BODY:
            self.connection.settimeout(15)
            self.rfile.read(length)
        self.close_connection = True
        self.respond(status, payload)

    def do_GET(self):
        if self.path == '/health':
            self.respond(200, {'status': 'ok', 'engine_version': '0.2.0'})
        else:
            self.respond(404, {'error': 'not found'})

    def do_POST(self):
        # Reject browser-initiated cross-origin requests to the local planner.
        if self.headers.get('Origin') is not None:
            self.reject(403, {'error': 'browser origins are not accepted by this local API'})
            return
        if self.path not in ('/v1/plan', '/v1/validate', '/v1/verify', '/v1/optimize', '/v1/setup-plan', '/v1/setup-search'):
            self.reject(404, {'error': 'not found'})
            return
        try:
            if self.headers.get_content_type() != 'application/json':
                self.reject(415, {'error': 'application/json required'})
                return
            if self.headers.get('Transfer-Encoding'):
                self.reject(400, {'error': 'chunked requests unsupported'})
                return
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                self.reject(413, {'error': 'body must be between 1 byte and 8 MiB'})
                return
            self.connection.settimeout(15)
            text = self.rfile.read(length).decode('utf-8')
            if self.path == '/v1/setup-search':
                from .json_io import _no_duplicates
                data = json.loads(text, object_pairs_hook=_no_duplicates,
                                  parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
                allowed = {'scenario', 'rules', 'budget', 'repair_budget'}
                if not isinstance(data, dict) or set(data) - allowed or not {'scenario', 'rules'} <= set(data):
                    raise ValidationError(['setup-search requires scenario and rules, with optional budgets'])
                budget, repair_budget = data.get('budget', 8), data.get('repair_budget', 12)
                if type(budget) is not int or not 1 <= budget <= 32:
                    raise ValidationError(['budget must be an integer between 1 and 32'])
                if type(repair_budget) is not int or not 1 <= repair_budget <= 32:
                    raise ValidationError(['repair_budget must be an integer between 1 and 32'])
                s = scenario_from_dict(data['scenario'])
                rules = load_setup_rules(json.dumps(data['rules'], allow_nan=False), s)
                result = search_setup_alternatives(s, rules, budget=budget, repair_budget=repair_budget)
                errors = verify_plan(result.scenario, result.plan)
                if errors or not result.setup_audit.valid:
                    self.respond(422, {'error': 'setup search candidate failed independent audit', 'errors': errors,
                                       'setup_audit': result.setup_audit.to_dict()})
                else:
                    self.respond(200, {'scenario': result.scenario, 'plan': result.plan,
                                       'candidates_evaluated': result.candidates_evaluated,
                                       'budget': result.budget, 'winner_order': result.winner_order,
                                       'rejected': result.rejected, 'setup_audit': result.setup_audit.to_dict()})
            elif self.path == '/v1/setup-plan':
                from .json_io import _no_duplicates
                data = json.loads(text, object_pairs_hook=_no_duplicates,
                                  parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
                if not isinstance(data, dict) or set(data) - {'scenario', 'rules', 'budget'} or not {'scenario', 'rules'} <= set(data):
                    raise ValidationError(['setup-plan requires scenario and rules, with optional budget'])
                budget = data.get('budget', 12)
                if type(budget) is not int or not 1 <= budget <= 32:
                    raise ValidationError(['budget must be an integer between 1 and 32'])
                s = scenario_from_dict(data['scenario'])
                rules_text = json.dumps(data['rules'], allow_nan=False)
                rules = load_setup_rules(rules_text, s)
                result = plan_with_setups(s, rules, budget=budget)
                errors = verify_plan(result.scenario, result.plan)
                if errors or not result.setup_audit.valid:
                    self.respond(422, {'error': 'setup-aware plan failed independent audit', 'errors': errors,
                                        'setup_audit': result.setup_audit.to_dict()})
                else:
                    self.respond(200, {'scenario': result.scenario, 'plan': result.plan,
                                       'iterations': result.iterations, 'budget': result.budget,
                                       'setup_audit': result.setup_audit.to_dict()})
            elif self.path == '/v1/verify':
                from .json_io import _no_duplicates
                data = json.loads(text, object_pairs_hook=_no_duplicates)
                if not isinstance(data, dict) or set(data) != {'scenario', 'plan'}:
                    raise ValidationError(['verify requires exactly scenario and plan'])
                s = scenario_from_dict(data['scenario'])
                p = plan_loads(json.dumps(data['plan'], allow_nan=False))
                errors = verify_plan(s, p)
                self.respond(422 if errors else 200, {'valid': not errors, 'errors': errors})
            else:
                s = loads(text)
                if self.path == '/v1/validate':
                    for c in s.calendars:
                        expand(c, s.start, s.end)
                    self.respond(200, {'valid': True})
                elif self.path == '/v1/optimize':
                    self.respond(200, optimize(s))
                else:
                    self.respond(200, plan(s))  # partial plans carry explicit unscheduled entries
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self.respond(422, {'error': str(exc)})
        except (OSError, TimeoutError):
            self.respond(408, {'error': 'request read timed out'})

    def log_message(self, format, *args):
        pass  # Do not log manufacturing payloads or caller-controlled request paths.


class LocalServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16


def make_server(host='127.0.0.1', port=8765):
    if host != '127.0.0.1':
        raise ValueError('development API must bind to 127.0.0.1')
    return LocalServer((host, port), Handler)


def serve(port=8765):
    with make_server(port=port) as server:
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
