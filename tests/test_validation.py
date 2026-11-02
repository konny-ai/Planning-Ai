from dataclasses import replace
from datetime import time
import json
import unittest
from planning_factory.adapters.json_io import dumps, loads
from planning_factory.domain.models import Calendar, Dependency, Nesting, Settings, Shift
from planning_factory.domain.validation import ValidationError, compile_tasks
from planning_factory.domain.durations import remaining_seconds
from .helpers import dt, op, scenario


class ValidationTests(unittest.TestCase):
    def reject(self, s):
        with self.assertRaises(ValidationError):
            compile_tasks(s)

    def test_duplicate_operation(self):
        self.reject(scenario([op(), op()]))

    def test_missing_dependency(self):
        self.reject(scenario([op(predecessors=(Dependency('missing'),))]))

    def test_self_dependency(self):
        self.reject(scenario([op(predecessors=(Dependency('a'),))]))

    def test_cycle(self):
        self.reject(scenario([op('a', predecessors=(Dependency('b'),)), op('b', predecessors=(Dependency('a'),))]))

    def test_collapsed_nesting_cycle(self):
        self.reject(scenario([op('a'), op('b', predecessors=(Dependency('a'),)), op('c', predecessors=(Dependency('b'),))],
                             nestings=(Nesting('n', ('a', 'c'), 3600),)))

    def test_internal_nesting_dependency(self):
        self.reject(scenario([op('a'), op('b', predecessors=(Dependency('a'),))], nestings=(Nesting('n', ('a', 'b'), 3600),)))

    def test_member_in_two_nestings(self):
        self.reject(scenario([op()], nestings=(Nesting('n1', ('a',), 1), Nesting('n2', ('a',), 1))))

    def test_incompatible_nesting_resources(self):
        from planning_factory.domain.models import Resource
        self.reject(scenario([op('a'), op('b', resource='n')], resources=(Resource('m', 'work'), Resource('n', 'work')),
                             nestings=(Nesting('n', ('a', 'b'), 3600),)))

    def test_incompatible_nesting_modes(self):
        self.reject(scenario([op('a'), op('b', mode='backward')], nestings=(Nesting('n', ('a', 'b'), 3600),)))

    def test_missing_resource(self):
        self.reject(scenario([op(resource='unknown')]))

    def test_timezone_required(self):
        self.reject(scenario([op()], start=dt().replace(tzinfo=None)))

    def test_fractional_seconds_rejected(self):
        self.reject(scenario([op(release=dt().replace(microsecond=1))]))

    def test_negative_duration(self):
        self.reject(scenario([replace(op(), duration_seconds=-1)]))

    def test_bool_not_integer_duration(self):
        self.reject(scenario([replace(op(), duration_seconds=True)]))

    def test_negative_weights(self):
        self.reject(scenario([op()], settings=Settings(due_weight=-1)))

    def test_invalid_horizon(self):
        self.reject(scenario([op()], end=dt(18)))

    def test_unknown_timezone(self):
        self.reject(scenario([op()], calendars=(Calendar('work', 'No/SuchPlace', (Shift(0, time(8), time(17)),)),)))

    def test_zero_duration_pending_rejected(self):
        self.reject(scenario([op(hours=0)]))

    def test_completed_in_future_rejected(self):
        self.reject(scenario([op(hours=0, completed_at=dt(20))]))

    def test_fixed_alternative_index(self):
        self.reject(scenario([op(fixed_start=dt(), fixed_alternative=10)]))

    def test_unknown_json_fields(self):
        value = json.loads(dumps(scenario([op()])))
        value['operations'][0]['magical_optimizer'] = True
        with self.assertRaises(ValidationError):
            loads(json.dumps(value))

    def test_bad_json_types_and_dates(self):
        for field, value in [('duration_seconds', '3600'), ('duration_seconds', 1.5), ('release', '2026-10-19'),
                             ('release', '2026-02-31T08:00:00Z'), ('splittable', 'false')]:
            data = json.loads(dumps(scenario([op()])))
            data['operations'][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                loads(json.dumps(data))

    def test_duplicate_keys_rejected(self):
        with self.assertRaises(ValidationError):
            loads('{"id":"a","id":"b"}')

    def test_unknown_schema_version(self):
        self.reject(replace(scenario([op()]), schema_version=2))

    def test_nonfinite_json(self):
        with self.assertRaises(ValidationError):
            loads('{"duration_seconds": NaN}')


class DurationTests(unittest.TestCase):
    def test_quantity_progress(self):
        self.assertEqual(remaining_seconds('12.5', 10, 60, produced_quantity=3), (88, 60))

    def test_time_progress_and_setup(self):
        self.assertEqual(remaining_seconds(20, 10, 60, recorded_work_seconds=80, recorded_setup_seconds=10, policy='time'), (120, 50))

    def test_no_progress(self):
        self.assertEqual(remaining_seconds(20, 10, 60, recorded_work_seconds=80, policy='none'), (200, 60))

    def test_clamp_to_zero(self):
        self.assertEqual(remaining_seconds(10, 1, 5, produced_quantity=2, recorded_setup_seconds=10), (0, 0))

    def test_reject_bad_values(self):
        for v in ['NaN', '-1', 'Infinity', 'hello']:
            with self.assertRaises(ValidationError):
                remaining_seconds(v, 10)
