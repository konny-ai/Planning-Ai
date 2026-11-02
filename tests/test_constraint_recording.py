"""#88: the scheduler records the constraint that fixed each start; the reconstruction must agree."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from dataclasses import replace
from pathlib import Path

from planning_factory.adapters.json_io import dumps, loads
from planning_factory.cli import main
from planning_factory.domain.models import Dependency
from planning_factory.engine.constraint_chain import constraint_chain
from planning_factory.engine.scheduler import plan
from tests.helpers import dt, op, scenario

ROOT = Path(__file__).resolve().parents[1]


def recorded(p, task_id):
    a = next(x for x in p.assignments if x.task_id == task_id)
    return next(e for e in a.explanation if e.startswith('constraint='))


class RecordingTests(unittest.TestCase):
    def test_off_by_default_so_published_plans_do_not_change(self):
        s = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
        self.assertEqual(dumps(plan(s)), (ROOT / 'examples' / 'workshop-plan.json').read_text(encoding='utf-8'))
        self.assertFalse(any(e.startswith('constraint=') for a in plan(s).assignments for e in a.explanation))

    def test_each_kind_is_recorded(self):
        s = scenario([op('a', 2, order_id='o', priority=0), op('b', 1, order_id='o', predecessors=(Dependency('a'),)),
                      op('c', 1, priority=1), op('late', 1, release=dt(hour=12, minute=30), resource='x'),
                      op('pin', 1, resource='x', fixed_start=dt(day=20, hour=9))],
                     resources=scenario().resources + (replace(scenario().resources[0], id='x'),))
        p = plan(s, record_constraints=True)
        self.assertEqual('constraint=horizon;calendar', recorded(p, 'job:a'))  # shift opens at 8:00
        self.assertEqual('constraint=predecessor:job:a', recorded(p, 'job:b'))
        self.assertEqual('constraint=capacity', recorded(p, 'job:c'))
        self.assertEqual('constraint=release;calendar', recorded(p, 'job:late'))
        self.assertEqual('constraint=fixed', recorded(p, 'job:pin'))
        agreement = constraint_chain(s, p)['scheduler_agreement']
        self.assertEqual((5, 5, []), (agreement['checked'], agreement['agree'], agreement['mismatches']))

    def test_agreement_on_the_examples_and_disagreement_is_reported(self):
        for path in ('examples/workshop.json',):
            s = loads((ROOT / path).read_text(encoding='utf-8'))
            agreement = constraint_chain(s, plan(s, record_constraints=True))['scheduler_agreement']
            self.assertEqual([], agreement['mismatches'], path)
        s = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
        p = plan(s, record_constraints=True)
        tampered = replace(p, assignments=tuple(
            replace(a, explanation=tuple('constraint=capacity' if e.startswith('constraint=') else e for e in a.explanation))
            if a.task_id == 'job:ord-a/saldatura' else a for a in p.assignments))
        mismatches = constraint_chain(s, tampered)['scheduler_agreement']['mismatches']
        self.assertEqual([('job:ord-a/saldatura', 'capacity', 'predecessor')],
                         [(m['unit'], m['scheduler'], m['reconstruction']) for m in mismatches])

    def test_plans_without_recording_have_no_agreement_section(self):
        s = scenario([op('a', 1)])
        self.assertNotIn('scheduler_agreement', constraint_chain(s, plan(s)))

    def test_cli_schedule_flag(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp, 'p.json')
            with redirect_stdout(io.StringIO()):
                self.assertEqual(0, main(['schedule', str(ROOT / 'examples' / 'workshop.json'), '-o', str(out),
                                          '--record-constraints']))
            text = out.read_text(encoding='utf-8')
            self.assertIn('constraint=', text)
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(0, main(['explain', str(ROOT / 'examples' / 'workshop.json'), str(out)]))
            self.assertEqual([], json.loads(stdout.getvalue())['scheduler_agreement']['mismatches'])


if __name__ == '__main__':
    unittest.main()
