"""Explicit full-capacity future holds for resources still occupied at snapshot.

Do not infer active machine setup state from a running operation. All holds
originate from an attested source and block the entire resource calendar up
to a declared future release. Fractional holds need a separate booking model.
"""
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from planning_factory.domain.models import Interval, Scenario
from planning_factory.domain.setup_contract import SetupContract
from planning_factory.domain.validation import ValidationError, compile_tasks, instant


@dataclass(frozen=True)
class ActiveResourceHold:
    resource_id: str
    observed_at: datetime
    reserved_until: datetime
    source_ref: str
    occupying_task_id: str  # already running, must NOT also appear in this scenario


@dataclass(frozen=True)
class HeldResourceScenario:
    scenario: Scenario
    setup_contract: SetupContract
    held_resources: tuple[str, ...]


def apply_full_capacity_holds(
    scenario: Scenario,
    contract: SetupContract,
    holds: tuple[ActiveResourceHold, ...],
) -> HeldResourceScenario:
    """Use dedicated calendars so one held machine never blocks its peers.

    A hold is the complete occupancy of one capacity=1 resource from the
    scenario start to an explicitly observed, future release timestamp.
    Machine state is reset to UNKNOWN after occupancy; only a subsequent
    independent state observation may justify skipping setup.
    """
    tasks, _, _ = compile_tasks(scenario)
    operations = {oid for task in tasks.values() for oid in task.operation_ids}
    machines = {r.id for r in contract.resources if r.kind == 'machine'}
    by_resource = {r.id: r for r in scenario.resources}
    by_calendar = {c.id: c for c in scenario.calendars}
    used_names = set(by_calendar)
    held = {}
    horizon_start = scenario.start.astimezone(UTC)
    horizon_end = scenario.end.astimezone(UTC)
    for item in holds:
        if item.resource_id not in by_resource or item.resource_id in held:
            raise ValidationError([f'unknown or duplicate active resource {item.resource_id!r}'])
        if by_resource[item.resource_id].capacity != 1:
            raise ValidationError([f'{item.resource_id}: full resource hold needs unit capacity'])
        if not isinstance(item.source_ref, str) or not item.source_ref.strip():
            raise ValidationError([f'{item.resource_id}: active resource source_ref required'])
        if (not isinstance(item.occupying_task_id, str) or
                not item.occupying_task_id.strip()):
            raise ValidationError([f'{item.resource_id}: occupying task ID required'])
        if item.occupying_task_id in tasks or item.occupying_task_id in operations:
            raise ValidationError([
                f'{item.resource_id}: already executing task must be excluded from new scenario'
            ])
        instant(item.observed_at, f'{item.resource_id}.observed_at')
        instant(item.reserved_until, f'{item.resource_id}.reserved_until')
        if item.observed_at.astimezone(UTC) != horizon_start:
            raise ValidationError([f'{item.resource_id}: hold observation must match horizon start'])
        if not horizon_start < item.reserved_until.astimezone(UTC) <= horizon_end:
            raise ValidationError([f'{item.resource_id}: invalid future hold release'])
        held[item.resource_id] = item

    new_calendars = list(scenario.calendars)
    new_resources = []
    for resource in scenario.resources:
        hold = held.get(resource.id)
        if hold is None:
            new_resources.append(resource)
            continue
        prefix = f'active-hold::{resource.id}'
        new_id = prefix
        count = 1
        while new_id in used_names:
            new_id = f'{prefix}::{count}'
            count += 1
        used_names.add(new_id)
        source_calendar = by_calendar[resource.calendar_id]
        new_calendars.append(replace(
            source_calendar, id=new_id,
            closures=source_calendar.closures + (
                Interval(scenario.start, hold.reserved_until),)))
        new_resources.append(replace(resource, calendar_id=new_id))

    state = dict(contract.initial_machine_states)
    for machine in machines.intersection(held):
        state[machine] = None  # no post-job state can be inferred
    updated = replace(scenario, resources=tuple(new_resources),
                      calendars=tuple(new_calendars))
    compile_tasks(updated)
    return HeldResourceScenario(updated, replace(
        contract, initial_machine_states=tuple(sorted(state.items()))),
        tuple(sorted(held)))
