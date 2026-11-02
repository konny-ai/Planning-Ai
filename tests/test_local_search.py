"""Bounded local-search behavior, invariants, and CLI safety."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from planning_factory.adapters.json_io import dumps, plan_loads
from planning_factory.cli import main
from planning_factory.domain.models import Dependency, Nesting
from planning_factory.engine.local_search import local_search
from planning_factory.engine.optimization import measure
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op, scenario


def conflicting_priorities():
    return scenario([
        op('long', hours=3, priority=-10, due=dt(22, 17), order_id='large'),
        op('urgent', hours=2, priority=0, due=dt(19, 11), order_id='express'),
    ])


class LocalSearchTests(unittest.TestCase):
    def test_single_swap_improves_tardiness_and_preserves_scenario(self):
        s = conflicting_priorities()
        snapshot = dumps(s)
        result = local_search(s, budget=1)
        self.assertTrue(result.improved)
        self.assertEqual(result.evaluated_neighbors, 1)
        self.assertEqual(result.accepted_improvements, 1)
        self.assertEqual(result.baseline.policy, 'priority')
        self.assertLess(result.winner.ranking, result.baseline.ranking)
        self.assertEqual(result.winner.total_tardiness_seconds, 0)
        self.assertEqual(verify_plan(s, result.plan), ())
        self.assertEqual(dumps(s), snapshot)

    def test_deterministic_and_never_worse_than_baseline(self):
        for s in (scenario([op()]), conflicting_priorities()):
            a = local_search(s, budget=16)
            b = local_search(s, budget=16)
            self.assertEqual(dumps(a), dumps(b))
            self.assertLessEqual(a.winner.ranking, measure(s, plan(s), 'priority').ranking)
            self.assertEqual(verify_plan(s, a.plan), ())

    def test_validates_budget_and_order(self):
        s = scenario([op('a'), op('b')])
        for invalid in (0, 65, True, 1.5, '8'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                local_search(s, budget=invalid)
        with self.assertRaises(ValueError):
            plan(s, dispatch_order=('job:a',))
        with self.assertRaises(ValueError):
            plan(s, dispatch_order=('job:a', 'job:a'))
        with self.assertRaises(ValueError):
            plan(s, dispatch_order=('job:a', 'job:missing'))
        with self.assertRaises(ValueError):
            plan(s, dispatch_policy='earliest_due', dispatch_order=('job:b', 'job:a'))

    def test_respects_dependency_and_atomic_nesting(self):
        s = scenario([
            op('a', hours=2, order_id='a'),
            op('b', hours=1, predecessors=(Dependency('a'),), order_id='b'),
            op('c', hours=1, order_id='c')
        ])
        p = local_search(s, budget=20)
        self.assertEqual(verify_plan(s, p.plan), ())
        by_op = {o: a for a in p.plan.assignments for o in a.operation_ids}
        self.assertGreaterEqual(by_op['b'].start, by_op['a'].end)
        nested = scenario([op('a', order_id='a'), op('b', order_id='b'),
                           op('c', hours=2, order_id='c')],
                          nestings=(Nesting('ab', ('a', 'b'), 3600),))
        n = local_search(nested, budget=20)
        self.assertEqual(verify_plan(nested, n.plan), ())
        self.assertIn('nest:ab', n.final_dispatch_order)
        self.assertEqual(len(n.final_dispatch_order), 2)

    def test_fixed_work_preserved(self):
        s = scenario([op('a', fixed_start=dt(hour=8)), op('b')])
        p = local_search(s, budget=16)
        self.assertEqual(verify_plan(s, p.plan), ())
        original = plan(s)
        self.assertEqual(next(a for a in p.plan.assignments if a.task_id == 'job:a'),
                         next(a for a in original.assignments if a.task_id == 'job:a'))

    def test_cli_with_budget_and_diagnostics(self):
        with TemporaryDirectory() as t:
            root = Path(t)
            s = conflicting_priorities()
            source = root / 'scenario.json'
            output = root / 'result.json'
            metrics = root / 'metrics.json'
            source.write_text(dumps(s), encoding='utf-8')
            stdout = StringIO()
            with redirect_stdout(stdout):
                code = main(['local-search', str(source), '-o', str(output),
                             '--budget', '1', '--metrics', str(metrics)])
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(stdout.getvalue())['evaluated_neighbors'], 1)
            self.assertEqual(verify_plan(s, plan_loads(output.read_text(encoding='utf-8'))), ())
            self.assertTrue(json.loads(metrics.read_text(encoding='utf-8'))['winner']
                            ['total_tardiness_seconds'] == 0)
            original = source.read_text(encoding='utf-8')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                code = main(['local-search', str(source), '-o', str(output),
                             '--metrics', str(source)])
            self.assertEqual(code, 1)
            self.assertEqual(source.read_text(encoding='utf-8'), original)
