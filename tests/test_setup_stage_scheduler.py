"""Real two-stage setup/run planning using capacity-scaled canonical resources."""
import json
import unittest
from datetime import timedelta

from planning_factory.domain.models import Dependency, Interval, Resource, Nesting
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_stage_scheduler import schedule_setup_stages
from planning_factory.engine.setup_capacity import StageAllocation, StageBooking, audit_stage_capacity
from planning_factory.engine.verification import verify_plan
from .helpers import alt, dt, op, scenario


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

    def test_ss_between_two_milestones_is_ignored_while_ss_into_machine_work_still_fails(self):
        # Mutation testing showed no test covered a milestone-to-milestone edge: it orders nothing on
        # machines, so it must not trigger the "SS from supplier" refusal that guards real work.
        from planning_factory.domain.models import Operation
        sup1 = Operation('sup1', 'order', 0, (), external_end=dt(day=20, hour=9))
        sup2 = Operation('sup2', 'order', 0, (), external_end=dt(day=21, hour=9),
                         predecessors=(Dependency('sup1', 'SS', 3600),))
        s = base([sup1, sup2, op('a', predecessors=(Dependency('sup2', 'FS', 3600),))])
        result = schedule_setup_stages(s, self._contract(), ('job:a',))
        run = next(b for b in result.bookings if b.stage == 'run')
        self.assertGreaterEqual(run.segments[0].start, dt(day=21, hour=10))      # the FS floor of sup2 + lag
        self.assertEqual(audit_stage_capacity(self._contract(), s, ('job:a',), result.bookings), ())
        refused = base([sup1, op('a', predecessors=(Dependency('sup1', 'SS'),))])
        with self.assertRaisesRegex(ValidationError, 'SS from supplier'):
            schedule_setup_stages(refused, self._contract(), ('job:a',))

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


class FixedStartTests(unittest.TestCase):
    def _c(self):
        data = spec()
        data['phases'] = [data['phases'][0]]
        return load_setup_contract(json.dumps(data))

    def test_run_is_pinned_and_setup_finishes_before_it(self):
        s = base([op('a', fixed_start=dt(day=19, hour=10))])
        c = self._c()
        result = schedule_setup_stages(s, c, ('job:a',))
        run = next(b for b in result.bookings if b.stage == 'run')
        setup = next(b for b in result.bookings if b.stage == 'setup')
        self.assertEqual(run.segments[0].start, dt(day=19, hour=10))
        self.assertLessEqual(setup.segments[-1].end, run.segments[0].start)
        self.assertEqual(audit_stage_capacity(c, s, ('job:a',), result.bookings), ())

    def test_infeasible_pin_fails_closed(self):
        # Fixed start at 8:00 leaves no room for the 30 min setup before it.
        s = base([op('a', fixed_start=dt(day=19, hour=8))])
        with self.assertRaises(ValidationError):
            schedule_setup_stages(s, self._c(), ('job:a',))

    def test_audit_rejects_moved_run(self):
        s = base([op('a', fixed_start=dt(day=19, hour=10))])
        c = self._c()
        result = schedule_setup_stages(s, c, ('job:a',))
        moved = tuple(
            StageBooking(b.phase_id, b.stage,
                         (Interval(dt(day=19, hour=11), dt(day=19, hour=12)),), b.allocations)
            if b.stage == 'run' else b for b in result.bookings)
        self.assertTrue(any('fixed_start' in e for e in
                            audit_stage_capacity(c, s, ('job:a',), moved)))

    def test_fixed_alternative_restricts_eligible_machines(self):
        # The fixed alternative narrows the run to machine m2; a contract
        # that names machine m is therefore rejected, one naming m2 is planned.
        s = scenario([op('a', resource='m', fixed_start=dt(day=19, hour=10),
                         alternatives=(alt('m'), alt('m2')), fixed_alternative=1)],
                     resources=(Resource('m', 'work'), Resource('m2', 'work'),
                                Resource('op', 'work', kind='operator'),
                                Resource('tool', 'work', kind='tool')))
        with self.assertRaisesRegex(ValidationError, 'not an eligible run machine'):
            schedule_setup_stages(s, self._c(), ('job:a',))
        data = spec()
        data['phases'] = [dict(data['phases'][0], machine_id='m2')]
        for key in ('setup_requirements', 'run_requirements'):
            data['phases'][0][key][0]['candidate_ids'] = ['m2']
        data['resources'].append({'id': 'm2', 'kind': 'machine'})
        data['initial_machine_states']['m2'] = 'RED'
        result = schedule_setup_stages(s, load_setup_contract(json.dumps(data)), ('job:a',))
        run = next(b for b in result.bookings if b.stage == 'run')
        self.assertEqual(run.segments[0].start, dt(day=19, hour=10))

class CompletedPredecessorLagTests(unittest.TestCase):
    """A lag from a completed predecessor still binds after its edge leaves the stage scenario
    (found by code review: the staged plan started the successor a day too early)."""

    def _contract(self):
        data = spec()
        data['phases'] = [dict(data['phases'][0], id='job:a'), dict(data['phases'][1], id='job:b')]
        return load_setup_contract(json.dumps(data))

    def test_run_waits_for_completion_plus_lag_like_the_standard_planner(self):
        from planning_factory.engine.scheduler import plan
        c = self._contract()
        for kind in ('FS', 'SS'):
            lag = 20 * 3600
            ops = lambda: [op('a', hours=0, completed_at=dt(hour=0)),
                           op('b', predecessors=(Dependency('a', kind, lag),))]
            s = base(ops())
            standard = next(a for a in plan(scenario(ops())).assignments if a.task_id == 'job:b')
            result = schedule_setup_stages(s, c, ('job:a', 'job:b'))
            run = next(b for b in result.bookings if b.phase_id == 'job:b' and b.stage == 'run')
            with self.subTest(kind=kind):
                self.assertEqual(run.segments[0].start, standard.segments[0].start)
                self.assertGreaterEqual(run.segments[0].start, dt(hour=0) + timedelta(seconds=lag))
                self.assertEqual(audit_stage_capacity(c, s, ('job:a', 'job:b'), result.bookings), ())

    def test_audit_rejects_a_run_that_ignores_the_lag(self):
        c = self._contract()
        with_lag = base([op('a', hours=0, completed_at=dt(hour=0)),
                         op('b', predecessors=(Dependency('a', 'FS', 20 * 3600),))])
        without_lag = base([op('a', hours=0, completed_at=dt(hour=0)),
                            op('b', predecessors=(Dependency('a', 'FS', 0),))])
        early = schedule_setup_stages(without_lag, c, ('job:a', 'job:b'))      # starts on day 19
        errors = audit_stage_capacity(c, with_lag, ('job:a', 'job:b'), early.bookings)
        self.assertTrue(any('completed/supplier milestone end plus lag' in e for e in errors), errors)

    def test_zero_lag_and_later_completion_are_unchanged(self):
        c = self._contract()
        s = base([op('a', hours=0, completed_at=dt(hour=0)), op('b', predecessors=(Dependency('a', 'FS'),))])
        run = next(b for b in schedule_setup_stages(s, c, ('job:a', 'job:b')).bookings
                   if b.phase_id == 'job:b' and b.stage == 'run')
        self.assertEqual(run.segments[0].start, dt(hour=8))

if __name__ == '__main__':
    unittest.main()
