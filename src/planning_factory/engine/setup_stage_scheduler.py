"""Opt-in, limited, fully audited two-stage scheduler for ADR 003.

The original run-only scenario must contain NO v0.2 static setup seconds.
This scheduler never touches SQL/ERP. It does not claim to support historical
bookings, frozen starts, backward policies or every production dependency.
Unsupported cases fail closed. The canonical input remains ERP-independent.
"""
from dataclasses import dataclass
from itertools import combinations, product

from planning_factory.domain.models import (
    Alternative, Demand, Dependency, Operation, Resource, Scenario, Settings,
)
from planning_factory.domain.setup_contract import (
    SetupContract, StageRequirement, resolve_setup_states,
)
from planning_factory.domain.validation import ValidationError, compile_tasks
from .scheduler import plan
from .verification import verify_plan
from .setup_capacity import (
    StageAllocation, StageBooking, audit_stage_capacity,
)


@dataclass(frozen=True)
class StagedSetupResult:
    scenario: Scenario
    plan: object
    bookings: tuple[StageBooking, ...]
    # Maps compiled stage tasks to their original canonical phase and stage.
    stage_map: tuple[tuple[str, str, str], ...]


def _bundles(requirements: tuple[StageRequirement, ...], *, machine_id: str | None,
             max_alternatives: int) -> tuple[Alternative, ...]:
    if not requirements:
        raise ValidationError(['a positive-duration stage needs explicit resource requirements'])
    groups = []
    for req in requirements:
        groups.append(tuple(combinations(req.candidate_ids, req.count)))
    # Reject blow-ups instead of arbitrarily dropping an eligible alternative.
    total = 1
    for choices in groups:
        total *= len(choices)
        if total > max_alternatives:
            raise ValidationError([f'stage has {total}+ resource combinations: budget {max_alternatives} exceeded'])
    bundles = set()
    for selections in product(*groups):
        demands = tuple(sorted(
            (Demand(rid, req.occupancy_bps)
             for req, chosen in zip(requirements, selections) for rid in chosen),
            key=lambda x: x.resource_id))
        ids = [d.resource_id for d in demands]
        if len(ids) != len(set(ids)):
            # Ambiguous double demand on a single resource is not supported.
            continue
        if machine_id is not None and machine_id not in ids:
            continue
        bundles.add(demands)
    if not bundles:
        raise ValidationError(['no distinct resource combination includes required machine'])
    return tuple(Alternative(d) for d in sorted(bundles))


