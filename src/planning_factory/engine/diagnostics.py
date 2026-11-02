"""Plan diagnostics from #78 that never change a plan: P13 nesting advance, P18 stability, P19 overtime what-if.

- P13: members of a nest worked long before they are needed because the most urgent
  order of the program pulls them forward (stock effect). Report only.
- P18: how much a recomputation moves the published plan, inside a window and above a
  threshold, on top of ``compare``.
- P19: candidate extra working windows evaluated in simulation, each with its verified
  KPI gain; the published plan is not touched.
- P6 (first version): FS waits longer than the declared ``max_lag_seconds``, reported
  as a warning and KPI; the serial heuristic does not enforce them.
"""
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from planning_factory.domain.models import CalendarException, Plan, Scenario
from planning_factory.domain.validation import ValidationError
from .calendars import expand
from .optimization import measure
from .scheduler import plan as schedule
from .simulation import compare
from .verification import verify_plan


def _text(value):
    return value.isoformat().replace('+00:00', 'Z') if value is not None else None


def nesting_advance(s: Scenario, p: Plan, max_advance_seconds: int) -> dict:
    """P13: advance = (member due - remaining downstream work) - nest start; flagged above the limit."""
    errors = verify_plan(s, p)
    if errors:
        raise ValidationError(list(errors))
    ops = {o.id: o for o in s.operations}
    successors = defaultdict(list)
    for o in s.operations:
        for d in o.predecessors:
            successors[d.predecessor_id].append(o.id)

    def downstream(op_id, seen=None):
        seen = set() if seen is None else seen
        total = 0
        for nxt in successors[op_id]:
            if nxt not in seen:
                seen.add(nxt)
                total += ops[nxt].total_seconds + downstream(nxt, seen)
        return total

    flagged, measured = [], 0
    for a in p.assignments:
        if not a.task_id.startswith('nest:') or not a.segments:
            continue
        for op_id in a.operation_ids:
            due = ops[op_id].due
            if due is None:
                continue
            measured += 1
            needed_start = due - timedelta(seconds=downstream(op_id))
            advance = int((needed_start - a.start).total_seconds())
            if advance > max_advance_seconds:
                flagged.append({'unit': a.task_id, 'operation_id': op_id, 'order_id': ops[op_id].order_id,
                                'nest_start': _text(a.start), 'needed_start': _text(needed_start),
                                'advance_seconds': advance})
    flagged.sort(key=lambda f: (-f['advance_seconds'], f['operation_id']))
    return {'max_advance_seconds': max_advance_seconds, 'members_measured': measured, 'flagged': flagged,
            'largest_advance_seconds': flagged[0]['advance_seconds'] if flagged else 0}


def stability(published: Plan, recomputed: Plan, *, threshold_seconds: int = 0, window_end: datetime | None = None) -> dict:
    """P18: operations moved more than the threshold, optionally only those published to start before window_end."""
    if type(threshold_seconds) is not int or threshold_seconds < 0:
        raise ValidationError(['threshold_seconds must be a nonnegative integer'])
    diff = compare(published, recomputed)
    before = {oid: a for a in published.assignments for oid in a.operation_ids}
    after = {oid for a in recomputed.assignments for oid in a.operation_ids}
    moved, added, removed = [], [], []
    for change in diff['changed_operations']:
        oid = change['operation_id']
        a = before.get(oid)
        if a is None:
            added.append(oid)  # new work, not a move of the published plan
            continue
        if window_end is not None and (a.start is None or a.start >= window_end):
            continue
        if oid not in after:
            removed.append(oid)
            continue
        shift = change['shift_seconds']
        if abs(shift) > threshold_seconds or change['resources_changed']:
            moved.append({'operation_id': change['operation_id'], 'shift_seconds': shift,
                          'resources_changed': change['resources_changed']})
    shifts = [abs(m['shift_seconds']) for m in moved if m['shift_seconds'] is not None]
    return {'threshold_seconds': threshold_seconds, 'window_end': _text(window_end),
            'operations_compared': diff['assignment_operations_compared'], 'moved_operations': len(moved),
            'mean_absolute_shift_seconds': int(sum(shifts) / len(shifts)) if shifts else 0,
            'max_absolute_shift_seconds': max(shifts, default=0), 'moved': moved,
            'added_operations': added, 'removed_operations': removed}


