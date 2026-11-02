"""ERP-neutral production structure and deterministic material allocation (v0.3).

This module is deliberately separate from the v0.2 scenario wire contract.
It adds traceable, validated BOM expansion and material availability without
guessing purchasing, reservations, substitutions, or stock already committed.
"""
from collections import defaultdict
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from datetime import UTC, datetime

from planning_factory.domain.models import Scenario
from planning_factory.domain.validation import ValidationError, compile_tasks, instant


@dataclass(frozen=True)
class Item:
    id: str
    unit: str
    supply_mode: str  # make | buy


@dataclass(frozen=True)
class BomLine:
    parent_id: str
    component_id: str
    quantity_per_parent: Decimal


@dataclass(frozen=True)
class CustomerLine:
    id: str
    item_id: str
    quantity: Decimal
    due: datetime


@dataclass(frozen=True)
class ProductionLot:
    id: str
    item_id: str
    quantity: Decimal
    customer_line_ids: tuple[str, ...]
    operation_ids: tuple[str, ...]


@dataclass(frozen=True)
class MaterialSupply:
    id: str
    item_id: str
    quantity: Decimal
    available_at: datetime


@dataclass(frozen=True)
class MaterialCommitment:
    """Quantity already reserved by an external system against a known supply.

    Commitments are inputs from a trusted snapshot, not reservations created
    by this planner. They reduce its allocatable balance for the entire run.
    """
    supply_id: str
    quantity: Decimal


@dataclass(frozen=True)
class MaterialNeed:
    task_id: str  # compiled job:id / nest:id, never individual nesting members
    item_id: str
    quantity: Decimal


@dataclass(frozen=True)
class MaterialPeg:
    task_id: str
    item_id: str
    supply_id: str
    quantity: Decimal


@dataclass(frozen=True)
class MaterialAvailability:
    releases: tuple[tuple[str, datetime], ...]
    pegs: tuple[MaterialPeg, ...]
    shortages: tuple[tuple[str, str, Decimal], ...]
    source_scenario_digest: str


def _positive(value, label):
    if not isinstance(value, Decimal) or not value.is_finite() or value <= 0:
        raise ValidationError([f'{label}: finite positive Decimal required'])


def validate_production(items: tuple[Item, ...], bom: tuple[BomLine, ...],
                        customer_lines: tuple[CustomerLine, ...] = (),
                        lots: tuple[ProductionLot, ...] = ()) -> None:
    ids = {}
    for item in items:
        if (not item.id or not item.id.strip() or
                not item.unit or not item.unit.strip() or
                item.supply_mode not in ('make', 'buy') or item.id in ids):
            raise ValidationError([f'unknown, malformed or duplicate item {item.id!r}'])
        ids[item.id] = item
    tree = defaultdict(list)
    pairs = set()
    for edge in bom:
        if (edge.parent_id not in ids or edge.component_id not in ids or
                edge.parent_id == edge.component_id):
            raise ValidationError(['BOM references unknown item or itself'])
        _positive(edge.quantity_per_parent, 'BOM quantity')
        pair = edge.parent_id, edge.component_id
        if pair in pairs:
            raise ValidationError([f'duplicate BOM parent/component {pair}'])
        pairs.add(pair)
        tree[edge.parent_id].append(edge.component_id)
    visiting, done = set(), set()
    def visit(node):
        if node in visiting:
            raise ValidationError([f'cyclic BOM at {node}'])
        if node in done:
            return
        visiting.add(node)
        for child in tree[node]:
            visit(child)
        visiting.remove(node)
        done.add(node)
    for item_id in ids:
        visit(item_id)
    line_ids = set()
    line_by_id = {}
    for line in customer_lines:
        if not line.id or line.id in line_ids or line.item_id not in ids:
            raise ValidationError([f'invalid customer line {line.id!r}'])
        line_ids.add(line.id)
        line_by_id[line.id] = line
        _positive(line.quantity, 'customer line quantity')
        instant(line.due, f'customer line {line.id} due')
    lot_ids = set()
    for lot in lots:
        if (not lot.id or lot.id in lot_ids or lot.item_id not in ids or
                ids[lot.item_id].supply_mode != 'make' or
                len(lot.customer_line_ids) != len(set(lot.customer_line_ids)) or
                len(lot.operation_ids) != len(set(lot.operation_ids)) or
                any(cid not in line_ids for cid in lot.customer_line_ids) or
                any(line_by_id[cid].item_id != lot.item_id
                    for cid in lot.customer_line_ids if cid in line_by_id) or
                not lot.operation_ids):
            raise ValidationError([f'invalid production lot {lot.id!r}'])
        lot_ids.add(lot.id)
        _positive(lot.quantity, 'production lot quantity')


def explode_bom(root_id: str, quantity: Decimal, items: tuple[Item, ...],
                bom: tuple[BomLine, ...]) -> tuple[tuple[str, Decimal], ...]:
    """Gross component needs, including intermediate make components.

    Assumes no stock or yield corrections and does NOT auto-create work orders.
    Callers explicitly decide which intermediate components to manufacture.
    """
    validate_production(items, bom)
    _positive(quantity, 'explosion quantity')
    if root_id not in {i.id for i in items}:
        raise ValidationError([f'unknown BOM root {root_id}'])
    children = defaultdict(list)
    for edge in bom:
        children[edge.parent_id].append(edge)
    totals = defaultdict(Decimal)
    def walk(item_id, qty):
        for edge in children[item_id]:
            needed = qty * edge.quantity_per_parent
            totals[edge.component_id] += needed
            walk(edge.component_id, needed)
    walk(root_id, quantity)
    return tuple(sorted(totals.items()))


