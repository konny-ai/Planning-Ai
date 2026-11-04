"""Real two-stage setup/run planning using capacity-scaled canonical resources."""
import json
import unittest

from planning_factory.domain.models import Dependency, Interval, Resource, Nesting
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_stage_scheduler import schedule_setup_stages
from planning_factory.engine.setup_capacity import StageAllocation, StageBooking, audit_stage_capacity
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op, scenario


def requirement(kind, count, bps, ids):
    return {'kind': kind, 'count': count, 'occupancy_bps': bps, 'candidate_ids': ids}


def spec(second_machine=False):
    resources = [{'id': 'm', 'kind': 'machine'},
                 {'id': 'op', 'kind': 'operator'},
                 {'id': 'tool', 'kind': 'tool'}]
    phases = []
    for pid, target in [('a', 'BLUE'), ('b', 'BLUE')]:
        phases.append({
            'id': f'job:{pid}', 'machine_id': 'm', 'target_state': target,
            'setup_seconds': 1800,
            'setup_requirements': [
                requirement('machine', 1, 10000, ['m']),
                requirement('operator', 1, 5000, ['op']),
                requirement('tool', 1, 10000, ['tool'])],
            'run_requirements': [
                requirement('machine', 1, 10000, ['m']),
                requirement('operator', 1, 2500, ['op'])],
        })
    return {'schema_version': 1, 'resources': resources,
            'initial_machine_states': {'m': 'RED'}, 'phases': phases}


def base(operations=None, **kw):
    return scenario(operations or [op('a'), op('b')],
                    resources=(Resource('m', 'work', kind='machine'),
                               Resource('op', 'work', kind='operator'),
                               Resource('tool', 'work', kind='tool')),
                    **kw)


