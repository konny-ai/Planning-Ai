"""Deterministic serial schedule generation with finite multi-resource capacity."""
import heapq
from datetime import timedelta
from planning_factory.domain.models import (
    Assignment, Interval, OrderResult, Plan, ResourceLoad, Scenario, Unscheduled,
)
from planning_factory.domain.validation import ValidationError, compile_tasks, planned_seconds, utc
from .booking import bundle_free, reserve, select_segments
from .calendars import expand


MAX_BACKWARD_REPAIRS = 5


def plan(s: Scenario, *, dispatch_policy: str = 'priority',
         dispatch_order: tuple[str, ...] | None = None, record_constraints: bool = False,
         resume: dict | None = None, trace: dict | None = None) -> Plan:
    """Plan deterministically, then repair backward targets that proved unreachable.

    Backward (ALAP) targets are capacity-free estimates, fixed on a predecessor before its
    successors are booked. When a successor does not fit before its target and falls back to
    forward, the predecessors already sit too late to recover. Each repair round moves the
    backward targets of those predecessors earlier by the shortfall and replans; the repaired
    plan is kept only if it neither leaves more work unplaced nor adds tardiness.

    ``trace`` (output) and ``resume`` (input) enable an exact incremental replan: when a
    scenario differs from a previous one only by the ``release`` of some tasks, the tasks
    dispatched before the first changed one are identical, so their assignments are reused
    instead of searched again. ``resume`` is ``trace`` of the previous plan plus
    ``changed`` (task IDs). Every precondition is re-checked (``_reusable``); when any fails
    the plan is simply computed in full, so the result never depends on ``resume``.
    """
    shifts: dict[str, int] = {}
    best = _schedule(s, dispatch_policy, dispatch_order, record_constraints, shifts, report := {}, resume)
    if trace is not None and not shifts and 'order' in report:
        trace.update(order=report['order'], tasks=report['tasks'], edges=report['edges'], scenario=s,
                     plan=best, record_constraints=record_constraints)
    for _ in range(MAX_BACKWARD_REPAIRS):
        if not report['shifts']:
            break
        trial = dict(shifts)
        for k, v in report['shifts'].items():
            trial[k] = trial.get(k, 0) + v  # the shortfall is measured against the already shifted target
        candidate = _schedule(s, dispatch_policy, dispatch_order, record_constraints, trial, trial_report := {})
        if _rank(candidate) > _rank(best):
            break
        best, shifts, report = candidate, trial, trial_report
    return best


def _rank(result: Plan) -> tuple[int, int]:
    """Unplaced work first, then total tardiness (a missing delivery result is not an improvement)."""
    return len(result.unscheduled), sum(o.tardiness_seconds or 0 for o in result.orders)


def _reusable(s: Scenario, tasks: dict, edges, record_constraints: bool, resume: dict) -> dict:
    """Assignments of the previous plan that are provably identical in this one, or {}.

    Valid only for forward-only 'priority' dispatch, where the dispatch order depends on
    static keys (never on ``release``). Everything except the ``release`` of the changed
    tasks must be equal, and the previous plan must have no unscheduled work.
    """
    from dataclasses import replace
    base_s, base_tasks = resume['scenario'], resume['tasks']
    if (resume.get('record_constraints') != record_constraints
            or (s.start, s.end, s.calendars, s.resources, s.settings) !=
            (base_s.start, base_s.end, base_s.calendars, base_s.resources, base_s.settings)
            or set(tasks) != set(base_tasks) or set(edges) != set(resume['edges'])
            or any(t.mode != 'forward' for t in tasks.values())
            or resume['plan'].unscheduled):
        return {}
    changed = resume['changed']
    for tid, task in tasks.items():
        before = base_tasks[tid]
        if (replace(task, release=before.release) if tid in changed else task) != before:
            return {}
    order = resume['order']
    cut = next((i for i, tid in enumerate(order) if tid in changed), None)
    if cut is None:
        return {}   # a changed task was not dispatched (pinned or historic): replan in full
    by_id = {a.task_id: a for a in resume['plan'].assignments}
    if any(tid not in by_id for tid in order[:cut]):
        return {}
    return {tid: by_id[tid] for tid in order[:cut]}


