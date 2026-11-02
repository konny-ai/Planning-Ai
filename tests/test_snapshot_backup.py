from pathlib import Path
import tempfile
import unittest

from planning_factory.adapters.rule_memory import RuleMemory
from planning_factory.adapters.store import SnapshotStore
from planning_factory.engine.scheduler import plan
from tools.backup_snapshots import backup
from .helpers import op, scenario


class SnapshotBackupTests(unittest.TestCase):
    def test_recovery_of_plan_and_confirmed_memory_without_overwriting(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source, copy = folder / 'source.db', folder / 'backup.db'
            store = SnapshotStore(source)
            s = scenario((op('a'),))
            result = plan(s)
            store.save_snapshot(s, result, expected_revision=0)
            memory = RuleMemory(source)
            draft = memory.create_draft({'action': 'set_priority', 'value': 2,
                                         'order_id': s.operations[0].order_id}, 'planner')
            memory.decide(draft['id'], 1, 'confirm', 'planner')
            backup(source, copy)
            recovered = SnapshotStore(copy)
            self.assertEqual((s, result), recovered.load_snapshot(s.id, 1))
            self.assertEqual('confirmed', RuleMemory(copy).list()[0]['status'])
            with self.assertRaises(FileExistsError):
                backup(source, copy)
            self.assertEqual((s, result), store.load_snapshot(s.id, 1))
