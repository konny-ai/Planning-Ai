"""Contract and regression tests for the Decision Center (all offline)."""
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from planning_factory.adapters.decision_center import (
    DEFAULT_RULES, check_rules, get_rules, save_rules, select_profile, risks,
    recommend, review_decision, list_reviews,
)
from planning_factory.adapters.decision_history import risk_estimate, validate_history
from planning_factory.adapters.json_io import loads


def scenario():
    root = Path(__file__).resolve().parents[1]
    return loads((root / "examples" / "workshop.json").read_text(encoding="utf-8"))


class DecisionCenterContracts(unittest.TestCase):
    def test_rules_schema_rejects_untrusted_dispatch_and_expression(self):
        for alteration in (
            {"policies": ["earliest_due", "priority"]},
            {"policies": ["priority", "priority"]},
            {"max_candidates": 10},
            {"local_search_budget": 65},
            {"decision_rules": [{"name": "evil", "field": "__class__", "operator": "gte",
                                 "value": 0, "profile": "delivery"}]},
            {"decision_rules": [{"name": "evil", "field": "late_orders", "operator": "exec",
                                 "value": 0, "profile": "delivery"}]},
        ):
            with self.subTest(alteration=alteration), self.assertRaises(ValueError):
                check_rules({**DEFAULT_RULES, **alteration})

    def test_ordered_rules_first_match_wins(self):
        rules = deepcopy(DEFAULT_RULES)
        result, matched = select_profile({"late_orders": 3,
                                          "max_resource_utilization_pct": 99}, rules)
        self.assertEqual((result, matched), ("delivery", "consegne_a_rischio"))

    def test_unknown_rule_field_never_passes(self):
        rules = deepcopy(DEFAULT_RULES)
        rules["decision_rules"] = [{"name": "unknown", "field": "max_resource_utilization_pct",
                                    "operator": "lte", "value": 100, "profile": "stability"}]
        self.assertEqual(select_profile({"max_resource_utilization_pct": None}, rules),
                         ("balanced", None))

    def test_config_is_persisted_outside_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "rules.json"
            with patch.dict("os.environ", {"PLANNING_DECISION_RULES": str(target)}):
                self.assertEqual(save_rules(DEFAULT_RULES), get_rules())
                self.assertTrue(target.is_file())

    def test_verified_candidates_and_no_erp_write(self):
        s = scenario()
        rules = deepcopy(DEFAULT_RULES)
        rules.update(max_candidates=2, policies=["priority", "earliest_due"],
                     decision_rules=[], max_seconds=30)
        report = recommend(s, rules)
        self.assertTrue(report["verified"])
        self.assertEqual(report["candidate_count"], 2)
        self.assertEqual(report["candidates"][0]["policy"], "priority")
        self.assertFalse(report["authorizes_execution"])
        self.assertFalse(report["can_commit_to_erp"])
        self.assertFalse(report["published_plan_changed"])
        self.assertTrue(report["requires_review"])
        self.assertIn(report["winner_policy"], ("priority", "earliest_due"))

    def test_recommendation_is_reproducible(self):
        s = scenario()
        rules = {**DEFAULT_RULES, "policies": ["priority"], "max_candidates": 1}
        first = recommend(s, rules)
        second = recommend(s, rules)
        self.assertEqual(first["decision_id"], second["decision_id"])
        self.assertEqual(first["candidates"], second["candidates"])

    def test_ai_advice_cannot_inject_policy_or_execute(self):
        rules = {**DEFAULT_RULES, "policies": ["priority"], "max_candidates": 1,
                 "decision_rules": [], "use_ai_advice": True}
        r = recommend(scenario(), rules, ai_advice={
            "status": "shadow", "label": "DELIVERY_RISK",
            "command": "DROP TABLE orders", "authorizes_execution": True})
        self.assertEqual(r["profile"], "delivery")
        self.assertTrue(r["ai_influenced_profile"])
        self.assertFalse(r["authorizes_execution"])
        self.assertEqual(r["winner_policy"], "priority")

    def test_review_requires_fresh_digest_and_is_one_time(self):
        rules = {**DEFAULT_RULES, "policies": ["priority"], "max_candidates": 1}
        r = recommend(scenario(), rules)
        with tempfile.TemporaryDirectory() as tmp:
            store = Path(tmp) / "review.json"
            with self.assertRaises(ValueError):
                review_decision(r, "approve", "planner", expected_digest="stale", path=store)
            result = review_decision(r, "approve", "planner",
                                     expected_digest=r["scenario_digest"], path=store)
            self.assertEqual(result["status"], "approved")
            self.assertFalse(result["authorizes_execution"])
            self.assertEqual(len(list_reviews(store)), 1)
            with self.assertRaises(ValueError):
                review_decision(r, "reject", "planner",
                                expected_digest=r["scenario_digest"], path=store)

    def test_history_rejects_identifiers_and_leakage(self):
        with self.assertRaises(ValueError):
            validate_history([{"late_orders": 1, "unscheduled_units": 0,
                               "max_resource_utilization_pct": 85, "late_actual": True,
                               "customer_name": "sensitive"}])

    def test_history_abstains_below_support(self):
        self.assertEqual(risk_estimate({}, [], min_samples=30)["status"], "insufficient_data")

    def test_history_only_provisional_with_validation(self):
        rows = [{"late_orders": 0, "unscheduled_units": 0,
                 "max_resource_utilization_pct": 70, "late_actual": i % 5 == 0}
                for i in range(100)]
        result = risk_estimate({"late_orders": 0, "unscheduled_units": 0,
                                "max_resource_utilization_pct": 70},
                               rows, min_samples=30)
        self.assertEqual(result["status"], "provisional")
        self.assertGreater(result["probability"], 0)
        self.assertLess(result["probability"], 1)
        self.assertGreaterEqual(result["validation_count"], 10)


if __name__ == "__main__":
    unittest.main()
