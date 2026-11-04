"""ADR 006 option B: plan the plannable, report the rest explicitly."""
import json
import unittest

from planning_factory.domain.models import Dependency, Nesting, Operation
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_capacity import audit_stage_capacity
from planning_factory.engine.setup_partial import (
    NEST_MEMBER_UNPLANNED, NO_CANDIDATE, PREDECESSOR_UNPLANNED, plan_setup_stages_partial,
)
from planning_factory.engine.setup_stage_scheduler import schedule_setup_stages
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op
from .test_setup_stage_scheduler import base, spec


def contract(*names, extra=()):
    data = spec()
    template = data['phases'][0]
    data['phases'] = [dict(template, id=f'job:{n}') for n in (*names, *extra)]
    return load_setup_contract(json.dumps(data))


class PartialPlanningTests(unittest.TestCase):
    def scenario(self):
        return base([op('a'), Operation('x', 'order', 3600, ()),
                     op('y', predecessors=(Dependency('x', 'FS'),)), op('b')])

    def test_plannable_tasks_are_planned_and_the_rest_is_reported(self):
        s = self.scenario()
        c = contract('a', 'b', extra=('y',))          # x has no phase at all
        result = plan_setup_stages_partial(s, c, ('job:a', 'job:y', 'job:b'))
        self.assertEqual({u.operation_id: u.code for u in result.unplanned},
                         {'x': NO_CANDIDATE, 'y': PREDECESSOR_UNPLANNED})
        self.assertEqual({b.phase_id for b in result.staged.bookings}, {'job:a', 'job:b'})
        self.assertEqual(result.machine_sequence, ('job:a', 'job:b'))
        self.assertEqual(verify_plan(result.staged.scenario, result.staged.plan), ())
        self.assertEqual(audit_stage_capacity(result.contract, result.scenario,
                                              result.machine_sequence, result.staged.bookings), ())
        # inputs untouched, strict planner still fails closed
        self.assertEqual(len(s.operations), 4)
        with self.assertRaises(ValidationError):
            schedule_setup_stages(s, c, ('job:a', 'job:y', 'job:b'))

    def test_nesting_with_an_unplanned_member_is_not_planned(self):
        s = base([op('a'), Operation('x', 'order', 3600, ()), op('m')],
                 nestings=(Nesting('n', ('x', 'm'), 3600),))
        data = spec()
        data['phases'] = [dict(data['phases'][0], id='job:a')]
        result = plan_setup_stages_partial(s, load_setup_contract(json.dumps(data)), ('job:a',))
        self.assertEqual({u.operation_id: u.code for u in result.unplanned},
                         {'x': NO_CANDIDATE, 'm': NEST_MEMBER_UNPLANNED})
        self.assertEqual({b.phase_id for b in result.staged.bookings}, {'job:a'})

    def test_extra_operations_and_nothing_plannable(self):
        s = base([op('a'), op('b')])
        result = plan_setup_stages_partial(s, contract('a', 'b'), ('job:a', 'job:b'),
                                           extra_without_candidates=('b',))
        self.assertEqual([(u.operation_id, u.code) for u in result.unplanned], [('b', NO_CANDIDATE)])
        self.assertEqual({b.phase_id for b in result.staged.bookings}, {'job:a'})
        none = plan_setup_stages_partial(s, contract('a', 'b'), ('job:a', 'job:b'),
                                         extra_without_candidates=('a', 'b'))
        self.assertIsNone(none.staged)
        self.assertEqual(len(none.unplanned), 2)
        with self.assertRaisesRegex(ValidationError, 'unknown operation'):
            plan_setup_stages_partial(s, contract('a', 'b'), ('job:a', 'job:b'),
                                      extra_without_candidates=('zzz',))

    def test_without_gaps_it_equals_the_strict_plan_and_backward_works(self):
        s = base([op('a', due=dt(day=23, hour=17)), op('b', due=dt(day=23, hour=17))])
        c = contract('a', 'b')
        seq = ('job:a', 'job:b')
        partial = plan_setup_stages_partial(s, c, seq)
        self.assertEqual(partial.unplanned, ())
        self.assertEqual(partial.staged.bookings, schedule_setup_stages(s, c, seq).bookings)
        back = plan_setup_stages_partial(s, c, seq, backward=True)
        self.assertIsNotNone(back.backward)
        self.assertEqual(audit_stage_capacity(back.contract, back.scenario, back.machine_sequence,
                                              back.staged.bookings), ())

    def test_frozen_booking_of_an_unplanned_task_is_rejected(self):
        from planning_factory.domain.models import Interval
        from planning_factory.engine.setup_capacity import StageAllocation, StageBooking
        s = self.scenario()
        frozen = StageBooking('job:y', 'run', (Interval(dt(hour=8), dt(hour=9)),),
                              (StageAllocation('m', 10000),))
        with self.assertRaisesRegex(ValidationError, 'not planned'):
            plan_setup_stages_partial(s, contract('a', 'b', extra=('y',)),
                                      ('job:a', 'job:y', 'job:b'), frozen_bookings=(frozen,))


if __name__ == '__main__':
    unittest.main()


class DigestMemoTests(unittest.TestCase):
    def test_digest_is_memoized_per_object_and_never_stale(self):
        from dataclasses import replace
        from planning_factory.adapters.json_io import dumps, scenario_digest
        import hashlib
        s = base([op('a')])
        first = scenario_digest(s)
        self.assertEqual(first, hashlib.sha256(dumps(s).encode('utf-8')).hexdigest())
        self.assertEqual(first, scenario_digest(s))
        changed = replace(s, id='other')           # a different object: a different digest
        self.assertNotEqual(first, scenario_digest(changed))
        for i in range(40):                         # eviction keeps results exact
            other = replace(s, id=f'x{i}')
            self.assertEqual(scenario_digest(other),
                             hashlib.sha256(dumps(other).encode('utf-8')).hexdigest())
        self.assertEqual(first, scenario_digest(s))
