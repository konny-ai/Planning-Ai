"""Opt-in bounded scheduler that reserves declared sequence setup as working-time gaps."""
from dataclasses import dataclass, replace
from datetime import timedelta

from planning_factory.domain.models import Scenario, Plan
from planning_factory.domain.validation import ValidationError, compile_tasks, utc
from .calendars import expand
from .scheduler import plan
from .setup_audit import _rules, audit_setups


@dataclass(frozen=True)
class SetupPlanningResult:
    scenario: Scenario
    plan: Plan
    iterations: int
    budget: int
    setup_audit: object


def _add_work(intervals, anchor, seconds):
    """Return earliest instant at/after anchor with the requested machine-work seconds."""
    cursor = utc(anchor)
    remaining = seconds
    for interval in intervals:
        if interval.end <= cursor:
            continue
        begin = max(cursor, interval.start)
        if remaining == 0:
            return begin
        available = int((interval.end - begin).total_seconds())
        if available >= remaining:
            return begin + timedelta(seconds=remaining)
        remaining -= available
        cursor = interval.end
    raise ValidationError(['setup duration cannot fit in the machine calendar horizon'])


def plan_with_setups(scenario: Scenario, rules: dict, *, budget: int = 12) -> SetupPlanningResult:
    """Iteratively delay successor releases, replan and audit; default plan() is unchanged."""
    if type(budget) is not int or not 1 <= budget <= 32:
        raise ValueError('setup planning budget must be an integer between 1 and 32')
    families, machines = _rules(rules, scenario)
    resources = {r.id: r for r in scenario.resources}
    calendars = {c.id: c for c in scenario.calendars}
    work = {rid: expand(calendars[resources[rid].calendar_id], scenario.start, scenario.end)
            for rid in machines}
    current = scenario
    seen = set()
    for iteration in range(budget + 1):
        result = plan(current)
        audit = audit_setups(current, result, rules)
        if audit.valid:
            return SetupPlanningResult(current, result, iteration, budget, audit)
        if iteration == budget:
            break
        tasks, _, _ = compile_tasks(current)
        ops = {o.id: o for o in current.operations}
        new_releases = {}
        for violation in audit.violations:
            machine = violation.resource_id
            # A preempted predecessor may resume after the successor has
            # started. Its full assignment.end is then in the future and
            # cannot anchor the transition. Use the actual preceding run.
            base = violation.predecessor_end or current.start
            target = _add_work(work[machine], base, violation.required_seconds)
            task = tasks[violation.successor_task]
            for oid in task.operation_ids:
                old = ops[oid].release or current.start
                if utc(old) < utc(target):
                    previous_target = new_releases.get(oid, old)
                    new_releases[oid] = max(previous_target, target, key=utc)
        if not new_releases:
            raise ValidationError(['setup constraints are infeasible or cannot make monotonic progress'])
        key = tuple(sorted((oid, utc(value).isoformat()) for oid, value in new_releases.items()))
        if key in seen:
            raise ValidationError(['setup planning detected a repeated constraint state'])
        seen.add(key)
        updated = tuple(replace(o, release=max(o.release or current.start, new_releases[o.id], key=utc))
                        if o.id in new_releases else o for o in current.operations)
        current = replace(current, operations=updated)
    raise ValidationError([f'no setup-feasible plan found within {budget} repair iterations'])
