"""Private, restartable Factory -> workbook worker. No credentials in request files."""
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import sys

from planning_factory.adapters.factory_workbook_pipeline import extract_tables
from planning_factory.adapters.workbook_io import read_tables,write_tables
from planning_factory.adapters.workbook_merge import merge_tables,json_bytes,MARKER
from planning_factory.adapters.planning_workbook import load_workbook,summary
from planning_factory.adapters.json_io import dumps,scenario_digest


def prepare(request_path,*,resolution=None):
    request_path=Path(request_path);root=request_path.parent
    request=json.loads(request_path.read_text(encoding='utf-8'))
    def phase(text):
        (root/'phase.json').write_text(json.dumps(dict(phase=text)),encoding='utf-8')
        print(text,flush=True)
    local,_,_=read_tables(root/'local.xlsx')
    base=json.loads(Path(request['baseline']).read_text(encoding='utf-8'))
    if {k:request['config'][k].casefold() for k in ('server','database')} != {
            k:base['source'][k].casefold() for k in ('server','database')}:
        raise ValueError('La base di sincronizzazione appartiene a un altro server/database; usare una istanza distinta.')
    policy=next((r.get('residual_policy') for r in local['domain_policy'] if r['domain']=='progress'),'quantity')
    remote_path=root/'remote.json'
    if not remote_path.exists():
        phase('Lettura Factory e viste SQL')
        remote=extract_tables(request['pipeline_root'],root/'extraction',request['config'],request['start'],progress=policy)
        remote_path.write_bytes(json_bytes(remote))
    remote=json.loads(remote_path.read_text(encoding='utf-8'))
    phase('Confronto dati Factory e modifiche Excel')
    tables,merge=merge_tables(base['tables'],local,remote,resolution=resolution)
    # Factory populates these domains; a workbook that never had them must declare them as managed.
    declared={r['domain'] for r in tables['domain_policy']}
    for row in (dict(domain='materials',coverage='gestito',allocation_order='due'),
                dict(domain='nesting',coverage='gestito'),
                dict(domain='progress',coverage='gestito',residual_policy=policy)):
        if row['domain'] not in declared:tables['domain_policy'].append(row)
    (root/'merge.json').write_bytes(json_bytes(merge))
    if merge['conflict_count'] and resolution is None:
        phase('Conflitti da risolvere; Excel attivo non modificato')
        return 2
    envelope=dict(schema_version=1,source={k:request['config'][k] for k in ('server','database','views')},tables=remote)
    baseline_bytes=json_bytes(envelope)
    baseline_hash=hashlib.sha256(baseline_bytes).hexdigest()
    (root/'baseline.json').write_bytes(baseline_bytes)
    now=datetime.now(UTC).replace(microsecond=0).isoformat()
    tables['source']=[r for r in tables['source'] if r['id']!=MARKER]
    tables['source'].append(dict(id=MARKER,system='factory-sync-v1',environment=tables['scenario'][0]['dataset_id'],
        extracted_at=now,data_nature='effettivi',content_hash=baseline_hash))
    phase('Validazione Excel completo')
    write_tables(root/'candidate.xlsx',tables)
    workbook=load_workbook(root/'candidate.xlsx')
    phase('Calcolo e verifica indipendente del piano')
    scenario,plan,evidence=workbook.calculate()
    (root/'scenario.json').write_text(dumps(scenario),encoding='utf-8')
    (root/'plan.json').write_text(dumps(plan),encoding='utf-8')
    report=dict(workbook=summary(workbook),evidence=evidence,merge=merge,
        baseline_hash=baseline_hash,input_scenario_digest=scenario_digest(workbook.source),
        unscheduled=len(plan.unscheduled),operations=len(workbook.source.operations),
        orders=len({o.order_id for o in workbook.source.operations}),resources=len(workbook.source.resources),
        publishable=workbook.built.publishable,finished_at=datetime.now(UTC).replace(microsecond=0).isoformat())
    coverage=root/'extraction/source-coverage.json'
    if coverage.is_file():report['source_coverage']=json.loads(coverage.read_text(encoding='utf-8'))
    (root/'ready.json').write_bytes(json_bytes(report))
    phase('Excel e piano verificati, pronti per aggiornare il sistema')
    return 0


def main():
    path=Path(sys.argv[1])
    try:
        code=prepare(path,resolution=sys.argv[2] if len(sys.argv)>2 else None)
    except Exception as exc:
        # The parent strips secrets from every log line as an additional guard.
        message='; '.join(getattr(exc,'errors',())) or str(exc)
        password=os.environ.get('CLIENTE_PASSWORD','')
        if password:message=message.replace(password,'[omessa]')
        (path.parent/'error.json').write_text(json.dumps(dict(error=message[:4000])),encoding='utf-8')
        print(type(exc).__name__+': '+message,flush=True)
        return 1
    finally:
        os.environ.pop('CLIENTE_PASSWORD',None)
    return code


if __name__=='__main__':raise SystemExit(main())
