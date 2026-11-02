"""Source-grounded setup coverage diagnostic for Factory JSON extraction.

Separate *static* FAHAT/FASAT from directional changeover rules. Never infer a
setup matrix or infer that resource-demand columns have been approved.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import sys


def analyze(source: dict, converted: dict) -> dict:
    operations = source.get('operations')
    engine_ops = converted.get('operations')
    if not isinstance(operations, list) or not isinstance(engine_ops, list):
        raise ValueError('missing Factory or engine operations')
    engine_ids = {str(x['id']) for x in engine_ops}
    active = [o for o in operations
              if f"{o['orderId']}/{o['lotId']}/{o['phaseId']}" in engine_ids]
    if engine_ops and not active:
        raise ValueError('cannot match engine operations to source: identifiers may be anonymized')
    matched = {f"{o['orderId']}/{o['lotId']}/{o['phaseId']}" for o in active}
    if matched != engine_ids:
        raise ValueError('incomplete Factory operation identity mapping')
    positive = [o for o in active if float(o.get('setupSec') or 0) > 0]
    times = sorted(float(o['setupSec']) for o in positive)
    def percentile(q):
        if not times:
            return None
        return round(times[max(0, math.ceil(q * len(times)) - 1)], 3)
    setup_without_demands = sum(
        int(o.get('setupMachines') or 0) == 0 and int(o.get('setupOperators') or 0) == 0
        for o in positive)
    setup_demand_differs = sum(
        int(o.get('setupMachines') or 0) != int(o.get('runMachines') or 0)
        or int(o.get('setupOperators') or 0) != int(o.get('runOperators') or 0)
        for o in positive)
    declared_setup = [o for o in active
                      if int(o.get('setupMachines') or 0) > 0
                      or int(o.get('setupOperators') or 0) > 0]
    # External subcontracting time can be represented in source setup fields,
    # but must not be interpreted as a machine changeover in our workshop.
    # Keep internal/external populations disjoint and explicit.
    by_execution = {}
    for code in sorted({str(o.get('executionCode') or 'UNKNOWN') for o in active}):
        subset = [o for o in active if str(o.get('executionCode') or 'UNKNOWN') == code]
        positive_subset = [o for o in subset if float(o.get('setupSec') or 0) > 0]
        by_execution[code] = {
            'operations': len(subset),
            'positive_static_setup_operations': len(positive_subset),
            'raw_static_setup_seconds': round(
                sum(float(o.get('setupSec') or 0) for o in positive_subset), 3),
            'positive_without_declared_setup_resources': sum(
                int(o.get('setupMachines') or 0) == 0 and
                int(o.get('setupOperators') or 0) == 0 for o in positive_subset),
            'declared_setup_machine_operations': sum(
                int(o.get('setupMachines') or 0) > 0 for o in subset),
            'declared_setup_operator_operations': sum(
                int(o.get('setupOperators') or 0) > 0 for o in subset),
        }
    return {
        'schema_version': 1,
        'source_database': source.get('source', {}).get('database'),
        'total_source_operations': len(operations),
        'matched_schedulable_operations': len(active),
        'positive_static_setup_operations': len(positive),
        'by_execution_code': by_execution,
        'declared_setup_machine_operations': sum(
            int(o.get('setupMachines') or 0) > 0 for o in active),
        'declared_setup_operator_operations': sum(
            int(o.get('setupOperators') or 0) > 0 for o in active),
        'positive_static_setup_with_no_declared_setup_resources': setup_without_demands,
        'positive_static_setup_with_different_setup_vs_run_resource_counts': setup_demand_differs,
        'declared_setup_resources_with_zero_static_duration': sum(
            float(o.get('setupSec') or 0) == 0 for o in declared_setup),
        'raw_nominal_static_setup_seconds': round(sum(times), 3),
        'positive_setup_seconds_p50': percentile(0.5),
        'positive_setup_seconds_p90': percentile(0.9),
        'positive_setup_seconds_max': percentile(1),
        'directional_changeover_matrix_present': False,
        'interpretation': ('Only static source setupSec (FAHAT*3600+FASAT) '
                           'and FANMA/FANPA resource counts were measured. '
                           'A directional machine-family setup matrix was NOT inferred. '
                           'Engine setup_seconds can be reduced by progress policy.'),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('source_scenario')
    ap.add_argument('engine_scenario')
    ap.add_argument('--output', required=True)
    args = ap.parse_args(argv)
    target = Path(args.output).resolve()
    if target in {Path(args.source_scenario).resolve(),
                  Path(args.engine_scenario).resolve()}:
        ap.error('output must not overwrite an input scenario')
    try:
        source = json.loads(Path(args.source_scenario).read_text(encoding='utf-8'))
        engine = json.loads(Path(args.engine_scenario).read_text(encoding='utf-8'))
        result = analyze(source, engine)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2,
                                     sort_keys=True) + '\n', encoding='utf-8')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'Factory setup diagnostic failed: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
