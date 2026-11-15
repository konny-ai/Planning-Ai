"""Regression tests for optional planning decision models; never calls paid providers."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from planning_factory.adapters import decision_model as dm


class DecisionModelTests(unittest.TestCase):
    def test_default_disabled(self):
        self.assertEqual(dm.evaluate(dm.DEFAULT, {"scheduled_count": 1}, approved=True)["status"], "disabled")

    def test_approval_required(self):
        config = {**dm.DEFAULT, "enabled": True, "provider": "baseline"}
        self.assertEqual(dm.evaluate(config, {}, approved=False)["status"], "approval_required")

    def test_baseline_is_advisory(self):
        config = {**dm.DEFAULT, "enabled": True, "provider": "baseline"}
        result = dm.evaluate(config, {"scheduled_count": 2, "unscheduled_count": 1, "delivery_count": 1}, approved=True)
        self.assertEqual(result["label"], "CAPACITY_REVIEW")
        self.assertFalse(result["authorizes_execution"])
        self.assertTrue(result["requires_review"])

    def test_threshold_abstention(self):
        config = {**dm.DEFAULT, "enabled": True, "provider": "local", "model": "demo", "threshold": .9}
        with patch.object(dm, "_predict", return_value={"label": "NO_ALERT", "confidence": .75, "reason": "demo"}):
            self.assertEqual(dm.evaluate(config, {}, approved=True)["label"], "UNCERTAIN")

    def test_network_failure_fails_closed(self):
        config = {**dm.DEFAULT, "enabled": True, "provider": "openai", "model": "demo",
                  "external_approved": True}
        with patch.object(dm, "_predict", side_effect=ConnectionError("secret")):
            result = dm.evaluate(config, {}, approved=True)
        self.assertEqual(result["label"], "UNCERTAIN")
        self.assertNotIn("secret", result["reason"])

    def test_block_ssrf_and_secrets(self):
        for endpoint in ("http://192.168.1.1/v1", "https://localhost/v1",
                         "http://localhost:11434/private", "http://user:pass@localhost/v1",
                         "http://localhost:11434/v1?token=x"):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    dm.validate_config({**dm.DEFAULT, "endpoint": endpoint})

    def test_config_roundtrip(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "decision.json"
            with patch.dict("os.environ", {"PLANNING_DECISION_CONFIG": str(path)}):
                config = {**dm.DEFAULT, "enabled": True, "provider": "baseline"}
                self.assertEqual(dm.save_config(config), dm.get_config())
                self.assertFalse(path.with_suffix(".tmp").exists())

    def test_benchmark_never_calls_provider(self):
        config = {**dm.DEFAULT, "enabled": True, "provider": "baseline"}
        with patch.object(dm, "_http_json", side_effect=AssertionError("network forbidden")):
            result = dm.synthetic_benchmark(config, approved=True)
        self.assertEqual(result["correct"], 4)
        self.assertEqual(result["external_calls"], 0)


if __name__ == "__main__":
    unittest.main()
