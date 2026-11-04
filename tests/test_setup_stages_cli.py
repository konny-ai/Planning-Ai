"""CLI contract for the opt-in two-stage setup/run planner."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest
from datetime import timedelta

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

    def test_backward_option_a_reports_gaps_and_derived_releases(self):
        from .helpers import dt
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ops = [op(n, due=dt(day=23, hour=17)) for n in 'abc']
            src, con = self._files(root, ops)
            out, adjusted = root / 'stages.json', root / 'adjusted.json'
            with redirect_stderr(StringIO()):
                code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--backward')
            self.assertNotEqual(code, 0)  # needs --scenario-output
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out),
                          '--sequence', 'job:a,job:b,job:c', '--backward', '--scenario-output', str(adjusted))
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(report['audit_violations'], [])
            self.assertEqual(report['backward']['option'], 'A')
            self.assertTrue(report['backward']['delayed_runs'])
            self.assertIn('job:a', report['backward']['setup_gaps_seconds'])
            self.assertEqual(report['kpis']['late_orders'], 0)

    def test_backward_combines_with_sequence_search(self):
        from .helpers import dt
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ops = [op(n, due=dt(day=23, hour=17)) for n in 'abc']
            src, con = self._files(root, ops)
            out, adjusted = root / 'stages.json', root / 'adjusted.json'
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out),
                          '--sequence', 'job:a,job:b,job:c', '--objective', 'setup', '--budget', '12',
                          '--neighbourhood', 'insertion', '--backward', '--scenario-output', str(adjusted))
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(report['audit_violations'], [])
            self.assertEqual(report['search']['baseline']['setup_seconds'], 5400)
            self.assertEqual(report['kpis']['setup_seconds'], 1800)  # searched order, then backward
            self.assertEqual(report['backward']['baseline']['setup_seconds'], 1800)
            self.assertEqual(report['search']['evaluated_on'], 'backward plan')

    def test_backward_and_just_in_time_are_mutually_exclusive(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            with redirect_stderr(StringIO()) as err:
                code, _ = run('setup-stages', str(src), str(con), '-o', str(root / 'o.json'),
                              '--backward', '--just-in-time-tail', '--scenario-output', str(root / 's.json'))
            self.assertNotEqual(code, 0)
            self.assertIn('use one of them', err.getvalue())

    def test_replay_reproduces_a_backward_plan_from_files_only(self):
        from .helpers import dt
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            due = dt(day=23, hour=17)
            src, con = self._files(root, [op(n, due=due) for n in 'abc'])
            out, adjusted, again = root / 'stages.json', root / 'adjusted.json', root / 'again.json'
            self.assertEqual(run('setup-stages', str(src), str(con), '-o', str(out),
                                 '--sequence', 'job:a,job:b,job:c', '--backward',
                                 '--scenario-output', str(adjusted))[0], 0)
            first = json.loads(out.read_text(encoding='utf-8'))
            self.assertTrue(first['backward']['setup_releases'])  # a derived release exists
            # replay on the EXPORTED scenario reproduces the exact bookings
            code, text = run('setup-stages', str(adjusted), str(con), '-o', str(again), '--replay', str(out))
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(text)['replay_matches'])
            self.assertEqual(json.loads(again.read_text(encoding='utf-8'))['bookings'], first['bookings'])
            # without the derived setup releases the plan differs: replay must flag it
            tampered = json.loads(out.read_text(encoding='utf-8'))
            tampered['backward']['setup_releases'] = {}
            bad = root / 'tampered.json'
            bad.write_text(json.dumps(tampered), encoding='utf-8')
            code, text = run('setup-stages', str(adjusted), str(con), '-o', str(again), '--replay', str(bad))
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(text)['replay_matches'])

    def test_replay_rejects_malformed_reports_with_a_clear_message(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            for name, text in (('empty.json', '{}'), ('list.json', '[]'), ('nobookings.json', '{"sequence": []}'),
                               ('badback.json', '{"sequence": [], "bookings": [], "backward": 3}')):
                report = root / name
                report.write_text(text, encoding='utf-8')
                with self.subTest(name=name), redirect_stderr(StringIO()) as err:
                    code, _ = run('setup-stages', str(src), str(con), '-o', str(root / 'o.json'),
                                  '--replay', str(report))
                self.assertEqual(code, 1)
                self.assertIn('written by setup-stages', err.getvalue())

    def test_replay_rejects_hostile_report_contents_with_a_clear_message(self):
        # Found by fuzzing the replay report: wrong types escaped as AttributeError / unhashable type.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            good = {'sequence': ['job:a'], 'bookings': [], 'backward': {'setup_releases': {}}}
            cases = {'releases-none': {'backward': {'setup_releases': None}},
                     'releases-list': {'backward': {'setup_releases': []}},
                     'releases-text': {'backward': {'setup_releases': 'x'}},
                     'release-not-a-date': {'backward': {'setup_releases': {'job:a': 'yesterday'}}},
                     'release-no-offset': {'backward': {'setup_releases': {'job:a': '2026-10-20T08:00:00'}}},
                     'release-number': {'backward': {'setup_releases': {'job:a': 5}}},
                     'sequence-item-list': {'sequence': [[]]},
                     'sequence-item-dict': {'sequence': [{}]},
                     'backward-not-an-object': {'backward': 7}}
            for name, change in cases.items():
                (root / 'rep.json').write_text(json.dumps({**good, **change}), encoding='utf-8')
                with self.subTest(name=name), redirect_stderr(StringIO()) as err:
                    code, _ = run('setup-stages', str(src), str(con), '-o', str(root / 'o.json'),
                                  '--replay', str(root / 'rep.json'))
                self.assertEqual(code, 1)
                self.assertTrue(any(w in err.getvalue() for w in ('--replay needs', 'ISO 8601', 'UTC offset')),
                                err.getvalue())

    def test_replay_rejects_other_options_and_self_overwrite(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            rep = root / 'r.json'
            rep.write_text('{"sequence": [], "bookings": []}', encoding='utf-8')
            with redirect_stderr(StringIO()) as err:
                code, _ = run('setup-stages', str(src), str(con), '-o', str(root / 'o.json'),
                              '--replay', str(rep), '--backward', '--scenario-output', str(root / 's.json'))
            self.assertNotEqual(code, 0)
            self.assertIn('cannot be combined', err.getvalue())
            with redirect_stderr(StringIO()) as err:
                code, _ = run('setup-stages', str(src), str(con), '-o', str(rep), '--replay', str(rep))
            self.assertNotEqual(code, 0)
            self.assertIn('overwrite', err.getvalue())

    def test_task_modes_honours_per_task_backward(self):
        from .helpers import dt
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            due = dt(day=23, hour=17)
            src, con = self._files(root, [op('a', due=due), op('b', due=due),
                                          op('c', due=due, mode='backward')])
            out, adjusted = root / 'stages.json', root / 'adjusted.json'
            with redirect_stderr(StringIO()):  # plain run rejects non-forward modes
                code, _ = run('setup-stages', str(src), str(con), '-o', str(out))
            self.assertNotEqual(code, 0)
            with redirect_stderr(StringIO()):
                code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--task-modes')
            self.assertNotEqual(code, 0)  # needs --scenario-output
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out),
                          '--sequence', 'job:a,job:b,job:c', '--task-modes',
                          '--scenario-output', str(adjusted))
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual(report['task_modes'], {'job:a': 'forward', 'job:b': 'forward', 'job:c': 'backward'})
            self.assertEqual(report['audit_violations'], [])
            self.assertEqual([d['phase_id'] for d in report['backward']['delayed_runs']], ['job:c'])

    def test_task_modes_cannot_be_combined_with_other_policies(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            with redirect_stderr(StringIO()) as err:
                code, _ = run('setup-stages', str(src), str(con), '-o', str(root / 'o.json'),
                              '--task-modes', '--backward', '--scenario-output', str(root / 's.json'))
            self.assertNotEqual(code, 0)
            self.assertIn('cannot be combined', err.getvalue())

    def test_partial_plans_the_plannable_and_reports_the_rest(self):
        from planning_factory.domain.models import Dependency
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            ops = [op('a'), op('x'), op('c', predecessors=(Dependency('x', 'FS'),)), op('b')]
            data = _three_spec()
            data['phases'] = [dict(data['phases'][0], id=f'job:{n}') for n in 'abc']
            src, con = self._files(root, ops, data)
            out = root / 'stages.json'
            with redirect_stderr(StringIO()):                   # strict mode still fails closed
                self.assertNotEqual(run('setup-stages', str(src), str(con), '-o', str(out))[0], 0)
            code, text = run('setup-stages', str(src), str(con), '-o', str(out), '--partial',
                             '--no-candidate', 'x')
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual({u['operation_id']: u['code'] for u in report['unplanned']},
                             {'x': 'NO_CANDIDATE', 'c': 'PREDECESSOR_UNPLANNED'})
            self.assertEqual(sorted({b['phase_id'] for b in report['bookings']}), ['job:a', 'job:b'])
            self.assertEqual(json.loads(text)['planned'], ['job:a', 'job:b'])
            self.assertEqual(report['audit_violations'], [])

    def test_partial_with_no_candidate_option_and_backward(self):
        from .helpers import dt
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            due = dt(day=23, hour=17)
            data = _three_spec()
            src, con = self._files(root, [op(n, due=due) for n in 'abc'], data)
            out, adjusted = root / 'stages.json', root / 'adj.json'
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--partial',
                          '--no-candidate', 'b', '--backward', '--scenario-output', str(adjusted))
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual([u['operation_id'] for u in report['unplanned']], ['b'])
            self.assertEqual(sorted({b['phase_id'] for b in report['bookings']}), ['job:a', 'job:c'])
            self.assertEqual(report['backward']['option'], 'A')
            self.assertTrue(adjusted.exists())
            with redirect_stderr(StringIO()) as err:
                code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--partial', '--objective', 'setup')
            self.assertNotEqual(code, 0)
            self.assertIn('--partial can only be combined', err.getvalue())

    def test_restarts_run_extra_starts_and_report_them(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            out = root / 'stages.json'
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--objective', 'setup',
                          '--restarts', '3', '--seed', '5', '--budget', '6')
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertGreaterEqual(report['multistart']['starts'], 2)
            self.assertEqual(report['multistart']['seed'], 5)
            self.assertEqual(report['audit_violations'], [])
            self.assertEqual(report['kpis']['setup_seconds'], 1800)
            for argv, messages in ((['--restarts', '2'], ('requires --objective',)),
                                   (['--objective', 'setup', '--restarts', '2', '--partial'],
                                    ('only be combined', 'only available')),
                                   (['--objective', 'setup', '--restarts', '2', '--task-modes'],
                                    ('cannot be combined', 'only available'))):
                with self.subTest(argv=argv), redirect_stderr(StringIO()) as err:
                    code, _ = run('setup-stages', str(src), str(con), '-o', str(out), *argv)
                self.assertNotEqual(code, 0)
                self.assertTrue(any(m in err.getvalue() for m in messages), err.getvalue())

    def test_restarts_also_work_with_backward(self):
        from .helpers import dt
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            due = dt(day=23, hour=17)
            src, con = self._files(root, [op(n, due=due) for n in 'abc'])
            out, adj = root / 'stages.json', root / 'adj.json'
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--backward',
                          '--scenario-output', str(adj), '--objective', 'setup', '--restarts', '2',
                          '--seed', '3', '--budget', '4')
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertGreaterEqual(report['multistart']['starts'], 2)
            self.assertEqual(report['search']['evaluated_on'], 'backward plan')
            self.assertEqual(report['audit_violations'], [])
            self.assertEqual(report['kpis']['setup_seconds'], 1800)
            self.assertEqual(report['backward']['option'], 'A')

    def test_partial_backward_writes_the_scenario_even_when_nothing_is_plannable(self):
        from planning_factory.adapters.json_io import loads
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            out, adj = root / 'o.json', root / 'adj.json'
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--partial', '--backward',
                          '--no-candidate', 'a,b,c', '--scenario-output', str(adj))
            self.assertEqual(code, 0)
            report = json.loads(out.read_text(encoding='utf-8'))
            self.assertEqual((report['bookings'], report['kpis'], len(report['unplanned'])), ([], None, 3))
            self.assertTrue(adj.exists())                       # the file the command asked for
            self.assertEqual(loads(adj.read_text(encoding='utf-8')).operations, ())

    def test_reports_have_sorted_keys_so_they_can_be_diffed_and_reproduced(self):
        # Mutation testing: nothing checked the key order of the JSON written for every command mode.
        def sorted_everywhere(node):
            if isinstance(node, dict):
                return list(node) == sorted(node) and all(sorted_everywhere(v) for v in node.values())
            if isinstance(node, list):
                return all(sorted_everywhere(v) for v in node)
            return True
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)
            adj, rep = root / 'adj.json', root / 'rep.json'
            modes = {'plain': [], 'partial': ['--partial', '--no-candidate', 'c'],
                     'backward': ['--sequence', 'job:a,job:b,job:c', '--backward', '--scenario-output', str(adj)]}
            for name, extra in modes.items():
                out = root / f'{name}.json'
                code, summary = run('setup-stages', str(src), str(con), '-o', str(out), *extra)
                self.assertEqual(code, 0)
                with self.subTest(mode=name):
                    self.assertTrue(sorted_everywhere(json.loads(out.read_text(encoding='utf-8'))))
                    self.assertTrue(sorted_everywhere(json.loads(summary)))          # the printed summary too
                    self.assertEqual(out.read_text(encoding='utf-8'),
                                     json.dumps(json.loads(out.read_text(encoding='utf-8')), indent=2, sort_keys=True) + '\n')
            code, summary = run('setup-stages', str(adj), str(con), '-o', str(rep), '--replay', str(root / 'backward.json'))
            self.assertEqual(code, 0)
            self.assertTrue(sorted_everywhere(json.loads(rep.read_text(encoding='utf-8'))))
            self.assertTrue(sorted_everywhere(json.loads(summary)))

    def test_objective_without_restarts_is_a_single_start_search(self):
        # With restarts the search also starts from the state-grouped order; without them it must not.
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src, con = self._files(root)                          # a BLUE, b RED, c BLUE; the machine starts RED
            single, multi = root / 'single.json', root / 'multi.json'
            self.assertEqual(run('setup-stages', str(src), str(con), '-o', str(single),
                                 '--objective', 'setup', '--budget', '1')[0], 0)
            self.assertEqual(run('setup-stages', str(src), str(con), '-o', str(multi),
                                 '--objective', 'setup', '--budget', '1', '--restarts', '1')[0], 0)
            one, many = (json.loads(f.read_text(encoding='utf-8')) for f in (single, multi))
            self.assertNotIn('multistart', one)
            self.assertEqual(one['sequence'], ['job:a', 'job:b', 'job:c'])        # budget 1 keeps the due-date order
            self.assertEqual(one['kpis']['setup_seconds'], 5400)
            self.assertEqual(many['kpis']['setup_seconds'], 1800)                   # the grouped start is only tried with restarts
            self.assertGreaterEqual(many['multistart']['starts'], 2)         # due-date + grouped (duplicates are dropped)

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


class StageHtmlReportTests(unittest.TestCase):
    def test_html_report_for_plain_backward_and_partial_runs(self):
        from planning_factory.domain.models import Dependency
        from .helpers import dt
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            due = dt(day=23, hour=17)
            ops = [op('a', due=due), op('x', due=due), op('c', predecessors=(Dependency('x', 'FS'),)), op('b', due=due)]
            data = _three_spec()
            data['phases'] = [dict(data['phases'][0], id=f'job:{n}') for n in 'abc']
            src, con = root / 's.json', root / 'c.json'
            from .test_setup_stage_scheduler import base
            src.write_text(dumps(base(ops)), encoding='utf-8')
            con.write_text(json.dumps(data), encoding='utf-8')
            page, adj, out = root / 'r.html', root / 'adj.json', root / 'o.json'
            code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--partial',
                          '--no-candidate', 'x', '--backward', '--scenario-output', str(adj),
                          '--html', str(page))
            self.assertEqual(code, 0)
            text = page.read_text(encoding='utf-8')
            self.assertTrue(text.startswith('<!doctype html>'))
            for needle in ('job:a', 'attrezzaggio', 'NO_CANDIDATE', 'PREDECESSOR_UNPLANNED',
                           'Distanza attrezzaggio', 'nessuna violazione'):
                self.assertIn(needle, text)
            self.assertNotIn('<script', text)
            with redirect_stderr(StringIO()) as err:       # never overwrite an input
                code, _ = run('setup-stages', str(src), str(con), '-o', str(out), '--partial',
                               '--no-candidate', 'x', '--html', str(src))
            self.assertNotEqual(code, 0)
            self.assertIn('--html must not overwrite', err.getvalue())

    def test_bar_geometry_is_proportional_to_the_time_span(self):
        # Mutation testing: nothing checked where the bars are drawn, so a wrong span or min/max went unnoticed.
        import re
        from planning_factory.adapters.stage_report import stage_html_report
        from planning_factory.domain.models import Interval
        from planning_factory.engine.setup_capacity import StageAllocation, StageBooking
        from .helpers import dt
        first = StageBooking('job:a', 'setup', (Interval(dt(hour=8), dt(hour=9)),), (StageAllocation('m', 10000),))
        second = StageBooking('job:a', 'run', (Interval(dt(hour=9), dt(hour=11)),), (StageAllocation('m', 10000),))
        tick = StageBooking('job:b', 'run', (Interval(dt(hour=11), dt(hour=11) + timedelta(seconds=1)),),
                            (StageAllocation('n', 10000),))
        page = stage_html_report((first, second, tick))
        bars = re.findall(r'class="bar (\w+)" style="left:([\d.]+)%;width:([\d.]+)%"', page)
        span = 3 * 3600 + 1                                               # 08:00 to 11:00:01
        pct = lambda seconds: f'{seconds / span * 100:.3f}'
        self.assertEqual(bars[0], ('setup', '0.000', pct(3600)))          # 1h at the very start
        self.assertEqual(bars[1], ('run', pct(3600), pct(7200)))          # starts after the first, lasts 2h
        self.assertEqual(bars[2], ('run', pct(3 * 3600), '0.200'))        # a one-second bar keeps a visible width
        self.assertLess(float(bars[0][1]) + float(bars[0][2]), float(bars[1][1]) + 0.001)   # bars do not overlap
        single = stage_html_report((first,))                              # one booking alone spans the whole track
        self.assertIn('left:0.000%;width:100.000%', single)

    def test_a_zero_length_booking_renders_a_minimal_bar_instead_of_dividing_by_zero(self):
        from planning_factory.adapters.stage_report import stage_html_report
        from planning_factory.domain.models import Interval
        from planning_factory.engine.setup_capacity import StageAllocation, StageBooking
        from .helpers import dt
        point = StageBooking('job:a', 'run', (Interval(dt(hour=8), dt(hour=8)),), (StageAllocation('m', 10000),))
        page = stage_html_report((point,))
        self.assertIn('left:0.000%;width:0.200%', page)

    def test_html_escapes_untrusted_ids(self):
        from planning_factory.adapters.stage_report import stage_html_report
        from planning_factory.domain.models import Interval
        from planning_factory.engine.setup_capacity import StageAllocation, StageBooking
        from .helpers import dt
        booking = StageBooking('<img src=x onerror=alert(1)>', 'run',
                               (Interval(dt(hour=8), dt(hour=9)),), (StageAllocation('<b>m</b>', 10000),))
        page = stage_html_report((booking,), unplanned=({'operation_id': '<script>', 'code': 'X', 'detail': '"&'},))
        self.assertNotIn('<img src', page)
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)
        empty = stage_html_report(())
        self.assertIn('<!doctype html>', empty)
