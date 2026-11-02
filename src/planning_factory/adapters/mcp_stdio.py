"""Local MCP stdio read transport. No remote endpoint, SQL or model credentials."""
import json
import sys

from planning_factory.adapters.json_io import _no_duplicates
from planning_factory.adapters.planning_tools import PlanningTools
from planning_factory.domain.validation import ValidationError

VERSIONS = ('2025-11-25', '2025-06-18', '2025-03-26', '2024-11-05')


class MCPReadServer:
    def __init__(self, state):
        self.state = state
        self.initialized = False

    def handle(self, request):
        def error(code, message):
            return {'jsonrpc': '2.0', 'id': request.get('id') if isinstance(request, dict) else None,
                    'error': {'code': code, 'message': message}}
        if not isinstance(request, dict) or request.get('jsonrpc') != '2.0' or not isinstance(request.get('method'), str):
            return error(-32600, 'Invalid Request')
        notification = 'id' not in request
        method, params = request['method'], request.get('params', {})
        if notification:
            return None
        if type(request['id']) not in (int, str) or not isinstance(params, dict):
            return error(-32600, 'Invalid Request')
        if method == 'initialize':
            version = params.get('protocolVersion')
            self.initialized = True
            result = {'protocolVersion': version if version in VERSIONS else VERSIONS[0],
                      'capabilities': {'tools': {'listChanged': False}},
                      'serverInfo': {'name': 'planning-factory-read', 'version': '0.1.0'}}
        elif method == 'ping':
            result = {}
        elif not self.initialized:
            return error(-32002, 'Initialize first')
        elif method == 'tools/list':
            if params:
                return error(-32602, 'Invalid params')
            # Schema discovery does not run the planner.
            result = {'tools': PlanningTools({}).list_tools()}
        elif method == 'tools/call':
            if set(params) - {'name', 'arguments'} or 'name' not in params:
                return error(-32602, 'Invalid params')
            try:
                texts = self.state.order_texts() if hasattr(self.state, 'order_texts') else {}
                value = PlanningTools({**self.state.bootstrap(), 'order_texts': texts}).call(
                    params['name'], params.get('arguments', {}))
                result = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}],
                          'structuredContent': value, 'isError': False}
            except (ValidationError, ValueError):
                result = {'content': [{'type': 'text', 'text': 'Read tool unavailable or invalid arguments'}], 'isError': True}
        else:
            return error(-32601, 'Method not found')
        return {'jsonrpc': '2.0', 'id': request['id'], 'result': result}


def serve_stdio(state, source=None, destination=None):
    source, destination = source or sys.stdin, destination or sys.stdout
    server = MCPReadServer(state)
    while True:
        line = source.readline(65538)
        if not line:
            return
        if len(line.encode('utf-8')) > 65536 or not line.endswith('\n'):
            # Drop the remainder of an oversized frame without treating it as another request.
            while line and not line.endswith('\n'):
                line = source.readline(65538)
            response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Frame too large or incomplete'}}
        else:
            try:
                request = json.loads(line, object_pairs_hook=_no_duplicates,
                                     parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite JSON')))
                response = server.handle(request)
            except (ValueError, ValidationError, RecursionError):
                response = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Parse error'}}
        if response is not None:
            destination.write(json.dumps(response, ensure_ascii=False) + '\n')
            destination.flush()
