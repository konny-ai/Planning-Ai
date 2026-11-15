"""Controller for server-side, read-only decision simulations.

User input is an allowlisted what-if payload. The server constructs the
Scenario from trusted files; neither the model nor browser may provide plans.
"""
from __future__ import annotations

from planning_factory.adapters.dashboard_api import apply_settings
from planning_factory.adapters.dashboard_simulation import apply_whatif_edits
from planning_factory.adapters.decision_center import get_rules, recommend
from planning_factory.adapters.decision_model import evaluate, get_config, metrics
from planning_factory.adapters.json_io import scenario_digest
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.simulation import compare
from planning_factory.engine.verification import verify_plan


def build_recommendation(state, payload):
    """Produce recommendations under current server data without storing changes."""
    if not isinstance(payload, dict) or set(payload) not in (
        {"ai_approved"}, {"ai_approved", "edits", "settings"}
    ):
        raise ValidationError(["expected ai_approved or ai_approved+edits+settings"])
    if type(payload["ai_approved"]) is not bool:
        raise ValidationError(["ai_approved must be boolean"])
    rules = get_rules()
    source, current = state.calculate()
    scenario = source
    whatif = None
    if "edits" in payload:
        if state.workbook_input is not None:
            raise ValidationError(["Workbook planning changes must be made in the source workbook"])
        scenario = apply_whatif_edits(source, payload["edits"])
        scenario = apply_settings(scenario, payload["settings"], state.manifest)
        tentative = plan(scenario)
        failures = verify_plan(scenario, tentative)
        if failures:
            raise ValidationError(list(failures))
        difference = compare(current, tentative)
        whatif = {
            "original_scenario_digest": scenario_digest(source),
            "derived_scenario_digest": scenario_digest(scenario),
            "changed_operations": difference["changed_assignment_operations"],
            "resource_changes": difference["operations_with_resource_change"],
            "start_shift_hours": round(difference["total_absolute_start_shift_seconds"] / 3600, 2),
            "previous_unscheduled_units": difference["before_unscheduled"],
            "new_unscheduled_units": difference["after_unscheduled"],
            "previous_tardiness_hours": round(difference["before_tardiness_seconds"] / 3600, 2),
            "new_tardiness_hours": round(difference["after_tardiness_seconds"] / 3600, 2),
            "simulation_only": True, "source_modified": False,
        }
    advice = None
    if rules["use_ai_advice"] and payload["ai_approved"]:
        advice = evaluate(get_config(), metrics(current), approved=True)
    result = recommend(scenario, rules, ai_advice=advice)
    result["whatif_delta"] = whatif
    return result
