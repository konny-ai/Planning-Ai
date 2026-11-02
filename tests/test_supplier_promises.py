"""Supplier-promised external milestones: no booking, distinct from historical completion."""
from dataclasses import replace
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from planning_factory.adapters.json_io import dumps, loads, plan_loads
from planning_factory.adapters.factory import import_snapshot
from planning_factory.domain.models import Dependency
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op, scenario
from .test_factory_export_safety import scenario as factory_source

ROOT = Path(__file__).resolve().parents[1]


class SupplierPromiseTests(unittest.TestCase):
    def test_promised_finish_before_horizon_does_not_use_capacity(self):
        external = op('supplier', hours=0, alternatives=(), external_end=dt(18, 12),
                      order_id='external-order')
        internal = op('assembly', predecessors=(Dependency('supplier'),))
        s = scenario([external, internal])
        result = plan(s)
        a = next(x for x in result.assignments if x.task_id == 'job:supplier')
        self.assertEqual(a.external_end, dt(18, 12))
        self.assertEqual(a.start, dt(18, 12))
        self.assertEqual(a.effective_mode, 'external')
        self.assertFalse(a.segments)
        self.assertFalse(a.demands)
        self.assertIsNone(a.completed_at)
        self.assertEqual(result.resources[0].booked_unit_seconds, 3600)
        self.assertEqual(verify_plan(s, result), ())
        self.assertEqual(loads(dumps(s)), s)
        self.assertEqual(plan_loads(dumps(result)), result)

    def test_future_promise_delays_successor_without_booking(self):
        s = scenario([op('supplier', hours=0, alternatives=(), external_end=dt(20, 14)),
                      op('assembly', predecessors=(Dependency('supplier'),))])
        result = plan(s)
        assembly = next(x for x in result.assignments if x.task_id == 'job:assembly')
        self.assertEqual(assembly.start, dt(20, 14))
        self.assertEqual(verify_plan(s, result), ())

    def test_conflicting_promise_with_predecessor_is_rejected(self):
        s = scenario([op('prep'), op('supplier', hours=0, alternatives=(),
                       external_end=dt(18, 14), predecessors=(Dependency('prep'),))])
        with self.assertRaisesRegex(ValidationError, 'predecessor|precedence'):
            plan(s)

    def test_reject_external_milestone_with_resources_or_duration(self):
        bad = (
            op('x', hours=1, alternatives=(), external_end=dt(18)),
            op('x', hours=0, external_end=dt(18)),
            op('x', hours=0, alternatives=(), completed_at=dt(18), external_end=dt(18)),
            op('x', hours=0, alternatives=(), external_end=dt(18), release=dt(19)),
        )
        for value in bad:
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    plan(scenario([value]))

    def test_audit_rejects_forged_supplier_end(self):
        s = scenario([op('supplier', hours=0, alternatives=(), external_end=dt(18, 12))])
        result = plan(s)
        forged = replace(result, assignments=(replace(result.assignments[0], external_end=dt(17)),))
        self.assertTrue(any('external supplier' in x for x in verify_plan(s, forged)))

    def test_factory_converter_opt_in_and_snapshot_roundtrip(self):
        data = factory_source()
        source_op = data['operations'][0]
        source_op.update(executionCode='E', runMachines=0, runOperators=0)
        data['suppliers'] = [{'code': 'SUP-1', 'calendarId': 0}]
        data['operationResources'] = [{
            'operationId': 101, 'resourceKind': 'SUPPLIER',
            'resourceCode': 'SUP-1', 'assignmentRule': 'ALLOWED',
            'calendarId': None, 'calendarModelId': None,
        }]
        data['subcontractOrders'] = [
            {'operationId': 101, 'supplierCode': 'SUP-1', 'promisedDate': '2026-10-18T14:00:00'},
            {'operationId': 101, 'supplierCode': 'SUP-1', 'promisedDate': '2026-10-17T11:00:00'},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src.json'
            src.write_text(json.dumps(data), encoding='utf-8')
            tool = ROOT / 'integrations/factory-export/tools/to_engine_v02.py'
            def convert(folder, *args):
                out = Path(tmp) / folder
                subprocess.run([sys.executable, str(tool), str(src), '--out', str(out), *args],
                               capture_output=True, text=True, check=True, timeout=25)
                return out
            old = convert('old')
            self.assertNotIn('external_end', json.loads((old / 'engine-scenario.json').read_text())['operations'][0])
            new = convert('anchored', '--subcontract-policy', 'anchored')
            s = loads((new / 'engine-scenario.json').read_text())
            o = s.operations[0]
            self.assertEqual(o.duration_seconds, 0)
            self.assertEqual(o.alternatives, ())
            self.assertEqual(o.external_end.isoformat(), '2026-10-18T12:00:00+00:00')
            p = plan(s)
            self.assertEqual(verify_plan(s, p), ())
            self.assertEqual(p.assignments[0].effective_mode, 'external')
            snap = json.loads((new / 'factory-snapshot.json').read_text())
            template = loads((new / 'factory-template.json').read_text())
            imported = import_snapshot(template, snap)
            self.assertEqual(imported.operations[0].external_end, o.external_end)
            self.assertEqual(verify_plan(imported, plan(imported)), ())

    def test_factory_converter_anchors_like_legacy_rule(self):
        # docs/28 R4: only the first external phase of a lot with an L_LARE supplier,
        # and only for jobs that the automatic mode plans forward.
        data = factory_source()
        data['suppliers'] = [{'code': 'SUP-1', 'calendarId': 0}]
        data['lots'] = [{'orderId': 1, 'lotId': lot, 'parentLotId': None, 'priority': 0} for lot in (1, 2, 3)]
        template = data['operations'][0]
        phases = {  # operationId: (lot, sequence, supplier in L_LARE, due)
            101: (1, 10, True, '2026-10-30'), 102: (1, 20, True, '2026-10-30'),
            201: (2, 10, True, '2026-12-30'),
            301: (3, 10, False, '2026-10-30'), 302: (3, 20, True, '2026-10-30'),
        }
        data['operations'], data['operationResources'], data['subcontractOrders'] = [], [], []
        for oid, (lot, seq, listed, due) in phases.items():
            data['operations'].append(dict(template, operationId=oid, lotId=lot, phaseId=oid, sequence=seq,
                                           executionCode='E', runMachines=0, runOperators=0,
                                           legacyDueDate=due))
            if listed:
                data['operationResources'].append({
                    'operationId': oid, 'resourceKind': 'SUPPLIER', 'resourceCode': 'SUP-1',
                    'assignmentRule': 'ALLOWED', 'calendarId': None, 'calendarModelId': None})
            data['subcontractOrders'].append(
                {'operationId': oid, 'supplierCode': 'SUP-1', 'promisedDate': '2026-10-12T00:00:00'})
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / 'src.json'
            src.write_text(json.dumps(data), encoding='utf-8')
            out = Path(tmp) / 'out'
            subprocess.run([sys.executable, str(ROOT / 'integrations/factory-export/tools/to_engine_v02.py'),
                            str(src), '--out', str(out), '--subcontract-policy', 'anchored'],
                           capture_output=True, text=True, check=True, timeout=25)
            s = loads((out / 'engine-scenario.json').read_text(encoding='utf-8'))
            report = (out / 'conversion-report.md').read_text(encoding='utf-8')
        anchored = sorted(o.id for o in s.operations if o.external_end is not None)
        self.assertEqual(anchored, ['1/1/101', '1/3/302'])
        self.assertIn('| esterne con promessa non ancorate: commessa a ritroso | 1 |', report)
        self.assertIn('| esterne con promessa non ancorate: non prima fase esterna con fornitore del lotto | 2 |', report)
        self.assertEqual(verify_plan(s, plan(s)), ())


if __name__ == '__main__':
    unittest.main()
