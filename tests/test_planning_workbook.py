"""Complete workbook ingestion, physical workload and end-to-end audits."""
from copy import deepcopy
from decimal import Decimal
from datetime import datetime
from pathlib import Path
import tempfile
import unittest

from planning_factory.adapters.json_io import loads, primitive, scenario_digest
from planning_factory.adapters.planning_workbook import (
    compile_tables, export_scenario, load_workbook, seconds, bps,
)
from planning_factory.adapters.workbook_io import catalogue, read_tables, write_tables
from planning_factory.canonical.entities import ENTITIES
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tools.build_workbook_demo import bending_tables

ROOT=Path(__file__).resolve().parents[1]


class WorkbookTests(unittest.TestCase):
    def test_catalogue_has_one_location_for_every_current_canonical_field(self):
        targets=[f['mapping'] for f in catalogue()['fields'] if f['mapping'] and
                 f['mapping'].split('.')[0] in ENTITIES]
        expected={f'{e.name}.{f.name}' for e in ENTITIES.values() for f in e.fields}
        self.assertEqual(set(targets),expected)
        self.assertEqual(len(targets),len(expected))

    def test_standard_workshop_xlsx_roundtrip_keeps_verified_plan(self):
        source=loads((ROOT/'examples/workshop.json').read_text(encoding='utf-8'))
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'inputs.xlsx'
            export_scenario(source,path,extracted_at='2026-10-19T00:00:00Z')
            workbook=load_workbook(path)
            scenario,result,evidence=workbook.calculate()
        actual,expected=primitive(result),primitive(plan(source))
        actual.pop('scenario_digest');expected.pop('scenario_digest')
        self.assertEqual(actual,expected)
        self.assertEqual(verify_plan(scenario,result),())
        self.assertEqual(evidence['path'],'standard')

    def test_two_people_one_machine_do_not_multiply_or_halve_cycle_duration(self):
        workbook=compile_tables(bending_tables())
        self.assertEqual(workbook.source.operations[0].duration_seconds,60000)
        self.assertEqual(workbook.source.operations[0].setup_seconds,7200)
        scenario,result,evidence=workbook.calculate()
        self.assertEqual(verify_plan(scenario,result),())
        setup,run=sorted(evidence['stage_bookings'],key=lambda b:b['stage']=='run')
        self.assertEqual(sum((datetime.fromisoformat(s['end'].replace('Z','+00:00'))-
                              datetime.fromisoformat(s['start'].replace('Z','+00:00'))).total_seconds()
                             for s in run['segments']),60000)
        self.assertEqual({a['resource_id'] for a in run['allocations']},{'BENDER','PERSON-1','PERSON-2'})
        self.assertTrue(all(a['occupancy_bps']==10000 for a in run['allocations']))
        self.assertEqual({a['resource_id'] for a in setup['allocations']},{'BENDER','PERSON-1'})
        self.assertEqual({r.order_id for r in result.orders},{'DEMO-ORDER'})
        self.assertTrue(any(a.operation_ids==('BEND',) for a in result.assignments))

    def test_shared_operator_fractional_capacity_allows_concurrent_machines(self):
        tables=bending_tables(operator_quota=Decimal('0.25'))
        first=tables['operation'][0]
        first.update(setup_operator_count=0,setup_operator_utilization=0,run_operator_count=1)
        tables['attendance_policy'][0]['mode']='assente'
        tables['resource'].append(dict(tables['resource'][0],id='BENDER-2'))
        tables['operation'].append(dict(first,id='BEND-2'))
        tables['phase_group'].append(dict(tables['phase_group'][0],operation_id='BEND-2'))
        gid=tables['phase_group'][0]['group_id']
        tables['resource_group'].append(dict(id='SECOND',description='Seconda macchina'))
        tables['phase_group'][-1]['group_id']='SECOND'
        tables['group_demand'].append(dict(group_id='SECOND',alternative=0,alternative_code='SECOND-A0',resource_id='BENDER-2',units=1))
        tables['setup_phase'].append(dict(tables['setup_phase'][0],operation_id='BEND-2',machine_id='BENDER-2',execution_rank=2))
        tables['machine_state'].append(dict(tables['machine_state'][0],resource_id='BENDER-2'))
        tables['stage_requirement']=[r for r in tables['stage_requirement'] if not (r['stage']=='setup' and r['kind']=='operator')]
        base=deepcopy(tables['stage_requirement'])
        tables['stage_requirement'] += [dict(r,operation_id='BEND-2') for r in base]
        tables['stage_candidate']=[r for r in tables['stage_candidate'] if not (r['stage']=='setup' and r['requirement_id']=='operator') and r['resource_id']!='PERSON-2']
        tables['stage_candidate'] += [dict(r,operation_id='BEND-2',resource_id='BENDER-2' if r['resource_id']=='BENDER' else r['resource_id'])
                                      for r in deepcopy(tables['stage_candidate'])]
        tables['attendance_policy'] += [dict(r,operation_id='BEND-2') for r in deepcopy(tables['attendance_policy'])]
        scenario,result,evidence=compile_tables(tables).calculate()
        runs=[b for b in evidence['stage_bookings'] if b['stage']=='run']
        self.assertEqual(runs[0]['segments'][0]['start'],runs[1]['segments'][0]['start'])
        self.assertEqual(verify_plan(scenario,result),())

    def test_atomic_nesting_retains_both_commercial_orders_and_one_duration(self):
        tables=bending_tables(unattended=True)
        tables['order'].append({'id':'SECOND-ORDER'})
        tables['operation'].append(dict(tables['operation'][0],id='MEMBER-2',order_id='SECOND-ORDER'))
        tables['phase_group'].append(dict(tables['phase_group'][0],operation_id='MEMBER-2'))
        tables['nest']=[dict(id='SHARED',duration_seconds=Decimal(1000),setup_seconds=Decimal(120))]
        tables['nest_member']=[dict(nest_id='SHARED',operation_id=oid) for oid in ('BEND','MEMBER-2')]
        for op in tables['operation']:op['splittable']=False
        tables['domain_policy'].append(dict(domain='nesting',coverage='gestito'))
        tables['setup_phase'][0]['operation_id']='nest:SHARED'
        for op in tables['operation']:op.pop('setup_frequency');op.pop('setup_occurrences')
        for code in ('stage_requirement','stage_candidate','attendance_policy'):
            for r in tables[code]:r['operation_id']='nest:SHARED'
        scenario,result,evidence=compile_tables(tables).calculate()
        runs=[a for a in result.assignments if a.task_id=='nest:SHARED']
        self.assertEqual(len(runs),1)
        self.assertEqual(set(runs[0].operation_ids),{'BEND','MEMBER-2'})
        self.assertEqual(sum(s.seconds for s in runs[0].segments),60000)
        self.assertEqual({o.order_id for o in result.orders},{'DEMO-ORDER','SECOND-ORDER'})
        self.assertEqual(verify_plan(scenario,result),())

    def test_material_units_are_explicit_and_setup_can_precede_arrival(self):
        tables=bending_tables(unattended=True)
        tables['uom']=[dict(id='PZ',description='Pezzi',dimension='conteggio',decimal_places=0)]
        tables['item']=[dict(id='BLANK',description='Semilavorato',kind='semilavorato',base_uom='PZ')]
        tables['material_need']=[dict(operation_id='BEND',item_id='BLANK',quantity='200',uom_id='PZ')]
        tables['material_supply']=[dict(id='STOCK',item_id='BLANK',quantity='200',uom_id='PZ',available_at='2026-10-26T12:00:00Z')]
        tables['domain_policy'].append(dict(domain='materials',coverage='gestito',allocation_order='due'))
        scenario,result,evidence=compile_tables(tables).calculate()
        setup=next(b for b in evidence['stage_bookings'] if b['stage']=='setup')
        run=next(b for b in evidence['stage_bookings'] if b['stage']=='run')
        self.assertEqual(setup['segments'][0]['start'],'2026-10-26T07:00:00Z')
        self.assertEqual(run['segments'][0]['start'],'2026-10-26T12:00:00Z')
        self.assertEqual(evidence['material_pegs'][0]['quantity'],'200')
        del tables['item'][0]['base_uom']
        with self.assertRaisesRegex(ValidationError,'unità base'):
            compile_tables(tables)

    def test_objective_table_selects_backward_and_sequence_objective(self):
        def tables(*rows):
            value=bending_tables(unattended=True)
            value['order'][0]['due']='2026-10-29T15:00:00Z'
            value['objective']=[dict(id=f'O{i}',kind='politica',code=c,value=v,unit='scelta')
                                for i,(c,v) in enumerate(rows)]
            return value
        _,_,forward=compile_tables(tables()).calculate()
        scenario,result,backward=compile_tables(tables(('planning_direction','backward'))).calculate()
        self.assertEqual((forward['planning_direction'],backward['planning_direction']),('forward','backward'))
        self.assertEqual(verify_plan(scenario,result),())
        start=lambda e,s:next(b for b in e['stage_bookings'] if b['stage']==s)['segments'][0]['start']
        self.assertGreater(start(backward,'run'),start(forward,'run'))      # run moved later
        self.assertGreaterEqual(start(backward,'setup'),start(forward,'setup'))
        self.assertTrue(backward['erp_publishable'] is False)
        _,_,searched=compile_tables(tables(('planning_direction','backward'),
                                           ('sequence_objective','setup'))).calculate()
        self.assertEqual(searched['machine_sequence'],forward['machine_sequence'])

    def test_objective_table_rejects_unsupported_or_inconsistent_rows(self):
        base_rows=lambda *rows:dict(bending_tables(unattended=True),objective=[
            dict(id=f'O{i}',kind=k,code=c,value=v,unit='scelta') for i,(k,c,v) in enumerate(rows)])
        cases=((('politica','planning_direction','sideways'),'valore ammesso'),
               (('budget','max_evaluations','10'),'impostazione non supportata'),
               (('obiettivo','planning_direction','backward'),'impostazione non supportata'),   # right code, wrong kind
               (('politica','not_a_setting','backward'),'impostazione non supportata'),       # right kind, wrong code
               (('politica','sequence_objective','setup'),'richiede planning_direction'),
               (('politica','sequence_objective','flow'),'valore ammesso'))
        for row,message in cases:
            with self.subTest(row=row),self.assertRaisesRegex(ValidationError,message):
                compile_tables(base_rows(row)).calculate()
        with self.assertRaisesRegex(ValidationError,'più di una volta'):
            compile_tables(base_rows(('politica','planning_direction','backward'),
                                     ('politica','planning_direction','backward')))

    def test_objective_policy_survives_a_real_xlsx_roundtrip(self):
        # The policy was first proven only on in-memory tables; this writes and rereads a real file.
        def tables(rows):
            value=bending_tables(unattended=True)
            value['order'][0]['due']='2026-10-29T15:00:00Z'
            value['objective']=rows
            return value
        policy=[dict(id='O1',kind='politica',code='planning_direction',value='backward',unit='scelta'),
                dict(id='O2',kind='politica',code='sequence_objective',value='setup',unit='scelta')]
        starts={}
        with tempfile.TemporaryDirectory() as folder:
            for label,rows in (('forward',[]),('backward',policy)):
                path=Path(folder)/f'{label}.xlsx'
                write_tables(path,tables(rows))
                scenario,result,evidence=load_workbook(path).calculate()
                self.assertEqual(verify_plan(scenario,result),())
                self.assertEqual(evidence['planning_direction'],label)
                self.assertEqual(evidence['machine_sequence'],['job:BEND'])
                starts[label]={b['stage']:b['segments'][0]['start'] for b in evidence['stage_bookings']}
        self.assertGreater(starts['backward']['run'],starts['forward']['run'])
        self.assertGreaterEqual(starts['backward']['setup'],starts['forward']['setup'])

    def test_original_source_age_and_scoped_corrections_survive(self):
        tables=bending_tables()
        tables['source'][0]['extracted_at']='2026-10-25T07:00:00Z'
        tables['domain_policy'][0]['max_age_minutes']=60
        compiled=compile_tables(tables)
        self.assertTrue(any(i.code=='stale_data' for i in compiled.built.issues))
        with self.assertRaisesRegex(ValidationError,'non pronto'):
            compiled.calculate()
        tables=bending_tables()
        tables['correction']=[dict(id='C1',dataset_id='other',entity='operation',record_key={'id':'BEND'},
            action='sostituisci',field='duration_seconds',new_value=10,status='attiva',reason='Test',actor='Planner',at='2026-10-26T07:00:00Z')]
        with self.assertRaisesRegex(ValidationError,'ambito correzione'):
            compile_tables(tables)

    def test_unattended_run_continues_after_human_calendar_ends(self):
        workbook=compile_tables(bending_tables(unattended=True))
        _,_,evidence=workbook.calculate()
        run=next(b for b in evidence['stage_bookings'] if b['stage']=='run')
        setup=next(b for b in evidence['stage_bookings'] if b['stage']=='setup')
        self.assertEqual(run['allocations'],[{'resource_id':'BENDER','occupancy_bps':10000}])
        self.assertEqual(run['segments'][0]['start'],'2026-10-26T09:00:00Z')
        self.assertEqual(run['segments'][-1]['end'],'2026-10-27T01:40:00Z')
        self.assertIn('PERSON-1',{a['resource_id'] for a in setup['allocations']})

    def test_percentages_are_capacity_not_speed(self):
        workbook=compile_tables(bending_tables(operator_quota=Decimal('0.25')))
        _,_,evidence=workbook.calculate()
        run=next(b for b in evidence['stage_bookings'] if b['stage']=='run')
        self.assertEqual(workbook.source.operations[0].duration_seconds,60000)
        self.assertEqual({a['occupancy_bps'] for a in run['allocations'] if a['resource_id'].startswith('PERSON')},{2500})

    def test_all_named_tables_and_explicit_zero_survive_xlsx(self):
        original=bending_tables(unattended=True)
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'book.xlsx';write_tables(p,original)
            tables,locations,_=read_tables(p)
            self.assertEqual(len(tables),len(catalogue()['tables']))
            self.assertEqual(tables['operation'][0]['run_operator_count'],0)
            self.assertEqual(tables['operation'][0]['run_operator_utilization'],0)
            self.assertEqual(tables['machine_state'][0]['state'],'null')
            via_xlsx=load_workbook(p)
            self.assertEqual(scenario_digest(via_xlsx.source),scenario_digest(compile_tables(original).source))
            self.assertEqual(via_xlsx.calculate()[1],compile_tables(original).calculate()[1])
            self.assertIn('!A8:',locations['operation'][0])

    def test_rejects_partial_resource_inputs_and_incoherent_zero(self):
        for change in ({'run_operator_count':0},{'run_operator_utilization':0}):
            tables=bending_tables();tables['operation'][0].update(change)
            with self.assertRaisesRegex(ValidationError,'numero e utilizzo'):
                compile_tables(tables)
        tables=bending_tables();del tables['operation'][0]['setup_operator_count']
        with self.assertRaisesRegex(ValidationError,'otto campi'):
            compile_tables(tables)

    def test_no_unverified_machine_state_or_staff_outside_shift_inference(self):
        tables=bending_tables();tables['machine_state'][0]['observed_at']='2026-10-25T07:00:00Z'
        with self.assertRaisesRegex(ValidationError,'inizio dello scenario'):
            compile_tables(tables)
        tables=bending_tables(operator_quota=Decimal('0.25'))
        tables['attendance_policy'][1]['outside_staff_shifts']=True
        with self.assertRaisesRegex(ValidationError,'fuori turno'):
            compile_tables(tables)

    def test_unknown_operational_constraints_do_not_disappear(self):
        for code in ('transport','material_reservation','attendance_window'):
            tables=bending_tables()
            # Call the boundary with a structurally valid transport declaration.
            if code=='transport': tables[code]=[dict(id='T')]
            elif code=='material_reservation':tables[code]=[dict(id='R')]
            else:tables[code]=[dict(operation_id='BEND',stage='run',id='A')]
            with self.assertRaisesRegex(ValidationError,'non ancora supportato'):
                compile_tables(tables)

    def test_version_empty_cells_and_true_subseconds_are_not_guessed(self):
        tables=bending_tables();tables['scenario'][0]['model_version']='v999'
        with self.assertRaisesRegex(ValidationError,'versione'):
            compile_tables(tables)
        self.assertEqual(seconds(Decimal(1)/60,'time'),1)
        with self.assertRaisesRegex(ValidationError,'secondi interi'):
            seconds('0.0001','time')
        with self.assertRaises(ValidationError):bps('25','percent')
        self.assertEqual(bps('0.25','percent'),2500)

    def test_corrections_apply_once_and_time_values_use_workbook_minutes(self):
        tables=bending_tables()
        tables['correction']=[dict(id='C1',dataset_id='workbook',entity='operation',record_key={'id':'BEND'},
            action='sostituisci',field='duration_seconds',new_value=10,status='attiva',reason='Synthetic override',
            actor='Planner',at='2026-10-26T07:00:00Z')]
        tables['scenario'][0]['input_basis']='base_con_correzioni'
        self.assertEqual(compile_tables(tables).source.operations[0].duration_seconds,600)
        tables['scenario'][0]['input_basis']='effettivi'
        self.assertEqual(compile_tables(tables).source.operations[0].duration_seconds,60000)

    def test_confirmed_material_ignore_requires_matching_policy_and_retains_shortage(self):
        tables=bending_tables(unattended=True)
        tables['uom']=[dict(id='PZ')]
        tables['item']=[dict(id='BLANK',base_uom='PZ')]
        tables['material_need']=[dict(operation_id='BEND',item_id='BLANK',quantity=200)]
        tables['domain_policy'].append(dict(domain='materials',coverage='gestito',allocation_order='due',
                                             material_policy='ignorati_con_regola'))
        with self.assertRaisesRegex(ValidationError,'non concordano'):
            compile_tables(tables)
        tables['guidance']=[dict(id='G1',action='ignore_material',status='confermata',confirmed_by='Planner',
            confirmed_at='2026-10-26T07:00:00Z')]
        loaded=compile_tables(tables)
        self.assertTrue(any(i.code=='material_shortage' for i in loaded.built.issues))
        self.assertFalse(loaded.calculate()[1].unscheduled)

    def test_unconsumed_stage_rows_and_repeated_setup_are_rejected(self):
        tables=bending_tables()
        tables['stage_candidate'].append(dict(operation_id='BEND',stage='run',requirement_id='missing',resource_id='BENDER'))
        with self.assertRaisesRegex(ValidationError,'requisito di stadio inesistente'):
            compile_tables(tables)
        tables=bending_tables();tables['setup_phase'].append(dict(operation_id='UNKNOWN'))
        with self.assertRaisesRegex(ValidationError,'inesistente'):
            compile_tables(tables)
        tables=bending_tables();tables['operation'][0]['setup_occurrences']=2
        with self.assertRaisesRegex(ValidationError,'ripetuti'):
            compile_tables(tables)


if __name__=='__main__':unittest.main()
