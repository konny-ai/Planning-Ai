"""Opt-in, ERP-free production planning pipeline with explicit material gates.

The input supplies must use ONE explicit stock accounting convention from a
coherent inventory snapshot: net free balances or gross with reservations. This module never makes external reservations or publishes
to Factory, and never treats an unverified scenario as production-authorized.
"""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal

from planning_factory.domain.material_audit import audit_material_pegging
from planning_factory.domain.active_resource_holds import (
    ActiveResourceHold, apply_full_capacity_holds,
)
from planning_factory.domain.models import Scenario
from planning_factory.domain.production_flow import (
    MaterialAvailability, MaterialCommitment, MaterialNeed, MaterialSupply, allocate_materials,
    apply_material_releases,
)
from planning_factory.domain.setup_contract import SetupContract
from planning_factory.domain.validation import ValidationError
from planning_factory.domain.wip_snapshot import (
    MachineObservation, PausedRunProgress, prepare_paused_wip,
)
from planning_factory.engine.setup_stage_scheduler import (
    StagedSetupResult, schedule_setup_stages,
)
from planning_factory.engine.setup_capacity import StageBooking, audit_stage_capacity
from planning_factory.engine.verification import verify_plan


@dataclass(frozen=True)
class MaterialStagedPlan:
    """An immutable result scoped to the inputs of a single computation."""
    source_scenario: Scenario
    material_scenario: Scenario
    allocation: MaterialAvailability
    staged: StagedSetupResult


def plan_materials_and_setup_stages(
    scenario: Scenario,
    contract: SetupContract,
    machine_sequence: tuple[str, ...],
    supplies: tuple[MaterialSupply, ...],
    needs: tuple[MaterialNeed, ...],
    task_priority: tuple[str, ...],
    *,
    supply_quantities_net_of_external_commitments: bool = False,
    external_commitments: tuple[MaterialCommitment, ...] | None = None,
    frozen_bookings: tuple[StageBooking, ...] = (),
    max_alternatives: int = 32,
) -> MaterialStagedPlan:
    """Allocate -> audit material -> apply releases -> stage plan -> audit.

    Supply either attested net-free stock, or gross supply with the complete
    external-commitment snapshot. Never mix conventions, never double-net.
    Frozen one-segment stage bookings can be preserved exactly. Partial WIP
    and unmodelled historic consumption must be rejected by this interface.
    Zero needs are permitted only when priority covers every task.
    """
    # Exactly one stock accounting convention must be asserted. A supplied
    # empty tuple means "gross supplies, explicitly confirmed no commitments".
    # None means the gross commitment snapshot has NOT been supplied.
    net = supply_quantities_net_of_external_commitments is True
    gross = external_commitments is not None
    if net == gross:
        raise ValidationError([
            'choose exactly one stock convention: net free quantities OR '
            'gross supplies with explicit external commitments'
        ])
    commitments = () if net else external_commitments
    allocation = allocate_materials(
        scenario, supplies, needs, task_priority, commitments=commitments)
    committed = defaultdict(Decimal)
    for entry in commitments:
        committed[entry.supply_id] += entry.quantity
    material_errors = audit_material_pegging(
        scenario, supplies, needs, allocation,
        committed_by_supply=committed)
    if material_errors:
        raise ValidationError(['material audit failed', *material_errors])
    if allocation.shortages:
        raise ValidationError([
            'material shortages block scheduling',
            *(f'{task}/{item}: short by {quantity}'
              for task, item, quantity in allocation.shortages),
        ])
    adjusted = apply_material_releases(scenario, allocation)
    staged = schedule_setup_stages(adjusted, contract, machine_sequence,
                                   max_alternatives=max_alternatives,
                                   frozen_bookings=frozen_bookings)
    plan_errors = verify_plan(staged.scenario, staged.plan)
    stage_errors = audit_stage_capacity(
        contract, adjusted, machine_sequence, staged.bookings,
        frozen_bookings=frozen_bookings)
    if plan_errors or stage_errors:
        raise ValidationError([
            'end-to-end planning audit failed', *plan_errors, *stage_errors
        ])
    return MaterialStagedPlan(scenario, adjusted, allocation, staged)


def plan_residual_wip_materials_and_setup(
    scenario: Scenario,
    contract: SetupContract,
    machine_sequence: tuple[str, ...],
    observations: tuple[MachineObservation, ...],
    progresses: tuple[PausedRunProgress, ...],
    supplies: tuple[MaterialSupply, ...],
    residual_needs: tuple[MaterialNeed, ...],
    task_priority: tuple[str, ...],
    *,
    residual_material_requirements_confirmed: bool = False,
    supply_quantities_net_of_external_commitments: bool = False,
    external_commitments: tuple[MaterialCommitment, ...] | None = None,
    frozen_bookings: tuple[StageBooking, ...] = (),
    max_alternatives: int = 32,
) -> MaterialStagedPlan:
    """Normalize paused WIP, then run the same material and stage audits.

    The caller must provide actual remaining material requirements, not
    historical gross needs that would reserve consumed materials twice.
    """
    if progresses and residual_material_requirements_confirmed is not True:
        raise ValidationError([
            'WIP material requirements must be explicitly confirmed as residual'
        ])
    normalized = prepare_paused_wip(
        scenario, contract, machine_sequence, observations, progresses)
    return plan_materials_and_setup_stages(
        normalized.scenario, normalized.setup_contract, machine_sequence,
        supplies, residual_needs, task_priority,
        supply_quantities_net_of_external_commitments=(
            supply_quantities_net_of_external_commitments),
        external_commitments=external_commitments,
        frozen_bookings=frozen_bookings,
        max_alternatives=max_alternatives,
    )


def plan_materials_with_active_holds(
    scenario: Scenario,
    contract: SetupContract,
    machine_sequence: tuple[str, ...],
    holds: tuple[ActiveResourceHold, ...],
    supplies: tuple[MaterialSupply, ...],
    needs: tuple[MaterialNeed, ...],
    task_priority: tuple[str, ...],
    *,
    supply_quantities_net_of_external_commitments: bool = False,
    external_commitments: tuple[MaterialCommitment, ...] | None = None,
    frozen_bookings: tuple[StageBooking, ...] = (),
    max_alternatives: int = 32,
) -> MaterialStagedPlan:
    """Block currently occupied resources, then allocate and plan normally.

    Cannot infer progress/residuals for a run still executing inside a hold.
    Those must be handled separately, never double-counted as new work.
    """
    prepared = apply_full_capacity_holds(scenario, contract, holds)
    return plan_materials_and_setup_stages(
        prepared.scenario, prepared.setup_contract, machine_sequence,
        supplies, needs, task_priority,
        supply_quantities_net_of_external_commitments=(
            supply_quantities_net_of_external_commitments),
        external_commitments=external_commitments,
        frozen_bookings=frozen_bookings,
        max_alternatives=max_alternatives,
    )
