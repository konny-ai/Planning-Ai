"""CLI contract for the opt-in setup-aware planner and digest pairing."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from planning_factory.adapters.json_io import dumps, loads, plan_loads
from planning_factory.cli import main
from planning_factory.engine.setup_audit import audit_setups
from planning_factory.engine.verification import verify_plan
from .helpers import op, scenario


class SetupPlanCliTests(unittest.TestCase):
    def test_writes_adjusted_scenario_and_matching_plan(self):
        s = scenario([op('a'), op('b')])
        rules = {'schema_version': 1, 'families': {'a': 'A', 'b': 'B'},
                 'machines': [{'resource_id': 'm', 'initial_family': 'A',
                               'transitions': [{'from_family': 'A', 'to_family': 'B', 'seconds': 1800}]}]}
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, matrix = root/'scenario.json', root/'rules.json'
            out, adjusted, report = root/'plan.json', root/'adjusted.json', root/'report.html'
            src.write_text(dumps(s), encoding='utf-8')
            matrix.write_text(json.dumps(rules), encoding='utf-8')
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(['setup-plan', str(src), str(matrix), '-o', str(out),
                             '--scenario-output', str(adjusted), '--budget', '8', '--html', str(report)])
            self.assertEqual(code, 0)
            s2 = loads(adjusted.read_text(encoding='utf-8'))
            p = plan_loads(out.read_text(encoding='utf-8'))
            self.assertNotEqual(s2, s)
            self.assertEqual(verify_plan(s2, p), ())
            self.assertTrue(audit_setups(s2, p, rules).valid)
            self.assertEqual(json.loads(stdout.getvalue())['setup_audit']['valid'], True)
            self.assertIn('Planning Factory', report.read_text(encoding='utf-8'))
            original = src.read_text(encoding='utf-8')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                code = main(['setup-plan', str(src), str(matrix), '-o', str(src),
                             '--scenario-output', str(adjusted)])
            self.assertEqual(code, 1)
            self.assertEqual(src.read_text(encoding='utf-8'), original)
