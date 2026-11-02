"""End-to-end CLI checks for the setup machine projection contract."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from planning_factory.adapters.json_io import dumps
from planning_factory.engine.scheduler import plan
from tools.project_setup_bookings import main
from .helpers import dt, op, scenario


class SetupProjectionCliTests(unittest.TestCase):
    def test_report_and_inputs_are_distinct(self):
        s = scenario([op('a'), op('b', release=dt(hour=10))])
        p = plan(s)
        rules = {
            'schema_version': 1,
            'families': {'a': 'A', 'b': 'B'},
            'machines': [
                {'resource_id': 'm', 'initial_family': 'A',
                 'transitions': [{'from_family': 'A', 'to_family': 'B',
                                  'seconds': 3600}]},
            ],
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            scenario_file = root / 'scenario.json'
            plan_file = root / 'plan.json'
            rules_file = root / 'rules.json'
            output = root / 'report.json'
            scenario_file.write_text(dumps(s), encoding='utf-8')
            plan_file.write_text(dumps(p), encoding='utf-8')
            rules_file.write_text(json.dumps(rules), encoding='utf-8')
            arguments = [str(scenario_file), str(plan_file), str(rules_file),
                         '--output', str(output), '--placement', 'latest',
                         '--split-policy', 'atomic']
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                self.assertEqual(main(arguments), 0)
            report = json.loads(output.read_text(encoding='utf-8'))
            self.assertEqual(report['total_machine_setup_seconds'], 3600)
            self.assertEqual(len(report['reservations']), 1)
            original_plan = plan_file.read_text(encoding='utf-8')
            with self.assertRaises(SystemExit), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                main([*arguments[:4], str(plan_file), *arguments[5:]])
            self.assertEqual(plan_file.read_text(encoding='utf-8'), original_plan)

    def test_invalid_setup_time_fails_without_creating_success_file(self):
        s = scenario([op('a'), op('b')])  # no free working-time gap
        p = plan(s)
        rules = {
            'schema_version': 1, 'families': {'a': 'A', 'b': 'B'},
            'machines': [{
                'resource_id': 'm', 'initial_family': 'A',
                'transitions': [{'from_family': 'A', 'to_family': 'B',
                                 'seconds': 3600}],
            }],
        }
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            f = [root / name for name in ('scenario.json', 'plan.json', 'rules.json', 'output.json')]
            f[0].write_text(dumps(s), encoding='utf-8')
            f[1].write_text(dumps(p), encoding='utf-8')
            f[2].write_text(json.dumps(rules), encoding='utf-8')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                code = main([str(f[0]), str(f[1]), str(f[2]),
                             '--output', str(f[3]), '--placement', 'latest',
                             '--split-policy', 'atomic'])
            self.assertEqual(code, 2)
            self.assertFalse(f[3].exists())


if __name__ == '__main__':
    unittest.main()
