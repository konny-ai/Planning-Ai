"""#78 P13, P18, P19: diagnostics that never change a plan."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from planning_factory.adapters.json_io import dumps
from planning_factory.cli import main
from planning_factory.domain.models import Dependency, Nesting
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.diagnostics import nesting_advance, overtime_candidates, stability
from planning_factory.engine.scheduler import plan
from tests.helpers import dt, op, scenario


class NestingAdvanceTests(unittest.TestCase):
    def test_member_pulled_forward_by_the_urgent_order_is_flagged(self):
        s = scenario([op('cut-urgent', 1, order_id='urgent', due=dt(hour=12)),
                      op('cut-later', 1, order_id='later', due=dt(day=30, hour=17)),
                      op('weld-later', 2, order_id='later', due=dt(day=30, hour=17),
                         predecessors=(Dependency('cut-later'),))],
                     nestings=(Nesting('PL-1', ('cut-later', 'cut-urgent'), 3600),), end=dt(day=31, hour=0))
        p = plan(s)
        before = dumps(p)
        result = nesting_advance(s, p, 24 * 3600)
        self.assertEqual(dumps(p), before)
        self.assertEqual(2, result['members_measured'])
        self.assertEqual(['cut-later'], [f['operation_id'] for f in result['flagged']])
        flagged = result['flagged'][0]
        self.assertEqual('2026-10-30T15:00:00Z', flagged['needed_start'])  # due minus the 2 h weld
        self.assertGreater(flagged['advance_seconds'], 10 * 86400)

    def test_foreign_plan_is_rejected(self):
        s = scenario([op('a', 1)])
        with self.assertRaises(ValidationError):
            nesting_advance(replace(s, id='other'), plan(s), 0)


class StabilityTests(unittest.TestCase):
    def setUp(self):
        self.before = scenario([op('a', 2, priority=5), op('b', 2, priority=5)])
        self.published = plan(self.before)
        self.recomputed = plan(replace(self.before, operations=self.before.operations + (op('rush', 2, priority=0),)))

    def test_moves_above_threshold_and_inside_the_window(self):
        moved = stability(self.published, self.recomputed)
        self.assertEqual(2, moved['moved_operations'])
        self.assertEqual(10800, moved['max_absolute_shift_seconds'])  # b: 10:00 -> 13:00 across lunch
        self.assertEqual(['rush'], moved['added_operations'])
        self.assertEqual(0, stability(self.published, self.recomputed, threshold_seconds=8 * 3600)['moved_operations'])
        only_first = stability(self.published, self.recomputed, window_end=dt(hour=9))
        self.assertEqual(['a'], [m['operation_id'] for m in only_first['moved']])

    def test_identical_plans_are_stable_and_threshold_is_checked(self):
        self.assertEqual(0, stability(self.published, self.published)['moved_operations'])
        with self.assertRaises(ValidationError):
            stability(self.published, self.published, threshold_seconds=-1)


class OvertimeTests(unittest.TestCase):
    def setUp(self):
        self.s = scenario([op('a', 4, order_id='a', due=dt(hour=17)), op('b', 4, order_id='b', due=dt(hour=17)),
                           op('c', 4, order_id='c', due=dt(hour=21))])

    def test_candidates_are_ranked_by_verified_gain_without_touching_the_plan(self):
        published = dumps(plan(self.s))
        result = overtime_candidates(self.s, [
            {'resource_id': 'm', 'start': '2026-10-22T17:00:00Z', 'end': '2026-10-22T21:00:00Z'},
            {'resource_id': 'm', 'start': '2026-10-19T17:00:00Z', 'end': '2026-10-19T21:00:00Z'}])
        self.assertEqual(1, result['baseline']['late_orders'])
        best = result['candidates'][0]
        self.assertEqual(('2026-10-19T17:00:00Z', 1, 14400), (best['start'], best['late_orders_gain'], best['added_seconds']))
        self.assertEqual(0, result['candidates'][1]['late_orders_gain'])
        self.assertEqual(published, dumps(plan(self.s)))

    def test_candidate_validation(self):
        for candidate in ({'resource_id': 'm'}, {'resource_id': 'x', 'start': '2026-10-19T17:00:00Z', 'end': '2026-10-19T18:00:00Z'},
                          {'resource_id': 'm', 'start': '2026-10-19T18:00:00Z', 'end': '2026-10-19T17:00:00Z'}):
            with self.assertRaises(ValidationError):
                overtime_candidates(self.s, [candidate])

    def test_cli_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            scenario_path, plan_path, candidates = Path(tmp, 's.json'), Path(tmp, 'p.json'), Path(tmp, 'c.json')
            scenario_path.write_text(dumps(self.s), encoding='utf-8')
            plan_path.write_text(dumps(plan(self.s)), encoding='utf-8')
            candidates.write_text(json.dumps([{'resource_id': 'm', 'start': '2026-10-19T17:00:00Z',
                                               'end': '2026-10-19T21:00:00Z'}]), encoding='utf-8')
            for args in (['overtime', str(scenario_path), str(candidates)],
                         ['stability', str(plan_path), str(plan_path), '--threshold-minutes', '30'],
                         ['nesting-advance', str(scenario_path), str(plan_path)]):
                out = io.StringIO()
                with redirect_stdout(out):
                    self.assertEqual(0, main(args), args)
                json.loads(out.getvalue())


if __name__ == '__main__':
    unittest.main()
