"""Machine-only setup reservation projection: bounded, independently verified."""
from dataclasses import replace
import unittest

from planning_factory.domain.models import Interval, Nesting
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.setup_audit import audit_setups
from planning_factory.engine.setup_booking import (
    project_machine_setups, verify_setup_projection,
)
from .helpers import dt, op, scenario


def rules(seconds=3600):
    return {'schema_version': 1, 'families': {'a': 'A', 'b': 'B'},
            'machines': [{'resource_id': 'm', 'initial_family': 'A',
                          'transitions': [
                              {'from_family': 'A', 'to_family': 'B',
                               'seconds': seconds}]}]}


class SetupBookingTests(unittest.TestCase):
    def test_explicit_machine_booking_is_calendar_aligned_and_deterministic(self):
        s = scenario([op('a'), op('b', release=dt(hour=10))])
        p = plan(s)
        before = p
        one = project_machine_setups(s, p, rules(), placement='latest',
                                     allow_split=False)
        two = project_machine_setups(s, p, rules(), placement='latest',
                                     allow_split=False)
        self.assertEqual(one, two)
        self.assertEqual(before, p)
        self.assertEqual(len(one.reservations), 1)
        r, = one.reservations
        self.assertEqual((r.predecessor_task, r.successor_task), ('job:a', 'job:b'))
        self.assertEqual(r.segments, (Interval(dt(hour=9), dt(hour=10)),))
        self.assertEqual(one.to_dict()['total_machine_setup_seconds'], 3600)
        self.assertEqual(verify_setup_projection(s, p, rules(), one), ())

    def test_explicit_split_policy_across_two_working_days(self):
        s = scenario([op('a', fixed_start=dt(hour=15)), op('b', release=dt(20, 10))])
        p = plan(s)
        self.assertTrue(audit_setups(s, p, rules(10800)).valid)
        with self.assertRaisesRegex(ValidationError, 'under explicit split policy'):
            project_machine_setups(s, p, rules(10800),
                                   placement='earliest', allow_split=False)
        result = project_machine_setups(s, p, rules(10800),
                                        placement='earliest', allow_split=True)
        r, = result.reservations
        self.assertEqual(r.segments,
                         (Interval(dt(hour=16), dt(hour=17)),
                          Interval(dt(20, 8), dt(20, 10))))
        self.assertEqual(verify_setup_projection(s, p, rules(10800), result), ())

    def test_insufficient_gap_or_unknown_policy_is_refused(self):
        s = scenario([op('a'), op('b')])
        p = plan(s)
        with self.assertRaisesRegex(ValidationError, 'setup-infeasible plan'):
            project_machine_setups(s, p, rules(), placement='latest',
                                   allow_split=False)
        with self.assertRaises(ValueError):
            project_machine_setups(s, p, rules(), placement='center',
                                   allow_split=False)
        with self.assertRaises(ValueError):
            project_machine_setups(s, p, rules(), placement='latest',
                                   allow_split=1)

    def test_forged_overlap_or_duration_is_rejected(self):
        s = scenario([op('a'), op('b', release=dt(hour=10))])
        p = plan(s)
        projection = project_machine_setups(s, p, rules(),
                                            placement='latest', allow_split=False)
        r, = projection.reservations
        # Conflicts with production a at 08:00 - 09:00.
        forged = replace(projection, reservations=(
            replace(r, segments=(Interval(dt(hour=8), dt(hour=9)),)),))
        violations = verify_setup_projection(s, p, rules(), forged)
        self.assertTrue(any('outside predecessor/successor' in v or 'overlaps work' in v
                            for v in violations))
        forged = replace(projection, reservations=(
            replace(r, segments=(Interval(dt(hour=9), dt(hour=9,)),)),))
        self.assertTrue(verify_setup_projection(s, p, rules(), forged))

    def test_shared_nesting_counts_one_machine_setup(self):
        s = scenario([
            op('a', order_id='A', release=dt(hour=10)),
            op('b', order_id='B', release=dt(hour=10)),
        ], nestings=(Nesting('shared', ('a', 'b'), 3600),))
        spec = rules()
        spec['machines'][0]['initial_family'] = 'B'
        spec['machines'][0]['transitions'].append(
            {'from_family': 'B', 'to_family': 'A', 'seconds': 3600})
        spec['families']['b'] = 'A'
        result = project_machine_setups(s, plan(s), spec,
                                        placement='earliest', allow_split=False)
        self.assertEqual(len(result.reservations), 1)
        self.assertEqual(result.reservations[0].successor_task, 'nest:shared')

    def test_interleaving_with_explicit_zero_transitions_needs_no_booking(self):
        s = scenario([op('a', hours=7, splittable=True),
                      op('b', fixed_start=dt(hour=13))])
        spec = rules(0)
        spec['machines'][0]['transitions'].append(
            {'from_family': 'B', 'to_family': 'A', 'seconds': 0})
        p = plan(s)
        result = project_machine_setups(s, p, spec, placement='latest',
                                        allow_split=False)
        self.assertEqual(result.reservations, ())
        self.assertEqual(verify_setup_projection(s, p, spec, result), ())


if __name__ == '__main__':
    unittest.main()
