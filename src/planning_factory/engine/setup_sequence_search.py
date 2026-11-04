"""Bounded opt-in machine-order improvement for ADR 003 setup/run plans.

Never assumes unapproved business weights: caller selects the objective.
All candidates are independently audited by the two-stage planner; no
production database, GUI, or legacy equivalence is involved.
"""
import heapq
from dataclasses import dataclass, replace
from datetime import UTC
from planning_factory.domain.models import Scenario
from planning_factory.domain.setup_contract import SetupContract
from planning_factory.domain.validation import ValidationError, compile_tasks
from .setup_stage_scheduler import StagedSetupResult, schedule_setup_stages


@dataclass(frozen=True)
class SequenceMetrics:
    late_orders: int
    tardiness_seconds: int
    setup_seconds: int
    makespan_seconds: int

    def as_dict(self):
        return dict(late_orders=self.late_orders,
                    tardiness_seconds=self.tardiness_seconds,
                    setup_seconds=self.setup_seconds,
                    makespan_seconds=self.makespan_seconds)


@dataclass(frozen=True)
class SequenceSearch:
    objective: str
    winner: StagedSetupResult
    sequence: tuple[str, ...]
    baseline: SequenceMetrics
    selected: SequenceMetrics
    evaluated: int
    rejected: int
    improved: bool


def sequence_metrics(s: Scenario, solution: StagedSetupResult) -> SequenceMetrics:
    """Compute KPIs from ORIGINAL orders; a nesting contributes to all owners."""
    tasks, _, _ = compile_tasks(s)
    owner = {o.id: o for o in s.operations}
    ends = {}
    due = {}
    for b in solution.bookings:
        if b.stage != 'run':
            continue
        for oid in tasks[b.phase_id].operation_ids:
            order = owner[oid].order_id
            end = b.segments[-1].end.astimezone(UTC)
            if order not in ends or ends[order] < end:
                ends[order] = end
            item_due = owner[oid].due
            if item_due is not None:
                item_due = item_due.astimezone(UTC)
                if order not in due or item_due < due[order]:
                    due[order] = item_due
    for tid, task in tasks.items():
        stated = task.completed_at if task.completed_at is not None else task.external_end
        if stated is None:
            continue  # active task: end comes from its run booking
        done_at = stated.astimezone(UTC)
        for oid in task.operation_ids:
            order = owner[oid].order_id
            if order not in ends or ends[order] < done_at:
                ends[order] = done_at
            item_due = owner[oid].due
            if item_due is not None:
                item_due = item_due.astimezone(UTC)
                if order not in due or item_due < due[order]:
                    due[order] = item_due
    incomplete = {o.order_id for o in s.operations} - set(ends)
    if incomplete:
        raise ValidationError([f'incomplete orders in staged KPI: {sorted(incomplete)}'])
    tardy = [max(0, int((end - due[order]).total_seconds()))
             for order, end in ends.items() if order in due]
    setups = sum(sum(i.seconds for i in b.segments)
                 for b in solution.bookings if b.stage == 'setup')
    makespan = (int((max(ends.values()) - s.start.astimezone(UTC)).total_seconds())
                if ends else 0)
    return SequenceMetrics(sum(x > 0 for x in tardy), sum(tardy), setups, makespan)


def _score(m: SequenceMetrics, objective: str):
    if objective == 'delivery':
        return (m.late_orders, m.tardiness_seconds, m.setup_seconds, m.makespan_seconds)
    if objective == 'setup':
        return (m.setup_seconds, m.late_orders, m.tardiness_seconds, m.makespan_seconds)
    if objective == 'flow':
        return (m.makespan_seconds, m.late_orders, m.tardiness_seconds, m.setup_seconds)
    raise ValueError('objective must be explicitly chosen: delivery, setup or flow')


