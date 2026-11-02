"""Read-only adapter for an EXPLICIT normalized Factory export, not live SQL.

The source view must already resolve resource compatibility and delivery dates.
Identity is always CONUM + LOCOD + IDFAS; LOT parent is LOCLP, never LOCLM.
"""
from collections import defaultdict
from dataclasses import replace
from decimal import Decimal, InvalidOperation
from urllib.parse import quote
from planning_factory.domain.durations import remaining_seconds
from planning_factory.domain.models import Alternative, Demand, Dependency, Operation
from planning_factory.domain.validation import ValidationError, compile_tasks
from .json_io import timestamp


def identity(conum, locod, idfas) -> str:
    if any(type(x) not in (int, str) or not str(x).strip() for x in (conum, locod, idfas)):
        raise ValidationError(['Factory identity requires nonempty CONUM/LOCOD/IDFAS'])
    return '/'.join(quote(str(x), safe='') for x in (conum, locod, idfas))


def decimal(value, name):
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        raise ValidationError([f'{name}: decimal required']) from None
    if not result.is_finite() or result < 0:
        raise ValidationError([f'{name}: finite nonnegative decimal required'])
    return result


def import_snapshot(template, snapshot, *, progress_policy='none', zero_phase_policy='reject'):
    """Replace template.operations from a normalized export.

    Resource calendars/bundles are explicit, avoiding guessed L_LARE RITPU logic.
    Material availability is provided as release; CMDTS is already the FINAL-lot due.
    No ERP connection, locks, SQL statements, trigger execution or production writes.
    """
    if zero_phase_policy not in ('reject', 'skip'):
        raise ValidationError(['zero_phase_policy must be reject or skip'])
    if not isinstance(snapshot, dict) or set(snapshot) - {'phases', 'lots', 'links'} or 'phases' not in snapshot:
        raise ValidationError(['Factory snapshot requires phases and optional lots/links'])
    if not all(isinstance(snapshot.get(k, []), list) for k in ('phases', 'lots', 'links')):
        raise ValidationError(['Factory snapshot collections must be arrays'])
    allowed = {'CONUM', 'LOCOD', 'IDFAS', 'FASEQ', 'FAHLV', 'FASLV', 'FAHAT', 'FASAT', 'LAQTP',
               'LOPRI', 'LAPRI', 'CustomerPriority', 'CMDTS', 'release', 'resource_bundles',
               'produced_quantity', 'recorded_work_seconds', 'recorded_setup_seconds',
               'mode', 'splittable', 'fixed_start', 'fixed_alternative', 'external_end'}
    required = {'CONUM', 'LOCOD', 'IDFAS', 'FASEQ', 'FAHLV', 'FASLV', 'FAHAT', 'FASAT', 'LAQTP', 'resource_bundles'}
    operations = {}
    all_phases = set()
    skipped = set()
    groups = defaultdict(lambda: defaultdict(list))
    for row in snapshot['phases']:
        if not isinstance(row, dict) or set(row) - allowed or required - set(row):
            raise ValidationError(['Factory phase contains missing/unknown columns'])
        oid = identity(row['CONUM'], row['LOCOD'], row['IDFAS'])
        if oid in all_phases:
            raise ValidationError([f'duplicate Factory phase {oid}'])
        all_phases.add(oid)
        if type(row['FASEQ']) is not int:
            raise ValidationError([f'{oid}: FASEQ must be integer'])
        unit = decimal(row['FAHLV'], 'FAHLV') * 3600 + decimal(row['FASLV'], 'FASLV')
        setup = decimal(row['FAHAT'], 'FAHAT') * 3600 + decimal(row['FASAT'], 'FASAT')
        work, setup = remaining_seconds(unit, row['LAQTP'], setup,
                    produced_quantity=row.get('produced_quantity', 0),
                    recorded_work_seconds=row.get('recorded_work_seconds', 0),
                    recorded_setup_seconds=row.get('recorded_setup_seconds', 0), policy=progress_policy)
        anchor = timestamp(row.get('external_end'))
        if anchor is not None:
            if row.get('resource_bundles') != [] or row.get('release') is not None or row.get('fixed_start') is not None:
                raise ValidationError([f'{oid}: external_end requires empty resource bundles and no release/fixed_start'])
            if type(row.get('LOPRI', 0)) is not int or type(row.get('LAPRI', 0)) is not int:
                raise ValidationError([f'{oid}: priority columns must be integers'])
            operations[oid] = Operation(oid, str(row['CONUM']), 0, (), due=timestamp(row.get('CMDTS')),
                                        priority=row.get('LOPRI', 0),
                                        customer_priority=row.get('CustomerPriority', 0),
                                        phase_priority=row.get('LAPRI', 0),
                                        external_end=anchor)
            groups[(str(row['CONUM']), str(row['LOCOD']))][row['FASEQ']].append(oid)
            continue
        # No historical completion is invented. Only an explicit opt-in may
        # remove zero-work placeholders from a NEW planning profile.
        if work + setup == 0:
            if zero_phase_policy != 'skip':
                raise ValidationError([f'{oid}: zero remaining work needs explicit completion normalization'])
            skipped.add(oid)
            continue
        raw_bundles = row['resource_bundles']
        if not isinstance(raw_bundles, list) or not all(isinstance(b, list) for b in raw_bundles):
            raise ValidationError([f'{oid}: resource_bundles must be arrays of resource IDs'])
        if not all(isinstance(rid, str) for b in raw_bundles for rid in b):
            raise ValidationError([f'{oid}: resource IDs must be strings'])
        alternatives = tuple(Alternative(tuple(Demand(rid) for rid in bundle)) for bundle in raw_bundles)
        priority = row.get('LOPRI', 0)
        if type(priority) is not int or type(row.get('LAPRI', 0)) is not int:
            raise ValidationError([f'{oid}: priority columns must be integers'])
        operations[oid] = Operation(oid, str(row['CONUM']), work, alternatives, setup_seconds=setup,
                                    release=timestamp(row.get('release')), due=timestamp(row.get('CMDTS')),
                                    priority=priority, customer_priority=row.get('CustomerPriority', 0), phase_priority=row.get('LAPRI', 0),
                                    mode=row.get('mode'), splittable=row.get('splittable', False),
                                    fixed_start=timestamp(row.get('fixed_start')), fixed_alternative=row.get('fixed_alternative'))
        groups[(str(row['CONUM']), str(row['LOCOD']))][row['FASEQ']].append(oid)
    deps = defaultdict(set)
    for phases in groups.values():
        sequences = sorted(phases)
        for previous, current in zip(sequences, sequences[1:]):
            for target in phases[current]:
                for source in phases[previous]:
                    deps[target].add(Dependency(source))
    lots = {}
    for lot in snapshot.get('lots', []):
        if not isinstance(lot, dict) or set(lot) != {'CONUM', 'LOCOD', 'LOCLP'}:
            raise ValidationError(['lots require exactly CONUM, LOCOD, LOCLP'])
        # Validate identity types consistently even for rows without operations.
        identity(lot['CONUM'], lot['LOCOD'], lot['LOCLP'])
        key = str(lot['CONUM']), str(lot['LOCOD'])
        if key in lots:
            raise ValidationError(['duplicate Factory lot'])
        lots[key] = str(lot['LOCLP'])
    # Validate even inactive lot nodes: parent cycles cannot hide in a sparse export.
    for key in lots:
        seen = set()
        current = key
        while current in lots:
            parent = lots[current]
            if parent in ('0', current[1]):
                break  # LOCLP == LOCOD means root in Factory, not a cycle.
            if current in seen:
                raise ValidationError(['Factory LOCLP hierarchy cycle'])
            seen.add(current)
            current = current[0], parent
            if current not in lots:
                raise ValidationError(['Factory export contains a missing parent lot'])
    # Propagate child completion to the nearest ancestor with active phases.
    for child in groups:
        if child not in lots:
            continue
        current = child
        while current in lots:
            parent = lots[current]
            if parent in ('0', current[1]):
                break
            current = current[0], parent
            if current in groups:
                for source in groups[child][max(groups[child])]:
                    for target in groups[current][min(groups[current])]:
                        deps[target].add(Dependency(source))
                break
    explicit = defaultdict(list)
    for link in snapshot.get('links', []):
        keys = {'CONUM', 'LOCOD', 'IDFAS', 'Prev_CONUM', 'Prev_LOCOD', 'Prev_IDFAS', 'LinkType'}
        if not isinstance(link, dict) or (set(link) - {'lag_seconds'}) != keys:
            raise ValidationError(['Factory link contains missing/unknown columns'])
        target = identity(link['CONUM'], link['LOCOD'], link['IDFAS'])
        source = identity(link['Prev_CONUM'], link['Prev_LOCOD'], link['Prev_IDFAS'])
        if target not in all_phases or source not in all_phases:
            raise ValidationError(['Factory explicit link references phase outside snapshot'])
        if type(link['LinkType']) is not int or link['LinkType'] not in (0, 1, 2):
            raise ValidationError(['Factory LinkType must be 0, 1 or 2'])
        lag_seconds = link.get('lag_seconds', 0)
        if type(lag_seconds) is not int:
            raise ValidationError(['Factory link lag_seconds must be integer'])
        explicit[target].append((source, 'SS' if link['LinkType'] == 1 else 'FS', lag_seconds))

    def normalized_predecessors(target, visited):
        """Cross only deliberately skipped zero-work phases; retain upstream FS/SS."""
        for source, kind, lag in explicit[target]:
            if source in visited:
                raise ValidationError(['Factory explicit link cycle through omitted phase'])
            if source in operations:
                yield Dependency(source, kind, lag)
            else:
                # Every source was validated against all_phases. The skipped
                # source has no duration, so its upstream lag must survive.
                for prior in normalized_predecessors(source, visited | {source}):
                    yield Dependency(prior.predecessor_id, prior.kind, prior.lag_seconds + lag)

    for target in operations:
        deps[target].update(normalized_predecessors(target, {target}))
    result = replace(template, operations=tuple(replace(operations[oid], predecessors=tuple(sorted(deps[oid], key=lambda d: (d.predecessor_id, d.kind))))
                                                 for oid in sorted(operations)))
    compile_tasks(result)
    return result
