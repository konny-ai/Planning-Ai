"""Bounded opt-in machine-order improvement for ADR 003 setup/run plans.

Never assumes unapproved business weights: caller selects the objective.
All candidates are independently audited by the two-stage planner; no
production database, GUI, or legacy equivalence is involved.
"""
from dataclasses import dataclass
from datetime import UTC
from planning_factory.domain.models import Scenario
from planning_factory.domain.setup_contract import SetupContract
from planning_factory.domain.validation import ValidationError, compile_tasks
from .setup_stage_scheduler import StagedSetupResult, schedule_setup_stages


@dataclass(frozen=True)
class SequenceMetrics:
    late_orders: int
    tardiness_seconds: int
    setup_seconds: int
    makespan_seconds: int

    def as_dict(self):
        return dict(late_orders=self.late_orders,
                    tardiness_seconds=self.tardiness_seconds,
                    setup_seconds=self.setup_seconds,
                    makespan_seconds=self.makespan_seconds)


@dataclass(frozen=True)
class SequenceSearch:
    objective: str
    winner: StagedSetupResult
    sequence: tuple[str, ...]
    baseline: SequenceMetrics
    selected: SequenceMetrics
    evaluated: int
    rejected: int
    improved: bool


def sequence_metrics(s: Scenario, solution: StagedSetupResult) -> SequenceMetrics:
    """Compute KPIs from ORIGINAL orders; a nesting contributes to all owners."""
    tasks, _, _ = compile_tasks(s)
    owner = {o.id: o for o in s.operations}
    ends = {}
    due = {}
    for b in solution.bookings:
        if b.stage != 'run':
            continue
        for oid in tasks[b.phase_id].operation_ids:
            order = owner[oid].order_id
            end = b.segments[-1].end.astimezone(UTC)
            if order not in ends or ends[order] < end:
                ends[order] = end
            item_due = owner[oid].due
            if item_due is not None:
                item_due = item_due.astimezone(UTC)
                if order not in due or item_due < due[order]:
                    due[order] = item_due
    incomplete = {o.order_id for o in s.operations} - set(ends)
    if incomplete:
        raise ValidationError([f'incomplete orders in staged KPI: {sorted(incomplete)}'])
    tardy = [max(0, int((end - due[order]).total_seconds()))
             for order, end in ends.items() if order in due]
    setups = sum(sum(i.seconds for i in b.segments)
                 for b in solution.bookings if b.stage == 'setup')
    makespan = (int((max(ends.values()) - s.start.astimezone(UTC)).total_seconds())
                if ends else 0)
    return SequenceMetrics(sum(x > 0 for x in tardy), sum(tardy), setups, makespan)


def _score(m: SequenceMetrics, objective: str):
    if objective == 'delivery':
        return (m.late_orders, m.tardiness_seconds, m.setup_seconds, m.makespan_seconds)
    if objective == 'setup':
        return (m.setup_seconds, m.late_orders, m.tardiness_seconds, m.makespan_seconds)
    if objective == 'flow':
        return (m.makespan_seconds, m.late_orders, m.tardiness_seconds, m.setup_seconds)
    raise ValueError('objective must be explicitly chosen: delivery, setup or flow')


def _neighbours(sequence, machine_by_task):
    """Swap adjacent jobs on each machine, including noncontiguous global positions."""
    for mid in sorted(set(machine_by_task.values())):
        positions = [i for i, tid in enumerate(sequence) if machine_by_task[tid] == mid]
        for a, b in zip(positions, positions[1:]):
            candidate = list(sequence)
            candidate[a], candidate[b] = candidate[b], candidate[a]
            yield tuple(candidate)


def optimize_setup_sequence(s: Scenario, contract: SetupContract,
                            initial_sequence: tuple[str, ...], *,
                            objective: str, max_evaluations: int = 24,
                            max_alternatives: int = 32) -> SequenceSearch:
    """Bounded best-improvement search; no claim of global mathematical optimum.

    The initial sequence must be feasible. Every candidate is independently
    validated; impossible precedence or capacity configurations are rejected.
    Reports both baseline and selected KPIs, preserving baseline on ties.
    """
    _score(SequenceMetrics(0, 0, 0, 0), objective)
    if type(max_evaluations) is not int or not 1 <= max_evaluations <= 256:
        raise ValueError('max_evaluations must be integer 1..256')
    tasks, _, _ = compile_tasks(s)
    if len(initial_sequence) != len(tasks) or set(initial_sequence) != set(tasks):
        raise ValidationError(['initial machine sequence must contain each compiled task once'])
    machine_by_task = {p.id: p.machine_id for p in contract.phases}
    if set(machine_by_task) != set(tasks):
        raise ValidationError(['setup contract phases differ from run tasks'])
    winner = schedule_setup_stages(s, contract, initial_sequence,
                                   max_alternatives=max_alternatives)
    initial = sequence_metrics(s, winner)
    best = initial
    current = initial_sequence
    evaluated = 1
    rejected = 0
    seen = {initial_sequence}
    # Explore neighbours of the current best, but never repeat rejected cases.
    while evaluated < max_evaluations:
        improved = False
        for candidate in _neighbours(current, machine_by_task):
            if evaluated >= max_evaluations:
                break
            if candidate in seen:
                continue
            seen.add(candidate)
            evaluated += 1
            try:
                result = schedule_setup_stages(s, contract, candidate,
                                               max_alternatives=max_alternatives)
            except ValidationError:
                rejected += 1
                continue
            metrics = sequence_metrics(s, result)
            if _score(metrics, objective) < _score(best, objective):
                winner, best, current = result, metrics, candidate
                improved = True
        if not improved:
            break
    return SequenceSearch(objective, winner, current, initial, best,
                          evaluated, rejected, current != initial_sequence)
