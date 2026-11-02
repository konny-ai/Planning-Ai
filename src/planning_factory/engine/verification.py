"""Independent result audit: never trusts the scheduler's reservation state."""
from collections import defaultdict
from datetime import timedelta
from planning_factory.domain.models import Plan, Scenario
from planning_factory.domain.validation import compile_tasks, instant, planned_seconds, utc, ValidationError
from .calendars import expand


def verify_plan(s: Scenario, result: Plan) -> tuple[str, ...]:
    from planning_factory.adapters.json_io import scenario_digest
    tasks, edges, _ = compile_tasks(s)
    errors = []
    if type(result.schema_version) is not int or result.schema_version != 1 or result.scenario_id != s.id or result.scenario_digest != scenario_digest(s):
        errors.append('scenario identity/digest/version mismatch')
    assigned = {}
    failed = {}
    resources = {r.id: r for r in s.resources}
    calendars = {c.id: expand(c, s.start, s.end) for c in s.calendars}
    events = defaultdict(lambda: defaultdict(int))
    used = defaultdict(int)
    for a in result.assignments:
        if a.task_id in assigned or a.task_id not in tasks:
            errors.append(f'unknown/duplicate assignment {a.task_id}')
            continue
        assigned[a.task_id] = a
        t = tasks[a.task_id]
        if tuple(a.operation_ids) != t.operation_ids:
            errors.append(f'{t.id}: wrong nesting members')
        if t.completed_at is not None:
            if (a.segments or a.demands or a.completed_at != t.completed_at
                    or a.external_end is not None or a.effective_mode != 'completed'):
                errors.append(f'{t.id}: invalid completion history')
            continue
        if t.external_end is not None:
            if (a.segments or a.demands or a.completed_at is not None
                    or a.external_end != t.external_end or a.fixed or a.effective_mode != 'external'):
                errors.append(f'{t.id}: invalid external supplier milestone')
            continue
        if a.completed_at is not None or a.external_end is not None or not a.segments:
            errors.append(f'{t.id}: missing active segments')
            continue
        try:
            for seg in a.segments:
                instant(seg.start, 'segment.start')
                instant(seg.end, 'segment.end')
        except ValidationError:
            errors.append(f'{t.id}: invalid segment timestamps')
            continue
        if any(seg.start >= seg.end for seg in a.segments):
            errors.append(f'{t.id}: nonpositive segment')
        if any(b.start < a_seg.end for a_seg, b in zip(a.segments, a.segments[1:])):
            errors.append(f'{t.id}: unordered/overlapping segments')
        # Planned time depends on the booked bundle's efficiency (#78 P9); unknown resources are reported below.
        expected = (planned_seconds(t.duration_seconds, a.demands, resources)
                    if all(d.resource_id in resources for d in a.demands) else t.duration_seconds)
        if sum(seg.seconds for seg in a.segments) != expected:
            errors.append(f'{t.id}: booked duration mismatch')
        if not t.splittable and len(a.segments) != 1:
            errors.append(f'{t.id}: forbidden interruption')
        if a.start < t.release or a.start < utc(s.start) or a.end > utc(s.end):
            errors.append(f'{t.id}: release/horizon violation')
        if t.fixed_start is not None and (a.start != t.fixed_start or not a.fixed):
            errors.append(f'{t.id}: fixed start changed')
        if t.fixed_start is None and a.fixed:
            errors.append(f'{t.id}: unexpected fixed flag')
        if a.requested_mode != t.mode or a.effective_mode not in ('forward', 'backward', 'fixed'):
            errors.append(f'{t.id}: invalid mode metadata')
        bundle = tuple(sorted(a.demands))
        if not any(bundle == tuple(sorted(alt.demands)) for alt in t.alternatives):
            errors.append(f'{t.id}: unauthorized resource bundle')
        if len({d.resource_id for d in a.demands}) != len(a.demands):
            errors.append(f'{t.id}: duplicate resource demand')
        for d in a.demands:
            if d.resource_id not in resources or type(d.units) is not int or d.units <= 0:
                errors.append(f'{t.id}: invalid resource demand')
                continue
            availability = calendars[resources[d.resource_id].calendar_id]
            for seg in a.segments:
                if not any(i.start <= seg.start < seg.end <= i.end for i in availability):
                    errors.append(f'{t.id}: outside calendar {d.resource_id}')
                events[d.resource_id][seg.start] += d.units
                events[d.resource_id][seg.end] -= d.units
                used[d.resource_id] += seg.seconds * d.units
    for u in result.unscheduled:
        if u.task_id in failed or u.task_id not in tasks or u.task_id in assigned:
            errors.append(f'unknown/duplicate unscheduled {u.task_id}')
        else:
            failed[u.task_id] = u
            if u.operation_ids != tasks[u.task_id].operation_ids or not u.code or not u.detail:
                errors.append(f'{u.task_id}: invalid unscheduled diagnostic')
            if tasks[u.task_id].fixed_start or tasks[u.task_id].completed_at:
                errors.append(f'{u.task_id}: fixed/history task not preserved')
    if set(tasks) != set(assigned) | set(failed):
        errors.append('task coverage mismatch')
    for rid, points in events.items():
        capacity = resources[rid].capacity
        load = 0
        for at, delta in sorted(points.items()):
            load += delta
            if capacity is not None and load > capacity:
                errors.append(f'{rid}: capacity exceeded at {at.isoformat()}')
                break
    for e in edges:
        pred, succ = assigned.get(e.predecessor), assigned.get(e.successor)
        if succ:
            if not pred or not pred.end or not succ.start:
                errors.append(f'{e.successor}: missing predecessor {e.predecessor}')
                continue
            base = pred.end if e.kind == 'FS' else pred.start
            if succ.start < base + timedelta(seconds=e.lag_seconds):
                errors.append(f'{e.successor}: {e.kind} precedence violation from {e.predecessor}')
    loads = {r.resource_id: r for r in result.resources}
    if len(loads) != len(result.resources) or set(loads) != set(resources):
        errors.append('resource metrics coverage mismatch')
    for rid, r in resources.items():
        metric = loads.get(rid)
        available = sum(i.seconds for i in calendars[r.calendar_id]) * r.capacity if r.capacity is not None else None
        utilization = used[rid] / available if available else None
        if metric and (metric.available_unit_seconds != available or metric.booked_unit_seconds != used[rid]
                       or metric.utilization != utilization):
            errors.append(f'{rid}: load metrics mismatch')
    by_op = {oid: a for a in assigned.values() for oid in a.operation_ids}
    order_ids = {o.order_id for o in s.operations}
    orders = {o.order_id: o for o in result.orders}
    if len(orders) != len(result.orders) or set(orders) != order_ids:
        errors.append('order metrics coverage mismatch')
    for oid in order_ids:
        ops = [o for o in s.operations if o.order_id == oid]
        complete = all(o.id in by_op for o in ops)
        due = min((utc(o.due) for o in ops if o.due), default=None)
        finish = max((by_op[o.id].end for o in ops if o.id in by_op), default=None) if complete else None
        late = max(0, int((finish - due).total_seconds())) if finish and due else None
        metric = orders.get(oid)
        if metric and (metric.complete != complete or metric.end != finish or metric.due != due or metric.tardiness_seconds != late):
            errors.append(f'{oid}: delivery metrics mismatch')
    return tuple(errors)
