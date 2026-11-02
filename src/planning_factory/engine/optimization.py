"""Bounded, deterministic, opt-in search over dispatch heuristics.

This is a policy search, not a global optimality certificate. Every candidate is
audited against the same immutable scenario before it can be ranked.
"""
from dataclasses import dataclass
from planning_factory.domain.models import Plan, Scenario
from .scheduler import plan
from .time_budget import Deadline
from .verification import verify_plan

DISPATCH_POLICIES = (
    'priority',
    'earliest_due',
    'shortest_processing',
    'longest_processing',
)
# Slack-based policies (#78 P2): opt-in through ``policies``; the default search is unchanged.
SLACK_POLICIES = ('min_slack', 'critical_ratio', 'atc')
ALL_POLICIES = DISPATCH_POLICIES + SLACK_POLICIES


@dataclass(frozen=True)
class CandidateMetrics:
    policy: str
    unscheduled_operations: int
    late_orders: int
    total_tardiness_seconds: int
    makespan_seconds: int
    max_tardiness_seconds: int = 0
    moved_operations: int | None = None  # only measured against a published plan (P18)

    @property
    def ranking(self) -> tuple[int, int, int, int]:
        return (self.unscheduled_operations, self.late_orders,
                self.total_tardiness_seconds, self.makespan_seconds)

    def key(self, objectives: tuple[str, ...]) -> tuple[int, ...]:
        """Lexicographic key for a declared objective profile (#78 P17)."""
        return tuple(getattr(self, name) for name in objectives)


DEFAULT_OBJECTIVES = ('unscheduled_operations', 'late_orders', 'total_tardiness_seconds', 'makespan_seconds')
OBJECTIVES = DEFAULT_OBJECTIVES + ('max_tardiness_seconds', 'moved_operations')


def check_objectives(objectives, published) -> tuple[str, ...]:
    objectives = tuple(objectives)
    if not objectives or len(set(objectives)) != len(objectives) or any(o not in OBJECTIVES for o in objectives):
        raise ValueError(f'objectives must be distinct names among {list(OBJECTIVES)}')
    if 'moved_operations' in objectives and published is None:
        raise ValueError('the moved_operations objective needs the published plan')
    return objectives


@dataclass(frozen=True)
class OptimizationResult:
    winner_policy: str
    plan: Plan
    candidates: tuple[CandidateMetrics, ...]
    rejected: tuple[str, ...] = ()
    pareto_frontier: tuple[str, ...] = ()
    time_limit_reached: bool = False


def pareto_policies(metrics: tuple[CandidateMetrics, ...]) -> tuple[str, ...]:
    """Return non-dominated policies in their original evaluation order.

    A candidate dominates another only when it is no worse on EVERY
    declared objective and strictly better on at least one. The current
    lexicographic winner remains unchanged; this is diagnostic information
    for business trade-off decisions, not a new selection policy.
    """
    return tuple(item.policy for item in metrics
                 if not any(other.ranking != item.ranking
                            and all(a <= b for a, b in zip(other.ranking, item.ranking))
                            and any(a < b for a, b in zip(other.ranking, item.ranking))
                            for other in metrics))


def measure(s: Scenario, p: Plan, policy: str, published: Plan | None = None) -> CandidateMetrics:
    """Ranking uses only independently audited plan data."""
    errors = verify_plan(s, p)
    if errors:
        raise ValueError(f'{policy}: invalid candidate: {"; ".join(errors)}')
    unscheduled = sum(len(u.operation_ids) for u in p.unscheduled)
    late_orders = sum(o.tardiness_seconds is not None and o.tardiness_seconds > 0
                      for o in p.orders)
    tardiness = sum(o.tardiness_seconds or 0 for o in p.orders)
    active_ends = [a.end for a in p.assignments if a.segments]
    start = s.start.timestamp()
    makespan = max(0, int(max((dt.timestamp() for dt in active_ends), default=start) - start))
    moved = None
    if published is not None:
        from .simulation import compare
        before = {oid for a in published.assignments for oid in a.operation_ids}
        moved = sum(1 for c in compare(published, p)['changed_operations'] if c['operation_id'] in before)
    return CandidateMetrics(policy, unscheduled, late_orders, tardiness, makespan,
                            max((o.tardiness_seconds or 0 for o in p.orders), default=0), moved)


def optimize(s: Scenario, policies: tuple[str, ...] = DISPATCH_POLICIES, *,
             time_limit_seconds: float | None = None, clock=None,
             objectives: tuple[str, ...] = DEFAULT_OBJECTIVES, published: Plan | None = None) -> OptimizationResult:
    """Evaluate bounded dispatch alternatives, retaining the first policy on ties.

    The caller must opt in. Policies cannot modify the immutable scenario.
    The baseline policy is always considered first for a no-regression guarantee
    under the published ranking; invalid candidates are not returned as winners.
    """
    if not policies or len(policies) > len(ALL_POLICIES):
        raise ValueError(f'select between 1 and {len(ALL_POLICIES)} dispatch policies')
    if len(set(policies)) != len(policies) or any(p not in ALL_POLICIES for p in policies):
        raise ValueError('unknown or duplicate dispatch policy')
    if policies[0] != 'priority':
        raise ValueError('the baseline priority policy must be first')
    objectives = check_objectives(objectives, published)
    deadline = Deadline(time_limit_seconds, *(() if clock is None else (clock,)))
    results = []
    rejected = []
    reached = False
    for policy in policies:
        if policy != 'priority' and deadline.expired():
            reached = True
            break
        try:
            candidate = plan(s, dispatch_policy=policy)
            metrics = measure(s, candidate, policy, published)
        except (RuntimeError, ValueError) as exc:
            if policy == 'priority':
                raise
            rejected.append(f'{policy}: {exc}')
            continue
        results.append((metrics, candidate))
    winner_metrics, winner = min(results, key=lambda item: item[0].key(objectives))
    metrics = tuple(item[0] for item in results)
    return OptimizationResult(winner_metrics.policy, winner,
                              metrics, tuple(rejected), pareto_policies(metrics), reached)
