"""Read-only Factory extraction -> canonical snapshot -> self-contained workbook tables.

Runs in a private worker directory. CSV/JSON here are transient connector
artifacts; the planning server never consumes them as its active source.
"""
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import csv
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from zoneinfo import ZoneInfo

from planning_factory.adapters.json_io import primitive, timestamp, dumps
from planning_factory.adapters.workbook_migration import snapshot_tables
from planning_factory.canonical.binding import load_binding
from planning_factory.canonical.builder import read_sources, build_scenario
from planning_factory.canonical.entities import ENTITIES
from planning_factory import connectors
from planning_factory.domain.validation import ValidationError
from planning_factory.adapters import factory_views


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def load_tool(path,name):
    spec=importlib.util.spec_from_file_location(name,path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def run(argv,env=None):
    print('Esecuzione',Path(argv[1]).name,flush=True)
    subprocess.run(argv,env=env,check=True,stdin=subprocess.DEVNULL,timeout=1800)


def tables_from_binding(binding_path,raw_path,descriptions_path,*,now,material_items=None):
    binding=load_binding(Path(binding_path),connectors.available())
    batches=read_sources(binding,connectors.create,timestamp(now))
    built=build_scenario(binding,batches,timestamp(now),keep_records=True)
    if built.scenario is None:
        raise ValidationError([f'{i.code}: {i.detail}' for i in built.issues if i.severity=='error'])
    records={e:[dict(r.values) for r in built.records.get(e,())] for e in ENTITIES}
    snapshot=dict(records=records,manifest=built.manifest,descriptions=read_json(descriptions_path),
        built_at=now,corrections=[],company_rules=[],options={d.domain:{k:getattr(d,k)
            for k in ('residual_policy','allocation_order','max_age_minutes','include_statuses')
            if getattr(d,k) is not None} for d in binding.domains})
    raw=read_json(raw_path)
    ids={r['id'] for r in records['operation']}
    parameters=[];originals={}
    for row in raw['operations']:
        oid='/'.join(str(row.get(k)) for k in ('orderId','lotId','phaseId'))
        if oid not in ids:continue
        target={'id':oid};original={}
        for stage in ('setup','run'):
            for kind,title in (('machine','Machine'),('operator','Operator')):
                count=row[stage+title+'s'];pct=row[stage+title+'Pct']
                if count is None or pct is None:
                    raise ValidationError([f'{oid}: numero e percentuale {stage}/{kind} mancanti in Factory'])
                target[f'{stage}_{kind}_count']=count
                target[f'{stage}_{kind}_utilization']=Decimal(str(pct))/100 if count else 0
                original[stage+title+'s']=count;original[stage+title+'Pct']=pct
        parameters.append(target);originals[oid]=original
    if len(parameters)!=len(ids):raise ValidationError(['mappatura risorse Factory incompleta'])
    needed={r['item_id'] for e in ('material_need','material_supply') for r in records[e]}
    units=defaultdict(set);labels={}
    for row in material_items if material_items is not None else raw['lotComponents']:
        if row['partCode'] in needed and row.get('uom'):
            units[row['partCode']].add(row['uom']);labels[row['partCode']]=row.get('description')
    items=[]
    for item in sorted(needed):
        if len(units[item])!=1:raise ValidationError([f'{item}: unita materiale assente o ambigua'])
        items.append(dict(id=item,base_uom=next(iter(units[item])),
            **({'description':labels[item]} if labels.get(item) else {})))
    tables=snapshot_tables(snapshot,timezone='Europe/Rome',material_items=items,resource_parameters=parameters)
    for oid,original in originals.items():
        tables['provenance'].append(dict(id='RESOURCES:'+oid,entity='operation',record_key={'id':oid},
            source_id='migration:routings',original_value=original,
            transformation='risorse_factory_bundle_comune',data_nature='dichiarato'))
    return tables


def real_materials(scenario,extract,out):
    """Only declared stock and dated purchase orders. Missing supplies stay missing."""
    def rows(name):
        with (extract/name/'1.csv').open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
    def supplement(name):
        with (extract/name).open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
    items=supplement('material-items.csv')
    units={r['partCode']:r['uom'] for r in items}
    if len(units)!=len(items):raise ValidationError(['Anagrafica unita materiali duplicata'])
    nests={n['id'].removeprefix('NEST-'):n for n in scenario['nestings']}
    sheets=defaultdict(dict)
    seen_slots=set()
    for r in rows('12_nesting_lamiere'):
        if r['workplanId'] in nests and r.get('sheetPartCode'):
            key=(r['workplanId'],r['sheetSlot'])
            if key in seen_slots:raise ValidationError(['Riga lamiera nesting duplicata nella vista SQL'])
            seen_slots.add(key)
            q=Decimal(r['sheetsRequired'] or '0')
            if q<=0:raise ValidationError(['quantita lamiere nesting mancante o non positiva'])
            unit=units.get(r['sheetPartCode'])
            if unit=='KG':
                weight=Decimal(r.get('sheetWeightKg') or '0')
                if weight<=0:raise ValidationError(['Peso lamiera dichiarato necessario per il materiale in KG'])
                q*=weight
            elif unit not in ('NR','PZ'):
                raise ValidationError([f"{r['sheetPartCode']}: conversione da lamiere a {unit!r} non dichiarata"])
            current=sheets[r['workplanId']].get(r['sheetPartCode'],Decimal(0))
            sheets[r['workplanId']][r['sheetPartCode']]=current+q
    needed={i for s in sheets.values() for i in s}
    # QTLOC is physical stock, PAIMP is committed stock. Use the free balance;
    # reservations are retained as source evidence, not added back implicitly.
    stock={}
    for r in rows('13_materiali_giacenze_arrivi'):
        if r.get('stockAvailable') in (None,'') or r.get('engaged') in (None,''):
            raise ValidationError(['La vista giacenze deve dichiarare giacenza fisica e quantita impegnata'])
        stock[r['sheetPartCode']]=max(Decimal(0),Decimal(r['stockAvailable'])-Decimal(r['engaged']))
    supplies=[];needs=[]
    start=timestamp(scenario['start'])
    for item in sorted(needed):
        if stock.get(item,0)>0:
            supplies.append(dict(id='STK-'+item,item_id=item,quantity=str(stock[item]),
                available_at=start.isoformat(),kind='stock',reference='Factory: giacenza fisica meno impegni dichiarati, senza riaccredito riserve'))
    residuals={}
    for r in supplement('purchase-residuals.csv'):
        key=(r['partCode'],r['purchaseOrderCode'].strip(),r['rowNo'])
        if key in residuals:raise ValidationError(['Ordine fornitore duplicato nella vista SQL'])
        residuals[key]=r
    with (extract/'13_materiali_giacenze_arrivi/2.csv').open(encoding='utf-8-sig',newline='') as f:
        for r in csv.DictReader(f):
            if r.get('partCode') not in needed or not r.get('promisedDate'):continue
            when=datetime.fromisoformat(r['promisedDate'])
            if when.tzinfo is None:when=when.replace(tzinfo=ZoneInfo('Europe/Rome'))
            key=(r['partCode'],r['purchaseOrderCode'].strip(),r['rowNo'])
            detail=residuals.get(key)
            if detail is None or detail['uom']!=units[r['partCode']] or detail['deliveredQty']=='':
                raise ValidationError(['Quantita residua o unita ordine fornitore mancante o incoerente'])
            quantity=max(Decimal(0),Decimal(detail['orderedQty'])-Decimal(detail['deliveredQty']))
            if quantity>0:
                supplies.append(dict(id=f"PO-{r['purchaseOrderCode']}-{r['rowNo']}",item_id=r['partCode'],
                    quantity=str(quantity),available_at=max(start,when).isoformat(),kind='purchase_order',
                    reference=f"Factory: ordine {r['purchaseOrderCode']}, residuo ordinato meno consegnato"))
    for nid,items in sheets.items():
        for item,q in sorted(items.items()):
            needs.append(dict(operation_id=nests[nid]['operation_ids'][0],item_id=item,quantity=str(q)))
    for name,fields,data in (
        ('material_supply',('id','item_id','quantity','available_at','kind','reference'),supplies),
        ('material_need',('operation_id','item_id','quantity'),needs)):
        with (out/(name+'.csv')).open('w',encoding='utf-8',newline='') as f:
            writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader();writer.writerows(data)
    print('Materiali effettivi:',len(supplies),'disponibilita,',len(needs),'fabbisogni; nessun arrivo simulato',flush=True)


def extract_tables(repo,folder,config,start,*,progress='quantity'):
    repo,folder=Path(repo),Path(folder)
    cliente=repo/'integrations/cliente-export';tools=repo/'integrations/factory-export/tools'
    extract=folder/'extract';raw=folder/'raw';prepared=folder/'prepared';engine=folder/'engine';canonical=folder/'canonical'
    for p in (extract,raw,prepared,engine,canonical):p.mkdir(parents=True,exist_ok=True)
    env=dict(os.environ,CLIENTE_EXTRACT_OUT=str(extract),PYTHONIOENCODING='utf-8')
    # Password is inherited through a private worker environment, never argv or a file.
    if config['user']:env['CLIENTE_USER']=config['user']
    else:env.pop('CLIENTE_USER',None);env.pop('CLIENTE_PASSWORD',None)
    exporter=load_tool(tools/'export_factory_scenario.py','sync_interchange')
    def reading(message):
        (folder.parent/'phase.json').write_text(json.dumps(dict(phase=message)),encoding='utf-8')
        print(message,flush=True)
    fetched,coverage=factory_views.extract(config,folder,start,exporter,
        password=os.environ.get('CLIENTE_PASSWORD',''),on_progress=reading)
    sys.path.insert(0,str(cliente))
    builder=load_tool(cliente/'build_scenario_cliente.py','sync_cliente_builder');builder.OUT=str(extract)
    builder.prepara(str(raw/'scenario.json'),str(prepared))
    run([sys.executable,str(tools/'to_engine_v02.py'),str(prepared/'scenario.json'),'--out',str(engine),
         '--subcontract-policy','anchored','--progress-policy','none'],env)
    nested=engine/'with-nesting.json'
    builder.nesting(str(engine/'engine-scenario.json'),str(nested),apply_material_release=False)
    scenario=read_json(nested)
    real_materials(scenario,extract,canonical)
    from planning_factory.adapters.json_io import scenario_from_dict
    from planning_factory.canonical.decompose import scenario_to_bundle
    now=datetime.now(UTC).replace(microsecond=0).isoformat()
    bundle=scenario_to_bundle(scenario_from_dict(scenario),timestamp(now),profile_id='cliente-regole')
    (canonical/'factory-bundle.json').write_text(dumps(bundle),encoding='utf-8')
    clients=load_tool(cliente/'build_clienti.py','sync_cliente_clients');clients.main(str(canonical/'factory-bundle.json'),str(prepared/'scenario.json'))
    attributes=load_tool(cliente/'build_materiale_spessore.py','sync_cliente_attributes')
    attributes.main(str(canonical/'factory-bundle.json'),str(raw/'scenario.json'),str(extract))
    actuals=load_tool(cliente/'build_avanzamento_trd.py','sync_cliente_actuals')
    actuals.main(str(canonical/'factory-bundle.json'),str(prepared/'scenario.json'),str(extract/'15_avanzamento_trd/1.csv'),str(canonical),progress)
    bind=read_json(cliente/'binding-cliente.json')
    bind['domains']['progress']['residual_policy']=progress
    day=datetime.fromisoformat(start).replace(tzinfo=ZoneInfo('Europe/Rome'))
    bind['scenario']=dict(id='cliente-regole-'+start,start=day.isoformat(),end=(day+timedelta(days=365)).isoformat())
    (canonical/'binding.json').write_text(json.dumps(bind),encoding='utf-8')
    desc=load_tool(cliente/'build_descrizioni.py','sync_cliente_descriptions')
    description=desc.build(read_json(raw/'scenario.json'),scenario,extract,materials_note='Viste Factory configurate, nessuna disponibilita simulata')
    (canonical/'descrizioni.json').write_text(json.dumps(description),encoding='utf-8')
    now=datetime.now(UTC).replace(microsecond=0).isoformat()
    with (extract/'material-items.csv').open(encoding='utf-8',newline='') as f:material_items=list(csv.DictReader(f))
    tables=tables_from_binding(canonical/'binding.json',raw/'scenario.json',canonical/'descrizioni.json',
        now=now,material_items=material_items)
    from planning_factory.adapters.cliente_reconciliation import reconcile_nestings
    def read_view(name):
        with (extract/name/'1.csv').open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f))
    reconciliation=reconcile_nestings(scenario_from_dict(scenario),read_view('10_nesting_programmi'),read_view('11_nesting_membri'))
    (folder/'nesting-reconciliation.json').write_text(json.dumps(reconciliation,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in reconciliation['rows']:
        tables['provenance'].append(dict(id='NEST-RECON:'+row['workplan_id'],entity='nest',
            record_key={'id':'NEST-'+row['workplan_id']},source_id='migration:nesting',
            original_value=row,transformation='source_nesting_reconciliation',data_nature='dichiarato'))
    import hashlib
    for source in fetched:
        label=source['key'];source_id='SQL:'+label
        encoded=json.dumps(source['datasets'],sort_keys=True,ensure_ascii=False).encode()
        tables['source'].append(dict(id=source_id,system=source['name'],environment=config['database'],
            extracted_at=now,data_nature='effettivi',content_hash=hashlib.sha256(encoded).hexdigest()))
        occurrences=defaultdict(int)
        for dataset,originals in source['datasets'].items():
            for row in originals:
                original=dict(tipo_record=dataset,**row)
                identity=hashlib.sha256(json.dumps(original,sort_keys=True).encode()).hexdigest()[:24]
                n=occurrences[identity];occurrences[identity]+=1
                tables['provenance'].append(dict(id=source_id+':'+identity+':'+str(n),entity='source',
                    record_key={'id':identity},source_id=source_id,original_value=original,
                    transformation='factory_view_source',data_nature='effettivo'))
    (folder/'source-coverage.json').write_text(json.dumps(dict(
        contract_version=2,view_rows={k:v['rows'] for k,v in coverage.items()},
        datasets={k:v['datasets'] for k,v in coverage.items()},supplemental_rows={},
        nesting={k:v for k,v in reconciliation.items() if k!='rows'}),ensure_ascii=False,indent=2),encoding='utf-8')
    return tables
