"""Build a wheel offline, install into a clean venv and prove an installed flow.

Produces a technical bundle, never a production approval. Windows and a real
browser/Claude/SQL Server are separate acceptance environments.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]

PROBE = r'''
import http.client, importlib.resources, json, pathlib, sys, threading
from planning_factory.adapters.secure_dashboard import make_secure_dashboard_server
from planning_factory.adapters.service import new_token
from planning_factory.engine.verification import verify_plan
from planning_factory.adapters.json_io import loads, plan_loads
folder=pathlib.Path(sys.argv[1])
assert pathlib.Path(__import__('planning_factory').__file__).is_relative_to(pathlib.Path(sys.prefix))
assert importlib.resources.files('planning_factory').joinpath('web/app.js').is_file()
token,entry=new_token('package-probe','planner')
(folder/'users.json').write_text(json.dumps({'users':[entry]}))
server=make_secure_dashboard_server(folder/'scenario.json',folder/'users.json',folder/'plans.sqlite',folder/'audit.jsonl',staged_spec=folder/'stage-spec.json',port=0)
thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
def request(method,path,body=None,cookie=None):
 c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=20)
 headers={'Content-Type':'application/json'}
 if cookie:headers['Cookie']=cookie
 c.request(method,path,json.dumps(body) if body is not None else None,headers)
 r=c.getresponse();code=r.status;h=dict(r.getheaders());raw=r.read();c.close()
 return code,h,json.loads(raw)
try:
 assert request('GET','/api/bootstrap')[0]==401
 code,headers,_=request('POST','/api/login',{'token':token});assert code==200
 cookie=headers['Set-Cookie'].split(';')[0]
 code,_,base=request('GET','/api/bootstrap',cookie=cookie);assert code==200
 assert not verify_plan(loads(json.dumps(base['scenario'])),plan_loads(json.dumps(base['plan'])))
 code,_,stage=request('POST','/api/v03-preview',{},cookie);assert code==200 and stage['audited'] and not stage['publishable']
 data={'scenario':base['scenario'],'plan':base['plan'],'expected_revision':0,'evidence_id':base['meta']['evidence_id']}
 assert request('POST','/api/snapshots',data,cookie)[0]==200
 assert request('POST','/api/snapshots',data,cookie)[0]==409
 sid=base['scenario']['id']
 assert request('GET',f'/api/snapshots/{sid}/revisions/1',cookie=cookie)[0]==200
 assert request('POST','/api/snapshots/preview',{'scenario_id':sid,'revision':1,'settings':{},'edits':{'fixed_tasks':[],'closures':[]}},cookie)[0]==200
 assert request('POST','/api/publish',{},cookie)[0]==404
 print(json.dumps({'installed_package':True,'authenticated_http':True,'stage_audit':True,'snapshot_conflict':True,'archived_preview':True,'publish_absent':True}))
finally:
 server.shutdown();server.server_close();thread.join(5)
'''


def run(destination):
    folder = Path(destination).resolve()
    folder.mkdir(parents=True)  # exclusively owned output, never overwrites
    bundle = folder / 'bundle'
    bundle.mkdir()
    logs = folder / 'logs'
    logs.mkdir()
    env = {k: v for k, v in os.environ.items() if k not in ('PYTHONPATH', 'PYTHONHOME') and not k.startswith('PLANNING_CLAUDE_')}
    env['PIP_NO_INDEX'] = '1'

    def execute(label, arguments, *, cwd=folder, stdin=None):
        result = subprocess.run(arguments, input=stdin, text=True, cwd=cwd, env=env,
                                capture_output=True, timeout=180)
        (logs / (label + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError(f'{label} failed; see {logs / (label + ".log")}')
        return result.stdout

    wheels = bundle / 'wheels'
    execute('build-wheel', [sys.executable, '-m', 'pip', 'wheel', '--no-deps', '--no-build-isolation',
                            '--no-index', '--wheel-dir', str(wheels), str(ROOT)])
    wheel, = wheels.glob('planning_factory-*.whl')
    environment = folder / 'validation-venv'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    execute('install-wheel', [str(python), '-I', '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)])
    from tools.generate_v03_stage_demo import make_example, write_example_csv
    from planning_factory.adapters.json_io import dumps
    source, spec = make_example()
    (bundle / 'scenario.json').write_text(dumps(source), encoding='utf-8')
    (bundle / 'stage-spec.json').write_text(json.dumps(spec, indent=2) + '\n', encoding='utf-8')
    write_example_csv(bundle / 'tables', source, spec)
    for name in ('init_local_instance.py', 'backup_snapshots.py', 'build_v03_spec_from_csv.py'):
        shutil.copyfile(ROOT / 'tools' / name, bundle / name)
    probe = folder / 'installed_probe.py'
    probe.write_text(PROBE, encoding='utf-8')
    probe_data = folder / 'probe-data'
    probe_data.mkdir()
    for name in ('scenario.json', 'stage-spec.json'):
        shutil.copyfile(bundle / name, probe_data / name)
    output = execute('installed-http', [str(python), '-I', str(probe), str(probe_data)])
    http_evidence = json.loads(output.strip().splitlines()[-1])
    execute('cli-validate', [str(python), '-I', '-m', 'planning_factory', 'validate', str(bundle / 'scenario.json')])
    execute('cli-schedule', [str(python), '-I', '-m', 'planning_factory', 'schedule', str(bundle / 'scenario.json'), '-o', str(folder / 'plan.json')])
    execute('cli-verify', [str(python), '-I', '-m', 'planning_factory', 'verify', str(bundle / 'scenario.json'), str(folder / 'plan.json')])
    # Real subprocess and stdio framing, without a model service or its client.
    messages = [ {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'protocolVersion': '2025-11-25', 'capabilities': {}, 'clientInfo': {'name': 'package-probe', 'version': '1'}}},
                 {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                 {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {'name': 'get_plan_summary', 'arguments': {}}} ]
    mcp = execute('installed-mcp', [str(python), '-I', '-m', 'planning_factory', 'mcp', '--fixture', str(bundle / 'scenario.json')],
                  stdin=''.join(json.dumps(x) + '\n' for x in messages))
    replies = [json.loads(line) for line in mcp.splitlines()]
    if [x.get('id') for x in replies] != [1, 2] or any('error' in x for x in replies) or replies[1]['result'].get('isError'):
        raise RuntimeError('installed MCP probe failed')
    execute('installed-backup', [str(python), '-I', str(bundle / 'backup_snapshots.py'), '--source', str(probe_data / 'plans.sqlite'), '--out', str(folder / 'backup.sqlite')])
    execute('installed-csv-import', [str(python), '-I', str(bundle / 'build_v03_spec_from_csv.py'),
                                    '--scenario', str(bundle / 'scenario.json'), '--tables', str(bundle / 'tables'),
                                    '--out', str(folder / 'imported-spec.json'), '--stock-accounting', 'net'])
    execute('installed-initializer', [str(python), '-I', str(bundle / 'init_local_instance.py'), '--out', str(folder / 'fresh-instance'),
                                       '--fixture', str(bundle / 'scenario.json'), '--stage-spec', str(folder / 'imported-spec.json')])
    # The initializer intentionally prints a one-time token. Keep that log
    # private, never include it in the distributable evidence or ZIP.
    os.chmod(logs / 'installed-initializer.log', 0o600)
    summary = {'passed': True, 'platform': sys.platform, 'python': sys.version,
               'wheel': wheel.name, 'http': http_evidence, 'mcp_subprocess': True,
               'cli_validate_schedule_verify': True, 'backup_executed': True,
               'csv_templates_imported': True,
               'browser_executed': False, 'live_claude_calls': 0,
               'sql_server_executed': False, 'windows_verified': os.name == 'nt',
               'production_approved': False, 'data': 'SYNTHETIC; replace with validated CLIENTE snapshots'}
    (bundle / 'verification.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    shutil.copyfile(ROOT / 'docs' / 'local-installation.md', bundle / 'README.md')
    hashes = {str(p.relative_to(bundle)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(bundle.rglob('*')) if p.is_file()}
    (bundle / 'SHA256SUMS.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
    archive = folder / 'planning-factory-technical.zip'
    with zipfile.ZipFile(archive, 'x', zipfile.ZIP_DEFLATED) as target:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                target.write(path, path.relative_to(bundle))
    print(json.dumps({'archive': str(archive), 'verification': summary}, indent=2))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='new output directory')
    return 0 if run(parser.parse_args(argv).out)['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
