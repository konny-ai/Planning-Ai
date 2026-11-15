"""Extra Decision Center edge/negative tests for branch QA.

Only synthetic fixture data; all HTTP servers bind to loopback.
"""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from planning_factory.adapters.dashboard_api import DEFAULT_FIXTURE, PlannerBusyError
from planning_factory.adapters.decision_center import DEFAULT_RULES, rules_digest
from planning_factory.adapters.decision_workflow import build_recommendation
from planning_factory.adapters.secure_dashboard import make_secure_dashboard_server
from tests.test_decision_center_http import DecisionCenterHttpTests


class ReviewFreshnessTests(DecisionCenterHttpTests):
    """Exercise the genuine secure-web authorization path, not an in-memory mock."""

    def test_whatif_review_uses_original_source_digest(self):
        self.start(secure=True)
        baseline = {**DEFAULT_RULES, "policies": ["priority"], "max_candidates": 1}
        self.assertEqual(self.request("POST", "/api/decision-center/rules", baseline)[0], 200)
        case = {
            "ai_approved": False,
            "edits": {
                "fixed_tasks": [],
                "closures": [{
                    "resource_id": "PIEGA-1",
                    "start": "2026-10-20T08:00:00Z",
                    "end": "2026-10-20T10:00:00Z",
                    "reason": "Synthetic test downtime",
                }],
            },
            "settings": {},
        }
        status, recommendation = self.request("POST", "/api/decision-center/recommend", case)
        self.assertEqual(status, 200, recommendation)
        self.assertTrue(recommendation["verified"])
        self.assertIsNotNone(recommendation["whatif_delta"])
        self.assertNotEqual(recommendation["scenario_digest"],
                            recommendation["whatif_delta"]["original_scenario_digest"])
        status, review = self.request("POST", "/api/decision-center/review", {
            "decision_id": recommendation["decision_id"],
            "expected_digest": recommendation["scenario_digest"],
            "action": "approve",
            "note": "Review only, never publish",
        })
        self.assertEqual(status, 200, review)
        self.assertEqual(review["status"], "approved")
        self.assertFalse(review["authorizes_execution"])
        self.assertEqual(self.server.application.store.list_revisions(), [])

    def test_review_rejects_policy_changes_after_proposal(self):
        self.start(secure=True)
        baseline = {**DEFAULT_RULES, "policies": ["priority"], "max_candidates": 1}
        self.assertEqual(self.request("POST", "/api/decision-center/rules", baseline)[0], 200)
        status, report = self.request("POST", "/api/decision-center/recommend",
                                      {"ai_approved": False})
        self.assertEqual(status, 200)
        changed = {**baseline, "profile": "delivery"}
        self.assertNotEqual(rules_digest(baseline), rules_digest(changed))
        self.assertEqual(self.request("POST", "/api/decision-center/rules", changed)[0], 200)
        request = {"decision_id": report["decision_id"],
                   "expected_digest": report["scenario_digest"],
                   "action": "approve", "note": "stale policy"}
        status, result = self.request("POST", "/api/decision-center/review", request)
        self.assertEqual(status, 409, result)
        self.assertEqual(self.request("GET", "/api/decision-center/reviews")[1]["reviews"], [])
        self.assertEqual(self.server.application.store.list_revisions(), [])

    def test_duplicate_review_cannot_change_approval_to_rejection(self):
        self.start(secure=True)
        rules = {**DEFAULT_RULES, "policies": ["priority"], "max_candidates": 1}
        self.assertEqual(self.request("POST", "/api/decision-center/rules", rules)[0], 200)
        status, report = self.request("POST", "/api/decision-center/recommend",
                                      {"ai_approved": False})
        self.assertEqual(status, 200)
        request = {"decision_id": report["decision_id"],
                   "expected_digest": report["scenario_digest"],
                   "action": "approve", "note": "first decision"}
        self.assertEqual(self.request("POST", "/api/decision-center/review", request)[0], 200)
        status, _ = self.request("POST", "/api/decision-center/review",
                                  {**request, "action": "reject", "note": "second decision"})
        self.assertEqual(status, 422)
        result = self.request("GET", "/api/decision-center/reviews")[1]["reviews"]
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "approved")


class DecisionConcurrencyTests(unittest.TestCase):
    def test_recommendation_slot_rejects_parallel_and_releases_after_success(self):
        entered = threading.Event()
        release = threading.Event()
        completed = []

        def slow(_state, _payload):
            entered.set()
            if not release.wait(timeout=5):
                raise RuntimeError("test thread stuck")
            return {"verified": True}

        def worker():
            try:
                completed.append(build_recommendation(None, {"ai_approved": False}))
            except Exception as exc:
                completed.append(exc)

        with patch("planning_factory.adapters.decision_workflow._build_recommendation", side_effect=slow):
            worker_thread = threading.Thread(target=worker, daemon=True)
            worker_thread.start()
            self.assertTrue(entered.wait(timeout=3))
            with self.assertRaises(PlannerBusyError):
                build_recommendation(None, {"ai_approved": False})
            release.set()
            worker_thread.join(timeout=4)
            self.assertFalse(worker_thread.is_alive())
            self.assertEqual(completed, [{"verified": True}])
            self.assertEqual(build_recommendation(None, {"ai_approved": False}),
                             {"verified": True})

    def test_recommendation_slot_releases_on_failure(self):
        with patch("planning_factory.adapters.decision_workflow._build_recommendation",
                   side_effect=[ValueError("invalid case"), {"verified": True}]):
            with self.assertRaises(ValueError):
                build_recommendation(None, {"ai_approved": False})
            self.assertEqual(build_recommendation(None, {"ai_approved": False}),
                             {"verified": True})


if __name__ == "__main__":
    unittest.main()
