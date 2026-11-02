"""Version-independent production BOM and finite shared material tests."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import unittest

from planning_factory.domain.models import Nesting
from planning_factory.domain.production_flow import (
    Item, BomLine, CustomerLine, ProductionLot, MaterialCommitment, MaterialSupply, MaterialNeed,
    validate_production, explode_bom, allocate_materials, apply_material_releases,
)
from planning_factory.domain.validation import ValidationError, compile_tasks
from .helpers import dt, op, scenario


def d(x):
    return Decimal(str(x))


class ProductionFlowTests(unittest.TestCase):
    def test_bom_multilevel_and_multiple_customer_lines(self):
        items = (Item('P', 'pcs', 'make'), Item('SUB', 'pcs', 'make'),
                 Item('SCREW', 'pcs', 'buy'), Item('SHEET', 'kg', 'buy'))
        bom = (BomLine('P', 'SUB', d(2)), BomLine('SUB', 'SCREW', d(4)),
               BomLine('P', 'SHEET', d('1.25')))
        lines = (CustomerLine('line-A', 'P', d(3), dt(23)),
                 CustomerLine('line-B', 'P', d(2), dt(24)))
        lots = (ProductionLot('LOT', 'P', d(5), ('line-A', 'line-B'), ('a',)),)
        validate_production(items, bom, lines, lots)
        self.assertEqual(dict(explode_bom('P', d(5), items, bom)),
                         {'SUB': d(10), 'SCREW': d(40), 'SHEET': d('6.25')})

    def test_cycle_unknown_item_duplicate_and_zero_qty_rejected(self):
        items = (Item('P', 'pcs', 'make'), Item('C', 'pcs', 'buy'))
        for invalid in (
            (BomLine('P', 'C', d(2)), BomLine('C', 'P', d(1))),
            (BomLine('P', 'P', d(1)),),
            (BomLine('P', 'MISSING', d(1)),),
            (BomLine('P', 'C', d(0)),),
            (BomLine('P', 'C', d('NaN')),),
            (BomLine('P', 'C', d(1)), BomLine('P', 'C', d(2))),
        ):
            with self.subTest(invalid=invalid), self.assertRaises(ValidationError):
                validate_production(items, invalid)

    def test_pegging_never_double_spends_stock_and_respects_receipt_dates(self):
        s = scenario([op('a'), op('b')])
        supplies = (MaterialSupply('STOCK', 'S', d(5), dt(19, 7)),
                    MaterialSupply('PO', 'S', d(5), dt(20, 10)))
        needs = (MaterialNeed('job:a', 'S', d(6)), MaterialNeed('job:b', 'S', d(4)))
        result = allocate_materials(s, supplies, needs, ('job:a', 'job:b'))
        self.assertEqual(result.shortages, ())
        self.assertEqual(dict(result.releases)['job:a'], dt(20, 10))
        self.assertEqual(dict(result.releases)['job:b'], dt(20, 10))
        self.assertEqual([(p.supply_id, p.quantity) for p in result.pegs],
                         [('STOCK', d(5)), ('PO', d(1)), ('PO', d(4))])
        adjusted = apply_material_releases(s, result)
        tasks, _, _ = compile_tasks(adjusted)
        self.assertEqual(tasks['job:a'].release, dt(20, 10))
        self.assertEqual(tasks['job:b'].release, dt(20, 10))
        self.assertIsNone(s.operations[0].release)  # input unchanged

    def test_unavailable_material_does_not_consume_partial_stock(self):
        s = scenario([op('a'), op('b')])
        result = allocate_materials(
            s, (MaterialSupply('s', 'S', d(3), dt(19)),),
            (MaterialNeed('job:a', 'S', d(4)),
             MaterialNeed('job:b', 'S', d(3))), ('job:a', 'job:b'))
        self.assertEqual(result.shortages, (('job:a', 'S', d(1)),))
        self.assertEqual(dict(result.releases)['job:b'], dt(19))
        self.assertEqual(sum(p.quantity for p in result.pegs), d(3))
        with self.assertRaisesRegex(ValidationError, 'shortages'):
            apply_material_releases(s, result)

    def test_shared_nesting_material_released_once_for_all_member_orders(self):
        s = scenario([op('a', order_id='A'), op('b', order_id='B')],
                     nestings=(Nesting('nest', ('a', 'b'), 3600),))
        result = allocate_materials(
            s, (MaterialSupply('purchase', 'S', d(6), dt(20, 9)),),
            (MaterialNeed('nest:nest', 'S', d(6)),), ('nest:nest',))
        self.assertEqual(len(result.pegs), 1)
        adjusted = apply_material_releases(s, result)
        tasks, _, _ = compile_tasks(adjusted)
        self.assertEqual(tasks['nest:nest'].release, dt(20, 9))
        self.assertEqual([o.release for o in adjusted.operations],
                         [dt(20, 9), dt(20, 9)])

    def test_incomplete_sequence_or_phantom_task_is_rejected(self):
        s = scenario([op('a'), op('b')])
        with self.assertRaisesRegex(ValidationError, 'priority'):
            allocate_materials(s, (), (), ('job:a',))
        with self.assertRaisesRegex(ValidationError, 'unknown'):
            allocate_materials(s, (), (MaterialNeed('job:c', 'S', d(1)),),
                               ('job:a', 'job:b'))

    def test_material_release_can_not_override_later_hard_release(self):
        s = scenario([op('a', release=dt(22, 8))])
        report = allocate_materials(s, (MaterialSupply('stock', 'S', d(3), dt(19)),),
                                    (MaterialNeed('job:a', 'S', d(2)),), ('job:a',))
        adjusted = apply_material_releases(s, report)
        self.assertEqual(adjusted.operations[0].release, dt(22, 8))

    def test_customer_lot_cannot_silently_cover_another_product(self):
        items = (Item('A', 'pcs', 'make'), Item('B', 'pcs', 'make'))
        lines = (CustomerLine('sale-B', 'B', d(1), dt(24)),)
        wrong = (ProductionLot('lot-A', 'A', d(1), ('sale-B',), ('a',)),)
        with self.assertRaisesRegex(ValidationError, 'invalid production lot'):
            validate_production(items, (), lines, wrong)

    def test_material_allocation_is_bound_to_exact_scenario_revision(self):
        s = scenario([op('a', hours=1)])
        stock = (MaterialSupply('stock', 'MATERIAL', d(1), dt(19)),)
        needs = (MaterialNeed('job:a', 'MATERIAL', d(1)),)
        result = allocate_materials(s, stock, needs, ('job:a',))
        # Exact-match task IDs do not imply identical schedules.
        altered = replace(s, operations=(replace(s.operations[0],
                                                 duration_seconds=7200),))
        with self.assertRaisesRegex(ValidationError, 'another scenario revision'):
            apply_material_releases(altered, result)
        # The original scenario still accepts the same immutable allocation.
        self.assertEqual(apply_material_releases(s, result).operations[0].release,
                         dt(19))

    def test_external_commitments_are_not_allocated_again(self):
        s = scenario([op('a'), op('b')])
        supplies = (MaterialSupply('stock', 'S', d(7), dt(19, 7)),)
        needs = (MaterialNeed('job:a', 'S', d(4)),
                 MaterialNeed('job:b', 'S', d(2)))
        outcome = allocate_materials(
            s, supplies, needs, ('job:a', 'job:b'),
            commitments=(MaterialCommitment('stock', d(1)),
                         MaterialCommitment('stock', d(1))))
        self.assertEqual(outcome.shortages, (('job:b', 'S', d(1)),))
        self.assertEqual([(peg.supply_id, peg.quantity) for peg in outcome.pegs],
                         [('stock', d(4))])
        self.assertEqual(tuple(tid for tid, _ in outcome.releases), ('job:a',))
        with self.assertRaisesRegex(ValidationError, 'shortages'):
            apply_material_releases(s, outcome)

    def test_external_commitments_reject_unknown_or_overdrawn_stock(self):
        s = scenario([op('a')])
        supplies = (MaterialSupply('stock', 'S', d(3), dt(19)),)
        for entries, problem in (
            ((MaterialCommitment('missing', d(1)),), 'unknown committed supply'),
            ((MaterialCommitment('stock', d(4)),), 'exceed'),
            ((MaterialCommitment('stock', d(2)),
              MaterialCommitment('stock', d(2))), 'exceed'),
            ((MaterialCommitment('stock', d(0)),), 'finite positive'),
        ):
            with self.subTest(entries=entries), self.assertRaisesRegex(ValidationError, problem):
                allocate_materials(s, supplies, (), ('job:a',), commitments=entries)


if __name__ == '__main__':
    unittest.main()
