"""Factory publication: changeset and T-SQL script built from a plan, without any database.

Synthetic data only. Execution against SQL Server runs in the private clone workflow.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from .test_factory_export_safety import TOOLS, load_tool, scenario as synthetic

publish = load_tool('publish_factory_plan')


def factory_scenario():
    s = synthetic()
    s['operations'][0].update(statusCode=41, sourceLastUpdate='2026-10-14T16:47:38.127000')
    s['operationResources'][0]['assignmentId'] = 501   # MAC-A
    s['operationResources'][1]['assignmentId'] = 502   # OP-A
    s['operationResources'][0]['usagePct'] = 100
    s['operationResources'][1]['usagePct'] = 50
    return s


def plan(segments, demands=('MAC:MAC-A', 'OP:OP-A'), mode='forward', **extra):
    assignment = {'task_id': 'job:1/1/11', 'operation_ids': ['1/1/11'], 'effective_mode': mode,
                  'segments': [{'start': s, 'end': e} for s, e in segments],
                  'demands': [{'resource_id': r, 'units': 1} for r in demands]}
    assignment.update(extra)
    return {'scenario_id': 'synthetic', 'scenario_digest': 'abc', 'assignments': [assignment], 'unscheduled': []}


class FactoryPublishTests(unittest.TestCase):
    def test_changeset_converts_to_factory_local_time_and_marks_used_resources(self):
        cs = publish.build_changeset(factory_scenario(), plan([
            ('2026-10-19T08:30:00Z', '2026-10-19T10:00:00Z'), ('2026-10-19T11:30:00Z', '2026-10-19T13:00:00Z')]))
        phase, = cs['phases']
        self.assertEqual(phase['start'], datetime(2026, 10, 19, 10, 30))   # CEST = UTC+2
        self.assertEqual(phase['end'], datetime(2026, 10, 19, 15, 0))
        self.assertEqual([(r['IDLARE'], r['RIUSE'], r['IDBLK']) for r in cs['lare']],
                         [(501, 'Y', 1), (502, 'Y', 2)])               # machines first, then operators
        self.assertEqual([(b['IDBLK'], b['RIPOC'], b['start'].hour) for b in cs['blocks']],
                         [(1, 100.0, 10), (1, 100.0, 13), (2, 50.0, 10), (2, 50.0, 13)])
        self.assertEqual(cs['expected'], {101: (41, '2026-10-14T16:47:38.127000')})
        self.assertEqual(cs['lots'], [{'key': (1, 1), 'start': phase['start'], 'end': phase['end']}])
        self.assertEqual(cs['orders'][0]['COPNF'], 0)

    def test_unused_candidates_are_released_and_backward_sets_order_type(self):
        cs = publish.build_changeset(factory_scenario(), plan(
            [('2026-11-02T08:00:00Z', '2026-11-02T09:00:00Z')], demands=('OP:OP-A',), mode='backward'))
        self.assertEqual([(r['IDLARE'], r['RIUSE']) for r in cs['lare']], [(501, 'N'), (502, 'Y')])
        self.assertEqual({b['IDBLK'] for b in cs['blocks']}, {2})
        self.assertEqual(cs['phases'][0]['start'], datetime(2026, 11, 2, 9, 0))  # CET = UTC+1
        self.assertEqual(cs['orders'][0]['COPNF'], 1)

    def test_anchored_external_phase_uses_the_ordered_supplier_with_a_zero_block(self):
        s = factory_scenario()
        s['operations'][0]['executionCode'] = 'E'
        s['operationResources'] = [
            {'assignmentId': 601, 'operationId': 101, 'resourceKind': 'SUPPLIER', 'resourceCode': 'F-A', 'usagePct': 100},
            {'assignmentId': 602, 'operationId': 101, 'resourceKind': 'SUPPLIER', 'resourceCode': 'F-B', 'usagePct': 100}]
        s['subcontractOrders'] = [{'operationId': 101, 'supplierCode': 'F-B', 'promisedDate': '2026-08-07'}]
        cs = publish.build_changeset(s, plan([], demands=(), mode='external', external_end='2026-08-06T22:00:00Z'))
        self.assertEqual((cs['phases'][0]['start'], cs['phases'][0]['end']), (datetime(2026, 8, 7), datetime(2026, 8, 7)))
        self.assertEqual([(r['IDLARE'], r['RIUSE']) for r in cs['lare']], [(601, 'N'), (602, 'Y')])
        self.assertEqual([(b['IDBLK'], b['start'], b['end']) for b in cs['blocks']], [(2, datetime(2026, 8, 7), datetime(2026, 8, 7))])
        self.assertEqual(cs['skipped'], {'fasi esterne ancorate alla data promessa: blocco di durata zero sul fornitore': 1})
        s['operationResources'] = []
        cs = publish.build_changeset(s, plan([], demands=(), mode='external', external_end='2026-08-06T22:00:00Z'))
        self.assertEqual((cs['lare'], cs['blocks']), ([], []))

    def test_virtual_resource_gets_dates_only(self):
        cs = publish.build_changeset(factory_scenario(), plan(
            [('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')], demands=('NO_RESOURCE',)))
        self.assertEqual(cs['blocks'], [])
        self.assertTrue(all(r['RIUSE'] == 'N' for r in cs['lare']))

    def test_supplier_from_subcontract_order_gets_dates_only_like_legacy(self):
        s = factory_scenario()
        s['operations'][0]['executionCode'] = 'E'
        cs = publish.build_changeset(s, plan([('2026-10-19T08:00:00Z', '2026-10-21T15:00:00Z')], demands=('SUP:F1',)))
        self.assertEqual(cs['blocks'], [])
        self.assertEqual(len(cs['phases']), 1)
        self.assertIn("fasi esterne con fornitore dall'ordine di conto lavoro (senza L_LARE): date senza blocchi", cs['skipped'])

    def test_phases_outside_the_plan_become_milestones_like_legacy(self):
        s = factory_scenario()
        base = s['operations'][0]
        s['operations'] += [dict(base, operationId=102, phaseId=12, theoreticalTotalSec=0),   # after 101
                            dict(base, operationId=103, phaseId=13, theoreticalTotalSec=0),   # after 102 (chain)
                            dict(base, operationId=100, phaseId=9, theoreticalTotalSec=0),    # before 101 only
                            dict(base, operationId=104, lotId=2, phaseId=1, theoreticalTotalSec=0)]  # isolated
        s['precedences'] = [{'predecessorOperationId': 101, 'successorOperationId': 102},
                            {'predecessorOperationId': 102, 'successorOperationId': 103},
                            {'predecessorOperationId': 100, 'successorOperationId': 101}]
        cs = publish.build_changeset(s, plan([('2026-10-19T08:30:00Z', '2026-10-19T10:00:00Z')], mode='backward'))
        dates = {p['IDLAV']: (p['start'], p['end']) for p in cs['phases']}
        self.assertEqual(dates[102], (datetime(2026, 10, 19, 12), datetime(2026, 10, 19, 12)))
        self.assertEqual(dates[103], dates[102])
        self.assertEqual(dates[100], (datetime(2026, 10, 19, 10, 30),) * 2)
        self.assertEqual(dates[104], (datetime(2026, 10, 19),) * 2)   # isolated: planning start
        self.assertEqual(set(cs['expected']), {100, 101, 102, 103, 104})
        self.assertEqual(cs['orders'][0]['COPNF'], 1)       # milestones never decide the order type
        self.assertEqual(cs['skipped']["fasi fuori piano isolate (senza legami con fasi pianificate): all'inizio della pianificazione"], 1)
        self.assertEqual(cs['skipped']['fasi fuori piano datate come milestone dai legami (inizio = fine)'], 3)
        sql = publish.render_sql(cs)
        self.assertIn("LADIP='2026-10-19', LAHIP='12:00:00', LADFP='2026-10-19', LAHFP='12:00:00' WHERE CONUM=1 AND LOCOD=1 AND IDFAS=13", sql)

    def test_milestone_marks_stale_resource_candidates_unused(self):
        s = factory_scenario()
        s['operations'].append(dict(s['operations'][0], operationId=102, phaseId=12,
                                    theoreticalTotalSec=0))
        s['operationResources'].append(dict(s['operationResources'][0], operationId=102,
                                            assignmentId=503))
        cs = publish.build_changeset(s, plan(
            [('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')]))
        self.assertEqual([(r['IDLARE'], r['RIUSE']) for r in cs['lare'] if r['IDLARE'] == 503],
                         [(503, 'N')])
        self.assertIn("UPDATE dbo.L_LARE SET RIUSE='N'", publish.render_sql(cs))

    def test_anchored_supplier_matches_promised_date_not_first_supplier(self):
        s = factory_scenario()
        s['operations'][0]['executionCode'] = 'E'
        s['operationResources'] = [
            {'assignmentId': 601, 'operationId': 101, 'resourceKind': 'SUPPLIER',
             'resourceCode': 'F-A', 'usagePct': 100},
            {'assignmentId': 602, 'operationId': 101, 'resourceKind': 'SUPPLIER',
             'resourceCode': 'F-B', 'usagePct': 100}]
        s['subcontractOrders'] = [
            {'operationId': 101, 'supplierCode': 'F-A', 'promisedDate': '2026-08-05'},
            {'operationId': 101, 'supplierCode': 'F-B', 'promisedDate': '2026-08-07'}]
        cs = publish.build_changeset(s, plan([], demands=(), mode='external',
                                              external_end='2026-08-06T22:00:00Z'))
        self.assertEqual([(r['code'], r['RIUSE']) for r in cs['lare']],
                         [('F-A', 'N'), ('F-B', 'Y')])

    def test_refuses_plans_that_do_not_belong_to_the_scenario(self):
        anonymized = plan([('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')])
        anonymized['assignments'][0]['operation_ids'] = ['ORD001/1/11']
        with self.assertRaisesRegex(publish.PublishError, 'assente nello scenario'):
            publish.build_changeset(factory_scenario(), anonymized)
        other = plan([('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')])
        other['scenario_id'] = 'factory-bench-a-2026-10-05'
        with self.assertRaisesRegex(publish.PublishError, 'riconvertire senza --anonymize'):
            publish.build_changeset(factory_scenario(), other)

    def test_script_is_one_guarded_transaction_in_legacy_order(self):
        sql = publish.render_sql(publish.build_changeset(factory_scenario(), plan(
            [('2026-10-19T08:30:00Z', '2026-10-19T10:00:00Z')])))
        order = ['BEGIN TRANSACTION', 'THROW 50001', 'THROW 50002', 'DELETE b FROM dbo.L_LAPN b INNER JOIN @publish p',
                 "UPDATE dbo.A_LAV SET FPIAN='Y', LADIP='2026-10-19', LAHIP='10:30:00', LADFP='2026-10-19', LAHFP='12:00:00' WHERE CONUM=1 AND LOCOD=1 AND IDFAS=11",
                 "UPDATE dbo.L_LARE SET RIUSE='Y', IDBLK=1 WHERE IDLARE=501",
                 'UPDATE dst SET LODIP', 'UPDATE dst SET COPNF=CASE',
                 "VALUES (1,1,11,1,0,0,0,100.00,'2026-10-19','10:30:00','2026-10-19','12:00:00')",
                 'COMMIT TRANSACTION']
        positions = [sql.index(s) for s in order]
        self.assertEqual(positions, sorted(positions))
        self.assertIn('SET XACT_ABORT ON', sql)
        self.assertIn("(101,41,CONVERT(datetime2(6), '2026-10-14T16:47:38.127000', 126))", sql)
        for untouched in ('LADTS', 'LASCH', 'PASCH', 'JobPrevious', 'A_MAC', 'FAPNF'):
            self.assertNotIn(untouched, sql)

    def test_unscheduled_phases_keep_their_blocks_and_dates(self):
        s = factory_scenario()
        s['operations'].append(dict(s['operations'][0], operationId=102, phaseId=12))
        p = plan([('2026-10-19T08:30:00Z', '2026-10-19T10:00:00Z')])
        p['unscheduled'] = [{'task_id': 'job:1/1/12', 'operation_ids': ['1/1/12'], 'code': 'NO_FEASIBLE_SLOT'}]
        cs = publish.build_changeset(s, p)
        self.assertEqual(cs['keep'], [(1, 1, 12)])
        self.assertNotIn(102, cs['expected'])                 # neither dated as milestone nor touched
        sql = publish.render_sql(cs)
        self.assertIn('INSERT INTO @keep (CONUM, LOCOD, IDFAS) VALUES\n  (1,1,12);', sql)
        self.assertIn('ON b.CONUM = p.CONUM AND b.LOCOD = p.LOCOD AND b.IDFAS = p.IDFAS', sql)

    def test_publication_keeps_unexported_phases_of_same_order(self):
        cs = publish.build_changeset(factory_scenario(), plan(
            [('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')]))
        sql = publish.render_sql(cs)
        self.assertIn('INSERT INTO @publish (CONUM, LOCOD, IDFAS)', sql)
        self.assertIn('DELETE b FROM dbo.L_LAPN b INNER JOIN @publish p', sql)
        self.assertNotIn('DELETE FROM dbo.L_LAPN WHERE CONUM IN', sql)
        self.assertIn('THROW 50003', sql)   # concurrent new L_LARE candidate

    def test_internal_resource_without_l_lare_row_is_refused(self):
        with self.assertRaisesRegex(publish.PublishError, 'senza riga L_LARE'):
            publish.build_changeset(factory_scenario(), plan(
                [('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')], demands=('MAC:MAC-A', 'OP:OP-Z')))

    def test_nesting_members_share_the_program_blocks_like_legacy(self):
        s = factory_scenario()
        s['operations'].append(dict(s['operations'][0], operationId=102, phaseId=12))
        s['operationResources'] += [dict(r, operationId=102, assignmentId=r['assignmentId'] + 10)
                                    for r in s['operationResources']]
        p = plan([('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')], task_id='nest:NEST-777')
        p['assignments'][0]['operation_ids'] = ['1/1/11', '1/1/12']
        cs = publish.build_changeset(s, p)
        self.assertEqual([ph['key'] for ph in cs['phases']], [(1, 1, 11), (1, 1, 12)])
        self.assertEqual({(b['key'][2], b['IDPTC'], b['RIPOC']) for b in cs['blocks']},
                         {(11, 777, 50.0), (11, 777, 25.0), (12, 777, 50.0), (12, 777, 25.0)})  # 100/2, 50/2
        p['assignments'][0]['task_id'] = 'nest:NEST-x'
        with self.assertRaisesRegex(publish.PublishError, 'IDNES numerico'):
            publish.build_changeset(s, p)

    def test_derived_engine_scenario_must_keep_the_converted_operations(self):
        base = {'operations': [{'id': '1/1/11'}]}
        publish.check_derived(base, {'operations': [{'id': '1/1/11'}], 'nestings': [{'id': 'NEST-1'}]})
        with self.assertRaisesRegex(publish.PublishError, 'stesse fasi'):
            publish.check_derived(base, {'operations': [{'id': '1/1/11'}, {'id': '1/1/12'}]})
        self.assertTrue(publish.same_scenario('synthetic-nesting', 'synthetic'))
        self.assertFalse(publish.same_scenario('synthetic2', 'synthetic'))

    def test_rule_derived_candidates_never_become_l_lare_writes(self):
        # CLIENTE: candidates from the phase-type rule carry assignmentId 0 (no L_LARE row).
        s = factory_scenario()
        s['operationResources'].append(dict(s['operationResources'][1], assignmentId=0, resourceCode='OP-R'))
        cs = publish.build_changeset(s, plan([('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')]))
        self.assertEqual([r['IDLARE'] for r in cs['lare']], [501, 502])   # unused derived row: no UPDATE
        self.assertEqual(cs['lare_new'], [])
        # Used: a new L_LARE row after the existing candidates, numbered by the Factory trigger.
        cs = publish.build_changeset(s, plan([('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')],
                                             demands=('MAC:MAC-A', 'OP:OP-R')))
        self.assertEqual([(r['IDLARE'], r['RIUSE']) for r in cs['lare']], [(501, 'Y'), (502, 'N')])
        self.assertEqual([(r['kind'], r['code'], r['IDBLK']) for r in cs['lare_new']], [('OPERATOR', 'OP-R', 3)])
        self.assertEqual(sorted(b['IDBLK'] for b in cs['blocks']), [1, 3])
        sql = publish.render_sql(cs)
        self.assertIn("INSERT INTO dbo.L_LARE (CONUM,LOCOD,IDFAS,RECOD,MACOD,OPCOD,RIPAS,RIUSE,IDBLK,IDNUM,RIPOC,"
                      "OPATR,TMBNS,INEFF,RPRIO,RITPU,IDLARE) VALUES (1,1,11,NULL,NULL,N'OP-R',10000,'Y',3,0,", sql)
        self.assertNotIn('WHERE IDLARE=0', sql)
        # The insert happens after the concurrency guards, inside the single transaction.
        self.assertLess(sql.index('THROW 50003'), sql.index('INSERT INTO dbo.L_LARE'))
        self.assertLess(sql.index('INSERT INTO dbo.L_LARE'), sql.index('COMMIT TRANSACTION'))

    def test_explicit_zero_usage_is_kept_and_resource_rows_are_guarded(self):
        s = factory_scenario()
        s['operationResources'][0].update(usagePct=0, assignmentRule='FORCED', resourceCode="MAC'A")
        s['machines'][0]['code'] = "MAC'A"
        cs = publish.build_changeset(s, plan([('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')], demands=("MAC:MAC'A", 'OP:OP-A')))
        self.assertEqual({b['RIPOC'] for b in cs['blocks'] if b['IDBLK'] == 1}, {0.0})
        sql = publish.render_sql(cs)
        self.assertIn("(501,1,1,11,N'MACHINE',N'MAC''A',N'FORCED',0.00)", sql)   # quoted, compared under lock
        self.assertIn("ISNULL(r.RIPOC, 100) <> e.RIPOC", sql)

    def test_null_lastupdate_transitions_count_as_stale(self):
        sql = publish.render_sql(publish.build_changeset(factory_scenario(), plan([('2026-10-19T08:00:00Z', '2026-10-19T09:00:00Z')])))
        self.assertIn('(l.LASTUPDATE IS NULL AND e.LASTUPDATE IS NOT NULL) OR (l.LASTUPDATE IS NOT NULL AND e.LASTUPDATE IS NULL)', sql)
        self.assertNotIn('DATEDIFF', sql)   # overflows on timestamps weeks apart
        self.assertTrue(sql.rstrip().endswith('COMMIT TRANSACTION;'))     # nothing after the commit

    def convert_and_plan(self, root, s):
        from planning_factory.adapters.json_io import dumps, loads
        from planning_factory.engine.scheduler import plan as schedule
        (root / 's.json').write_text(json.dumps(s), encoding='utf-8')
        subprocess.run([sys.executable, str(TOOLS / 'to_engine_v02.py'), str(root / 's.json'), '--out', str(root / 'm')],
                       check=True, capture_output=True, timeout=25)
        engine = loads((root / 'm' / 'engine-scenario.json').read_text(encoding='utf-8'))
        (root / 'p.json').write_text(dumps(schedule(engine)), encoding='utf-8')
        return [sys.executable, str(TOOLS / 'publish_factory_plan.py'), str(root / 's.json'), str(root / 'p.json'),
                '--engine-dir', str(root / 'm'), '--out', str(root / 'out')]

    def test_cli_dry_run_and_execute_guards(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = self.convert_and_plan(root, factory_scenario())
            result = subprocess.run(base, capture_output=True, text=True, timeout=25)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('COMMIT TRANSACTION', (root / 'out' / 'publish.sql').read_text(encoding='utf-8'))
            self.assertIn('fasi pubblicate (A_LAV) | 1', (root / 'out' / 'publish-report.md').read_text(encoding='utf-8'))
            for extra, message in ((['--execute', '--database', 'SYNTHETIC'], 'confirm-database'),
                                   (['--verify', '--database', 'SYNTHETIC'], 'confirm-database'),
                                   (['--execute', '--database', 'OTHER', '--confirm-database', 'OTHER'], "viene da 'SYNTHETIC'")):
                result = subprocess.run(base + extra, capture_output=True, text=True, timeout=25)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_cli_refuses_a_plan_from_another_extraction_or_scenario(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = self.convert_and_plan(root, factory_scenario())
            newer = factory_scenario()
            newer['source']['capturedAt'] = '2026-10-20T00:00:00+02:00'   # same ID, different extraction
            (root / 's.json').write_text(json.dumps(newer), encoding='utf-8')
            result = subprocess.run(base, capture_output=True, text=True, timeout=25)
            self.assertIn('impronta diversa', result.stderr)
            base = self.convert_and_plan(root, factory_scenario())
            p = json.loads((root / 'p.json').read_text(encoding='utf-8'))
            p['scenario_digest'] = '0' * 64
            (root / 'p.json').write_text(json.dumps(p), encoding='utf-8')
            result = subprocess.run(base, capture_output=True, text=True, timeout=25)
            self.assertIn('digest diverso', result.stderr)
            (root / 'm' / 'conversion.json').unlink()
            result = subprocess.run(base, capture_output=True, text=True, timeout=25)
            self.assertIn('conversion.json mancante', result.stderr)

    def test_cli_rejects_target_server_different_from_extraction(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            data = factory_scenario()
            data['source']['server'] = 'clone-db;TrustServerCertificate=yes'
            base = self.convert_and_plan(root, data)
            result = subprocess.run(
                base + ['--verify', '--database', 'SYNTHETIC',
                        '--confirm-database', 'SYNTHETIC',
                        '--server', 'production-db;TrustServerCertificate=yes'],
                capture_output=True, text=True, timeout=25)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("server diverso dall'estrazione", result.stderr)

    def test_cli_refuses_corrupted_plan_with_correct_scenario_digest(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            base = self.convert_and_plan(root, factory_scenario())
            p = json.loads((root / 'p.json').read_text(encoding='utf-8'))
            # A digest authenticates the scenario, not arbitrary plan contents.
            p['assignments'][0]['segments'][0]['end'] = p['assignments'][0]['segments'][0]['start']
            (root / 'p.json').write_text(json.dumps(p), encoding='utf-8')
            result = subprocess.run(base, capture_output=True, text=True, timeout=25)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('piano non valido', result.stderr)

    def test_end_to_end_from_converter_and_engine(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            self.convert_and_plan(root, factory_scenario())
            result = json.loads((root / 'p.json').read_text(encoding='utf-8'))
        cs = publish.build_changeset(factory_scenario(), result)
        self.assertEqual(len(cs['phases']), 1)
        self.assertEqual({r['RIUSE'] for r in cs['lare']}, {'Y'})
        self.assertEqual(sum(b['IDBLK'] == 1 for b in cs['blocks']), sum(b['IDBLK'] == 2 for b in cs['blocks']))

if __name__ == '__main__':
    unittest.main()
