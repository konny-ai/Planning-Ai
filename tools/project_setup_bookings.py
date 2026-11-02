"""Project machine-only setup reservations from an independently audited plan.

A capacity reservation report is NOT an updated schedule and is NOT an ERP
publication. The explicit choices do not establish factory-wide defaults.
"""
import argparse
import json
from pathlib import Path
import sys

from planning_factory.adapters.json_io import loads, plan_loads
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_audit import load_setup_rules
from planning_factory.engine.setup_booking import project_machine_setups


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('scenario', help='Canonical scenario JSON')
    ap.add_argument('plan', help='Audited plan JSON')
    ap.add_argument('setup_rules', help='Explicit setup family matrix JSON v1')
    ap.add_argument('--output', required=True)
    ap.add_argument('--placement', choices=('earliest', 'latest'), required=True)
    ap.add_argument('--split-policy', choices=('atomic', 'splittable'), required=True)
    args = ap.parse_args(argv)

    source = [Path(x).resolve() for x in (args.scenario, args.plan, args.setup_rules)]
    out = Path(args.output).resolve()
    if out in source:
        ap.error('output must not overwrite any input')
    try:
        s = loads(Path(args.scenario).read_text(encoding='utf-8'))
        p = plan_loads(Path(args.plan).read_text(encoding='utf-8'))
        rules = load_setup_rules(Path(args.setup_rules).read_text(encoding='utf-8'), s)
        result = project_machine_setups(s, p, rules, placement=args.placement,
                                        allow_split=args.split_policy == 'splittable')
        text = json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True, indent=2) + '\n'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding='utf-8')
    except (OSError, ValueError, ValidationError, KeyError, TypeError) as exc:
        print(f'setup projection failed: {exc}', file=sys.stderr)
        return 2
    print(json.dumps({'reservations': len(result.reservations),
                      'machine_setup_seconds': result.to_dict()['total_machine_setup_seconds'],
                      'output': str(out)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
