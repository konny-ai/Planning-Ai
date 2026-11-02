"""#78 P16: optional wall-clock limit for the opt-in searches; the baseline is always evaluated."""
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
import tempfile

from planning_factory.adapters.json_io import dumps
from planning_factory.cli import main
from planning_factory.domain.models import Alternative, Demand, Resource
from planning_factory.engine.local_search import local_search
from planning_factory.engine.optimization import optimize
from planning_factory.engine.scheduler import plan
from planning_factory.engine.setup_search import search_setup_alternatives
from planning_factory.engine.time_budget import Deadline
from tests.helpers import dt, op, scenario

ROOT = Path(__file__).resolve().parents[1]


def ticking(step=1.0):
    """Fake clock: every reading advances by `step` seconds."""
    state = {'now': 0.0}

    def clock():
        state['now'] += step
        return state['now']
    return clock


def busy():
    return scenario([op(f'o{i}', 1, order_id=f'x{i}', due=dt(hour=9 + i), priority=10 - i) for i in range(5)])


class TimeLimitTests(unittest.TestCase):
    def test_optimize_keeps_the_baseline_and_says_the_limit_was_reached(self):
        s = busy()
        full = optimize(s)
        limited = optimize(s, time_limit_seconds=0.5, clock=ticking())
        self.assertEqual(4, len(full.candidates))
        self.assertEqual((('priority',), True), (tuple(c.policy for c in limited.candidates), limited.time_limit_reached))
        self.assertEqual(dumps(plan(s)), dumps(limited.plan))
        self.assertFalse(full.time_limit_reached)

    def test_local_search_stops_before_the_next_trial(self):
        s = busy()
        limited = local_search(s, budget=16, time_limit_seconds=0.5, clock=ticking())
        self.assertEqual((0, True), (limited.evaluated_neighbors, limited.time_limit_reached))
        generous = local_search(s, budget=16, time_limit_seconds=3600, clock=ticking())
        self.assertEqual(dumps(local_search(s, budget=16).plan), dumps(generous.plan))
        self.assertFalse(generous.time_limit_reached)

    def test_setup_search_returns_the_baseline_candidate(self):
        s = scenario([op('a', resource='m1'), op('b', resource='m2'),
                      op('c', alternatives=(Alternative((Demand('m1'),)), Alternative((Demand('m2'),))))],
                     resources=(Resource('m1', 'work'), Resource('m2', 'work')))
        rules = {'schema_version': 1, 'families': {'a': 'A', 'b': 'A', 'c': 'B'},
                 'machines': [{'resource_id': 'm1', 'initial_family': 'A',
                               'transitions': [{'from_family': 'A', 'to_family': 'B', 'seconds': 1800}]},
                              {'resource_id': 'm2', 'initial_family': 'A',
                               'transitions': [{'from_family': 'A', 'to_family': 'B', 'seconds': 3600}]}]}
        result = search_setup_alternatives(s, rules, budget=8, time_limit_seconds=0.5, clock=ticking())
        self.assertEqual((1, True), (result.candidates_evaluated, result.time_limit_reached))
        self.assertTrue(result.setup_audit.valid)

    def test_limit_values_are_checked(self):
        for value in (0, -1, True, 'ten', 86401):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Deadline(value)
        self.assertFalse(Deadline(None).expired())

    def test_cli_reports_the_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(['optimize', str(ROOT / 'examples' / 'workshop.json'), '-o', str(Path(tmp, 'p.json')),
                             '--time-limit', '3600'])
            self.assertEqual(0, code)
            self.assertFalse(json.loads(out.getvalue())['time_limit_reached'])


if __name__ == '__main__':
    unittest.main()
