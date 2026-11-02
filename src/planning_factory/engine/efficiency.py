"""#78 P9: resource efficiency measured on recorded history, for adapters that read actual times.

The scheduler only reads ``Resource.efficiency_bp``. This module turns pairs of (standard,
actual) seconds into that value, and never invents one: with too few samples the resource
keeps 100 % and a diagnostic says why.
"""
from dataclasses import dataclass, replace

from planning_factory.domain.models import Scenario
from planning_factory.domain.validation import MAX_EFFICIENCY_BP


@dataclass(frozen=True)
class EfficiencyEstimate:
    resource_id: str
    samples: int
    efficiency_bp: int | None  # None = not enough usable history
    detail: str


def history_efficiency_bp(resource_id: str, samples, min_samples: int = 5) -> EfficiencyEstimate:
    """Ratio of total standard to total actual time over the usable samples, in basis points."""
    usable = [(int(std), int(act)) for std, act in samples if int(std) > 0 and int(act) > 0]
    if len(usable) < min_samples:
        return EfficiencyEstimate(resource_id, len(usable), None,
                                  f'INSUFFICIENT_HISTORY: {len(usable)} usable samples < {min_samples}, kept 100%')
    standard, actual = sum(s for s, _ in usable), sum(a for _, a in usable)
    bp = min(MAX_EFFICIENCY_BP, max(1, round(standard * 10000 / actual)))
    return EfficiencyEstimate(resource_id, len(usable), bp, f'{len(usable)} samples: {standard}s standard / {actual}s actual')


def apply_history(s: Scenario, samples_by_resource: dict, min_samples: int = 5) -> tuple[Scenario, tuple[EfficiencyEstimate, ...]]:
    """Set ``efficiency_bp`` from history where it is sufficient; other resources are left unchanged."""
    estimates = tuple(history_efficiency_bp(rid, samples_by_resource.get(rid, ()), min_samples)
                      for rid in sorted(r.id for r in s.resources))
    measured = {e.resource_id: e.efficiency_bp for e in estimates if e.efficiency_bp is not None}
    resources = tuple(replace(r, efficiency_bp=measured[r.id]) if r.id in measured else r for r in s.resources)
    return replace(s, resources=resources), estimates
