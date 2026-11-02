"""Audited, algorithm-independent quality indicators for production plans.

There is intentionally no acceptance threshold or legacy-Gantt comparator here.
Product priorities and trade-offs belong to explicit business decisions.
"""
from collections import Counter, defaultdict
from datetime import UTC
from hashlib import sha256

from planning_factory.adapters.json_io import dumps
from planning_factory.domain.models import Plan, Scenario
from planning_factory.engine.optimization import measure


def assess_quality(scenario: Scenario, result: Plan) -> dict:
    """Return deterministic KPIs after an independent audit of the whole plan.

    Incomplete orders must never be counted as on time merely because they
    have no tardiness timestamp. Infinite-capacity or zero-availability
    resources are excluded from weighted utilization denominators.
    """
    metrics = measure(scenario, result, "quality")
    complete = [o for o in result.orders if o.complete]
    committed = [o for o in complete if o.due is not None]
    on_time = sum((o.tardiness_seconds or 0) == 0 for o in committed)
    # A customer promise already past when planning begins cannot be made
    # punctual by any future rescheduling. Keep these orders visible but
    # distinguish them from due dates the planner can still influence.
    overdue_at_start = [o for o in committed if o.due < scenario.start]
    due_during_horizon = [o for o in committed if scenario.start <= o.due < scenario.end]
    due_after_horizon = [o for o in committed if o.due >= scenario.end]
    due_from_start = due_during_horizon + due_after_horizon
    punctual_from_start = sum((o.tardiness_seconds or 0) == 0 for o in due_from_start)
    unscheduled = Counter()
    for task in result.unscheduled:
        unscheduled[task.code] += len(task.operation_ids)

    by_id = {r.id: r for r in scenario.resources}
    aggregates = defaultdict(lambda: {"count": 0, "finite_resources": 0,
                                      "available_unit_seconds": 0, "booked_unit_seconds": 0})
    finite_loads = []
    for row in result.resources:
        kind = by_id[row.resource_id].kind
        group = aggregates[kind]
        group["count"] += 1
        group["booked_unit_seconds"] += row.booked_unit_seconds
        if row.available_unit_seconds is not None:
            group["finite_resources"] += 1
            group["available_unit_seconds"] += row.available_unit_seconds
            if row.available_unit_seconds > 0:
                finite_loads.append((row.booked_unit_seconds / row.available_unit_seconds,
                                     row.resource_id, row.booked_unit_seconds,
                                     row.available_unit_seconds))

    # The existing v0.2 operation/nesting setup_seconds is *static* duration
    # included in the assigned task. A nesting replaces all its member
    # durations, so member setups must NOT be summed a second time.
    processed_ops = {oid for a in result.assignments if a.segments
                     for oid in a.operation_ids}
    nested_ops = {oid for n in scenario.nestings for oid in n.operation_ids}
    positive_static = [
        o.setup_seconds for o in scenario.operations
        if o.id in processed_ops and o.id not in nested_ops and o.setup_seconds > 0]
    positive_nesting = [
        n.setup_seconds for n in scenario.nestings
        if n.setup_seconds > 0 and all(oid in processed_ops for oid in n.operation_ids)]
    static_setup = {
        "booked_tasks_with_static_setup": len(positive_static) + len(positive_nesting),
        "booked_nestings_with_static_setup": len(positive_nesting),
        "nominal_static_setup_seconds": sum(positive_static) + sum(positive_nesting),
        "sequence_dependent_setup_assessed": False,
    }

    kinds = {}
    for kind, values in sorted(aggregates.items()):
        denom = values["available_unit_seconds"]
        kinds[kind] = {**values,
                       "weighted_utilization_percent": round(100 * values["booked_unit_seconds"] / denom, 2)
                       if denom and values["finite_resources"] == values["count"] else None}
    # If a kind includes infinite resources, its booked total includes those
    # resources and must NOT be presented as finite weighted utilization.
    bottlenecks = [
        {"resource_id": rid, "booked_unit_seconds": booked,
         "available_unit_seconds": available, "utilization_percent": round(100 * ratio, 2)}
        for ratio, rid, booked, available in sorted(finite_loads, key=lambda x: (-x[0], x[1]))[:5]
    ]
    return {
        "schema_version": 1,
        "scenario_id": scenario.id,
        "plan_sha256": sha256(dumps(result).encode("utf-8")).hexdigest(),
        "scheduled_tasks": len(result.assignments),
        "scheduled_operations": sum(len(a.operation_ids) for a in result.assignments),
        "unscheduled_tasks": len(result.unscheduled),
        "unscheduled_operations": metrics.unscheduled_operations,
        "unscheduled_by_code": dict(sorted(unscheduled.items())),
        "orders": {
            "total": len(result.orders), "complete": len(complete),
            "incomplete": len(result.orders) - len(complete),
            "complete_with_due": len(committed),
            "complete_on_time": on_time,
            "complete_late": len(committed) - on_time,
            "service_level_percent": round(100 * on_time / len(committed), 2) if committed else None,
            "complete_due_before_horizon": len(overdue_at_start),
            "complete_due_during_horizon": len(due_during_horizon),
            "complete_due_after_horizon": len(due_after_horizon),
            "complete_due_from_start": len(due_from_start),
            "complete_on_time_due_from_start": punctual_from_start,
            "service_level_due_from_start_percent": (
                round(100 * punctual_from_start / len(due_from_start), 2)
                if due_from_start else None),
            "total_tardiness_seconds": metrics.total_tardiness_seconds,
        },
        "makespan_seconds": metrics.makespan_seconds,
        "static_setup_coverage": static_setup,
        "resource_load": {"by_kind": kinds, "highest_utilization_finite": bottlenecks},
    }
