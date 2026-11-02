"""Administrator-controlled sync, staged validation, conflicts and atomic publication."""
from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import uuid
from zoneinfo import ZoneInfo

from planning_factory.adapters.factory_extraction import FactoryExtraction
from planning_factory.adapters.workbook_io import read_tables
from planning_factory.adapters.workbook_merge import MARKER
from planning_factory.adapters.json_io import loads,plan_loads,scenario_digest
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.verification import verify_plan


class FactoryWorkbookSync(FactoryExtraction):
    def __init__(self,folder,state,on_done):
        self.state=state
        super().__init__(folder,Path(folder)/'factory-sync',Path(folder),on_done)
        self.output_dir.mkdir(parents=True,exist_ok=True)
        self.settings_path=Path(folder)/'factory-sync.json'
        self.status_path=self.output_dir/'status.json'
        self._process=None
        self._worker_active=False
        self._cancelled=False
        if self.status_path.exists():
            try:old=json.loads(self.status_path.read_text(encoding='utf-8'))
            except (OSError,ValueError):old=dict(state='failed',error='Registro sincronizzazione non leggibile; il calcolo da Excel resta disponibile.')
            if old.get('state') in ('running','publishing'):
                ready=self.output_dir/'runs'/str(old.get('job_id'))/'ready.json'
                report=json.loads(ready.read_text(encoding='utf-8')) if ready.is_file() else {}
                if report.get('workbook',{}).get('workbook_sha256')==state.workbook_input.fingerprint:
                    old.update(state='done',operations=report['operations'],orders=report['orders'],
                               resources=report['resources'],unscheduled=report['unscheduled'])
                else:old.update(state='failed',error='Sincronizzazione interrotta dal riavvio; riprovare.')
            self.job=old

    def config(self):
        result=super().config()
        result.update(mode='factory-excel-planning')
        result.setdefault('start',self.state.source.start.astimezone(ZoneInfo('Europe/Rome')).date().isoformat())
        result['pipeline_configured']=self.settings_path.exists()
        return result

    def save_config(self,data):
        with self.lock:
            if self._worker_active:raise ValidationError(['Attendere la sincronizzazione in corso prima di cambiare il collegamento.'])
            clean=self._validated_config(data)
            temporary=self.config_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(clean,ensure_ascii=False),encoding='utf-8');os.replace(temporary,self.config_path)
        return self.config()

    def _settings(self):
        data=json.loads(self.settings_path.read_text(encoding='utf-8'))
        for key in ('pipeline_root','bootstrap_baseline'):
            if not isinstance(data.get(key),str):raise ValidationError(['Configurazione sincronizzazione incompleta: '+key])
        return data

    def _baseline(self,tables,settings):
        marker=next((r for r in tables.get('source',[]) if r['id']==MARKER),None)
        if marker:
            h=marker.get('content_hash','')
            if not re.fullmatch('[a-f0-9]{64}',h):raise ValidationError(['Impronta base sincronizzazione non valida'])
            path=self.output_dir/'baselines'/(h+'.json')
            if hashlib.sha256(path.read_bytes()).hexdigest()!=h:raise ValidationError(['Base sincronizzazione modificata o corrotta'])
            return path
        path=Path(settings['bootstrap_baseline'])
        if not path.is_file():raise ValidationError(['Base iniziale assente: nessun aggiornamento per evitare la perdita delle modifiche Excel.'])
        return path

    def _save_status(self,**values):
        with self.lock:
            self.job.update(values)
            temp=self.status_path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.job,ensure_ascii=False),encoding='utf-8')
            os.replace(temp,self.status_path)

    def status(self):
        with self.lock:job={**self.job,'worker_active':self._worker_active and self.job.get('state') in ('running','publishing','cancelled')}
        if job.get('job_id'):
            folder=self.output_dir/'runs'/job['job_id']
            try:job.update(json.loads((folder/'phase.json').read_text(encoding='utf-8')))
            except (OSError,ValueError):pass
            if job.get('state')=='conflicts':
                detail=json.loads((folder/'merge.json').read_text(encoding='utf-8'))
                job.update(conflict_count=detail['conflict_count'],conflicts=detail['conflicts'][:100])
        return job

    def conflict_report(self):
        with self.lock:jid=self.job.get('job_id')
        if not jid:raise ValidationError(['Nessuna sincronizzazione da consultare'])
        path=self.output_dir/'runs'/jid/'merge.json'
        if not path.is_file():raise ValidationError(['Confronto non ancora disponibile'])
        report=json.loads(path.read_text(encoding='utf-8'))
        for key,filename in (('source_coverage','source-coverage.json'),('nesting_reconciliation','nesting-reconciliation.json')):
            evidence=path.parent/'extraction'/filename
            if evidence.is_file():report[key]=json.loads(evidence.read_text(encoding='utf-8'))
        return report

    def reference_sql(self,key):
        from planning_factory.adapters.factory_views import contract
        if key=='perimetro':
            return (Path(self._settings()['pipeline_root'])/'integrations/cliente-export/views/00_Perimetro.sql').read_text(encoding='utf-8')
        view=next((v for v in contract()['views'] if v['key']==key),None)
        if view is None:raise ValidationError(['Area SQL non riconosciuta'])
        path=Path(self._settings()['pipeline_root'])/'integrations/cliente-export/views'/view['sql']
        return path.read_text(encoding='utf-8')

    def start(self,data,actor):
        if not isinstance(data,dict) or set(data)!={'start','password'}:raise ValidationError(['start e password richiesti'])
        try:day=date.fromisoformat(data['start'])
        except (TypeError,ValueError):raise ValidationError(['start: data YYYY-MM-DD richiesta']) from None
        if not date(2000,1,1)<=day<=datetime.now(UTC).date()+timedelta(days=730):raise ValidationError(['Data fuori intervallo'])
        password=data['password']
        if not isinstance(password,str) or len(password)>1024:raise ValidationError(['Password non valida'])
        raw=self.config();config=self._validated_config({k:raw[k] for k in ('server','database','user','views','driver')})
        if config['user'] and not password:raise ValidationError(['Password SQL richiesta per questa estrazione'])
        settings=self._settings()
        content=self.state.path.read_bytes();tables,_,_=read_tables(self.state.path,for_sync=True)
        baseline=self._baseline(tables,settings)
        with self.lock:
            if self._worker_active:raise ValidationError(['Attendere la conclusione della sincronizzazione precedente'])
            jid=uuid.uuid4().hex
            self.job=dict(state='running',job_id=jid,actor=actor,start=data['start'],started_at=datetime.now(UTC).isoformat())
            self._cancelled=False
            self._worker_active=True
        try:
            folder=self.output_dir/'runs'/jid;folder.mkdir(parents=True)
            (folder/'local.xlsx').write_bytes(content)
            request=dict(config=config,start=data['start'],baseline=str(baseline),pipeline_root=settings['pipeline_root'],
                         expected_input_hash=hashlib.sha256(content).hexdigest())
            (folder/'request.json').write_text(json.dumps(request),encoding='utf-8')
        except Exception:
            with self.lock:self._worker_active=False
            self._save_status(state='failed',error='Impossibile preparare la cartella di lavoro; Excel non modificato.')
            raise
        self._redact_values=tuple(v for v in (config['user'],password) if v)
        self._save_status()
        threading.Thread(target=self._run_worker,args=(folder,password,None),daemon=True).start()
        return self.status()

    def resolve(self,data,actor):
        if not isinstance(data,dict) or set(data)!={'job_id','decision'} or data['decision'] not in ('excel','factory'):
            raise ValidationError(['job_id e decision excel/factory richiesti'])
        with self.lock:
            if self._worker_active:raise ValidationError(['Attendere la conclusione del worker precedente'])
            if self.job.get('state')!='conflicts' or self.job['job_id']!=data['job_id']:
                raise ValidationError(['Conflitto non piu attuale: rileggere lo stato'])
            self.job.update(state='running',resolved_by=actor,resolution=data['decision'])
            self._worker_active=True
        folder=self.output_dir/'runs'/data['job_id']
        self._save_status()
        threading.Thread(target=self._run_worker,args=(folder,'',data['decision']),daemon=True).start()
        return self.status()

    def cancel(self,actor):
        with self.lock:
            if self.job.get('state')!='running':raise ValidationError(['Nessuna sincronizzazione in corso'])
            self._cancelled=True
            if self._process:self._process.terminate()
        self._save_status(state='cancelled',cancelled_by=actor)
        return self.status()

    def _run_worker(self,folder,password,resolution):
        try:
            req=json.loads((folder/'request.json').read_text(encoding='utf-8'))
            if hashlib.sha256(self.state.path.read_bytes()).hexdigest()!=req['expected_input_hash']:
                raise ValidationError(['Excel modificato durante la sincronizzazione: riprovare senza sovrascrivere le nuove modifiche.'])
            if resolution is None:
                checked=self.test_connection({**req['config'],'password':password})
                if not checked['ok']:raise ValidationError([checked['error']])
            if self._cancelled:return
            env=dict(os.environ,PYTHONIOENCODING='utf-8')
            if password:env['CLIENTE_PASSWORD']=password
            else:env.pop('CLIENTE_PASSWORD',None)
            argv=[sys.executable,'-m','planning_factory.factory_sync_worker',str(folder/'request.json')]
            if resolution:argv.append(resolution)
            process=subprocess.Popen(argv,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace',
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            with self.lock:self._process=process
            try:output,_=process.communicate(timeout=3600)
            except subprocess.TimeoutExpired:
                process.kill();output,_=process.communicate()
                raise ValidationError(['Tempo massimo superato; Excel attivo non modificato'])
            (folder/'worker.log').write_text(self._redact(output),encoding='utf-8')
            if self._cancelled:return
            if process.returncode==2:
                self._save_status(state='conflicts');return
            if process.returncode!=0:
                error=json.loads((folder/'error.json').read_text(encoding='utf-8')) if (folder/'error.json').exists() else {'error':'Worker terminato senza risultato'}
                raise ValidationError([error['error']])
            report=json.loads((folder/'ready.json').read_text(encoding='utf-8'))
            if not self._publish(folder,req,report):return
            self._save_status(state='done',finished_at=datetime.now(UTC).isoformat(),
                operations=report['operations'],orders=report['orders'],resources=report['resources'],
                unscheduled=report['unscheduled'],merge={k:v for k,v in report['merge'].items() if k!='conflicts'},
                source_coverage=report.get('source_coverage'),
                workbook_sha256=report['workbook']['workbook_sha256'])
        except Exception as exc:
            if not self._cancelled:
                error='; '.join(getattr(exc,'errors',())) or str(exc)
                self._save_status(state='failed',error=self._redact(error)[:4000])
        finally:
            self._redact_values=()
            with self.lock:self._process=None;self._worker_active=False

    def _publish(self,folder,request,report):
        from planning_factory.adapters.dashboard_api import DashboardState,PlannerBusyError
        candidate=DashboardState(folder/'candidate.xlsx')
        if candidate.workbook_input.fingerprint!=report['workbook']['workbook_sha256'] or scenario_digest(candidate.source)!=report['input_scenario_digest']:
            raise ValidationError(['Il candidato non corrisponde al piano verificato'])
        scenario=loads((folder/'scenario.json').read_text(encoding='utf-8'))
        plan=plan_loads((folder/'plan.json').read_text(encoding='utf-8'))
        errors=verify_plan(scenario,plan)
        if errors:raise ValidationError(list(errors))
        if not self.state._compute_slot.acquire(timeout=300):raise PlannerBusyError('Il calcolo in corso non e terminato; Excel non aggiornato')
        try:
            content=self.state.path.read_bytes()
            if hashlib.sha256(content).hexdigest()!=request['expected_input_hash']:
                raise ValidationError(['Excel cambiato: aggiornamento non applicato; riprovare la sincronizzazione'])
            if self._cancelled:return False
            baseline=(folder/'baseline.json').read_bytes()
            h=hashlib.sha256(baseline).hexdigest()
            if h!=report['baseline_hash']:raise ValidationError(['Base di confronto non valida'])
            baselines=self.output_dir/'baselines';baselines.mkdir(exist_ok=True)
            (baselines/(h+'.json')).write_bytes(baseline)
            history=self.output_dir/'history';history.mkdir(exist_ok=True)
            (history/(request['expected_input_hash']+'.xlsx')).write_bytes(content)
            active=self.state.path
            # Atomic replace on the same volume; a locked Excel file fails
            # before installing a new state. The former file stays available.
            with self.lock:
                if self._cancelled:return False
                self.job['state']='publishing'
                os.replace(folder/'candidate.xlsx',active)
                candidate.path=active
                try:self.state.adopt_workbook(candidate,(scenario,plan,report['evidence']))
                except Exception:
                    rollback=active.with_name(active.name+'.rollback')
                    rollback.write_bytes(content);os.replace(rollback,active)
                    raise
        finally:self.state._compute_slot.release()
        self.on_done()
        return True
