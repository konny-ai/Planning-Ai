"""Build an auditable v0.3 stage specification from explicit canonical-like CSV.

No database or network access; never deduces MES state from planning counters.
The operator supplies all stock/machine facts, sequence and rank. A sidecar
records hashes and machine observation references; it is NOT certification.
"""
import argparse
import csv
from datetime import UTC
from hashlib import sha256
import json
from pathlib import Path
import sys

from planning_factory.adapters.json_io import loads, scenario_digest, timestamp
from planning_factory.adapters.staged_file import load_staged_file
from planning_factory.domain.validation import ValidationError, compile_tasks

MAX_TABLE_BYTES=8*1024*1024
MAX_ROWS=10000
REQUIRED={
    'resources.csv': ('id','kind'),
    'phases.csv': ('id','machine_id','target_state','setup_seconds','setup_locked'),
    'requirements.csv': ('phase_id','stage','kind','count','occupancy_bps','candidate_ids'),
    'machine_states.csv': ('machine_id','state_id','observed_at','source_ref'),
    'sequence.csv': ('position','task_id'),
    'priority.csv': ('position','task_id'),
    'supplies.csv': ('id','item_id','quantity','available_at'),
    'needs.csv': ('task_id','item_id','quantity'),
}
OPTIONAL={
    'commitments.csv': ('supply_id','quantity'),
    'frozen_bookings.csv': ('phase_id','stage','start','end','resource_id','occupancy_bps'),
    'observations.csv': ('machine_id','observed_at','state_id','source_ref','idle_confirmed'),
    'paused_progresses.csv': ('task_id','machine_id','original_seconds','worked_seconds',
                             'remaining_seconds','paused_at','source_ref'),
    'active_holds.csv': ('resource_id','observed_at','reserved_until','source_ref','occupying_task_id'),
}


def read_table(folder: Path, name: str, columns, *, required: bool):
    path=folder/name
    if not path.exists():
        if required:
            raise ValidationError([f'missing mandatory CSV {name}'])
        return []
    if not path.is_file() or path.stat().st_size>MAX_TABLE_BYTES:
        raise ValidationError([f'{name}: file must be under 8MiB'])
    try:
        with path.open('r',encoding='utf-8-sig',newline='') as stream:
            reader=csv.DictReader(stream)
            if reader.fieldnames is None or len(set(reader.fieldnames))!=len(reader.fieldnames) or (
                    set(reader.fieldnames)!=set(columns)):
                raise ValidationError([f'{name}: expected exactly columns {columns}'])
            result=[]
            for row in reader:
                if len(result)>=MAX_ROWS:
                    raise ValidationError([f'{name}: too many rows'])
                if None in row:
                    raise ValidationError([f'{name}: excess CSV fields'])
                if any(value is None for value in row.values()):
                    raise ValidationError([f'{name}: missing CSV cell'])
                result.append(dict(row))
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValidationError([f'{name}: cannot read CSV: {exc}']) from None
    return result


def text(value, label):
    if not isinstance(value,str) or not value.strip() or len(value)>256:
        raise ValidationError([f'{label}: required nonempty text up to 256 chars'])
    return value.strip()


def natural(value,label,*,positive=False):
    if not isinstance(value,str) or not value.isascii() or not value.isdecimal():
        raise ValidationError([f'{label}: integer digits required'])
    n=int(value)
    if n<0 or (positive and n==0):
        raise ValidationError([f'{label}: positive integer required'])
    return n


def state(value):
    if value=='null':
        return None
    return text(value,'setup state')


def instant_text(value,label):
    t=timestamp(text(value,label))
    if t is None or t.microsecond:
        raise ValidationError([f'{label}: second precision and explicit timezone required'])
    return t.astimezone(UTC).isoformat().replace('+00:00','Z')


