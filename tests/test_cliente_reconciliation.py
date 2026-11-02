from dataclasses import replace
import unittest

from planning_factory.adapters.cliente_reconciliation import reconcile_nestings
from planning_factory.domain.models import Nesting
from planning_factory.domain.validation import ValidationError
from .helpers import op, scenario


class ClienteReconciliationTests(unittest.TestCase):
    def setUp(self):
        self.source = scenario((op('A/1/cut'), op('B/1/cut')))
        self.programs = [{'workplanId': '1', 'cutSec': '3600'}]
        self.members = [{'workplanId': '1', 'orderId': a, 'lotId': '1', 'phaseId': 'cut'} for a in ('A', 'B')]

    def test_included_shared_program_and_digest(self):
        source = replace(self.source, nestings=(Nesting('NEST-1', ('A/1/cut', 'B/1/cut'), 3600),))
        report = reconcile_nestings(source, self.programs, self.members)
        self.assertEqual({'included': 1}, report['counts'])
        self.assertEqual(0, report['review_count'])
        self.assertFalse(report['production_approved'])

    def test_exclusion_never_disappears_behind_a_complete_plan(self):
        report = reconcile_nestings(self.source, self.programs, self.members)
        self.assertEqual('unexplained_exclusion', report['rows'][0]['reason'])
        self.assertEqual(1, report['review_count'])

    def test_partial_membership_and_orphan_are_reported(self):
        source = replace(self.source, nestings=(Nesting('NEST-1', ('A/1/cut',), 3600),
                                               Nesting('NEST-other', ('B/1/cut',), 3600)))
        report = reconcile_nestings(source, self.programs, self.members)
        self.assertEqual('membership_mismatch', report['rows'][0]['reason'])
        self.assertEqual(['NEST-other'], report['orphan_scenario_nestings'])
        self.assertEqual({'B/1/cut': 'NEST-other'}, report['rows'][0]['members_in_other_nestings'])

    def test_program_whose_members_belong_to_another_nesting_is_explained(self):
        source = replace(self.source, nestings=(Nesting('NEST-1', ('A/1/cut', 'B/1/cut'), 3600),))
        programs = self.programs + [{'workplanId': '2', 'cutSec': '1800'}]
        members = self.members + [{'workplanId': '2', 'orderId': 'B', 'lotId': '1', 'phaseId': 'cut'}]
        report = reconcile_nestings(source, programs, members)
        second = report['rows'][1]
        self.assertEqual('members_in_other_nesting', second['reason'])
        self.assertEqual({'B/1/cut': 'NEST-1'}, second['members_in_other_nestings'])
        self.assertTrue(second['requires_review'])
        self.assertEqual({'included': 1, 'members_in_other_nesting': 1}, report['counts'])

    def test_outside_members_and_zero_cut_program_remain_visible(self):
        report = reconcile_nestings(scenario((op('A/1/cut'),)),
                                    [{'workplanId': '1', 'cutSec': '0'}], self.members)
        self.assertEqual(['B/1/cut'], report['rows'][0]['members_outside_scenario'])
        self.assertEqual('non_cut_zero_duration', report['rows'][0]['reason'])
        self.assertEqual(1, report['review_count'])

    def test_duplicate_programs_and_nonfinite_durations_rejected(self):
        for programs in (self.programs * 2, [{'workplanId': '1', 'cutSec': 'NaN'}]):
            with self.assertRaises(ValidationError):
                reconcile_nestings(self.source, programs, self.members)