def default_machine_sequence(s: Scenario) -> tuple[str, ...]:
    """Deterministic starting order: earliest due date first, precedence-safe.

    Kahn topological order over the compiled task graph; among ready tasks the
    key is (active, has no due, due, release, priority, id). It is a neutral, explicit
    heuristic that invents no business weights; the caller may still pass any
    other sequence. Feasibility is still decided by the staged scheduler audit.
    """
    tasks, edges, _ = compile_tasks(s)
    remaining = {tid: 0 for tid in tasks}
    successors = {tid: [] for tid in tasks}
    for edge in edges:
        remaining[edge.successor] += 1
        successors[edge.predecessor].append(edge.successor)

    def key(tid):
        t = tasks[tid]
        return (t.completed_at is None, t.due is None, t.due.timestamp() if t.due is not None else 0,
                t.release.timestamp() if t.release is not None else 0,
                t.priority, tid)

    # Supplier milestones are not machine work: they only gate successors.
    for tid in [x for x, task in tasks.items() if task.external_end is not None]:
        for nxt in successors[tid]:
            remaining[nxt] -= 1
        del remaining[tid]
    ready = [(key(tid), tid) for tid, n in remaining.items() if n == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        _, tid = heapq.heappop(ready)
        order.append(tid)
        for nxt in successors[tid]:
            remaining[nxt] -= 1
            if remaining[nxt] == 0:
                heapq.heappush(ready, (key(nxt), nxt))
    if len(order) != len(remaining):
        raise ValidationError(['precedence cycle among compiled tasks'])
    return tuple(order)


def state_grouped_sequence(s: Scenario, contract: SetupContract,
                           base: tuple[str, ...] | None = None) -> tuple[str, ...]:
    """Optional starting order that groups equal target states per machine.

    Keeps the relative order of `base` (default: due-date order) across
    machines and, on each machine, puts jobs whose target state equals the
    machine's declared initial state first, then the other states by first
    appearance. Completed history stays first. A neutral heuristic to cut
    changeovers; it may violate precedences, in which case the staged
    scheduler rejects it and the caller falls back to the base order.
    """
    base = base if base is not None else default_machine_sequence(s)
    tasks, _, _ = compile_tasks(s)
    phases = {p.id: p for p in contract.phases}
    initial = dict(contract.initial_machine_states)
    per_machine = {}
    for tid in base:
        per_machine.setdefault(phases[tid].machine_id, []).append(tid)
    reordered = {}
    for mid, jobs in per_machine.items():
        done = [j for j in jobs if tasks[j].completed_at is not None]
        active = [j for j in jobs if tasks[j].completed_at is None]
        order = []
        for j in active:
            state = phases[j].target_state
            if state not in order:
                order.append(state)
        if initial.get(mid) in order:
            order.remove(initial[mid])
            order.insert(0, initial[mid])
        rank = {state: i for i, state in enumerate(order)}
        reordered[mid] = done + sorted(active, key=lambda j: rank[phases[j].target_state])
    cursor = {mid: iter(jobs) for mid, jobs in reordered.items()}
    return tuple(next(cursor[phases[tid].machine_id]) for tid in base)


def best_start_sequence(s: Scenario, contract: SetupContract, *, objective: str,
                        max_alternatives: int = 32) -> tuple[str, ...]:
    """Pick the better feasible start between due-date and state-grouped order.

    Ties keep the due-date order. The due-date order must be feasible (as in
    optimize_setup_sequence); an infeasible grouped order is simply skipped.
    """
    edd = default_machine_sequence(s)
    best = edd
    best_score = _score(sequence_metrics(s, schedule_setup_stages(
        s, contract, edd, max_alternatives=max_alternatives)), objective)
    grouped = state_grouped_sequence(s, contract, edd)
    if grouped != edd:
        try:
            score = _score(sequence_metrics(s, schedule_setup_stages(
                s, contract, grouped, max_alternatives=max_alternatives)), objective)
        except ValidationError:
            return best
        if score < best_score:
            best = grouped
    return best


def _neighbours(sequence, machine_by_task, neighbourhood='swap'):
    """Moves on each machine, including noncontiguous global positions.

    'swap' exchanges adjacent jobs; 'insertion' additionally relocates a job
    to any non-adjacent position on the same machine.
    """
    for mid in sorted(set(machine_by_task.values())):
        positions = [i for i, tid in enumerate(sequence) if machine_by_task[tid] == mid]
        for a, b in zip(positions, positions[1:]):
            candidate = list(sequence)
            candidate[a], candidate[b] = candidate[b], candidate[a]
            yield tuple(candidate)
        if neighbourhood != 'insertion':
            continue
        jobs = [sequence[i] for i in positions]
        for i in range(len(jobs)):
            for j in range(len(jobs)):
                if abs(i - j) < 2:
                    continue
                moved = list(jobs)
                moved.insert(j, moved.pop(i))
                candidate = list(sequence)
                for pos, tid in zip(positions, moved):
                    candidate[pos] = tid
                yield tuple(candidate)


def optimize_setup_sequence(s: Scenario, contract: SetupContract,
                            initial_sequence: tuple[str, ...] | None = None, *,
                            objective: str, max_evaluations: int = 24,
                            max_alternatives: int = 32,
                            neighbourhood: str = 'swap') -> SequenceSearch:
    """Bounded best-improvement search; no claim of global mathematical optimum.

    The initial sequence must be feasible. Every candidate is independently
    validated; impossible precedence or capacity configurations are rejected.
    Reports both baseline and selected KPIs, preserving baseline on ties.
    """
    _score(SequenceMetrics(0, 0, 0, 0), objective)
    if neighbourhood not in ('swap', 'insertion'):
        raise ValueError("neighbourhood must be 'swap' or 'insertion'")
    if initial_sequence is None:
        initial_sequence = default_machine_sequence(s)
    if type(max_evaluations) is not int or not 1 <= max_evaluations <= 256:
        raise ValueError('max_evaluations must be integer 1..256')
    tasks, _, _ = compile_tasks(s)
    tasks = {k: v for k, v in tasks.items() if v.external_end is None}
    if len(initial_sequence) != len(tasks) or set(initial_sequence) != set(tasks):
        raise ValidationError(['initial machine sequence must contain each compiled task once'])
    machine_by_task = {p.id: p.machine_id for p in contract.phases}
    if set(machine_by_task) != set(tasks):
        raise ValidationError(['setup contract phases differ from run tasks'])
    winner = schedule_setup_stages(s, contract, initial_sequence,
                                   max_alternatives=max_alternatives)
    initial = sequence_metrics(s, winner)
    best = initial
    current = initial_sequence
    evaluated = 1
    rejected = 0
    seen = {initial_sequence}
    # Explore neighbours of the current best, but never repeat rejected cases.
    while evaluated < max_evaluations:
        improved = False
        for candidate in _neighbours(current, machine_by_task, neighbourhood):
            if evaluated >= max_evaluations:
                break
            if candidate in seen:
                continue
            seen.add(candidate)
            evaluated += 1
            try:
                result = schedule_setup_stages(s, contract, candidate,
                                               max_alternatives=max_alternatives)
            except ValidationError:
                rejected += 1
                continue
            metrics = sequence_metrics(s, result)
            if _score(metrics, objective) < _score(best, objective):
                winner, best, current = result, metrics, candidate
                improved = True
        if not improved:
            break
    return SequenceSearch(objective, winner, current, initial, best,
                          evaluated, rejected, current != initial_sequence)


@dataclass(frozen=True)
class JustInTimeResult:
    solution: StagedSetupResult
    scenario: Scenario          # source scenario with the derived releases
    baseline: SequenceMetrics
    selected: SequenceMetrics
    delayed: tuple[tuple[str, int], ...]   # (phase id, seconds of delay)
    rejected: tuple[str, ...]


def just_in_time_tail(s: Scenario, contract: SetupContract,
                      machine_sequence: tuple[str, ...], *,
                      max_alternatives: int = 32) -> JustInTimeResult:
    """EXPERIMENTAL, opt-in, not an approved rule (see ADR 005, option B-like).

    Delays the production run of each *terminal* task (last on its machine and
    without successors) by its delivery slack, keeping its setup as early as
    the resources allow. A candidate is kept only if the whole staged plan is
    re-planned and audited, every OTHER phase's bookings are unchanged and the
    KPIs do not get worse; otherwise it is reported as rejected. The delay is
    expressed as a derived release in the returned scenario, never applied to
    the caller's input.
    """
    tasks, edges, _ = compile_tasks(s)
    base = schedule_setup_stages(s, contract, machine_sequence,
                                 max_alternatives=max_alternatives)
    baseline = sequence_metrics(s, base)
    has_successor = {e.predecessor for e in edges}
    phases = {p.id: p for p in contract.phases}
    last_on_machine = {}
    for pid in machine_sequence:
        if tasks[pid].completed_at is None:
            last_on_machine[phases[pid].machine_id] = pid
    current_s, current, metrics = s, base, baseline
    delayed, rejected = [], []
    for pid in sorted(last_on_machine.values()):
        task = tasks[pid]
        if pid in has_successor or task.due is None:
            continue
        run = next(b for b in current.bookings if b.phase_id == pid and b.stage == 'run')
        slack = int((task.due - run.segments[-1].end).total_seconds())
        if slack <= 0:
            continue
        members = set(task.operation_ids)
        accepted = None
        shift = task.due - run.segments[-1].end
        # Wall-clock slack ignores calendars: when the full delay is infeasible
        # (non-working days, horizon end) try progressively smaller delays.
        for _ in range(6):
            new_release = run.segments[0].start + shift
            candidate_s = replace(current_s, operations=tuple(
                replace(o, release=new_release) if o.id in members else o
                for o in current_s.operations))
            try:
                candidate = schedule_setup_stages(candidate_s, contract, machine_sequence,
                                                  max_alternatives=max_alternatives)
            except ValidationError:
                shift = shift / 2
                continue
            others_same = sorted((x for x in candidate.bookings if x.phase_id != pid),
                                 key=lambda x: (x.phase_id, x.stage)) == \
                sorted((x for x in current.bookings if x.phase_id != pid),
                       key=lambda x: (x.phase_id, x.stage))
            new_metrics = sequence_metrics(candidate_s, candidate)
            no_worse = (new_metrics.late_orders <= metrics.late_orders and
                        new_metrics.tardiness_seconds <= metrics.tardiness_seconds and
                        new_metrics.setup_seconds <= metrics.setup_seconds)
            if others_same and no_worse:
                accepted = (candidate_s, candidate, new_metrics, int(shift.total_seconds()))
                break
            shift = shift / 2
        if accepted:
            current_s, current, metrics, applied = accepted
            delayed.append((pid, applied))
        else:
            rejected.append(pid)
    return JustInTimeResult(current, current_s, baseline, metrics,
                            tuple(delayed), tuple(rejected))
