"""Strict domain validation and conversion of operations into atomic booking tasks."""
import threading
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from .models import Alternative, Edge, Scenario, Task


class ValidationError(ValueError):
    def __init__(self, errors: list[str] | tuple[str, ...]):
        self.errors = tuple(errors)
        super().__init__('; '.join(errors))


def instant(value: datetime, label: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError([f'{label}: timezone offset required'])
    if value.microsecond:
        raise ValidationError([f'{label}: precision must be whole seconds'])


def whole(value: int, label: str, minimum: int = 0) -> None:
    if type(value) is not int or value < minimum:
        raise ValidationError([f'{label}: integer >= {minimum} required'])


def unique(items, label: str) -> dict:
    result = {}
    for item in items:
        if not isinstance(item.id, str) or not item.id.strip() or item.id in result:
            raise ValidationError([f'{label}: invalid or duplicate id {item.id!r}'])
        result[item.id] = item
    return result


def utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def validate(s: Scenario) -> None:
    instant(s.start, 'start')
    instant(s.end, 'end')
    if utc(s.start) >= utc(s.end):
        raise ValidationError(['end must be after start'])
    if not isinstance(s.id, str) or not s.id.strip():
        raise ValidationError(['scenario id required'])
    if type(s.schema_version) is not int or s.schema_version != 1:
        raise ValidationError(['unsupported schema_version'])
    if s.settings.mode not in ('forward', 'backward', 'auto'):
        raise ValidationError(['invalid settings.mode'])
    if s.settings.auto_rule not in ('due_distance', 'slack'):
        raise ValidationError(['invalid settings.auto_rule'])
    for key in ('auto_backward_days', 'priority_weight', 'due_weight', 'customer_weight',
                'alternative_switch_min_gain_seconds', 'auto_slack_seconds'):
        whole(getattr(s.settings, key), key)
    calendars = unique(s.calendars, 'calendar')
    resources = unique(s.resources, 'resource')
    operations = unique(s.operations, 'operation')
    # A bounded planning window prevents accidental multi-century expansions.
    if (utc(s.end) - utc(s.start)).total_seconds() > 3660 * 86400:
        raise ValidationError(['planning horizon must not exceed 3660 days'])
    unique(s.nestings, 'nesting')
    for c in s.calendars:
        try:
            ZoneInfo(c.timezone)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValidationError([f'calendar {c.id}: unknown timezone']) from None
        if len({e.day for e in c.exceptions}) != len(c.exceptions):
            raise ValidationError([f'calendar {c.id}: duplicate exception date'])
        for shift in c.shifts:
            whole(shift.weekday, 'weekday')
            if shift.weekday > 6:
                raise ValidationError(['weekday must be 0..6'])
        times = [(a.start, a.end) for a in c.shifts]
        times += [pair for e in c.exceptions for pair in e.shifts]
        for a, b in times:
            if a.tzinfo or b.tzinfo or a.second or b.second or a.microsecond or b.microsecond:
                raise ValidationError(['shift endpoints require local HH:MM precision'])
        for closure in c.closures:
            instant(closure.start, 'closure.start')
            instant(closure.end, 'closure.end')
            if utc(closure.start) >= utc(closure.end):
                raise ValidationError(['closure end must be after start'])
    for r in s.resources:
        if not isinstance(r.calendar_id, str) or r.calendar_id not in calendars:
            raise ValidationError([f'resource {r.id}: unknown calendar {r.calendar_id}'])
        if not isinstance(r.kind, str) or not r.kind.strip():
            raise ValidationError([f'resource {r.id}: kind must be a nonempty string'])
        if r.capacity is not None:
            whole(r.capacity, f'resource {r.id} capacity', 1)
        whole(r.efficiency_bp, f'resource {r.id} efficiency_bp', 1)
        if r.efficiency_bp > MAX_EFFICIENCY_BP:
            raise ValidationError([f'resource {r.id}: efficiency_bp above {MAX_EFFICIENCY_BP}'])
    for o in s.operations:
        if not isinstance(o.order_id, str) or not o.order_id.strip():
            raise ValidationError([f'operation {o.id}: order_id required'])
        whole(o.duration_seconds, f'{o.id} duration')
        whole(o.setup_seconds, f'{o.id} setup')
        if any(type(v) is not int for v in (o.priority, o.customer_priority, o.phase_priority)):
            raise ValidationError([f'{o.id}: priorities must be integers'])
        if type(o.splittable) is not bool:
            raise ValidationError([f'{o.id}: splittable must be boolean'])
        for key in ('release', 'due', 'fixed_start', 'completed_at', 'external_end'):
            value = getattr(o, key)
            if value is not None:
                instant(value, f'{o.id}.{key}')
        if o.mode not in (None, 'forward', 'backward', 'auto'):
            raise ValidationError([f'{o.id}: invalid mode'])
        if o.external_end is not None:
            # Contractual supplier milestones are neither resource reservations nor
            # proof of completed work. They may be before or after the horizon.
            if (o.total_seconds != 0 or o.alternatives or o.completed_at is not None
                    or o.fixed_start is not None or o.fixed_alternative is not None or o.release is not None):
                raise ValidationError([f'{o.id}: external_end requires zero work, no resources, release, fixed start or completion'])
        elif o.completed_at is not None:
            if o.total_seconds != 0 or o.fixed_start is not None or utc(o.completed_at) > utc(s.start):
                raise ValidationError([f'{o.id}: completed requires zero remaining time, no fixed start, completion <= horizon start'])
        elif o.total_seconds <= 0:
            raise ValidationError([f'{o.id}: positive remaining duration required'])
        if not o.alternatives and o.completed_at is None and o.external_end is None:
            raise ValidationError([f'{o.id}: at least one resource alternative required'])
        for alternative in o.alternatives:
            ids = set()
            if not alternative.demands:
                raise ValidationError([f'{o.id}: empty resource bundle'])
            for d in alternative.demands:
                whole(d.units, f'{o.id} demand units', 1)
                if not isinstance(d.resource_id, str):   # a list or dict would fail as 'unhashable'
                    raise ValidationError([f'{o.id}: resource ID must be text'])
                if d.resource_id not in resources or d.resource_id in ids:
                    raise ValidationError([f'{o.id}: unknown or duplicate resource {d.resource_id}'])
                ids.add(d.resource_id)
        if o.preferred_alternative is not None:
            whole(o.preferred_alternative, f'{o.id} preferred_alternative')
            if o.preferred_alternative >= len(o.alternatives):
                raise ValidationError([f'{o.id}: preferred_alternative must index an alternative'])
        if o.fixed_alternative is not None:
            whole(o.fixed_alternative, f'{o.id} fixed_alternative')
            if o.fixed_alternative >= len(o.alternatives) or o.fixed_start is None:
                raise ValidationError([f'{o.id}: fixed_alternative requires a valid index and fixed_start'])
        for dep in o.predecessors:
            if dep.predecessor_id not in operations or dep.predecessor_id == o.id:
                raise ValidationError([f'{o.id}: missing or self predecessor {dep.predecessor_id}'])
            if dep.kind not in ('FS', 'SS'):
                raise ValidationError([f'{o.id}: dependency kind must be FS or SS'])
            whole(dep.lag_seconds, f'{o.id} dependency lag')
            if dep.max_lag_seconds is not None:
                whole(dep.max_lag_seconds, f'{o.id} dependency max_lag')
                if dep.kind != 'FS' or dep.max_lag_seconds < dep.lag_seconds:
                    raise ValidationError([f'{o.id}: max_lag_seconds needs an FS dependency and must be >= lag_seconds'])
    members = set()
    for n in s.nestings:
        whole(n.duration_seconds, f'{n.id} duration')
        whole(n.setup_seconds, f'{n.id} setup')
        if not n.operation_ids or len(set(n.operation_ids)) != len(n.operation_ids) or n.duration_seconds + n.setup_seconds <= 0:
            raise ValidationError([f'{n.id}: nonempty unique members and positive duration required'])
        for oid in n.operation_ids:
            if oid not in operations or oid in members:
                raise ValidationError([f'{n.id}: missing or multiply grouped operation {oid}'])
            if operations[oid].completed_at is not None or operations[oid].external_end is not None:
                raise ValidationError([f'{n.id}: completed/external milestones cannot belong to active nesting'])
            members.add(oid)


MAX_EFFICIENCY_BP = 100000  # 1000 %: anything above is a data error, not a fast machine


def planned_seconds(duration: int, demands, resources: dict) -> int:
    """Standard time stretched by the least efficient resource of the bundle (#78 P9), rounded up."""
    efficiency = min((resources[d.resource_id].efficiency_bp for d in demands), default=10000)
    return duration if efficiency == 10000 else -(-duration * 10000 // efficiency)


def bundle_key(a: Alternative) -> tuple:
    return tuple(sorted(a.demands))


_COMPILED_LOCK = threading.Lock()   # the service plans in threads: guard eviction and insertion
_COMPILED: dict = {}   # id(scenario) -> (scenario, tasks, edges, order); the strong ref keeps the id unique


def compile_tasks(s: Scenario) -> tuple[dict[str, Task], tuple[Edge, ...], tuple[str, ...]]:
    """Validate and collapse a scenario into schedulable tasks.

    Memoized per scenario *object* (scenarios are frozen, so the result of one object never
    changes; the planner asks for it several times per plan). The cache is small, holds a
    strong reference so an id is never reused by another object, and every call returns a
    fresh dict so no caller can alter what is cached (tasks, edges and order are immutable).
    Validation failures are never cached.
    """
    hit = _COMPILED.get(id(s))
    if hit is not None and hit[0] is s:
        return dict(hit[1]), hit[2], hit[3]
    tasks, edges, order = _compile_tasks(s)
    with _COMPILED_LOCK:
        while len(_COMPILED) >= 16:
            _COMPILED.pop(next(iter(_COMPILED)), None)
        _COMPILED[id(s)] = (s, tasks, edges, order)
    return dict(tasks), edges, order


def _compile_tasks(s: Scenario) -> tuple[dict[str, Task], tuple[Edge, ...], tuple[str, ...]]:
    validate(s)
    ops = {o.id: o for o in s.operations}
    membership = {oid: 'nest:' + n.id for n in s.nestings for oid in n.operation_ids}
    tasks = {}
    groups = [('job:' + o.id, (o.id,), o.total_seconds) for o in s.operations if o.id not in membership]
    groups += [('nest:' + n.id, tuple(sorted(n.operation_ids)), n.duration_seconds + n.setup_seconds) for n in s.nestings]
    for tid, ids, duration in groups:
        values = [ops[i] for i in ids]
        # An alternative is a complete simultaneous bundle. Nest members must agree.
        candidate_sets = []
        for o in values:
            alternatives = o.alternatives if o.fixed_alternative is None else (o.alternatives[o.fixed_alternative],)
            candidate_sets.append({bundle_key(a) for a in alternatives})
        common = set.intersection(*candidate_sets)
        if not common and values[0].completed_at is None and values[0].external_end is None:
            raise ValidationError([f'{tid}: nesting members have no common resource bundle'])
        alternatives = tuple(Alternative(k) for k in sorted(common))
        preferred = {bundle_key(o.alternatives[o.preferred_alternative]) for o in values
                     if o.preferred_alternative is not None}
        if len(preferred) > 1:
            raise ValidationError([f'{tid}: nesting members disagree on the preferred alternative'])
        preferred_bundle = next(iter(preferred), None)
        if preferred_bundle is not None and preferred_bundle not in common:
            preferred_bundle = None  # not available to the whole unit: plain earliest choice
        modes = {o.mode or s.settings.mode for o in values}
        fixed = {utc(o.fixed_start) for o in values if o.fixed_start is not None}
        if len(modes) != 1 or len(fixed) > 1 or len({o.splittable for o in values}) > 1:
            raise ValidationError([f'{tid}: nesting members disagree on mode, splitting or fixed start'])
        due = min((utc(o.due) for o in values if o.due is not None), default=None)
        release = max([utc(s.start)] + [utc(o.release) for o in values if o.release is not None])
        tasks[tid] = Task(tid, ids, duration, alternatives, release, due,
                          min(o.priority for o in values), min(o.customer_priority for o in values), min(o.phase_priority for o in values),
                          modes.pop(), values[0].splittable, next(iter(fixed), None), None,
                          utc(values[0].completed_at) if values[0].completed_at else None,
                          utc(values[0].external_end) if values[0].external_end else None, preferred_bundle)
    edges = set()
    for o in s.operations:
        target = membership.get(o.id, 'job:' + o.id)
        for dep in o.predecessors:
            source = membership.get(dep.predecessor_id, 'job:' + dep.predecessor_id)
            if source == target:
                raise ValidationError([f'{target}: dependency inside atomic nesting'])
            edges.add(Edge(source, target, dep.kind, dep.lag_seconds))
    edges = tuple(sorted(edges, key=lambda e: (e.predecessor, e.successor, e.kind, e.lag_seconds)))
    successors = {k: set() for k in tasks}
    degree = {k: 0 for k in tasks}
    for e in edges:
        if e.successor not in successors[e.predecessor]:
            successors[e.predecessor].add(e.successor)
            degree[e.successor] += 1
    ready = sorted(k for k, d in degree.items() if d == 0)
    order = []
    import heapq
    while ready:
        k = heapq.heappop(ready)
        order.append(k)
        for nxt in sorted(successors[k]):
            degree[nxt] -= 1
            if not degree[nxt]:
                heapq.heappush(ready, nxt)
    if len(order) != len(tasks):
        raise ValidationError(['dependency cycle (including collapsed nesting): ' + ', '.join(k for k, d in degree.items() if d)])
    return tasks, edges, tuple(order)