def _schedule(s: Scenario, dispatch_policy: str, dispatch_order, record_constraints: bool,
              shifts: dict, report: dict, resume: dict | None = None) -> Plan:
    """Plan deterministically; alternative dispatch policies are opt-in only.

    With ``record_constraints`` every assignment also carries one machine-readable
    ``constraint=<kind>[:<ref>][;calendar]`` explanation entry naming the constraint that
    fixed its start (#88); off by default so published plans and goldens do not change.
    """
    supported = ('priority', 'earliest_due', 'shortest_processing', 'longest_processing',
                 'min_slack', 'critical_ratio', 'atc')
    if dispatch_policy not in supported:
        raise ValueError(f'unsupported dispatch policy: {dispatch_policy}')
    tasks, edges, topo = compile_tasks(s)
    if dispatch_order is not None:
        if (dispatch_policy != 'priority' or not isinstance(dispatch_order, tuple)
                or len(dispatch_order) != len(tasks) or len(set(dispatch_order)) != len(tasks)
                or set(dispatch_order) != set(tasks)):
            raise ValueError('dispatch_order must be a permutation of all task IDs under priority mode')
    reuse = {}
    if resume is not None and dispatch_policy == 'priority' and dispatch_order is None and not shifts:
        reuse = _reusable(s, tasks, edges, record_constraints, resume)
    dispatched = []
    order_rank = {tid: pos for pos, tid in enumerate(dispatch_order)} if dispatch_order is not None else None
    resources = {r.id: r for r in s.resources}
    calendars = {c.id: expand(c, s.start, s.end) for c in s.calendars}
    incoming = {k: [] for k in tasks}
    outgoing = {k: [] for k in tasks}
    for e in edges:
        incoming[e.successor].append(e)
        outgoing[e.predecessor].append(e)
    reservations = {}
    switch_notes = {}
    min_gain = s.settings.alternative_switch_min_gain_seconds
    free_cache, empty_cache = {}, {}
    assigned = {}
    failed = {}
    start, end = utc(s.start), utc(s.end)

    auto_notes = {}

    def dur(t, demands):
        return planned_seconds(t.duration_seconds, demands, resources)

    def efficiency_note(t, demands):
        planned = dur(t, demands)
        if planned == t.duration_seconds:
            return ()
        efficiency = min(resources[d.resource_id].efficiency_bp for d in demands)
        return (f'efficiency {efficiency}bp: {t.duration_seconds}s planned as {planned}s',)

    def mode(t):
        requested = t.mode
        if requested == 'auto' and s.settings.auto_rule == 'slack':
            # #78 P4: backward only when the capacity-free margin to the latest finish is large enough.
            margin = int((latest[t.id] - earliest_finish[t.id]).total_seconds())
            requested = 'backward' if margin > s.settings.auto_slack_seconds else 'forward'
            auto_notes[t.id] = f'auto by slack: margin {margin}s vs {s.settings.auto_slack_seconds}s -> {requested}'
        elif requested == 'auto':
            requested = ('backward' if t.due and t.due >= start + timedelta(days=s.settings.auto_backward_days)
                         else 'forward')
        return requested

    # Capacity-free latest-start estimates propagate successors' due dates upstream.
    # These are heuristic targets, never substitutes for actual precedence checks.
    latest = {}
    latest_start = {}
    for tid in reversed(topo):
        t = tasks[tid]
        upper = min(t.due or end, end)
        max_start = upper
        for e in outgoing[tid]:
            lag = timedelta(seconds=e.lag_seconds)
            if e.kind == 'FS':
                upper = min(upper, latest_start[e.successor] - lag)
            else:
                max_start = min(max_start, latest_start[e.successor] - lag)
        latest[tid] = upper
        estimates = []
        for alternative in t.alternatives:
            free = bundle_free(alternative, resources, calendars, {}, start, upper, empty_cache)
            segments = select_segments(free, dur(t, alternative.demands), t.splittable, backward=True)
            if segments:
                estimates.append(min(segments[0].start, max_start))
        latest_start[tid] = (t.fixed_start or t.completed_at or t.external_end or max(estimates, default=start))
    # Repair shifts move only the backward placement target; the auto-mode classification keeps `latest`.
    target, target_start = dict(latest), dict(latest_start)
    for tid, seconds in shifts.items():
        if tid in target:
            target[tid] -= timedelta(seconds=seconds)
            target_start[tid] -= timedelta(seconds=seconds)

    # Capacity-free earliest finish, only needed by the slack rule of the automatic mode.
    earliest_start, earliest_finish = {}, {}
    if s.settings.auto_rule == 'slack':
        for tid in topo:
            t = tasks[tid]
            done = t.completed_at or t.external_end
            if done is not None:
                earliest_start[tid] = earliest_finish[tid] = done
                continue
            lower = t.fixed_start or t.release
            for e in incoming[tid]:
                base = earliest_finish[e.predecessor] if e.kind == 'FS' else earliest_start[e.predecessor]
                lower = max(lower, base + timedelta(seconds=e.lag_seconds))
            best = None
            for alternative in t.alternatives:
                free = bundle_free(alternative, resources, calendars, {}, lower, end, empty_cache)
                segments = select_segments(free, dur(t, alternative.demands), t.splittable)
                if segments and (best is None or segments[-1].end < best[1]):
                    best = (segments[0].start, segments[-1].end)
            earliest_start[tid], earliest_finish[tid] = best or (lower, end)

    def search(t, lower, upper, backward=False, exact=None, max_start=None):
        choices = []
        # Different efficiencies give different planned times (#78 P9): then the earliest finish wins,
        # otherwise the historical earliest start (same duration, so nearly the same finish).
        by_finish = not backward and len({dur(t, a.demands) for a in t.alternatives}) > 1
        for alternative in t.alternatives:
            free = bundle_free(alternative, resources, calendars, reservations, lower, upper, free_cache)
            duration = dur(t, alternative.demands)
            if backward and max_start is not None:
                # Try each free interval boundary and the SS latest-start boundary.
                if t.splittable:
                    # First find latest placement; if SS requires earlier start, cap its
                    # first fragment by retrying an upper bound based on working time.
                    candidate = select_segments(free, duration, True, True)
                    if candidate and candidate[0].start > max_start:
                        prefix = tuple(i for i in free if i.start <= max_start)
                        if prefix:
                            from .calendars import intersect
                            possible = select_segments(intersect(free, (Interval(lower, max_start + timedelta(seconds=1)),)), 1, False, True)
                            if possible:
                                # A valid earlier start is preferred over falsely claiming ALAP.
                                earlier = bundle_free(alternative, resources, calendars, reservations, possible[0].start, upper, free_cache)
                                candidate = select_segments(earlier, duration, True)
                    segments = candidate
                else:
                    adjusted = tuple(Interval(i.start, min(i.end, max_start + timedelta(seconds=duration)))
                                     for i in free if i.start <= max_start)
                    segments = select_segments(adjusted, duration, False, True)
            else:
                segments = select_segments(free, duration, t.splittable, backward, exact)
            if segments and (max_start is None or segments[0].start <= max_start):
                key = ((-segments[-1].end.timestamp(), -segments[0].start.timestamp()) if backward
                       else (segments[-1].end.timestamp(), segments[0].start.timestamp()) if by_finish
                       else (segments[0].start.timestamp(), segments[-1].end.timestamp()))
                choices.append((key, tuple(sorted(alternative.demands)), segments))
        if not choices:
            return None
        _, demands, segments = best = min(choices)
        if t.preferred_bundle is not None and min_gain and exact is None:
            preferred = next((c for c in choices if c[1] == t.preferred_bundle), None)
            if preferred is not None and preferred is not best:
                # Gain on the finish: earlier when planning forward, later (closer to the target) backward.
                gain = int(((preferred[2][-1].end - segments[-1].end) if not backward
                            else (segments[-1].end - preferred[2][-1].end)).total_seconds())
                if gain < min_gain:
                    switch_notes[t.id] = f'preferred alternative kept: another saves only {gain}s < {min_gain}s'
                    return preferred[1], preferred[2]
                switch_notes[t.id] = f'left preferred alternative: saves {gain}s >= {min_gain}s'
        return demands, segments

    def active_constraint(t, chosen_mode, lower, limiting, demands, segments):
        if chosen_mode == 'backward':
            return 'constraint=backward'
        refs = sorted(item.split(' from ', 1)[1].split(' + ', 1)[0] for item in limiting)
        bound = f'predecessor:{refs[-1]}' if refs else ('release' if t.release > start else 'horizon')
        if segments[0].start == lower:
            return f'constraint={bound}'
        # Same bundle without any other booking: if it starts at the same instant, only the calendar delayed it.
        from planning_factory.domain.models import Alternative
        free = bundle_free(Alternative(demands), resources, calendars, {}, lower, end, empty_cache)
        alone = select_segments(free, dur(t, demands), t.splittable)
        if alone and alone[0].start == segments[0].start:
            return f'constraint={bound};calendar'
        return 'constraint=capacity'

    # Completed history and pinned work are established before flexible work.
    for tid in topo:
        t = tasks[tid]
        if t.completed_at is not None:
            assigned[tid] = Assignment(tid, t.operation_ids, (), (), t.mode, 'completed', completed_at=t.completed_at)
        elif t.external_end is not None:
            assigned[tid] = Assignment(tid, t.operation_ids, (), (), t.mode, 'external',
                                       external_end=t.external_end,
                                       explanation=('supplier-promised finish, not verified completion',))
        elif t.fixed_start is not None:
            if t.fixed_start < t.release or not start <= t.fixed_start < end:
                raise ValidationError([f'{tid}: fixed start violates release or horizon'])
            found = search(t, t.fixed_start, end, exact=t.fixed_start)
            if not found:
                raise ValidationError([f'{tid}: fixed booking conflicts with calendar, capacity or horizon'])
            demands, segments = found
            assignment = Assignment(tid, t.operation_ids, segments, demands, t.mode, 'fixed', True,
                                    explanation=('preserved fixed start',) + efficiency_note(t, demands)
                                    + (('constraint=fixed',) if record_constraints else ()))
            assigned[tid] = assignment
            reserve(assignment, reservations)

    pending = set(tasks) - set(assigned)

    # Remaining downstream work (#78 P2): longest path of durations and FS lags in the collapsed DAG.
    tail = {}
    if dispatch_policy in ('min_slack', 'critical_ratio', 'atc'):
        for tid in reversed(topo):
            after = [tail[e.successor] + (e.lag_seconds if e.kind == 'FS' else 0) for e in outgoing[tid]]
            tail[tid] = tasks[tid].duration_seconds + max(after, default=0)
        mean_duration = max(1, sum(t.duration_seconds for t in tasks.values()) // max(1, len(tasks)))

    def priority(tid):
        t = tasks[tid]
        days = int(((t.due or end) - start).total_seconds() // 86400)
        score = (t.priority * s.settings.priority_weight + days * s.settings.due_weight
                 + t.customer_priority * s.settings.customer_weight + t.phase_priority)
        if order_rank is not None:
            return (order_rank[tid], tid)
        if dispatch_policy == 'earliest_due':
            return (t.due or end, score, tid)
        if dispatch_policy == 'shortest_processing':
            return (t.duration_seconds, score, t.due or end, tid)
        if dispatch_policy == 'longest_processing':
            return (-t.duration_seconds, score, t.due or end, tid)
        if dispatch_policy in ('min_slack', 'critical_ratio', 'atc'):
            if t.due is None:
                return (1, 0, score, tid)  # no due date: after every dated unit
            available = (t.due - t.release).total_seconds()
            if dispatch_policy == 'min_slack':
                return (0, available - tail[tid], score, tid)
            if dispatch_policy == 'critical_ratio':
                return (0, available / max(1, tail[tid]), score, tid)
            # Apparent Tardiness Cost with k = 2: larger index first.
            import math
            slack = max(0.0, available - tail[tid])
            index = math.exp(-slack / (2 * mean_duration)) / max(1, t.duration_seconds)
            return (0, -index, score, tid)
        return (score, t.due or end, tid)

    # Ready tasks in a heap: priority keys are static and unique (they end with the task id),
    # so popping the minimum picks exactly what min(ready, key=priority) would pick.
    waiting = {tid: sum(1 for e in incoming[tid] if e.predecessor in pending) for tid in pending}
    ready = [(priority(tid), tid) for tid in pending if not waiting[tid]]
    heapq.heapify(ready)
    while pending:
        if not ready:
            raise RuntimeError('validated acyclic graph made no progress')
        _, tid = heapq.heappop(ready)
        pending.remove(tid)
        for e in outgoing[tid]:
            if e.successor in pending:
                waiting[e.successor] -= 1
                if not waiting[e.successor]:
                    heapq.heappush(ready, (priority(e.successor), e.successor))
        dispatched.append(tid)
        if tid in reuse:   # identical to the previous plan: no search needed
            assigned[tid] = reuse[tid]
            reserve(reuse[tid], reservations)
            continue
        t = tasks[tid]
        blocked = [e.predecessor for e in incoming[tid] if e.predecessor in failed]
        if blocked:
            failed[tid] = Unscheduled(tid, t.operation_ids, 'PREDECESSOR_UNSCHEDULED', ', '.join(sorted(set(blocked))))
            continue
        lower = t.release
        limiting = []
        for e in incoming[tid]:
            pred = assigned[e.predecessor]
            bound = (pred.end if e.kind == 'FS' else pred.start) + timedelta(seconds=e.lag_seconds)
            if bound > lower:
                lower = bound
                limiting = [f'{e.kind} from {e.predecessor} + {e.lag_seconds}s']
            elif bound == lower:
                limiting.append(f'{e.kind} from {e.predecessor} + {e.lag_seconds}s')
        upper = end
        max_start = None
        for e in outgoing[tid]:
            if e.successor in assigned:
                bound = assigned[e.successor].start - timedelta(seconds=e.lag_seconds)
                if e.kind == 'FS':
                    upper = min(upper, bound)
                else:
                    max_start = min(max_start or bound, bound)
        chosen_mode = mode(t)
        explanation = ['release/horizon lower bound', *limiting]
        if tid in auto_notes:
            explanation.append(auto_notes.pop(tid))
        found = None
        if chosen_mode == 'backward':
            found = search(t, lower, min(upper, target[tid]), True,
                           max_start=min(max_start or end, target_start[tid]))
            if not found:
                explanation.append('backward target infeasible: fell back to earliest feasible slot')
                chosen_mode = 'forward'
        if not found:
            found = search(t, lower, upper, max_start=max_start)
        if not found:
            failed[tid] = Unscheduled(tid, t.operation_ids, 'NO_FEASIBLE_SLOT',
                                      'No resource bundle fits release, calendar, capacity, fixed successors and horizon')
            continue
        demands, segments = found
        if segments[0].start > lower:
            explanation.append('start shifted by resource availability/capacity')
        if tid in switch_notes:
            explanation.append(switch_notes.pop(tid))
        explanation.extend(efficiency_note(t, demands))
        if record_constraints:
            explanation.append(active_constraint(t, chosen_mode, lower, limiting, demands, segments))
        a = Assignment(tid, t.operation_ids, segments, demands, t.mode, chosen_mode,
                       explanation=tuple(explanation))
        assigned[tid] = a
        reserve(a, reservations)

    # Backward targets that a successor missed: the earlier backward ancestors must aim earlier.
    wanted = {}
    for tid, a in assigned.items():
        shortfall = int((a.end - target[tid]).total_seconds()) if tid in target and a.segments else 0
        if shortfall <= 0 or not any('backward target infeasible' in x for x in a.explanation):
            continue
        stack, seen = [tid], set()
        while stack:
            for e in incoming[stack.pop()]:
                if e.predecessor not in seen:
                    seen.add(e.predecessor)
                    stack.append(e.predecessor)
                    if assigned[e.predecessor].effective_mode == 'backward':
                        wanted[e.predecessor] = max(wanted.get(e.predecessor, 0), shortfall)
    report['shifts'] = wanted
    report.update(order=tuple(dispatched), tasks=tasks, edges=frozenset(edges))

    # Fixed predecessors may have been booked after their fixed successors. Refuse
    # inconsistent pinned/history input rather than returning an invalid plan.
    for e in edges:
        pred, succ = assigned.get(e.predecessor), assigned.get(e.successor)
        if pred and succ:
            bound = (pred.end if e.kind == 'FS' else pred.start) + timedelta(seconds=e.lag_seconds)
            if succ.start < bound:
                raise ValidationError([f'fixed/history precedence conflict: {e.predecessor} -> {e.successor}'])
        elif succ and (succ.fixed or succ.completed_at is not None or succ.external_end is not None):
            raise ValidationError([f'pinned/history task {e.successor} has an unscheduled predecessor'])

    loads = []
    for r in sorted(s.resources, key=lambda r: r.id):
        available = sum(i.seconds for i in calendars[r.calendar_id])
        available_units = available * r.capacity if r.capacity is not None else None
        used = sum(i.seconds * units for i, units in reservations.get(r.id, ()))
        loads.append(ResourceLoad(r.id, available_units, used,
                                  used / available_units if available_units else None))
    per_operation = {oid: a for a in assigned.values() for oid in a.operation_ids}
    order_results = []
    for order_id in sorted({o.order_id for o in s.operations}):
        operations = [o for o in s.operations if o.order_id == order_id]
        complete = all(o.id in per_operation for o in operations)
        finish = max((per_operation[o.id].end for o in operations if o.id in per_operation), default=None)
        due = min((utc(o.due) for o in operations if o.due), default=None)
        # No misleading partial delivery date when any phase could not be booked.
        order_results.append(OrderResult(order_id, finish if complete else None, due,
                                         max(0, int((finish - due).total_seconds())) if complete and due and finish else None,
                                         complete))
    from planning_factory.adapters.json_io import scenario_digest
    result = Plan(s.id, scenario_digest(s), tuple(assigned[k] for k in sorted(assigned)),
                  tuple(failed[k] for k in sorted(failed)), tuple(loads), tuple(order_results))
    from .verification import verify_plan
    errors = verify_plan(s, result)
    if errors:
        raise RuntimeError('Internal plan verification failed: ' + '; '.join(errors))
    return result
