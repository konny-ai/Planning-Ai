"""Strict, read-only JSON entrypoint to the v0.3 material/setup/WIP pipeline.

The file is explicitly selected by a local operator on startup, never supplied
as a request path or inferred from Factory. It must refer to the exact Scenario
digest, declare a single stock convention and provide all machine/setup facts.
"""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json
import re

from planning_factory.adapters.json_io import _no_duplicates, scenario_digest, timestamp
from planning_factory.domain.models import Interval, Scenario
from planning_factory.domain.production_flow import (
    MaterialCommitment, MaterialNeed, MaterialSupply,
)
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.wip_snapshot import MachineObservation, PausedRunProgress
from planning_factory.domain.active_resource_holds import ActiveResourceHold
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.setup_capacity import StageAllocation, StageBooking
from planning_factory.engine.material_stage_pipeline import (
    plan_materials_and_setup_stages, plan_residual_wip_materials_and_setup,
    plan_materials_with_active_holds,
)

MAX_SPEC_BYTES = 1024 * 1024


@dataclass(frozen=True)
class StagedFileRequest:
    setup_contract: object
    machine_sequence: tuple[str, ...]
    supplies: tuple[MaterialSupply, ...]
    needs: tuple[MaterialNeed, ...]
    task_priority: tuple[str, ...]
    inventory_net: bool
    external_commitments: tuple[MaterialCommitment, ...] | None
    frozen_bookings: tuple[StageBooking, ...]
    observations: tuple[MachineObservation, ...]
    progresses: tuple[PausedRunProgress, ...]
    active_holds: tuple[ActiveResourceHold, ...]
    residual_material_requirements_confirmed: bool
    max_alternatives: int
    mode: str
    backward: bool = False
    sequence_objective: str | None = None


def _keys(value, required, optional=(), label="object"):
    if not isinstance(value, dict) or set(required)-set(value) or set(value)-set(required)-set(optional):
        raise ValidationError([f"{label}: invalid or missing fields"])
    return value


def _list(value, label, maximum=2000):
    if not isinstance(value, list) or len(value)>maximum:
        raise ValidationError([f"{label}: bounded array required"])
    return value


def _id(value, label):
    if not isinstance(value, str) or not value.strip() or len(value)>256:
        raise ValidationError([f"{label}: nonempty identifier (max 256) required"])
    return value


def _seconds(value, label, *, positive=False):
    if type(value) is not int or (value<=0 if positive else value<0):
        raise ValidationError([f"{label}: exact integer seconds required"])
    return value


def _qty(value, label):
    if not isinstance(value, str):
        raise ValidationError([f"{label}: exact decimal string required (not float)"])
    try:
        number=Decimal(value)
    except InvalidOperation:
        raise ValidationError([f"{label}: invalid decimal"]) from None
    if not number.is_finite() or number<=0:
        raise ValidationError([f"{label}: finite positive quantity required"])
    return number


def _instant(value, label):
    try:
        instant=timestamp(value)
    except ValidationError:
        raise ValidationError([f"{label}: ISO8601 timestamp with UTC offset required"]) from None
    if instant is None or instant.microsecond:
        raise ValidationError([f"{label}: non-null timestamp at second precision required"])
    return instant


def _ids(value, label):
    items=_list(value,label)
    values=tuple(_id(x,label) for x in items)
    if len(values)!=len(set(values)):
        raise ValidationError([f"{label}: duplicate IDs"])
    return values


def _stage_booking(raw):
    r=_keys(raw,("phase_id","stage","segments","allocations"),label="frozen booking")
    tid=_id(r["phase_id"],"frozen phase")
    if r["stage"] not in ("setup","run"):
        raise ValidationError([f"{tid}: stage must be setup or run"])
    segments=[]
    for seg in _list(r["segments"],"frozen segments",10):
        seg=_keys(seg,("start","end"),label="segment")
        first,last=_instant(seg["start"],"segment start"),_instant(seg["end"],"segment end")
        if first>=last:
            raise ValidationError([f"{tid}: invalid stage interval"])
        segments.append(Interval(first,last))
    allocations=[]
    for x in _list(r["allocations"],"stage allocations",100):
        x=_keys(x,("resource_id","occupancy_bps"),label="stage allocation")
        bps=_seconds(x["occupancy_bps"],"occupancy",positive=True)
        if bps>10000:
            raise ValidationError(["occupancy must not exceed 10000 bps"])
        allocations.append(StageAllocation(_id(x["resource_id"],"resource"),bps))
    return StageBooking(tid,r["stage"],tuple(segments),tuple(allocations))


