from dataclasses import replace
from datetime import UTC, date, datetime, time
import unittest
from planning_factory.domain.models import Calendar, CalendarException, Interval, Shift
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.calendars import expand, intersect, merge, subtract
from planning_factory.engine.scheduler import plan
from .helpers import dt, op, scenario


class CalendarTests(unittest.TestCase):
    def test_weekday_holiday(self):
        s = scenario([op()])
        s = replace(s, calendars=(replace(s.calendars[0], holidays=(date(2026, 10, 19),)),))
        self.assertEqual(plan(s).assignments[0].start, dt(20))

    def test_exception_replaces_day(self):
        s = scenario([op()])
        ex = CalendarException(date(2026, 10, 19), ((time(10), time(12)),))
        s = replace(s, calendars=(replace(s.calendars[0], exceptions=(ex,)),))
        self.assertEqual(plan(s).assignments[0].start, dt(hour=10))

    def test_empty_exception_closes_day(self):
        s = scenario([op()])
        s = replace(s, calendars=(replace(s.calendars[0], exceptions=(CalendarException(date(2026, 10, 19)),)),))
        self.assertEqual(plan(s).assignments[0].start, dt(20))

    def test_closure_maintenance(self):
        s = scenario([op()])
        s = replace(s, calendars=(replace(s.calendars[0], closures=(Interval(dt(), dt(hour=11)),)),))
        self.assertEqual(plan(s).assignments[0].start, dt(hour=11))

    def test_night_carry_over(self):
        c = Calendar('night', 'UTC', (Shift(6, time(22), time(6)),))
        intervals = expand(c, dt(hour=0), dt(20, 0))
        self.assertEqual(intervals, (Interval(dt(hour=0), dt(hour=6)),))

    def test_holiday_cuts_previous_night(self):
        c = Calendar('night', 'UTC', (Shift(6, time(22), time(6)),), holidays=(date(2026, 10, 19),))
        self.assertEqual(expand(c, dt(hour=0), dt(20, 0)), ())

    def test_intersect_machine_operator_calendars(self):
        from planning_factory.domain.models import Resource
        from .helpers import alt
        c1 = Calendar('a', 'UTC', (Shift(0, time(8), time(12)),))
        c2 = Calendar('b', 'UTC', (Shift(0, time(10), time(14)),))
        s = scenario([op(alternatives=(alt('m', 'p'),))], calendars=(c1, c2), resources=(Resource('m', 'a'), Resource('p', 'b')))
        self.assertEqual(plan(s).assignments[0].start, dt(hour=10))

    def test_dst_fall_elapsed_night(self):
        c = Calendar('night', 'Europe/Rome', (Shift(5, time(22), time(6)),))
        intervals = expand(c, dt(24, 0), dt(26, 0))
        self.assertEqual(sum(i.seconds for i in intervals), 9 * 3600)

    def test_dst_spring_elapsed_night(self):
        c = Calendar('night', 'Europe/Rome', (Shift(5, time(22), time(6)),))
        intervals = expand(c, dt(28, 0, month=3), dt(30, 0, month=3))
        self.assertEqual(sum(i.seconds for i in intervals), 7 * 3600)

    def test_nonexistent_endpoint_rejected(self):
        c = Calendar('night', 'Europe/Rome', (Shift(6, time(2, 30), time(6)),))
        with self.assertRaisesRegex(ValidationError, 'nonexistent'):
            expand(c, dt(29, 0, month=3), dt(30, 0, month=3))

    def test_ambiguous_endpoint_rejected(self):
        c = Calendar('night', 'Europe/Rome', (Shift(6, time(2, 30), time(6)),))
        with self.assertRaisesRegex(ValidationError, 'ambiguous'):
            expand(c, dt(25, 0), dt(26, 0))

    def test_after_2038_supported(self):
        c = Calendar('all', 'UTC', tuple(Shift(d, time(0), time(0)) for d in range(7)))
        self.assertEqual(sum(i.seconds for i in expand(c, dt(19, 0, year=2040), dt(20, 0, year=2040))), 86400)

    def test_overlaps_merged(self):
        c = Calendar('c', 'UTC', (Shift(0, time(8), time(12)), Shift(0, time(10), time(14))))
        self.assertEqual(expand(c, dt(hour=0), dt(20, 0)), (Interval(dt(), dt(hour=14)),))

    def test_interval_algebra(self):
        a = (Interval(dt(), dt(hour=12)), Interval(dt(hour=13), dt(hour=17)))
        cut = (Interval(dt(hour=11), dt(hour=14)),)
        self.assertEqual(intersect(a, cut), (Interval(dt(hour=11), dt(hour=12)), Interval(dt(hour=13), dt(hour=14))))
        self.assertEqual(subtract(a, cut), (Interval(dt(), dt(hour=11)), Interval(dt(hour=14), dt(hour=17))))