class TwoStageSchedulerTests(unittest.TestCase):
    def test_setup_and_work_are_independently_reserved_without_double_count(self):
        s, contract = base(), load_setup_contract(json.dumps(spec()))
        result = schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        self.assertEqual(len(result.bookings), 3)  # A setup/run, B run only
        self.assertEqual(
            [(x.phase_id, x.stage) for x in result.bookings if x.stage == 'setup'],
            [('job:a', 'setup')])
        self.assertEqual(verify_plan(result.scenario, result.plan), ())
        self.assertEqual(audit_stage_capacity(contract, s, ('job:a', 'job:b'),
                                              result.bookings), ())
        run_starts = {b.phase_id: b.segments[0].start for b in result.bookings
                      if b.stage == 'run'}
        self.assertEqual(run_starts, {'job:a': dt(hour=8, minute=30),
                                      'job:b': dt(hour=9, minute=30)})
        self.assertEqual(result, schedule_setup_stages(s, contract, ('job:a', 'job:b')))

    def test_setup_can_happen_before_material_release(self):
        s = base([op('a', release=dt(20, 8)), op('b')])
        contract = load_setup_contract(json.dumps(spec()))
        result = schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        setup = next(x for x in result.bookings if x.phase_id == 'job:a' and x.stage == 'setup')
        work = next(x for x in result.bookings if x.phase_id == 'job:a' and x.stage == 'run')
        self.assertLess(setup.segments[0].start, dt(20, 8))
        self.assertGreaterEqual(work.segments[0].start, dt(20, 8))
        self.assertEqual(verify_plan(result.scenario, result.plan), ())

    def test_setup_can_span_shift_boundary(self):
        s = base([op('a', release=dt(20, 8)), op('b')])
        p = spec()
        p['phases'][0]['setup_seconds'] = 9 * 3600
        result = schedule_setup_stages(s, load_setup_contract(json.dumps(p)),
                                       ('job:a', 'job:b'))
        setup = next(b for b in result.bookings if b.phase_id == 'job:a' and b.stage == 'setup')
        self.assertGreater(len(setup.segments), 1)
        self.assertEqual(sum(x.seconds for x in setup.segments), 9 * 3600)

    def test_shared_operator_cannot_be_double_booked_across_machines(self):
        p = spec()
        p['resources'].append({'id': 'm2', 'kind': 'machine'})
        p['initial_machine_states']['m2'] = 'RED'
        p['phases'][1]['machine_id'] = 'm2'
        p['phases'][1]['target_state'] = 'GREEN'
        p['phases'][1]['setup_requirements'][0]['candidate_ids'] = ['m2']
        p['phases'][1]['run_requirements'][0]['candidate_ids'] = ['m2']
        for phase in p['phases']:
            phase['setup_requirements'][1]['occupancy_bps'] = 10000
        s = scenario([op('a'), op('b', resource='m2')],
                     resources=(Resource('m', 'work'),
                                Resource('m2', 'work'),
                                Resource('op', 'work', kind='operator'),
                                Resource('tool', 'work', kind='tool')))
        contract = load_setup_contract(json.dumps(p))
        result = schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        setups = sorted((b for b in result.bookings if b.stage == 'setup'),
                        key=lambda b: b.segments[0].start)
        self.assertEqual(len(setups), 2)
        self.assertLessEqual(setups[0].segments[-1].end, setups[1].segments[0].start)
        self.assertEqual(audit_stage_capacity(contract, s, ('job:a', 'job:b'),
                                              result.bookings), ())

    def test_one_atomic_nesting_is_one_run_for_two_orders(self):
        p = spec()
        p['phases'] = [dict(p['phases'][0], id='nest:shared')]
        s = base([op('a', order_id='A'), op('b', order_id='B')],
                 nestings=(Nesting('shared', ('a', 'b'), 3600),))
        contract = load_setup_contract(json.dumps(p))
        result = schedule_setup_stages(s, contract, ('nest:shared',))
        self.assertEqual({(b.phase_id, b.stage) for b in result.bookings},
                         {('nest:shared', 'setup'), ('nest:shared', 'run')})
        self.assertEqual(sum(i.seconds for b in result.bookings if b.stage == 'run'
                             for i in b.segments), 3600)
        self.assertEqual(audit_stage_capacity(contract, s, ('nest:shared',),
                                              result.bookings), ())

    def test_ss_dependency_on_run_starts(self):
        s = base([op('a'), op('b', predecessors=(Dependency('a', 'SS', 7200),))])
        result = schedule_setup_stages(s, load_setup_contract(json.dumps(spec())),
                                       ('job:a', 'job:b'))
        starts = {b.phase_id: b.segments[0].start
                  for b in result.bookings if b.stage == 'run'}
        self.assertGreaterEqual(
            (starts['job:b'] - starts['job:a']).total_seconds(), 7200)

    def test_setup_rejects_ineligible_canonical_machine(self):
        data = spec()
        data['phases'] = [data['phases'][0]]
        s = scenario([op('a', resource='m2')], resources=(
            Resource('m', 'work'), Resource('m2', 'work'),
            Resource('op', 'work', kind='operator'),
            Resource('tool', 'work', kind='tool')))
        with self.assertRaisesRegex(ValidationError, 'not an eligible run machine'):
            schedule_setup_stages(s, load_setup_contract(json.dumps(data)),
                                  ('job:a',))

    def test_fail_closed_on_existing_static_setup_or_locked_work(self):
        contract = load_setup_contract(json.dumps(spec()))
        s = base([op('a', setup_seconds=3600), op('b')])
        with self.assertRaisesRegex(ValidationError, 'double-count'):
            schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        data = spec()
        data['phases'][0]['setup_locked'] = True
        with self.assertRaisesRegex(ValidationError, 'locked setup'):
            schedule_setup_stages(base(), load_setup_contract(json.dumps(data)),
                                  ('job:a', 'job:b'))

    def test_staged_scheduler_rejects_machine_excluded_by_run_alternatives(self):
        data = spec()
        data['phases'] = [data['phases'][0]]
        s = scenario([op('a', resource='m2')], resources=(
            Resource('m', 'work'), Resource('m2', 'work'),
            Resource('op', 'work', kind='operator'),
            Resource('tool', 'work', kind='tool')))
        c = load_setup_contract(json.dumps(data))
        with self.assertRaisesRegex(ValidationError, 'not an eligible run machine'):
            schedule_setup_stages(s, c, ('job:a',))

    def test_ss_precedence_is_enforced_between_runs_with_elapsed_lag(self):
        # The second run cannot start until two elapsed hours after the
        # first run started, although its setup may be prepared earlier.
        s = base([op('a'), op('b', predecessors=(Dependency('a', 'SS', 7200),))])
        contract = load_setup_contract(json.dumps(spec()))
        result = schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        runs = {b.phase_id: b for b in result.bookings if b.stage == 'run'}
        self.assertGreaterEqual(
            (runs['job:b'].segments[0].start -
             runs['job:a'].segments[0].start).total_seconds(), 7200)
        self.assertEqual(verify_plan(result.scenario, result.plan), ())

    def test_fs_precedence_still_uses_predecessor_run_end(self):
        s = base([op('a'), op('b', predecessors=(Dependency('a', 'FS', 3600),))])
        contract = load_setup_contract(json.dumps(spec()))
        result = schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        runs = {b.phase_id: b for b in result.bookings if b.stage == 'run'}
        self.assertGreaterEqual(
            (runs['job:b'].segments[0].start -
             runs['job:a'].segments[-1].end).total_seconds(), 3600)
        self.assertEqual(verify_plan(result.scenario, result.plan), ())

    def test_intact_frozen_setup_and_run_are_preserved(self):
        data = spec()
        data['phases'][0]['setup_locked'] = True
        contract = load_setup_contract(json.dumps(data))
        frozen = (
            StageBooking('job:a', 'setup',
                         (Interval(dt(hour=8), dt(hour=8, minute=30)),),
                         (StageAllocation('m', 10000), StageAllocation('op', 5000),
                          StageAllocation('tool', 10000))),
            StageBooking('job:a', 'run',
                         (Interval(dt(hour=8, minute=30), dt(hour=9, minute=30)),),
                         (StageAllocation('m', 10000), StageAllocation('op', 2500))),
        )
        result = schedule_setup_stages(base(), contract, ('job:a', 'job:b'),
                                       frozen_bookings=frozen)
        by_key = {(x.phase_id, x.stage): x for x in result.bookings}
        self.assertEqual(tuple(by_key[(x.phase_id, x.stage)] for x in frozen),
                         frozen)
        self.assertEqual(audit_stage_capacity(
            contract, base(), ('job:a', 'job:b'), result.bookings,
            frozen_bookings=frozen), ())

    def test_rejects_partial_frozen_work_and_changed_machine_bundle(self):
        contract = load_setup_contract(json.dumps(spec()))
        partial = StageBooking(
            'job:a', 'run',
            (Interval(dt(hour=8, minute=30), dt(hour=9)),
             Interval(dt(hour=9), dt(hour=9, minute=30))),
            (StageAllocation('m', 10000), StageAllocation('op', 2500)))
        with self.assertRaisesRegex(ValidationError, 'one intact segment'):
            schedule_setup_stages(base(), contract, ('job:a', 'job:b'),
                                  frozen_bookings=(partial,))
        wrong = StageBooking('job:a', 'run',
                             (Interval(dt(hour=8, minute=30),
                                       dt(hour=9, minute=30)),),
                             (StageAllocation('tool', 10000),))
        with self.assertRaisesRegex(ValidationError, 'not an eligible stage bundle'):
            schedule_setup_stages(base(), contract, ('job:a', 'job:b'),
                                  frozen_bookings=(wrong,))

