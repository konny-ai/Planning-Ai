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

    def test_windows_utf8_bom_config(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "decision-model.json"
            config = {**dm.DEFAULT, "enabled": True, "provider": "baseline"}
            import json
            path.write_text(json.dumps(config), encoding="utf-8-sig")
            with patch.dict("os.environ", {"PLANNING_DECISION_CONFIG": str(path)}):
                self.assertEqual(dm.get_config(), config)

    def test_native_openai_choice(self):
        response = {"answers": [{"type": "choice", "choice": "CAPACITY_REVIEW",
                                 "confidence": .93}]}
        with patch.object(dm, "_http_json", return_value=response) as call:
            result = dm._native_decision("openai_decisions", "gpt-6-luna", "dummy",
                                         {"unscheduled_count": 2}, "")
        self.assertEqual(result["label"], "CAPACITY_REVIEW")
        self.assertTrue(call.call_args.args[0].endswith("/v1/decisions"))

    def test_native_openrouter_choice(self):
        response = {"answers": {"plan_review": {"type": "choice",
                    "choice": "PLAN_REVIEW", "confidence": .88}}}
        with patch.object(dm, "_http_json", return_value=response) as call:
            result = dm._native_decision("openrouter_decisions", "scaledown/classify-1",
                                         "dummy", {"scheduled_count": 0}, "")
        self.assertEqual(result["label"], "PLAN_REVIEW")
        self.assertIn("/api/alpha/decisions", call.call_args.args[0])
        payload = call.call_args.args[2]
        self.assertEqual(payload["questions"]["plan_review"]["type"], "choice")
        self.assertIn("CAPACITY_REVIEW", payload["questions"]["plan_review"]["criteria"])

    def test_local_system_one_response(self):
        response = {"data": {"result": {"answers": {
            "plan_review": {"type": "choice", "choice": "NO_ALERT", "confidence": .95}}}}}
        with patch.object(dm, "_http_json", return_value=response) as call:
            result = dm._native_decision("local_decisions", "local-model", "",
                                        {"scheduled_count": 5}, "",
                                        "http://127.0.0.1:11434/v1")
        self.assertEqual(result["label"], "NO_ALERT")
        self.assertEqual(call.call_args.args[0],
                         "http://127.0.0.1:11434/v1/systemone")

    def test_native_refusal_is_failure(self):
        with patch.object(dm, "_http_json", return_value={"answers": [{"type": "refusal"}]}):
            with self.assertRaises(ValueError):
                dm._native_decision("openai_decisions", "gpt-6-luna", "dummy", {}, "")

    def test_plan_metrics_use_actual_plan_contract(self):
        from types import SimpleNamespace
        plan = SimpleNamespace(assignments=(object(), object()), unscheduled=(),
              orders=(SimpleNamespace(tardiness_seconds=7200, complete=True),
                      SimpleNamespace(tardiness_seconds=None, complete=False)),
              resources=(SimpleNamespace(utilization=.8),))
        summary = dm.metrics(plan)
        self.assertEqual(summary["delivery_count"], 2)
        self.assertEqual(summary["late_delivery_count"], 1)
        self.assertEqual(summary["unknown_delivery_count"], 1)
        self.assertEqual(summary["max_delay_hours"], 2.0)
        self.assertEqual(summary["max_utilization_pct"], 80.0)

    def test_external_hourly_limit_fails_closed(self):
        from collections import deque
        with patch.object(dm, "_EXTERNAL_CALLS", deque([2000.0] * 24)):
            with patch.object(dm.time, "monotonic", return_value=2001.0):
                with self.assertRaisesRegex(PermissionError, "hourly_limit"):
                    dm._reserve_external_call()

    def test_benchmark_never_calls_provider(self):
        config = {**dm.DEFAULT, "enabled": True, "provider": "baseline"}
        with patch.object(dm, "_http_json", side_effect=AssertionError("network forbidden")):
            result = dm.synthetic_benchmark(config, approved=True)
        self.assertEqual(result["correct"], 10)
        self.assertEqual(result["count"], 10)
        self.assertEqual(result["abstentions"], 0)
        self.assertEqual(result["confusion_matrix"]["DELIVERY_RISK"]["DELIVERY_RISK"], 2)
        self.assertEqual(result["external_calls"], 0)


if __name__ == "__main__":
    unittest.main()