def load_staged_file(path: Path, scenario: Scenario) -> StagedFileRequest:
    path=Path(path).expanduser().resolve(strict=True)
    if path.suffix.lower()!=".json" or path.stat().st_size>MAX_SPEC_BYTES:
        raise ValidationError(["v0.3 specification must be a JSON file under 1 MiB"])
    try:
        raw=json.loads(path.read_text(encoding="utf-8"),
                       object_pairs_hook=_no_duplicates,
                       parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (ValueError, TypeError) as exc:
        raise ValidationError([f"invalid v0.3 JSON: {exc}"]) from None
    required=("schema_version","source_scenario_digest","mode","setup_contract",
              "machine_sequence","task_priority","stock","needs")
    optional=("frozen_bookings","max_alternatives","observations",
              "paused_progresses","active_holds","residual_material_requirements_confirmed",
              "backward","sequence_objective")
    r=_keys(raw,required,optional,"v0.3 specification")
    if type(r["schema_version"]) is not int or r["schema_version"]!=1:
        raise ValidationError(["v0.3 specification schema_version must be 1"])
    digest=r["source_scenario_digest"]
    if not isinstance(digest,str) or not re.fullmatch(r"[a-f0-9]{64}",digest):
        raise ValidationError(["source_scenario_digest must be exact SHA256"])
    if digest!=scenario_digest(scenario):
        raise ValidationError(["v0.3 spec does not match the immutable source Scenario digest"])
    mode=r["mode"]
    if mode not in ("normal","paused","holds"):
        raise ValidationError(["v0.3 mode must be normal, paused or holds"])
    contract=load_setup_contract(json.dumps(r["setup_contract"],ensure_ascii=False,allow_nan=False))
    sequence=_ids(r["machine_sequence"],"machine_sequence")
    priority=_ids(r["task_priority"],"task_priority")
    stock=_keys(r["stock"],("accounting","supplies"),("external_commitments",),"stock")
    accounting=stock["accounting"]
    if accounting not in ("net","gross"):
        raise ValidationError(["stock accounting must be net or gross"])
    if (accounting=="net" and "external_commitments" in stock) or (
            accounting=="gross" and "external_commitments" not in stock):
        raise ValidationError(["net stock forbids commitments; gross stock requires explicit commitments"])
    supplies=[]
    for item in _list(stock["supplies"],"supplies"):
        item=_keys(item,("id","item_id","quantity","available_at"),label="supply")
        supplies.append(MaterialSupply(_id(item["id"],"supply ID"),
                                       _id(item["item_id"],"supply item"),
                                       _qty(item["quantity"],"supply quantity"),
                                       _instant(item["available_at"],"supply availability")))
    commitments=None if accounting=="net" else tuple(
        MaterialCommitment(_id(x["supply_id"],"commitment supply"),
                           _qty(x["quantity"],"external commitment quantity"))
        for x in (_keys(item,("supply_id","quantity"),label="external commitment")
                  for item in _list(stock["external_commitments"],"commitments"))
    )
    needs=[]
    for item in _list(r["needs"],"material needs"):
        item=_keys(item,("task_id","item_id","quantity"),label="material need")
        needs.append(MaterialNeed(_id(item["task_id"],"need task"),
                                  _id(item["item_id"],"need item"),
                                  _qty(item["quantity"],"need quantity")))
    frozen=tuple(_stage_booking(row) for row in
                 _list(r.get("frozen_bookings",[]),"frozen bookings"))
    observations=[]
    for row in _list(r.get("observations",[]),"machine observations"):
        row=_keys(row,("machine_id","observed_at","state_id","source_ref","idle_confirmed"),label="observation")
        if type(row["idle_confirmed"]) is not bool:
            raise ValidationError(["idle_confirmed must be an explicit boolean"])
        state=row["state_id"]
        if state is not None:
            state=_id(state,"observed setup state")
        observations.append(MachineObservation(
            _id(row["machine_id"],"observed machine"),
            _instant(row["observed_at"],"machine observation"),
            state,_id(row["source_ref"],"observation provenance"),
            row["idle_confirmed"]))
    progresses=[]
    for row in _list(r.get("paused_progresses",[]),"paused progresses"):
        row=_keys(row,("task_id","machine_id","original_seconds","worked_seconds",
                       "remaining_seconds","paused_at","source_ref"),label="paused progress")
        progresses.append(PausedRunProgress(
            _id(row["task_id"],"paused task"),_id(row["machine_id"],"paused machine"),
            _seconds(row["original_seconds"],"original run",positive=True),
            _seconds(row["worked_seconds"],"worked run",positive=True),
            _seconds(row["remaining_seconds"],"remaining run",positive=True),
            _instant(row["paused_at"],"paused_at"),_id(row["source_ref"],"progress provenance")))
    holds=[]
    for row in _list(r.get("active_holds",[]),"active holds"):
        row=_keys(row,("resource_id","observed_at","reserved_until","source_ref","occupying_task_id"),
                  label="active hold")
        holds.append(ActiveResourceHold(
            _id(row["resource_id"],"held resource"),
            _instant(row["observed_at"],"hold observation"),
            _instant(row["reserved_until"],"hold release"),
            _id(row["source_ref"],"hold provenance"),
            _id(row["occupying_task_id"],"occupying task")))
    if mode=="normal" and (observations or progresses or holds):
        raise ValidationError(["normal mode forbids WIP observations, progresses or active holds"])
    if mode=="paused" and holds:
        raise ValidationError(["paused WIP mode cannot be combined with an active hold"])
    if mode=="holds" and (observations or progresses):
        raise ValidationError(["active holds mode cannot infer paused WIP or machine observations"])
    if mode=="paused" and not progresses:
        raise ValidationError(["paused mode requires explicit progressed tasks"])
    if mode=="holds" and not holds:
        raise ValidationError(["holds mode requires explicit current occupations"])
    confirmed=r.get("residual_material_requirements_confirmed",False)
    if type(confirmed) is not bool or (mode!="paused" and confirmed):
        raise ValidationError(["residual material confirmation is reserved for paused WIP"])
    alternatives=r.get("max_alternatives",32)
    if type(alternatives) is not int or not 1<=alternatives<=64:
        raise ValidationError(["max_alternatives must be 1..64"])
    backward=r.get("backward",False)
    objective=r.get("sequence_objective")
    if type(backward) is not bool:
        raise ValidationError(["backward must be an explicit boolean"])
    if objective is not None and objective not in ("delivery","setup","gap"):
        raise ValidationError(["sequence_objective must be delivery, setup or gap"])
    if objective is not None and not backward:
        raise ValidationError(["sequence_objective requires backward=true"])
    return StagedFileRequest(contract,sequence,tuple(supplies),tuple(needs),
                             priority,accounting=="net",commitments,frozen,
                             tuple(observations),tuple(progresses),tuple(holds),
                             confirmed,alternatives,mode,backward,objective)


def calculate_staged_preview(scenario: Scenario, spec: StagedFileRequest):
    opts={"supply_quantities_net_of_external_commitments":spec.inventory_net,
          "external_commitments":spec.external_commitments,
          "frozen_bookings":spec.frozen_bookings,
          "max_alternatives":spec.max_alternatives,
          "backward":spec.backward,
          "sequence_objective":spec.sequence_objective}
    if spec.mode=="paused":
        return plan_residual_wip_materials_and_setup(
            scenario,spec.setup_contract,spec.machine_sequence,
            spec.observations,spec.progresses,spec.supplies,spec.needs,
            spec.task_priority,
            residual_material_requirements_confirmed=spec.residual_material_requirements_confirmed,
            **opts)
    if spec.mode=="holds":
        return plan_materials_with_active_holds(
            scenario,spec.setup_contract,spec.machine_sequence,
            spec.active_holds,spec.supplies,spec.needs,spec.task_priority,
            **opts)
    return plan_materials_and_setup_stages(
        scenario,spec.setup_contract,spec.machine_sequence,
        spec.supplies,spec.needs,spec.task_priority,**opts)
