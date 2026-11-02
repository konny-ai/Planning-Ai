"""Read-only evidence completeness check for Planning Factory acceptance.

NEVER connects to SQL or authorizes production publication. Any evidence file
is untrusted, so a 'ready_for_manual_review' result is NOT a go-live permit.
"""
import argparse
from datetime import datetime, timezone, timedelta
from urllib.parse import urlsplit
import json
from pathlib import Path
import re
import sys

# Evidence names are stable and machine-readable; requirements may become
# stricter, but missing approvals must never silently become "passed".
REQUIRED_EVIDENCE = (
    'linux-python312',
    'linux-python313',
    'windows-python312',
    'sql-server-ci',
    'bancoa-clone-restore',
    'clone-publish-reread',
    'clone-rollback-conflict',
    'clone-trigger-audit',
    'setup-rich-test-scenarios',
    'material-pegging-tests',
    'technical-compatibility-tests',
    'multi-client-representative-benchmark',
    'business-kpi-acceptance',
    'operator-workflow-acceptance',
    'production-security-review',
)


def evaluate_evidence(document: dict) -> dict:
    """Evaluate structure and completeness only, never authenticity or consent."""
    reasons = []
    if not isinstance(document, dict) or type(document.get('schema_version')) is not int or document['schema_version'] != 1:
        return {'status': 'blocked', 'reasons': ['invalid evidence schema version'],
                'production_authorized': False}
    sha = document.get('engine_sha')
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{40}', sha):
        reasons.append('engine_sha must be a pinned 40-character lowercase SHA')
    if document.get('production_write_attempted') is not False:
        reasons.append('production_write_attempted must be explicitly false')
    entries = document.get('evidence')
    if not isinstance(entries, list):
        entries = []
        reasons.append('evidence must be an array')
    known = {}
    for e in entries:
        if not isinstance(e, dict) or not isinstance(e.get('name'), str):
            reasons.append('malformed evidence entry')
            continue
        name = e['name']
        if name in known:
            reasons.append(f'duplicate evidence: {name}')
            continue
        known[name] = e
    for name in REQUIRED_EVIDENCE:
        e = known.get(name)
        if e is None:
            reasons.append(f'missing: {name}')
            continue
        if e.get('result') != 'passed':
            reasons.append(f'not passed: {name}')
        url = e.get('url')
        if (not isinstance(url, str) or urlsplit(url).scheme != 'https'
                or not urlsplit(url).hostname):
            reasons.append(f'missing traceable https evidence: {name}')
        if e.get('engine_sha') != sha:
            reasons.append(f'wrong or stale engine revision: {name}')
        recorded_at = e.get('recorded_at')
        try:
            if not isinstance(recorded_at, str):
                raise ValueError('bad timestamp')
            stamp = datetime.fromisoformat(recorded_at.replace('Z', '+00:00'))
            if stamp.utcoffset() is None:
                raise ValueError('no timezone')
            if stamp.astimezone(timezone.utc) > datetime.now(timezone.utc) + timedelta(minutes=5):
                raise ValueError('future evidence')
        except ValueError:
            reasons.append(f'invalid evidence timestamp: {name}')
    return {
        'status': 'blocked' if reasons else 'ready_for_manual_review',
        'reasons': reasons,
        'checked_requirements': len(REQUIRED_EVIDENCE),
        'present_requirements': sum(name in known for name in REQUIRED_EVIDENCE),
        # This tool can never issue an authorization; only humans can.
        'production_authorized': False,
        'warning': ('Evidence files are assertions, not authenticated proof. '
                    'Manually verify all links, revisions, customer approvals, '
                    'database/trigger compatibility, backups and rollback. '
                    'Do not use this output to permit production writes.'),
    }


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON evidence key: {key}')
        result[key] = value
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', help='local acceptance evidence JSON')
    parser.add_argument('--output', help='optional output report JSON path')
    args = parser.parse_args(argv)
    if args.output and (Path(args.output).resolve() == Path(args.manifest).resolve()
                        or (Path(args.output).exists() and
                            Path(args.output).samefile(Path(args.manifest)))):
        parser.error('output must not overwrite evidence source')
    try:
        raw = json.loads(Path(args.manifest).read_text(encoding='utf-8'),
                         object_pairs_hook=_unique_object,
                         parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        report = evaluate_evidence(raw)
        text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + '\n'
        if args.output:
            Path(args.output).write_text(text, encoding='utf-8')
        print(text, end='')
        return 0 if report['status'] == 'ready_for_manual_review' else 2
    except (OSError, ValueError, TypeError) as exc:
        print(f'acceptance evidence error: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
