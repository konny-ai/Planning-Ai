"""Authenticated Decision Center roles, CSRF and fail-closed review tests."""
import http.client
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from planning_factory.adapters.dashboard_api import DEFAULT_FIXTURE
from planning_factory.adapters.decision_center import DEFAULT_RULES
from planning_factory.adapters.secure_dashboard import make_secure_dashboard_server
from planning_factory.adapters.service import new_token


class DecisionCenterSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.tokens = {}
        users = []
        for role in ("admin", "planner", "reparto"):
            token, entry = new_token("qa-" + role, role)
            self.tokens[role] = token
            users.append(entry)
        credentials = root / "users.json"
        credentials.write_text(json.dumps({"users": users}), encoding="utf-8")
        self.env = patch.dict("os.environ", {
            "PLANNING_DECISION_RULES": str(root / "rules.json"),
            "PLANNING_DECISION_CONFIG": str(root / "model.json"),
            "PLANNING_DECISION_HISTORY": str(root / "history.json")})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.server = make_secure_dashboard_server(
            DEFAULT_FIXTURE, credentials, root / "snapshot.db", root / "audit.jsonl", port=0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)

    def request(self, method, route, data=None, cookie=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=20)
        h = dict(headers or {})
        if cookie:
            h["Cookie"] = cookie
        raw = json.dumps(data).encode() if data is not None else None
        if raw is not None:
            h["Content-Type"] = "application/json"
        conn.request(method, route, body=raw, headers=h)
        response = conn.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return result

    def login(self, role):
        status, headers, _ = self.request("POST", "/api/login", {"token": self.tokens[role]})
        self.assertEqual(status, 200)
        return headers["Set-Cookie"].split(";", 1)[0]

    def test_authenticated_role_matrix_and_one_time_review(self):
        self.assertEqual(self.request("GET", "/api/decision-center/reviews")[0], 401)
        admin, planner, reparto = (self.login(role) for role in ("admin", "planner", "reparto"))
        self.assertEqual(self.request("GET", "/api/decision-center/rules", cookie=reparto)[0], 403)
        self.assertEqual(self.request("GET", "/api/decision-center/rules", cookie=planner)[0], 403)
        self.assertEqual(self.request("GET", "/api/decision-center/rules", cookie=admin)[0], 200)
        self.assertEqual(self.request("POST", "/api/decision-center/rules", DEFAULT_RULES, planner)[0], 403)
        self.assertEqual(self.request("POST", "/api/decision-center/rules", DEFAULT_RULES, admin)[0], 200)
        self.assertEqual(self.request("GET", "/api/decision-model/config", cookie=planner)[0], 403)
        self.assertEqual(self.request("GET", "/api/decision-center/reviews", cookie=reparto)[0], 403)
        self.assertEqual(self.request("POST", "/api/decision-center/recommend",
                                      {"ai_approved": False}, reparto)[0], 403)
        status, _, data = self.request("POST", "/api/decision-center/recommend",
                                      {"ai_approved": False}, planner)
        self.assertEqual(status, 200)
        report = json.loads(data)
        self.assertFalse(report["authorizes_execution"])
        review = {"decision_id": report["decision_id"],
                  "expected_digest": report["scenario_digest"],
                  "action": "approve", "note": "qa test"}
        self.assertEqual(self.request("POST", "/api/decision-center/review", review, reparto)[0], 403)
        self.assertEqual(self.request("POST", "/api/decision-center/review", review, planner,
                    headers={"Origin": "https://evil.invalid"})[0], 403)
        self.assertEqual(self.request("POST", "/api/decision-center/review", review, planner)[0], 200)
        self.assertEqual(self.request("POST", "/api/decision-center/review", review, planner)[0], 422)
        self.assertEqual(self.request("GET", "/api/decision-center/reviews", cookie=planner)[0], 200)
        self.assertEqual(self.server.application.store.list_revisions(), [])


if __name__ == "__main__":
    unittest.main()
