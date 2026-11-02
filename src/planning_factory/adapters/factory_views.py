"""The complete Factory connector reads eight declared views and no base tables.

SQL business logic belongs to the documented views. This module transports
typed records and reconstructs the existing interchange format in a private
job folder. Planning itself still reads only the published workbook.
"""
from collections import defaultdict
from datetime import date,datetime,time,timedelta
from decimal import Decimal
import csv
import hashlib
from importlib.resources import files
import json
from pathlib import Path
import re

from planning_factory.domain.validation import ValidationError


def contract():
    return json.loads(files('planning_factory').joinpath('schemas/factory-views-v2.json').read_text(encoding='utf-8'))


def defaults():return {v['key']:v['default'] for v in contract()['views']}


def quoted_object(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*',value):
        raise ValidationError(['Nome vista SQL non valido'])
    return '.'.join('['+p+']' for p in value.split('.'))


def required_columns(view):return {'TipoRecord',*view['columns']}


def typed(value,sql_type):
    if value is None:return None
    kind=sql_type.split('(')[0]
    if kind in ('int','bigint','smallint','tinyint'):
        number=Decimal(str(value))
        if not number.is_finite() or number!=number.to_integral_value():raise ValueError('intero richiesto')
        return int(number)
    if kind=='bit':
        if value not in (0,1,False,True,Decimal(0),Decimal(1)):raise ValueError('bit 0 o 1 richiesto')
        return bool(value)
    if kind in ('decimal','numeric','money','smallmoney','float','real'):
        number=Decimal(str(value))
        if not number.is_finite():raise ValueError('numero finito richiesto')
        return float(number)  # matches the established Factory interchange JSON
    if kind=='date':
        return (value.date() if isinstance(value,datetime) else value).isoformat() if isinstance(value,(datetime,date)) else date.fromisoformat(value).isoformat()
    if kind in ('datetime','datetime2','smalldatetime','time','datetimeoffset'):
        if isinstance(value,(datetime,date,time)):return value.isoformat()
        return str(value).replace(' ','T',1) if kind!='time' else str(value)
    return str(value).rstrip()


def split_records(view,columns,rows):
    """Unknown/missing fields or record types fail, rather than silently losing data."""
    actual={c.casefold():i for i,c in enumerate(columns)}
    missing=required_columns(view)-{c for c in required_columns(view) if c.casefold() in actual}
    if missing:raise ValidationError([view['label']+': colonne mancanti '+', '.join(sorted(missing))])
    extra=set(actual)-{c.casefold() for c in required_columns(view)}
    if extra:raise ValidationError([view['label']+': colonne fuori contratto '+', '.join(sorted(extra))])
    if len(actual)!=len(columns):raise ValidationError([view['label']+': colonne duplicate'])
    kinds={d['tipo']:d for d in view['datasets']}
    data={d['name']:[] for d in view['datasets']}
    for row in rows:
        label=row[actual['tiporecord']]
        if label not in kinds:raise ValidationError([view['label']+': TipoRecord sconosciuto '+str(label)])
        definition=kinds[label];record={}
        for field,sql_type in definition['fields'].items():
            try:record[field]=typed(row[actual[field.casefold()]],sql_type)
            except (ValueError,TypeError,ArithmeticError) as exc:
                raise ValidationError([view['label']+'/'+label+'.'+field+': '+str(exc)]) from None
        for field in view['columns']:
            if field not in definition['fields'] and row[actual[field.casefold()]] is not None:
                raise ValidationError([view['label']+'/'+label+': valore in colonna non pertinente '+field])
        data[definition['name']].append(record)
    return data


def connect(config,password=''):
    import pyodbc
    value=lambda v:'{'+str(v).replace('}','}}')+'}'
    parts=['DRIVER='+value(config['driver']),'SERVER='+value(config['server']),
           'DATABASE='+value(config['database']),'Encrypt=yes','TrustServerCertificate=yes','ApplicationIntent=ReadOnly']
    if config['user']:parts+=['UID='+value(config['user']),'PWD='+value(password)]
    else:parts+=['Trusted_Connection=yes']
    return pyodbc.connect(';'.join(parts),readonly=True,timeout=15)


def extract(config,folder,start,exporter,*,password='',on_progress=lambda message:None):
    """Exactly one data SELECT per configured view. No SQL batches or temp tables."""
    folder=Path(folder);extract_dir=folder/'extract';raw_dir=folder/'raw'
    extract_dir.mkdir(parents=True,exist_ok=True);raw_dir.mkdir(parents=True,exist_ok=True)
    definition=contract()
    if set(config['views'])!=set(defaults()):raise ValidationError(['Sono richieste le otto viste complete del contratto 2'])
    all_data={};coverage={};fetched=[]
    connection=connect(config,password)
    try:
        connection.timeout=1200
        for view in definition['views']:
            name=config['views'][view['key']]
            on_progress('Lettura vista '+view['label'])
            cursor=connection.cursor()
            try:
                cursor.execute('SELECT * FROM '+quoted_object(name))
                columns=[c[0] for c in cursor.description]
                data=split_records(view,columns,cursor.fetchall())
            finally:cursor.close()
            all_data.update(data)
            counts={k:len(v) for k,v in data.items()}
            coverage[view['key']]=dict(view=name,rows=sum(counts.values()),datasets=counts)
            fetched.append(dict(key=view['key'],name=name,datasets=data))
    finally:connection.close()
    # Stable order avoids depending on an undefined ORDER BY inside a SQL view.
    for name,records in all_data.items():
        records.sort(key=lambda r:json.dumps(r,sort_keys=True,ensure_ascii=False))
    lots={(r['orderId'],r['lotId']):r for r in all_data['lots']}
    all_data['operations'].sort(key=lambda o:(o['orderId'],-(lots[(o['orderId'],o['lotId'])].get('finalLotId') or 0),o['lotId'],o.get('sequence') or 0,o['phaseId']))
    # Calendar source is exhaustive. Only the established interchange window is
    # bounded; fetched source records are retained in full workbook provenance.
    first=date.fromisoformat(start)-timedelta(days=7);last=date.fromisoformat(start)+timedelta(days=365)
    raw={}
    for view in definition['views']:
        for ds in view['datasets']:
            records=all_data[ds['name']]
            if ds['csv']:
                path=extract_dir/ds['csv'];path.parent.mkdir(parents=True,exist_ok=True)
                with path.open('w',encoding='utf-8',newline='') as f:
                    writer=csv.DictWriter(f,fieldnames=ds['fields']);writer.writeheader();writer.writerows(records)
            if ds['raw']:raw[ds['name']]=records
    raw['calendarPeriods']=[r for r in raw['calendarPeriods'] if r['toDate'] and r['fromDate'] and date.fromisoformat(r['toDate'][:10])>=first and date.fromisoformat(r['fromDate'][:10])<=last]
    for name in ('calendarExceptions','operatorAbsences'):
        raw[name]=[r for r in raw[name] if r['date'] and first<=date.fromisoformat(r['date'][:10])<=last]
    derived=exporter.derive_precedences(raw['operations'],raw['lots'],True)
    raw['precedences']=derived+[dict(predecessorOperationId=r['predecessorOperationId'],
        successorOperationId=r['successorOperationId'],linkType=r['linkType'],lagSec=0,overlapPct=0,source='EXPLICIT')
        for r in raw['explicitLinks'] if r['predecessorOperationId'] is not None]
    fingerprint=hashlib.sha256(json.dumps(config['views'],sort_keys=True).encode()).hexdigest()[:16]
    scenario=dict(schemaVersion='1.0',scenarioId='factory-'+config['database'].lower()+'-'+start,
        source=dict(system='Factory ERP',server=config['server'],database=config['database'],
            capturedAt=datetime.now().astimezone().isoformat(timespec='seconds'),
            extractor='planning_factory/factory-views-v2',configFingerprint=fingerprint),
        planning=dict(start=start+'T00:00:00',horizonDays=365,timeZone='Europe/Rome'),
        universe=dict(lavorazioni=len(raw['operations']),lotti=len(raw['lots']),commesse=len(raw['orders'])),**raw)
    (raw_dir/'scenario.json').write_text(json.dumps(scenario,ensure_ascii=False,indent=1),encoding='utf-8')
    (folder/'view-coverage.json').write_text(json.dumps(coverage,ensure_ascii=False,indent=2),encoding='utf-8')
    return fetched,coverage
