"""#78 P2: slack-based dispatch policies, opt-in in optimize with priority as baseline."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads
from planning_factory.cli import main
from planning_factory.domain.models import Dependency, Nesting, Resource
from planning_factory.engine.optimization import ALL_POLICIES, DISPATCH_POLICIES, SLACK_POLICIES, optimize
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tests.helpers import dt, op, scenario

ROOT = Path(__file__).resolve().parents[1]


def avoidable_delay():
    """B has the better priority, but A has a long tail: serving B first makes A late for nothing."""
    return scenario([op('b1', 2, order_id='B', due=dt(hour=12), priority=0, resource='m'),
                     op('a1', 2, order_id='A', due=dt(hour=17), priority=1, resource='m'),
                     op('a2', 6, order_id='A', due=dt(hour=17), priority=1, resource='y', splittable=True,
                        predecessors=(Dependency('a1'),))],
                    resources=(Resource('m', 'work'), Resource('y', 'work')))


def late(p):
    return {o.order_id: o.tardiness_seconds for o in p.orders}


class SlackDispatchTests(unittest.TestCase):
    def test_priority_makes_an_avoidable_delay_that_slack_policies_avoid(self):
        s = avoidable_delay()
        self.assertGreater(late(plan(s))['A'], 0)
        for policy in SLACK_POLICIES:
            p = plan(s, dispatch_policy=policy)
            self.assertFalse(verify_plan(s, p), policy)
            self.assertEqual({'A': 0, 'B': 0}, late(p), policy)

    def test_optimize_default_is_unchanged_and_slack_policies_are_opt_in(self):
        s = avoidable_delay()
        self.assertEqual(DISPATCH_POLICIES, tuple(c.policy for c in optimize(s).candidates))
        result = optimize(s, ALL_POLICIES)
        self.assertEqual('min_slack', result.winner_policy)
        self.assertEqual('priority', result.candidates[0].policy)
        with self.assertRaises(ValueError):
            optimize(s, ('min_slack', 'priority'))

    def test_units_without_due_go_after_dated_ones_and_nests_are_handled(self):
        s = scenario([op('free', 1, order_id='F'), op('dated', 1, order_id='D', due=dt(day=20, hour=17)),
                      op('n1', 1, order_id='N1', due=dt(day=21, hour=17)), op('n2', 1, order_id='N2', due=dt(day=22, hour=17))],
                     nestings=(Nesting('N', ('n1', 'n2'), 3600),))
        for policy in SLACK_POLICIES:
            p = plan(s, dispatch_policy=policy)
            self.assertFalse(verify_plan(s, p))
            starts = {a.task_id: a.start for a in p.assignments}
            self.assertLess(starts['job:dated'], starts['job:free'], policy)

    def test_workshop_plans_are_valid_under_every_policy(self):
        s = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
        for policy in SLACK_POLICIES:
            self.assertFalse(verify_plan(s, plan(s, dispatch_policy=policy)), policy)

    def test_cli_policies_option(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp, 's.json')
            sp.write_text(dumps(avoidable_delay()), encoding='utf-8')
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, main(['optimize', str(sp), '-o', str(Path(tmp, 'p.json')), '--policies', 'all']))
            summary = json.loads(out.getvalue())
            self.assertEqual(('min_slack', 7), (summary['winner_policy'], summary['candidates_evaluated']))


if __name__ == '__main__':
    unittest.main()
