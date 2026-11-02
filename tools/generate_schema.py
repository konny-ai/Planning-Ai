"""Generate JSON Schemas from the public dataclasses without third-party packages."""
import argparse
from dataclasses import MISSING, fields, is_dataclass
from datetime import date, datetime, time
import json
from pathlib import Path
import types
from typing import get_args, get_origin, get_type_hints, Literal, Union
from planning_factory.domain import models


def schema_type(tp):
    if tp is type(None):
        return {'type': 'null'}
    if tp in (str, int, bool, float):
        return {'type': {str: 'string', int: 'integer', bool: 'boolean', float: 'number'}[tp]}
    if tp is time:
        return {'type': 'string', 'pattern': r'^([01]\d|2[0-3]):[0-5]\d(:00)?$'}
    if tp in (date, datetime):
        return {'type': 'string', 'format': {date: 'date', datetime: 'date-time', time: 'time'}[tp]}
    if is_dataclass(tp):
        return {'$ref': '#/$defs/' + tp.__name__}
    origin, args = get_origin(tp), get_args(tp)
    if origin is Literal:
        return {'enum': list(args)}
    if origin in (types.UnionType, Union):
        return {'anyOf': [schema_type(t) for t in args]}
    if origin is tuple:
        if len(args) == 2 and args[1] is Ellipsis:
            return {'type': 'array', 'items': schema_type(args[0])}
        return {'type': 'array', 'prefixItems': [schema_type(t) for t in args],
                'minItems': len(args), 'maxItems': len(args)}
    raise TypeError(tp)


def definition(cls):
    hints = get_type_hints(cls)
    props = {f.name: schema_type(hints[f.name]) for f in fields(cls)}
    for name, prop in props.items():
        if name in ('id', 'resource_id', 'calendar_id', 'order_id', 'predecessor_id', 'task_id', 'scenario_id'):
            prop['minLength'] = 1
        if name in ('duration_seconds', 'setup_seconds', 'lag_seconds', 'auto_backward_days',
                    'priority_weight', 'due_weight', 'customer_weight'):
            prop['minimum'] = 0
        if name == 'weekday':
            prop.update(minimum=0, maximum=6)
        if name == 'units':
            prop['minimum'] = 1
        if name in ('start', 'end') and cls is models.Shift:
            prop.pop('format', None)
            prop['pattern'] = r'^([01]\d|2[0-3]):[0-5]\d(:00)?$'
        if name == 'schema_version':
            prop.update(const=1)
    required = [f.name for f in fields(cls) if f.default is MISSING and f.default_factory is MISSING]
    if cls in (models.Scenario, models.Plan) and 'schema_version' not in required:
        required.append('schema_version')
    return {'type': 'object', 'additionalProperties': False, 'properties': props, 'required': required}


def generate():
    classes = {name: cls for name, cls in vars(models).items() if isinstance(cls, type) and is_dataclass(cls)}
    definitions = {name: definition(cls) for name, cls in classes.items()}
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / 'src/planning_factory/schemas'
    for name in ('Scenario', 'Plan'):
        schema = {'$schema': 'https://json-schema.org/draft/2020-12/schema',
                  'title': 'Planning Factory ' + name + ' v1', '$ref': '#/$defs/' + name, '$defs': definitions}
        text = json.dumps(schema, indent=2, sort_keys=True) + '\n'
        path = root / (name.lower() + '.schema.json')
        if args.check:
            if path.read_text() != text:
                raise SystemExit(f'{path.name} is stale')
        else:
            path.write_text(text)


if __name__ == '__main__':
    generate()