def _with_extra_window(s: Scenario, resource_id: str, start: datetime, end: datetime) -> Scenario:
    """Give one resource its own copy of its calendar with an extra working window."""
    resource = next((r for r in s.resources if r.id == resource_id), None)
    if resource is None:
        raise ValidationError([f'unknown resource {resource_id}'])
    calendar = next(c for c in s.calendars if c.id == resource.calendar_id)
    zone = ZoneInfo(calendar.timezone)
    local_start, local_end = start.astimezone(zone), end.astimezone(zone)
    if local_start.date() != local_end.date() and local_end.time().isoformat() != '00:00:00':
        raise ValidationError(['an overtime window must stay within one local day'])
    day = local_start.date()
    existing = [(sh.start, sh.end) for sh in calendar.shifts if sh.weekday == day.weekday()]
    for exception in calendar.exceptions:
        if exception.day == day:
            existing = list(exception.shifts)
    shifts = tuple(sorted(existing + [(local_start.time(), local_end.time())]))
    exceptions = tuple(e for e in calendar.exceptions if e.day != day) + (CalendarException(day, shifts),)
    copy = replace(calendar, id=f'{calendar.id}+overtime:{resource_id}', exceptions=exceptions,
                   holidays=tuple(h for h in calendar.holidays if h != day))
    resources = tuple(replace(r, calendar_id=copy.id) if r.id == resource_id else r for r in s.resources)
    return replace(s, calendars=s.calendars + (copy,), resources=resources)


def overtime_candidates(s: Scenario, candidates: list[dict]) -> dict:
    """P19: plan once per candidate window; report the verified KPI change against the unchanged baseline."""
    baseline_plan = schedule(s)
    baseline = measure(s, baseline_plan, 'baseline')
    results = []
    for c in candidates:
        if not isinstance(c, dict) or set(c) != {'resource_id', 'start', 'end'}:
            raise ValidationError(['each candidate needs exactly resource_id, start, end'])
        start, end = (datetime.fromisoformat(c[k].replace('Z', '+00:00')) for k in ('start', 'end'))
        if start.tzinfo is None or end.tzinfo is None or not start < end:
            raise ValidationError(['candidate window needs ordered instants with offset'])
        variant = _with_extra_window(s, c['resource_id'], start, end)
        calendar = next(cal for cal in variant.calendars if cal.id.endswith(f'overtime:{c["resource_id"]}'))
        added = sum(i.seconds for i in expand(calendar, s.start, s.end)) - \
            sum(i.seconds for i in expand(next(cal for cal in s.calendars if cal.id == calendar.id.split('+overtime:')[0]),
                                          s.start, s.end))
        metrics = measure(variant, schedule(variant), 'overtime')
        results.append({**c, 'added_seconds': added,
                        'unscheduled_operations_gain': baseline.unscheduled_operations - metrics.unscheduled_operations,
                        'late_orders_gain': baseline.late_orders - metrics.late_orders,
                        'tardiness_seconds_gain': baseline.total_tardiness_seconds - metrics.total_tardiness_seconds,
                        'makespan_seconds_gain': baseline.makespan_seconds - metrics.makespan_seconds})
    results.sort(key=lambda r: (-r['late_orders_gain'], -r['tardiness_seconds_gain'], r['resource_id'], r['start']))
    return {'baseline': {'unscheduled_operations': baseline.unscheduled_operations, 'late_orders': baseline.late_orders,
                         'total_tardiness_seconds': baseline.total_tardiness_seconds,
                         'makespan_seconds': baseline.makespan_seconds},
            'candidates': results}


def waits_over_limit(s: Scenario, p: Plan) -> dict:
    """P6: successors that start later than max_lag_seconds after their FS predecessor ends."""
    errors = verify_plan(s, p)
    if errors:
        raise ValidationError(list(errors))
    by_op = {oid: a for a in p.assignments for oid in a.operation_ids}
    checked, over = 0, []
    for o in s.operations:
        for d in o.predecessors:
            if d.max_lag_seconds is None:
                continue
            before, after = by_op.get(d.predecessor_id), by_op.get(o.id)
            if before is None or after is None or not after.segments or before.end is None:
                continue
            checked += 1
            wait = int((after.start - before.end).total_seconds())
            if wait > d.max_lag_seconds:
                over.append({'predecessor_id': d.predecessor_id, 'operation_id': o.id, 'wait_seconds': wait,
                             'max_lag_seconds': d.max_lag_seconds, 'excess_seconds': wait - d.max_lag_seconds,
                             'predecessor_end': _text(before.end), 'successor_start': _text(after.start)})
    over.sort(key=lambda x: (-x['excess_seconds'], x['operation_id']))
    return {'dependencies_checked': checked, 'over_limit': len(over),
            'max_excess_seconds': over[0]['excess_seconds'] if over else 0, 'waits': over}
