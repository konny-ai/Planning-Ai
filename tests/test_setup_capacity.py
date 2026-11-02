"""Synthetic setup/run resource capacity audit, independent from Factory ERP."""
import json
from dataclasses import replace
import unittest

from planning_factory.domain.models import Dependency, Nesting, Resource, Interval
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.engine.setup_capacity import (
    StageAllocation, StageBooking, audit_stage_capacity,
)
from planning_factory.domain.validation import ValidationError
from .helpers import alt, dt, op, scenario


def contract_data(second_machine=False):
    resources = [
        {'id': 'm', 'kind': 'machine'},
        {'id': 'op', 'kind': 'operator'},
        {'id': 'tool', 'kind': 'tool'},
    ]
    phases = [
        {'id': 'job:a', 'machine_id': 'm', 'target_state': 'BLUE',
         'setup_seconds': 1800,
         'setup_requirements': [
             {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
              'candidate_ids': ['m']},
             {'kind': 'operator', 'count': 1, 'occupancy_bps': 5000,
              'candidate_ids': ['op']},
             {'kind': 'tool', 'count': 1, 'occupancy_bps': 10000,
              'candidate_ids': ['tool']}],
         'run_requirements': [
             {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
              'candidate_ids': ['m']},
             {'kind': 'operator', 'count': 1, 'occupancy_bps': 2500,
              'candidate_ids': ['op']}]},
        {'id': 'job:b', 'machine_id': 'm', 'target_state': 'BLUE',
         'setup_seconds': 1200,
         'setup_requirements': [
             {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
              'candidate_ids': ['m']}],
         'run_requirements': [
             {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
              'candidate_ids': ['m']}]},
    ]
    return {'schema_version': 1, 'resources': resources,
            'initial_machine_states': {'m': 'RED'}, 'phases': phases}


def stage(phase, mode, start, end, allocations):
    return StageBooking(phase, mode, (Interval(start, end),),
                        tuple(StageAllocation(name, pct) for name, pct in allocations))


def factory_scenario():
    return scenario([op('a'), op('b')], resources=(
        Resource('m', 'work', 1, 'machine'),
        Resource('op', 'work', 1, 'operator'),
        Resource('tool', 'work', 1, 'tool')))


