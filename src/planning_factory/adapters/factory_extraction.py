"""Administrator-triggered CLIENTE extraction using the installed local export pipeline."""
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

from planning_factory.domain.validation import ValidationError
from planning_factory.adapters import factory_views

_SERVER = re.compile(r'^[A-Za-z0-9_.\\,-]+$')
_DATABASE = re.compile(r'^[A-Za-z0-9_]+$')
_USER = re.compile(r'^[^;{}\r\n]{1,128}$')
_OBJECT = re.compile(r'^(?:[A-Za-z_][A-Za-z0-9_]*\.)?[A-Za-z_][A-Za-z0-9_]*$')
_DEFAULT_VIEWS = factory_views.defaults()
_MAX_TIMEOUT = 4 * 3600


def _now():
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


class FactoryExtraction:
    """Runs the fixed local CLIENTE pipeline; credentials only live for the requested run."""

    def __init__(self, folder, output_dir, repo_root, on_done):
        self.folder = Path(folder)
        self.output_dir = Path(output_dir)
        self.repo_root = Path(repo_root)
        self.on_done = on_done
        self.config_path = self.folder / 'factory-source.json'
        self.log_path = self.folder / 'factory-extraction.log'
        self.lock = threading.Lock()
        self.job = {'state': 'idle'}
        self._redact_values = ()

    def config(self):
        raw = {'server': r'SERVER\ISTANZA', 'database': 'Factory_Cliente', 'user': '', 'driver': 'ODBC Driver 17 for SQL Server', 'views': dict(_DEFAULT_VIEWS)}
        try:
            saved = json.loads(self.config_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            saved = {}
        if isinstance(saved, dict):
            raw.update({k: v for k, v in saved.items() if k in ('server', 'database', 'user', 'views', 'driver')})
        view_contract=[{k:v[k] for k in ('key','label','description','sheets','sql')} |
            {'datasets':[{k:d[k] for k in ('name','tipo','description')} for d in v['datasets']]} for v in factory_views.contract()['views']]
        return {'available': True, **raw, 'contract_version':2,'view_contract':view_contract,
            'password_required_each_run': True, **self.status()}

    def _validated_config(self, data):
        if not isinstance(data, dict) or not {'server', 'database', 'user', 'views'} <= data.keys() or set(data)-{'server','database','user','views','driver'}:
            raise ValidationError(['server, database, user e views richiesti'])
        server, database, user, views = (data.get(k) for k in ('server', 'database', 'user', 'views'))
        if not isinstance(server, str) or not _SERVER.fullmatch(server):
            raise ValidationError(['istanza SQL non valida (usa server\\istanza)'])
        if not isinstance(database, str) or not _DATABASE.fullmatch(database):
            raise ValidationError(['database non valido'])
        if user and (not isinstance(user, str) or not _USER.fullmatch(user)):
            raise ValidationError(['utente SQL non valido'])
        if not isinstance(views, dict) or set(views) != set(_DEFAULT_VIEWS):
            raise ValidationError(['Configura le otto viste complete: calendari, risorse, commesse, fasi, cicli, nesting, materiali, avanzamento'])
        for label, value in views.items():
            if not isinstance(value, str) or not _OBJECT.fullmatch(value):
                raise ValidationError([f'{label}: nome tabella/vista non valido'])
        driver=data.get('driver','ODBC Driver 17 for SQL Server')
        if driver not in ('ODBC Driver 17 for SQL Server','ODBC Driver 18 for SQL Server'):
            raise ValidationError(['driver ODBC 17 o 18 richiesto'])
        return {'server': server, 'database': database, 'user': user or '', 'views': dict(views), 'driver':driver}

    def save_config(self, data):
        clean = self._validated_config(data)
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.config_path.write_text(json.dumps(clean, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        return self.config()

    def test_connection(self, data):
        """Check SQL login and SELECT access to each configured export view; never writes."""
        if not isinstance(data, dict) or not {'server', 'database', 'user', 'password', 'views'} <= data.keys() or set(data)-{'server','database','user','password','views','driver'}:
            raise ValidationError(['server, database, user, password e views richiesti'])
        clean = self._validated_config({k:v for k,v in data.items() if k!='password'})
        password = data['password']
        if not isinstance(password, str) or len(password) > 1024:
            raise ValidationError(['password non valida'])
        if clean['user'] and not password:
            raise ValidationError(['inserisci la password dell’utente SQL configurato'])

        def odbc_value(value):
            return '{' + str(value).replace('}', '}}') + '}'

        parts = [f'DRIVER={{{clean["driver"]}}}', f"SERVER={odbc_value(clean['server'])}",
                 f"DATABASE={odbc_value(clean['database'])}", 'Encrypt=yes', 'TrustServerCertificate=yes',
                 'ApplicationIntent=ReadOnly']
        if clean['user']:
            parts += [f"UID={odbc_value(clean['user'])}", f'PWD={odbc_value(password)}']
        else:
            parts.append('Trusted_Connection=yes')
        started = time.monotonic()
        try:
            import pyodbc
            connection = pyodbc.connect(';'.join(parts), readonly=True, timeout=12)
            try:
                connection.timeout = 12
                cursor = connection.cursor()
                cursor.execute('SELECT DB_NAME(), @@SERVERNAME')
                database, server = cursor.fetchone()
                checked = []
                definitions={v['key']:v for v in factory_views.contract()['views']}
                for label, value in clean['views'].items():
                    table = '.'.join('[' + part + ']' for part in value.split('.'))
                    try:
                        cursor.execute('SELECT TOP (0) * FROM ' + table)
                        actual={c[0].casefold() for c in cursor.description}
                        required={c.casefold() for c in factory_views.required_columns(definitions[label])}
                        if actual!=required:
                            raise ValueError('Colonne non conformi al contratto della vista: mancanti '+str(sorted(required-actual))+', aggiunte '+str(sorted(actual-required)))
                    except Exception as exc:
                        raise RuntimeError(f'{label} ({value}): {exc}') from None
                    checked.append(label)
                cursor.close()
            finally:
                connection.close()
            return {'ok': True, 'server': server, 'database': database,
                    'authentication': 'sql' if clean['user'] else 'windows', 'checked_views': len(checked),
                    'latency_ms': round((time.monotonic() - started) * 1000)}
        except Exception as exc:
            detail = str(exc).replace(password, '[omessa]') if password else str(exc)
            if '18456' in detail or 'Login failed' in detail:
                error = 'Accesso SQL rifiutato: verifica login/password e che SQL Server consenta l’autenticazione SQL.'
            elif '-2146893042' in detail or 'No credentials are available' in detail:
                error = 'Handshake SSL/TLS SQL non riuscito (SEC_E_NO_CREDENTIALS). Dettaglio driver: ' + detail[:500]
            elif 'IM002' in detail:
                error = 'Driver ODBC per SQL Server non trovato: installa Microsoft ODBC Driver 18.'
            elif '4060' in detail or 'Cannot open database' in detail:
                error = 'Login riuscito ma database non accessibile. Verifica nome database e permessi.'
            elif 'Login timeout' in detail or '08001' in detail:
                error = 'Server/istanza/database non raggiungibili. Dettaglio driver: ' + detail[:500]
            else:
                error = detail[:700]
            return {'ok': False, 'server': clean['server'], 'database': clean['database'], 'error': error,
                    'latency_ms': round((time.monotonic() - started) * 1000)}

    def status(self):
        with self.lock:
            job = dict(self.job)
        if job.get('state') != 'idle':
            try:
                lines = self.log_path.read_text(encoding='utf-8', errors='replace').splitlines()[-12:]
            except OSError:
                lines = []
            job['log_tail'] = [self._redact(line)[:300] for line in lines]
        return job

    def _redact(self, line):
        for value in self._redact_values:
            if value:
                line = line.replace(value, '[omesso]')
        return line

    def start(self, data, actor):
        if not isinstance(data, dict) or set(data) != {'start', 'password'}:
            raise ValidationError(['start e password richiesti; lascia password vuota per autenticazione Windows'])
        try:
            start = date.fromisoformat(data['start'])
        except (TypeError, ValueError):
            raise ValidationError(['start: data YYYY-MM-DD']) from None
        if not date(2000, 1, 1) <= start <= datetime.now(UTC).date() + timedelta(days=730):
            raise ValidationError(['start: data fuori intervallo'])
        password = data['password']
        if not isinstance(password, str) or len(password) > 1024:
            raise ValidationError(['password non valida'])
        config = self.config()
        if config['user'] and not password:
            raise ValidationError(['inserisci la password dell’utente SQL configurato'])
        self._redact_values = tuple(x for x in (config['user'], password) if x)
        with self.lock:
            if self.job.get('state') == 'running':
                raise ValidationError(['una estrazione Factory è già in corso'])
            self.job = {'state': 'running', 'start': start.isoformat(), 'actor': actor, 'started_at': _now()}
        threading.Thread(target=self._run, args=(config, start.isoformat(), password), daemon=True).start()
        return self.status()

    def _run(self, config, start, password):
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        script = self.repo_root / 'integrations' / 'cliente-export' / 'run_cliente_locale.ps1'
        powershell = shutil.which('pwsh.exe') or r'C:\Program Files\PowerShell\7\pwsh.exe'
        argv = [powershell, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script),
                '-Start', start, '-Server', config['server'], '-Database', config['database'],
                '-Out', str(self.output_dir)]
        env = dict(__import__('os').environ)
        env.update({'CLIENTE_SERVER': config['server'], 'CLIENTE_DB': config['database'],
                    'CLIENTE_VIEWS_JSON': json.dumps(config['views'], separators=(',', ':'))})
        if config['user']:
            env['CLIENTE_USER'] = config['user']
            env['CLIENTE_PASSWORD'] = password
        else:
            env.pop('CLIENTE_USER', None)
            env.pop('CLIENTE_PASSWORD', None)
        try:
            preflight_data = {key: config[key] for key in ('server', 'database', 'user', 'views')}
            preflight_data['password'] = password
            preflight = self.test_connection(preflight_data)
            if not preflight['ok']:
                with self.log_path.open('w', encoding='utf-8') as log:
                    log.write('Verifica SQL interrotta prima di aggiornare i file.\n' + preflight['error'] + '\n')
                with self.lock:
                    self.job.update({'state': 'failed', 'error': preflight['error'], 'exit_code': 1, 'ended_at': _now()})
                return
            with self.log_path.open('w', encoding='utf-8') as log:
                log.write(f"Connessione SQL OK: {preflight['server']} / {preflight['database']}; "
                          f"viste accessibili {preflight['checked_views']}/11.\n")
            with self.log_path.open('a', encoding='utf-8') as log:
                process = subprocess.Popen(argv, cwd=self.repo_root, env=env, stdout=log, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL)
                try:
                    code = process.wait(timeout=_MAX_TIMEOUT)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    code = None
            if code == 0:
                self.on_done()
                outcome = {'state': 'done', 'exit_code': 0}
            else:
                try:
                    output = self.log_path.read_text(encoding='utf-8', errors='replace')
                except OSError:
                    output = ''
                if 'Nessuna credenziale disponibile nel pacchetto di sicurezza' in output or '-2146893042' in output:
                    error = ('Negoziazione SSL/TLS con SQL Server non riuscita (SEC_E_NO_CREDENTIALS). '
                             'Apri “Verifica connessione SQL” per il dettaglio del driver.')
                else:
                    error = 'tempo massimo superato' if code is None else f'comando terminato con codice {code}'
                outcome = {'state': 'failed', 'exit_code': code,
                           'error': error}
        except Exception as exc:
            outcome = {'state': 'failed', 'error': str(exc)[:500]}
        with self.lock:
            self.job.update(outcome, ended_at=_now())
