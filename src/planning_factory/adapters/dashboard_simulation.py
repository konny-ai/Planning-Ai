"""Bounded, provenance-neutral browser what-if edits, never ERP writes.

Edits rebuild an immutable derived Scenario; historical bookings remain out
of scope. Fixed alternatives must already exist in the source Scenario.
Resource downtime is represented with a dedicated derived calendar so a
shared shift calendar never blocks other resources by accident.
"""
from dataclasses import replace

from planning_factory.adapters.json_io import timestamp
from planning_factory.domain.models import Interval, Scenario
from planning_factory.domain.validation import ValidationError, bundle_key, compile_tasks

MAX_EDITS = 40


def _array(data, key):
    value = data.get(key, [])
    if not isinstance(value, list) or len(value) > MAX_EDITS:
        raise ValidationError([f'{key}: array of up to {MAX_EDITS} items required'])
    return value


def _fix_nesting(source, task, item, changes, op_by_id):
    """Fix the start of a whole cutting program: every member gets the same start and the resource bundle
    chosen by ``alternative``, an index into the program's common bundles (the order of ``task.alternatives``)."""
    tid = task.id
    ops = [op_by_id[o] for o in task.operation_ids]
    if any(o.completed_at is not None or o.external_end is not None or o.fixed_start is not None for o in ops):
        raise ValidationError([f'{tid}: already completed/external/fixed in source'])
    alt = item['alternative']
    if type(alt) is not int or not 0 <= alt < len(task.alternatives):
        raise ValidationError([f'{tid}: alternative not found in the common technology of the nesting'])
    at = timestamp(item['start'])
    if at is None or not source.start <= at < source.end:
        raise ValidationError([f'{tid}: fixed start outside planning horizon'])
    wanted = bundle_key(task.alternatives[alt])
    for op in ops:
        if op.release is not None and at < op.release:
            raise ValidationError([f'{tid}: proposed start precedes material release'])
        index = next(i for i, a in enumerate(op.alternatives) if bundle_key(a) == wanted)
        changes[op.id] = replace(op, fixed_start=at, fixed_alternative=index)


def apply_whatif_edits(source: Scenario, edits: dict) -> Scenario:
    """Apply future-only fixed starts and resource downtime from a demo UI.

    No material reservations, frozen historical stage overrides or
    previously consumed time can be manufactured using this interface.
    """
    if not isinstance(edits, dict) or set(edits)-{'fixed_tasks', 'closures'}:
        raise ValidationError(['what-if edits must have only fixed_tasks and closures'])
    fixed, closures = _array(edits, 'fixed_tasks'), _array(edits, 'closures')
    tasks, _, _ = compile_tasks(source)
    op_by_id = {op.id: op for op in source.operations}
    used_tasks = set()
    changes = {}
    for item in fixed:
        if not isinstance(item, dict) or set(item) != {'task_id', 'start', 'alternative'}:
            raise ValidationError(['fixed task must contain task_id, start and alternative'])
        tid = item['task_id']
        if not isinstance(tid, str) or tid in used_tasks or tid not in tasks:
            raise ValidationError([f'unknown or duplicate fixed task: {tid!r}'])
        used_tasks.add(tid)
        task = tasks[tid]
        if tid.startswith('nest:'):
            _fix_nesting(source, task, item, changes, op_by_id)
            continue
        if not tid.startswith('job:') or len(task.operation_ids) != 1:
            raise ValidationError([f'{tid}: unsupported task kind'])
        op = op_by_id[task.operation_ids[0]]
        if op.completed_at is not None or op.external_end is not None or op.fixed_start is not None:
            raise ValidationError([f'{tid}: already completed/external/fixed in source'])
        alt = item['alternative']
        if type(alt) is not int or not 0 <= alt < len(op.alternatives):
            raise ValidationError([f'{tid}: alternative not found in original technology'])
        at = timestamp(item['start'])
        if at is None or not source.start <= at < source.end:
            raise ValidationError([f'{tid}: fixed start outside planning horizon'])
        if op.release is not None and at < op.release:
            raise ValidationError([f'{tid}: proposed start precedes material release'])
        changes[op.id] = replace(op, fixed_start=at, fixed_alternative=alt)

    resources = {r.id: r for r in source.resources}
    calendars = {c.id: c for c in source.calendars}
    holds = {}
    for item in closures:
        if not isinstance(item, dict) or set(item) != {'resource_id', 'start', 'end', 'reason'}:
            raise ValidationError(['closure requires resource_id, start, end, reason'])
        rid = item['resource_id']
        if not isinstance(rid, str) or rid not in resources:
            raise ValidationError([f'unknown resource for downtime: {rid!r}'])
        reason = item['reason']
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 160:
            raise ValidationError([f'{rid}: nonempty reason (max 160 characters) required'])
        at, until = timestamp(item['start']), timestamp(item['end'])
        if (at is None or until is None or not source.start <= at < until <= source.end):
            raise ValidationError([f'{rid}: closure outside horizon or invalid interval'])
        holds.setdefault(rid, []).append(Interval(at, until))

    updated_calendars = list(source.calendars)
    updated_resources = []
    used_names = set(calendars)
    for resource in source.resources:
        extra = holds.get(resource.id)
        if not extra:
            updated_resources.append(resource)
            continue
        base = calendars[resource.calendar_id]
        candidate = '__whatif__' + resource.id
        sequence = 1
        while candidate in used_names:
            candidate = '__whatif__' + resource.id + '_' + str(sequence)
            sequence += 1
        used_names.add(candidate)
        updated_calendars.append(replace(
            base, id=candidate, closures=base.closures + tuple(extra)))
        updated_resources.append(replace(resource, calendar_id=candidate))

    adjusted = replace(
        source,
        operations=tuple(changes.get(op.id, op) for op in source.operations),
        resources=tuple(updated_resources),
        calendars=tuple(updated_calendars),
    )
    compile_tasks(adjusted)
    return adjusted
