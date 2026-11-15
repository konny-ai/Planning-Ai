"""Decision Center: bounded, auditable decision support for finite-capacity plans.

The planner and its independent verifier are authoritative. AI may classify
risk but cannot submit dispatch parameters, authorize production, or override
deterministic candidate rankings. No external I/O occurs in this module.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
import json
import math
import os
import threading
import time

from planning_factory.adapters.json_io import scenario_digest
from planning_factory.engine.optimization import ALL_POLICIES, measure
from planning_factory.engine.scheduler import plan
from planning_factory.engine.simulation import compare
from planning_factory.engine.verification import verify_plan

POLICY_PROFILES = {
    "delivery": ("unscheduled_operations", "late_orders",
                 "total_tardiness_seconds", "max_tardiness_seconds", "makespan_seconds"),
    "balanced": ("unscheduled_operations", "late_orders",
                 "total_tardiness_seconds", "makespan_seconds"),
    "stability": ("unscheduled_operations", "late_orders",
                  "total_tardiness_seconds", "makespan_seconds"),
}
DEFAULT_RULES = {
    "schema_version": 1, "profile": "balanced",
    "policies": ["priority", "earliest_due", "min_slack", "critical_ratio"],
    "max_candidates": 4, "max_seconds": 30,
    "local_search_budget": 0, "use_ai_advice": False,
    "risk_threshold_hours": 8, "capacity_threshold_pct": 90,
    "min_historical_samples": 30,
    "model_routes": {
        "strategy": {"provider": "inherit", "model": ""},
        "risk": {"provider": "disabled", "model": ""},
        "explanation": {"provider": "disabled", "model": ""},
    },
    "decision_rules": [
        {"name": "consegne_a_rischio", "field": "late_orders",
         "operator": "gte", "value": 1, "profile": "delivery"},
        {"name": "carichi_elevati", "field": "max_resource_utilization_pct",
         "operator": "gte", "value": 90, "profile": "balanced"},
    ],
}
_LOCK = threading.Lock()
REVIEW_STATES = ("pending", "approved", "rejected")


def rules_path():
    return Path(os.environ.get("PLANNING_DECISION_RULES",
                str(Path.home() / ".planning-factory" / "decision-rules.json"))).expanduser()


def check_rules(raw):
    if not isinstance(raw, dict) or set(raw) != set(DEFAULT_RULES):
        raise ValueError("Decision Center policy schema mismatch")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise ValueError("Unsupported decision policy schema")
    if raw["profile"] not in POLICY_PROFILES:
        raise ValueError("Unknown decision profile")
    policies = raw["policies"]
    if (not isinstance(policies, list) or not policies or len(policies) > 7
            or any(not isinstance(p, str) or p not in ALL_POLICIES for p in policies)
            or len(policies) != len(set(policies)) or policies[0] != "priority"):
        raise ValueError("Dispatch policies must be unique, valid, start with priority")
    for key, bottom, top in (("max_candidates", 1, 7), ("max_seconds", 1, 120),
                             ("local_search_budget", 0, 64),
                             ("risk_threshold_hours", 1, 720),
                             ("capacity_threshold_pct", 1, 100),
                             ("min_historical_samples", 20, 10000)):
        if type(raw[key]) is not int or not bottom <= raw[key] <= top:
            raise ValueError("Invalid bounded policy setting: " + key)
    if type(raw["use_ai_advice"]) is not bool:
        raise ValueError("use_ai_advice must be a boolean")
    from planning_factory.adapters.decision_model import PROVIDERS
    routes = raw["model_routes"]
    if not isinstance(routes, dict) or set(routes) != {"strategy", "risk", "explanation"}:
        raise ValueError("Decision model routes must specify strategy, risk and explanation")
    for role, route in routes.items():
        if not isinstance(route, dict) or set(route) != {"provider", "model"}:
            raise ValueError("Invalid model route for " + role)
        if route["provider"] not in (*PROVIDERS, "inherit"):
            raise ValueError("Unknown provider for " + role)
        if not isinstance(route["model"], str) or len(route["model"]) > 120:
            raise ValueError("Invalid model route identifier")
    decision_rules = raw["decision_rules"]
    if not isinstance(decision_rules, list) or len(decision_rules) > 16:
        raise ValueError("Maximum 16 decision rules")
    seen = set()
    for rule in decision_rules:
        if not isinstance(rule, dict) or set(rule) != {"name", "field", "operator", "value", "profile"}:
            raise ValueError("Invalid typed decision rule")
        if not isinstance(rule["name"], str) or not 1 <= len(rule["name"]) <= 60 or rule["name"] in seen:
            raise ValueError("Duplicate or invalid decision rule")
        seen.add(rule["name"])
        if rule["field"] not in ("late_orders", "incomplete_orders", "unscheduled_units",
                                 "max_delay_hours", "max_resource_utilization_pct"):
            raise ValueError("Unknown decision rule field")
        if rule["operator"] not in ("gte", "lte", "eq"):
            raise ValueError("Invalid comparison operator")
        if type(rule["value"]) not in (float, int) or not math.isfinite(rule["value"]) or not 0 <= rule["value"] <= 100000:
            raise ValueError("Rule threshold out of range")
        if rule["profile"] not in POLICY_PROFILES:
            raise ValueError("Unknown decision profile in rule")
    return json.loads(json.dumps(raw))


def get_rules():
    path = rules_path()
    return check_rules(json.loads(path.read_text(encoding="utf-8-sig"))) if path.is_file() else check_rules(DEFAULT_RULES)


def save_rules(value):
    value = check_rules(value)
    path = rules_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return value


def _digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def risks(scenario, baseline, rules):
    """Explain measured risk signals, not fabricated probability estimates."""
    late = sum(o.tardiness_seconds is not None and o.tardiness_seconds > 0 for o in baseline.orders)
    incomplete = sum(not o.complete for o in baseline.orders)
    max_delay = max((o.tardiness_seconds or 0 for o in baseline.orders), default=0) / 3600
    utilization = [r.utilization for r in baseline.resources
                   if r.utilization is not None and math.isfinite(r.utilization)]
    max_pct = max(utilization, default=0) * 100
    signals = []
    if baseline.unscheduled:
        signals.append({"code": "UNSCHEDULED", "severity": "critical",
                        "count": len(baseline.unscheduled),
                        "reason": "Lavorazioni prive di assegnazione"})
    if late:
        signals.append({"code": "LATE_ORDERS", "severity": "high",
                        "count": late, "reason": "Consegne oltre la scadenza nel piano"})
    if incomplete:
        signals.append({"code": "INCOMPLETE", "severity": "high",
                        "count": incomplete, "reason": "Commesse non completate nel piano"})
    if max_delay >= rules["risk_threshold_hours"]:
        signals.append({"code": "MAX_DELAY", "severity": "high",
                        "hours": round(max_delay, 2), "reason": "Ritardo massimo oltre soglia"})
    if utilization and max_pct >= rules["capacity_threshold_pct"]:
        signals.append({"code": "CAPACITY", "severity": "medium",
                        "percent": round(max_pct, 2), "reason": "Risorsa con carico oltre soglia"})
    # Specific identifiers remain inside the local authenticated planner UI.
    # They are never passed into provider requests, which receive only metrics().
    late_orders = sorted((
        {"order_id": o.order_id, "tardiness_hours": round((o.tardiness_seconds or 0) / 3600, 2),
         "complete": o.complete}
        for o in baseline.orders if (o.tardiness_seconds or 0) > 0
    ), key=lambda o: (-o["tardiness_hours"], o["order_id"]))[:12]
    bottlenecks = sorted((
        {"resource_id": r.resource_id, "utilization_pct": round(r.utilization * 100, 2)}
        for r in baseline.resources if r.utilization is not None and math.isfinite(r.utilization)
    ), key=lambda item: (-item["utilization_pct"], item["resource_id"]))[:12]
    return {"signals": signals,
            "late_orders": late_orders, "bottlenecks": bottlenecks,
            "summary": {"late_orders": late, "incomplete_orders": incomplete,
                        "unscheduled_units": len(baseline.unscheduled),
                        "max_delay_hours": round(max_delay, 2),
                        "max_resource_utilization_pct": round(max_pct, 2) if utilization else None},
            "source": "verified_plan", "probabilities_available": False}


def select_profile(risk_summary, rules):
    """Ordered, auditable DMN-like decision table; first matching row wins."""
    for rule in rules["decision_rules"]:
        value = risk_summary.get(rule["field"])
        if type(value) not in (int, float) or not math.isfinite(value):
            continue  # unknown is never treated as zero
        operators = {"gte": value >= rule["value"],
                     "lte": value <= rule["value"],
                     "eq": value == rule["value"]}
        if operators[rule["operator"]]:
            return rule["profile"], rule["name"]
    return rules["profile"], None


def _objectives(profile, published):
    if profile == "stability" and published is not None:
        return ("unscheduled_operations", "late_orders", "moved_operations",
                "total_tardiness_seconds", "makespan_seconds")
    return POLICY_PROFILES[profile]


def _metrics_data(item):
    return {"policy": item.policy, "unscheduled_operations": item.unscheduled_operations,
            "late_orders": item.late_orders,
            "total_tardiness_hours": round(item.total_tardiness_seconds / 3600, 2),
            "max_tardiness_hours": round(item.max_tardiness_seconds / 3600, 2),
            "makespan_hours": round(item.makespan_seconds / 3600, 2),
            "moved_operations": item.moved_operations}


def recommend(scenario, rules=None, *, published=None, published_scenario=None, ai_advice=None):
    """Evaluate a strictly bounded set of independently verified alternatives.

    The caller provides only a trusted, server-side Scenario, never planner
    edits from a browser. No plan is applied and no ERP operation is possible.
    """
    rules = check_rules(rules if rules is not None else get_rules())
    if published is not None:
        reference_scenario = published_scenario if published_scenario is not None else scenario
        if verify_plan(reference_scenario, published):
            raise ValueError("Reference plan does not verify against its source Scenario")
    started = time.monotonic()
    base = plan(scenario)
    base_errors = verify_plan(scenario, base)
    if base_errors:
        raise ValueError("Baseline invalid: " + "; ".join(base_errors))
    basis = published if published is not None else base
    baseline_risks = risks(scenario, base, rules)
    selected_profile, matched_rule = select_profile(baseline_risks["summary"], rules)
    ai_influenced_profile = False
    if (matched_rule is None and rules["use_ai_advice"] and isinstance(ai_advice, dict)
            and ai_advice.get("status") == "shadow"):
        choice = {"DELIVERY_RISK": "delivery", "CAPACITY_REVIEW": "balanced",
                  "PLAN_REVIEW": "stability"}.get(ai_advice.get("label"))
        if choice is not None:
            selected_profile = choice
            ai_influenced_profile = True
    objectives = _objectives(selected_profile, published)
    candidates = []
    rejected = []
    winner_plan = base
    winner_score = measure(scenario, base, "priority", published)
    baseline_score = winner_score
    for policy in rules["policies"][:rules["max_candidates"]]:
        if policy != "priority" and time.monotonic() - started >= rules["max_seconds"]:
            rejected.append({"policy": policy, "reason": "time_budget"})
            break
        try:
            candidate = base if policy == "priority" else plan(scenario, dispatch_policy=policy)
            score = baseline_score if policy == "priority" else measure(scenario, candidate, policy, published)
            diff = compare(basis, candidate)
            candidates.append({**_metrics_data(score),
                               "changed_operations": diff["changed_assignment_operations"],
                               "resource_changes": diff["operations_with_resource_change"],
                               "start_shift_hours": round(diff["total_absolute_start_shift_seconds"] / 3600, 2),
                               "verified": True})
            if score.key(objectives) < winner_score.key(objectives):
                winner_score, winner_plan = score, candidate
        except (ValueError, RuntimeError) as exc:
            rejected.append({"policy": policy, "reason": type(exc).__name__})
    if rules["local_search_budget"] and time.monotonic() - started < rules["max_seconds"]:
        try:
            from planning_factory.engine.local_search import local_search
            remaining = max(0.001, rules["max_seconds"] - (time.monotonic() - started))
            local = local_search(scenario, budget=rules["local_search_budget"],
                                 time_limit_seconds=remaining, objectives=objectives,
                                 published=published)
            diff = compare(basis, local.plan)
            candidates.append({**_metrics_data(local.winner),
                               "policy": "local_search",
                               "changed_operations": diff["changed_assignment_operations"],
                               "resource_changes": diff["operations_with_resource_change"],
                               "start_shift_hours": round(diff["total_absolute_start_shift_seconds"] / 3600, 2),
                               "verified": True, "neighbors_evaluated": local.evaluated_neighbors})
            if local.winner.key(objectives) < winner_score.key(objectives):
                winner_score, winner_plan = local.winner, local.plan
        except (ValueError, RuntimeError) as exc:
            rejected.append({"policy": "local_search", "reason": type(exc).__name__})
    safety = verify_plan(scenario, winner_plan)
    if safety:
        raise ValueError("Selected plan did not pass independent verification")
    if ai_advice is not None and not isinstance(ai_advice, dict):
        raise ValueError("Invalid AI advisory result")
    from planning_factory.adapters.decision_history import load_history, risk_estimate
    try:
        history_result = risk_estimate(baseline_risks["summary"], load_history(),
                                       min_samples=rules["min_historical_samples"])
    except (ValueError, OSError):
        history_result = {"status": "invalid_local_dataset", "probability": None}
    report = {
        "schema_version": 1, "scenario_digest": scenario_digest(scenario),
        "policy_digest": _digest(rules), "profile": selected_profile,
        "reference_scenario_digest": scenario_digest(published_scenario if published_scenario is not None else scenario)
            if published is not None else None,
        "reference_type": "current_verified_plan" if published is not None else "generated_baseline",
        "matched_rule": matched_rule,
        "ai_influenced_profile": ai_influenced_profile,
        "objectives": list(objectives), "candidate_count": len(candidates),
        "candidates": candidates, "rejected": rejected,
        "winner_policy": "local_search" if winner_score.policy.startswith("local_swap:") else winner_score.policy,
        "winner": _metrics_data(winner_score),
        "baseline": _metrics_data(baseline_score),
        "risk": baseline_risks,
        "verified": True, "requires_review": True, "authorizes_execution": False,
        "can_commit_to_erp": False, "published_plan_changed": False,
        "ai_advice": ai_advice if rules["use_ai_advice"] else None,
        "ai_is_advisory_only": True,
        "historical_risk": history_result,
    }
    explanations = []
    if winner_score.policy != "priority":
        explanations.append("La politica candidata è preferita ai KPI della baseline verificata.")
    else:
        explanations.append("Nessuna alternativa valutata migliora gli obiettivi dichiarati rispetto alla baseline.")
    if winner_score.unscheduled_operations < baseline_score.unscheduled_operations:
        explanations.append("Diminuiscono le operazioni non programmate.")
    if winner_score.late_orders < baseline_score.late_orders:
        explanations.append("Diminuisce il numero delle commesse in ritardo.")
    if winner_score.total_tardiness_seconds < baseline_score.total_tardiness_seconds:
        explanations.append("Si riduce il ritardo cumulato delle consegne.")
    if matched_rule:
        explanations.append("La tabella decisionale ha selezionato il profilo tramite " + matched_rule + ".")
    if ai_influenced_profile:
        explanations.append("Il profilo è suggerito da una classificazione AI; la valutazione dei piani resta deterministica.")
    if rejected:
        explanations.append("Non tutti i candidati sono stati valutati; controllare i motivi di esclusione.")
    report["explanation"] = explanations
    report["recommendation_type"] = "review_dispatch_or_whatif"
    # The hash is stable across runs for identical inputs and evaluated results.
    report["decision_id"] = _digest({k: v for k, v in report.items() if k != "decision_id"})[:32]
    return report


def record_path(custom_path=None):
    if custom_path is not None:
        return Path(custom_path).expanduser()
    return Path(os.environ.get("PLANNING_DECISION_REVIEWS",
                 str(Path.home() / ".planning-factory" / "decision-reviews.json"))).expanduser()


def _read_reviews(path):
    if not path.exists():
        return {"schema_version": 1, "reviews": {}}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(data, dict) or data.get("schema_version") != 1 or not isinstance(data.get("reviews"), dict):
        raise ValueError("Invalid decision review log")
    return data


def review_decision(report, action, actor, *, expected_digest, note="", path=None):
    """Record review only. Approving a recommendation never applies its plan."""
    if action not in ("approve", "reject") or not isinstance(actor, str) or not actor.strip():
        raise ValueError("Invalid review actor or action")
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError("Review note exceeds 500 characters")
    if expected_digest != report["scenario_digest"]:
        raise ValueError("Scenario changed; recalculate before reviewing")
    path = record_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _LOCK:
        state = _read_reviews(path)
        previous = state["reviews"].get(report["decision_id"])
        if previous is not None:
            raise ValueError("Decision already reviewed")
        result = {"decision_id": report["decision_id"],
                  "scenario_digest": report["scenario_digest"],
                  "policy_digest": report["policy_digest"],
                  "winner_policy": report["winner_policy"],
                  "status": "approved" if action == "approve" else "rejected",
                  "actor": actor[:120], "note": note.strip(),
                  "reviewed_at": datetime.now(UTC).isoformat(),
                  "authorizes_execution": False}
        state["reviews"][report["decision_id"]] = result
        temporary = path.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
        return result


def list_reviews(path=None):
    return list(_read_reviews(record_path(path))["reviews"].values())[-100:]
