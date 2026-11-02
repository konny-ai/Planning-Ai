"""Standalone, explicit sequence-changeover audit. No bookings or ERP writes.

The rule durations are *additional machine working time between assignments*,
not part of an operation's existing static setup_seconds.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Any
import json

from planning_factory.domain.models import Plan, Scenario
from planning_factory.domain.validation import ValidationError, compile_tasks
from .calendars import expand
from .verification import verify_plan


@dataclass(frozen=True)
class Changeover:
    resource_id: str
    predecessor_task: str | None
    successor_task: str
    from_family: str
    to_family: str
    required_seconds: int
    available_work_seconds: int
    predecessor_end: datetime | None = None
    successor_start: datetime | None = None

    @property
    def shortfall_seconds(self) -> int:
        return max(0, self.required_seconds - self.available_work_seconds)


@dataclass(frozen=True)
class SetupAudit:
    scenario_id: str
    scenario_digest: str
    checked_machines: tuple[str, ...]
    transitions: tuple[Changeover, ...]
    partial_plan: bool

    @property
    def violations(self) -> tuple[Changeover, ...]:
        return tuple(t for t in self.transitions if t.shortfall_seconds > 0)

    @property
    def valid(self) -> bool:
        return not self.violations

    def to_dict(self) -> dict:
        def encode(t: Changeover) -> dict:
            return dict(resource_id=t.resource_id, predecessor_task=t.predecessor_task,
                        successor_task=t.successor_task, from_family=t.from_family,
                        to_family=t.to_family,
                        predecessor_end=t.predecessor_end.isoformat() if t.predecessor_end else None,
                        successor_start=t.successor_start.isoformat() if t.successor_start else None,
                        required_seconds=t.required_seconds,
                        available_work_seconds=t.available_work_seconds,
                        shortfall_seconds=t.shortfall_seconds)
        return dict(scenario_id=self.scenario_id, scenario_digest=self.scenario_digest,
                    checked_machines=list(self.checked_machines), partial_plan=self.partial_plan,
                    valid=self.valid, transitions=[encode(t) for t in self.transitions],
                    violations=[encode(t) for t in self.violations])


def _object(value: Any, keys: set[str], required: set[str], label: str) -> dict:
    if not isinstance(value, dict) or set(value) - keys or required - set(value):
        raise ValidationError([f'{label}: invalid object keys'])
    return value


def _label(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError([f'{label}: nonempty string required'])
    return value


def _rules(spec: dict, s: Scenario) -> tuple[dict[str, str], dict[str, tuple[str, dict]]]:
    spec = _object(spec, {'schema_version', 'families', 'machines'},
                   {'schema_version', 'families', 'machines'}, 'setup specification')
    if type(spec['schema_version']) is not int or spec['schema_version'] != 1:
        raise ValidationError(['setup specification schema_version must be 1'])
    if not isinstance(spec['families'], dict) or not isinstance(spec['machines'], list):
        raise ValidationError(['families must be an object and machines an array'])
    known_ops = {o.id for o in s.operations}
    families = {}
    for oid, family in spec['families'].items():
        if oid not in known_ops:
            raise ValidationError([f'unknown operation family mapping: {oid}'])
        families[oid] = _label(family, f'family for {oid}')
    if not spec['machines']:
        raise ValidationError(['at least one configured machine is required'])
    resources = {r.id: r for r in s.resources}
    machines = {}
    for raw in spec['machines']:
        m = _object(raw, {'resource_id', 'initial_family', 'transitions'},
                    {'resource_id', 'initial_family', 'transitions'}, 'machine')
        rid = _label(m['resource_id'], 'machine resource_id')
        if rid in machines:
            raise ValidationError([f'duplicate setup machine: {rid}'])
        r = resources.get(rid)
        if r is None or r.capacity != 1 or r.kind != 'machine':
            raise ValidationError([f'{rid}: setup audit requires an existing unit-capacity machine'])
        initial = _label(m['initial_family'], f'{rid} initial_family')
        if not isinstance(m['transitions'], list):
            raise ValidationError([f'{rid}: transitions must be an array'])
        transitions = {}
        for raw_transition in m['transitions']:
            tr = _object(raw_transition, {'from_family', 'to_family', 'seconds'},
                         {'from_family', 'to_family', 'seconds'}, 'transition')
            source = _label(tr['from_family'], 'from_family')
            target = _label(tr['to_family'], 'to_family')
            seconds = tr['seconds']
            if type(seconds) is not int or seconds < 0:
                raise ValidationError([f'{rid}: transition seconds must be an integer >= 0'])
            if source == target:
                raise ValidationError([f'{rid}: same-family changeovers are implicitly zero'])
            key = (source, target)
            if key in transitions:
                raise ValidationError([f'{rid}: duplicate transition {source} -> {target}'])
            transitions[key] = seconds
        machines[rid] = (initial, transitions)
    return families, machines


def load_setup_rules(text: str, scenario: Scenario) -> dict:
    """Parse strictly, validate the matrix and preserve explicit zero entries."""
    def unique_pairs(pairs):
        result = {}
        for name, value in pairs:
            if name in result:
                raise ValidationError([f'duplicate setup JSON key: {name}'])
            result[name] = value
        return result
    try:
        spec = json.loads(text, object_pairs_hook=unique_pairs,
                          parse_constant=lambda v: (_ for _ in ()).throw(ValueError(v)))
    except (ValueError, TypeError) as exc:
        raise ValidationError([f'invalid setup specification JSON: {exc}']) from None
    _rules(spec, scenario)
    return spec


def audit_setups(s: Scenario, p: Plan, spec: dict) -> SetupAudit:
    """Independently audit machine time gaps; never claim to reschedule the plan.

    The whole plan must pass its ordinary audit. Consecutive fragments of
    the same task form one run even across idle shifts. If another task
    intervenes, the returning fragment starts a new run with a new,
    explicitly declared machine-family transition. This audit does NOT
    reserve setup capacity; it checks the actual calendar-work gap.
    """
    errors = verify_plan(s, p)
    if errors:
        raise ValidationError(['invalid plan before setup audit: ' + '; '.join(errors)])
    families, machines = _rules(spec, s)
    tasks, _, _ = compile_tasks(s)
    resources = {r.id: r for r in s.resources}
    work = {rid: expand(next(c for c in s.calendars
                             if c.id == resources[rid].calendar_id), s.start, s.end)
            for rid in machines}
    rows = {rid: [] for rid in machines}
    for a in p.assignments:
        if not a.segments:
            continue
        for rid in machines:
            if not any(d.resource_id == rid for d in a.demands):
                continue
            task = tasks[a.task_id]
            task_families = {families.get(oid) for oid in task.operation_ids}
            if None in task_families or len(task_families) != 1:
                raise ValidationError([f'{rid}: missing or conflicting setup families in {a.task_id}'])
            for seg in a.segments:
                rows[rid].append((seg.start, seg.end, a.task_id, next(iter(task_families))))
    changes = []
    for rid in sorted(machines):
        source_family, matrix = machines[rid]
        previous_task = None
        previous_end = s.start
        # Coalesce only adjacent fragments of one task; A/B/A forms three
        # runs, not one A. The returning A requires its own B->A setup.
        runs = []
        for start, end, task_id, family in sorted(rows[rid]):
            if runs and task_id == runs[-1][2]:
                first, _, _, previous_family = runs[-1]
                runs[-1] = (first, end, task_id, previous_family)
            else:
                runs.append((start, end, task_id, family))
        for current_start, current_end, task_id, family in runs:
            if family == source_family:
                required = 0
            else:
                if (source_family, family) not in matrix:
                    raise ValidationError([f'{rid}: missing explicit setup transition {source_family} -> {family}'])
                required = matrix[source_family, family]
            available = sum(max(0, int((min(i.end, current_start) - max(i.start, previous_end)).total_seconds()))
                            for i in work[rid] if i.start < current_start and previous_end < i.end)
            changes.append(Changeover(rid, previous_task, task_id, source_family, family,
                                      required, available, previous_end if previous_task else None,
                                      current_start))
            previous_end = current_end
            previous_task = task_id
            source_family = family
    return SetupAudit(s.id, p.scenario_digest, tuple(sorted(machines)), tuple(changes),
                      bool(p.unscheduled))
