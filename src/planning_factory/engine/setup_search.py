"""Bounded deterministic search over explicitly declared resource alternatives for setup plans."""
from dataclasses import dataclass, replace
from itertools import combinations, product

from planning_factory.domain.models import Scenario, Plan
from planning_factory.domain.validation import ValidationError
from .optimization import measure
from .setup_planner import plan_with_setups
from .time_budget import Deadline
from .verification import verify_plan


@dataclass(frozen=True)
class SetupSearchResult:
    scenario: Scenario
    plan: Plan
    setup_audit: object
    candidates_evaluated: int
    budget: int
    winner_order: tuple[tuple[str, tuple[str, ...]], ...]
    rejected: tuple[str, ...]
    time_limit_reached: bool = False


def _rank(scenario, plan):
    return measure(scenario, plan, 'setup-search').ranking


def _variants(flexible):
    """Cover individual machine switches before compound combinations.

    Earlier Cartesian-only enumeration forced *all* flexible operations at
    once. A small search budget could therefore spend its only extra attempt
    on the original first alternatives and never test a viable single-machine
    change. Keep generation lazy and deterministic, even for large factories.
    """
    yield ()  # Original scenario is always evaluated first.
    # Fair round-robin coverage: each operation gets a first alternative switch
    # before anyone gets its second or third switch.
    depth = max((len(alts) for _, alts in flexible), default=1)
    for idx in range(1, depth):
        for oid, alts in flexible:
            if idx < len(alts):
                yield ((oid, (alts[idx],)),)
    # Compound non-default switches can repair setup conflicts that no single
    # switch resolves. A two-operation/two-choice case fits in budget=4.
    for size in range(2, len(flexible) + 1):
        for group in combinations(flexible, size):
            for selected in product(*(alts[1:] for _, alts in group)):
                yield tuple((group[i][0], (alt,)) for i, alt in enumerate(selected))
    # Explicitly forcing the first alternative is also meaningful: the default
    # scheduler is free to choose another one. Include these after switches.
    for size in range(1, len(flexible) + 1):
        for group in combinations(flexible, size):
            for selected in product(*(alts for _, alts in group)):
                yield tuple((group[i][0], (alt,)) for i, alt in enumerate(selected))


def search_setup_alternatives(scenario: Scenario, rules: dict, *, budget: int = 8,
                              repair_budget: int = 12, time_limit_seconds: float | None = None,
                              clock=None) -> SetupSearchResult:
    """Try up to budget deterministic alternative selections, audited independently.

    Each candidate starts from the original immutable scenario and runs its own
    setup repair. The baseline is considered first. This is a bounded heuristic,
    not an optimality guarantee or a legacy dispatch emulation.
    """
    if type(budget) is not int or not 1 <= budget <= 32:
        raise ValueError('alternative search budget must be an integer between 1 and 32')
    # Pinned operations retain their indexed bundle; candidates never mutate it.
    flexible = sorted(((op.id, op.alternatives) for op in scenario.operations
                       if len(op.alternatives) > 1 and op.fixed_alternative is None),
                      key=lambda item: item[0])
    deadline = Deadline(time_limit_seconds, *(() if clock is None else (clock,)))
    candidates = []
    rejected = []
    seen = set()
    reached = False
    for variant in _variants(flexible):
        changes = dict(variant)
        key = tuple((oid, tuple(tuple((d.resource_id, d.units) for d in a.demands)
                                  for a in alternatives))
                    for oid, alternatives in variant)
        if key in seen:
            continue
        if len(seen) >= budget:
            break
        if seen and deadline.expired():
            reached = True
            break
        seen.add(key)
        operations = tuple(replace(op, alternatives=changes[op.id]) if op.id in changes else op
                           for op in scenario.operations)
        candidate_scenario = replace(scenario, operations=operations)
        try:
            result = plan_with_setups(candidate_scenario, rules, budget=repair_budget)
            errors = verify_plan(result.scenario, result.plan)
            if errors or not result.setup_audit.valid:
                raise ValidationError(['candidate failed independent plan/setup audit'] + list(errors))
            score = _rank(result.scenario, result.plan)
            candidates.append((score, result, key))
        except (ValidationError, ValueError, RuntimeError) as exc:
            rejected.append(f'{key or "baseline"}: {exc}')
    if not candidates:
        raise ValidationError([f'no setup-feasible alternative plan among {len(seen)} candidates', *rejected])
    score, winner, winner_key = min(candidates, key=lambda item: (item[0], repr(item[2])))
    winner_order = tuple((oid, tuple(rid for alternative in alternatives for rid, _units in alternative))
                         for oid, alternatives in winner_key)
    return SetupSearchResult(winner.scenario, winner.plan, winner.setup_audit,
                             len(seen), budget, winner_order, tuple(rejected), reached)
