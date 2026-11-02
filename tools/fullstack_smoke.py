"""Reproduce CSV -> engine -> HTTP -> what-if -> export -> independent audit.

Entirely synthetic, loopback only, no SQL/LLM/hosted runner. Browser layout
is a separate check; this command never labels an HTTP test as browser QA.
"""
import argparse
import http.client
import json
from pathlib import Path
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'src'))

from planning_factory.adapters.dashboard_api import make_dashboard_server
from planning_factory.adapters.json_io import dumps, loads, plan_loads
from planning_factory.engine.verification import verify_plan
from tools.build_v03_spec_from_csv import transform
from tools.generate_v03_stage_demo import make_example, write_example_csv


def run(destination: Path) -> dict:
    destination = Path(destination).resolve()
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('choose a new or empty output directory; evidence is not overwritten')
    destination.mkdir(parents=True, exist_ok=True)
    scenario, specification = make_example()
    tables = destination / 'tables'
    write_example_csv(tables, scenario, specification)
    imported, manifest = transform(scenario, tables, mode='normal',
                                  stock_accounting='net', residual_materials_confirmed=False)
    source = destination / 'scenario.json'
    source.write_text(dumps(scenario), encoding='utf-8')
    spec = destination / 'stage-spec.json'
    spec.write_text(json.dumps(imported, indent=2) + '\n', encoding='utf-8')
    (destination / 'sources.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    server = make_dashboard_server(source, staged_spec=spec, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(method, path, body=None, headers=None, expected=200):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=30)
        options = dict(headers or {})
        raw = None if body is None else json.dumps(body).encode('utf-8')
        if raw is not None:
            options['Content-Type'] = 'application/json'
        try:
            connection.request(method, path, raw, options)
            response = connection.getresponse()
            content = response.read()
            if response.status != expected:
                raise RuntimeError(f'{method} {path}: expected {expected}, got {response.status}: {content[:300]!r}')
            return content
        finally:
            connection.close()

    try:
        health = json.loads(request('GET', '/api/health'))
        for asset in ('/', '/app.js', '/styles.css'):
            if not request('GET', asset):
                raise RuntimeError(f'empty asset: {asset}')
        baseline = json.loads(request('GET', '/api/bootstrap'))
        candidate = json.loads(request('POST', '/api/simulate', {
            'settings': {}, 'edits': {'fixed_tasks': [], 'closures': [{
                'resource_id': 'WORKCELL_1', 'start': '2026-10-19T08:00:00Z',
                'end': '2026-10-19T09:00:00Z', 'reason': 'SYNTHETIC maintenance'}]}}))
        if candidate['plan']['scenario_digest'] == baseline['plan']['scenario_digest']:
            raise RuntimeError('what-if did not create a distinct scenario')
        for name, payload in (('baseline', baseline), ('whatif', candidate)):
            exported_source = destination / f'{name}-scenario.json'
            exported_plan = destination / f'{name}-plan.json'
            exported_source.write_text(json.dumps(payload['scenario']), encoding='utf-8')
            exported_plan.write_text(json.dumps(payload['plan']), encoding='utf-8')
            errors = verify_plan(loads(exported_source.read_text()), plan_loads(exported_plan.read_text()))
            if errors:
                raise RuntimeError(f'{name} audit: {errors}')
        staged = json.loads(request('POST', '/api/v03-preview', {}))
        if not staged['audited'] or staged['publishable'] or staged['partial_plan']:
            raise RuntimeError('v0.3 preview must be audited, complete, and non-publishable')
        if len(staged['material_pegs']) != 2 or not staged['stage_bookings']:
            raise RuntimeError('missing material or stage evidence')
        if json.loads(request('GET', '/api/bootstrap'))['plan'] != baseline['plan']:
            raise RuntimeError('simulation mutated baseline')
        request('POST', '/api/recalculate', {'settings': {'unknown': 1}}, expected=422)
        request('GET', '/api/bootstrap', headers={'Host': 'untrusted.example'}, expected=403)
        request('POST', '/api/recalculate', {'settings': {}},
                headers={'Origin': 'https://untrusted.example'}, expected=403)
        request('POST', '/api/publish', {}, expected=404)
        (destination / 'baseline-plan.csv').write_bytes(request('GET', '/api/plan.csv'))
        (destination / 'staged-preview.json').write_text(json.dumps(staged, indent=2), encoding='utf-8')
        summary = {
            'status': 'passed', 'synthetic': True, 'source': health['source'],
            'baseline_assignments': len(baseline['plan']['assignments']),
            'whatif_assignments': len(candidate['plan']['assignments']),
            'stage_bookings': len(staged['stage_bookings']),
            'material_pegs': len(staged['material_pegs']),
            'source_scenario_digest': baseline['plan']['scenario_digest'],
            'whatif_scenario_digest': candidate['plan']['scenario_digest'],
            'baseline_unchanged': True, 'exports_independently_verified': True,
            'host_origin_input_guards_checked': True, 'publish_endpoint_absent': True,
            'browser_executed': False, 'sql_server_used': False,
            'production_approved': False,
        }
        (destination / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
        return summary
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(run(args.out), sort_keys=True))


if __name__ == '__main__':
    main()
