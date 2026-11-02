"""Fail-closed conversion of observed, PAUSED work-in-progress to residual runs.

No connection to Factory. No deduction of productive machine state from phase
names or completion counters. Operating machines, residual setups, consumed
materials and history across the planning horizon require separate evidence.
"""
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from planning_factory.domain.models import Scenario
from planning_factory.domain.setup_contract import SetupContract
from planning_factory.domain.validation import ValidationError, compile_tasks, instant


@dataclass(frozen=True)
class MachineObservation:
    machine_id: str
    observed_at: datetime
    state_id: str | None  # None = unknown; never skip setup on unknown
    source_ref: str
    idle_confirmed: bool  # explicit: no physical job holds the machine now


@dataclass(frozen=True)
class PausedRunProgress:
    task_id: str  # job:id or nest:id; nesting stays atomic
    machine_id: str
    original_seconds: int
    worked_seconds: int
    remaining_seconds: int
    paused_at: datetime
    source_ref: str


@dataclass(frozen=True)
class WipPreparation:
    scenario: Scenario
    setup_contract: SetupContract
    progressed_tasks: tuple[str, ...]


def prepare_paused_wip(
    scenario: Scenario,
    setup_contract: SetupContract,
    machine_sequence: tuple[str, ...],
    observations: tuple[MachineObservation, ...],
    progresses: tuple[PausedRunProgress, ...],
) -> WipPreparation:
    """Prepare a residual scenario only with a complete as-of-now idle snapshot.

    The call-site must use a trusted source that reports all current machines
    and work progress at the planning horizon start, never a stale mix.
    """
    tasks, _, _ = compile_tasks(scenario)
    phases = {p.id: p for p in setup_contract.phases}
    if set(phases) != set(tasks):
        raise ValidationError(['WIP phase contract must cover every compiled task'])
    if len(machine_sequence) != len(tasks) or set(machine_sequence) != set(tasks):
        raise ValidationError(['WIP requires an explicit complete machine sequence'])
    machines = {r.id for r in setup_contract.resources if r.kind == 'machine'}
    if not machines:
        raise ValidationError(['WIP requires explicit machine resources'])
    observed = {}
    instant(scenario.start, 'scenario.start')
    as_of = scenario.start.astimezone(UTC)
    for obs in observations:
        instant(obs.observed_at, 'machine observation time')
        if (obs.machine_id not in machines or obs.machine_id in observed or
                not isinstance(obs.source_ref, str) or not obs.source_ref.strip() or
                type(obs.idle_confirmed) is not bool):
            raise ValidationError([f'invalid machine observation {obs.machine_id!r}'])
        if obs.observed_at.astimezone(UTC) != as_of:
            raise ValidationError([f'{obs.machine_id}: machine observation must match horizon start'])
        if not obs.idle_confirmed:
            raise ValidationError([f'{obs.machine_id}: active machine occupancy requires historical/future bookings'])
        if obs.state_id is not None and (not isinstance(obs.state_id, str) or not obs.state_id.strip()):
            raise ValidationError([f'{obs.machine_id}: invalid observed state'])
        observed[obs.machine_id] = obs
    if set(observed) != machines:
        raise ValidationError(['WIP requires an observation for every canonical machine'])

    changed = {}
    first_by_machine = {}
    for tid in machine_sequence:
        first_by_machine.setdefault(phases[tid].machine_id, tid)
    for progress in progresses:
        if progress.task_id not in tasks or progress.task_id in changed:
            raise ValidationError([f'unknown or duplicate WIP task {progress.task_id!r}'])
        if progress.machine_id != phases[progress.task_id].machine_id:
            raise ValidationError([f'{progress.task_id}: progress machine differs from canonical phase'])
        if first_by_machine[progress.machine_id] != progress.task_id:
            raise ValidationError([f'{progress.task_id}: resumed WIP must be first on its machine'])
        if (type(progress.original_seconds) is not int or
                type(progress.worked_seconds) is not int or
                type(progress.remaining_seconds) is not int or
                progress.worked_seconds <= 0 or progress.remaining_seconds <= 0 or
                progress.original_seconds != progress.worked_seconds + progress.remaining_seconds or
                progress.original_seconds != tasks[progress.task_id].duration_seconds):
            raise ValidationError([f'{progress.task_id}: inconsistent exact remaining run duration'])
        instant(progress.paused_at, f'{progress.task_id}.paused_at')
        if progress.paused_at.astimezone(UTC) > as_of:
            raise ValidationError([f'{progress.task_id}: pause is after planning horizon start'])
        if not isinstance(progress.source_ref, str) or not progress.source_ref.strip():
            raise ValidationError([f'{progress.task_id}: progress source reference required'])
        if (observed[progress.machine_id].state_id is None or
                observed[progress.machine_id].state_id != phases[progress.task_id].target_state):
            raise ValidationError([f'{progress.task_id}: observed machine setup state not confirmed'])
        if (tasks[progress.task_id].fixed_start is not None or
                tasks[progress.task_id].completed_at is not None or
                tasks[progress.task_id].external_end is not None):
            raise ValidationError([f'{progress.task_id}: already fixed/completed/external'])
        changed[progress.task_id] = progress.remaining_seconds

    operation_to_task = {oid: tid for tid, task in tasks.items() for oid in task.operation_ids}
    operations = tuple(
        replace(op, duration_seconds=changed[operation_to_task[op.id]],
                release=max((op.release or scenario.start).astimezone(UTC), as_of))
        if operation_to_task[op.id] in changed and operation_to_task[op.id].startswith('job:')
        else op
        for op in scenario.operations
    )
    nestings = tuple(
        replace(n, duration_seconds=changed['nest:' + n.id])
        if 'nest:' + n.id in changed else n for n in scenario.nestings
    )
    # Nests retain the original members' task-level releases; use a common
    # as-of lower bound, never duplicate the residual across member durations.
    operations = tuple(
        replace(op, release=max((op.release or scenario.start).astimezone(UTC), as_of))
        if operation_to_task[op.id] in changed and operation_to_task[op.id].startswith('nest:')
        else op for op in operations
    )
    updated_contract = replace(
        setup_contract,
        initial_machine_states=tuple(sorted(
            (mid, obs.state_id) for mid, obs in observed.items())))
    updated = replace(scenario, operations=operations, nestings=nestings)
    compile_tasks(updated)
    return WipPreparation(updated, updated_contract, tuple(sorted(changed)))