class SetupCapacityTests(unittest.TestCase):
    def test_stage_bookings_use_distinct_percentages_and_skip_same_machine_state(self):
        s = factory_scenario()
        contract = load_setup_contract(json.dumps(contract_data()))
        bookings = (
            stage('job:a', 'setup', dt(hour=8), dt(hour=8, minute=30),
                  [('m', 10000), ('op', 5000), ('tool', 10000)]),
            stage('job:a', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                  [('m', 10000), ('op', 2500)]),
            stage('job:b', 'run', dt(hour=9, minute=30), dt(hour=10, minute=30),
                  [('m', 10000)]),
        )
        self.assertEqual(audit_stage_capacity(contract, s, ('job:a', 'job:b'), bookings), ())
        extra = stage('job:b', 'setup', dt(hour=9, minute=30), dt(hour=9, minute=50),
                      [('m', 10000)])
        errors = audit_stage_capacity(contract, s, ('job:a', 'job:b'), bookings + (extra,))
        self.assertTrue(any('unexpected booking' in x for x in errors))

    def test_shared_operator_occupancy_must_not_exceed_full_capacity(self):
        data = contract_data()
        data['resources'].append({'id': 'm2', 'kind': 'machine'})
        data['initial_machine_states']['m2'] = 'BLUE'
        c = dict(data['phases'][1], id='job:c', machine_id='m2',
                 target_state='RED', setup_seconds=1800,
                 setup_requirements=[
                     {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
                      'candidate_ids': ['m2']},
                     {'kind': 'operator', 'count': 1, 'occupancy_bps': 5000,
                      'candidate_ids': ['op']}],
                 run_requirements=[
                     {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
                      'candidate_ids': ['m2']}])
        data['phases'].append(c)
        s = scenario([op('a'), op('b'), op('c', resource='m2')],
                     resources=(Resource('m', 'work'), Resource('m2', 'work'),
                                Resource('op', 'work', kind='operator'),
                                Resource('tool', 'work', kind='tool')))
        contract = load_setup_contract(json.dumps(data))
        booking = (
            stage('job:a', 'setup', dt(hour=8), dt(hour=8, minute=30),
                  [('m', 10000), ('op', 5000), ('tool', 10000)]),
            stage('job:c', 'setup', dt(hour=8), dt(hour=8, minute=30),
                  [('m2', 10000), ('op', 5000)]),
            stage('job:a', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                  [('m', 10000), ('op', 2500)]),
            stage('job:c', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                  [('m2', 10000)]),
            stage('job:b', 'run', dt(hour=9, minute=30), dt(hour=10, minute=30),
                  [('m', 10000)]),
        )
        self.assertEqual(audit_stage_capacity(contract, s,
                         ('job:a', 'job:c', 'job:b'), booking), ())
        bad = replace(booking[1], allocations=(
            StageAllocation('m2', 10000), StageAllocation('op', 7500)))
        errors = audit_stage_capacity(contract, s,
                                      ('job:a', 'job:c', 'job:b'),
                                      (booking[0], bad, *booking[2:]))
        self.assertTrue(any('occupancy' in x and 'op:' in x for x in errors))

    def test_setup_can_cross_shift_and_precede_material_release(self):
        p = contract_data()
        p['phases'] = [p['phases'][0]]
        p['phases'][0]['setup_seconds'] = 5400
        s = scenario([op('a', release=dt(20, 8, 30))],
                     resources=(Resource('m', 'work'),
                                Resource('op', 'work', kind='operator'),
                                Resource('tool', 'work', kind='tool')))
        a = StageBooking('job:a', 'setup', (
            Interval(dt(19, 16), dt(19, 17)),
            Interval(dt(20, 8), dt(20, 8, 30)),
        ), (StageAllocation('m', 10000), StageAllocation('op', 5000),
            StageAllocation('tool', 10000)))
        b = stage('job:a', 'run', dt(20, 8, 30), dt(20, 9, 30),
                  [('m', 10000), ('op', 2500)])
        self.assertEqual(audit_stage_capacity(load_setup_contract(json.dumps(p)),
                        s, ('job:a',), (a, b)), ())

    def test_nesting_task_is_one_run_and_setup_not_multiplied(self):
        p = contract_data()
        p['phases'] = [dict(p['phases'][0], id='nest:ab', setup_seconds=1800)]
        s = scenario([op('a', order_id='A'), op('b', order_id='B')],
                     nestings=(Nesting('ab', ('a', 'b'), 3600),),
                     resources=(Resource('m', 'work'),
                                Resource('op', 'work', kind='operator'),
                                Resource('tool', 'work', kind='tool')))
        a = stage('nest:ab', 'setup', dt(hour=8), dt(hour=8, minute=30),
                  [('m', 10000), ('op', 5000), ('tool', 10000)])
        b = stage('nest:ab', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                  [('m', 10000), ('op', 2500)])
        self.assertEqual(audit_stage_capacity(load_setup_contract(json.dumps(p)),
                         s, ('nest:ab',), (a, b)), ())

    def test_no_double_count_of_legacy_static_setup(self):
        s = scenario([op('a', setup_seconds=1800), op('b')])
        with self.assertRaisesRegex(ValidationError, 'double-count'):
            audit_stage_capacity(load_setup_contract(json.dumps(contract_data())),
                                 s, ('job:a', 'job:b'), ())

    def test_locked_positive_setup_cannot_be_silently_moved(self):
        p = contract_data()
        p['phases'][0]['setup_locked'] = True
        errors = audit_stage_capacity(
            load_setup_contract(json.dumps(p)), factory_scenario(),
            ('job:a', 'job:b'),
            (stage('job:a', 'setup', dt(hour=8), dt(hour=8, minute=30),
                   [('m', 10000), ('op', 5000), ('tool', 10000)]),
             stage('job:a', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                   [('m', 10000), ('op', 2500)]),
             stage('job:b', 'run', dt(hour=9, minute=30), dt(hour=10, minute=30),
                   [('m', 10000)])))
        self.assertTrue(any('locked setup requires frozen' in x for x in errors))

    def test_independent_audit_rejects_technically_excluded_machine(self):
        data = contract_data()
        data['phases'] = [data['phases'][0]]
        s = scenario([op('a', resource='m2')], resources=(
            Resource('m', 'work'), Resource('m2', 'work'),
            Resource('op', 'work', kind='operator'),
            Resource('tool', 'work', kind='tool')))
        bookings = (
            stage('job:a', 'setup', dt(hour=8), dt(hour=8, minute=30),
                  [('m', 10000), ('op', 5000), ('tool', 10000)]),
            stage('job:a', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                  [('m', 10000), ('op', 2500)]),
        )
        violations = audit_stage_capacity(
            load_setup_contract(json.dumps(data)), s, ('job:a',), bookings)
        self.assertTrue(any('not in eligible run alternatives' in v
                            for v in violations))

    def test_capacity_audit_checks_fs_lag_on_run_not_setup(self):
        # Capacity and machine order are valid, but b violates a 1-hour FS lag.
        s = scenario([
            op('a'),
            op('b', predecessors=(Dependency('a', 'FS', 3600),)),
        ], resources=(Resource('m', 'work'),
                      Resource('op', 'work', kind='operator'),
                      Resource('tool', 'work', kind='tool')))
        c = load_setup_contract(json.dumps(contract_data()))
        bookings = (
            stage('job:a', 'setup', dt(hour=8), dt(hour=8, minute=30),
                  [('m', 10000), ('op', 5000), ('tool', 10000)]),
            stage('job:a', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                  [('m', 10000), ('op', 2500)]),
            stage('job:b', 'run', dt(hour=9, minute=30), dt(hour=10, minute=30),
                  [('m', 10000)]),
        )
        errors = audit_stage_capacity(c, s, ('job:a', 'job:b'), bookings)
        self.assertTrue(any('FS predecessor job:a' in x for x in errors))
        valid = bookings[:-1] + (
            stage('job:b', 'run', dt(hour=10, minute=30), dt(hour=11, minute=30),
                  [('m', 10000)]),)
        self.assertEqual(audit_stage_capacity(c, s, ('job:a', 'job:b'), valid), ())

    def test_capacity_audit_checks_ss_lag_between_run_starts(self):
        s = scenario([
            op('a'),
            op('b', predecessors=(Dependency('a', 'SS', 7200),)),
        ], resources=(Resource('m', 'work'),
                      Resource('op', 'work', kind='operator'),
                      Resource('tool', 'work', kind='tool')))
        c = load_setup_contract(json.dumps(contract_data()))
        bookings = (
            stage('job:a', 'setup', dt(hour=8), dt(hour=8, minute=30),
                  [('m', 10000), ('op', 5000), ('tool', 10000)]),
            stage('job:a', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                  [('m', 10000), ('op', 2500)]),
            stage('job:b', 'run', dt(hour=9, minute=30), dt(hour=10, minute=30),
                  [('m', 10000)]),
        )
        errors = audit_stage_capacity(c, s, ('job:a', 'job:b'), bookings)
        self.assertTrue(any('SS predecessor job:a' in x for x in errors))

    def test_locked_setup_is_verified_against_exact_frozen_snapshot(self):
        data = contract_data()
        data['phases'][0]['setup_locked'] = True
        c = load_setup_contract(json.dumps(data))
        s = factory_scenario()
        original = (
            stage('job:a', 'setup', dt(hour=8), dt(hour=8, minute=30),
                  [('m', 10000), ('op', 5000), ('tool', 10000)]),
            stage('job:a', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                  [('m', 10000), ('op', 2500)]),
            stage('job:b', 'run', dt(hour=9, minute=30), dt(hour=10, minute=30),
                  [('m', 10000)]),
        )
        self.assertEqual(audit_stage_capacity(
            c, s, ('job:a', 'job:b'), original,
            frozen_bookings=(original[0],)), ())
        altered = replace(original[0], allocations=(
            StageAllocation('m', 10000), StageAllocation('op', 6000),
            StageAllocation('tool', 10000)))
        findings = audit_stage_capacity(
            c, s, ('job:a', 'job:b'), (altered, *original[1:]),
            frozen_bookings=(original[0],))
        self.assertTrue(any('frozen booking was changed' in x for x in findings))
        missing = audit_stage_capacity(
            c, s, ('job:a', 'job:b'), original,
            frozen_bookings=(original[0], original[0]))
        self.assertTrue(any('duplicate frozen booking' in x for x in missing))

    def test_frozen_run_cannot_be_moved_even_without_setup_lock(self):
        c = load_setup_contract(json.dumps(contract_data()))
        s = factory_scenario()
        original = (
            stage('job:a', 'setup', dt(hour=8), dt(hour=8, minute=30),
                  [('m', 10000), ('op', 5000), ('tool', 10000)]),
            stage('job:a', 'run', dt(hour=8, minute=30), dt(hour=9, minute=30),
                  [('m', 10000), ('op', 2500)]),
            stage('job:b', 'run', dt(hour=9, minute=30), dt(hour=10, minute=30),
                  [('m', 10000)]),
        )
        self.assertEqual(audit_stage_capacity(
            c, s, ('job:a', 'job:b'), original,
            frozen_bookings=(original[1],)), ())
        moved = replace(original[1], segments=(Interval(
            dt(hour=8, minute=40), dt(hour=9, minute=40)),))
        errors = audit_stage_capacity(
            c, s, ('job:a', 'job:b'), (original[0], moved, original[2]),
            frozen_bookings=(original[1],))
        self.assertTrue(any('frozen booking was changed' in x for x in errors))

if __name__ == '__main__':
    unittest.main()