def ordered_ids(rows, name):
    indexed={}
    values=set()
    for item in rows:
        position=natural(item['position'],name+' position')
        value=text(item['task_id'],name+' task')
        if position in indexed or value in values:
            raise ValidationError([f'{name}: duplicate task or ordinal'])
        indexed[position]=value
        values.add(value)
    if sorted(indexed)!=list(range(1,len(indexed)+1)):
        raise ValidationError([f'{name}: ordinal positions must be consecutive 1..N'])
    return [indexed[key] for key in sorted(indexed)]


def transform(source, folder: Path, *, mode: str, stock_accounting: str,
              residual_materials_confirmed: bool):
    folder=Path(folder).resolve(strict=True)
    if not folder.is_dir():
        raise ValidationError(['CSV source must be a directory'])
    expected=set(REQUIRED)|set(OPTIONAL)
    extras={p.name for p in folder.glob('*.csv')}-expected
    if extras:
        raise ValidationError([f'unrecognized CSV tables: {sorted(extras)}'])
    raw={}
    used=[]
    for filename,fields in REQUIRED.items():
        raw[filename]=read_table(folder,filename,fields,required=True)
        used.append(filename)
    for filename,fields in OPTIONAL.items():
        path=folder/filename
        if path.exists():
            raw[filename]=read_table(folder,filename,fields,required=False)
            used.append(filename)
        else:
            raw[filename]=[]
    if stock_accounting not in ('net','gross'):
        raise ValidationError(['stock-accounting must be net or gross'])
    if stock_accounting=='net' and (folder/'commitments.csv').exists():
        raise ValidationError(['net stock forbids commitments.csv'])
    if stock_accounting=='gross' and not (folder/'commitments.csv').exists():
        raise ValidationError(['gross stock requires commitments.csv (header may be empty)'])
    if mode not in ('normal','paused','holds'):
        raise ValidationError(['mode must be normal, paused or holds'])
    incompatible={
        'normal': ('observations.csv','paused_progresses.csv','active_holds.csv'),
        'paused': ('active_holds.csv',),
        'holds': ('observations.csv','paused_progresses.csv'),
    }
    for filename in incompatible[mode]:
        if (folder/filename).exists():
            raise ValidationError([f'{mode}: cannot include {filename}'])
    if mode=='paused' and (not raw['observations.csv'] or not raw['paused_progresses.csv']
                           or residual_materials_confirmed is not True):
        raise ValidationError(['paused WIP requires observations, progress and explicit residual material confirmation'])
    if mode=='holds' and not raw['active_holds.csv']:
        raise ValidationError(['holds requires declared current active resource occupations'])
    if mode!='paused' and residual_materials_confirmed:
        raise ValidationError(['residual confirmation is valid only for paused mode'])

    task_ids=set(compile_tasks(source)[0])
    sequence=ordered_ids(raw['sequence.csv'],'sequence')
    priority=ordered_ids(raw['priority.csv'],'priority')
    if set(sequence)!=task_ids or len(sequence)!=len(task_ids) or (
            set(priority)!=task_ids or len(priority)!=len(task_ids)):
        raise ValidationError(['sequence and priority must cover exact compiled Scenario task IDs'])

    resources=[{'id':text(x['id'],'resource'),'kind':text(x['kind'],'resource kind')}
               for x in raw['resources.csv']]
    machines={x['id'] for x in resources if x['kind']=='machine'}
    if not machines:
        raise ValidationError(['at least one machine must be explicitly declared'])
    if len({x['id'] for x in resources})!=len(resources):
        raise ValidationError(['resources.csv: duplicate resource ID'])
    machine_states={}
    source_refs={}
    as_of=source.start.astimezone(UTC).isoformat().replace('+00:00','Z')
    for row in raw['machine_states.csv']:
        mid=text(row['machine_id'],'machine ID')
        if mid in machine_states:
            raise ValidationError([f'{mid}: duplicate machine as-of state'])
        observed=instant_text(row['observed_at'],'machine state as-of')
        if observed!=as_of:
            raise ValidationError([f'{mid}: machine state timestamp must equal Scenario.start'])
        machine_states[mid]=state(row['state_id'])
        source_refs[mid]=text(row['source_ref'],'machine state evidence reference')
    if set(machine_states)!=machines:
        raise ValidationError(['machine_states.csv must attest one state for every declared machine'])
    phases={}
    for row in raw['phases.csv']:
        pid=text(row['id'],'phase')
        if pid in phases:
            raise ValidationError([f'duplicate phase {pid}'])
        locked=row['setup_locked'].lower()
        if locked not in ('true','false'):
            raise ValidationError([f'{pid}: setup_locked must be explicit true/false'])
        phases[pid]={
            'id':pid,
            'machine_id':text(row['machine_id'],'machine'),
            'target_state':state(row['target_state']),
            'setup_seconds':natural(row['setup_seconds'],'setup seconds'),
            'setup_locked':locked=='true',
            'setup_requirements':[],
            'run_requirements':[],
        }
    if set(phases)!=task_ids:
        raise ValidationError(['setup phase IDs must match every compiled Scenario task'])
    for row in raw['requirements.csv']:
        pid=text(row['phase_id'],'requirement phase')
        if pid not in phases:
            raise ValidationError([f'{pid}: unknown requirement phase'])
        stage=text(row['stage'],'requirement stage')
        if stage not in ('setup','run'):
            raise ValidationError([f'{pid}: stage must be setup/run'])
        candidates=[text(k,'resource candidate') for k in row['candidate_ids'].split('|')]
        if len(set(candidates))!=len(candidates):
            raise ValidationError([f'{pid}: duplicate resource candidates'])
        phases[pid][stage+'_requirements'].append({
            'kind':text(row['kind'],'requirement kind'),
            'count':natural(row['count'],'requirement count',positive=True),
            'occupancy_bps':natural(row['occupancy_bps'],'occupancy bps',positive=True),
            'candidate_ids':candidates,
        })

    stock={'accounting':stock_accounting,'supplies':[{
        'id':text(row['id'],'supply'), 'item_id':text(row['item_id'],'item'),
        'quantity':text(row['quantity'],'supply quantity'),
        'available_at':instant_text(row['available_at'],'supply date'),
    } for row in raw['supplies.csv']]}
    if stock_accounting=='gross':
        stock['external_commitments']=[{
            'supply_id':text(row['supply_id'],'reserved supply'),
            'quantity':text(row['quantity'],'committed quantity'),
        } for row in raw['commitments.csv']]

    bookings={}
    for row in raw['frozen_bookings.csv']:
        key=(text(row['phase_id'],'frozen phase'),text(row['stage'],'frozen stage'))
        interval=(instant_text(row['start'],'frozen start'),
                  instant_text(row['end'],'frozen end'))
        if key not in bookings:
            bookings[key]={
                'phase_id':key[0],'stage':key[1],
                'segments':[{'start':interval[0],'end':interval[1]}],
                'allocations':[],
            }
        elif bookings[key]['segments'][0] != {'start':interval[0],'end':interval[1]}:
            raise ValidationError([f'{key}: a frozen stage must have exactly one intact shared interval'])
        if any(x['resource_id']==row['resource_id'] for x in bookings[key]['allocations']):
            raise ValidationError([f'{key}: duplicate frozen resource'])
        bookings[key]['allocations'].append({
            'resource_id':text(row['resource_id'],'frozen resource'),
            'occupancy_bps':natural(row['occupancy_bps'],'frozen occupancy',positive=True),
        })
    def attested_rows(filename, columns):
        return [{name:(instant_text(row[name],name) if name in ('observed_at','paused_at','reserved_until')
                         else state(row[name]) if name=='state_id'
                         else natural(row[name],name,positive=True) if name in (
                             'original_seconds','worked_seconds','remaining_seconds')
                         else row[name].lower()=='true' if name=='idle_confirmed' and
                              row[name].lower() in ('true','false')
                         else text(row[name],name))
                 for name in columns} for row in raw[filename]]
    observations=attested_rows('observations.csv',OPTIONAL['observations.csv'])
    for row in raw['observations.csv']:
        if row['idle_confirmed'].lower() not in ('true','false'):
            raise ValidationError(['idle_confirmed must be an explicit true/false'])
    progresses=attested_rows('paused_progresses.csv',OPTIONAL['paused_progresses.csv'])
    holds=attested_rows('active_holds.csv',OPTIONAL['active_holds.csv'])
    request={
        'schema_version':1,
        'source_scenario_digest':scenario_digest(source),
        'mode':mode,
        'setup_contract':{
            'schema_version':1,
            'resources':resources,
            'initial_machine_states':machine_states,
            'phases':list(phases.values()),
        },
        'machine_sequence':sequence,
        'task_priority':priority,
        'stock':stock,
        'needs':[{
            'task_id':text(row['task_id'],'need task'),
            'item_id':text(row['item_id'],'need item'),
            'quantity':text(row['quantity'],'need quantity'),
        } for row in raw['needs.csv']],
        'frozen_bookings':list(bookings.values()),
        'observations':observations,
        'paused_progresses':progresses,
        'active_holds':holds,
        'residual_material_requirements_confirmed':residual_materials_confirmed,
    }
    provenance={
        'version':1,'read_only':True,
        'snapshot_at':as_of,
        'scenario_digest':scenario_digest(source),
        'mode':mode,
        'accounting':stock_accounting,
        'input_csv_sha256':{
            name:sha256((folder/name).read_bytes()).hexdigest()
            for name in sorted(used)
        },
        'machine_state_sources':source_refs,
        'warning':'Hashes establish file identity, not MES/stock certification.',
    }
    return request,provenance


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scenario',required=True,type=Path)
    parser.add_argument('--tables',required=True,type=Path)
    parser.add_argument('--out',required=True,type=Path)
    parser.add_argument('--stock-accounting',choices=('net','gross'),required=True)
    parser.add_argument('--mode',choices=('normal','paused','holds'),default='normal')
    parser.add_argument('--residual-materials-confirmed',action='store_true')
    args=parser.parse_args(argv)
    out=args.out.expanduser().resolve()
    if out.suffix.lower()!='.json':
        parser.error('v0.3 output must have a .json suffix for strict file validation')
    provenance=out.with_name(out.stem+'.sources.json')
    if out==args.scenario.expanduser().resolve() or out.exists() or provenance.exists():
        parser.error('output files must be new and must not overwrite the input')
    try:
        scenario=loads(args.scenario.read_text(encoding='utf-8'))
        request,manifest=transform(scenario,args.tables,mode=args.mode,
                                   stock_accounting=args.stock_accounting,
                                   residual_materials_confirmed=args.residual_materials_confirmed)
        if len(json.dumps(request).encode('utf-8'))>1024*1024:
            raise ValidationError(['v0.3 request exceeds the bounded 1MiB JSON contract'])
        out.parent.mkdir(parents=True,exist_ok=True)
        temporary=out.with_name(out.stem+'.pending.json')
        if temporary.exists():
            raise ValidationError(['temporary output exists; refuse overwrite'])
        try:
            temporary.write_text(json.dumps(request,ensure_ascii=False,indent=2,sort_keys=True)+'\n',
                                 encoding='utf-8')
            load_staged_file(temporary,scenario)
            temporary.replace(out)
            provenance.write_text(json.dumps(manifest,ensure_ascii=False,indent=2,sort_keys=True)+'\n',
                                  encoding='utf-8')
        finally:
            if temporary.exists():
                temporary.unlink()
    except (ValueError,OSError,ValidationError) as exc:
        parser.exit(2,f'file-only stage import rejected: {exc}\n')
    print('v0.3 specification:',out)
    print('source manifest:',provenance)
    print('No ERP or SQL Server accessed. This is not a certified production snapshot.')


if __name__=='__main__':
    main()
