"""Read-only Factory WIP evidence inventory; never manufactures MES confirmations.

Factory time counters and fixed-start flags can identify candidate work
requiring investigation, but do not prove current machine occupancy, elapsed
runtime, remaining work, released tools, or machine setup state at snapshot.
"""
import argparse
from collections import Counter
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

ACTUAL_TIME_FIELDS = (
    'actualSetupMachineSec', 'actualSetupOperatorSec',
    'actualWorkMachineSec', 'actualWorkOperatorSec',
)


def _nonnegative_number(value):
    if value is None:
        return Decimal(0)
    if type(value) not in (str, int, float, Decimal):
        return None
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return n if n.is_finite() and n >= 0 else None


def diagnose(source: dict, converted: dict) -> dict:
    original = source.get('operations')
    converted_ops = converted.get('operations')
    if not isinstance(original, list) or not isinstance(converted_ops, list):
        raise ValueError('missing operations in source or converted scenario')
    by_key = {}
    for item in original:
        if not isinstance(item, dict):
            raise ValueError('invalid Factory operation')
        try:
            key = f"{item['orderId']}/{item['lotId']}/{item['phaseId']}"
        except KeyError as exc:
            raise ValueError('missing Factory phase identity') from exc
        if key in by_key:
            raise ValueError(f'duplicate Factory operation {key}')
        by_key[key] = item
    converted_ids = []
    for item in converted_ops:
        if not isinstance(item, dict) or not isinstance(item.get('id'), str):
            raise ValueError('invalid converted operation identity')
        converted_ids.append(item['id'])
    if len(converted_ids) != len(set(converted_ids)):
        raise ValueError('duplicate converted operation identities')
    if not set(converted_ids).issubset(by_key):
        raise ValueError('source and converted operation IDs do not match')

    counters = Counter()
    details = []
    for task_id in sorted(converted_ids):
        item = by_key[task_id]
        counters['converted_operations'] += 1
        if str(item.get('executionCode') or '') == 'I':
            counters['internal_operations'] += 1
        if item.get('fixedStartFlag'):
            counters['fixed_start_hints'] += 1
        if item.get('currentMachineCode'):
            counters['current_machine_code_hints'] += 1
        observed = {}
        invalid = []
        for field in ACTUAL_TIME_FIELDS:
            number = _nonnegative_number(item.get(field))
            if number is None:
                invalid.append(field)
            else:
                observed[field] = number
        if invalid:
            counters['invalid_actual_time_fields'] += 1
        has_actual = any(n > 0 for n in observed.values())
        if has_actual:
            counters['operations_with_positive_actual_time'] += 1
        if not (has_actual or invalid or item.get('fixedStartFlag') or
                item.get('currentMachineCode')):
            continue
        reasons = []
        if invalid:
            reasons.append('invalid_actual_time_value')
        # Factory totals do not by themselves prove exact run residual seconds.
        if has_actual:
            reasons.extend((
                'remaining_run_seconds_not_independently_confirmed',
                'residual_material_needs_not_confirmed',
            ))
        if item.get('fixedStartFlag'):
            reasons.append('fixed_booking_segments_and_resources_unverified')
        if item.get('currentMachineCode'):
            reasons.append('machine_assignment_is_not_as_of_state_observation')
        reasons.extend((
            'no_as_of_machine_idle_observation',
            'no_authoritative_post_run_setup_state',
        ))
        details.append({'phase_id': task_id,
                        'actual_time_fields_present': sorted(
                            k for k, v in observed.items() if v > 0),
                        'reasons': sorted(set(reasons))})
    return {
        'schema_version': 1,
        'source_database': source.get('source', {}).get('database'),
        'scenario_start': source.get('planning', {}).get('start'),
        'counters': dict(sorted(counters.items())),
        'candidate_details': details,
        'machine_observations_attested': False,
        'paused_progress_attested': False,
        'active_holds_attested': False,
        'residual_materials_attested': False,
        'wip_contract_generated': False,
        'explanation': (
            'Legacy progress counters and flags are diagnostic hints only. '
            'They do not prove machine status at the planning horizon, exact '
            'remaining run/setup time, external resource holds, or residual '
            'material quantities. Never construct MachineObservation, '
            'PausedRunProgress or ActiveResourceHold automatically here.'),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('factory_scenario')
    parser.add_argument('engine_scenario')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    if output in {Path(args.factory_scenario).resolve(),
                  Path(args.engine_scenario).resolve()}:
        parser.error('output must not overwrite an input scenario')
    try:
        source = json.loads(Path(args.factory_scenario).read_text(encoding='utf-8'))
        converted = json.loads(Path(args.engine_scenario).read_text(encoding='utf-8'))
        report = diagnose(source, converted)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, ensure_ascii=False,
                                     sort_keys=True) + '\n', encoding='utf-8')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'Factory WIP readiness inventory failed: {exc}', file=sys.stderr)
        return 2
    print(json.dumps({key: report[key] for key in (
        'scenario_start', 'counters', 'machine_observations_attested',
        'wip_contract_generated')}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
