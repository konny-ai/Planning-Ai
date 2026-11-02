import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from planning_factory.adapters.json_io import dumps
from tools.generate_v03_stage_demo import make_example
from tools.init_local_instance import initialize


class LocalInstallationTests(unittest.TestCase):
    def test_new_instance_validates_paired_data_and_does_not_store_token(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, spec = make_example()
            (root / 'source.json').write_text(dumps(source))
            (root / 'spec.json').write_text(json.dumps(spec))
            folder, token = initialize(root / 'instance', root / 'source.json', root / 'spec.json')
            self.assertEqual('planner', json.loads((folder / 'users.json').read_text())['users'][0]['role'])
            self.assertTrue(all(token.encode() not in p.read_bytes() for p in folder.iterdir()))
            if os.name != 'nt':
                self.assertEqual(0o700, folder.stat().st_mode & 0o777)
                self.assertEqual(0o600, (folder / 'users.json').stat().st_mode & 0o777)
            with self.assertRaises(FileExistsError):
                initialize(folder, root / 'source.json')
            self.assertTrue((folder / 'plans.sqlite').exists())

    def test_failed_initialization_removes_only_its_new_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, _ = make_example()
            (root / 'source.json').write_text(dumps(source))
            with patch('tools.init_local_instance.SnapshotStore', side_effect=OSError('simulated full disk')):
                with self.assertRaises(OSError):
                    initialize(root / 'instance', root / 'source.json')
            self.assertFalse((root / 'instance').exists())
            self.assertTrue((root / 'source.json').exists())
