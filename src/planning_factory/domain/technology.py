"""Versioned, ERP-independent technical eligibility of finite machine alternatives.

Machine capabilities are not availability calendars. Technical checks filter
candidate bundles; the booking engine still checks when resources are free.
Units are exact identifiers: no silent unit conversions or missing defaults.
"""
from dataclasses import dataclass, replace
from decimal import Decimal

from planning_factory.domain.models import Alternative, Scenario
from planning_factory.domain.validation import ValidationError, compile_tasks


@dataclass(frozen=True)
class TechnicalNeed:
    task_id: str  # job:<id> or nest:<id>
    parameter_id: str
    value: Decimal
    unit: str


@dataclass(frozen=True)
class MachineCapability:
    machine_id: str
    parameter_id: str
    minimum: Decimal
    maximum: Decimal
    unit: str


@dataclass(frozen=True)
class TechnologyCatalog:
    revision: str
    needs: tuple[TechnicalNeed, ...]
    capabilities: tuple[MachineCapability, ...]


@dataclass(frozen=True)
class Eligibility:
    task_id: str
    accepted_indices: tuple[int, ...]
    rejected: tuple[tuple[int, tuple[str, ...]], ...]


def _decimal(value, name):
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValidationError([f'{name}: finite Decimal required'])


def assess_technology(s: Scenario, catalog: TechnologyCatalog) -> tuple[Eligibility, ...]:
    """Evaluate declared technical requirements on EVERY machine in a bundle.

    For tasks without declared technical requirements we do not pretend a
    technical certification: report them as pass-through, not 'qualified'.
    """
    tasks, _, _ = compile_tasks(s)
    resources = {r.id: r for r in s.resources}
    if not isinstance(catalog.revision, str) or not catalog.revision.strip():
        raise ValidationError(['technical catalog revision required'])
    by_task = {}
    for n in catalog.needs:
        if (n.task_id not in tasks or not n.parameter_id or not n.unit or
                (n.task_id, n.parameter_id) in by_task):
            raise ValidationError([f'unknown or duplicate technical requirement {n.task_id}/{n.parameter_id}'])
        _decimal(n.value, 'technical requirement value')
        by_task[n.task_id, n.parameter_id] = n
    capabilities = {}
    for c in catalog.capabilities:
        key = c.machine_id, c.parameter_id
        if (c.machine_id not in resources or resources[c.machine_id].kind != 'machine'
                or not c.parameter_id or not c.unit or key in capabilities):
            raise ValidationError([f'unknown or duplicate machine technical capability {key}'])
        _decimal(c.minimum, 'capability lower bound')
        _decimal(c.maximum, 'capability upper bound')
        if c.minimum > c.maximum:
            raise ValidationError([f'{key}: minimum higher than maximum'])
        capabilities[key] = c
    result = []
    for tid, task in sorted(tasks.items()):
        needs = [n for (name, _), n in sorted(by_task.items()) if name == tid]
        accepted, rejected = [], []
        for idx, alternative in enumerate(task.alternatives):
            machines = [d.resource_id for d in alternative.demands
                        if resources[d.resource_id].kind == 'machine']
            reasons = []
            if needs and not machines:
                reasons.append('technical requirements have no machine in bundle')
            for rid in machines:
                for n in needs:
                    c = capabilities.get((rid, n.parameter_id))
                    if c is None:
                        reasons.append(f'{rid}/{n.parameter_id}: unknown technical limit')
                    elif c.unit != n.unit:
                        reasons.append(f'{rid}/{n.parameter_id}: unit mismatch {n.unit}!={c.unit}')
                    elif not c.minimum <= n.value <= c.maximum:
                        reasons.append(f'{rid}/{n.parameter_id}: out of technical range')
            if reasons:
                rejected.append((idx, tuple(reasons)))
            else:
                accepted.append(idx)
        result.append(Eligibility(tid, tuple(accepted), tuple(rejected)))
    return tuple(result)


def apply_technical_limits(s: Scenario, catalog: TechnologyCatalog) -> Scenario:
    """Immutable filtering; refuse unqualified tasks and locked assignments.

    A workplan nesting remains indivisible: all original member operation
    alternatives are filtered by the SAME compiled task eligibility.
    """
    evaluated = assess_technology(s, catalog)
    tasks, _, _ = compile_tasks(s)
    eligibility = {x.task_id: x for x in evaluated}
    needed_tasks = {n.task_id for n in catalog.needs}
    for task_id in needed_tasks:
        e = eligibility[task_id]
        task = tasks[task_id]
        if not e.accepted_indices:
            raise ValidationError([f'{task_id}: no technically eligible machine bundle'])
        if task.fixed_start is not None or task.fixed_alternative is not None:
            raise ValidationError([f'{task_id}: fixed work requires manual reconciliation of technical alternatives'])
    allowed = {}
    for e in evaluated:
        if e.task_id not in needed_tasks:
            continue
        bundles = {tasks[e.task_id].alternatives[i] for i in e.accepted_indices}
        for op_id in tasks[e.task_id].operation_ids:
            allowed[op_id] = bundles
    new_ops = []
    for op in s.operations:
        if op.id not in allowed:
            new_ops.append(op)
            continue
        filtered = tuple(a for a in op.alternatives if a in allowed[op.id])
        if not filtered:
            raise ValidationError([f'{op.id}: no matching original alternative after filtering'])
        new_ops.append(replace(op, alternatives=filtered))
    updated = replace(s, operations=tuple(new_ops))
    compile_tasks(updated)
    return updated
