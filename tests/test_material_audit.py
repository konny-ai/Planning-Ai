"""Cases for independent material audit (authored; not executed in this PR)."""
from dataclasses import replace
from decimal import Decimal
import unittest

from planning_factory.domain.material_audit import audit_material_pegging
from planning_factory.domain.production_flow import (
    MaterialSupply, MaterialNeed, MaterialPeg, MaterialAvailability,
    allocate_materials,
)
from .helpers import dt, op, scenario


class MaterialAuditTests(unittest.TestCase):
    def setUp(self):
        self.scenario = scenario([op('a'), op('b')])
        self.supplies = (
            MaterialSupply('stock', 'S', Decimal('4'), dt(19, 7)),
            MaterialSupply('purchase', 'S', Decimal('3'), dt(20, 10)),
        )
        self.needs = (
            MaterialNeed('job:a', 'S', Decimal('5')),
            MaterialNeed('job:b', 'S', Decimal('2')),
        )
        self.report = allocate_materials(
            self.scenario, self.supplies, self.needs, ('job:a', 'job:b'))

    def audit(self, report):
        return audit_material_pegging(self.scenario, self.supplies, self.needs, report)

    def test_audits_independent_result_without_mutating_sources(self):
        self.assertEqual(self.audit(self.report), ())
        self.assertEqual(self.scenario.operations[0].release, None)

    def test_stale_material_snapshot_is_rejected(self):
        stale = replace(self.report, source_scenario_digest='not-this-scenario')
        findings = self.audit(stale)
        self.assertTrue(any('another scenario revision' in x for x in findings))

    def test_detects_supply_overdraw_and_incomplete_task(self):
        excess = replace(self.report, pegs=self.report.pegs + (
            MaterialPeg('job:b', 'S', 'stock', Decimal('1')),))
        issues = self.audit(excess)
        self.assertTrue(any('supply overdrawn' in issue for issue in issues))
        self.assertTrue(any('incomplete or excess pegging' in issue for issue in issues))

    def test_detects_forged_release_and_material_mismatch(self):
        forged = replace(self.report, releases=(
            ('job:a', dt(19, 0)), *self.report.releases[1:]))
        self.assertTrue(any('release does not match' in x for x in self.audit(forged)))
        wrong = replace(self.report, pegs=(
            replace(self.report.pegs[0], item_id='OTHER'), *self.report.pegs[1:]))
        self.assertTrue(any('wrong material' in x for x in self.audit(wrong)))

    def test_external_reservations_cannot_be_double_allocated(self):
        findings = audit_material_pegging(
            self.scenario, self.supplies, self.needs, self.report,
            committed_by_supply={'stock': Decimal('1')})
        self.assertTrue(any('overdrawn after external commitments' in x
                            for x in findings))
        unknown = audit_material_pegging(
            self.scenario, self.supplies, self.needs, self.report,
            committed_by_supply={'missing': Decimal('1')})
        self.assertTrue(any('unknown supply' in x for x in unknown))

    def test_detects_phantom_release_and_shortage_pegging(self):
        phantom = replace(self.report, releases=(
            *self.report.releases, ('job:phantom', dt(20, 10))))
        self.assertTrue(any('unknown release' in x for x in self.audit(phantom)))
        shortage = MaterialAvailability(
            (('job:b', dt(19, 0)),),
            (MaterialPeg('job:a', 'S', 'stock', Decimal('1')),),
            (('job:a', 'S', Decimal('4')),),
            self.report.source_scenario_digest,
        )
        findings = self.audit(shortage)
        self.assertTrue(any('shortage task has committed pegs' in x for x in findings))


if __name__ == '__main__':
    unittest.main()
