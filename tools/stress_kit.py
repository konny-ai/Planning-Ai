"""Repeatable synthetic performance and stability checks, strictly offline.

Example: python tools/stress_kit.py --operations 100 --machines 4 --repeat 2
"""
import argparse
from datetime import UTC, datetime, time, timedelta
import hashlib
import json
from pathlib import Path
import random
import sys
from time import perf_counter

from planning_factory.adapters.json_io import dumps, scenario_digest
from planning_factory.domain.models import (
    Alternative, Calendar, Demand, Dependency, Operation, Resource, Scenario, Shift,
)
from planning_factory.engine.local_search import local_search
from planning_factory.engine.optimization import DISPATCH_POLICIES
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan


def _whole(value, label, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f'{label} must be an integer between {lower} and {upper}')


def make_scenario(*, seed=0, operations=100, machines=4, horizon_days=40):
    """Build a seeded DAG with alternative machines, FS/SS lags and varying priorities."""
    _whole(seed, 'seed', 0, 2**31 - 1)
    _whole(operations, 'operations', 1, 1000)
    _whole(machines, 'machines', 1, 32)
    _whole(horizon_days, 'horizon_days', 1, 120)
    rng = random.Random(seed)
    start = datetime(2026, 10, 19, tzinfo=UTC)
    resources = tuple(Resource(f'm{i:02d}', 'standard') for i in range(machines))
    shifts = tuple(Shift(weekday, t0, t1) for weekday in range(5)
                   for t0, t1 in ((time(8), time(12)), (time(13), time(17))))
    ops = []
    for i in range(operations):
        dep = ()
        if i % 5:
            dep = (Dependency(f'phase-{i-1:04d}', 'SS' if i % 7 == 0 else 'FS',
                              rng.choice((0, 900, 1800))),)
        primary = rng.randrange(machines)
        choices = [primary]
        if machines > 1 and rng.random() < 0.45:
            second = rng.randrange(machines - 1)
            choices.append(second + (second >= primary))
        options = tuple(Alternative((Demand(f'm{resource:02d}'),)) for resource in choices)
        due_days = rng.randrange(1, horizon_days + 1)
        ops.append(Operation(
            f'phase-{i:04d}', f'order-{i//5:04d}',
            rng.randint(1, 8) * 900, options, predecessors=dep,
            priority=rng.randint(-2, 5), phase_priority=rng.randint(0, 3),
            due=start + timedelta(days=due_days, hours=17),
            splittable=(i % 9 == 0),
        ))
    return Scenario(f'synthetic-{seed}-{operations}', start,
                    start + timedelta(days=horizon_days),
                    (Calendar('standard', 'UTC', shifts),), resources, tuple(ops))


def run_stress(*, seed=0, operations=100, machines=4, horizon_days=40,
               repeat=2, policy='priority', budget=8):
    """Run independent complete plans and assert byte-identical output over repeats."""
    _whole(repeat, 'repeat', 1, 5)
    _whole(budget, 'budget', 1, 16)
    if policy != 'local-search' and policy not in DISPATCH_POLICIES:
        raise ValueError('unsupported policy')
    if policy == 'local-search' and operations > 80:
        raise ValueError('local-search stress runs are limited to 80 operations')
    s = make_scenario(seed=seed, operations=operations, machines=machines,
                      horizon_days=horizon_days)
    plan_hashes = []
    wall_seconds = []
    assigned = unscheduled = late = 0
    for _ in range(repeat):
        start = perf_counter()
        result = (local_search(s, budget=budget).plan if policy == 'local-search'
                  else plan(s, dispatch_policy=policy))
        wall_seconds.append(round(perf_counter() - start, 6))
        errors = verify_plan(s, result)
        if errors:
            raise RuntimeError('independent verification failed: ' + '; '.join(errors))
        plan_hashes.append(hashlib.sha256(dumps(result).encode('utf-8')).hexdigest())
        assigned, unscheduled = len(result.assignments), len(result.unscheduled)
        late = sum(bool(o.tardiness_seconds) for o in result.orders)
    if len(set(plan_hashes)) != 1:
        raise RuntimeError('non-deterministic plan output over repeated identical inputs')
    return {
        'type': 'offline_synthetic_stress',
        'seed': seed, 'operations': operations, 'machines': machines,
        'horizon_days': horizon_days, 'repeat': repeat,
        'policy': policy, 'budget': budget if policy == 'local-search' else None,
        'scenario_digest': scenario_digest(s), 'plan_sha256': plan_hashes[0],
        'identical_repeats': True,
        'assigned_tasks': assigned, 'unscheduled_tasks': unscheduled,
        'late_orders': late, 'seconds_per_run': wall_seconds,
        'python_version': sys.version.split()[0],
        'disclaimer': 'Synthetic offline machine timings only; no Factory ERP or SLA claim',
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=int, default=0)
    parser.add_argument('--operations', type=int, default=100)
    parser.add_argument('--machines', type=int, default=4)
    parser.add_argument('--horizon-days', type=int, default=40)
    parser.add_argument('--repeat', type=int, default=2)
    parser.add_argument('--policy', choices=(*DISPATCH_POLICIES, 'local-search'),
                        default='priority')
    parser.add_argument('--budget', type=int, default=8)
    parser.add_argument('--json', help='Optionally write the metrics to a file')
    args = parser.parse_args(argv)
    try:
        result = run_stress(seed=args.seed, operations=args.operations,
                            machines=args.machines, horizon_days=args.horizon_days,
                            repeat=args.repeat, policy=args.policy, budget=args.budget)
        output = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + '\n'
        if args.json:
            Path(args.json).write_text(output, encoding='utf-8')
        print(output, end='')
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
