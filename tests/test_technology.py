"""Typed machine technical constraints independent from scheduling calendars."""
from decimal import Decimal
import unittest

from planning_factory.domain.models import Alternative, Demand, Nesting, Resource
from planning_factory.domain.technology import (
    TechnicalNeed, MachineCapability, TechnologyCatalog,
    assess_technology, apply_technical_limits,
)
from planning_factory.domain.validation import ValidationError, compile_tasks
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op, scenario


def catalog(*, value='8', unit='mm'):
    return TechnologyCatalog('2026-10-release-1',
        (TechnicalNeed('job:a', 'THICKNESS', Decimal(value), unit),),
        (MachineCapability('m', 'THICKNESS', Decimal('0.5'), Decimal('6'), 'mm'),
         MachineCapability('m2', 'THICKNESS', Decimal('1'), Decimal('12'), 'mm')))


def shop(operations=None):
    a = Alternative((Demand('m'),))
    b = Alternative((Demand('m2'),))
    return scenario(operations or [op('a', alternatives=(a, b))],
                    resources=(Resource('m', 'work'), Resource('m2', 'work')))


class TechnicalEligibilityTests(unittest.TestCase):
    def test_filters_machine_by_parameter_before_finite_booking(self):
        s = shop()
        c = catalog()
        r = assess_technology(s, c)
        self.assertEqual(r[0].accepted_indices, (1,))
        self.assertIn('out of technical range', r[0].rejected[0][1][0])
        filtered = apply_technical_limits(s, c)
        self.assertEqual(filtered.operations[0].alternatives,
                         (Alternative((Demand('m2'),)),))
        self.assertEqual(s.operations[0].alternatives[0], Alternative((Demand('m'),)))
        result = plan(filtered)
        self.assertEqual(verify_plan(filtered, result), ())
        self.assertEqual(result.assignments[0].demands[0].resource_id, 'm2')

    def test_missing_range_or_unit_mismatch_fails_closed(self):
        s = shop()
        no_capability = TechnologyCatalog('r1',
            (TechnicalNeed('job:a', 'THICKNESS', Decimal(8), 'mm'),), ())
        with self.assertRaisesRegex(ValidationError, 'no technically eligible'):
            apply_technical_limits(s, no_capability)
        incompatible_units = catalog(unit='cm')
        with self.assertRaisesRegex(ValidationError, 'no technically eligible'):
            apply_technical_limits(s, incompatible_units)
        reason = assess_technology(s, incompatible_units)[0].rejected[0][1]
        self.assertTrue(any('unit mismatch' in x for x in reason))

    def test_rejects_invalid_or_duplicate_catalog_values(self):
        s = shop()
        for bad in (
            TechnologyCatalog('r', (TechnicalNeed('job:a', 'W', Decimal('NaN'), 'mm'),), ()),
            TechnologyCatalog('r', (TechnicalNeed('job:unknown', 'W', Decimal(5), 'mm'),), ()),
            TechnologyCatalog('r', (), (MachineCapability('m', 'W', Decimal(5), Decimal(1), 'mm'),)),
            TechnologyCatalog('r', (), (MachineCapability('m', 'W', Decimal(0), Decimal(1), 'mm'),
                                        MachineCapability('m', 'W', Decimal(0), Decimal(2), 'mm'))),
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                assess_technology(s, bad)

    def test_atomic_nesting_filters_both_members_identically(self):
        alt = (Alternative((Demand('m'),)), Alternative((Demand('m2'),)))
        s = shop([op('a', order_id='A', alternatives=alt),
                  op('b', order_id='B', alternatives=alt)])
        from dataclasses import replace
        s = replace(s, nestings=(Nesting('shared', ('a', 'b'), 3600),))
        c = TechnologyCatalog('r1',
           (TechnicalNeed('nest:shared', 'THICKNESS', Decimal(8), 'mm'),),
           catalog().capabilities)
        updated = apply_technical_limits(s, c)
        task, _, _ = compile_tasks(updated)
        self.assertEqual(len(task['nest:shared'].alternatives), 1)
        self.assertEqual(len(updated.operations[0].alternatives), 1)
        self.assertEqual(len(updated.operations[1].alternatives), 1)
        self.assertEqual(verify_plan(updated, plan(updated)), ())

    def test_empty_technical_needs_do_not_alter_original_scenario(self):
        s = shop()
        c = TechnologyCatalog('r1', (), ())
        self.assertEqual(apply_technical_limits(s, c), s)

    def test_existing_fixed_work_requires_explicit_reconciliation(self):
        s = shop([op('a', alternatives=(Alternative((Demand('m'),)),
                                         Alternative((Demand('m2'),))),
                     fixed_start=dt(hour=8))])
        with self.assertRaisesRegex(ValidationError, 'fixed work'):
            apply_technical_limits(s, catalog())


if __name__ == '__main__':
    unittest.main()
