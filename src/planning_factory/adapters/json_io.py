"""Versioned strict JSON boundary. Unknown fields and implicit conversions fail."""
import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import UTC, date, datetime, time
from planning_factory.domain.models import (
    Alternative, Assignment, Calendar, CalendarException, Demand, Dependency, Interval,
    Nesting, Operation, OrderResult, Plan, Resource, ResourceLoad, Scenario, Settings, Shift, Unscheduled,
)
from planning_factory.domain.validation import ValidationError, compile_tasks, instant


# Optional fields added after v1: omitted at their default so existing digests do not change.
_OMITTED_DEFAULTS = {'external_end': None, 'preferred_alternative': None, 'max_lag_seconds': None,
                     'alternative_switch_min_gain_seconds': 0, 'auto_rule': 'due_distance', 'auto_slack_seconds': 0,
                     'efficiency_bp': 10000}


_SCALARS = frozenset((str, int, float, bool, type(None)))
_DATACLASS_FIELDS: dict = {}   # class -> ((name, omitted default or _KEEP), ...)
_KEEP = object()


def _field_plan(cls):
    plan = _DATACLASS_FIELDS.get(cls)
    if plan is None:
        plan = _DATACLASS_FIELDS[cls] = tuple(
            (f.name, _OMITTED_DEFAULTS.get(f.name, _KEEP)) for f in fields(cls))
    return plan


def primitive(value):
    kind = type(value)
    if kind in _SCALARS:          # exact builtin scalars: the generic path would return them unchanged
        return value
    if isinstance(value, datetime):
        instant(value, 'datetime')
        return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')
    if isinstance(value, (date, time)):
        return value.isoformat()
    if is_dataclass(value):
        # Preserve existing v1 scenario digests and golden fixtures when unused.
        result = {}
        for name, omitted in _field_plan(kind):
            item = getattr(value, name)
            if omitted is not _KEEP and item == omitted and type(item) is type(omitted):
                continue
            result[name] = primitive(item)
        return result
    if isinstance(value, (tuple, list)):
        return [primitive(x) for x in value]
    if isinstance(value, dict):
        return {k: primitive(v) for k, v in value.items()}
    return value


