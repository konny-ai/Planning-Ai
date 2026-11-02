"""Reproducible synthetic benchmark; not a claim about a customer's ERP database."""
from datetime import UTC, datetime, time, timedelta
import json
from time import perf_counter
from planning_factory.domain.models import Alternative, Calendar, Demand, Dependency, Operation, Resource, Scenario, Shift
from planning_factory.engine.scheduler import plan


def run(count=1000):
    start = datetime(2026, 10, 19, tzinfo=UTC)
    resources = tuple(Resource(f'm{i}', 'c') for i in range(8))
    operations = []
    for i in range(count):
        deps = (Dependency(str(i - 1)),) if i % 5 else ()
        operations.append(Operation(str(i), f'order-{i // 5}', 1800,
                                    tuple(Alternative((Demand(r.id),)) for r in resources), predecessors=deps))
    s = Scenario('benchmark', start, start + timedelta(days=100),
                 (Calendar('c', 'UTC', tuple(Shift(d, time(8), time(16)) for d in range(5))),), resources, tuple(operations))
    begin = perf_counter()
    result = plan(s)
    return {'operations': count, 'resources': len(resources), 'horizon_days': 100,
            'elapsed_seconds': round(perf_counter() - begin, 3),
            'assigned': len(result.assignments), 'unscheduled': len(result.unscheduled)}


if __name__ == '__main__':
    print(json.dumps(run(), indent=2))
