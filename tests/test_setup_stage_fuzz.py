"""Seeded fuzz: whatever the two-stage planner returns must pass the auditor.

Either a ValidationError (fail closed) or a plan with zero audit violations and
zero verifier problems; never a silently invalid plan.
"""
import json
import random
import unittest

from planning_factory.domain.models import Dependency, Operation, Resource
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_capacity import audit_stage_capacity
from planning_factory.engine.setup_sequence_search import (
    default_machine_sequence, optimize_setup_sequence,
)
from planning_factory.engine.setup_stage_scheduler import schedule_setup_stages
from planning_factory.engine.verification import verify_plan
from .helpers import alt, dt, scenario


def req(kind, count, bps, ids):
    return {'kind': kind, 'count': count, 'occupancy_bps': bps, 'candidate_ids': ids}


def build(rng):
    machines = ['m1', 'm2'][:rng.randint(1, 2)]
    n = rng.randint(2, 6)
    ops, phases = [], []
    names = [f'p{i}' for i in range(n)]
    for i, name in enumerate(names):
        machine = rng.choice(machines)
        kind = rng.choices(['active', 'done', 'supplier'], [8, 1, 1])[0]
        kw = {}
        if kind == 'supplier':
            ops.append(Operation(name, f'o{i % 3}', 0, (), external_end=dt(day=20, hour=rng.randint(8, 16))))
            continue
        if kind == 'done':
            ops.append(Operation(name, f'o{i % 3}', 0, (alt(machine),), completed_at=dt(hour=0)))
        else:
            preds = tuple(Dependency(names[j], rng.choice(['FS', 'FS', 'SS']), rng.choice([0, 0, 1800]))
                          for j in range(i) if rng.random() < 0.25)
            if rng.random() < 0.15:
                kw['fixed_start'] = dt(day=rng.randint(19, 21), hour=rng.choice([9, 10, 13]))
            if rng.random() < 0.5:
                kw['due'] = dt(day=rng.randint(20, 28), hour=17)
            ops.append(Operation(name, f'o{i % 3}', rng.choice([1800, 3600, 7200]), (alt(machine),),
                                 predecessors=preds, **kw))
        phases.append({
            'id': f'job:{name}', 'machine_id': machine,
            'target_state': rng.choice(['RED', 'BLUE']), 'setup_seconds': rng.choice([900, 1800]),
            'setup_requirements': [req('machine', 1, 10000, [machine]),
                                   req('operator', 1, rng.choice([5000, 10000]), ['op'])],
            'run_requirements': [req('machine', 1, 10000, [machine]),
                                 req('operator', 1, rng.choice([2500, 5000]), ['op'])]})
    resources = [Resource(m, 'work', kind='machine') for m in machines] + [Resource('op', 'work', kind='operator')]
    s = scenario(ops, resources=tuple(resources))
    data = {'schema_version': 1,
            'resources': [{'id': m, 'kind': 'machine'} for m in machines] + [{'id': 'op', 'kind': 'operator'}],
            'initial_machine_states': {m: 'RED' for m in machines}, 'phases': phases}
    return s, data


class StageFuzzTests(unittest.TestCase):
    def test_every_returned_plan_passes_the_independent_audit(self):
        planned = failed_closed = 0
        for seed in range(120):
            rng = random.Random(seed)
            try:
                s, data = build(rng)
                if not data['phases']:
                    continue
                contract = load_setup_contract(json.dumps(data))
                sequence = default_machine_sequence(s)
                result = schedule_setup_stages(s, contract, sequence)
            except ValidationError:
                failed_closed += 1
                continue
            planned += 1
            with self.subTest(seed=seed):
                self.assertEqual(verify_plan(result.scenario, result.plan), ())
                self.assertEqual(audit_stage_capacity(contract, s, sequence, result.bookings), ())
                search = optimize_setup_sequence(s, contract, sequence, objective='setup',
                                                 max_evaluations=6, neighbourhood='insertion')
                self.assertEqual(audit_stage_capacity(contract, s, search.sequence,
                                                      search.winner.bookings), ())
                self.assertLessEqual(search.selected.setup_seconds, search.baseline.setup_seconds)
        self.assertGreater(planned, 20, 'fuzz must exercise real plans, not only rejections')


if __name__ == '__main__':
    unittest.main()
