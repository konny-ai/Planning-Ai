"""Read-only Factory -> ADR003 stage contract readiness audit.

NO ERP connection and NO fabricated canonical resource assignments. Factory
setup counts/percentages do not establish which exact machine, operator or
tool is occupied. A phase is never silently certified as schedulable.
"""
from collections import Counter
from decimal import Decimal, InvalidOperation
import argparse
import json
from pathlib import Path
import sys


def _decimal(value):
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            return None
        return result
    except (InvalidOperation, ValueError, TypeError):
        return None


def diagnose(source: dict, converted: dict) -> dict:
    original = source.get('operations')
    converted_ops = converted.get('operations')
    if not isinstance(original, list) or not isinstance(converted_ops, list):
        raise ValueError('missing Factory operations or converted engine operations')
    by_key = {}
    for row in original:
        try:
            key = f"{row['orderId']}/{row['lotId']}/{row['phaseId']}"
        except (KeyError, TypeError) as exc:
            raise ValueError('invalid Factory phase identity') from exc
        if key in by_key:
            raise ValueError(f'duplicate source phase: {key}')
        by_key[key] = row
    engine = {}
    for row in converted_ops:
        tid = str(row['id'])
        if tid in engine:
            raise ValueError(f'duplicate converted phase: {tid}')
        engine[tid] = row
    if not set(engine).issubset(by_key):
        raise ValueError('converted scenario differs from source; anonymized or unmatched IDs')
    resources = {r['id']: r for r in converted.get('resources', [])}
    groups = Counter()
    reasons_total = Counter()
    details = []
    for phase_id, run in sorted(engine.items()):
        row = by_key[phase_id]
        execution = str(row.get('executionCode') or 'UNKNOWN')
        groups[f'phase_execution_{execution}'] += 1
        if execution != 'I':
            continue
        setup = _decimal(row.get('setupSec'))
        if setup is None or setup < 0:
            reasons_total['invalid_setup_duration'] += 1
            details.append({'phase_id': phase_id, 'setup_seconds': None,
                            'reasons': ['invalid_setup_duration']})
            continue
        if setup == 0:
            groups['internal_without_positive_setup'] += 1
            continue
        groups['internal_positive_setup'] += 1
        missing = []
        def number(kind):
            try:
                val = row.get(kind)
                if type(val) not in (int, float, str, Decimal) or val is None:
                    return None
                d = _decimal(val)
                return int(d) if d is not None and d == int(d) and d >= 0 else None
            except (ValueError, TypeError, OverflowError):
                return None
        for kind in ('setupMachines', 'setupOperators'):
            n = number(kind)
            pct = _decimal(row.get('setupMachinePct' if kind == 'setupMachines'
                                   else 'setupOperatorPct'))
            if n is None:
                missing.append(f'{kind}_unreadable')
            elif n > 0 and (pct is None or not 0 < pct <= 100):
                missing.append(f'{kind}_percentage_unknown')
        # Source operationResources describe selectable run alternatives;
        # they are NOT approved as setup candidates.
        bundles = run.get('alternatives') or []
        machines = sorted({
            demand['resource_id']
            for alternative in bundles for demand in alternative.get('demands', [])
            if resources.get(demand.get('resource_id'), {}).get('kind') == 'machine'
        })
        if not machines:
            missing.append('no_unambiguous_run_machine_candidate')
        elif len(machines) != 1:
            missing.append('machine_selection_unfixed')
        if row.get('fixedStartFlag') or row.get('currentMachineCode'):
            missing.append('frozen_or_active_stage_requires_snapshot')
        missing.append('setup_resources_not_authoritative')
        missing.append('machine_setup_state_unverified')
        for reason in missing:
            reasons_total[reason] += 1
        details.append({
            'phase_id': phase_id, 'setup_seconds': str(setup),
            'declared_setup_machines': number('setupMachines'),
            'declared_setup_operators': number('setupOperators'),
            'run_machine_candidates': machines,
            'source_class': {k: row.get(k) for k in
                             ('setupClassId', 'setupClassGroup', 'setupClassValue')},
            'reasons': missing,
        })
    return {
        'schema_version': 1,
        'source_database': source.get('source', {}).get('database'),
        'converted_phases': len(engine),
        'counters': dict(sorted(groups.items())),
        'blocked_reasons': dict(sorted(reasons_total.items())),
        'candidate_details': details,
        'canonical_contract_generated': False,
        'explanation': ('Factory setup durations/counts may describe real stage effort, '
                        'but the mapping to machine configuration, candidate setup '
                        'resources, tools and semantics of zero percentages is not '
                        'approved. This diagnostic does not certify any phase or '
                        'generate a schedulable ADR 003 contract.'),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('factory_scenario')
    parser.add_argument('engine_scenario')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    out = Path(args.output).resolve()
    if out in {Path(args.factory_scenario).resolve(), Path(args.engine_scenario).resolve()}:
        parser.error('output cannot overwrite an input')
    try:
        source = json.loads(Path(args.factory_scenario).read_text(encoding='utf-8'))
        engine = json.loads(Path(args.engine_scenario).read_text(encoding='utf-8'))
        report = diagnose(source, engine)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, sort_keys=True,
                                  indent=2) + '\n', encoding='utf-8')
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f'Factory ADR003 readiness audit failed: {exc}', file=sys.stderr)
        return 2
    print(json.dumps({'converted_phases': report['converted_phases'],
                      'counters': report['counters'],
                      'blocked_reasons': report['blocked_reasons'],
                      'canonical_contract_generated': False},
                     ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
