"""The documented examples must keep working: they are the guide's commands."""
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from planning_factory.cli import main

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples' / 'setup-stages'
GUIDE = Path(__file__).resolve().parents[1] / 'docs' / 'guida-pianificazione-a-stadi.md'
SCENARIO, CONTRACT = str(EXAMPLES / 'scenario.json'), str(EXAMPLES / 'contract.json')


def run(*argv):
    out = StringIO()
    with redirect_stdout(out):
        code = main(list(argv))
    return code, out.getvalue()


class GuideExamplesTests(unittest.TestCase):
    def test_commands_and_numbers_in_the_guide(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, _ = run('setup-stages', SCENARIO, CONTRACT, '-o', str(root / 'a.json'),
                          '--html', str(root / 'a.html'))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads((root / 'a.json').read_text())['kpis']['setup_seconds'], 5400)
            self.assertIn('<!doctype html>', (root / 'a.html').read_text())
            code, _ = run('setup-stages', SCENARIO, CONTRACT, '-o', str(root / 'b.json'),
                          '--objective', 'setup', '--restarts', '2', '--budget', '8')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads((root / 'b.json').read_text())['kpis']['setup_seconds'], 1800)
            code, _ = run('setup-stages', SCENARIO, CONTRACT, '-o', str(root / 'c.json'),
                          '--scenario-output', str(root / 'adj.json'),
                          '--sequence', 'job:taglio-b,job:taglio-a,job:taglio-c', '--backward')
            self.assertEqual(code, 0)
            self.assertEqual(json.loads((root / 'c.json').read_text())['audit_violations'], [])
            code, text = run('setup-stages', str(root / 'adj.json'), CONTRACT,
                             '-o', str(root / 'replay.json'), '--replay', str(root / 'c.json'))
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(text)['replay_matches'])

    def test_guide_mentions_every_documented_flag_that_exists(self):
        text = GUIDE.read_text(encoding='utf-8')
        for flag in ('--sequence', '--start', '--objective', '--restarts', '--backward', '--task-modes',
                     '--replay', '--partial', '--no-candidate', '--html', '--scenario-output'):
            self.assertIn(flag, text)


if __name__ == '__main__':
    unittest.main()
