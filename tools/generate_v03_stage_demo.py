"""Create a wholly synthetic two-stage workshop for the private web preview.

No ERP connection, no banco A data, no downloading a backup. The paired
source_scenario_digest is computed using the canonical JSON v1 serializer.
Run explicitly in a Codespace; this module is not executed by CI by default.
"""
import argparse
import csv
from datetime import UTC, datetime, time
import json
from pathlib import Path

from planning_factory.adapters.json_io import dumps, scenario_digest
from planning_factory.domain.models import (
    Alternative, Calendar, Demand, Operation, Resource, Scenario, Shift,
)


def make_example():
    calendar=Calendar(
        'single-shift','UTC',
        tuple(Shift(day,time(8),time(12)) for day in range(5))+
        tuple(Shift(day,time(13),time(17)) for day in range(5)),
    )
    resources=(
        Resource('WORKCELL_1','single-shift',kind='machine'),
        Resource('SETUP_OPERATOR','single-shift',kind='operator'),
        Resource('SETUP_TOOL','single-shift',kind='tool'),
    )
    operations=(
        Operation('CUT-A','ORDER-DEMO-1',3600,
                  (Alternative((Demand('WORKCELL_1'),)),),
                  due=datetime(2026,10,20,16,tzinfo=UTC)),
        Operation('CUT-B','ORDER-DEMO-2',3600,
                  (Alternative((Demand('WORKCELL_1'),)),),
                  due=datetime(2026,10,20,16,tzinfo=UTC)),
    )
    scenario=Scenario(
        'SYNTHETIC-V03-WORKSHOP',
        datetime(2026,10,19,0,tzinfo=UTC),
        datetime(2026,10,22,0,tzinfo=UTC),
        (calendar,),resources,operations,
    )
    phase_ids=('job:CUT-A','job:CUT-B')
    machine_requirement={
        'kind':'machine','count':1,'occupancy_bps':10000,
        'candidate_ids':['WORKCELL_1'],
    }
    operator_requirement={
        'kind':'operator','count':1,'occupancy_bps':5000,
        'candidate_ids':['SETUP_OPERATOR'],
    }
    tool_requirement={
        'kind':'tool','count':1,'occupancy_bps':10000,
        'candidate_ids':['SETUP_TOOL'],
    }
    phases=[
        {'id':pid,'machine_id':'WORKCELL_1','target_state':'PROGRAM_BLUE',
         'setup_seconds':1800,
         'setup_requirements':[
            machine_requirement,operator_requirement,tool_requirement],
         'run_requirements':[
            machine_requirement,{'kind':'operator','count':1,
                'occupancy_bps':2500,'candidate_ids':['SETUP_OPERATOR']}],
        } for pid in phase_ids
    ]
    specification={
        'schema_version':1,
        'source_scenario_digest':scenario_digest(scenario),
        'mode':'normal',
        'setup_contract':{
            'schema_version':1,
            'resources':[{'id':r.id,'kind':r.kind} for r in resources],
            'initial_machine_states':{'WORKCELL_1':'PROGRAM_RED'},
            'phases':phases,
        },
        'machine_sequence':list(phase_ids),
        'task_priority':list(phase_ids),
        'stock':{
            'accounting':'net',
            'supplies':[{
                'id':'SYNTH-SUPPLY-001',
                'item_id':'SYNTH-PART-001',
                'quantity':'5',
                'available_at':'2026-10-19T09:00:00Z',
            }],
        },
        'needs':[
            {'task_id':pid,'item_id':'SYNTH-PART-001','quantity':'2'}
            for pid in phase_ids
        ],
        'frozen_bookings':[],
    }
    return scenario,specification


def write_example_csv(folder: Path, scenario, request):
    """Generate full synthetic CSV input to exercise the strict importer."""
    tables={
        'resources.csv':request['setup_contract']['resources'],
        'phases.csv':[
            {**phase,
             'target_state':phase['target_state'] or 'null',
             'setup_locked':'false'}
            for phase in request['setup_contract']['phases']
        ],
        'requirements.csv':[
            {'phase_id':phase['id'], 'stage':stage,
             'kind':req['kind'], 'count':req['count'],
             'occupancy_bps':req['occupancy_bps'],
             'candidate_ids':'|'.join(req['candidate_ids'])}
            for phase in request['setup_contract']['phases']
            for stage in ('setup','run')
            for req in phase[stage+'_requirements']
        ],
        'machine_states.csv':[
            {'machine_id':mid,'state_id':value or 'null',
             'observed_at':scenario.start.isoformat(),
             'source_ref':'SYNTHETIC:INITIAL_MACHINE_STATE:'+mid}
            for mid,value in request['setup_contract']['initial_machine_states'].items()
        ],
        'sequence.csv':[
            {'position':i,'task_id':tid}
            for i,tid in enumerate(request['machine_sequence'],1)
        ],
        'priority.csv':[
            {'position':i,'task_id':tid}
            for i,tid in enumerate(request['task_priority'],1)
        ],
        'supplies.csv':request['stock']['supplies'],
        'needs.csv':request['needs'],
    }
    # Standalone scripts launched as 'python tools/...' do not necessarily
    # have the repository root on sys.path; keep these CSV columns local.
    columns_by_table={
        'resources.csv':('id','kind'),
        'phases.csv':('id','machine_id','target_state','setup_seconds','setup_locked'),
        'requirements.csv':('phase_id','stage','kind','count','occupancy_bps','candidate_ids'),
        'machine_states.csv':('machine_id','state_id','observed_at','source_ref'),
        'sequence.csv':('position','task_id'),
        'priority.csv':('position','task_id'),
        'supplies.csv':('id','item_id','quantity','available_at'),
        'needs.csv':('task_id','item_id','quantity'),
    }
    folder.mkdir(parents=True,exist_ok=True)
    for name,rows in tables.items():
        path=folder/name
        if path.exists():
            raise FileExistsError(f'{path}: refuse to overwrite existing table')
        with path.open('w',encoding='utf-8',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=columns_by_table[name],
                                  extrasaction='ignore',lineterminator='\n')
            writer.writeheader()
            for row in rows:
                writer.writerow(row)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True,
                        help='Dedicated output directory for two SYNTHETIC JSON files')
    parser.add_argument('--csv',action='store_true',
                        help='Also generate all source CSV tables for the strict v0.3 importer')
    args=parser.parse_args(argv)
    folder=args.out.expanduser().resolve()
    src,spec=folder/'scenario.json',folder/'stage-spec.json'
    if src.exists() or spec.exists():
        parser.error('synthetic files already exist; choose a new directory')
    folder.mkdir(parents=True,exist_ok=True)
    scenario,request=make_example()
    src.write_text(dumps(scenario),encoding='utf-8')
    spec.write_text(json.dumps(request,indent=2,sort_keys=True,ensure_ascii=False)+'\n',
                    encoding='utf-8')
    if args.csv:
        write_example_csv(folder/'tables',scenario,request)
        print('Synthetic CSV source:',folder/'tables')
    print('Synthetic file-only workshop created. No ERP/banco A data.')
    print('Scenario:',src)
    print('Spec:',spec)
    print('Digest:',request['source_scenario_digest'])


if __name__=='__main__':
    main()
