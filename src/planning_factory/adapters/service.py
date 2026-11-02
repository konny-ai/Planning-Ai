"""Network service (#90): authenticated users, roles, isolated compute workers and an audit log.

- Users and roles live in a credentials file kept outside the repository; it stores only
  the SHA-256 of each token. Tokens travel as ``Authorization: Bearer``.
- Roles: ``reparto`` reads, ``planner`` saves scenarios and computes plans, ``admin`` also
  reads the audit log. No endpoint publishes to the ERP (#3).
- Every plan is computed in a separate process with a time limit; a bounded number of
  workers run at once and further requests wait in a short queue.
- Concurrent writers are serialized by the SnapshotStore revision check.
- Outside loopback the service requires TLS, unless explicitly declared behind a proxy.
"""
import hashlib
import hmac
import ipaddress
import json
import multiprocessing
import secrets
import ssl
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from planning_factory.domain.validation import ValidationError
from planning_factory.engine.verification import verify_plan
from .json_io import _no_duplicates, dumps, loads, plan_loads, scenario_digest, scenario_from_dict
from .store import ConflictError, SnapshotStore

ROLES = {'reparto': 1, 'planner': 2, 'admin': 3}
MAX_BODY = 8 * 1024 * 1024


def new_token(name: str, role: str) -> tuple[str, dict]:
    """A random token (shown once) and the credentials entry that stores only its hash."""
    if role not in ROLES or not isinstance(name, str) or not name.strip():
        raise ValidationError([f'name and a role among {list(ROLES)} required'])
    token = secrets.token_urlsafe(32)
    return token, {'name': name, 'role': role, 'token_sha256': hashlib.sha256(token.encode()).hexdigest()}


def load_credentials(path: Path) -> dict[str, tuple[str, str]]:
    data = json.loads(Path(path).read_text(encoding='utf-8'), object_pairs_hook=_no_duplicates)
    users = data.get('users') if isinstance(data, dict) and set(data) == {'users'} else None
    if not isinstance(users, list) or not users:
        raise ValidationError(['credentials: {"users": [...]} required'])
    result, names = {}, set()
    for u in users:
        if not isinstance(u, dict) or set(u) != {'name', 'role', 'token_sha256'}:
            raise ValidationError(['credentials: each user needs exactly name, role, token_sha256 (never a plain token)'])
        digest = u['token_sha256']
        if u['role'] not in ROLES or not isinstance(digest, str) or len(digest) != 64 or \
                any(c not in '0123456789abcdef' for c in digest) or u['name'] in names or digest in result:
            raise ValidationError([f'credentials: invalid or duplicate user {u.get("name")!r}'])
        names.add(u['name'])
        result[digest] = (u['name'], u['role'])
    return result


def _plan_worker(connection, scenario_text: str, memory_mb: int | None):
    try:
        if memory_mb:
            try:
                import resource
                limit = memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            except (ImportError, ValueError, OSError):
                pass  # not available on this platform: documented limitation
        from planning_factory.engine.scheduler import plan
        connection.send(('ok', dumps(plan(loads(scenario_text)))))
    except Exception as exc:  # reported to the caller, the worker process ends anyway
        connection.send(('error', f'{type(exc).__name__}: {exc}'))
    finally:
        connection.close()