def dumps(value) -> str:
    return json.dumps(primitive(value), ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'


_DIGESTS: dict = {}   # id(scenario) -> (scenario, digest); the strong reference keeps the id unique


def scenario_digest(s: Scenario) -> str:
    """SHA-256 of the canonical JSON. Memoized per scenario *object*: scenarios
    are frozen dataclasses of tuples, so the digest of one object never changes
    (the planner asks for it several times per plan). The cache is small and
    holds a strong reference so an id can never be reused by another object."""
    hit = _DIGESTS.get(id(s))
    if hit is not None and hit[0] is s:
        return hit[1]
    digest = hashlib.sha256(dumps(s).encode('utf-8')).hexdigest()
    if len(_DIGESTS) >= 16:
        _DIGESTS.pop(next(iter(_DIGESTS)))
    _DIGESTS[id(s)] = (s, digest)
    return digest


def obj(value, cls, required=()):
    if not isinstance(value, dict):
        raise ValidationError([f'{cls.__name__}: object required'])
    unknown = set(value) - {f.name for f in fields(cls)}
    missing = set(required) - set(value)
    if unknown or missing:
        raise ValidationError([f'{cls.__name__}: unknown fields {sorted(unknown)}, missing fields {sorted(missing)}'])
    return dict(value)


def array(value, label):
    if not isinstance(value, list):
        raise ValidationError([f'{label}: array required'])
    return value


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError(['timestamp must be an ISO 8601 string with offset'])
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise ValidationError([f'invalid timestamp: {value}']) from None
    instant(parsed, 'timestamp')
    return parsed.astimezone(UTC)


def local_time(value):
    if not isinstance(value, str):
        raise ValidationError(['shift endpoint must be HH:MM string'])
    try:
        return time.fromisoformat(value)
    except ValueError:
        raise ValidationError([f'invalid local time: {value}']) from None


def local_date(value):
    if not isinstance(value, str):
        raise ValidationError(['date must be YYYY-MM-DD string'])
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError([f'invalid date: {value}']) from None


def interval(value):
    data = obj(value, Interval, ('start', 'end'))
    start, end = timestamp(data['start']), timestamp(data['end'])
    if start is None or end is None:
        raise ValidationError(['interval endpoints cannot be null'])
    return Interval(start, end)


def demand(value):
    return Demand(**obj(value, Demand, ('resource_id',)))


def alternative(value):
    data = obj(value, Alternative, ('demands',))
    return Alternative(tuple(demand(x) for x in array(data['demands'], 'demands')))


def scenario_from_dict(value) -> Scenario:
    data = obj(value, Scenario, ('id', 'start', 'end', 'calendars', 'resources', 'operations', 'schema_version'))
    data['start'], data['end'] = timestamp(data['start']), timestamp(data['end'])
    calendars = []
    for item in array(data['calendars'], 'calendars'):
        c = obj(item, Calendar, ('id', 'timezone', 'shifts'))
        shifts = []
        for raw in array(c['shifts'], 'shifts'):
            sh = obj(raw, Shift, ('weekday', 'start', 'end'))
            shifts.append(Shift(sh['weekday'], local_time(sh['start']), local_time(sh['end'])))
        exceptions = []
        for raw in array(c.get('exceptions', []), 'exceptions'):
            ex = obj(raw, CalendarException, ('day',))
            pairs = []
            for pair in array(ex.get('shifts', []), 'exception shifts'):
                if not isinstance(pair, list) or len(pair) != 2:
                    raise ValidationError(['exception shift must be [start, end]'])
                pairs.append((local_time(pair[0]), local_time(pair[1])))
            exceptions.append(CalendarException(local_date(ex['day']), tuple(pairs)))
        c.update(shifts=tuple(shifts), exceptions=tuple(exceptions),
                 holidays=tuple(local_date(x) for x in array(c.get('holidays', []), 'holidays')),
                 closures=tuple(interval(x) for x in array(c.get('closures', []), 'closures')))
        calendars.append(Calendar(**c))
    operations = []
    for item in array(data['operations'], 'operations'):
        o = obj(item, Operation, ('id', 'order_id', 'duration_seconds', 'alternatives'))
        o['alternatives'] = tuple(alternative(x) for x in array(o['alternatives'], 'alternatives'))
        o['predecessors'] = tuple(Dependency(**obj(x, Dependency, ('predecessor_id',)))
                                  for x in array(o.get('predecessors', []), 'predecessors'))
        for key in ('release', 'due', 'fixed_start', 'completed_at', 'external_end'):
            if key in o:
                o[key] = timestamp(o[key])
        operations.append(Operation(**o))
    nests = []
    for item in array(data.get('nestings', []), 'nestings'):
        n = obj(item, Nesting, ('id', 'operation_ids', 'duration_seconds'))
        n['operation_ids'] = tuple(array(n['operation_ids'], 'operation_ids'))
        if not all(isinstance(i, str) for i in n['operation_ids']):
            raise ValidationError(['nesting operation_ids must be strings'])
        nests.append(Nesting(**n))
    data.update(calendars=tuple(calendars), operations=tuple(operations), nestings=tuple(nests),
                resources=tuple(Resource(**obj(x, Resource, ('id', 'calendar_id')))
                                for x in array(data['resources'], 'resources')),
                settings=Settings(**obj(data.get('settings', {}), Settings)))
    scenario = Scenario(**data)
    compile_tasks(scenario)
    return scenario


def loads(text: str) -> Scenario:
    try:
        value = json.loads(text, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)),
                           object_pairs_hook=_no_duplicates)
        return scenario_from_dict(value)
    except ValidationError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValidationError([f'invalid scenario JSON: {exc}']) from None


def _no_duplicates(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f'duplicate JSON key: {key}')
        data[key] = value
    return data


def plan_loads(text: str) -> Plan:
    try:
        data = obj(json.loads(text, object_pairs_hook=_no_duplicates, parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v))), Plan,
                   ('scenario_id', 'scenario_digest', 'assignments', 'unscheduled', 'resources', 'orders'))
        assignments = []
        for raw in array(data['assignments'], 'assignments'):
            a = obj(raw, Assignment, ('task_id', 'operation_ids', 'segments', 'demands', 'requested_mode', 'effective_mode'))
            a['operation_ids'] = tuple(array(a['operation_ids'], 'operation_ids'))
            a['segments'] = tuple(interval(x) for x in array(a['segments'], 'segments'))
            a['demands'] = tuple(demand(x) for x in array(a['demands'], 'demands'))
            a['explanation'] = tuple(array(a.get('explanation', []), 'explanation'))
            a['completed_at'] = timestamp(a.get('completed_at'))
            a['external_end'] = timestamp(a.get('external_end'))
            assignments.append(Assignment(**a))
        unscheduled = []
        for raw in array(data['unscheduled'], 'unscheduled'):
            u = obj(raw, Unscheduled, ('task_id', 'operation_ids', 'code', 'detail'))
            u['operation_ids'] = tuple(array(u['operation_ids'], 'operation_ids'))
            unscheduled.append(Unscheduled(**u))
        orders = []
        for raw in array(data['orders'], 'orders'):
            o = obj(raw, OrderResult, ('order_id', 'end', 'due', 'tardiness_seconds', 'complete'))
            o['end'], o['due'] = timestamp(o['end']), timestamp(o['due'])
            orders.append(OrderResult(**o))
        data.update(assignments=tuple(assignments), unscheduled=tuple(unscheduled), orders=tuple(orders),
                    resources=tuple(ResourceLoad(**obj(x, ResourceLoad, ('resource_id', 'available_unit_seconds', 'booked_unit_seconds', 'utilization')))
                                    for x in array(data['resources'], 'resources')))
        return Plan(**data)
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ValidationError([f'invalid plan JSON: {exc}']) from None
