"""Scenario builder (ADR 004): canonical domains -> the existing v1 ``Scenario``.

Deterministic: every list is ordered by canonical key, never by source order, so the
same data read through different bindings yields the same scenario digest. The engine
already breaks ties by id, therefore ordering changes the digest but never the plan.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime

from planning_factory.adapters.json_io import scenario_digest, scenario_from_dict
from planning_factory.domain.models import Scenario
from planning_factory.domain.validation import ValidationError
from .binding import Binding
from .compose import compose_domain, domain_digest, freshness
from .corrections import apply_corrections, digest as corrections_digest
from .entities import ENTITIES, SCHEMA_VERSION, domain_entities
from .materials import apply_materials
from .model import Issue, ReadRequest, SourceBatch
from .progress import apply_progress
from .delivery import PROFILE_OPTIONS, apply_delivery
from .perimeter import apply_perimeter
from .rules import apply_rules
from .quality import identity_maps, scenario_checks, translate, with_units

_RANK = {'error': 0, 'block': 1, 'warn': 2}
_INHERITED = ('due', 'release', 'priority', 'customer_priority', 'mode')
_NEUTRAL = {'priority': 0, 'customer_priority': 0}


@dataclass(frozen=True)
class BuildResult:
    scenario: Scenario | None
    manifest: dict
    issues: tuple[Issue, ...]
    # Merged canonical records per entity (after corrections) and those excluded by a correction:
    # what the data screens show. Empty unless the caller asked for them.
    records: dict | None = None
    excluded: dict | None = None
    # The same Scenario before the material releases (None without a materials domain): what the plan
    # starts from when a confirmed company-memory switch turns the material constraint off.
    material_free: Scenario | None = None

    @property
    def publishable(self) -> bool:
        return self.manifest['publishable']

    def report(self) -> dict:
        counts = {s: sum(1 for i in self.issues if i.severity == s) for s in _RANK}
        return {'schema_version': SCHEMA_VERSION, 'scenario_id': self.manifest['scenario_id'],
                'buildable': self.scenario is not None, 'publishable': self.publishable,
                'counts': counts, 'issues': [i.to_dict() for i in self.issues]}


def _utc_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def read_sources(binding: Binding, create_connector, now: datetime) -> dict[str, list[SourceBatch]]:
    """Run every bound connector once per domain. A failing source becomes an error issue, never a silent gap."""
    connectors, batches = {}, {}
    for d in binding.domains:
        request = ReadRequest(d.domain, domain_entities(d.domain), binding.horizon_start, binding.horizon_end, now)
        for name in d.sources:
            source = binding.source(name)
            try:
                if name not in connectors:
                    connectors[name] = create_connector(source, binding.base_dir)
                batch = connectors[name].read(request)
            except (OSError, ValidationError, ValueError, KeyError) as exc:
                batch = SourceBatch(name, source.connector, d.domain, now,
                                    issues=(Issue('error', 'source_unavailable', d.domain, str(exc), name),))
            batches.setdefault(d.domain, []).append(batch)
    return batches


def build_scenario(binding: Binding, batches: dict[str, list[SourceBatch]], now: datetime,
                   accept_stale: bool = False, corrections=(), keep_records: bool = False) -> BuildResult:
    if now.tzinfo is None:
        raise ValueError('now must be timezone-aware')
    issues: list[Issue] = []
    records = defaultdict(list)
    domains = {}
    maps = {}
    kept, removed = defaultdict(list), defaultdict(list)
    bound = {d.domain for d in binding.domains}
    for c in corrections:
        if ENTITIES[c.entity].domain not in bound:
            issues.append(Issue('warn', 'correction_unbound', ENTITIES[c.entity].domain,
                                f'correction #{c.id}: domain not bound in this installation, ignored',
                                'correzioni', c.entity))
    ordered = sorted(binding.domains, key=lambda d: d.domain != 'identity')  # maps first, then the domains using them
    for d in ordered:
        domain_batches = batches.get(d.domain, [])
        if d.domain != 'identity':
            domain_batches = [translate(b, binding.source(b.source), maps) for b in domain_batches]
        merged, found = compose_domain(d, domain_batches)
        if d.domain == 'identity':
            maps = identity_maps(merged)
        issues += found
        merged, found, excluded = apply_corrections(d.domain, merged, corrections)
        issues += found
        sources, stale = freshness(d, domain_batches, now, accept_stale)
        issues += stale
        for r in merged:
            records[r.entity].append(r.values)
            if keep_records:
                kept[r.entity].append(r)
        for r in excluded:
            removed[r.entity].append(r)
        domains[d.domain] = {'digest': domain_digest(merged), 'records': len(merged),
                             'max_age_minutes': d.max_age_minutes, 'sources': sources}
    scenario, profile, data, rules = None, None, None, None
    found, perimeter = apply_perimeter(records, binding.perimeter)
    issues += found
    if not any(i.severity == 'error' for i in issues):
        data, profile, found, rules = _assemble(binding, records, _utc_text(now))
        found += scenario_checks(data, binding)
        issues += found
        if not any(i.severity == 'error' for i in found):
            try:
                scenario = scenario_from_dict(data)
            except ValidationError as exc:
                issues += [Issue('error', 'scenario_invalid', 'routings', e) for e in exc.errors]
    materials, material_free = None, None
    materials_binding = binding.domain('materials')
    if scenario is not None and materials_binding is not None:
        material_free = scenario
        scenario, found, materials = apply_materials(scenario, records, materials_binding.allocation_order)
        issues += found
        if any(i.severity == 'error' for i in found):
            scenario = material_free = None
    issues = with_units(issues, data)
    issues.sort(key=lambda i: (_RANK[i.severity], i.domain, i.code, i.entity, i.key, i.source, i.detail, i.original_key))
    scope_order_ids = {o.order_id for o in scenario.operations} if scenario else set()
    scope_operation_ids = {o.id for o in scenario.operations} if scenario else set()
    manifest = {
        'schema_version': SCHEMA_VERSION, 'installation': binding.installation, 'binding_digest': binding.digest,
        'built_at': _utc_text(now), 'scenario_id': binding.scenario_id,
        'scenario_digest': scenario_digest(scenario) if scenario else None,
        'horizon': {'start': _utc_text(binding.horizon_start), 'end': _utc_text(binding.horizon_end)},
        'profile': profile, 'buildable': scenario is not None,
        'publishable': scenario is not None and not any(i.severity in ('error', 'block') for i in issues),
        'issues': {s: sum(1 for i in issues if i.severity == s) for s in _RANK},
        'domains': domains,
        # Retain selected canonical attributes that the v1 engine Scenario does
        # not carry. Local confirmed rules must never guess them from labels.
        **({'rule_scope': {
            'source_scenario_digest': scenario_digest(scenario),
            'orders': [{k: r[k] for k in ('id', 'customer_id') if r.get(k) is not None}
                       for r in records['order'] if r['id'] in scope_order_ids],
            'operations': [{k: r[k] for k in ('id', 'material', 'thickness_mm') if r.get(k) is not None}
                           for r in records['operation'] if r['id'] in scope_operation_ids],
        }} if scenario is not None else {}),
        **({'materials': materials} if materials is not None else {}),
        **({'rules': {k: v for k, v in rules.items() if k != 'delivery'}}
           if rules and (rules['applied'] or rules['skipped']) else {}),
        **({'delivery': rules['delivery']} if rules and rules.get('delivery') else {}),
        **({'perimeter': perimeter} if perimeter is not None else {}),
        **({'corrections': {'active': len(corrections), 'digest': corrections_digest(corrections),
                            'excluded': sum(len(v) for v in removed.values())}} if corrections else {}),
    }
    return BuildResult(scenario, manifest, tuple(issues), dict(kept) if keep_records else None,
                       dict(removed) if keep_records else None, material_free)


def _assemble(binding: Binding, records, now: str) -> tuple[dict, str | None, list[Issue], dict]:
    issues = []

    def missing(domain, entity, key, what, ref):
        issues.append(Issue('error', 'unknown_reference', domain, f'{what} {ref!r} does not exist', entity=entity, key=key))

    calendars = {r['id']: {'id': r['id'], 'timezone': r['timezone'], 'shifts': [], 'holidays': [], 'exceptions': [],
                           'closures': []} for r in records['calendar']}
    exceptions = {}
    for r in records['calendar_shift']:
        if r['calendar_id'] not in calendars:
            missing('calendars', 'calendar_shift', r['calendar_id'], 'calendar', r['calendar_id'])
        else:
            calendars[r['calendar_id']]['shifts'].append({'weekday': r['weekday'], 'start': r['start'], 'end': r['end']})
    for r in records['calendar_holiday']:
        if r['calendar_id'] not in calendars:
            missing('calendars', 'calendar_holiday', r['calendar_id'], 'calendar', r['calendar_id'])
        else:
            calendars[r['calendar_id']]['holidays'].append(r['day'])
    for r in records['calendar_exception']:
        if r['calendar_id'] not in calendars:
            missing('calendars', 'calendar_exception', r['calendar_id'], 'calendar', r['calendar_id'])
        else:
            exceptions[(r['calendar_id'], r['day'])] = {'day': r['day'], 'shifts': []}
            calendars[r['calendar_id']]['exceptions'].append(exceptions[(r['calendar_id'], r['day'])])
    for r in records['calendar_exception_shift']:
        target = exceptions.get((r['calendar_id'], r['day']))
        if target is None:
            missing('calendars', 'calendar_exception_shift', f"{r['calendar_id']}/{r['day']}", 'calendar_exception',
                    f"{r['calendar_id']}/{r['day']}")
        else:
            target['shifts'].append([r['start'], r['end']])
    for r in records['calendar_closure']:
        if r['calendar_id'] not in calendars:
            missing('calendars', 'calendar_closure', r['calendar_id'], 'calendar', r['calendar_id'])
        else:
            calendars[r['calendar_id']]['closures'].append({'start': r['start'], 'end': r['end']})
    resources = []
    for r in records['resource']:
        if r['calendar_id'] not in calendars:
            missing('resources', 'resource', r['id'], 'calendar', r['calendar_id'])
        resources.append({'id': r['id'], 'calendar_id': r['calendar_id'], 'capacity': r['capacity'],
                          'kind': r.get('kind', ENTITIES['resource'].field('kind').default),
                          **({'efficiency_bp': r['efficiency_bp']} if r.get('efficiency_bp') is not None else {})})
    resource_ids = {r['id'] for r in resources}
    orders = {r['id']: r for r in records['order']}
    operations = {}
    for r in records['operation']:
        order = orders.get(r['order_id'])
        if order is None:
            missing('routings', 'operation', r['id'], 'order', r['order_id'])
            order = {}
        op = {'id': r['id'], 'order_id': r['order_id'], 'duration_seconds': r['duration_seconds'],
              'setup_seconds': r['setup_seconds'], 'alternatives': [], 'predecessors': [],
              'phase_priority': r.get('phase_priority', 0), 'splittable': r.get('splittable', False),
              **({'preferred_alternative': r['preferred_alternative']} if r.get('preferred_alternative') is not None else {})}
        for field in _INHERITED:
            value = r.get(field)
            if value is None:
                value = order.get(field)
            if value is None:
                value = _NEUTRAL.get(field)
            if value is not None:
                op[field] = value
        operations[r['id']] = op
    alternatives = defaultdict(lambda: defaultdict(list))
    for r in records['operation_demand']:
        if r['operation_id'] not in operations:
            missing('routings', 'operation_demand', r['operation_id'], 'operation', r['operation_id'])
        elif r['resource_id'] not in resource_ids:
            missing('routings', 'operation_demand', r['operation_id'], 'resource', r['resource_id'])
        else:
            alternatives[r['operation_id']][r['alternative']].append(
                {'resource_id': r['resource_id'], 'units': r.get('units', 1)})
    for op_id, by_index in alternatives.items():
        if sorted(by_index) != list(range(len(by_index))):
            issues.append(Issue('error', 'alternative_gap', 'routings',
                                f'alternative indexes must be 0..n-1, found {sorted(by_index)}', entity='operation_demand',
                                key=op_id))
            continue
        operations[op_id]['alternatives'] = [
            {'demands': sorted(by_index[i], key=lambda d: d['resource_id'])} for i in range(len(by_index))]
    for r in records['operation_dependency']:
        for ref in (r['operation_id'], r['predecessor_id']):
            if ref not in operations:
                missing('routings', 'operation_dependency', f"{r['operation_id']}/{r['predecessor_id']}", 'operation', ref)
        if r['operation_id'] in operations and r['predecessor_id'] in operations:
            operations[r['operation_id']]['predecessors'].append(
                {'predecessor_id': r['predecessor_id'], 'kind': r.get('kind', 'FS'), 'lag_seconds': r.get('lag_seconds', 0),
                 **({'max_lag_seconds': r['max_lag_seconds']} if r.get('max_lag_seconds') is not None else {})})
    for r in records['operation_external_end']:
        if r['operation_id'] not in operations:
            missing('subcontract', 'operation_external_end', r['operation_id'], 'operation', r['operation_id'])
        else:
            operations[r['operation_id']]['external_end'] = r['external_end']
    for r in records['operation_state']:
        if r['operation_id'] not in operations:
            missing('progress', 'operation_state', r['operation_id'], 'operation', r['operation_id'])
            continue
        for field in ('completed_at', 'fixed_start', 'fixed_alternative'):
            if r.get(field) is not None:
                operations[r['operation_id']][field] = r[field]
    progress = binding.domain('progress')
    issues += apply_progress(operations, records, progress.residual_policy if progress else None,
                             _utc_text(binding.horizon_start))
    nesting = binding.domain('nesting')
    statuses = nesting.include_statuses if nesting else None
    excluded = {}
    nests, programs = {}, {}
    for r in records['nest']:
        if statuses is not None and r.get('status') not in statuses:
            excluded[r['id']] = r.get('status')
            issues.append(Issue('warn', 'nest_excluded', 'nesting',
                                f'status {r.get("status") or "not declared"} not in {list(statuses)}: members planned one by one',
                                entity='nest', key=r['id']))
            continue
        nests[r['id']] = {'id': r['id'], 'duration_seconds': r['duration_seconds'], 'setup_seconds': r['setup_seconds'],
                          'operation_ids': []}
        programs[r['id']] = r
    for r in records['nest_member']:
        if r['nest_id'] in excluded:
            continue
        if r['nest_id'] not in nests:
            missing('nesting', 'nest_member', r['nest_id'], 'nest', r['nest_id'])
        elif r['operation_id'] not in operations:
            missing('nesting', 'nest_member', r['nest_id'], 'operation', r['operation_id'])
        else:
            nests[r['nest_id']]['operation_ids'].append(r['operation_id'])
    for nest in nests.values():
        if not nest['operation_ids']:
            issues.append(Issue('error', 'empty_nest', 'nesting', 'nest without members', entity='nest', key=nest['id']))
        machine = programs[nest['id']].get('resource_id')
        if machine is None:
            continue
        if machine not in resource_ids:
            missing('nesting', 'nest', nest['id'], 'resource', machine)
            continue
        # The CAM assigned the program to one machine: members keep only the alternatives using it.
        for op_id in nest['operation_ids']:
            op = operations[op_id]
            uses = [any(d['resource_id'] == machine for d in a['demands']) for a in op['alternatives']]
            fixed = op.get('fixed_alternative')
            if fixed is not None:
                if fixed >= len(uses) or not uses[fixed]:
                    issues.append(Issue('error', 'nest_resource_conflict', 'nesting',
                                        f'{op_id} is fixed on an alternative without {machine}', entity='nest', key=nest['id']))
            elif not any(uses):
                issues.append(Issue('error', 'nest_resource_conflict', 'nesting',
                                    f'{op_id} has no alternative using {machine}', entity='nest', key=nest['id']))
            else:
                op['alternatives'] = [a for a, use in zip(op['alternatives'], uses) if use]
                op.pop('preferred_alternative', None)  # the CAM already chose the machine
    settings, profile = {}, None
    profiles = {r['id']: r for r in records['profile']}
    if binding.domain('rules') is None:
        issues.append(Issue('warn', 'default_profile', 'rules', 'rules domain not bound: engine default settings used'))
    elif binding.profile is not None:
        if binding.profile not in profiles:
            missing('rules', 'profile', binding.profile, 'profile', binding.profile)
        else:
            profile = binding.profile
    elif len(profiles) == 1:
        profile = next(iter(profiles))
    else:
        issues.append(Issue('error', 'profile_selection', 'rules',
                            f'binding.profile must select one of {sorted(profiles)}' if profiles else 'no profile available'))
    if profile is not None:
        settings = {k: v for k, v in profiles[profile].items() if k != 'id' and k not in PROFILE_OPTIONS}
    progressed = {r['operation_id'] for r in records['operation_progress']} | \
        {r['operation_id'] for r in records['operation_state']}
    members = {op for n in nests.values() for op in n['operation_ids']}
    buffers = {}
    found, rules = apply_rules(operations, records, profile, now, members, progressed, buffers)
    issues += found
    started_orders = {operations[o]['order_id'] for o in progressed if o in operations}
    rules = dict(rules, delivery=apply_delivery(operations, profiles.get(profile), buffers, started_orders))
    for c in calendars.values():
        c['shifts'].sort(key=lambda s: (s['weekday'], s['start'], s['end']))
        c['holidays'].sort()
        c['exceptions'].sort(key=lambda e: e['day'])
        for e in c['exceptions']:
            e['shifts'].sort()
        c['closures'].sort(key=lambda x: (x['start'], x['end']))
    for op in operations.values():
        op['predecessors'].sort(key=lambda p: p['predecessor_id'])
    data = {'schema_version': 1, 'id': binding.scenario_id, 'start': _utc_text(binding.horizon_start),
            'end': _utc_text(binding.horizon_end), 'calendars': [calendars[k] for k in sorted(calendars)],
            'resources': sorted(resources, key=lambda r: r['id']),
            'operations': [operations[k] for k in sorted(operations)],
            'nestings': [dict(n, operation_ids=sorted(n['operation_ids'])) for _, n in sorted(nests.items())],
            'settings': settings}
    return data, profile, issues, rules