class Service:
    def __init__(self, store_path, credentials_path, audit_path, *, workers: int = 2, queue_seconds: float = 30,
                 job_timeout: float = 120, memory_mb: int | None = None):
        if type(workers) is not int or not 1 <= workers <= 32:
            raise ValueError('workers must be 1..32')
        self.store = SnapshotStore(store_path)
        self.credentials = load_credentials(credentials_path)
        self.audit_path = Path(audit_path)
        self.slots = threading.BoundedSemaphore(workers)
        self.queue_seconds, self.job_timeout, self.memory_mb = queue_seconds, job_timeout, memory_mb
        self.audit_lock = threading.Lock()

    def authenticate(self, header: str | None):
        if not header or not header.startswith('Bearer '):
            return None
        digest = hashlib.sha256(header[7:].strip().encode()).hexdigest()
        for known, user in self.credentials.items():
            if hmac.compare_digest(known, digest):
                return user
        return None

    def audit(self, **entry):
        entry = {'at': datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z'), **entry}
        with self.audit_lock, self.audit_path.open('a', encoding='utf-8') as f:
            f.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + '\n')

    def compute_plan(self, scenario_text: str):
        if not self.slots.acquire(timeout=self.queue_seconds):
            raise TimeoutError('queue')
        try:
            context = multiprocessing.get_context('spawn')
            parent, child = context.Pipe(duplex=False)
            process = context.Process(target=_plan_worker, args=(child, scenario_text, self.memory_mb), daemon=True)
            process.start()
            child.close()
            if not parent.poll(self.job_timeout):
                process.terminate()
                process.join(5)
                raise TimeoutError('job')
            status, payload = parent.recv()
            process.join(5)
            if status != 'ok':
                raise ValidationError([payload])
            return plan_loads(payload)
        finally:
            self.slots.release()


