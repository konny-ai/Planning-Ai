"""Opt-in machine-only setup reservations projected into audited free gaps.

These placements are NOT edits to Plan or ERP publications. They model only a
machine-capacity booking, with split policy and placement chosen explicitly by
the caller. Operator needs and production dispatch policy remain undecided.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from planning_factory.domain.models import Interval, Plan, Scenario
from planning_factory.domain.validation import ValidationError
from .booking import select_segments
from .calendars import expand, intersect
from .setup_audit import audit_setups


@dataclass(frozen=True)
class SetupReservation:
    resource_id: str
    transition_index: int
    predecessor_task: str | None
    successor_task: str
    from_family: str
    to_family: str
    predecessor_end: datetime
    successor_start: datetime
    required_seconds: int
    segments: tuple[Interval, ...]


@dataclass(frozen=True)
class SetupProjection:
    scenario_id: str
    scenario_digest: str
    placement: str
    allow_split: bool
    reservations: tuple[SetupReservation, ...]
    # A projection is evidence of feasible placement in an EXISTING plan.
    # It does not modify its assignments, dispatch, loads or SQL representation.

    def to_dict(self) -> dict:
        return {
            'schema_version': 1, 'scenario_id': self.scenario_id,
            'scenario_digest': self.scenario_digest, 'mode': 'machine_only_projection',
            'placement': self.placement, 'allow_split': self.allow_split,
            'total_machine_setup_seconds': sum(r.required_seconds for r in self.reservations),
            'reservations': [
                {'resource_id': r.resource_id, 'transition_index': r.transition_index,
                 'predecessor_task': r.predecessor_task, 'successor_task': r.successor_task,
                 'from_family': r.from_family, 'to_family': r.to_family,
                 'predecessor_end': r.predecessor_end.isoformat(),
                 'successor_start': r.successor_start.isoformat(),
                 'required_seconds': r.required_seconds,
                 'segments': [{'start': seg.start.isoformat(), 'end': seg.end.isoformat()}
                              for seg in r.segments]}
                for r in self.reservations],
        }


def verify_setup_projection(s: Scenario, p: Plan, rules: dict,
                            projection: SetupProjection) -> tuple[str, ...]:
    """Independently check exact duration, calendar, bounds and machine capacity."""
    audit = audit_setups(s, p, rules)
    if not audit.valid:
        raise ValidationError(['cannot verify reservations for setup-infeasible plan'])
    errors = []
    if projection.scenario_id != s.id or projection.scenario_digest != p.scenario_digest:
        errors.append('projection bound to different scenario or plan')
    if projection.placement not in ('earliest', 'latest') or type(projection.allow_split) is not bool:
        errors.append('invalid explicit projection policy')
    expected = {(x.resource_id, i): x for i, x in enumerate(audit.transitions)
                if x.required_seconds > 0}
    actual = {(x.resource_id, x.transition_index): x for x in projection.reservations}
    if len(actual) != len(projection.reservations) or set(actual) != set(expected):
        errors.append('setup reservation coverage differs from audited transitions')
    resources = {r.id: r for r in s.resources}
    calendars = {c.id: c for c in s.calendars}
    avail = {rid: expand(calendars[resources[rid].calendar_id], s.start, s.end)
             for rid in audit.checked_machines}
    occupied = defaultdict(list)
    for a in p.assignments:
        for d in a.demands:
            if d.resource_id in avail:
                occupied[d.resource_id].extend(a.segments)
    for record in projection.reservations:
        key = record.resource_id, record.transition_index
        transition = expected.get(key)
        if transition is None:
            errors.append(f'{key}: unrecognized setup reservation')
            continue
        lower = transition.predecessor_end or s.start
        upper = transition.successor_start
        if (record.predecessor_task != transition.predecessor_task or
                record.successor_task != transition.successor_task or
                record.from_family != transition.from_family or
                record.to_family != transition.to_family or
                record.required_seconds != transition.required_seconds or
                record.predecessor_end != lower or record.successor_start != upper):
            errors.append(f'{key}: setup transition identity or bounds mismatch')
        if not projection.allow_split and len(record.segments) != 1:
            errors.append(f'{key}: atomic setup cannot span multiple segments')
        if sum(seg.seconds for seg in record.segments) != record.required_seconds:
            errors.append(f'{key}: setup duration mismatch')
        if not record.segments:
            errors.append(f'{key}: empty positive-duration setup')
        for seg in record.segments:
            if not lower <= seg.start < seg.end <= upper:
                errors.append(f'{key}: setup outside predecessor/successor gap')
                continue
            # Ensure every booked second is both available on the machine and
            # free from production work or another projected setup.
            inside = intersect(avail[record.resource_id], (seg,))
            if sum(i.seconds for i in inside) != seg.seconds:
                errors.append(f'{key}: setup outside workable machine calendar')
            if any(other.start < seg.end and seg.start < other.end
                   for other in occupied[record.resource_id]):
                errors.append(f'{key}: machine booking overlaps work or setup')
            occupied[record.resource_id].append(seg)
    return tuple(errors)


def project_machine_setups(s: Scenario, p: Plan, rules: dict, *,
                           placement: str, allow_split: bool) -> SetupProjection:
    """Materialize setup machine time within existing audited schedule gaps.

    Neither `placement` nor `allow_split` is inferred: these are still
    undecided production rules, and must be supplied explicitly.
    """
    if placement not in ('earliest', 'latest') or type(allow_split) is not bool:
        raise ValueError('choose placement earliest/latest and boolean allow_split explicitly')
    audit = audit_setups(s, p, rules)
    if not audit.valid:
        raise ValidationError(['setup-infeasible plan: cannot project real reservations'])
    resources = {r.id: r for r in s.resources}
    calendars = {c.id: c for c in s.calendars}
    work = {rid: expand(calendars[resources[rid].calendar_id], s.start, s.end)
            for rid in audit.checked_machines}
    bookings = []
    for index, t in enumerate(audit.transitions):
        if t.required_seconds == 0:
            continue
        lower = t.predecessor_end or s.start
        upper = t.successor_start
        if upper is None or lower >= upper:
            raise ValidationError([f'{t.resource_id}: no free time before {t.successor_task}'])
        free = intersect(work[t.resource_id], (Interval(lower, upper),))
        segments = select_segments(free, t.required_seconds, allow_split,
                                   backward=placement == 'latest')
        if segments is None:
            raise ValidationError([f'{t.resource_id}: cannot place setup within '
                                   f'calendar gap before {t.successor_task} '
                                   f'under explicit split policy'])
        bookings.append(SetupReservation(
            t.resource_id, index, t.predecessor_task, t.successor_task,
            t.from_family, t.to_family, lower, upper, t.required_seconds, segments))
    projection = SetupProjection(s.id, p.scenario_digest, placement,
                                 allow_split, tuple(bookings))
    errors = verify_setup_projection(s, p, rules, projection)
    if errors:
        raise ValidationError(list(errors))
    return projection
