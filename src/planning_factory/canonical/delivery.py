"""Delivery buffer (#78 P3) and work-in-progress bonus (#78 P12), as declared profile options.

P3: the engine plans against an *internal* due date = promised due - buffer (elapsed
seconds; per customer or order through ``set_delivery_buffer_seconds`` rules). The
promised dates are kept in the manifest so service can be measured on both.
P12: open operations of orders already started get their priority lowered by the
bonus (smaller is first), to close what is in progress. Both default to off.
"""
from dataclasses import replace
from datetime import datetime, timedelta

from planning_factory.domain.models import Plan, Scenario

PROFILE_OPTIONS = ('delivery_buffer_seconds', 'wip_priority_bonus')


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace('Z', '+00:00'))


def _text(value: datetime) -> str:
    return value.isoformat().replace('+00:00', 'Z')


def apply_delivery(operations: dict, profile_record: dict | None, buffers: dict, started_orders: set) -> dict | None:
    profile_record = profile_record or {}
    default = profile_record.get('delivery_buffer_seconds', 0)
    bonus = profile_record.get('wip_priority_bonus', 0)
    promised, boosted = {}, []
    for op_id in sorted(operations):
        op = operations[op_id]
        buffer = buffers.get(op_id, default)
        if op.get('due') is not None and buffer and 'completed_at' not in op and 'external_end' not in op:
            promised[op_id] = op['due']
            op['due'] = _text(_instant(op['due']) - timedelta(seconds=buffer))
        if bonus and op['order_id'] in started_orders and 'completed_at' not in op and 'external_end' not in op:
            op['priority'] = op.get('priority', 0) - bonus
            boosted.append(op_id)
    if not promised and not boosted and not default and not bonus:
        return None
    return {'delivery_buffer_seconds': default, 'buffer_overrides': dict(sorted(buffers.items())),
            'promised_due': promised, 'wip_priority_bonus': bonus, 'wip_boosted_operations': boosted}


def with_buffer(scenario: Scenario, delivery: dict | None, seconds: int) -> Scenario:
    """What-if: another default delivery buffer over the promised dates the source was built with.

    ``delivery`` is the manifest entry written by ``apply_delivery`` (promised dates and the per-customer or
    per-order overrides of confirmed rules, which keep their own buffer). Started or subcontracted operations
    are not re-dated, as at build time. The profile and the rules stay untouched: this only changes one plan.
    """
    delivery = delivery or {}
    promised, overrides = delivery.get('promised_due') or {}, delivery.get('buffer_overrides') or {}
    operations = []
    for op in scenario.operations:
        if op.due is None or op.completed_at is not None or op.external_end is not None:
            operations.append(op)
            continue
        base = _instant(promised[op.id]) if op.id in promised else op.due
        operations.append(replace(op, due=base - timedelta(seconds=overrides.get(op.id, seconds))))
    return replace(scenario, operations=tuple(operations))


def service_levels(plan: Plan, operations_order: dict[str, str], promised_due: dict[str, str]) -> dict:
    """On-time per order against the internal due (in the plan) and the promised due (from the manifest)."""
    by_order = {}
    for op_id, due in promised_due.items():
        order = operations_order[op_id]
        by_order[order] = min(by_order.get(order, due), due)
    rows = []
    for result in plan.orders:
        promised = by_order.get(result.order_id)
        promised_dt = _instant(promised) if promised else result.due
        rows.append({'order_id': result.order_id, 'end': _text(result.end) if result.end else None,
                     'internal_due': _text(result.due) if result.due else None,
                     'promised_due': _text(promised_dt) if promised_dt else None,
                     'on_time_internal': bool(result.end and result.due and result.end <= result.due),
                     'on_time_promised': bool(result.end and promised_dt and result.end <= promised_dt)})
    with_due = [r for r in rows if r['promised_due']]
    return {'orders': rows,
            'on_time_internal': sum(r['on_time_internal'] for r in with_due),
            'on_time_promised': sum(r['on_time_promised'] for r in with_due), 'orders_with_due': len(with_due)}
