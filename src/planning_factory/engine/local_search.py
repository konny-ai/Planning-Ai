"""Deterministic, bounded first-improvement search over dispatch-order neighbors.

No global optimum claim: each trial recomputes and independently audits a full
finite-capacity plan, and the immutable scenario is never modified.
"""
from dataclasses import dataclass

from planning_factory.domain.models import Plan, Scenario
from .optimization import DEFAULT_OBJECTIVES, CandidateMetrics, check_objectives, measure
from .scheduler import plan
from .time_budget import Deadline


@dataclass(frozen=True)
class LocalSearchResult:
    plan: Plan
    baseline: CandidateMetrics
    winner: CandidateMetrics
    evaluated_neighbors: int
    budget: int
    accepted_improvements: int
    final_dispatch_order: tuple[str, ...]
    rejected: tuple[str, ...]
    time_limit_reached: bool = False

    @property
    def improved(self) -> bool:
        return self.winner != self.baseline


def local_search(s: Scenario, *, budget: int = 32, time_limit_seconds: float | None = None,
                 clock=None, objectives: tuple[str, ...] = DEFAULT_OBJECTIVES,
                 published: Plan | None = None) -> LocalSearchResult:
    """Try up to `budget` distinct adjacent swaps of non-pinned tasks.

    Baseline is always the unchanged priority planner; a candidate must improve
    the same lexicographic score already published by the optimizer. On ties,
    the previously accepted plan wins. `budget` counts actual planning runs,
    not the cost of computing the baseline.
    """
    if type(budget) is not int or not 1 <= budget <= 64:
        raise ValueError('local search budget must be an integer between 1 and 64')

    objectives = check_objectives(objectives, published)
    deadline = Deadline(time_limit_seconds, *(() if clock is None else (clock,)))
    current = plan(s)
    baseline = measure(s, current, 'priority', published)
    score = baseline
    pinned = {
        op.id for op in s.operations
        if op.fixed_start is not None or op.completed_at is not None or op.external_end is not None
    }
    # An atomic nesting with any pinned member is treated as pinned.
    tasks = {}
    for a in current.assignments:
        tasks[a.task_id] = a
    for u in current.unscheduled:
        tasks[u.task_id] = u
    ordered = [
        a.task_id for a in sorted(current.assignments, key=lambda a: (a.start, a.task_id))
    ] + sorted(u.task_id for u in current.unscheduled)
    # Do not perturb the order of pinned tasks, but their positions are retained.
    movable = {
        a.task_id for a in current.assignments
        if a.segments and not any(oid in pinned for oid in a.operation_ids)
    }
    movable.update(
        u.task_id for u in current.unscheduled
        if not any(oid in pinned for oid in u.operation_ids)
    )
    tried = set()
    rejected = []
    evaluated = 0
    improvements = 0
    reached = False
    while evaluated < budget and not reached:
        improved_this_round = False
        for i in range(len(ordered) - 1):
            if evaluated >= budget:
                break
            if deadline.expired():
                reached = True
                break
            if ordered[i] not in movable or ordered[i + 1] not in movable:
                continue
            trial = list(ordered)
            trial[i], trial[i + 1] = trial[i + 1], trial[i]
            key = tuple(trial)
            if key in tried:
                continue
            tried.add(key)
            evaluated += 1
            try:
                proposal = plan(s, dispatch_order=key)
                metrics = measure(s, proposal, f'local_swap:{i}', published)
            except (RuntimeError, ValueError) as exc:
                rejected.append(f'swap at position {i}: {exc}')
                continue
            if metrics.key(objectives) < score.key(objectives):
                current, score, ordered = proposal, metrics, trial
                improvements += 1
                improved_this_round = True
                break
        if not improved_this_round:
            break
    return LocalSearchResult(current, baseline, score, evaluated,
                             budget, improvements, tuple(ordered), tuple(rejected), reached)
