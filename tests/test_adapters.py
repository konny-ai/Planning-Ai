from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
import tempfile
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from planning_factory.adapters.http_api import make_server
from planning_factory.adapters.json_io import dumps, plan_loads, scenario_digest
from planning_factory.adapters.reports import csv_report, html_report
from planning_factory.adapters.store import ConflictError, SnapshotStore
from planning_factory.cli import main
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.simulation import freeze
from .helpers import dt, op, scenario


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = SnapshotStore(Path(self.tmp.name) / 'planning.sqlite')
        self.s = scenario([op()])

    def test_snapshot_round_trip(self):
        self.assertEqual(self.store.save(self.s), 1)
        revision, value = self.store.load(self.s.id)
        self.assertEqual((revision, value), (1, self.s))

    def test_optimistic_conflict(self):
        self.store.save(self.s)
        with self.assertRaises(ConflictError):
            self.store.save(self.s)
        self.assertEqual(self.store.save(self.s, expected_revision=1), 2)

    def test_plan_round_trip(self):
        self.store.save(self.s)
        p = plan(self.s)
        self.store.save_plan(self.s, p)
        self.assertEqual(self.store.load_plan(self.s.id, scenario_digest(self.s)), p)

    def test_reject_stale_plan(self):
        self.store.save(self.s)
        changed = replace(self.s, operations=(op(release=dt(20)),))
        with self.assertRaises(ConflictError):
            self.store.save_plan(changed, plan(changed))

    def test_old_plan_kept_after_new_revision(self):
        self.store.save(self.s)
        p = plan(self.s)
        self.store.save_plan(self.s, p)
        changed = replace(self.s, operations=(op(release=dt(20)),))
        self.store.save(changed, expected_revision=1)
        self.assertEqual(self.store.load_plan(self.s.id, p.scenario_digest), p)

    def test_forged_plan_not_persisted(self):
        with self.assertRaises(ValidationError):
            self.store.save_plan(self.s, replace(plan(self.s), assignments=()))


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.s = scenario([op()])
        self.source = self.root / 'scenario.json'
        self.source.write_text(dumps(self.s))

    def run_cli(self, *args):
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(map(str, args)))
        return code, out.getvalue(), err.getvalue()

    def test_end_to_end_files(self):
        output = self.root / 'plan.json'
        report = self.root / 'report.html'
        csv = self.root / 'plan.csv'
        self.assertEqual(self.run_cli('validate', self.source)[0], 0)
        self.assertEqual(self.run_cli('schedule', self.source, '-o', output, '--html', report, '--csv', csv)[0], 0)
        self.assertEqual(self.run_cli('verify', self.source, output)[0], 0)
        self.assertIn('job:a', report.read_text())
        self.assertIn('resource_id', csv.read_text())
        self.assertEqual(self.run_cli('compare', output, output)[0], 0)

    def test_partial_exit_code(self):
        self.source.write_text(dumps(scenario([op(hours=100)])))
        self.assertEqual(self.run_cli('schedule', self.source, '-o', self.root / 'plan.json')[0], 2)

    def test_invalid_exit_code(self):
        self.source.write_text('{}')
        code, _, err = self.run_cli('validate', self.source)
        self.assertEqual(code, 1)
        self.assertIn('error', json.loads(err))

    def test_no_overwrite_scenario(self):
        original = self.source.read_text()
        self.assertEqual(self.run_cli('schedule', self.source, '-o', self.source)[0], 1)
        self.assertEqual(self.source.read_text(), original)

    def test_report_escapes_html(self):
        s = scenario([op(id='<script>alert(1)</script>')])
        html = html_report(s, plan(s))
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)

    def test_csv_formula_escape(self):
        s = scenario([op(id='=formula')])
        # Task IDs have a job: prefix; member IDs need independent escaping.
        self.assertIn("'=formula", csv_report(plan(s)))

    def test_freeze_unknown_rejected(self):
        with self.assertRaises(ValidationError):
            freeze(self.s, plan(self.s), ['missing'])


class HTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server(port=0)
        cls.thread = Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, path, payload, **headers):
        req = Request(self.base + path, data=dumps(payload).encode(), headers={'Content-Type': 'application/json', **headers})
        with urlopen(req, timeout=3) as response:
            return response.status, json.load(response)

    def test_health(self):
        with urlopen(self.base + '/health', timeout=3) as response:
            self.assertEqual(json.load(response)['status'], 'ok')

    def test_schedule_verify_flow(self):
        s = scenario([op()])
        status, p = self.post('/v1/plan', s)
        self.assertEqual(status, 200)
        status, result = self.post('/v1/verify', {'scenario': s, 'plan': p})
        self.assertTrue(result['valid'])

    def test_optimize_is_explicit_and_auditable(self):
        s = scenario([op('a', hours=3, priority=-2, due=dt(22, 17), order_id='A'),
                      op('b', hours=2, due=dt(19, 11), order_id='B')])
        status, original = self.post('/v1/plan', s)
        self.assertEqual(status, 200)
        status, tuned = self.post('/v1/optimize', s)
        self.assertEqual(status, 200)
        self.assertEqual(tuned['winner_policy'], 'earliest_due')
        self.assertEqual(tuned['candidates'][0]['policy'], 'priority')
        self.assertNotEqual(tuned['plan']['assignments'], original['assignments'])
        status, audited = self.post('/v1/verify', {'scenario': s, 'plan': tuned['plan']})
        self.assertTrue(audited['valid'])

    def test_validate(self):
        self.assertTrue(self.post('/v1/validate', scenario([op()]))[1]['valid'])

    def test_bad_scenario(self):
        with self.assertRaises(HTTPError) as error:
            self.post('/v1/plan', {})
        self.assertEqual(error.exception.code, 422)

    def test_cross_origin_denied(self):
        with self.assertRaises(HTTPError) as error:
            self.post('/v1/plan', scenario([op()]), Origin='https://example.test')
        self.assertEqual(error.exception.code, 403)

    def test_unknown_route(self):
        with self.assertRaises(HTTPError) as error:
            self.post('/v1/unknown', {})
        self.assertEqual(error.exception.code, 404)

    def test_early_rejections_answer_with_their_status(self):
        # The server used to answer without reading the body; on Windows the unread data
        # reset the socket (WinError 10053) and the client saw an abort instead of the code.
        for _ in range(40):
            for path, headers, code in (('/v1/unknown', {}, 404),
                                        ('/v1/plan', {'Origin': 'https://example.test'}, 403)):
                with self.assertRaises(HTTPError) as error:
                    self.post(path, scenario([op()]), **headers)
                self.assertEqual(error.exception.code, code)

    def test_non_loopback_binding_refused(self):
        with self.assertRaises(ValueError):
            make_server('0.0.0.0', 0)
