"""Constraint chain per plan unit and finite-capacity critical path per order (#88).

Reconstructed from a verified plan and its scenario only, independently of the
algorithm that produced the plan, and without changing it. For every scheduled unit
the binding constraint is the one whose earliest calendar-feasible start equals the
actual start: fixed start, horizon, release (or material, when labelled), predecessor.
If none is tight the unit waited for capacity, and the occupant of the shared resource
that finished last before the start is reported. Backward units are placed against
their target, so their start is explained by the target, not by an earliest bound.
"""
from collections import defaultdict
from datetime import timedelta

from planning_factory.domain.models import Plan, Scenario
from planning_factory.domain.validation import ValidationError, compile_tasks
from .calendars import expand, intersect
from .verification import verify_plan

_PREFERENCE = {'predecessor': 0, 'material': 1, 'release': 1, 'horizon': 2}


def _earliest(intervals, bound, seconds, splittable):
    """First calendar-feasible start at or after bound, ignoring capacity.

    A non-splittable unit needs one working interval long enough for its booked work.
    """
    for interval in intervals:
        start = max(interval.start, bound)
        if interval.end <= start:
            continue
        if splittable or (interval.end - start).total_seconds() >= seconds:
            return start
    return None


def _text(value):
    return value.isoformat().replace('+00:00', 'Z') if value is not None else None


def constraint_chain(s: Scenario, p: Plan, release_reasons: dict[str, str] | None = None) -> dict:
    errors = verify_plan(s, p)
    if errors:
        raise ValidationError(list(errors))
    release_reasons = release_reasons or {}
    tasks, edges, _ = compile_tasks(s)
    calendars = {c.id: expand(c, s.start, s.end) for c in s.calendars}
    resource_calendar = {r.id: calendars[r.calendar_id] for r in s.resources}
    assigned = {a.task_id: a for a in p.assignments}
    incoming = defaultdict(list)
    for e in edges:
        incoming[e.successor].append(e)
    occupancy = defaultdict(list)
    for a in p.assignments:
        for d in a.demands:
            for segment in a.segments:
                occupancy[d.resource_id].append((segment.end, a.task_id))
    units = {}
    for tid, a in sorted(assigned.items()):
        entry = {'operations': list(a.operation_ids), 'start': _text(a.start), 'end': _text(a.end)}
        units[tid] = entry
        if not a.segments:
            entry['cause'] = 'completed' if a.completed_at is not None else 'external'
            continue
        if a.fixed:
            entry['cause'] = 'fixed'
            continue
        if a.effective_mode == 'backward':
            entry['cause'] = 'backward'
            entry['detail'] = 'placed as late as possible against its due date or successors'
            continue
        window = None
        for d in a.demands:
            window = resource_calendar[d.resource_id] if window is None else intersect(window, resource_calendar[d.resource_id])
        task = tasks[tid]
        candidates = [('horizon', None, s.start)]
        if task.release > s.start:
            candidates.append((release_reasons.get(tid, 'release'), None, task.release))
        for e in incoming[tid]:
            before = assigned.get(e.predecessor)
            if before is not None and before.start is not None:
                base = before.end if e.kind == 'FS' else before.start
                candidates.append(('predecessor', e.predecessor, base + timedelta(seconds=e.lag_seconds)))
        booked = sum(x.seconds for x in a.segments)
        tight = [(kind, ref, bound) for kind, ref, bound in candidates
                 if _earliest(window or (), bound, booked, task.splittable) == a.start]
        if tight:
            kind, ref, bound = max(tight, key=lambda c: (c[2], -_PREFERENCE.get(c[0], 1), c[1] or ''))
            entry.update(cause=kind, bound=_text(bound))
            if ref:
                entry['ref'] = ref
            wait = int((a.start - bound).total_seconds())
            if wait:
                entry['calendar_wait_seconds'] = wait
            continue
        earliest = max((_earliest(window or (), b, booked, task.splittable) or a.start for _, _, b in candidates),
                       default=a.start)
        # Occupants of a shared resource that released it during the wait.
        blockers = [(end, other, d.resource_id) for d in a.demands for end, other in occupancy[d.resource_id]
                    if other != tid and earliest < end <= a.start]
        entry.update(cause='resource', capacity_wait_seconds=int((a.start - earliest).total_seconds()))
        if blockers:
            end, other, resource = max(blockers, key=lambda b: (b[0], b[1], b[2]))
            entry.update(ref=other, resource=resource)
    order_of = {o.id: o.order_id for o in s.operations}
    orders = {}
    for result in p.orders:
        mine = [tid for tid, a in assigned.items() if any(order_of[o] == result.order_id for o in a.operation_ids)]
        if not mine:
            continue
        last = max(mine, key=lambda t: (assigned[t].end, t))
        path, seen, current = [], set(), last
        while current is not None and current not in seen and current in units:
            seen.add(current)
            step = {'unit': current, 'cause': units[current]['cause']}
            for key in ('ref', 'resource'):
                if key in units[current]:
                    step[key] = units[current][key]
            path.append(step)
            current = units[current].get('ref') if units[current]['cause'] in ('predecessor', 'resource') else None
        orders[result.order_id] = {'end': _text(result.end), 'due': _text(result.due),
                                   'tardiness_seconds': result.tardiness_seconds, 'critical_path': path}
    result = {'scenario_digest': p.scenario_digest, 'units': units, 'orders': orders,
              'unscheduled': {u.task_id: u.code for u in p.unscheduled}}
    agreement = cross_check(p, units)
    if agreement['checked']:
        result['scheduler_agreement'] = agreement
    return result


def cross_check(p: Plan, units: dict) -> dict:
    """Compare the constraints recorded by the scheduler with the independent reconstruction."""
    mismatches, checked = [], 0
    for a in p.assignments:
        recorded = next((e[len('constraint='):] for e in a.explanation if e.startswith('constraint=')), None)
        if recorded is None or a.task_id not in units:
            continue
        checked += 1
        unit = units[a.task_id]
        kind, _, rest = recorded.partition(':')
        kind = kind.split(';')[0]
        ref = rest.split(';')[0] if rest else None
        calendar = recorded.endswith(';calendar')
        cause = unit['cause']
        if kind == 'capacity':
            same = cause == 'resource'
        elif kind == 'predecessor':
            same = cause == 'predecessor' and unit.get('ref') == ref
        elif kind in ('release', 'horizon'):
            same = cause in (kind, 'material') if kind == 'release' else cause == 'horizon'
        else:
            same = cause == kind
        if same and kind not in ('capacity', 'backward', 'fixed'):
            same = calendar == bool(unit.get('calendar_wait_seconds'))
        if not same:
            mismatches.append({'unit': a.task_id, 'scheduler': recorded, 'reconstruction': cause,
                               **({'ref': unit['ref']} if 'ref' in unit else {})})
    return {'checked': checked, 'agree': checked - len(mismatches), 'mismatches': mismatches}
