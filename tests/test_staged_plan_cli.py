"""planning-factory staged-plan: materials + setup/run (+ backward) from a v0.3 spec."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from planning_factory.adapters.json_io import dumps, loads, scenario_digest
from planning_factory.cli import main
from .helpers import dt, op
from .test_setup_stage_scheduler import base, spec


def run(*argv):
    out = StringIO()
    with redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


class StagedPlanCliTests(unittest.TestCase):
    def files(self, root, *, due=True, supplies=None, extra=None):
        due_at = dt(day=23, hour=17) if due else None
        scenario = base([op('a', due=due_at), op('b', due=due_at)])
        raw = {'schema_version': 1, 'source_scenario_digest': scenario_digest(scenario), 'mode': 'normal',
               'setup_contract': spec(), 'machine_sequence': ['job:a', 'job:b'],
               'task_priority': ['job:a', 'job:b'],
               'stock': {'accounting': 'net', 'supplies': supplies if supplies is not None else [
                   {'id': 'sheet', 'item_id': 'SHEET', 'quantity': '2', 'available_at': '2026-10-20T09:00:00Z'}]},
               'needs': [{'task_id': 'job:a', 'item_id': 'SHEET', 'quantity': '2'}], **(extra or {})}
        src, sp = root / 'scenario.json', root / 'spec.json'
        src.write_text(dumps(scenario), encoding='utf-8')
        sp.write_text(json.dumps(raw), encoding='utf-8')
        return src, sp

    def test_forward_then_backward_with_materials_and_html(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, sp = self.files(root)
            out, page = root / 'plan.json', root / 'plan.html'
            code, text = run('staged-plan', str(src), str(sp), '-o', str(out), '--html', str(page))
            self.assertEqual(code, 0)
            forward = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(forward['audit_violations'], [])
            self.assertEqual(forward['materials']['pegs'][0]['quantity'], '2')
            self.assertEqual(forward['materials']['shortages'], [])
            self.assertFalse(forward['erp_publishable'])
            self.assertIn('<!doctype html>', page.read_text(encoding='utf-8'))
            run_a = next(b for b in forward['bookings'] if b['phase_id'] == 'job:a' and b['stage'] == 'run')
            self.assertGreaterEqual(run_a['segments'][0]['start'], '2026-10-20T09:00:00')   # material gate holds
            # same files, backward + sequence search from the spec
            raw = json.loads(sp.read_text(encoding='utf-8'))
            raw.update(backward=True, sequence_objective='setup')
            sp.write_text(json.dumps(raw), encoding='utf-8')
            adj = root / 'adj.json'
            code, _ = run('staged-plan', str(src), str(sp), '-o', str(out), '--scenario-output', str(adj))
            self.assertEqual(code, 0)
            backward = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(backward['backward']['option'], 'A')
            self.assertEqual(backward['audit_violations'], [])
            start = lambda r, p: next(b for b in r['bookings'] if b['phase_id'] == p and b['stage'] == 'run')['segments'][0]['start']
            self.assertGreater(start(backward, 'job:b'), start(forward, 'job:b'))
            self.assertTrue(loads(adj.read_text(encoding='utf-8')).operations)

    def test_fail_closed_cases(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, sp = self.files(root, supplies=[
                {'id': 'sheet', 'item_id': 'SHEET', 'quantity': '1', 'available_at': '2026-10-20T09:00:00Z'}])
            out = root / 'plan.json'
            with redirect_stderr(StringIO()) as err:                       # material shortage blocks
                self.assertNotEqual(run('staged-plan', str(src), str(sp), '-o', str(out))[0], 0)
            self.assertIn('shortages', err.getvalue())
            self.assertFalse(out.exists())
            src, sp = self.files(root)
            raw = json.loads(sp.read_text(encoding='utf-8'))
            raw['source_scenario_digest'] = '0' * 64                        # stale spec
            sp.write_text(json.dumps(raw), encoding='utf-8')
            with redirect_stderr(StringIO()) as err:
                self.assertNotEqual(run('staged-plan', str(src), str(sp), '-o', str(out))[0], 0)
            self.assertIn('does not match', err.getvalue())
            with redirect_stderr(StringIO()) as err:                        # never overwrite an input
                self.assertNotEqual(run('staged-plan', str(src), str(sp), '-o', str(src))[0], 0)
            self.assertIn('must not overwrite', err.getvalue())

    def test_the_cli_reruns_its_own_audit_in_normal_mode_and_reports_violations_with_exit_2(self):
        # Mutation testing: nothing proved the CLI's own audit (after the pipeline's) actually runs.
        from unittest import mock
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, sp = self.files(root)
            out = root / 'plan.json'
            with mock.patch('planning_factory.engine.setup_capacity.audit_stage_capacity',
                            return_value=('injected violation',)):
                code, text = run('staged-plan', str(src), str(sp), '-o', str(out))
            self.assertEqual(code, 2)
            self.assertEqual(json.loads(out.read_text(encoding='utf-8'))['audit_violations'], ['injected violation'])
            self.assertEqual(json.loads(text)['audit_violations'], ['injected violation'])
            self.assertEqual(run('staged-plan', str(src), str(sp), '-o', str(out))[0], 0)    # untouched: clean

    def test_report_and_summary_keys_are_sorted(self):
        def sorted_everywhere(node):
            if isinstance(node, dict):
                return list(node) == sorted(node) and all(sorted_everywhere(v) for v in node.values())
            if isinstance(node, list):
                return all(sorted_everywhere(v) for v in node)
            return True
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, sp = self.files(root)
            out = root / 'plan.json'
            code, text = run('staged-plan', str(src), str(sp), '-o', str(out))
            self.assertEqual(code, 0)
            self.assertTrue(sorted_everywhere(json.loads(out.read_text(encoding='utf-8'))))
            self.assertTrue(sorted_everywhere(json.loads(text)))

    def test_hostile_spec_values_give_a_clean_error_and_no_report(self):
        for changes in ({'quantity': '1e999999999'}, {'available_at': '9999-12-31T23:59:59Z'}):
            with TemporaryDirectory() as tmp, self.subTest(changes=changes):
                root = Path(tmp)
                supply = {'id': 'sheet', 'item_id': 'SHEET', 'quantity': '2', 'available_at': '2026-10-20T09:00:00Z', **changes}
                src, sp = self.files(root, supplies=[supply])
                out = root / 'plan.json'
                with redirect_stderr(StringIO()) as err:
                    code, _ = run('staged-plan', str(src), str(sp), '-o', str(out))
                self.assertEqual(code, 1)
                self.assertIn('"error"', err.getvalue())
                self.assertNotIn('Traceback', err.getvalue())
                self.assertFalse(out.exists())

    def test_arithmetic_errors_are_reported_as_clean_errors_by_the_cli(self):
        from unittest import mock
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, sp = self.files(root)
            with mock.patch('planning_factory.adapters.staged_file.calculate_staged_preview',
                            side_effect=OverflowError('date value out of range')), \
                    redirect_stderr(StringIO()) as err:
                code, _ = run('staged-plan', str(src), str(sp), '-o', str(root / 'plan.json'))
            self.assertEqual(code, 1)
            self.assertIn('date value out of range', err.getvalue())


if __name__ == '__main__':
    unittest.main()
