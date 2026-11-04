"""CLI contract for the opt-in two-stage setup/run planner."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from planning_factory.adapters.json_io import dumps
from planning_factory.cli import main
from .test_setup_sequence_search import three
from .test_setup_stage_scheduler import base, spec
from .helpers import op


def run(*argv):
    out = StringIO()
    with redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


class SetupStagesCliTests(unittest.TestCase):
    def _files(self, root, ops=None, contract=None):
        src, con = root / 'scenario.json', root / 'contract.json'
        src.write_text(dumps(base(ops or [op('a'), op('b'), op('c')])), encoding='utf-8')
        con.write_text(json.dumps(contract or _three_spec()), encoding='utf-8')
        return src, con

    def test_default_sequence_writes_audited_stage_plan(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            out = root / 'stages.json'
            code, text = run('setup-stages', str(src), str(con), '-o', str(out))
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(report['audit_violations'], [])
            self.assertEqual(len(report['sequence']), 3)
            self.assertTrue(any(b['stage'] == 'setup' for b in report['bookings']))
            self.assertEqual(json.loads(text)['kpis'], report['kpis'])

    def test_objective_search_reduces_setup_and_reports_baseline(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            out = root / 'stages.json'
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out),
                          '--sequence', 'job:a,job:b,job:c', '--objective', 'setup',
                          '--neighbourhood', 'insertion', '--budget', '12')
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(report['search']['baseline']['setup_seconds'], 5400)
            self.assertEqual(report['kpis']['setup_seconds'], 1800)

    def test_state_grouped_start_cuts_setup_without_search(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            out = root / 'stages.json'
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out),
                          '--start', 'state-grouped')
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(report['kpis']['setup_seconds'], 1800)
            self.assertEqual(report['audit_violations'], [])

    def test_best_start_needs_objective_and_picks_lower_setup(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            out = root / 'stages.json'
            with redirect_stderr(StringIO()):
                code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--start', 'best')
            self.assertNotEqual(code, 0)
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out),
                          '--start', 'best', '--objective', 'setup', '--budget', '1')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(out.read_text(encoding='utf-8'))['kpis']['setup_seconds'], 1800)

    def test_just_in_time_tail_is_opt_in_and_writes_derived_scenario(self):
        from planning_factory.adapters.json_io import loads
        from .helpers import dt
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ops = [op('a', due=dt(day=23, hour=17)), op('b', due=dt(day=23, hour=17)),
                   op('c', due=dt(day=23, hour=17))]
            src, con = self._files(root, ops)
            out, adjusted = root / 'stages.json', root / 'adjusted.json'
            with redirect_stderr(StringIO()):
                code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--just-in-time-tail')
            self.assertNotEqual(code, 0)
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out),
                          '--just-in-time-tail', '--scenario-output', str(adjusted))
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertTrue(report['just_in_time_tail']['experimental'])
            self.assertEqual(report['audit_violations'], [])
            self.assertTrue(report['just_in_time_tail']['delayed'])
            self.assertTrue(any(o.release is not None for o in loads(adjusted.read_text(encoding='utf-8')).operations))
            self.assertTrue(all(o.release is None for o in loads(src.read_text(encoding='utf-8')).operations))

    def test_refuses_to_overwrite_inputs(self):
        with TemporaryDirectory() as tmp:
            src, con = self._files(Path(tmp))
            before = src.read_text(encoding='utf-8')
            with redirect_stderr(StringIO()) as err:
                code, _ = run('setup-stages', str(src), str(con), '-o', str(src))
            self.assertNotEqual(code, 0)
            self.assertIn('overwrite', err.getvalue())
            self.assertEqual(src.read_text(encoding='utf-8'), before)


def _three_spec():
    data = spec()
    data['phases'][1]['target_state'] = 'RED'
    data['phases'].append(dict(data['phases'][0], id='job:c'))
    return data


if __name__ == '__main__':
    unittest.main()
