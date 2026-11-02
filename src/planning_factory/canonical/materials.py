"""Materials domain (#85): canonical supplies and needs -> material-ready releases via the existing pegging.

``allocate_materials`` (domain/production_flow.py) does the deterministic, all-or-nothing
pegging. This module only prepares its inputs and applies its outcome tolerantly:
a task with a shortage is not dropped, it receives the horizon end as release, so the
engine reports it unscheduled instead of treating it as ready, and the report says why.
The allocation order is an explicit business choice declared in the binding.
"""
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

from planning_factory.domain.models import Scenario
from planning_factory.domain.production_flow import MaterialNeed, MaterialSupply, allocate_materials
from planning_factory.domain.validation import compile_tasks
from .model import Issue


def _instant(text: str) -> datetime:
    return datetime.fromisoformat(text.replace('Z', '+00:00')).astimezone(UTC)


def _text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def apply_materials(scenario: Scenario, records, allocation_order: str) -> tuple[Scenario, list[Issue], dict]:
    tasks, _, _ = compile_tasks(scenario)
    task_of = {op: tid for tid, task in tasks.items() for op in task.operation_ids}
    issues: list[Issue] = []
    supplies = []
    for r in records['material_supply']:
        if r.get('available_at') is None:
            issues.append(Issue('warn', 'supply_without_date', 'materials',
                                f'{r["item_id"]} x {r["quantity"]}: no availability date, not usable', entity='material_supply',
                                key=r['id']))
            continue
        supplies.append(MaterialSupply(r['id'], r['item_id'], Decimal(r['quantity']), _instant(r['available_at'])))
    needs = {}
    for r in records['material_need']:
        tid = task_of.get(r['operation_id'])
        if tid is None:
            issues.append(Issue('error', 'unknown_reference', 'materials', f'operation {r["operation_id"]!r} does not exist',
                                entity='material_need', key=r['operation_id']))
            continue
        if tasks[tid].completed_at is not None or tasks[tid].external_end is not None:
            issues.append(Issue('warn', 'need_on_closed_operation', 'materials',
                                f'{r["item_id"]}: operation already completed or external, no material reserved',
                                entity='material_need', key=r['operation_id']))
            continue
        needs[(tid, r['item_id'])] = needs.get((tid, r['item_id']), Decimal(0)) + Decimal(r['quantity'])
    ranks = {}
    for r in records['allocation_rank']:
        tid = task_of.get(r['operation_id'])
        if tid is None:
            issues.append(Issue('error', 'unknown_reference', 'materials', f'operation {r["operation_id"]!r} does not exist',
                                entity='allocation_rank', key=r['operation_id']))
        else:
            ranks[tid] = min(r['rank'], ranks.get(tid, r['rank']))
    needing = sorted({tid for tid, _ in needs})
    if allocation_order == 'rank':
        for tid in needing:
            if tid not in ranks:
                issues.append(Issue('error', 'missing_allocation_rank', 'materials',
                                    'allocation_order is "rank" but this unit needs material and has no rank',
                                    entity='allocation_rank', key=tid, units=(tid,)))
        order = sorted(tasks, key=lambda t: (t not in ranks, ranks.get(t, 0), t))
    else:
        far = datetime.max.replace(tzinfo=UTC)
        order = sorted(tasks, key=lambda t: (tasks[t].due is None, tasks[t].due or far, t))
    summary = {'allocation_order': allocation_order, 'pegs': [], 'shortages': []}
    if any(i.severity == 'error' for i in issues):
        return scenario, issues, summary
    result = allocate_materials(scenario, tuple(supplies),
                                tuple(MaterialNeed(t, i, q) for (t, i), q in sorted(needs.items())), tuple(order))
    end = scenario.end.astimezone(UTC)
    ready = dict(result.releases)
    short = {}
    for tid, item, missing in result.shortages:
        short.setdefault(tid, []).append((item, missing))
    for tid, items in sorted(short.items()):
        detail = ', '.join(f'{item} missing {format(q.normalize(), "f")}' for item, q in items)
        issues.append(Issue('block', 'material_shortage', 'materials',
                            f'{detail}: not plannable within the horizon until material is available',
                            entity='material_need', key=tid, units=(tid,)))
        ready[tid] = end
    summary['pegs'] = [{'unit': p.task_id, 'item_id': p.item_id, 'supply_id': p.supply_id,
                        'quantity': format(p.quantity.normalize(), 'f')} for p in result.pegs]
    summary['shortages'] = [{'unit': t, 'item_id': i, 'missing': format(q.normalize(), 'f')} for t, i, q in result.shortages]
    start = scenario.start.astimezone(UTC)
    operations = []
    for o in scenario.operations:
        tid = task_of[o.id]
        bound = ready.get(tid, start) if any(t == tid for t, _ in needs) else start
        if o.completed_at is not None or o.external_end is not None or bound <= start:
            operations.append(o)
            continue
        current = o.release.astimezone(UTC) if o.release else start
        operations.append(replace(o, release=max(current, bound)))
    return replace(scenario, operations=tuple(operations)), issues, summary
