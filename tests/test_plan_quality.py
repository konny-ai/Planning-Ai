"""Audited plan quality KPIs and deterministic baseline acceptance contract."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from planning_factory.adapters.json_io import dumps
from planning_factory.domain.models import Nesting, Resource
from planning_factory.engine.quality import assess_quality
from planning_factory.engine.scheduler import plan
from tools.plan_quality import main
from .helpers import dt, op, scenario


class PlanQualityTests(unittest.TestCase):
    def test_service_level_uses_only_complete_orders_with_due(self):
        s = scenario([
            op('a', order_id='A', due=dt(hour=12), fixed_start=dt(hour=8)),
            op('b', order_id='B', due=dt(hour=9)),
            op('c', order_id='C'),
        ])
        report = assess_quality(s, plan(s))
        self.assertEqual(report['orders']['total'], 3)
        self.assertEqual(report['orders']['complete'], 3)
        self.assertEqual(report['orders']['complete_with_due'], 2)
        self.assertEqual(report['orders']['complete_on_time'], 1)
        self.assertEqual(report['orders']['complete_late'], 1)
        self.assertEqual(report['orders']['service_level_percent'], 50.0)
        self.assertEqual(report['orders']['total_tardiness_seconds'], 3600)

    def test_due_date_cohorts_separate_past_promises_from_future_service(self):
        s = scenario([
            op('past', order_id='past', fixed_start=dt(hour=8), due=dt(18, 17)),
            op('today', order_id='today', fixed_start=dt(hour=9), due=dt(hour=10)),
            op('future', order_id='future', fixed_start=dt(hour=10), due=dt(25, 10)),
        ])
        report = assess_quality(s, plan(s))['orders']
        self.assertEqual(report['complete_due_before_horizon'], 1)
        self.assertEqual(report['complete_due_during_horizon'], 1)
        self.assertEqual(report['complete_due_after_horizon'], 1)
        self.assertEqual(report['complete_due_from_start'], 2)
        self.assertEqual(report['complete_on_time_due_from_start'], 2)
        self.assertEqual(report['service_level_percent'], 66.67)
        self.assertEqual(report['service_level_due_from_start_percent'], 100.0)

    def test_one_shared_nesting_counts_two_operations_one_machine_booking(self):
        s = scenario([op('a', order_id='A'), op('b', order_id='B')],
                     nestings=(Nesting('shared', ('a', 'b'), 3600),))
        p = plan(s)
        report = assess_quality(s, p)
        self.assertEqual(report['scheduled_tasks'], 1)
        self.assertEqual(report['scheduled_operations'], 2)
        self.assertEqual(report['resource_load']['by_kind']['machine']['booked_unit_seconds'], 3600)
        self.assertEqual(report['orders']['total'], 2)

    def test_static_setup_counts_nesting_once_not_member_setups(self):
        s = scenario([
            op('a', order_id='A', setup_seconds=1800),
            op('b', order_id='B', setup_seconds=1200),
            op('c', order_id='C', setup_seconds=600),
        ], nestings=(Nesting('shared', ('a', 'b'), 3600, setup_seconds=900),))
        report = assess_quality(s, plan(s))
        coverage = report['static_setup_coverage']
        self.assertEqual(coverage['booked_tasks_with_static_setup'], 2)
        self.assertEqual(coverage['booked_nestings_with_static_setup'], 1)
        self.assertEqual(coverage['nominal_static_setup_seconds'], 1500)
        self.assertFalse(coverage['sequence_dependent_setup_assessed'])

    def test_unscheduled_setup_is_not_counted_as_booked(self):
        s = scenario([op('a', setup_seconds=0),
                      op('b', hours=100, setup_seconds=1200)])
        coverage = assess_quality(s, plan(s))['static_setup_coverage']
        self.assertEqual(coverage['booked_tasks_with_static_setup'], 0)
        self.assertEqual(coverage['nominal_static_setup_seconds'], 0)

    def test_incomplete_orders_are_not_misclassified_as_on_time(self):
        s = scenario([op('a', hours=100, due=dt(hour=12))])
        report = assess_quality(s, plan(s))
        self.assertEqual(report['unscheduled_operations'], 1)
        self.assertEqual(report['orders']['complete'], 0)
        self.assertEqual(report['orders']['incomplete'], 1)
        self.assertEqual(report['orders']['complete_with_due'], 0)
        self.assertIsNone(report['orders']['service_level_percent'])
        self.assertEqual(sum(report['unscheduled_by_code'].values()), 1)

    def test_infinite_capacity_excluded_from_utilization_denominator(self):
        s = scenario([op('a')], resources=(Resource('m', 'work', None),))
        report = assess_quality(s, plan(s))
        machine = report['resource_load']['by_kind']['machine']
        self.assertEqual(machine['booked_unit_seconds'], 3600)
        self.assertEqual(machine['finite_resources'], 0)
        self.assertIsNone(machine['weighted_utilization_percent'])
        self.assertEqual(report['resource_load']['highest_utilization_finite'], [])

    def test_independent_audit_rejects_tampered_plan(self):
        s = scenario([op('a')])
        p = replace(plan(s), scenario_digest='incorrect')
        with self.assertRaises(ValueError):
            assess_quality(s, p)

    def test_cli_report_is_reproducible_and_cannot_overwrite_plan(self):
        s = scenario([op('a', order_id='A', due=dt(hour=14))])
        p = plan(s)
        with TemporaryDirectory() as root:
            src, file, dest = (Path(root) / f for f in ('scenario.json', 'plan.json', 'quality.json'))
            src.write_text(dumps(s), encoding='utf-8')
            file.write_text(dumps(p), encoding='utf-8')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                status = main([str(src), str(file), '--out', str(dest), '--check-reproduction'])
            self.assertEqual(status, 0)
            report = json.loads(dest.read_text(encoding='utf-8'))
            self.assertTrue(report['baseline_reproduction']['identical'])
            self.assertEqual(report['scheduled_operations'], 1)
            before = file.read_text(encoding='utf-8')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()), self.assertRaises(SystemExit):
                main([str(src), str(file), '--out', str(file)])
            self.assertEqual(file.read_text(encoding='utf-8'), before)


if __name__ == '__main__':
    unittest.main()
