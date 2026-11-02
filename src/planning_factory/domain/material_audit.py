"""Independent, side-effect-free audit of ERP-neutral material pegging.

Does not run the allocation algorithm or consult external ERP systems. The
caller must provide supply/needs from one coherent snapshot, and separately
validate all external reservations before calling this auditor.
"""
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal

from planning_factory.domain.production_flow import (
    MaterialAvailability, MaterialNeed, MaterialSupply,
)
from planning_factory.domain.models import Scenario
from planning_factory.domain.validation import compile_tasks


def audit_material_pegging(
    scenario: Scenario,
    supplies: tuple[MaterialSupply, ...],
    needs: tuple[MaterialNeed, ...],
    allocation: MaterialAvailability,
    *,
    committed_by_supply: Mapping[str, Decimal] | None = None,
) -> tuple[str, ...]:
    """Find overdraws, phantom pegs, incomplete tasks and invalid ready dates.

    This deliberately recomputes balances independently from allocate_materials.
    It does not infer missing stock, stock reservations, substitutions or yield.
    """
    tasks, _, _ = compile_tasks(scenario)
    errors: list[str] = []
    from planning_factory.adapters.json_io import scenario_digest
    if allocation.source_scenario_digest != scenario_digest(scenario):
        errors.append('material allocation belongs to another scenario revision')
    source = {}
    for supply in supplies:
        if supply.id in source or not supply.id:
            errors.append(f'duplicate or empty supply ID: {supply.id!r}')
            continue
        if (not isinstance(supply.quantity, Decimal) or
                not supply.quantity.is_finite() or supply.quantity <= 0):
            errors.append(f'{supply.id}: invalid supply quantity')
        if (not isinstance(supply.available_at, datetime) or
                supply.available_at.tzinfo is None or
                supply.available_at.utcoffset() is None):
            errors.append(f'{supply.id}: invalid availability timestamp')
        source[supply.id] = supply

    required = defaultdict(lambda: defaultdict(Decimal))
    for need in needs:
        if need.task_id not in tasks or not need.item_id:
            errors.append(f'{need.task_id}: unknown task or material')
            continue
        if (not isinstance(need.quantity, Decimal) or
                not need.quantity.is_finite() or need.quantity <= 0):
            errors.append(f'{need.task_id}: invalid material need')
            continue
        required[need.task_id][need.item_id] += need.quantity

    release_by_task = {}
    for task_id, at in allocation.releases:
        if task_id not in tasks or task_id in release_by_task:
            errors.append(f'{task_id}: duplicate or unknown release')
            continue
        if (not isinstance(at, datetime) or at.tzinfo is None or
                at.utcoffset() is None):
            errors.append(f'{task_id}: invalid release timestamp')
            continue
        release_by_task[task_id] = at.astimezone(UTC)

    shortage_tasks = set()
    for task_id, item_id, qty in allocation.shortages:
        if task_id not in tasks or item_id not in required[task_id]:
            errors.append(f'{task_id}/{item_id}: unknown shortage')
        if not isinstance(qty, Decimal) or not qty.is_finite() or qty <= 0:
            errors.append(f'{task_id}/{item_id}: invalid shortage amount')
        shortage_tasks.add(task_id)

    pegged_by_supply = defaultdict(Decimal)
    pegged_by_task = defaultdict(lambda: defaultdict(Decimal))
    ready_by_task = {}
    for peg in allocation.pegs:
        supply = source.get(peg.supply_id)
        if peg.task_id not in tasks or supply is None:
            errors.append(f'{peg.task_id}: peg references unknown task or supply')
            continue
        if not isinstance(peg.quantity, Decimal) or not peg.quantity.is_finite() or peg.quantity <= 0:
            errors.append(f'{peg.task_id}: invalid peg quantity')
            continue
        if peg.item_id != supply.item_id or peg.item_id not in required[peg.task_id]:
            errors.append(f'{peg.task_id}: peg references wrong material')
            continue
        pegged_by_supply[peg.supply_id] += peg.quantity
        pegged_by_task[peg.task_id][peg.item_id] += peg.quantity
        if (isinstance(supply.available_at, datetime) and
                supply.available_at.tzinfo is not None and
                supply.available_at.utcoffset() is not None):
            at = supply.available_at.astimezone(UTC)
            ready_by_task[peg.task_id] = max(ready_by_task.get(peg.task_id, at), at)

    reserved = {}
    for supply_id, qty in (committed_by_supply or {}).items():
        if supply_id not in source:
            errors.append(f'{supply_id}: commitment references unknown supply')
            continue
        if not isinstance(qty, Decimal) or not qty.is_finite() or qty < 0:
            errors.append(f'{supply_id}: invalid external commitment')
            continue
        reserved[supply_id] = qty
    for supply_id in set(pegged_by_supply) | set(reserved):
        available = source[supply_id].quantity
        if not isinstance(available, Decimal) or not available.is_finite():
            continue  # Already reported above.
        if reserved.get(supply_id, Decimal(0)) > available:
            errors.append(f'{supply_id}: external commitments exceed supply')
        if pegged_by_supply[supply_id] + reserved.get(supply_id, Decimal(0)) > available:
            errors.append(f'{supply_id}: supply overdrawn after external commitments')
    for task_id in tasks:
        completed = task_id in release_by_task
        shortage = task_id in shortage_tasks
        if completed == shortage:
            errors.append(f'{task_id}: expected exactly one of release or shortage')
        if shortage and task_id in pegged_by_task:
            errors.append(f'{task_id}: shortage task has committed pegs')
        for item_id, qty in required[task_id].items():
            pegged = pegged_by_task[task_id][item_id]
            if completed and pegged != qty:
                errors.append(f'{task_id}/{item_id}: incomplete or excess pegging')
        for item_id in pegged_by_task[task_id]:
            if item_id not in required[task_id]:
                errors.append(f'{task_id}/{item_id}: unexpected material peg')
        if completed:
            expected = max(scenario.start.astimezone(UTC),
                           ready_by_task.get(task_id, scenario.start.astimezone(UTC)))
            if release_by_task[task_id] != expected:
                errors.append(f'{task_id}: release does not match supply readiness')
    return tuple(errors)
