"""Offline dashboard and snapshot-comparison regression coverage."""
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from planning_factory.adapters.json_io import dumps
from planning_factory.adapters.reports import html_comparison, html_report
from planning_factory.cli import main
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op, scenario


class OfflineDashboardTests(unittest.TestCase):
    def test_gantt_filters_resources_and_stays_offline(self):
        s = scenario([op('cut', order_id='WO-1')])
        p = plan(s)
        html = html_report(s, p)
        self.assertIn('id="task-filter"', html)
        self.assertIn('data-filter="job:cut cut WO-1 m"', html)
        self.assertIn('id="gantt-body"', html)
        self.assertIn('Carico delle risorse', html)
        self.assertIn('Secondi unità prenotati', html)
        self.assertIn('document.getElementById', html)
        self.assertNotIn('https://', html)
        self.assertNotIn('src="', html)
        self.assertEqual(verify_plan(s, p), ())

    def test_untrusted_labels_are_escaped_everywhere(self):
        name = '<script>alert(1)</script>"&'
        s = scenario([op(name, order_id=name)])
        html = html_report(s, plan(s))
        self.assertNotIn('<script>', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('&quot;', html)

    def test_comparison_escapes_names_and_reports_shifts(self):
        name = '<img src=x onerror=alert(1)>'
        s = scenario([op(name), op('long', hours=3)])
        before = plan(s)
        after = plan(replace(s, operations=(replace(s.operations[0], priority=100), s.operations[1])))
        html = html_comparison(before, after)
        self.assertIn('Confronto piani', html)
        self.assertNotIn('<img src=x', html)
        self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html)

    def test_mismatched_plan_ids_rejected(self):
        s = scenario([op()])
        baseline = plan(s)
        with self.assertRaises(ValueError):
            html_comparison(baseline, replace(baseline, scenario_id='different'))

    def test_cli_compare_produces_html_and_refuses_input_overwrite(self):
        s = scenario([op()])
        p = plan(s)
        with TemporaryDirectory() as tmp:
            a = Path(tmp) / 'before.json'
            b = Path(tmp) / 'after.json'
            output = Path(tmp) / 'comparison.html'
            a.write_text(dumps(p), encoding='utf-8')
            b.write_text(dumps(p), encoding='utf-8')
            with redirect_stdout(StringIO()) as stdout:
                self.assertEqual(main(['compare', str(a), str(b), '--html', str(output)]), 0)
            self.assertEqual(json.loads(stdout.getvalue())['changed_operations'], [])
            self.assertIn('Nessuna variazione', output.read_text(encoding='utf-8'))
            original = a.read_text(encoding='utf-8')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(['compare', str(a), str(b), '--html', str(a)]), 1)
            self.assertEqual(a.read_text(encoding='utf-8'), original)
