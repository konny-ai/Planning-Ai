"""MS Project export (#89): a verified plan as MSPDI XML, read-only towards the plan.

Tasks are *manually scheduled* so Project keeps the dates computed and audited here
instead of rescheduling them with its own engine. Structure: one summary per order,
one task per plan unit (operation or atomic nest); nests spanning several orders sit
under their own summary; unscheduled units are listed and flagged, never dated as if
planned. Interrupted units are exported from first start to last end with the booked
work as duration and the fragments in the notes (no timephased splits).
"""
from datetime import datetime
from xml.etree import ElementTree
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from planning_factory.domain.models import Plan, Scenario
from planning_factory.domain.validation import ValidationError, compile_tasks
from .json_io import scenario_digest

NS = 'http://schemas.microsoft.com/project'
LINK_TYPES = {'FS': '1', 'SS': '3'}
ELAPSED_MINUTES = '4'  # MSPDI duration format "em": our lags are elapsed time, not working time


def _duration(seconds: int) -> str:
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f'PT{hours}H{minutes}M{secs}S'


def mspdi(scenario: Scenario, plan: Plan, timezone: str = 'Europe/Rome', title: str | None = None) -> str:
    if plan.scenario_digest != scenario_digest(scenario):
        raise ValidationError(['plan does not belong to this scenario (digest mismatch)'])
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError([f'unknown IANA timezone {timezone!r}']) from None

    def local(value: datetime) -> str:
        return value.astimezone(zone).replace(tzinfo=None).isoformat(timespec='seconds')

    tasks, edges, _ = compile_tasks(scenario)
    order_of = {o.id: o.order_id for o in scenario.operations}
    assigned = {a.task_id: a for a in plan.assignments}
    unscheduled = {u.task_id: u for u in plan.unscheduled}
    groups: dict[str, list[str]] = {}
    for tid, task in tasks.items():
        orders = sorted({order_of[o] for o in task.operation_ids})
        if tid in unscheduled:
            key = '~Non pianificate'
        elif len(orders) > 1:
            key = '~Nesting multicommessa'
        else:
            key = orders[0]
        groups.setdefault(key, []).append(tid)

    root = ElementTree.Element('Project', xmlns=NS)

    def sub(parent, tag, text=None):
        element = ElementTree.SubElement(parent, tag)
        if text is not None:
            element.text = str(text)
        return element

    sub(root, 'Name', title or scenario.id)
    sub(root, 'Title', title or scenario.id)
    sub(root, 'ScheduleFromStart', 1)
    sub(root, 'StartDate', local(scenario.start))
    sub(root, 'FinishDate', local(scenario.end))
    sub(root, 'CalendarUID', 1)
    calendars = sub(root, 'Calendars')
    calendar = sub(calendars, 'Calendar')
    for tag, value in (('UID', 1), ('Name', 'Standard'), ('IsBaseCalendar', 1)):
        sub(calendar, tag, value)
    entries, uid_of = [], {}

    def span(tid):
        a = assigned.get(tid)
        return (a.start, a.end) if a is not None else None

    for outline, group in enumerate(sorted(groups), 1):
        members = sorted(groups[group], key=lambda t: (span(t) or (scenario.end, scenario.end), t))
        spans = [span(t) for t in members if span(t)]
        summary = {'Name': group.lstrip('~') if group.startswith('~') else f'Commessa {group}',
                   'OutlineNumber': outline, 'OutlineLevel': 1,
                   'Start': local(min((s for s, _ in spans), default=scenario.start)),
                   'Finish': local(max((e for _, e in spans), default=scenario.start)), 'Summary': 1}
        notes = ['Unità senza collocazione nell\'orizzonte: le date mostrate non sono pianificate.'] \
            if group == '~Non pianificate' else []
        entries.append((summary, notes, []))
        for child, tid in enumerate(members, 1):
            uid_of[tid] = len(entries) + 1
            t, a = tasks[tid], assigned.get(tid)
            fields = {'Name': ' + '.join(t.operation_ids) if a else '[NON PIANIFICATA] ' + ' + '.join(t.operation_ids),
                      'OutlineNumber': f'{outline}.{child}', 'OutlineLevel': 2}
            notes = [f'Unità di piano: {tid}', f'Commesse: {", ".join(sorted({order_of[o] for o in t.operation_ids}))}']
            if a is None:
                u = unscheduled[tid]
                fields.update(Start=local(scenario.start), Finish=local(scenario.start), Duration=_duration(0),
                              Milestone=0, Summary=0)
                notes.append(f'Non pianificata: {u.code} {u.detail}')
            else:
                fields.update(Start=local(a.start), Finish=local(a.end), Duration=_duration(sum(s.seconds for s in a.segments)),
                              Milestone=0 if a.segments else 1, Summary=0)
                if a.completed_at is not None:
                    fields['PercentComplete'] = 100
                    notes.append('Storico: fase già chiusa')
                elif a.external_end is not None:
                    notes.append('Fine dichiarata dal fornitore di conto lavoro')
                if a.fixed:
                    notes.append('Inizio e risorse fissati')
                if len(a.segments) > 1:
                    notes.append('Interrotta: ' + '; '.join(f'{local(s.start)} → {local(s.end)}' for s in a.segments))
                notes += list(a.explanation)
            entries.append((fields, notes, []))
    for e in edges:
        if e.predecessor in uid_of and e.successor in uid_of:
            lag_tenths, rest = divmod(e.lag_seconds, 6)  # MSPDI LinkLag is in tenths of a minute
            _, notes, links = entries[uid_of[e.successor] - 1]
            links.append((uid_of[e.predecessor], LINK_TYPES[e.kind], lag_tenths))
            if rest:
                notes.append(f'Ritardo da {e.predecessor} arrotondato per difetto: {e.lag_seconds}s → {lag_tenths * 6}s')
    xml_tasks = sub(root, 'Tasks')
    for uid, (fields, notes, links) in enumerate(entries, 1):
        task = sub(xml_tasks, 'Task')
        sub(task, 'UID', uid)
        sub(task, 'ID', uid)
        # MSPDI requires the schema's element order.
        for tag in ('Name', 'OutlineNumber', 'OutlineLevel', 'Start', 'Finish', 'Duration', 'Milestone', 'Summary',
                    'PercentComplete'):
            if tag in fields:
                sub(task, tag, fields[tag])
        if notes:
            sub(task, 'Notes', '\n'.join(notes))
        for predecessor, kind, lag in links:
            link = sub(task, 'PredecessorLink')
            sub(link, 'PredecessorUID', predecessor)
            sub(link, 'Type', kind)
            sub(link, 'LinkLag', lag)
            sub(link, 'LagFormat', ELAPSED_MINUTES)
        sub(task, 'Active', 1)
        sub(task, 'Manual', 1)
    resources = sorted({d.resource_id for a in plan.assignments for d in a.demands})
    resource_uid = {r: i for i, r in enumerate(resources, 1)}
    xml_resources = sub(root, 'Resources')
    for r in resources:
        res = sub(xml_resources, 'Resource')
        sub(res, 'UID', resource_uid[r])
        sub(res, 'ID', resource_uid[r])
        sub(res, 'Name', r)
        sub(res, 'Type', 1)
    xml_assignments = sub(root, 'Assignments')
    uid = 1
    for a in sorted(plan.assignments, key=lambda x: x.task_id):
        for d in sorted(a.demands):
            assignment = sub(xml_assignments, 'Assignment')
            sub(assignment, 'UID', uid)
            sub(assignment, 'TaskUID', uid_of[a.task_id])
            sub(assignment, 'ResourceUID', resource_uid[d.resource_id])
            sub(assignment, 'Units', d.units)
            uid += 1
    ElementTree.indent(root)
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n' + ElementTree.tostring(root, encoding='unicode') + '\n'
