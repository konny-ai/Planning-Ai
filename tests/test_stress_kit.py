"""Synthetic stress kit: stable inputs/results and strict bounds."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from planning_factory.adapters.json_io import dumps, loads
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tools.stress_kit import main, make_scenario, run_stress


class StressKitTests(unittest.TestCase):
    def test_generator_seeded_and_roundtrips(self):
        a = make_scenario(seed=42, operations=12, machines=3, horizon_days=21)
        b = make_scenario(seed=42, operations=12, machines=3, horizon_days=21)
        other = make_scenario(seed=43, operations=12, machines=3, horizon_days=21)
        self.assertEqual(dumps(a), dumps(b))
        self.assertNotEqual(dumps(a), dumps(other))
        self.assertEqual(loads(dumps(a)), a)
        self.assertEqual(verify_plan(a, plan(a)), ())
        self.assertTrue(any(o.predecessors for o in a.operations))
        self.assertTrue(any(len(o.alternatives) == 2 for o in a.operations))

    def test_repeatable_sha_for_priority_and_local_search(self):
        for policy in ('priority', 'local-search'):
            with self.subTest(policy=policy):
                result = run_stress(seed=7, operations=12, machines=3,
                                    horizon_days=21, repeat=2, policy=policy, budget=2)
                self.assertTrue(result['identical_repeats'])
                self.assertEqual(len(result['plan_sha256']), 64)
                self.assertEqual(len(result['seconds_per_run']), 2)
                self.assertEqual(result['assigned_tasks'] + result['unscheduled_tasks'], 12)

    def test_rejects_invalid_bounds_without_creating_huge_scenarios(self):
        for params in ({'operations': 0}, {'operations': 1001}, {'machines': 0},
                       {'horizon_days': 121}, {'seed': -1}, {'seed': True}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                make_scenario(**params)
        for params in ({'repeat': 6}, {'repeat': 0}, {'repeat': True},
                       {'policy': 'nope'}, {'budget': 17},
                       {'operations': 81, 'policy': 'local-search'}):
            with self.subTest(params=params), self.assertRaises(ValueError):
                run_stress(**params)

    def test_cli_json_and_failure_exit(self):
        with TemporaryDirectory() as temp:
            destination = Path(temp) / 'metrics.json'
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(['--seed', '3', '--operations', '8', '--machines', '2',
                             '--repeat', '2', '--json', str(destination)])
            self.assertEqual(code, 0)
            data = json.loads(stdout.getvalue())
            self.assertEqual(data, json.loads(destination.read_text(encoding='utf-8')))
            self.assertEqual(data['policy'], 'priority')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()) as err:
                self.assertEqual(main(['--operations', '1001']), 1)
            self.assertIn('error', json.loads(err.getvalue()))