class CompletedPhaseTests(unittest.TestCase):
    def _done(self, **kw):
        return base([op('a', hours=0, completed_at=dt(hour=0)), op('b', **kw)])

    def test_completed_phase_has_no_booking_and_sets_machine_state(self):
        contract = load_setup_contract(json.dumps(spec()))
        s = self._done()
        result = schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        keys = {(b.phase_id, b.stage) for b in result.bookings}
        # a (BLUE) already ran, so b (BLUE) needs no setup either.
        self.assertEqual(keys, {('job:b', 'run')})
        self.assertEqual(audit_stage_capacity(contract, s, ('job:a', 'job:b'),
                                              result.bookings), ())

    def test_state_change_after_completed_phase_still_needs_setup(self):
        data = spec()
        data['phases'][1]['target_state'] = 'RED'
        contract = load_setup_contract(json.dumps(data))
        result = schedule_setup_stages(self._done(), contract, ('job:a', 'job:b'))
        self.assertEqual({(b.phase_id, b.stage) for b in result.bookings},
                         {('job:b', 'setup'), ('job:b', 'run')})

    def test_completed_predecessor_does_not_block_successor(self):
        s = base([op('a', hours=0, completed_at=dt(hour=0)),
                  op('b', predecessors=(Dependency('a', 'FS'),))])
        contract = load_setup_contract(json.dumps(spec()))
        result = schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        run = next(b for b in result.bookings if b.stage == 'run')
        self.assertEqual(run.segments[0].start, dt(hour=8))

    def test_completed_must_precede_active_on_same_machine(self):
        contract = load_setup_contract(json.dumps(spec()))
        with self.assertRaisesRegex(ValidationError, 'must precede active'):
            schedule_setup_stages(self._done(), contract, ('job:b', 'job:a'))

    def test_audit_rejects_booking_for_completed_phase(self):
        contract = load_setup_contract(json.dumps(spec()))
        s = base([op('a', hours=0, completed_at=dt(hour=0)), op('b')])
        result = schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        extra = StageBooking('job:a', 'run',
                             (Interval(dt(hour=10), dt(hour=11)),),
                             (StageAllocation('m', 10000), StageAllocation('op', 2500)))
        errors = audit_stage_capacity(contract, s, ('job:a', 'job:b'),
                                      (*result.bookings, extra))
        self.assertTrue(any('completed phase must not carry' in e for e in errors))

    def test_completed_order_counts_in_kpis(self):
        from planning_factory.engine.setup_sequence_search import sequence_metrics
        contract = load_setup_contract(json.dumps(spec()))
        s = base([op('a', hours=0, order_id='o1', completed_at=dt(hour=0), due=dt(hour=9)),
                  op('b', order_id='o2')])
        result = schedule_setup_stages(s, contract, ('job:a', 'job:b'))
        m = sequence_metrics(s, result)
        self.assertEqual(m.late_orders, 0)


