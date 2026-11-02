"""Canonical, ERP-independent per-phase setup requirements (ADR 003).

The v0.2 operation.setup_seconds and legacy matrix rules are intentionally
NOT changed by this model. This contract is an opt-in migration boundary;
resource capacities and the scheduling of setup work are separate increments.
"""
from dataclasses import dataclass
import json

from planning_factory.domain.validation import ValidationError


@dataclass(frozen=True)
class SetupResource:
    id: str
    kind: str  # machine, operator, tool or other


@dataclass(frozen=True)
class StageRequirement:
    kind: str
    count: int
    occupancy_bps: int  # 10000 = 100% of EACH selected resource
    candidate_ids: tuple[str, ...]


@dataclass(frozen=True)
class PhaseSetup:
    id: str
    machine_id: str
    target_state: str | None  # None => never assume the machine is already set
    setup_seconds: int
    setup_requirements: tuple[StageRequirement, ...]
    run_requirements: tuple[StageRequirement, ...]
    setup_locked: bool = False


@dataclass(frozen=True)
class SetupContract:
    resources: tuple[SetupResource, ...]
    phases: tuple[PhaseSetup, ...]
    initial_machine_states: tuple[tuple[str, str | None], ...]
    schema_version: int = 1


@dataclass(frozen=True)
class SetupDecision:
    phase_id: str
    machine_id: str
    state_before: str | None
    state_after: str | None
    requires_setup: bool
    setup_seconds: int
    setup_locked: bool


def _object(value, allowed: set[str], required: set[str], name: str):
    if not isinstance(value, dict) or (set(value) - allowed) or (required - set(value)):
        raise ValidationError([f'{name}: invalid or missing keys'])
    return value


