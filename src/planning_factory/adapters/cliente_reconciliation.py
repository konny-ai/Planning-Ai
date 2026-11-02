"""Read-only CLIENTE source coverage: scheduling success never hides excluded workplans."""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from planning_factory.adapters.json_io import scenario_digest
from planning_factory.domain.validation import ValidationError


def reconcile_nestings(scenario, programs, members):
    """Compare exported A_NES/L_ODLA rows to the resulting immutable Scenario.

    Reasons explain technical exclusions; they do not approve business filters.
    All source IDs remain in the report, including members outside the Scenario.
    """
    operations = {op.id: op for op in scenario.operations}
    nests = {n.id: n for n in scenario.nestings}
    # The engine admits an operation in one nesting only; CLIENTE may list the same
    # phase in several programs (pieces of one lot cut on different sheets).
    owner = {oid: n.id for n in scenario.nestings for oid in n.operation_ids}
    grouped = defaultdict(set)
    for row in members:
        try:
            wid = str(row['workplanId'])
            oid = '/'.join(str(row[k]) for k in ('orderId', 'lotId', 'phaseId'))
        except KeyError as exc:
            raise ValidationError([f'CLIENTE member: missing column {exc}']) from None
        grouped[wid].add(oid)
    seen, rows = set(), []
    for program in programs:
        try:
            wid = str(program['workplanId'])
            cut = Decimal(str(program['cutSec'] or '0'))
        except (KeyError, InvalidOperation):
            raise ValidationError(['CLIENTE programs require workplanId and exact cutSec']) from None
        if not wid or wid in seen or not cut.is_finite() or cut < 0:
            raise ValidationError([f'CLIENTE program {wid!r}: duplicate ID or invalid cutSec'])
        seen.add(wid)
        expected = grouped[wid]
        present = expected & operations.keys()
        missing = expected - operations.keys()
        included = nests.get('NEST-' + wid)
        actual = set(included.operation_ids) if included else set()
        mismatch = bool(included and (actual != present or cut <= 0 or not present))
        elsewhere = {oid: owner[oid] for oid in sorted(present) if owner.get(oid, 'NEST-' + wid) != 'NEST-' + wid}
        free = sorted(present - elsewhere.keys())
        bundles = {oid: {tuple(sorted((d.resource_id, d.units) for d in a.demands))
                         for a in operations[oid].alternatives} for oid in present}
        # The builder is greedy in program order, so check both the whole program
        # and the members still free: either lacking a common bundle explains it.
        common = all(not group or set.intersection(*(bundles[o] for o in group)) for group in (sorted(present), free))
        if included:
            reason = 'membership_mismatch' if mismatch else 'included'
        elif cut == 0:
            reason = 'non_cut_zero_duration'
        elif not present:
            reason = 'no_members_in_scenario'
        elif not free:
            reason = 'members_in_other_nesting'
        elif not common:
            reason = 'no_common_resource_bundle'
        else:
            reason = 'unexplained_exclusion'
        rows.append({'workplan_id': wid, 'reason': reason,
                     'source_members': sorted(expected), 'scenario_members': sorted(present),
                     'members_outside_scenario': sorted(missing), 'included_members': sorted(actual),
                     'members_in_other_nestings': elsewhere,
                     'requires_review': bool(missing or reason in (
                         'membership_mismatch', 'members_in_other_nesting', 'no_common_resource_bundle',
                         'unexplained_exclusion'))})
    orphans = sorted(n.id for n in scenario.nestings if n.id not in {'NEST-' + w for w in seen})
    unknown_member_programs = sorted(set(grouped) - seen)
    return {'schema_version': 1, 'scenario_digest': scenario_digest(scenario),
            'source_programs': len(rows), 'scenario_nestings': len(nests),
            'counts': dict(sorted(Counter(r['reason'] for r in rows).items())),
            'review_count': sum(r['requires_review'] for r in rows),
            'orphan_scenario_nestings': orphans,
            'member_programs_without_source_program': unknown_member_programs,
            'rows': rows, 'business_acceptance_required': True,
            'production_approved': False}
