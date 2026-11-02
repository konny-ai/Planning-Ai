"""Single-command Factory pipeline on a synthetic extraction (no database)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from .test_factory_export_safety import ROOT, TOOLS
from .test_factory_publish import factory_scenario


class FactoryRunnerTests(unittest.TestCase):
    def run_pipeline(self, root, payload, *extra):
        (root / 'scenario.json').write_text(json.dumps(payload), encoding='utf-8')
        env = dict(os.environ, PYTHONPATH=str(ROOT / 'src') + os.pathsep + os.environ.get('PYTHONPATH', ''))
        return subprocess.run([sys.executable, str(TOOLS / 'run_factory_planning.py'), '--database', 'SYNTHETIC',
                               '--start', '2026-10-19', '--scenario', str(root / 'scenario.json'), '--out', str(root / 'run'),
                               *extra], capture_output=True, text=True, timeout=120, env=env)

    def test_pipeline_plans_verifies_and_prepares_the_publication(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self.run_pipeline(root, factory_scenario())
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            summary = (root / 'run' / 'riepilogo.md').read_text(encoding='utf-8')
            for line in ('| conversione | OK |', '1 pianificate, 0 non pianificate', 'piano valido', '| pubblicazione | OK |',
                         'solo anteprima'):
                self.assertIn(line, summary)
            for path in ('motore/report.html', 'motore/plan.csv', 'motore/conversion.json', 'pubblicazione/publish.sql'):
                self.assertTrue((root / 'run' / path).is_file(), path)

    def test_pipeline_stops_at_the_failing_step_with_its_log(self):
        payload = factory_scenario()
        payload['operationResources'] = payload['operationResources'][:1]   # operator required but missing
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self.run_pipeline(root, payload)
            self.assertEqual(result.returncode, 1)
            self.assertIn('Interrotto al passo **conversione**', (root / 'run' / 'riepilogo.md').read_text(encoding='utf-8'))
            self.assertIn('risorse obbligatorie mancanti', (root / 'run' / 'conversione.log').read_text(encoding='utf-8'))
            self.assertFalse((root / 'run' / 'pubblicazione').exists())
            result = self.run_pipeline(root, payload, '--missing-resource-policy', 'available')
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_execute_requires_the_confirmation_before_touching_a_database(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            result = self.run_pipeline(root, factory_scenario(), '--execute')
            self.assertEqual(result.returncode, 1)
            self.assertIn('confirm-database', (root / 'run' / 'pubblicazione.log').read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
