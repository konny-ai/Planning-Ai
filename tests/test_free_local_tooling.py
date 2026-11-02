"""Free local validation/cleanup tooling: no SQL leaks and deterministic artifact selection."""
from datetime import datetime, timezone
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from tools import run_free_ci
from tools.cleanup_actions_artifacts import parse_pages, plan_cleanup


class LocalToolingTests(unittest.TestCase):
    def test_ci_runner_drops_inherited_sql_credentials(self):
        seen = []

        def fake_run(args, **kwargs):
            seen.append(kwargs['env'])
            return SimpleNamespace(returncode=0)

        with TemporaryDirectory() as root, \
                patch.dict(os.environ, {'FACTORY_PUBLISH_SQL': 'UNSAFE-CONNECTION'}), \
                patch.object(run_free_ci.subprocess, 'run', side_effect=fake_run), \
                patch('builtins.print'):
            self.assertEqual(run_free_ci.main(['--out', str(Path(root) / 'ci')]), 0)
        self.assertTrue(seen)
        self.assertTrue(all('FACTORY_PUBLISH_SQL' not in env for env in seen))

    def test_actions_artifact_inventory_parses_paginated_json(self):
        pages = ('{"artifacts":[{"id":1,"name":"unit-test","created_at":"2026-09-01T00:00:00Z"}]}'
                 '\n{"artifacts":[{"id":2,"name":"unit-test","created_at":"2026-09-02T00:00:00Z"}]}')
        self.assertEqual(len(parse_pages(pages)), 2)
        with self.assertRaises(ValueError):
            parse_pages('{"error":"unexpected"}')

    def test_cleanup_protects_latest_and_named_backups(self):
        items = [
            {"id": i, "name": "ci-results", "created_at": f"2026-08-{i:02d}T00:00:00Z",
             "size_in_bytes": 100}
            for i in range(1, 6)
        ]
        items.append({"id": 6, "name": "bancoa-backup",
                      "created_at": "2026-07-01T00:00:00Z",
                      "size_in_bytes": 1_000_000})
        remove, preserve = plan_cleanup(
            items, today=datetime(2026, 10, 21, tzinfo=timezone.utc),
            older_than_days=14)
        self.assertEqual([x["id"] for x in remove], [3, 2, 1])
        self.assertEqual({x["id"] for x in preserve}, {4, 5, 6})
        with self.assertRaises(ValueError):
            plan_cleanup(items, today=datetime.now(timezone.utc),
                         older_than_days=0)
