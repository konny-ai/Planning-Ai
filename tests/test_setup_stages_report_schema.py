"""Every setup-stages report must satisfy contracts/setup-stages-report-v1.schema.json."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from planning_factory.cli import main
from .schema_check import validate

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = json.loads((ROOT / 'contracts' / 'setup-stages-report-v1.schema.json').read_text(encoding='utf-8'))
SCENARIO = str(ROOT / 'examples' / 'setup-stages' / 'scenario.json')
CONTRACT = str(ROOT / 'examples' / 'setup-stages' / 'contract.json')
SEQ = 'job:taglio-b,job:taglio-a,job:taglio-c'


def run(*argv):
    with redirect_stdout(StringIO()):
        return main(list(argv))


class ReportSchemaTests(unittest.TestCase):
    def reports(self, root):
        out = lambda name: str(root / name)
        runs = {
            'plain': [],
            'search': ['--objective', 'setup', '--budget', '6'],
            'multistart': ['--objective', 'setup', '--restarts', '2', '--budget', '4', '--seed', '2'],
            'jit': ['--sequence', SEQ, '--just-in-time-tail', '--scenario-output', out('jit.json')],
            'backward': ['--sequence', SEQ, '--backward', '--scenario-output', out('b.json')],
            'backward_search': ['--backward', '--objective', 'gap', '--budget', '3',
                                '--scenario-output', out('bs.json')],
            'backward_multi': ['--backward', '--objective', 'setup', '--restarts', '1', '--budget', '3',
                               '--scenario-output', out('bm.json')],
            'task_modes': ['--sequence', SEQ, '--task-modes', '--scenario-output', out('tm.json')],
            'partial': ['--partial', '--no-candidate', 'taglio-c'],
            'partial_backward': ['--partial', '--no-candidate', 'taglio-c', '--backward',
                                 '--scenario-output', out('pb.json')],
        }
        reports = {}
        for name, extra in runs.items():
            target = out(f'{name}.report.json')
            self.assertEqual(run('setup-stages', SCENARIO, CONTRACT, '-o', target, *extra), 0, name)
            reports[name] = json.loads(Path(target).read_text(encoding='utf-8'))
        run('setup-stages', out('b.json'), CONTRACT, '-o', out('replay.report.json'),
            '--replay', out('backward.report.json'))
        reports['replay'] = json.loads(Path(out('replay.report.json')).read_text(encoding='utf-8'))
        return reports

    def test_all_modes_satisfy_the_schema(self):
        with TemporaryDirectory() as tmp:
            for name, report in self.reports(Path(tmp)).items():
                with self.subTest(mode=name):
                    self.assertEqual(validate(report, SCHEMA), [])

    def test_the_schema_actually_rejects_bad_reports(self):
        with TemporaryDirectory() as tmp:
            report = self.reports(Path(tmp))['backward']
        broken = json.loads(json.dumps(report))
        for mutate, expected in (
                (lambda r: r.pop('audit_violations'), 'missing audit_violations'),
                (lambda r: r.update(extra=1), 'unexpected extra'),
                (lambda r: r['bookings'][0].update(stage='both'), 'not in'),
                (lambda r: r['bookings'][0]['allocations'][0].update(occupancy_bps=20000), 'above 10000'),
                (lambda r: r['backward'].update(option='B'), "expected 'A'"),
                (lambda r: r.update(kpis={'late_orders': 0}), 'missing makespan_seconds'),
                (lambda r: r.update(sequence='job:a'), 'expected array')):
            candidate = json.loads(json.dumps(broken))
            mutate(candidate)
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in e for e in validate(candidate, SCHEMA)),
                                validate(candidate, SCHEMA))
        self.assertEqual(validate(dict(broken, kpis=None), SCHEMA), [])   # kpis may be null (partial plans)


if __name__ == '__main__':
    unittest.main()
