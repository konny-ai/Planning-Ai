"""Independent capacity audit for opt-in ADR 003 setup and run stages.

Resource demands use integer basis points, rather than the integer
resource units of the legacy v0.2 Plan. This auditor never edits a Plan,
does not publish ERP data, and is NOT a replacement for precedence audits.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from planning_factory.domain.models import Interval, Scenario
from planning_factory.domain.setup_contract import SetupContract, resolve_setup_states
from planning_factory.domain.validation import ValidationError, compile_tasks
from .calendars import expand, intersect


@dataclass(frozen=True)
class StageAllocation:
    resource_id: str
    occupancy_bps: int


@dataclass(frozen=True)
class StageBooking:
    phase_id: str
    stage: str  # setup or run
    segments: tuple[Interval, ...]
    allocations: tuple[StageAllocation, ...]


def audit_stage_capacity(contract: SetupContract, s: Scenario,
                         machine_sequence: tuple[str, ...],
                         bookings: tuple[StageBooking, ...], *,
                         frozen_bookings: tuple[StageBooking, ...] = ()) -> tuple[str, ...]:
    """Return exact rule/calendar/capacity violations for an explicit stage plan.

    Stage IDs match compiled task IDs (job:X or nest:X), not member phases of
    an atomic nesting. Only scenarios with setup_seconds=0 in their v0.2
    operations/nestings are accepted: otherwise a parallel v0.2 booking would
    double count setup, which this audit must never approve. Frozen stage
    snapshots can be compared by exact immutable segments and allocations;
    a setup_locked phase requires such a reference when setup is needed.
    """
    tasks, edges, _ = compile_tasks(s)
    if any(o.setup_seconds for o in s.operations) or any(n.setup_seconds for n in s.nestings):
        raise ValidationError(['stage audit requires a run-only v0.2 scenario (no static setup to double-count)'])
    phases = {p.id: p for p in contract.phases}
    if set(phases) != set(tasks):
        raise ValidationError(['every compiled job/nesting task needs exactly one canonical phase'])
    if len(machine_sequence) != len(phases) or set(machine_sequence) != set(phases):
        raise ValidationError(['machine sequence must contain each task exactly once'])
    if any(rid not in {r.id for r in s.resources} for rid in
           (r.id for r in contract.resources)):
        raise ValidationError(['a canonical setup resource is missing from the scenario'])
    resources = {r.id: r for r in s.resources}
    declared = {r.id: r for r in contract.resources}
    if any(resources[rid].kind != d.kind for rid, d in declared.items()):
        raise ValidationError(['resource kind mismatch between canonical setup contract and scenario'])
    # Recheck machine eligibility without trusting the scheduler output.
    disallowed = []
    for pid, phase in phases.items():
        eligible = {
            demand.resource_id
            for alternative in tasks[pid].alternatives
            for demand in alternative.demands
            if resources[demand.resource_id].kind == 'machine'
        }
        if phase.machine_id not in eligible:
            disallowed.append(f'{pid}: canonical machine not in eligible run alternatives')
    if disallowed:
        return tuple(sorted(disallowed))
    decisions = {d.phase_id: d for d in resolve_setup_states(contract, machine_sequence)}
    records = {}
    errors = []
    previous_machine_run_end = {}
    for b in bookings:
        key = b.phase_id, b.stage
        if b.phase_id not in phases or b.stage not in ('setup', 'run'):
            errors.append(f'{key}: unknown phase or stage')
            continue
        if key in records:
            errors.append(f'{key}: duplicate stage booking')
            continue
        records[key] = b
    # A frozen snapshot is authoritative only for the exact stages named.
    # The audit still checks all those stages' durations, capacity and calendars.
    # For a locked setup, an explicitly frozen setup booking is mandatory.
    frozen = {}
    for record in frozen_bookings:
        key = record.phase_id, record.stage
        if key in frozen:
            errors.append(f'{key}: duplicate frozen booking')
            continue
        if record.phase_id not in phases or record.stage not in ('setup', 'run'):
            errors.append(f'{key}: unknown frozen booking')
            continue
        frozen[key] = record
        if key not in records:
            errors.append(f'{key}: frozen booking missing from current plan')
        elif records[key] != record:
            errors.append(f'{key}: frozen booking was changed')
    events = defaultdict(lambda: defaultdict(int))
    calendar_cache = {}
    for pid in machine_sequence:
        phase = phases[pid]
        decision = decisions[pid]
        setup_required = decision.requires_setup and decision.setup_seconds > 0
        if phase.setup_locked and setup_required and (pid, 'setup') not in frozen:
            # A flag alone is not evidence that the original setup was kept.
            errors.append(f'{pid}: locked setup requires frozen booking reference')
        for stage in ('setup', 'run'):
            booking = records.get((pid, stage))
            required = stage == 'run' or (stage == 'setup' and setup_required)
            if required and booking is None:
                errors.append(f'{pid}/{stage}: missing required booking')
                continue
            if not required and booking is not None:
                errors.append(f'{pid}/{stage}: unexpected booking (already prepared or zero duration)')
                continue
            if not required:
                continue
            length = (phase.setup_seconds if stage == 'setup'
                      else tasks[pid].duration_seconds)
            if not booking.segments or sum(seg.seconds for seg in booking.segments) != length:
                errors.append(f'{pid}/{stage}: segment duration does not equal required duration')
            if any(seg.seconds <= 0 or seg.start < s.start or seg.end > s.end
                   for seg in booking.segments):
                errors.append(f'{pid}/{stage}: segments outside horizon or empty')
            if any(a.end > b.start for a, b in
                   zip(booking.segments, booking.segments[1:])):
                errors.append(f'{pid}/{stage}: segments overlap or unordered')
            requirements = (phase.setup_requirements if stage == 'setup'
                            else phase.run_requirements)
            chosen = {}
            for x in booking.allocations:
                if (x.resource_id in chosen or x.resource_id not in declared or
                        type(x.occupancy_bps) is not int or
                        not 1 <= x.occupancy_bps <= 10000):
                    errors.append(f'{pid}/{stage}: duplicate/unknown/invalid resource allocation')
                    continue
                chosen[x.resource_id] = x.occupancy_bps
            used = set()
            for requirement in requirements:
                matches = {rid for rid in chosen if rid in requirement.candidate_ids
                           and rid not in used and
                           chosen[rid] == requirement.occupancy_bps}
                if len(matches) != requirement.count:
                    errors.append(f'{pid}/{stage}: resources or percentages do not meet '
                                  f'{requirement.kind} count {requirement.count}')
                used.update(matches)
            if used != set(chosen):
                errors.append(f'{pid}/{stage}: undeclared resource or wrong occupancy')
            if stage == 'run' and phase.machine_id not in chosen:
                errors.append(f'{pid}/run: assigned machine must be one of the booked resources')
            for rid, bps in chosen.items():
                resource = resources.get(rid)
                if resource is None:
                    continue
                if resource.capacity is None:
                    errors.append(f'{pid}/{stage}: infinite capacity not supported in finite setup audit')
                    continue
                if rid not in calendar_cache:
                    calendar = next(c for c in s.calendars if c.id == resource.calendar_id)
                    calendar_cache[rid] = expand(calendar, s.start, s.end)
                availability = calendar_cache[rid]
                for seg in booking.segments:
                    inside = intersect(availability, (seg,))
                    if sum(i.seconds for i in inside) != seg.seconds:
                        errors.append(f'{pid}/{stage}: {rid} outside working calendar')
                    events[rid][seg.start] += bps
                    events[rid][seg.end] -= bps
        setup = records.get((pid, 'setup'))
        run = records.get((pid, 'run'))
        if run and run.segments:
            if run.segments[0].start < tasks[pid].release:
                errors.append(f'{pid}: processing starts before release or material availability')
            machine_ready = previous_machine_run_end.get(phase.machine_id)
            if machine_ready is not None:
                if run.segments[0].start < machine_ready:
                    errors.append(f'{pid}: machine order is not chronological/non-interleaved')
                if setup and setup.segments and setup.segments[0].start < machine_ready:
                    errors.append(f'{pid}: setup overlaps preceding machine job (external pre-setup not approved)')
            previous_machine_run_end[phase.machine_id] = run.segments[-1].end
        if setup and run and setup.segments and run.segments:
            if setup.segments[-1].end > run.segments[0].start:
                errors.append(f'{pid}: setup must finish before processing starts')
    # The stage-capacity audit must not accept a physically valid booking
    # that violates the original scenario's inter-task dependencies.
    # Setup may start ahead of materials or predecessors; run may not.
    for edge in edges:
        predecessor = records.get((edge.predecessor, 'run'))
        successor = records.get((edge.successor, 'run'))
        if (predecessor is None or successor is None or
                not predecessor.segments or not successor.segments):
            continue  # Missing or empty bookings are reported above.
        origin = (predecessor.segments[-1].end if edge.kind == 'FS'
                  else predecessor.segments[0].start)
        earliest = origin + timedelta(seconds=edge.lag_seconds)
        if successor.segments[0].start < earliest:
            errors.append(
                f'{edge.successor}: {edge.kind} predecessor {edge.predecessor} '
                f'with lag {edge.lag_seconds}s violated by run start'
            )
    for rid, series in sorted(events.items()):
        used = 0
        capacity = resources[rid].capacity * 10000
        for t in sorted(series):
            used += series[t]
            if used > capacity or used < 0:
                errors.append(f'{rid}: occupancy {used}/{capacity} basis points at {t.isoformat()}')
    return tuple(errors)
