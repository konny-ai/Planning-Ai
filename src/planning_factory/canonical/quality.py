"""Data quality before planning (#87): cross-system identity maps and checks on the assembled scenario.

Identity maps translate the keys of one system (e.g. CAM program members) into canonical
keys (e.g. ERP operations). A key without a mapping is reported and its record dropped,
never matched by similarity. Quality checks never change data: they only report.
"""
from dataclasses import replace

from .binding import Binding, SourceBinding
from .entities import ENTITIES
from .model import Issue, MergedRecord, SourceBatch


def identity_maps(records: tuple[MergedRecord, ...]) -> dict[tuple[str, str], str]:
    return {(r.values['map'], r.values['external_id']): r.values['canonical_id'] for r in records}


def translate(batch: SourceBatch, source: SourceBinding, maps: dict[tuple[str, str], str]) -> SourceBatch:
    """Rewrite reference fields of one source through its declared maps. Unmatched keys: record dropped, 'block'."""
    if not source.identity:
        return batch
    by_kind = dict(source.identity)
    records, issues = [], list(batch.issues)
    for record in batch.records:
        spec = ENTITIES.get(record.entity)
        if spec is None or not isinstance(record.values, dict):
            records.append(record)  # left to the strict validation of the composition step
            continue
        values, unmatched = dict(record.values), []
        for f in spec.fields:
            map_name = by_kind.get(f.ref)
            if map_name is None or values.get(f.name) is None:
                continue
            external = str(values[f.name])
            canonical = maps.get((map_name, external))
            if canonical is None:
                unmatched.append(f'{f.name}={external!r} not in map {map_name!r}')
            else:
                values[f.name] = canonical
        if unmatched:
            issues.append(Issue('block', 'unmatched_identity', batch.domain, '; '.join(unmatched), batch.source,
                                record.entity, original_key=record.original_key))
        else:
            records.append(replace(record, values=values))
    return replace(batch, records=tuple(records), issues=tuple(issues))


def scenario_checks(data: dict, binding: Binding) -> list[Issue]:
    """Checks on the assembled scenario dict (canonical, sorted, UTC 'Z' strings compare chronologically)."""
    issues = []
    start, end = data['start'], data['end']
    members = {op for n in data['nestings'] for op in n['operation_ids']}
    nest_resources = set(binding.nesting_required_resources)
    known_resources = {r['id'] for r in data['resources']}
    for r in sorted(nest_resources - known_resources):
        issues.append(Issue('warn', 'unknown_quality_resource', 'resources',
                            f'quality.nesting_required_resources lists {r!r}, not a resource', entity='resource', key=r))
    for op in data['operations']:
        closed = 'completed_at' in op or 'external_end' in op
        if closed:
            continue
        key = op['id']
        if not op['alternatives']:
            issues.append(Issue('error', 'no_resource', 'routings', 'open operation without any candidate resource',
                                entity='operation', key=key))
        elif op['duration_seconds'] == 0 and op['setup_seconds'] == 0 and key not in members:
            issues.append(Issue('error', 'zero_time', 'routings',
                                'open operation with missing or zero processing and setup time', entity='operation', key=key))
        if nest_resources and key not in members and op['alternatives'] and all(
                any(d['resource_id'] in nest_resources for d in a['demands']) for a in op['alternatives']):
            issues.append(Issue('warn', 'not_nested', 'nesting',
                                'cutting operation without a nesting program: its own time is used as an estimate',
                                entity='operation', key=key))
        if op.get('due') is not None and op['due'] < start:
            issues.append(Issue('warn', 'due_in_past', 'routings', f'due {op["due"]} before horizon start {start}',
                                entity='operation', key=key))
        if op.get('release') is not None and op['release'] >= end:
            issues.append(Issue('warn', 'release_after_horizon', 'routings',
                                f'release {op["release"]} not before horizon end {end}: cannot be planned',
                                entity='operation', key=key))
        if op.get('due') is not None and op.get('release') is not None and op['due'] < op['release']:
            issues.append(Issue('warn', 'due_before_release', 'routings', f'due {op["due"]} before release {op["release"]}',
                                entity='operation', key=key))
    return issues


def with_units(issues: list[Issue], data: dict | None) -> list[Issue]:
    """Attach the plan units (engine task ids) an operation- or nest-level issue affects."""
    if not data:
        return issues
    membership = {op: 'nest:' + n['id'] for n in data['nestings'] for op in n['operation_ids']}
    operations = {op['id'] for op in data['operations']}
    nests = {n['id'] for n in data['nestings']}
    result = []
    for issue in issues:
        units = ()
        if issue.key in operations and ENTITIES.get(issue.entity) and ENTITIES[issue.entity].fields[0].ref == 'operation':
            units = (membership.get(issue.key, 'job:' + issue.key),)
        elif issue.entity in ('nest', 'nest_member') and issue.key in nests:
            units = ('nest:' + issue.key,)
        result.append(replace(issue, units=units) if units else issue)
    return result
