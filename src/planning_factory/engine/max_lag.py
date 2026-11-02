"""#78 P6 hard version: enforce ``max_lag_seconds`` by postponing predecessors, or say why not.

The serial heuristic books each unit as early as it can, so a predecessor may finish long
before its successor can start. This repair moves such a predecessor later: it gives the
operation a release equal to the latest start, on its booked resources' calendars, that still
finishes before the successor (and before its own due date), then plans again. Releases only
grow, so the loop ends; it stops when nothing is left to move or after ``max_rounds``.

What cannot be repaired stays in the plan and is reported with a code:

- ``PREDECESSOR_PINNED``: the predecessor is fixed, completed or promised by a supplier;
- ``PREDECESSOR_DUE``: finishing late enough would make the predecessor miss its own due date;
- ``NO_CALENDAR_WINDOW``: the booked resources have no working time to finish in the window;
- ``CAPACITY``: the predecessor already starts as late as its calendar allows, the wait comes
  from capacity taken by other work;
- ``ROUNDS_EXHAUSTED``: still over the limit after the last round.

The serial heuristic gives no feasibility guarantee: a repaired plan can move other work, so
the result reports total tardiness before and after. The returned plan belongs to the
returned scenario (the original plus the new releases) and is verified against it.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from planning_factory.domain.models import Alternative, Plan, Scenario
from planning_factory.domain.validation import utc
from .booking import bundle_free, select_segments
from .calendars import expand
from .diagnostics import waits_over_limit
from .scheduler import plan as schedule


@dataclass(frozen=True)
class MaxLagRepair:
    scenario: Scenario
    plan: Plan
    rounds: int
    releases: tuple[tuple[str, datetime], ...]
    unrepaired: tuple[dict, ...]
    waits_before: int
    waits_after: int
    tardiness_before_seconds: int
    tardiness_after_seconds: int

    def to_dict(self) -> dict:
        return {'rounds': self.rounds, 'waits_over_limit_before': self.waits_before,
                'waits_over_limit_after': self.waits_after,
                'tardiness_before_seconds': self.tardiness_before_seconds,
                'tardiness_after_seconds': self.tardiness_after_seconds,
                'releases': [{'operation_id': oid, 'release': at} for oid, at in self.releases],
                'unrepaired': list(self.unrepaired), 'scenario_digest': self.plan.scenario_digest}


def _tardiness(p: Plan) -> int:
    return sum(o.tardiness_seconds or 0 for o in p.orders)


def enforce_max_lag(s: Scenario, *, max_rounds: int = 10, **plan_options) -> MaxLagRepair:
    if type(max_rounds) is not int or max_rounds < 1:
        raise ValueError('max_rounds must be a positive integer')
    resources = {r.id: r for r in s.resources}
    calendars = {c.id: expand(c, s.start, s.end) for c in s.calendars}
    current, p = s, schedule(s, **plan_options)
    first = waits_over_limit(current, p)
    tardiness_before = _tardiness(p)
    releases, refused, rounds = {}, {}, 0
    report = first
    while report['waits'] and rounds < max_rounds:
        ops = {o.id: o for o in current.operations}
        by_op = {oid: a for a in p.assignments for oid in a.operation_ids}
        wanted = {}
        for w in report['waits']:
            key = (w['predecessor_id'], w['operation_id'])
            before, after = ops[w['predecessor_id']], by_op[w['operation_id']]
            dep = next(d for d in ops[w['operation_id']].predecessors if d.predecessor_id == w['predecessor_id'])
            code, release = _postpone(current, resources, calendars, before, by_op[before.id], dep, after.start)
            if code:
                refused[key] = code
            else:
                refused.pop(key, None)
                wanted[before.id] = min(wanted.get(before.id, release), release)
        if not wanted:
            break
        rounds += 1
        releases.update(wanted)
        current = replace(current, operations=tuple(replace(o, release=wanted[o.id]) if o.id in wanted else o
                                                    for o in current.operations))
        p = schedule(current, **plan_options)
        report = waits_over_limit(current, p)
    unrepaired = tuple({**w, 'code': refused.get((w['predecessor_id'], w['operation_id']), 'ROUNDS_EXHAUSTED')}
                       for w in report['waits'])
    return MaxLagRepair(current, p, rounds, tuple(sorted(releases.items())), unrepaired,
                        first['over_limit'], report['over_limit'], tardiness_before, _tardiness(p))


def _postpone(s, resources, calendars, op, booked, dep, successor_start):
    """Latest release that lets ``op`` finish within the allowed wait of its successor, or a refusal code."""
    if op.fixed_start is not None or op.completed_at is not None or op.external_end is not None or booked.fixed:
        return 'PREDECESSOR_PINNED', None
    earliest_end = successor_start - timedelta(seconds=dep.max_lag_seconds)
    target = successor_start - timedelta(seconds=dep.lag_seconds)
    if op.due is not None:
        if utc(op.due) < earliest_end:
            return 'PREDECESSOR_DUE', None
        target = min(target, utc(op.due))
    duration = sum(seg.seconds for seg in booked.segments)
    free = bundle_free(Alternative(booked.demands), resources, calendars, {}, utc(s.start), target, {})
    segments = select_segments(free, duration, op.splittable, backward=True)
    if not segments or segments[-1].end < earliest_end:
        return 'NO_CALENDAR_WINDOW', None
    if segments[0].start <= booked.start:
        return 'CAPACITY', None
    return None, segments[0].start

