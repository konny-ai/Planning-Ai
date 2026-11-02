"""#78 P17: declared lexicographic objective profile for optimize and local search."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads
from planning_factory.cli import main
from planning_factory.engine.local_search import local_search
from planning_factory.engine.optimization import DEFAULT_OBJECTIVES, measure, optimize
from planning_factory.engine.scheduler import plan
from tests.helpers import dt, op, scenario

ROOT = Path(__file__).resolve().parents[1]


def busy():
    return scenario([op(f'o{i}', 1 + i % 3, order_id=f'x{i}', due=dt(hour=9 + i), priority=10 - i) for i in range(6)])


class ObjectiveProfileTests(unittest.TestCase):
    def test_default_profile_keeps_the_current_winner(self):
        for s in (busy(), loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))):
            self.assertEqual(optimize(s).winner_policy, optimize(s, objectives=DEFAULT_OBJECTIVES).winner_policy)

    def test_winner_follows_the_declared_order(self):
        s = busy()
        for objectives in (('makespan_seconds', 'late_orders'), ('max_tardiness_seconds',),
                           ('total_tardiness_seconds', 'unscheduled_operations')):
            result = optimize(s, objectives=objectives)
            best = min(c.key(objectives) for c in result.candidates)
            winner = next(c for c in result.candidates if c.policy == result.winner_policy)
            self.assertEqual(best, winner.key(objectives), objectives)
            self.assertEqual(winner.policy, next(c.policy for c in result.candidates if c.key(objectives) == best))

    def test_stability_objective_needs_the_published_plan_and_prefers_it(self):
        s = busy()
        with self.assertRaises(ValueError):
            optimize(s, objectives=('moved_operations',))
        published = plan(s)
        result = optimize(s, objectives=('moved_operations', 'late_orders'), published=published)
        self.assertEqual('priority', result.winner_policy)
        self.assertEqual(0, next(c for c in result.candidates if c.policy == 'priority').moved_operations)
        searched = local_search(s, budget=16, objectives=('moved_operations',), published=published)
        self.assertFalse(searched.improved)

    def test_max_tardiness_is_measured(self):
        s = busy()
        p = plan(s)
        self.assertEqual(max(o.tardiness_seconds or 0 for o in p.orders), measure(s, p, 'priority').max_tardiness_seconds)

    def test_invalid_profiles(self):
        for objectives in ((), ('speed',), ('late_orders', 'late_orders')):
            with self.subTest(objectives=objectives), self.assertRaises(ValueError):
                optimize(busy(), objectives=objectives)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            s_path, p_path = Path(tmp, 's.json'), Path(tmp, 'p.json')
            s_path.write_text(dumps(busy()), encoding='utf-8')
            p_path.write_text(dumps(plan(busy())), encoding='utf-8')
            out = io.StringIO()
            with redirect_stdout(out):
                code = main(['optimize', str(s_path), '-o', str(Path(tmp, 'o.json')),
                             '--objectives', 'moved_operations,late_orders', '--published', str(p_path)])
            self.assertIn(code, (0, 2))
            self.assertEqual('priority', json.loads(out.getvalue())['winner_policy'])


if __name__ == '__main__':
    unittest.main()
