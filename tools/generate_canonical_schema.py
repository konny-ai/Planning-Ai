"""Generate contracts/canonical-v1.schema.json from the canonical entity table (ADR 004)."""
import argparse
import json
from pathlib import Path

from planning_factory.canonical.entities import json_schema

TARGET = Path(__file__).resolve().parents[1] / 'contracts' / 'canonical-v1.schema.json'


def render() -> str:
    return json.dumps(json_schema(), ensure_ascii=False, indent=2, sort_keys=True) + '\n'


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true', help='Fail if the committed schema is out of date')
    args = parser.parse_args()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding='utf-8') != render():
            print(f'{TARGET.name} is out of date: run python tools/generate_canonical_schema.py')
            return 1
        return 0
    TARGET.write_text(render(), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
