"""Loopback HTTP tests for Decision Center and authenticated review gates."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from planning_factory.adapters.dashboard_api import DEFAULT_FIXTURE, DashboardState, make_dashboard_server
from planning_factory.adapters.secure_dashboard import make_secure_dashboard_server
from planning_factory.adapters.decision_center import DEFAULT_RULES


class DecisionCenterHttpTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.env = patch.dict("os.environ", {
            "PLANNING_DECISION_RULES": str(Path(self.folder.name) / "rules.json"),
            "PLANNING_DECISION_CONFIG": str(Path(self.folder.name) / "model.json"),
            "PLANNING_DECISION_HISTORY": str(Path(self.folder.name) / "missing_history.json"),
        })
        self.env.start()
        self.server = None
        self.thread = None
        self.addCleanup(self.cleanup)

    def cleanup(self):
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=5)
        self.env.stop()
        self.folder.cleanup()

    def start(self, secure=False):
        if secure:
            root = Path(self.folder.name)
            self.server = make_secure_dashboard_server(
                DEFAULT_FIXTURE, None, root / "snapshots.db", root / "audit.jsonl",
                port=0, local_user="decision-test")
        else:
            self.server = make_dashboard_server(DEFAULT_FIXTURE, port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def request(self, method, route, data=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=20)
        raw = None if data is None else json.dumps(data).encode("utf-8")
        options = dict(headers or {})
        if raw is not None:
            options["Content-Type"] = "application/json"
        conn.request(method, route, body=raw, headers=options)
        response = conn.getresponse()
        content = response.read()
        conn.close()
        try:
            body = json.loads(content.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            body = content.decode("utf-8", errors="replace")
        return response.status, body

    def test_demo_rules_and_read_only_comparison(self):
        self.start()
        status, defaults = self.request("GET", "/api/decision-center/rules")
        self.assertEqual(status, 200)
        self.assertEqual(defaults["profile"], "balanced")
        rules = {**DEFAULT_RULES, "policies": ["priority"], "max_candidates": 1}
        self.assertEqual(self.request("POST", "/api/decision-center/rules", rules)[0], 200)
        status, recommendation = self.request("POST", "/api/decision-center/recommend",
                                               {"ai_approved": False})
        self.assertEqual(status, 200)
        self.assertTrue(recommendation["verified"])
        self.assertFalse(recommendation["can_commit_to_erp"])
        self.assertEqual(recommendation["candidate_count"], 1)
        status, new_case = self.request("POST", "/api/decision-center/recommend", {
            "ai_approved": False, "edits": {"fixed_tasks": [], "closures": []}, "settings": {}
        })
        self.assertEqual(status, 200)
        self.assertTrue(new_case["whatif_delta"]["simulation_only"])
        self.assertEqual(self.request("POST", "/api/decision-center/review", {})[0], 404)

    def test_reject_untrusted_browser_scenario_and_unknown_resource(self):
        self.start()
        self.assertEqual(self.request("POST", "/api/decision-center/recommend",
            {"ai_approved": False, "scenario": {"operations": []}})[0], 422)
        status, value = self.request("POST", "/api/decision-center/recommend",
            {"ai_approved": False, "edits": {"closures": [{
                "resource_id": "made-up", "start": "2026-11-20T08:00:00+01:00",
                "end": "2026-11-20T12:00:00+01:00", "reason": "test"
            }]}, "settings": {}})
        self.assertEqual(status, 422)

    def test_secure_review_never_publishes_and_is_idempotently_denied(self):
        self.start(secure=True)
        rules = {**DEFAULT_RULES, "policies": ["priority"], "max_candidates": 1}
        self.assertEqual(self.request("POST", "/api/decision-center/rules", rules)[0], 200)
        status, recommendation = self.request("POST", "/api/decision-center/recommend",
                                               {"ai_approved": False})
        self.assertEqual(status, 200)
        self.assertEqual(self.request("GET", "/decision-model")[0], 200)
        payload = {"decision_id": recommendation["decision_id"],
                   "expected_digest": recommendation["scenario_digest"],
                   "action": "approve", "note": "Simulazione verificata"}
        status, review = self.request("POST", "/api/decision-center/review", payload)
        self.assertEqual(status, 200)
        self.assertEqual(review["status"], "approved")
        self.assertFalse(review["authorizes_execution"])
        self.assertEqual(self.request("POST", "/api/decision-center/review", payload)[0], 422)
        self.assertEqual(self.request("GET", "/api/decision-center/reviews")[1]["reviews"][0]["decision_id"],
                         recommendation["decision_id"])
        self.assertIn("decision_center_review", self.server.application.audit_path.read_text())


if __name__ == "__main__":
    unittest.main()
