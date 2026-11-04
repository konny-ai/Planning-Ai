"""Reproducible timing of the two-stage planner on synthetic scenarios.

    python tools/stage_bench.py --phases 80 --machines 5 [--backward] [--json out.json]

Synthetic data only (seeded); prints seconds for one staged plan, a 10-evaluation
sequence search and, with --backward, the ADR 005 option A policy. Timings depend
on the machine: compare runs on the same one.
"""
import argparse
import json
import random
import sys
import time
from datetime import UTC, datetime, time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from planning_factory.domain.models import (  # noqa: E402
    Alternative, Calendar, Demand, Operation, Resource, Scenario, Shift)
from planning_factory.domain.setup_contract import load_setup_contract  # noqa: E402
from planning_factory.engine.setup_sequence_search import (  # noqa: E402
    backward_setup_before_run, default_machine_sequence, optimize_setup_sequence)
from planning_factory.engine.setup_stage_scheduler import schedule_setup_stages  # noqa: E402


def requirement(kind, count, bps, ids):
    return {'kind': kind, 'count': count, 'occupancy_bps': bps, 'candidate_ids': ids}


def build(phases, machines, seed=1):
    rng = random.Random(seed)
    names = [f'm{i}' for i in range(machines)]
    ops, rows = [], []
    due = datetime(2026, 10, 28, 17, tzinfo=UTC)
    for i in range(phases):
        m = rng.choice(names)
        ops.append(Operation(f'p{i}', f'o{i}', rng.choice([1800, 3600]), (Alternative((Demand(m, 1),)),), due=due))
        rows.append({'id': f'job:p{i}', 'machine_id': m, 'target_state': rng.choice('ABC'), 'setup_seconds': 900,
                     'setup_requirements': [requirement('machine', 1, 10000, [m]), requirement('operator', 1, 5000, ['op'])],
                     'run_requirements': [requirement('machine', 1, 10000, [m]), requirement('operator', 1, 2500, ['op'])]})
    calendar = Calendar('work', 'UTC', tuple(Shift(d, dtime(8), dtime(12)) for d in range(5)) +
                        tuple(Shift(d, dtime(13), dtime(17)) for d in range(5)))
    resources = tuple(Resource(m, 'work', kind='machine') for m in names) + (Resource('op', 'work', kind='operator'),)
    scenario = Scenario('bench', datetime(2026, 10, 19, tzinfo=UTC), datetime(2026, 12, 30, tzinfo=UTC),
                        (calendar,), resources, tuple(ops))
    contract = load_setup_contract(json.dumps({
        'schema_version': 1,
        'resources': [{'id': m, 'kind': 'machine'} for m in names] + [{'id': 'op', 'kind': 'operator'}],
        'initial_machine_states': {m: 'A' for m in names}, 'phases': rows}))
    return scenario, contract


def timed(function):
    started = time.perf_counter()
    function()
    return round(time.perf_counter() - started, 3)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--phases', type=int, default=80)
    parser.add_argument('--machines', type=int, default=5)
    parser.add_argument('--backward', action='store_true', help='also time the (slower) backward policy')
    parser.add_argument('--json')
    args = parser.parse_args(argv)
    scenario, contract = build(args.phases, args.machines)
    sequence = default_machine_sequence(scenario)
    result = {'phases': args.phases, 'machines': args.machines,
              'plan_seconds': timed(lambda: schedule_setup_stages(scenario, contract, sequence)),
              'search10_seconds': timed(lambda: optimize_setup_sequence(
                  scenario, contract, sequence, objective='setup', max_evaluations=10))}
    if args.backward:
        result['backward_seconds'] = timed(lambda: backward_setup_before_run(scenario, contract, sequence))
    print(json.dumps(result, sort_keys=True))
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