def _label(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError([f'{name}: nonempty string required'])
    return value


def _unique_pairs(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValidationError([f'duplicate JSON key: {key}'])
        obj[key] = value
    return obj


def load_setup_contract(text: str) -> SetupContract:
    """Strict parse: no implicit percentages, resource IDs or setup states."""
    try:
        raw = json.loads(text, object_pairs_hook=_unique_pairs,
                         parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, TypeError) as exc:
        raise ValidationError([f'invalid setup contract JSON: {exc}']) from None
    _object(raw, {'schema_version', 'resources', 'phases', 'initial_machine_states'},
            {'schema_version', 'resources', 'phases', 'initial_machine_states'}, 'contract')
    if type(raw['schema_version']) is not int or raw['schema_version'] != 1:
        raise ValidationError(['setup contract schema_version must be 1'])
    if not isinstance(raw['resources'], list) or not isinstance(raw['phases'], list):
        raise ValidationError(['resources and phases must be arrays'])
    resources = {}
    for resource in raw['resources']:
        _object(resource, {'id', 'kind'}, {'id', 'kind'}, 'resource')
        rid = _label(resource['id'], 'resource id')
        kind = resource['kind']
        if kind not in ('machine', 'operator', 'tool', 'other'):
            raise ValidationError([f'{rid}: invalid resource kind'])
        if rid in resources:
            raise ValidationError([f'{rid}: duplicate resource'])
        resources[rid] = SetupResource(rid, kind)
    initial_raw = raw['initial_machine_states']
    if not isinstance(initial_raw, dict):
        raise ValidationError(['initial_machine_states must be an object'])
    initial_states = {}
    for rid, state in initial_raw.items():
        if rid not in resources or resources[rid].kind != 'machine':
            raise ValidationError([f'{rid}: initial state refers to unknown/non-machine resource'])
        if state is not None:
            _label(state, f'{rid} initial state')
        initial_states[rid] = state

    def requirements(raw_list, label):
        if not isinstance(raw_list, list):
            raise ValidationError([f'{label}: requirements must be an array'])
        result = []
        for i, raw_requirement in enumerate(raw_list):
            name = f'{label} requirement {i}'
            _object(raw_requirement, {'kind', 'count', 'occupancy_bps', 'candidate_ids'},
                    {'kind', 'count', 'occupancy_bps', 'candidate_ids'}, name)
            kind = raw_requirement['kind']
            count = raw_requirement['count']
            fraction = raw_requirement['occupancy_bps']
            ids = raw_requirement['candidate_ids']
            if kind not in ('machine', 'operator', 'tool', 'other'):
                raise ValidationError([f'{name}: invalid kind'])
            if type(count) is not int or count < 1:
                raise ValidationError([f'{name}: count must be positive integer'])
            if type(fraction) is not int or not 1 <= fraction <= 10000:
                raise ValidationError([f'{name}: occupancy_bps must be 1..10000 integer'])
            if not isinstance(ids, list) or len(ids) < count or len(set(
                    x for x in ids if isinstance(x, str))) != len(ids):
                raise ValidationError([f'{name}: missing or duplicate candidate IDs'])
            for rid in ids:
                if not isinstance(rid, str) or rid not in resources or resources[rid].kind != kind:
                    raise ValidationError([f'{name}: resource {rid!r} missing or wrong kind'])
            result.append(StageRequirement(kind, count, fraction, tuple(ids)))
        return tuple(result)

    phases = {}
    for raw_phase in raw['phases']:
        _object(raw_phase, {'id', 'machine_id', 'target_state', 'setup_seconds',
                            'setup_requirements', 'run_requirements', 'setup_locked'},
                {'id', 'machine_id', 'target_state', 'setup_seconds',
                 'setup_requirements', 'run_requirements'}, 'phase')
        pid = _label(raw_phase['id'], 'phase id')
        mid = _label(raw_phase['machine_id'], f'{pid} machine')
        if pid in phases:
            raise ValidationError([f'{pid}: duplicate phase'])
        if mid not in resources or resources[mid].kind != 'machine':
            raise ValidationError([f'{pid}: machine must exist in resource catalog'])
        state = raw_phase['target_state']
        if state is not None:
            _label(state, f'{pid} target state')
        seconds = raw_phase['setup_seconds']
        if type(seconds) is not int or seconds < 0:
            raise ValidationError([f'{pid}: setup_seconds must be nonnegative integer'])
        locked = raw_phase.get('setup_locked', False)
        if type(locked) is not bool:
            raise ValidationError([f'{pid}: setup_locked must be boolean'])
        setup = requirements(raw_phase['setup_requirements'], f'{pid} setup')
        run = requirements(raw_phase['run_requirements'], f'{pid} run')
        if seconds and not setup:
            raise ValidationError([f'{pid}: positive setup needs explicitly declared resources'])
        if not any(n.kind == 'machine' and mid in n.candidate_ids for n in run):
            raise ValidationError([f'{pid}: run demands must include the assigned machine'])
        phases[pid] = PhaseSetup(pid, mid, state, seconds, setup, run, locked)
    return SetupContract(tuple(resources.values()), tuple(phases.values()),
                         tuple(sorted(initial_states.items())))


def resolve_setup_states(contract: SetupContract, ordered_phase_ids: tuple[str, ...]
                         ) -> tuple[SetupDecision, ...]:
    """Compute phase-specific setup need from explicit machine states.

    Caller supplies ACTUAL per-machine execution order; we never infer
    sequence from ERP phase codes. Repeated IDs model resumed fragments and
    may trigger a second setup if another phase changed the machine state.
    A locked setup is reported as locked, not silently moved or removed.
    """
    phases = {p.id: p for p in contract.phases}
    state = dict(contract.initial_machine_states)
    decisions = []
    for pid in ordered_phase_ids:
        if pid not in phases:
            raise ValidationError([f'unknown phase in machine sequence: {pid}'])
        p = phases[pid]
        before = state.get(p.machine_id)
        needed = p.target_state is None or before != p.target_state
        decisions.append(SetupDecision(pid, p.machine_id, before, p.target_state,
                                       needed, p.setup_seconds if needed else 0,
                                       p.setup_locked))
        state[p.machine_id] = p.target_state
    return tuple(decisions)
