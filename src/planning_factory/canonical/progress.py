"""Progress domain (#86): recorded progress -> remaining work, using the existing explicit residual policies.

``domain/durations.remaining_seconds`` is the single rule for residuals (none, time,
quantity). The policy is declared in the binding. A started operation stays on the
machine the source says it runs on. Freezing started work in time is the time fence
of #78 (P1) and is not decided here. Unknown is not ready: nothing is inferred
from missing progress besides "nothing recorded".
"""
from decimal import Decimal

from planning_factory.domain.durations import remaining_seconds
from planning_factory.domain.validation import ValidationError
from .model import Issue


def apply_progress(operations: dict, records, policy: str | None, horizon_start: str) -> list[Issue]:
    issues = []

    def error(code, key, detail):
        issues.append(Issue('error', code, 'progress', detail, entity='operation_progress', key=key))

    progress = records['operation_progress']
    if progress and policy is None:
        issues.append(Issue('error', 'missing_residual_policy', 'progress',
                            'operation_progress records need binding.domains.progress.residual_policy (none, time, quantity)'))
        return issues
    legacy = {r['operation_id'] for r in records['operation_state']}
    quantities = {r['id']: r.get('quantity') for r in records['operation']}
    for r in progress:
        key = r['operation_id']
        op = operations.get(key)
        if op is None:
            issues.append(Issue('error', 'unknown_reference', 'progress', f'operation {key!r} does not exist',
                                entity='operation_progress', key=key))
            continue
        if key in legacy:
            error('progress_conflict', key, 'both operation_state and operation_progress describe this operation')
            continue
        if r['status'] == 'completed':
            done = r.get('completed_at')
            if done is None:
                error('completed_without_date', key, 'completed operation without completed_at')
            elif done > horizon_start:
                error('completed_after_horizon_start', key, f'completed_at {done} is after the horizon start {horizon_start}')
            else:
                op.update(completed_at=done, duration_seconds=0, setup_seconds=0)
                for field in ('release', 'fixed_start', 'fixed_alternative'):
                    op.pop(field, None)
            continue
        quantity = quantities.get(key)
        if policy == 'quantity' and quantity is None:
            error('missing_quantity', key, 'residual_policy "quantity" needs operation.quantity')
            continue
        if policy == 'quantity':
            planned = Decimal(quantity)
            unit, count = Decimal(op['duration_seconds']) / planned, planned
        else:
            unit, count = Decimal(op['duration_seconds']), Decimal(1)
        try:
            work, setup = remaining_seconds(unit, count, op['setup_seconds'],
                                            produced_quantity=Decimal(r.get('produced_quantity', '0')),
                                            recorded_work_seconds=r.get('recorded_work_seconds', 0),
                                            recorded_setup_seconds=r.get('recorded_setup_seconds', 0), policy=policy)
        except ValidationError as exc:
            error('invalid_progress', key, '; '.join(exc.errors))
            continue
        if work + setup == 0:
            error('no_residual', key, 'started operation with no remaining time: close it at the source or correct the progress')
            continue
        op.update(duration_seconds=work, setup_seconds=setup)
        machine = r.get('resource_id')
        if machine is not None:
            uses = [any(d['resource_id'] == machine for d in a['demands']) for a in op['alternatives']]
            if not any(uses):
                error('progress_resource_conflict', key, f'running on {machine}, which none of its alternatives uses')
            else:
                op['alternatives'] = [a for a, use in zip(op['alternatives'], uses) if use]
                op.pop('preferred_alternative', None)  # running: the machine is known
    return issues
