"""Generate synthetic, complete XLSX input for the bending acceptance cases."""
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
import argparse
import json

from planning_factory.adapters.json_io import primitive
from planning_factory.adapters.planning_workbook import tables_from_records
from planning_factory.adapters.workbook_io import write_tables


def bending_tables(*, unattended=False, operator_quota=Decimal(1)):
    records = {
        'calendar': [{'id':'machine','timezone':'Europe/Rome'}, {'id':'people','timezone':'Europe/Rome'}],
        'calendar_shift': [dict(calendar_id='machine',weekday=d,start='00:00:00',end='00:00:00') for d in range(7)] +
                          [dict(calendar_id='people',weekday=d,start='08:00:00',end='17:00:00') for d in range(5)],
        'resource': [{'id':'BENDER','calendar_id':'machine','capacity':1,'kind':'machine'},
                     {'id':'PERSON-1','calendar_id':'people','capacity':1,'kind':'operator'},
                     {'id':'PERSON-2','calendar_id':'people','capacity':1,'kind':'operator'}],
        'order': [{'id':'DEMO-ORDER'}],
        'operation': [{'id':'BEND','order_id':'DEMO-ORDER','duration_seconds':60000,'setup_seconds':7200,
                       'quantity':'200','splittable':True,'mode':'forward'}],
        'operation_demand': [{'operation_id':'BEND','alternative':0,'resource_id':rid,'units':1}
                             for rid in ('BENDER','PERSON-1','PERSON-2')],
        'profile':[{'id':'production','mode':'forward','auto_backward_days':10,
                    'priority_weight':10000,'due_weight':1,'customer_weight':1}],
    }
    tables = tables_from_records(records,scenario_id='DEMO-BENDING',
        start='2026-10-26T07:00:00Z',end='2026-10-30T07:00:00Z',
        extracted_at='2026-10-26T07:00:00Z',profile_id='production')
    tables['scenario'][0]['timezone']='Europe/Rome'
    tables['source'][0]['data_nature']='simulati'
    tables['operation'][0].update(
        work_time_mode='per_pezzo', declared_work_minutes=Decimal(5),
        setup_frequency='per_lotto',setup_occurrences=1,
        setup_machine_count=1,setup_machine_utilization=Decimal(1),
        setup_operator_count=1,setup_operator_utilization=Decimal(1),
        run_machine_count=1,run_machine_utilization=Decimal(1),
        run_operator_count=0 if unattended else 2,
        run_operator_utilization=Decimal(0) if unattended else operator_quota)
    tables['setup_phase']=[dict(operation_id='BEND',machine_id='BENDER',
        target_state=None,setup_minutes=Decimal(120),setup_locked=False,execution_rank=1)]
    reqs=[('setup','machine',['BENDER']),('setup','operator',['PERSON-1']),('run','machine',['BENDER'])]
    if not unattended:
        reqs.append(('run','operator',['PERSON-1','PERSON-2']))
    tables['stage_requirement']=[dict(operation_id='BEND',stage=s,requirement_id=k,kind=k) for s,k,ids in reqs]
    tables['stage_candidate']=[dict(operation_id='BEND',stage=s,requirement_id=k,resource_id=rid)
                                for s,k,ids in reqs for rid in ids]
    tables['machine_state']=[dict(resource_id='BENDER',state=None,observed_at='2026-10-26T07:00:00Z')]
    mode='assente' if unattended else 'continuo' if operator_quota==1 else 'quota_media'
    tables['attendance_policy']=[dict(operation_id='BEND',stage='setup',mode='continuo',outside_staff_shifts=False),
        dict(operation_id='BEND',stage='run',mode=mode,outside_staff_shifts=unattended)]
    return tables


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    folder=Path(args.out);folder.mkdir(parents=True,exist_ok=True)
    for name,kwargs in [('bending',{}),('unattended',{'unattended':True}),
                        ('fractional',{'operator_quota':Decimal('0.25')})]:
        tables=bending_tables(**kwargs)
        write_tables(folder/f'{name}.xlsx',tables)
        (folder/f'{name}.json').write_text(json.dumps(tables,ensure_ascii=False,indent=2,
            default=lambda n:format(n,'f'))+'\n',encoding='utf-8')
    print('Generated three synthetic workbook scenarios')