class SupplierMilestoneTests(unittest.TestCase):
    def _s(self, kind='FS', lag=0):
        from planning_factory.domain.models import Operation
        ms = Operation('sup', 'order', 0, (), external_end=dt(day=20, hour=9))
        return base([ms, op('a', predecessors=(Dependency('sup', kind, lag),))])

    def _contract(self):
        data = spec()
        data['phases'] = [data['phases'][0]]
        return load_setup_contract(json.dumps(data))

    def test_run_waits_for_supplier_end_plus_lag(self):
        s, c = self._s(lag=3600), self._contract()
        result = schedule_setup_stages(s, c, ('job:a',))
        run = next(b for b in result.bookings if b.stage == 'run')
        self.assertGreaterEqual(run.segments[0].start, dt(day=20, hour=10))
        self.assertEqual({b.phase_id for b in result.bookings}, {'job:a'})
        self.assertEqual(audit_stage_capacity(c, s, ('job:a',), result.bookings), ())

    def test_setup_may_precede_supplier_return(self):
        s, c = self._s(), self._contract()
        result = schedule_setup_stages(s, c, ('job:a',))
        setup = next(b for b in result.bookings if b.stage == 'setup')
        self.assertLess(setup.segments[0].start, dt(day=20, hour=9))

    def test_audit_rejects_run_before_supplier_end(self):
        s, c = self._s(), self._contract()
        result = schedule_setup_stages(s, c, ('job:a',))
        early = tuple(
            StageBooking(b.phase_id, b.stage,
                         (Interval(dt(day=19, hour=9), dt(day=19, hour=10)),), b.allocations)
            if b.stage == 'run' else b for b in result.bookings)
        errors = audit_stage_capacity(c, s, ('job:a',), early)
        self.assertTrue(any('supplier milestone' in e for e in errors))

    def test_ss_from_supplier_fails_closed(self):
        with self.assertRaisesRegex(ValidationError, 'SS from supplier'):
            schedule_setup_stages(self._s(kind='SS'), self._contract(), ('job:a',))

    def test_search_with_supplier_milestone_and_kpis(self):
        from planning_factory.engine.setup_sequence_search import (
            default_machine_sequence, optimize_setup_sequence)
        s, c = self._s(), self._contract()
        self.assertEqual(default_machine_sequence(s), ('job:a',))
        search = optimize_setup_sequence(s, c, objective='delivery')
        self.assertEqual(search.sequence, ('job:a',))


if __name__ == '__main__':
    unittest.main()
