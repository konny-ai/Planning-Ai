"""ADR 006, option B (chosen by the client): plan what can be planned.

Phases with no candidate resource are NOT planned and are reported with an
explicit code; their successors (and the other members of a nesting that
contains them) are reported as not planned too. Nothing is invented: no
resource, no duration, no date. The strict planner keeps failing closed; this
module is an opt-in entry point that never modifies its inputs.
"""
from dataclasses import dataclass, replace

from planning_factory.domain.models import Scenario
from planning_factory.domain.setup_contract import SetupContract
from planning_factory.domain.validation import ValidationError, compile_tasks
from .setup_stage_scheduler import StagedSetupResult, schedule_setup_stages

NO_CANDIDATE = 'NO_CANDIDATE'
PREDECESSOR_UNPLANNED = 'PREDECESSOR_UNPLANNED'
NEST_MEMBER_UNPLANNED = 'NEST_MEMBER_UNPLANNED'


@dataclass(frozen=True)
class Unplanned:
    operation_id: str
    code: str
    detail: str


@dataclass(frozen=True)
class PartialStagedResult:
    scenario: Scenario                       # the scenario actually planned
    contract: SetupContract
    machine_sequence: tuple[str, ...]
    staged: StagedSetupResult | None         # None when nothing is plannable
    backward: object | None
    unplanned: tuple[Unplanned, ...]


def plan_setup_stages_partial(s: Scenario, contract: SetupContract,
                              machine_sequence: tuple[str, ...] | None = None, *,
                              extra_without_candidates: tuple[str, ...] = (),
                              backward: bool = False, max_alternatives: int = 32,
                              frozen_bookings: tuple = ()) -> PartialStagedResult:
    """Plan every task that has candidates; report the rest instead of failing.

    A phase has no candidate when its operation lists no alternative (and is
    neither completed nor a supplier milestone) or when the caller names it in
    ``extra_without_candidates`` (operation IDs, e.g. from a data-quality
    `no_resource` finding). Frozen bookings of removed tasks are rejected.
    Without a ``machine_sequence`` the due-date order of the planned tasks is used.
    """
    ops = {o.id: o for o in s.operations}
    unknown = [x for x in extra_without_candidates if x not in ops]
    if unknown:
        raise ValidationError([f'unknown operation(s) without candidates: {sorted(unknown)}'])
    reason = {}
    for o in s.operations:
        if (not o.alternatives and o.completed_at is None and o.external_end is None) \
                or o.id in extra_without_candidates:
            reason[o.id] = (NO_CANDIDATE, 'no candidate resource declared')
    successors = {}
    for o in s.operations:
        for dep in o.predecessors:
            successors.setdefault(dep.predecessor_id, []).append(o.id)
    members = {n.id: set(n.operation_ids) for n in s.nestings}
    changed = True
    while changed:
        changed = False
        for oid in sorted(reason):
            for nxt in successors.get(oid, ()):
                if nxt not in reason:
                    reason[nxt] = (PREDECESSOR_UNPLANNED, f'depends on unplanned {oid}')
                    changed = True
        for nest_id, ids in sorted(members.items()):
            hit = sorted(i for i in ids if i in reason)
            if hit:
                for other in sorted(ids):
                    if other not in reason:
                        reason[other] = (NEST_MEMBER_UNPLANNED,
                                         f'nesting {nest_id} shares one run with unplanned {hit[0]}')
                        changed = True
    unplanned = tuple(Unplanned(oid, *reason[oid]) for oid in sorted(reason))
    kept_ops = tuple(o for o in s.operations if o.id not in reason)
    kept_nests = tuple(n for n in s.nestings if not (set(n.operation_ids) & set(reason)))
    narrowed = replace(s, operations=kept_ops, nestings=kept_nests)
    if not kept_ops:
        return PartialStagedResult(narrowed, replace(contract, phases=()), (), None, None, unplanned)
    tasks, _, _ = compile_tasks(narrowed)
    planned = {tid for tid, t in tasks.items() if t.external_end is None}
    removed_ids = set(machine_sequence or ()) | {p.id for p in contract.phases}
    removed_ids -= planned
    for booking in frozen_bookings:
        if booking.phase_id in removed_ids:
            raise ValidationError([f'{booking.phase_id}: frozen booking for a task that is not planned'])
    narrowed_contract = replace(contract, phases=tuple(
        p for p in contract.phases if p.id in planned))
    if machine_sequence is None:
        from .setup_sequence_search import default_machine_sequence
        machine_sequence = default_machine_sequence(narrowed)
    sequence = tuple(t for t in machine_sequence if t in planned)
    if set(sequence) != planned or {p.id for p in narrowed_contract.phases} != planned:
        raise ValidationError(['every planned task needs a canonical phase and a place in the sequence'])
    if backward:
        from .setup_sequence_search import backward_setup_before_run
        back = backward_setup_before_run(narrowed, narrowed_contract, sequence,
                                         max_alternatives=max_alternatives,
                                         frozen_bookings=frozen_bookings)
        return PartialStagedResult(back.scenario, narrowed_contract, sequence,
                                   back.solution, back, unplanned)
    staged = schedule_setup_stages(narrowed, narrowed_contract, sequence,
                                   max_alternatives=max_alternatives,
                                   frozen_bookings=frozen_bookings)
    return PartialStagedResult(narrowed, narrowed_contract, sequence, staged, None, unplanned)
