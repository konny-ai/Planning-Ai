"""Time fence (#78 P1): units of the published plan that start inside the window stay put.

Works across scenario revisions: the published plan belongs to the previous scenario,
so units are matched by their operation ids, never by position. A frozen unit keeps its
start and its resource bundle (``fixed_start`` + ``fixed_alternative``); a nest is frozen
whole. Units that cannot be frozen are reported with the reason, never silently moved
or dropped. ``check_fence`` audits a new plan against the frozen units.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from planning_factory.domain.models import Plan, Scenario
from planning_factory.domain.validation import ValidationError, compile_tasks
from .calendars import expand


@dataclass(frozen=True)
class FenceResult:
    scenario: Scenario
    until: datetime
    frozen: tuple[dict, ...]
    skipped: tuple[dict, ...]

    def to_dict(self) -> dict:
        return {'until': self.until.isoformat().replace('+00:00', 'Z'), 'frozen': list(self.frozen),
                'skipped': list(self.skipped)}


def working_until(s: Scenario, calendar_id: str, start: datetime, hours: float) -> datetime:
    """Instant reached after `hours` of working time on one calendar from `start`."""
    calendar = next((c for c in s.calendars if c.id == calendar_id), None)
    if calendar is None:
        raise ValidationError([f'unknown calendar {calendar_id}'])
    if hours <= 0:
        raise ValidationError(['time fence hours must be positive'])
    remaining = timedelta(hours=hours)
    for interval in expand(calendar, s.start, s.end):
        if interval.end <= start:
            continue
        begin = max(interval.start, start)
        if interval.end - begin >= remaining:
            return begin + remaining
        remaining -= interval.end - begin
    return s.end


def freeze_window(s: Scenario, published: Plan, until: datetime) -> FenceResult:
    tasks, _, _ = compile_tasks(s)
    ops = {o.id: o for o in s.operations}
    unit_of = {op: tid for tid, t in tasks.items() for op in t.operation_ids}
    frozen, skipped, pins = [], [], {}
    for a in sorted(published.assignments, key=lambda x: (x.start or s.start, x.task_id)):
        if not a.segments or not a.start < until:
            continue
        entry = {'unit': a.task_id, 'operations': list(a.operation_ids),
                 'start': a.start.isoformat().replace('+00:00', 'Z')}
        missing = [o for o in a.operation_ids if o not in ops]
        if missing:
            skipped.append({**entry, 'reason': f'operations no longer in the scenario: {", ".join(missing)}'})
            continue
        units = {unit_of[o] for o in a.operation_ids}
        if len(units) != 1 or set(tasks[units.pop()].operation_ids) != set(a.operation_ids):
            skipped.append({**entry, 'reason': 'unit composition changed (nesting edited)'})
            continue
        if any(ops[o].completed_at is not None or ops[o].external_end is not None for o in a.operation_ids):
            skipped.append({**entry, 'reason': 'completed or external in the new scenario'})
            continue
        if a.start < s.start:
            skipped.append({**entry, 'reason': 'started before the new horizon: report it as progress'})
            continue
        bundle = tuple(sorted(a.demands))
        indexes = {}
        for o in a.operation_ids:
            index = next((i for i, alt in enumerate(ops[o].alternatives) if tuple(sorted(alt.demands)) == bundle), None)
            if index is None:
                break
            indexes[o] = index
        if len(indexes) != len(a.operation_ids):
            skipped.append({**entry, 'reason': 'its resource bundle is no longer an alternative'})
            continue
        for o, index in indexes.items():
            pins[o] = (a.start, index)
        frozen.append({**entry, 'resources': [d.resource_id for d in bundle]})
    operations = tuple(replace(o, fixed_start=pins[o.id][0], fixed_alternative=pins[o.id][1]) if o.id in pins else o
                       for o in s.operations)
    return FenceResult(replace(s, operations=operations), until, tuple(frozen), tuple(skipped))


def check_fence(plan: Plan, fence: FenceResult) -> list[str]:
    """Every frozen unit must keep start and resources in the new plan."""
    by_ops = {tuple(a.operation_ids): a for a in plan.assignments}
    errors = []
    for unit in fence.frozen:
        a = by_ops.get(tuple(unit['operations']))
        if a is None:
            errors.append(f'{unit["unit"]}: frozen unit not scheduled')
        elif a.start.isoformat().replace('+00:00', 'Z') != unit['start'] or \
                [d.resource_id for d in sorted(a.demands)] != unit['resources']:
            errors.append(f'{unit["unit"]}: frozen unit moved')
    return errors
