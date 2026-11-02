"""Release evidence preflight is deliberately never a production authorization."""
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
import unittest
from tools.release_readiness import REQUIRED_EVIDENCE, evaluate_evidence, main


def example():
    sha = 'a' * 40
    return {
        'schema_version': 1,
        'engine_sha': sha,
        'production_write_attempted': False,
        'evidence': [
            {'name': name, 'result': 'passed', 'url': 'https://github.com/example/test/actions/runs/123',
             'engine_sha': sha, 'recorded_at': '2026-10-21T08:00:00Z'}
            for name in REQUIRED_EVIDENCE
        ],
    }


class ReleaseReadinessTests(unittest.TestCase):
    def test_missing_approval_and_compliance_fail_closed(self):
        raw = example()
        raw['evidence'] = raw['evidence'][:-2]
        report = evaluate_evidence(raw)
        self.assertEqual(report['status'], 'blocked')
        self.assertFalse(report['production_authorized'])
        self.assertTrue(any('production-security-review' in x for x in report['reasons']))

    def test_structural_completeness_is_only_manual_review(self):
        report = evaluate_evidence(example())
        self.assertEqual(report['status'], 'ready_for_manual_review')
        self.assertEqual(report['checked_requirements'], len(REQUIRED_EVIDENCE))
        self.assertFalse(report['production_authorized'])
        self.assertIn('not authenticated', report['warning'])

    def test_duplicate_or_stale_evidence_rejected(self):
        e = example()
        e['evidence'].append(dict(e['evidence'][0]))
        e['evidence'][1]['engine_sha'] = 'b' * 40
        e['evidence'][2]['url'] = 'http://untrusted.local'
        e['evidence'][3]['recorded_at'] = '2026-10-21'
        bad = evaluate_evidence(e)
        self.assertEqual(bad['status'], 'blocked')
        self.assertTrue(any('duplicate' in s for s in bad['reasons']))
        self.assertTrue(any('stale' in s for s in bad['reasons']))
        self.assertTrue(any('https' in s for s in bad['reasons']))
        self.assertTrue(any('timestamp' in s for s in bad['reasons']))

    def test_hostless_https_and_future_evidence_do_not_pass(self):
        e = example()
        e['evidence'][0]['url'] = 'https://#missing'
        e['evidence'][1]['recorded_at'] = '9999-12-31T23:59:59Z'
        reasons = evaluate_evidence(e)['reasons']
        self.assertTrue(any('https evidence' in x for x in reasons))
        self.assertTrue(any('timestamp' in x for x in reasons))

    def test_duplicate_json_keys_and_hardlinked_output_are_rejected(self):
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / 'manifest.json'
            dest = Path(tmp) / 'evidence-hardlink.json'
            src.write_text(json.dumps(example()), encoding='utf-8')
            os.link(src, dest)
            previous = src.read_bytes()
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                main([str(src), '--output', str(dest)])
            self.assertEqual(src.read_bytes(), previous)
            dest.unlink()
            src.write_text('{"schema_version":1, "production_write_attempted":true, '
                           '"production_write_attempted":false}', encoding='utf-8')
            output = Path(tmp) / 'result.json'
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main([str(src), '--output', str(output)]), 2)
            self.assertFalse(output.exists())

    def test_production_write_attempt_not_accepted(self):
        e = example()
        e['production_write_attempted'] = True
        self.assertEqual(evaluate_evidence(e)['status'], 'blocked')
        e['schema_version'] = True
        # Schema booleans are not integers even though bool == 1 in Python.
        self.assertEqual(evaluate_evidence(e)['status'], 'blocked')

    def test_cli_refuses_to_overwrite_input(self):
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / 'manifest.json'
            src.write_text(json.dumps(example()), encoding='utf-8')
            original = src.read_bytes()
            with self.assertRaises(SystemExit), redirect_stderr(StringIO()):
                main([str(src), '--output', str(src)])
            self.assertEqual(src.read_bytes(), original)
            incomplete = example()
            incomplete['evidence'] = []
            src.write_text(json.dumps(incomplete), encoding='utf-8')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main([str(src)]), 2)


if __name__ == '__main__':
    unittest.main()
