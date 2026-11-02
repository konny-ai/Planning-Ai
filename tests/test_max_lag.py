"""#78 P6 hard version: predecessors postponed to respect max_lag_seconds, or a reason for each refusal."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads, plan_loads
from planning_factory.cli import main
from planning_factory.domain.models import Dependency, Resource
from planning_factory.engine.diagnostics import waits_over_limit
from planning_factory.engine.max_lag import enforce_max_lag
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tests.helpers import dt, op, scenario

MACHINES = (Resource('m', 'work'), Resource('x', 'work'), Resource('y', 'work'))


def cut_and_bend(max_lag, **cut):
    """'cut' ends at 9:00; 'blocker' keeps the bending machine until 12:00, so 'bend' starts at 13:00."""
    return scenario([op('cut', 1, resource='m', order_id='o', **cut),
                     op('blocker', 4, resource='x', priority=-1),
                     op('bend', 1, resource='x', order_id='o',
                        predecessors=(Dependency('cut', max_lag_seconds=max_lag),))],
                    resources=MACHINES)


def chain(max_lag=3600):
    """a -> b -> c; c waits for a 7 h blocker: moving b later creates the a -> b wait repaired next."""
    return scenario([op('a', 1, resource='m', order_id='o'),
                     op('b', 1, resource='y', order_id='o', predecessors=(Dependency('a', max_lag_seconds=max_lag),)),
                     op('blocker', 7, resource='x', priority=-1, splittable=True),
                     op('c', 1, resource='x', order_id='o', predecessors=(Dependency('b', max_lag_seconds=max_lag),))],
                    resources=MACHINES)


def start(p, operation_id):
    return next(a.start for a in p.assignments if operation_id in a.operation_ids)


class EnforceMaxLagTests(unittest.TestCase):
    def test_predecessor_is_postponed_into_the_allowed_window(self):
        s = cut_and_bend(3600)
        result = enforce_max_lag(s)
        self.assertEqual((1, 1, 0), (result.rounds, result.waits_before, result.waits_after))
        self.assertEqual((('cut', dt(hour=11)),), result.releases)
        self.assertEqual((dt(hour=11), dt(hour=13)), (start(result.plan, 'cut'), start(result.plan, 'bend')))
        self.assertFalse(verify_plan(result.scenario, result.plan))
        self.assertEqual(0, waits_over_limit(result.scenario, result.plan)['over_limit'])
        self.assertEqual(result.tardiness_before_seconds, result.tardiness_after_seconds)
        self.assertEqual(dt(hour=11), next(o for o in result.scenario.operations if o.id == 'cut').release)

    def test_nothing_to_do_returns_the_plain_plan(self):
        s = cut_and_bend(5 * 3600)
        result = enforce_max_lag(s)
        self.assertEqual((0, (), ()), (result.rounds, result.releases, result.unrepaired))
        self.assertEqual(dumps(plan(s)), dumps(result.plan))
        self.assertIs(s, result.scenario)

    def test_repairs_cascade_upstream_over_rounds(self):
        result = enforce_max_lag(chain())
        self.assertEqual((2, 1, 0), (result.rounds, result.waits_before, result.waits_after))
        self.assertEqual([dt(hour=14), dt(hour=15), dt(hour=16)], [start(result.plan, x) for x in 'abc'])
        limited = enforce_max_lag(chain(), max_rounds=1)
        self.assertEqual([('a', 'b', 'ROUNDS_EXHAUSTED')],
                         [(u['predecessor_id'], u['operation_id'], u['code']) for u in limited.unrepaired])

    def test_refusals_carry_a_reason(self):
        cases = {'PREDECESSOR_PINNED': cut_and_bend(3600, fixed_start=dt(hour=8)),
                 'PREDECESSOR_DUE': cut_and_bend(3600, due=dt(hour=10)),
                 'NO_CALENDAR_WINDOW': cut_and_bend(0)}  # bend starts after lunch: nothing can end at 13:00
        for code, s in cases.items():
            result = enforce_max_lag(s)
            self.assertEqual([code], [u['code'] for u in result.unrepaired], code)
            self.assertEqual((0, 1), (result.rounds, result.waits_after), code)
            self.assertFalse(verify_plan(result.scenario, result.plan))

    def test_invalid_rounds(self):
        for value in (0, 1.5):
            with self.assertRaises(ValueError):
                enforce_max_lag(cut_and_bend(3600), max_rounds=value)

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            sp, so, pp = Path(tmp, 's.json'), Path(tmp, 'repaired.json'), Path(tmp, 'p.json')
            sp.write_text(dumps(cut_and_bend(3600)), encoding='utf-8')
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, main(['enforce-waits', str(sp), '-o', str(pp), '--scenario-out', str(so)]))
            summary = json.loads(out.getvalue())
            self.assertEqual((1, 0), (summary['waits_over_limit_before'], summary['waits_over_limit_after']))
            repaired = loads(so.read_text(encoding='utf-8'))
            self.assertFalse(verify_plan(repaired, plan_loads(pp.read_text(encoding='utf-8'))))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertNotEqual(0, main(['enforce-waits', str(sp), '-o', str(sp), '--scenario-out', str(so)]))


if __name__ == '__main__':
    unittest.main()
