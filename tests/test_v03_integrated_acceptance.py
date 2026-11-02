"""Integrated synthetic v0.3 acceptance: material + technology + ADR003 stages.

Not a customer acceptance nor proof that all v0.3 adapters are integrated.
The fixture explicitly declares selected machine and tool allocations.
"""
import json
from decimal import Decimal
import unittest

from planning_factory.domain.models import Alternative, Demand, Resource
from planning_factory.domain.production_flow import (
    MaterialNeed, MaterialSupply, allocate_materials, apply_material_releases,
)
from planning_factory.domain.technology import (
    TechnologyCatalog, TechnicalNeed, MachineCapability, apply_technical_limits,
)
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.engine.setup_sequence_search import optimize_setup_sequence
from planning_factory.engine.setup_capacity import audit_stage_capacity
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op, scenario


def workshop():
    machines = ('m1', 'm2', 'm3')
    resources = tuple(Resource(m, 'work') for m in machines) + (
        Resource('operator', 'work', kind='operator'),
        Resource('tool', 'work', kind='tool'))
    tasks, needs, phases = [], [], []
    for i in range(12):
        machine_index = i % 3
        machine = machines[machine_index]
        oid = f'phase{i:02d}'
        alternatives = tuple(Alternative((Demand(m), Demand('operator')))
                             for m in machines)
        tasks.append(op(oid, hours=1, order_id=f'ORDER{i:02d}',
                        due=dt(23, 12), alternatives=alternatives))
        needs.append(TechnicalNeed(f'job:{oid}', 'THICKNESS',
                                   Decimal(2 + machine_index * 4), 'mm'))
        st = 'BLUE' if (i // 3) % 2 else 'RED'
        phases.append({
            'id': f'job:{oid}', 'machine_id': machine,
            'target_state': st, 'setup_seconds': 900,
            'setup_requirements': [
                {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
                 'candidate_ids': [machine]},
                {'kind': 'operator', 'count': 1, 'occupancy_bps': 5000,
                 'candidate_ids': ['operator']},
                {'kind': 'tool', 'count': 1, 'occupancy_bps': 5000,
                 'candidate_ids': ['tool']}],
            'run_requirements': [
                {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
                 'candidate_ids': [machine]},
                {'kind': 'operator', 'count': 1, 'occupancy_bps': 2500,
                 'candidate_ids': ['operator']}],
        })
    source = scenario(tasks, resources=resources)
    catalog = TechnologyCatalog('industrial-synthetic-r1', tuple(needs),
        tuple(MachineCapability(m, 'THICKNESS', Decimal(lo), Decimal(hi), 'mm')
              for m, lo, hi in [('m1', 1, 4), ('m2', 5, 8), ('m3', 9, 12)]))
    contract = load_setup_contract(json.dumps({
        'schema_version': 1,
        'resources': [{'id': m, 'kind': 'machine'} for m in machines] +
                     [{'id': 'operator', 'kind': 'operator'},
                      {'id': 'tool', 'kind': 'tool'}],
        'initial_machine_states': {m: 'RED' for m in machines},
        'phases': phases,
    }))
    supplies = (
        MaterialSupply('warehouse', 'SHEET', Decimal(6), dt(19, 0)),
        MaterialSupply('purchase-PO2', 'SHEET', Decimal(6), dt(20, 9)),
    )
    usage = tuple(MaterialNeed(f'job:phase{i:02d}', 'SHEET', Decimal(1))
                  for i in range(12))
    ordered = tuple(f'job:phase{i:02d}' for i in range(12))
    return source, catalog, contract, supplies, usage, ordered


class IntegratedV03AcceptanceTests(unittest.TestCase):
    def test_12_phase_factory_works_across_model_material_setup_and_optimization(self):
        original, tech, contract, supplies, needs, seq = workshop()
        technically_valid = apply_technical_limits(original, tech)
        self.assertTrue(all(len(x.alternatives) == 1 for x in technically_valid.operations))
        material = allocate_materials(technically_valid, supplies, needs, seq)
        self.assertEqual(material.shortages, ())
        self.assertEqual(sum(x.quantity for x in material.pegs), Decimal(12))
        ready = apply_material_releases(technically_valid, material)
        self.assertEqual(sum(x.release >= dt(20, 9) for x in ready.operations), 6)
        scored = optimize_setup_sequence(ready, contract, seq, objective='delivery',
                                         max_evaluations=10)
        self.assertGreaterEqual(scored.evaluated, 1)
        self.assertLessEqual(scored.evaluated, 10)
        self.assertEqual(verify_plan(scored.winner.scenario, scored.winner.plan), ())
        self.assertEqual(audit_stage_capacity(
            contract, ready, scored.sequence, scored.winner.bookings), ())
        self.assertEqual(sum(x.stage == 'run' for x in scored.winner.bookings), 12)
        self.assertLessEqual(scored.selected.late_orders, scored.baseline.late_orders)
        self.assertEqual(scored, optimize_setup_sequence(
            ready, contract, seq, objective='delivery', max_evaluations=10))
        self.assertTrue(all(o.release is None for o in original.operations))

    def test_insufficient_purchase_fails_before_staged_scheduling(self):
        source, tech, contract, supplies, needs, seq = workshop()
        s = apply_technical_limits(source, tech)
        report = allocate_materials(s, supplies[:1], needs, seq)
        self.assertEqual(len(report.shortages), 6)
        from planning_factory.domain.validation import ValidationError
        with self.assertRaisesRegex(ValidationError, 'shortages'):
            apply_material_releases(s, report)


if __name__ == '__main__':
    unittest.main()
