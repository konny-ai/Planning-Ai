"""Optional historical delivery-risk calibration from local aggregated outcomes.

No probabilistic claim without an explicitly supplied, bounded historical
dataset and out-of-sample evidence. Historical input is never sent to LLMs.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json
import math
import os

MAX_ROWS = 5000
MAX_BYTES = 1024 * 1024
FEATURES = ("late_orders", "unscheduled_units", "max_resource_utilization_pct")
FIELDS = set(FEATURES) | {"late_actual"}


def history_path():
    raw = os.environ.get("PLANNING_DECISION_HISTORY")
    return Path(raw).expanduser() if raw else None


def _feature_key(row):
    """Three coarse, non-identifying features, never customer/operation ids."""
    return (int(row["late_orders"] > 0), int(row["unscheduled_units"] > 0),
            int(row["max_resource_utilization_pct"] >= 85))


def validate_history(rows):
    if not isinstance(rows, list) or not 0 <= len(rows) <= MAX_ROWS:
        raise ValueError("Historical dataset must be a bounded list")
    result = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != FIELDS or type(row["late_actual"]) is not bool:
            raise ValueError("Historical data must contain only aggregate features and boolean actual outcome")
        if any(type(row[k]) not in (int, float) or not math.isfinite(row[k]) or row[k] < 0
               for k in FEATURES):
            raise ValueError("Invalid historical risk feature")
        if row["max_resource_utilization_pct"] > 100000:
            raise ValueError("Invalid historical capacity percentage")
        result.append({k: row[k] for k in FEATURES} | {"late_actual": row["late_actual"]})
    return result


def load_history(path=None):
    path = Path(path) if path is not None else history_path()
    if path is None or not path.is_file():
        return []
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("Historical dataset exceeds 1 MiB")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1 or set(payload) != {"schema_version", "rows"}:
        raise ValueError("Historical dataset schema version mismatch")
    return validate_history(payload["rows"])


def risk_estimate(summary, rows, *, min_samples=30):
    """Out-of-sample Brier gate, plus bucket support, for a *tentative* probability.

    A stratified production evaluation is still required; a status of
    'provisional' must never be treated as guaranteed calibration.
    """
    rows = validate_history(rows)
    if type(min_samples) is not int or not 20 <= min_samples <= 10000:
        raise ValueError("Invalid minimum support")
    if len(rows) < max(min_samples, 50):
        return {"status": "insufficient_data", "sample_count": len(rows),
                "probability": None}
    if (not isinstance(summary, dict) or any(
            type(summary.get(k)) not in (int, float) or
            not math.isfinite(summary.get(k)) or summary.get(k) < 0 for k in FEATURES)):
        return {"status": "missing_current_features", "sample_count": len(rows),
                "probability": None}
    # Stable split by source order; the dataset must be chronological.
    split = int(len(rows) * .8)
    train, test = rows[:split], rows[split:]
    if len(test) < 10:
        return {"status": "insufficient_validation", "sample_count": len(rows), "probability": None}
    def estimate(input_row):
        key = _feature_key(input_row)
        same = [r for r in train if _feature_key(r) == key]
        if len(same) < max(10, min_samples // 3):
            return None, len(same)
        return (sum(r["late_actual"] for r in same) + 1) / (len(same) + 2), len(same)
    validated = []
    for row in test:
        p, support = estimate(row)
        if p is not None:
            validated.append((p, row["late_actual"]))
    if len(validated) < 10:
        return {"status": "insufficient_validation", "sample_count": len(rows), "probability": None}
    brier = sum((p - float(y)) ** 2 for p, y in validated) / len(validated)
    probability, support = estimate(summary)
    result = {"sample_count": len(rows), "validation_count": len(validated),
              "brier_score": round(brier, 4), "bucket_support": support,
              "probability": None}
    if probability is None:
        return {"status": "insufficient_bucket_support", **result}
    # No inference of universal calibration; only validation on the held-out set.
    if brier > .25:
        return {"status": "failed_validation", **result}
    return {"status": "provisional", **result, "probability": round(probability, 4),
            "warning": "Exploratory historical estimate, not a guaranteed on-time probability"}