def schedule_setup_stages(s: Scenario, contract: SetupContract,
                          machine_sequence: tuple[str, ...], *,
                          max_alternatives: int = 32,
                          frozen_bookings: tuple[StageBooking, ...] = ()) -> StagedSetupResult:
    """Plan real setup and production stages with finite fractional capacity.

    The machine order is explicit. A phase's setup may cross a shift boundary
    and start before material arrives; run starts after release/dependencies.
    Setup never overlaps the preceding run on the same machine. Fully intact
    one-segment frozen stages can be pinned; partial WIP still fails closed.
    Already completed phases are history: they hold no booking, satisfy
    their successors' precedences and advance the observed machine state.
    """
    if type(max_alternatives) is not int or not 1 <= max_alternatives <= 256:
        raise ValueError('max_alternatives must be an integer between 1 and 256')
    tasks, edges, _ = compile_tasks(s)
    if any(o.setup_seconds for o in s.operations) or any(n.setup_seconds for n in s.nestings):
        raise ValidationError(['run-only input required; v0.2 setup_seconds would double-count'])
    phases = {p.id: p for p in contract.phases}
    if set(tasks) != set(phases) or len(machine_sequence) != len(tasks) or set(machine_sequence) != set(tasks):
        raise ValidationError(['ordered canonical phases must correspond exactly to compiled run tasks'])
    done = {pid for pid, t in tasks.items() if t.completed_at is not None}
    if any(t.mode != 'forward' or t.fixed_start is not None or t.fixed_alternative is not None
           or t.external_end is not None or (t.completed_at is None and t.duration_seconds <= 0)
           for t in tasks.values()):
        raise ValidationError(['staged setup planner currently requires active, unfixed forward run tasks '
                               '(already completed tasks are accepted as history)'])
    # A completed phase really ran: it updates the observed machine state, so it
    # must come before every still-active phase on the same machine.
    seen_active = set()
    for pid in machine_sequence:
        mid = phases[pid].machine_id
        if pid in done:
            if mid in seen_active:
                raise ValidationError([f'{pid}: completed phase must precede active phases on machine {mid}'])
        else:
            seen_active.add(mid)
    by_id = {r.id: r for r in s.resources}
    if any(r.id not in by_id or by_id[r.id].kind != r.kind or
           by_id[r.id].capacity is None for r in contract.resources):
        raise ValidationError(['every canonical resource must be a known finite resource of matching kind'])
    # Technical eligibility of the original Scenario is a hard constraint:
    # canonical requirements can enrich operators/tools but cannot introduce
    # machines excluded from the run alternatives.
    for pid, phase in phases.items():
        allowed = {
            demand.resource_id
            for alternative in tasks[pid].alternatives
            for demand in alternative.demands
            if by_id[demand.resource_id].kind == 'machine'
        }
        if phase.machine_id not in allowed:
            raise ValidationError([
                f'{pid}: canonical machine {phase.machine_id} is not an eligible run machine'
            ])
    # Frozen stages are trusted input only after a separate provenance check.
    # Never reconstruct or silently shorten a partially executed booking.
    frozen = {}
    for booking in frozen_bookings:
        key = (booking.phase_id, booking.stage)
        if key in frozen or booking.phase_id not in tasks or booking.stage not in ('setup', 'run'):
            raise ValidationError([f'invalid or duplicate frozen stage {key}'])
        if booking.phase_id in done:
            raise ValidationError([f'{key}: completed phase cannot also carry a frozen booking'])
        if len(booking.segments) != 1:
            raise ValidationError([f'{key}: frozen stage must have exactly one intact segment'])
        segment = booking.segments[0]
        if segment.start < s.start or segment.end > s.end or segment.seconds <= 0:
            raise ValidationError([f'{key}: frozen stage outside current horizon'])
        frozen[key] = booking

    def stage_pin(pid: str, kind: str, alternatives: tuple[Alternative, ...]):
        record = frozen.get((pid, kind))
        if record is None:
            return {}
        bundle = tuple(sorted(
            (Demand(a.resource_id, a.occupancy_bps) for a in record.allocations)))
        index = next((i for i, a in enumerate(alternatives)
                      if tuple(sorted(a.demands)) == bundle), None)
        if index is None:
            raise ValidationError([f'{pid}/{kind}: frozen resources not an eligible stage bundle'])
        return {'fixed_start': record.segments[0].start, 'fixed_alternative': index}

    scaled_resources = tuple(
        Resource(r.id, by_id[r.id].calendar_id, by_id[r.id].capacity * 10000, r.kind)
        for r in contract.resources)
    # Stable stage identities prevent unrelated machines from changing the
    # planner's ID tie-breaks when only one machine's sequence is rearranged.
    run_ids = {pid: f'stage_run__{pid}' for pid in machine_sequence}
    setup_ids = {pid: f'stage_setup__{pid}' for pid in machine_sequence}
    machine_prev = {}
    edges_for_run = {pid: [] for pid in machine_sequence}
    for edge in edges:
        if edge.predecessor in done or edge.successor in done:
            continue  # a completed predecessor is already satisfied
        edges_for_run[edge.successor].append(
            Dependency(run_ids[edge.predecessor], edge.kind, edge.lag_seconds))
    state = {d.phase_id: d for d in resolve_setup_states(contract, machine_sequence)}
    stage_ops = []
    mapping = []
    for pid in machine_sequence:
        task, phase = tasks[pid], phases[pid]
        decision = state[pid]
        if pid in done:
            continue  # history: no setup/run booking; state already advanced
        if phase.setup_locked and decision.requires_setup and decision.setup_seconds and (pid, 'setup') not in frozen:
            raise ValidationError([f'{pid}: locked setup requires a trusted frozen booking'])
        if (pid, 'setup') in frozen and not (decision.requires_setup and decision.setup_seconds):
            raise ValidationError([f'{pid}: frozen setup is not required by current machine state'])
        previous_run = machine_prev.get(phase.machine_id)
        setup_id = setup_ids[pid]
        run_id = run_ids[pid]
        required = decision.requires_setup and decision.setup_seconds > 0
        if required:
            setup_predecessors = ((Dependency(previous_run, 'FS'),)
                                  if previous_run is not None else ())
            setup_alts = _bundles(phase.setup_requirements, machine_id=None,
                                  max_alternatives=max_alternatives)
            stage_ops.append(Operation(
                setup_id, f'technical:{pid}', phase.setup_seconds,
                setup_alts,
                predecessors=setup_predecessors, release=s.start,
                splittable=(pid, 'setup') not in frozen, mode='forward',
                priority=task.priority,
                **stage_pin(pid, 'setup', setup_alts),
            ))
            mapping.append((f'job:{setup_id}', pid, 'setup'))
            edges_for_run[pid].append(Dependency(setup_id, 'FS'))
        elif previous_run is not None:
            edges_for_run[pid].append(Dependency(previous_run, 'FS'))
        run_alts = _bundles(phase.run_requirements, machine_id=phase.machine_id,
                            max_alternatives=max_alternatives)
        stage_ops.append(Operation(
            run_id, f'technical:{pid}', task.duration_seconds,
            run_alts,
            predecessors=tuple(edges_for_run[pid]), release=task.release,
            due=task.due, priority=task.priority, mode='forward',
            splittable=task.splittable and (pid, 'run') not in frozen,
            **stage_pin(pid, 'run', run_alts),
        ))
        mapping.append((f'job:{run_id}', pid, 'run'))
        machine_prev[phase.machine_id] = run_id
    stage_scenario = Scenario(
        id=f'{s.id}:setup-stage-v1', start=s.start, end=s.end,
        calendars=s.calendars, resources=scaled_resources,
        operations=tuple(sorted(stage_ops, key=lambda o: o.id)),
        settings=Settings(mode='forward'))
    result = plan(stage_scenario)
    problems = verify_plan(stage_scenario, result)
    if problems or result.unscheduled:
        raise ValidationError(['staged schedule not feasible/audited',
                               *problems, *(f'{u.task_id}: {u.code}' for u in result.unscheduled)])
    stage_info = {key: (pid, mode) for key, pid, mode in mapping}
    booking = []
    for assignment in result.assignments:
        pid, stage = stage_info[assignment.task_id]
        booking.append(StageBooking(pid, stage, assignment.segments,
                                    tuple(StageAllocation(d.resource_id, d.units)
                                          for d in assignment.demands)))
    violations = audit_stage_capacity(contract, s, machine_sequence, tuple(booking),
                                      frozen_bookings=frozen_bookings)
    if violations:
        raise ValidationError(['staged schedule failed independent capacity audit', *violations])
    return StagedSetupResult(stage_scenario, result, tuple(booking), tuple(mapping))
