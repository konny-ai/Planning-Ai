"""Real loopback HTTP contract, CSV stage import and independently reloaded exports."""
import json
from pathlib import Path
import tempfile
import unittest

from tools.fullstack_smoke import run


class FullstackSmokeTests(unittest.TestCase):
    def test_synthetic_csv_http_whatif_and_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / 'evidence'
            result = run(folder)
            self.assertEqual('passed', result['status'])
            self.assertNotEqual(result['source_scenario_digest'], result['whatif_scenario_digest'])
            self.assertTrue(result['exports_independently_verified'])
            self.assertFalse(result['production_approved'])
            self.assertEqual(result, json.loads((folder / 'summary.json').read_text()))
            with self.assertRaisesRegex(ValueError, 'evidence is not overwritten'):
                run(folder)