def make_handler(service: Service):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'PlanningFactoryService/0.2'

        def respond(self, status, payload):
            if status >= 400 and not getattr(self, 'body_read', True):
                # Answering with the body still unread makes Windows reset the socket (WinError
                # 10053): the client sees an abort instead of the status. Drain it, then close.
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    length = 0
                if not self.headers.get('Transfer-Encoding') and 0 < length <= MAX_BODY:
                    self.connection.settimeout(15)
                    self.rfile.read(length)
                self.body_read = True
                self.close_connection = True
            content = dumps(payload).encode('utf-8')
            self.send_response(status)
            for key, value in (('Content-Type', 'application/json; charset=utf-8'), ('Content-Length', str(len(content))),
                               ('Cache-Control', 'no-store'), ('X-Content-Type-Options', 'nosniff')):
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(content)
            self.status = status

        def body(self):
            if self.headers.get('Transfer-Encoding') or self.headers.get_content_type() != 'application/json':
                raise ValidationError(['application/json with Content-Length required'])
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= MAX_BODY:
                raise ValidationError(['body must be between 1 byte and 8 MiB'])
            self.connection.settimeout(15)
            self.body_read = True
            data = json.loads(self.rfile.read(length).decode('utf-8'), object_pairs_hook=_no_duplicates,
                              parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
            if not isinstance(data, dict):
                raise ValidationError(['JSON object required'])
            return data

        def route(self, method):
            parts = [p for p in self.path.split('?')[0].split('/') if p]
            if method == 'GET' and parts == ['health']:
                return None, lambda: self.respond(200, {'status': 'ok'})
            table = {
                ('POST', 'scenarios'): ('planner', self.save_scenario),
                ('GET', 'scenario'): ('reparto', self.get_scenario),
                ('GET', 'plan'): ('reparto', self.get_plan),
                ('POST', 'plan'): ('planner', self.compute),
                ('POST', 'verify'): ('reparto', self.verify),
                ('GET', 'audit'): ('admin', self.read_audit),
            }
            if parts == ['v1', 'scenarios']:
                key, args = 'scenarios', ()
            elif len(parts) == 3 and parts[:2] == ['v1', 'scenarios']:
                key, args = 'scenario', (parts[2],)
            elif len(parts) == 4 and parts[:2] == ['v1', 'scenarios'] and parts[3] == 'plan':
                key, args = 'plan', (parts[2],)
            elif parts in (['v1', 'verify'], ['v1', 'audit']):
                key, args = parts[1], ()
            else:
                return None, lambda: self.respond(404, {'error': 'not found'})
            if (method, key) not in table:
                return None, lambda: self.respond(405, {'error': 'method not allowed'})
            role, handler = table[(method, key)]
            return role, lambda: handler(*args)

        def handle_method(self, method):
            self.status = None
            self.body_read = method != 'POST'
            user = None
            try:
                if self.headers.get('Origin') is not None:
                    self.respond(403, {'error': 'browser origins are not accepted'})
                    return
                role, action = self.route(method)
                if role is not None:
                    user = service.authenticate(self.headers.get('Authorization'))
                    if user is None:
                        self.respond(401, {'error': 'authentication required'})
                        return
                    if ROLES[user[1]] < ROLES[role]:
                        self.respond(403, {'error': f'role {role} required'})
                        return
                self.user = user
                action()
            except ConflictError as exc:
                self.respond(409, {'error': str(exc)})
            except TimeoutError as exc:
                self.respond(503 if str(exc) == 'queue' else 504,
                             {'error': 'compute queue full' if str(exc) == 'queue' else 'compute time limit exceeded'})
            except KeyError:
                self.respond(404, {'error': 'not found'})
            except (ValueError, TypeError, AttributeError) as exc:
                self.respond(422, {'error': str(exc)})
            finally:
                if self.path.split('?')[0] != '/health':
                    service.audit(user=user[0] if user else None, role=user[1] if user else None, method=method,
                                  path=self.path.split('?')[0], status=self.status, **getattr(self, 'extra', {}))

        def do_GET(self):
            self.handle_method('GET')

        def do_POST(self):
            self.handle_method('POST')

        def save_scenario(self):
            data = self.body()
            if set(data) - {'scenario', 'expected_revision'} or 'scenario' not in data:
                raise ValidationError(['scenario and expected_revision required'])
            expected = data.get('expected_revision', 0)
            if type(expected) is not int or expected < 0:
                raise ValidationError(['expected_revision must be a nonnegative integer'])
            scenario = scenario_from_dict(data['scenario'])
            revision = service.store.save(scenario, expected_revision=expected)
            self.extra = {'scenario_id': scenario.id, 'revision': revision, 'scenario_digest': scenario_digest(scenario)}
            self.respond(200, {'scenario_id': scenario.id, 'revision': revision, 'scenario_digest': scenario_digest(scenario)})

        def get_scenario(self, scenario_id):
            revision, scenario = service.store.load(scenario_id)
            self.respond(200, {'revision': revision, 'scenario': scenario})

        def get_plan(self, scenario_id):
            revision, scenario = service.store.load(scenario_id)
            self.respond(200, {'revision': revision, 'plan': service.store.load_plan(scenario_id, scenario_digest(scenario))})

        def compute(self, scenario_id):
            data = self.body()
            if set(data) != {'expected_revision'} or type(data['expected_revision']) is not int:
                raise ValidationError(['expected_revision required'])
            revision, scenario = service.store.load(scenario_id)
            if revision != data['expected_revision']:
                raise ConflictError(f'expected revision {data["expected_revision"]}, found {revision}')
            result = service.compute_plan(dumps(scenario))
            errors = verify_plan(scenario, result)
            if errors:
                raise ValidationError(list(errors))
            service.store.save_plan(scenario, result)
            self.extra = {'scenario_id': scenario_id, 'revision': revision, 'scenario_digest': result.scenario_digest,
                          'unscheduled': len(result.unscheduled)}
            self.respond(200, {'revision': revision, 'plan': result})

        def verify(self):
            data = self.body()
            if set(data) != {'scenario', 'plan'}:
                raise ValidationError(['scenario and plan required'])
            errors = verify_plan(scenario_from_dict(data['scenario']), plan_loads(json.dumps(data['plan'])))
            self.respond(422 if errors else 200, {'valid': not errors, 'errors': errors})

        def read_audit(self):
            lines = service.audit_path.read_text(encoding='utf-8').splitlines() if service.audit_path.exists() else []
            self.respond(200, {'entries': [json.loads(line) for line in lines[-200:]]})

        def log_message(self, format, *args):
            pass  # the audit log records who did what, without payloads

    return Handler


class ServiceServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32


def make_service_server(service: Service, host: str, port: int, *, certfile=None, keyfile=None,
                        behind_proxy: bool = False) -> ServiceServer:
    try:
        loopback = host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if not loopback and not certfile and not behind_proxy:
        raise ValueError('outside loopback the service requires TLS (cert and key) or an explicit --behind-proxy')
    server = ServiceServer((host, port), make_handler(service))
    if certfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(certfile, keyfile)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    return server
