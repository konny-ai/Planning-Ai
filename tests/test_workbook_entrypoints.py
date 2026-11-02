"""CLI and HTTP exercise the same real workbook -> audited engine path."""
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path
from urllib.request import urlopen, Request
import hashlib
import json
import tempfile
import threading
import unittest
import zipfile

from planning_factory.cli import main
from planning_factory.adapters.dashboard_api import DashboardState, make_dashboard_server
from planning_factory.adapters.json_io import loads,plan_loads,scenario_digest
from planning_factory.adapters.planning_workbook import (
    compile_tables,export_binding,include_factory_source,load_workbook,
)
from planning_factory.adapters.workbook_io import write_tables,read_tables
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.verification import verify_plan
from tools.build_workbook_demo import bending_tables

ROOT=Path(__file__).resolve().parents[1]


class WorkbookEntryTests(unittest.TestCase):
    def test_confirmed_material_override_is_accepted_by_startup_validation(self):
        tables=bending_tables()
        tables['domain_policy']=[r for r in tables['domain_policy'] if r['domain']!='materials']
        tables['domain_policy'].append(dict(domain='materials',coverage='gestito',allocation_order='due',material_policy='ignorati_con_regola'))
        tables['item']=[dict(id='M',base_uom='PZ')];tables['uom']=[dict(id='PZ',description='Pezzi')]
        tables['material_need']=[dict(operation_id='BEND',item_id='M',quantity=2,uom_id='PZ')]
        tables['material_supply']=[]
        tables['guidance']=[dict(id='IGNORE',action='ignore_material',note='Disponibilita assunta dal pianificatore',
            status='confermata',confirmed_by='test',confirmed_at=tables['scenario'][0]['snapshot_at'])]
        wb=compile_tables(tables)
        self.assertFalse(wb.built.publishable)
        self.assertTrue(wb.validation_accepted)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'input.xlsx';write_tables(path,tables)
            with redirect_stdout(StringIO()):self.assertEqual(main(['workbook-validate',str(path)]),0)

    def test_common_setup_run_counts_are_checked_against_every_existing_bundle(self):
        tables=bending_tables()
        for code in ('setup_phase','stage_requirement','stage_candidate','attendance_policy','machine_state'):
            tables[code]=[]
        row=tables['operation'][0]
        row.pop('setup_frequency');row.pop('setup_occurrences')
        row.update(setup_operator_count=2,mode='auto')
        workbook=compile_tables(tables)
        self.assertIsNone(workbook.stage_contract)
        scenario,result,_=workbook.calculate()
        self.assertEqual(verify_plan(scenario,result),())
        self.assertEqual({d.resource_id for d in scenario.operations[0].alternatives[0].demands},
                         {'BENDER','PERSON-1','PERSON-2'})
        row['run_operator_count']=1
        with self.assertRaises(ValidationError):
            compile_tables(tables)
        row.update(run_operator_count=2,run_operator_utilization='0.25')
        with self.assertRaises(ValidationError):
            compile_tables(tables)

    def test_common_unattended_bundle_has_no_operator_reservation(self):
        tables=bending_tables()
        for code in ('setup_phase','stage_requirement','stage_candidate','attendance_policy','machine_state'):
            tables[code]=[]
        row=tables['operation'][0]
        row.pop('setup_frequency');row.pop('setup_occurrences')
        row.update(setup_operator_count=0,run_operator_count=0,
                   setup_operator_utilization=0,run_operator_utilization=0)
        tables['group_demand']=[r for r in tables['group_demand'] if r['resource_id']=='BENDER']
        workbook=compile_tables(tables)
        self.assertIsNone(workbook.stage_contract)
        scenario,result,_=workbook.calculate()
        self.assertFalse(result.unscheduled)
        self.assertEqual(verify_plan(scenario,result),())
        self.assertEqual([d.resource_id for d in scenario.operations[0].alternatives[0].demands],['BENDER'])

    def test_cli_produces_matching_scenario_and_plan_and_never_edits_input(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            source=root/'input.xlsx';write_tables(source,bending_tables())
            before=hashlib.sha256(source.read_bytes()).hexdigest()
            with redirect_stdout(StringIO()):
                exit_code=main(['workbook-plan',str(source),'-o',str(root/'plan.json'),
                    '--scenario-out',str(root/'scenario.json'),'--report',str(root/'report.json')])
            self.assertEqual(exit_code,0)
            scenario=loads((root/'scenario.json').read_text(encoding='utf-8'))
            result=plan_loads((root/'plan.json').read_text(encoding='utf-8'))
            self.assertEqual(verify_plan(scenario,result),())
            self.assertEqual({r.order_id for r in result.orders},{'DEMO-ORDER'})
            report=json.loads((root/'report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['planning_path'],'setup-run')
            self.assertEqual(report['calculated_scenario_digest'],scenario_digest(scenario))
            self.assertEqual(before,hashlib.sha256(source.read_bytes()).hexdigest())

    def test_http_bootstrap_and_resource_loads_from_one_workbook(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'input.xlsx';write_tables(source,bending_tables(unattended=True))
            server=make_dashboard_server(source,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                base=f'http://127.0.0.1:{server.server_address[1]}'
                with urlopen(base+'/api/bootstrap',timeout=10) as response:
                    data=json.load(response)
                self.assertEqual(data['meta']['source'],'planning_workbook')
                self.assertTrue(data['meta']['engine_verified'])
                self.assertEqual(data['meta']['workbook']['capacity_scale'],10000)
                self.assertTrue(data['meta']['v03_available'])
                self.assertEqual({r['order_id'] for r in data['plan']['orders']},{'DEMO-ORDER'})
                request=Request(base+'/api/loads',data=json.dumps({'scenario_digest':data['plan']['scenario_digest'],
                    'start':'2026-10-26','days':2,'kind':'machine'}).encode(),
                    headers={'Content-Type':'application/json','Origin':base})
                with urlopen(request,timeout=10) as response:loads_data=json.load(response)
                row=next(r for r in loads_data['rows'] if r['resource_id']=='BENDER')
                self.assertEqual(row['capacity'],1)
                self.assertAlmostEqual(sum(row['booked_hours']),18.67,places=2)
                request=Request(base+'/api/v03-preview',data=b'{}',
                    headers={'Content-Type':'application/json','Origin':base})
                with urlopen(request,timeout=10) as response:preview=json.load(response)
                self.assertTrue(preview['audited'])
                self.assertEqual(len(preview['stage_bookings']),2)
            finally:
                server.shutdown();server.server_close();thread.join(timeout=5)

    def test_workbook_does_not_load_an_unrelated_descriptions_sidecar(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'input.xlsx';write_tables(source,bending_tables())
            (root/'descrizioni.json').write_text(json.dumps({'schema':'planning-factory/descrizioni-v1',
                'orders':{'PRIVATE':{'customer':'Another dataset'}}}),encoding='utf-8')
            state=DashboardState(source)
            self.assertNotIn('PRIVATE',state.descriptions()['orders'])
            state.bootstrap()
            preview=state.staged_preview()
            self.assertTrue(preview['audited'])
            self.assertEqual(len(preview['stage_bookings']),2)
            with self.assertRaisesRegex(ValidationError,'nel file completo'):
                state.calculate({'mode':'backward'})
            with self.assertRaisesRegex(ValidationError,'ricarica'):
                state.simulate({}, {})
            # Secure dashboard rules and archived previews use this same gate.
            with self.assertRaisesRegex(ValidationError,'ricarica'):
                state._audited_plan(state.source)

    def test_template_has_all_tables_and_rejects_calculation_until_completed(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'template.xlsx'
            with redirect_stdout(StringIO()):
                self.assertEqual(main(['workbook-template','-o',str(target)]),0)
            tables,_,_=read_tables(target)
            self.assertEqual(len(tables),59)
            with self.assertRaises(ValidationError):
                load_workbook(target)
            with zipfile.ZipFile(target) as package:
                self.assertIn(b'00 Indice',package.read('xl/workbook.xml'))

    def test_formula_derived_minutes_are_recomputed_and_percent_controls_are_native(self):
        with tempfile.TemporaryDirectory() as folder:
            original=Path(folder)/'original.xlsx';changed=Path(folder)/'changed.xlsx'
            write_tables(original,bending_tables())
            from xml.etree import ElementTree as ET
            from planning_factory.adapters.workbook_io import MAIN
            found=False
            with zipfile.ZipFile(original) as src,zipfile.ZipFile(changed,'w') as dst:
                for item in src.infolist():
                    content=src.read(item.filename)
                    if item.filename.startswith('xl/worksheets/sheet'):
                        root=ET.fromstring(content)
                        formulas=[c for c in root.iter(f'{{{MAIN}}}c') if c.find(f'{{{MAIN}}}f') is not None]
                        if formulas:
                            found=True
                            self.assertEqual(len(root.find(f'{{{MAIN}}}dataValidations')),4)
                            for cell in formulas:
                                cache=cell.find(f'{{{MAIN}}}v')
                                if cache is not None:cell.remove(cache)
                            content=ET.tostring(root)
                    dst.writestr(item,content)
            self.assertTrue(found)
            loaded=load_workbook(changed)
            self.assertEqual(loaded.source.operations[0].duration_seconds,60000)
            self.assertEqual(loaded.source.operations[0].setup_seconds,7200)

    def test_existing_csv_binding_exports_to_an_independent_workbook(self):
        with tempfile.TemporaryDirectory() as folder:
            target=Path(folder)/'data.xlsx'
            _,built=export_binding(ROOT/'examples/canonical-files/binding.json',target,
                                  now='2026-10-26T07:00:00Z',data_nature='simulati')
            imported=load_workbook(target)
            self.assertEqual(scenario_digest(imported.source),scenario_digest(built.scenario))
            self.assertTrue(imported.tables['provenance'])

    def test_editing_phase_counts_to_zero_drops_operator_demand_without_deleting_candidates(self):
        tables=bending_tables()
        tables['operation'][0].update(run_operator_count=0,run_operator_utilization=0)
        tables['attendance_policy'][1].update(mode='assente',outside_staff_shifts=True)
        _,_,evidence=compile_tables(tables).calculate()
        run=next(b for b in evidence['stage_bookings'] if b['stage']=='run')
        self.assertEqual(run['allocations'],[{'resource_id':'BENDER','occupancy_bps':10000}])

    def test_rich_factory_source_is_preserved_and_unapplied_constraints_block(self):
        tables=bending_tables();tables['operation'][0]['id']='1/2/3'
        for code,rows in tables.items():
            for r in rows:
                if r.get('operation_id')=='BEND':r['operation_id']='1/2/3'
        snapshot={'operations':[{'orderId':1,'lotId':2,'phaseId':3,'operationId':99,
            'setupMachines':1,'setupOperators':1,'runMachines':1,'runOperators':2,
            'runOperatorPct':100,'unitSec':300,'splitMaxFractions':3}]}
        enriched=include_factory_source(tables,snapshot)
        self.assertEqual(enriched['provenance'][0]['original_value']['unitSec'],300)
        with self.assertRaisesRegex(ValidationError,'splitMaxFractions'):
            compile_tables(enriched)

    def test_formula_without_cached_value_and_duplicate_named_table_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            original=Path(folder)/'original.xlsx';broken=Path(folder)/'broken.xlsx'
            write_tables(original,bending_tables())
            with zipfile.ZipFile(original) as src,zipfile.ZipFile(broken,'w') as dst:
                for item in src.infolist():
                    content=src.read(item.filename)
                    if item.filename=='xl/worksheets/sheet1.xml':
                        content=content.replace(b'<v>1</v>',b'<f>1</f>',1)
                    dst.writestr(item,content)
            with self.assertRaisesRegex(ValidationError,'formula without cached'):
                read_tables(broken)
            with zipfile.ZipFile(original) as src,zipfile.ZipFile(broken,'w') as dst:
                for item in src.infolist():
                    content=src.read(item.filename)
                    if item.filename=='xl/tables/table2.xml':
                        content=content.replace(b'pf_domain_policy',b'pf_scenario')
                    dst.writestr(item,content)
            with self.assertRaisesRegex(ValidationError,'duplicate planning table'):
                read_tables(broken)


if __name__=='__main__':unittest.main()
