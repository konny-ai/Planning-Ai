"""Reconcile exported CLIENTE programs/members against a Scenario; no database access."""
import argparse
import csv
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from planning_factory.adapters.cliente_reconciliation import reconcile_nestings
from planning_factory.adapters.json_io import loads


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('scenario', 'programs', 'members', 'out'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args(argv)
    with args.programs.open(encoding='utf-8-sig', newline='') as stream:
        programs = list(csv.DictReader(stream))
    with args.members.open(encoding='utf-8-sig', newline='') as stream:
        members = list(csv.DictReader(stream))
    report = reconcile_nestings(loads(args.scenario.read_text(encoding='utf-8')), programs, members)
    # Exclusive creation protects previous evidence and all input files.
    with args.out.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, sort_keys=True))
    return 2 if (report['review_count'] or report['orphan_scenario_nestings'] or
                 report['member_programs_without_source_program']) else 0


if __name__ == '__main__':
    raise SystemExit(main())
