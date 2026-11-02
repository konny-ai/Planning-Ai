"""#78 P1: time fence across scenario revisions."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads
from planning_factory.cli import main
from planning_factory.domain.models import Nesting
from planning_factory.engine.scheduler import plan
from planning_factory.engine.time_fence import check_fence, freeze_window, working_until
from planning_factory.engine.verification import verify_plan
from tests.helpers import alt, dt, op, scenario

ROOT = Path(__file__).resolve().parents[1]
WORKSHOP = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))


class TimeFenceTests(unittest.TestCase):
    def test_units_inside_the_window_do_not_move_when_urgent_work_arrives(self):
        before = scenario([op('a', 2, priority=5), op('b', 2, priority=5), op('c', 2, priority=5)])
        published = plan(before)
        urgent = replace(before, operations=before.operations + (op('rush', 2, priority=0),))
        self.assertEqual(dt(hour=8), plan(urgent).assignments[-1].start)  # without the fence rush jumps first
        fence = freeze_window(urgent, published, dt(hour=12))
        self.assertEqual(['job:a', 'job:b'], [f['unit'] for f in fence.frozen])
        new = plan(fence.scenario)
        self.assertFalse(verify_plan(fence.scenario, new))
        self.assertEqual([], check_fence(new, fence))
        starts = {a.task_id: a.start for a in new.assignments}
        self.assertEqual((dt(hour=8), dt(hour=10)), (starts['job:a'], starts['job:b']))
        self.assertGreaterEqual(starts['job:rush'], dt(hour=13))

    def test_nests_freeze_whole_with_their_bundle(self):
        published = plan(WORKSHOP)
        nest = next(a for a in published.assignments if a.task_id == 'nest:NEST-001')
        fence = freeze_window(WORKSHOP, published, nest.end)
        frozen = {f['unit']: f for f in fence.frozen}
        self.assertIn('nest:NEST-001', frozen)
        members = [o for o in fence.scenario.operations if o.id in ('ord-a/taglio', 'ord-b/taglio')]
        self.assertEqual({nest.start}, {o.fixed_start for o in members})
        new = plan(fence.scenario)
        self.assertEqual([], check_fence(new, fence))

    def test_units_that_cannot_be_frozen_are_explained(self):
        base = scenario([op('gone', 1), op('kept', 1), op('n1', 1), op('n2', 1)],
                        nestings=(Nesting('N', ('n1', 'n2'), 3600),))
        published = plan(base)
        # New revision: one operation deleted and the nest dissolved by the CAM.
        revised = replace(base, operations=tuple(o for o in base.operations if o.id != 'gone'), nestings=())
        fence = freeze_window(revised, published, dt(day=21))
        reasons = {s['unit']: s['reason'] for s in fence.skipped}
        self.assertIn('no longer in the scenario', reasons['job:gone'])
        self.assertIn('composition changed', reasons['nest:N'])
        self.assertEqual(['job:kept'], [f['unit'] for f in fence.frozen])

    def test_started_before_the_new_horizon_is_progress_not_fence(self):
        published = plan(scenario([op('a', 2)]))
        later = scenario([op('a', 2)], start=dt(hour=9))
        fence = freeze_window(later, published, dt(hour=12))
        self.assertEqual('started before the new horizon: report it as progress', fence.skipped[0]['reason'])

    def test_bundle_must_still_exist(self):
        s = scenario([op('a', 1)])
        published = plan(s)
        changed = replace(s, operations=(replace(s.operations[0], alternatives=(alt('m'), alt('m'))),))
        self.assertEqual(['job:a'], [f['unit'] for f in freeze_window(changed, published, dt(hour=12)).frozen])
        moved = replace(s, resources=s.resources + (replace(s.resources[0], id='y'),),
                        operations=(replace(s.operations[0], alternatives=(alt('y'),)),))
        self.assertIn('no longer an alternative', freeze_window(moved, published, dt(hour=12)).skipped[0]['reason'])

    def test_working_hours_window(self):
        s = scenario()
        self.assertEqual(dt(hour=12), working_until(s, 'work', dt(hour=8), 4))
        self.assertEqual(dt(hour=14), working_until(s, 'work', dt(hour=8), 5))
        self.assertEqual(dt(day=20, hour=9), working_until(s, 'work', dt(hour=8), 9))

    def test_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            workshop = ROOT / 'examples' / 'workshop.json'
            published, out = Path(tmp, 'published.json'), Path(tmp, 'frozen.json')
            published.write_text(dumps(plan(WORKSHOP)), encoding='utf-8')
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(0, main(['freeze-window', str(workshop), str(published), '-o', str(out),
                                          '--hours', '8', '--calendar', 'shop']))
            report = json.loads(stdout.getvalue())
            self.assertTrue(report['frozen'])
            frozen = loads(out.read_text(encoding='utf-8'))
            self.assertTrue(any(o.fixed_start for o in frozen.operations if o.id != 'ord-c/urgente'))
            with redirect_stdout(io.StringIO()):  # neither --until nor --hours
                self.assertEqual(1, main(['freeze-window', str(workshop), str(published), '-o', str(out)]))


if __name__ == '__main__':
    unittest.main()
