"""Scenario comparison and explicit freeze of starts/resources from a prior plan."""
from dataclasses import replace
from planning_factory.domain.models import Plan, Scenario
from planning_factory.domain.validation import ValidationError, compile_tasks
from .verification import verify_plan


def compare(before: Plan, after: Plan) -> dict:
    """Compare assignment stability across independently generated plans.

    A fragmented task may change its internal machine occupancy while keeping
    the same first/last instant. Compare every booked segment, not merely the
    headline start/end, or the stability KPI would hide those changes.
    """
    old = {oid: a for a in before.assignments for oid in a.operation_ids}
    new = {oid: a for a in after.assignments for oid in a.operation_ids}
    all_ids = sorted(old.keys() | new.keys())
    changes = []
    for oid in all_ids:
        a, b = old.get(oid), new.get(oid)
        segments_changed = a is None or b is None or a.segments != b.segments
        resources_changed = a is None or b is None or a.demands != b.demands
        milestone_changed = a is None or b is None or (
            a.completed_at, a.external_end) != (b.completed_at, b.external_end)
        if segments_changed or resources_changed or milestone_changed:
            changes.append({'operation_id': oid, 'before_start': a.start if a else None,
                            'after_start': b.start if b else None,
                            'shift_seconds': int((b.start - a.start).total_seconds()) if a and b else None,
                            'segments_changed': segments_changed,
                            'resources_changed': resources_changed,
                            'before_resources': a.demands if a else (), 'after_resources': b.demands if b else ()})
    shifts = [abs(item['shift_seconds']) for item in changes
              if item['shift_seconds'] is not None]
    return {'before_digest': before.scenario_digest, 'after_digest': after.scenario_digest,
            'changed_operations': changes,
            'assignment_operations_compared': len(all_ids),
            'unchanged_assignment_operations': len(all_ids) - len(changes),
            'changed_assignment_operations': len(changes),
            'operations_with_resource_change': sum(c['resources_changed'] for c in changes),
            'operations_with_fragment_change': sum(c['segments_changed'] for c in changes),
            'total_absolute_start_shift_seconds': sum(shifts),
            'max_absolute_start_shift_seconds': max(shifts, default=0),
            'before_unscheduled': len(before.unscheduled),
            'after_unscheduled': len(after.unscheduled),
            'before_tardiness_seconds': sum(o.tardiness_seconds or 0 for o in before.orders),
            'after_tardiness_seconds': sum(o.tardiness_seconds or 0 for o in after.orders)}


def freeze(s: Scenario, baseline: Plan, operation_ids) -> Scenario:
    """Pin starts AND bundles. For interrupted work, subsequent fragments may move.

    A nesting is always frozen atomically, even when only one member is selected.
    """
    errors = verify_plan(s, baseline)
    if errors:
        raise ValidationError(list(errors))
    tasks, _, _ = compile_tasks(s)
    selected = set(operation_ids)
    known = {o.id for o in s.operations}
    if selected - known:
        raise ValidationError(['cannot freeze unknown operations'])
    by_op = {oid: a for a in baseline.assignments for oid in a.operation_ids}
    expanded = set()
    for t in tasks.values():
        if selected.intersection(t.operation_ids):
            expanded.update(t.operation_ids)
    operations = []
    for op in s.operations:
        if op.id not in expanded or op.completed_at is not None:
            operations.append(op)
            continue
        a = by_op.get(op.id)
        if a is None:
            raise ValidationError([f'{op.id}: cannot freeze an unscheduled operation'])
        index = next(i for i, alt in enumerate(op.alternatives) if tuple(sorted(alt.demands)) == tuple(sorted(a.demands)))
        operations.append(replace(op, fixed_start=a.start, fixed_alternative=index))
    return replace(s, operations=tuple(operations))
