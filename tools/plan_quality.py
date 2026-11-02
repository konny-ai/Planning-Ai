"""Produce audited Planning Factory KPIs, with optional deterministic rerun.

Examples:
  python tools/plan_quality.py scenario.json plan.json --out quality.json
  python tools/plan_quality.py scenario.json plan.json --check-reproduction

--check-reproduction is only suitable for the standard 'schedule' plan.
It is not a comparison against any legacy program.
"""
import argparse
from hashlib import sha256
import json
from pathlib import Path
import sys

from planning_factory.adapters.json_io import dumps, loads, plan_loads
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.quality import assess_quality
from planning_factory.engine.scheduler import plan


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("scenario")
    ap.add_argument("plan")
    ap.add_argument("--out", help="Write JSON KPIs to this path")
    ap.add_argument("--check-reproduction", action="store_true",
                    help="Rerun the standard schedule policy and require byte-identical plan JSON")
    args = ap.parse_args(argv)
    if args.out and Path(args.out).resolve() in {Path(args.scenario).resolve(), Path(args.plan).resolve()}:
        ap.error("the report may not overwrite its inputs")
    try:
        s = loads(Path(args.scenario).read_text(encoding="utf-8"))
        p = plan_loads(Path(args.plan).read_text(encoding="utf-8"))
        report = assess_quality(s, p)
        if args.check_reproduction:
            repeated = sha256(dumps(plan(s)).encode("utf-8")).hexdigest()
            report["baseline_reproduction"] = {
                "identical": repeated == report["plan_sha256"],
                "recomputed_sha256": repeated,
            }
    except (ValueError, ValidationError, KeyError, OSError) as exc:
        print(f"invalid plan or scenario: {exc}", file=sys.stderr)
        return 2
    output = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if args.out:
        target = Path(args.out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(output, encoding="utf-8")
    print(output, end="")
    if args.check_reproduction and not report["baseline_reproduction"]["identical"]:
        print("the rerun is not byte-identical to the supplied baseline plan", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
