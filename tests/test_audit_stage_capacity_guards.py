"""The independent stage audit must catch each kind of tampered plan, and accept valid boundary cases.

Found by mutation testing of audit_stage_capacity: many of its conditions were only exercised indirectly.
"""
import unittest
from dataclasses import replace
from datetime import timedelta

from planning_factory.domain.models import Interval
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_capacity import StageAllocation, StageBooking, audit_stage_capacity
from planning_factory.engine.setup_stage_scheduler import schedule_setup_stages
from .helpers import dt, op
from .test_setup_sequence_search import three
from .test_setup_stage_scheduler import base

SEQ = ('job:a', 'job:b', 'job:c')


class AuditGuardTests(unittest.TestCase):
    def setUp(self):
        self.s = base([op('a'), op('b'), op('c')])
        self.c = three()                        # a BLUE, b RED, c BLUE; the machine starts RED
        self.bookings = schedule_setup_stages(self.s, self.c, SEQ).bookings

    def audit(self, bookings, sequence=SEQ):
        return audit_stage_capacity(self.c, self.s, sequence, tuple(bookings))

    def tampered(self, phase, stage, **changes):
        return tuple(replace(b, **changes) if (b.phase_id, b.stage) == (phase, stage) else b for b in self.bookings)

    def booking(self, phase, stage):
        return next(b for b in self.bookings if (b.phase_id, b.stage) == (phase, stage))

    def has(self, errors, text):
        return any(text in e for e in errors)

    def test_the_untouched_plan_is_clean(self):
        self.assertEqual(self.audit(self.bookings), ())

    def test_wrong_total_duration_is_caught(self):
        run = self.booking('job:b', 'run')
        short = (Interval(run.segments[0].start, run.segments[-1].end - timedelta(minutes=10)),)
        self.assertTrue(self.has(self.audit(self.tampered('job:b', 'run', segments=short)),
                                 'segment duration does not equal required duration'))
        self.assertTrue(self.has(self.audit(self.tampered('job:b', 'run', segments=())),
                                 'segment duration does not equal required duration'))

    def test_segments_outside_the_horizon_are_caught(self):
        run = self.booking('job:a', 'run')
        before = (Interval(self.s.start - timedelta(hours=1), self.s.start - timedelta(hours=1) + (run.segments[0].end - run.segments[0].start)),)
        after = (Interval(self.s.end - timedelta(minutes=30), self.s.end - timedelta(minutes=30) + (run.segments[0].end - run.segments[0].start)),)
        for label, segments in (('before', before), ('after', after)):
            with self.subTest(label=label):
                self.assertTrue(self.has(self.audit(self.tampered('job:a', 'run', segments=segments)),
                                         'segments outside horizon or empty'))

    def test_overlapping_or_unordered_segments_are_caught_but_touching_ones_are_fine(self):
        run = self.booking('job:a', 'run')
        start, end = run.segments[0].start, run.segments[-1].end
        half = start + (end - start) / 2
        touching = (Interval(start, half), Interval(half, end))
        self.assertEqual(self.audit(self.tampered('job:a', 'run', segments=touching)), ())
        overlapping = (Interval(start, half + timedelta(minutes=5)), Interval(half, end))
        self.assertTrue(self.has(self.audit(self.tampered('job:a', 'run', segments=overlapping)),
                                 'segments overlap or unordered'))

    def test_invalid_allocations_are_caught_and_boundary_percentages_are_accepted(self):
        run = self.booking('job:a', 'run')
        machine, operator = run.allocations
        bad = {'duplicate resource': run.allocations + (machine,),
               'unknown resource': run.allocations + (StageAllocation('ghost', 5000),),
               'zero percent': (machine, StageAllocation(operator.resource_id, 0)),
               'above 100%': (machine, StageAllocation(operator.resource_id, 10001)),
               'not an integer': (machine, StageAllocation(operator.resource_id, 25.5))}
        for label, allocations in bad.items():
            with self.subTest(label=label):
                self.assertTrue(self.has(self.audit(self.tampered('job:a', 'run', allocations=allocations)),
                                         'duplicate/unknown/invalid resource allocation'))
        for bps in (1, 10000):               # the legal extremes are not "invalid"; other rules may still object
            errors = self.audit(self.tampered('job:a', 'run',
                                              allocations=(machine, StageAllocation(operator.resource_id, bps))))
            self.assertFalse(self.has(errors, 'duplicate/unknown/invalid resource allocation'), bps)

    def test_a_run_without_its_machine_is_caught(self):
        run = self.booking('job:a', 'run')
        no_machine = tuple(a for a in run.allocations if a.resource_id != 'm')
        self.assertTrue(self.has(self.audit(self.tampered('job:a', 'run', allocations=no_machine)),
                                 'assigned machine must be one of the booked resources'))

    def test_unknown_phase_or_stage_in_the_bookings_is_caught_individually(self):
        run = self.booking('job:a', 'run')
        for label, extra in (('unknown phase', replace(run, phase_id='job:ghost')),
                             ('unknown stage', replace(run, stage='weld'))):
            with self.subTest(label=label):
                self.assertTrue(self.has(self.audit(self.bookings + (extra,)), 'unknown phase or stage'))

    def test_a_sequence_that_is_not_each_task_exactly_once_is_refused(self):
        for label, sequence in (('duplicate, same length', ('job:a', 'job:a', 'job:c')),
                                ('repeats one more', ('job:a', 'job:b', 'job:b', 'job:c')),
                                ('missing one', ('job:a', 'job:b'))):
            with self.subTest(label=label), self.assertRaisesRegex(ValidationError, 'each task exactly once'):
                self.audit(self.bookings, sequence)

    def test_a_setup_overlapping_the_previous_job_on_the_machine_is_caught_but_back_to_back_is_fine(self):
        previous_end = self.booking('job:a', 'run').segments[-1].end
        setup = self.booking('job:b', 'setup')
        self.assertEqual(setup.segments[0].start, previous_end)          # the valid plan is exactly back to back
        length = setup.segments[-1].end - setup.segments[0].start
        early = (Interval(previous_end - timedelta(minutes=10), previous_end - timedelta(minutes=10) + length),)
        errors = self.audit(self.tampered('job:b', 'setup', segments=early))
        self.assertTrue(self.has(errors, 'setup overlaps preceding machine job'))


    def test_unknown_phase_or_stage_in_frozen_records_is_caught_individually(self):
        run = self.booking('job:a', 'run')
        for label, record in (('unknown phase', replace(run, phase_id='job:ghost')),
                              ('unknown stage', replace(run, stage='weld'))):
            with self.subTest(label=label):
                errors = audit_stage_capacity(self.c, self.s, SEQ, self.bookings, frozen_bookings=(record,))
                self.assertTrue(self.has(errors, 'unknown frozen booking'), errors)

    def test_empty_segments_and_horizon_edges(self):
        run = self.booking('job:a', 'run')
        zero = (Interval(run.segments[0].start, run.segments[0].start),) + run.segments
        self.assertTrue(self.has(self.audit(self.tampered('job:a', 'run', segments=zero)),
                                 'segments outside horizon or empty'))            # a zero-length piece
        length = run.segments[-1].end - run.segments[0].start
        at_start = (Interval(self.s.start, self.s.start + length),)
        at_end = (Interval(self.s.end - length, self.s.end),)
        for label, segments in (('starts exactly at the horizon start', at_start),
                                ('ends exactly at the horizon end', at_end)):
            with self.subTest(label=label):      # inside the horizon: only other rules (calendar) may object
                self.assertFalse(self.has(self.audit(self.tampered('job:a', 'run', segments=segments)),
                                          'segments outside horizon or empty'))

    def test_a_predecessor_without_segments_is_reported_not_a_crash(self):
        from planning_factory.domain.models import Dependency
        s = base([op('a'), op('b', predecessors=(Dependency('a', 'FS'),)), op('c')])
        bookings = schedule_setup_stages(s, self.c, SEQ).bookings
        broken = tuple(replace(b, segments=()) if (b.phase_id, b.stage) == ('job:a', 'run') else b for b in bookings)
        errors = audit_stage_capacity(self.c, s, SEQ, broken)            # must return errors, never raise
        self.assertTrue(self.has(errors, 'segment duration does not equal required duration'))


if __name__ == '__main__':
    unittest.main()
