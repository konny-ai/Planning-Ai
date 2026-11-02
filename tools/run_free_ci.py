"""Local repeatable checks with evidence; never starts GitHub Actions or SQL."""
import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def source_digest():
    digest = hashlib.sha256()
    files = [ROOT / 'pyproject.toml']
    for folder in ('src', 'tests', 'tools', 'contracts'):
        files.extend(p for p in (ROOT / folder).rglob('*') if p.is_file() and p.suffix in
                     ('.py', '.js', '.css', '.html', '.json', '.sql'))
    for path in sorted(files):
        digest.update(str(path.relative_to(ROOT)).encode() + b'\0' + path.read_bytes() + b'\0')
    return digest.hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True,
                        help='new evidence directory; existing results are never overwritten')
    args = parser.parse_args(argv)
    folder = args.out.resolve()
    if folder.exists() and any(folder.iterdir()):
        parser.error('choose a new or empty output directory')
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'temp').mkdir(exist_ok=True)
    env = dict(os.environ)
    env['PYTHONPATH'] = os.pathsep.join((str(ROOT / 'src'), str(ROOT)))
    env['TMPDIR'] = str(folder / 'temp')
    env['TMP'] = env['TEMP'] = env['TMPDIR']
    # "never starts SQL": an inherited connection would run the SQL Server test (PR #80).
    env.pop('FACTORY_PUBLISH_SQL', None)
    commands = [
        ('compile', ['-m', 'compileall', '-q', 'src']),
        ('unittest', ['-m', 'unittest', 'discover', '-s', 'tests', '-t', '.']),
        ('scenario-schema', ['tools/generate_schema.py', '--check']),
        ('canonical-schema', ['tools/generate_canonical_schema.py', '--check']),
        ('fullstack-http', ['tools/fullstack_smoke.py', '--out', str(folder / 'fullstack')]),
        ('cliente-material-setup-wip', ['tools/cliente_regression_matrix.py', '--out', str(folder / 'cliente-matrix')]),
    ]
    results = []
    before = source_digest()
    for name, arguments in commands:
        with (folder / f'{name}.log').open('w', encoding='utf-8') as log:
            result = subprocess.run([sys.executable, *arguments], cwd=ROOT, env=env,
                                    stdout=log, stderr=subprocess.STDOUT, timeout=600)
        results.append({'name': name, 'arguments': arguments, 'exit_code': result.returncode})
        print(f'{name}: {"passed" if result.returncode == 0 else "FAILED"}', flush=True)
    unchanged = before == source_digest()
    summary = {'recorded_at': datetime.now(UTC).isoformat(), 'python': sys.version,
               'source_sha256': before, 'source_unchanged_during_checks': unchanged,
               'checks': results, 'passed': unchanged and all(r['exit_code'] == 0 for r in results),
               'browser_executed': False, 'sql_server_executed': False,
               'production_approved': False}
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