def allocate_materials(s: Scenario, supplies: tuple[MaterialSupply, ...],
                       needs: tuple[MaterialNeed, ...],
                       task_priority: tuple[str, ...], *,
                       commitments: tuple[MaterialCommitment, ...] = ()) -> MaterialAvailability:
    """Deterministic finite shared-stock pegging with all-or-nothing per task.

    `task_priority` is EXPLICIT business input, not an implicit due-date
    heuristic. Every task is enumerated once, even if it needs no material.
    Failed allocations are rolled back before evaluating the next task.
    External commitments are deducted from each named supply before pegging.
    Their quantities MUST be from a consistent ERP snapshot; this API does not
    infer pre-existing reservations or modify an external inventory.
    """
    tasks, _, _ = compile_tasks(s)
    if len(task_priority) != len(tasks) or set(task_priority) != set(tasks):
        raise ValidationError(['task_priority must cover each compiled task exactly once'])
    remaining, source = {}, {}
    for lot in supplies:
        if not lot.id or lot.id in source or not lot.item_id:
            raise ValidationError([f'invalid or duplicate material supply {lot.id}'])
        _positive(lot.quantity, f'supply {lot.id}')
        instant(lot.available_at, f'supply {lot.id}')
        source[lot.id] = lot
        remaining[lot.id] = lot.quantity
    committed = defaultdict(Decimal)
    for entry in commitments:
        if entry.supply_id not in source:
            raise ValidationError([f'unknown committed supply {entry.supply_id!r}'])
        _positive(entry.quantity, f'commitment for supply {entry.supply_id}')
        committed[entry.supply_id] += entry.quantity
    for supply_id, quantity in committed.items():
        if quantity > remaining[supply_id]:
            raise ValidationError([f'commitments exceed quantity for supply {supply_id!r}'])
        remaining[supply_id] -= quantity
    requirements = defaultdict(lambda: defaultdict(Decimal))
    for need in needs:
        if need.task_id not in tasks or not need.item_id:
            raise ValidationError([f'unknown material need task/item {need.task_id}'])
        if tasks[need.task_id].completed_at is not None or tasks[need.task_id].external_end is not None:
            raise ValidationError([f'{need.task_id}: cannot reserve new material for completed/external milestone'])
        _positive(need.quantity, 'material need quantity')
        requirements[need.task_id][need.item_id] += need.quantity
    index = defaultdict(list)
    for supply in supplies:
        index[supply.item_id].append(supply)
    for item_id in index:
        index[item_id].sort(key=lambda a: (a.available_at.astimezone(UTC), a.id))
    releases, pegs, shortages = [], [], []
    for tid in task_priority:
        requested = requirements[tid]
        proposed = {}
        staged = []
        missing = []
        readiness = s.start.astimezone(UTC)
        for item_id, needed in sorted(requested.items()):
            outstanding = needed
            for lot in index[item_id]:
                take = min(outstanding, proposed.get(lot.id, remaining[lot.id]))
                if take > 0:
                    proposed[lot.id] = proposed.get(lot.id, remaining[lot.id]) - take
                    staged.append(MaterialPeg(tid, item_id, lot.id, take))
                    outstanding -= take
                    readiness = max(readiness, lot.available_at.astimezone(UTC))
                if not outstanding:
                    break
            if outstanding > 0:
                missing.append((tid, item_id, outstanding))
        if missing:
            # Leave stock untouched to avoid phantom partial consumption.
            shortages.extend(missing)
            continue
        remaining.update(proposed)
        pegs.extend(staged)
        releases.append((tid, readiness))
    from planning_factory.adapters.json_io import scenario_digest
    return MaterialAvailability(tuple(releases), tuple(pegs), tuple(shortages),
                                scenario_digest(s))


def apply_material_releases(s: Scenario, result: MaterialAvailability) -> Scenario:
    """Immutable scenario adapter: material-ready dates only, never earlier.

    A missing task due to shortage is a hard failure; the scheduler must not
    treat it as ready. A nesting uses the shared compiled task release and all
    original member operations receive the SAME lower bound.
    """
    tasks, _, _ = compile_tasks(s)
    from planning_factory.adapters.json_io import scenario_digest
    if result.source_scenario_digest != scenario_digest(s):
        raise ValidationError(['material allocations were computed for another scenario revision'])
    releases = dict(result.releases)
    if result.shortages or len(releases) != len(tasks) or set(releases) != set(tasks):
        raise ValidationError(['material shortages or incomplete release coverage'])
    date_by_operation = {}
    for task_id, task in tasks.items():
        for op_id in task.operation_ids:
            date_by_operation[op_id] = releases[task_id]
    return replace(s, operations=tuple(
        o if (o.completed_at is not None or o.external_end is not None) else
        replace(o, release=max(o.release.astimezone(UTC) if o.release else s.start.astimezone(UTC),
                               date_by_operation[o.id]))
        for o in s.operations))
