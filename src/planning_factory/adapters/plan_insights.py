"""Read-only analyses of a verified plan for the Control Room and its assistant.

Pure functions over a Scenario and its audited Plan: nothing is written and
nothing is inferred beyond the plan itself, the calendars of the engine and the
independent constraint chain (#88). Days are UTC days, as in the load view.
"""
from datetime import UTC, date, datetime, timedelta

from planning_factory.domain.models import Plan, Scenario
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.calendars import expand

MAX_ROWS = 100
MAX_DAYS = 62
DAY = timedelta(days=1)


def _text(value):
    return value.isoformat().replace('+00:00', 'Z') if value is not None else None


def _hours(seconds):
    return round(seconds / 3600, 2)


def _identifier(value, label):
    if not isinstance(value, str) or not 1 <= len(value) <= 256:
        raise ValidationError([f'{label}: identifier of 1..256 characters required'])
    return value


def _day(value, label):
    if not isinstance(value, str):
        raise ValidationError([f'{label}: date YYYY-MM-DD required'])
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError([f'{label}: date YYYY-MM-DD required']) from None


def _midnight(day):
    return datetime(day.year, day.month, day.day, tzinfo=UTC)


def _overlap_by_day(start, end, first, days):
    """Seconds of [start, end) falling in each of ``days`` UTC days from ``first``."""
    origin = _midnight(first)
    lo, hi = max(start, origin), min(end, origin + days * DAY)
    while lo < hi:
        index = (lo - origin) // DAY
        boundary = origin + (index + 1) * DAY
        yield index, int((min(hi, boundary) - lo).total_seconds())
        lo = boundary


BACKWARD_FALLBACK = 'backward target infeasible'
BACKWARD_FALLBACK_TEXT = ("il motore segnala che era pianificata a ritroso ma entro quel limite non ha trovato "
                          "posto: ha ripiegato sul primo spazio libero")


def _cause_text(step):
    cause, ref, resource = step.get('cause'), step.get('ref'), step.get('resource')
    if cause == 'predecessor':
        return f'attende la fase precedente {ref}'
    if cause == 'resource':
        return (f'attende la risorsa {resource}, occupata da {ref}' if ref else
                'attende capacità libera sulle proprie risorse')
    if cause == 'material':
        return 'attende la disponibilità del materiale'
    if cause == 'release':
        return 'non può iniziare prima della data di rilascio'
    if cause == 'horizon':
        return "parte all'inizio dell'orizzonte di pianificazione"
    if cause == 'fixed':
        return 'inizio fissato in modo esplicito'
    if cause == 'backward':
        return 'pianificata a ritroso verso consegna o fasi successive'
    if cause == 'completed':
        return 'già completata'
    if cause == 'external':
        return 'lavorazione esterna con data fornitore'
    return 'causa non determinata'


class PlanInsights:
    """Analyses over one verified (Scenario, Plan) pair. Heavy parts are computed once."""

    def __init__(self, scenario: Scenario, plan: Plan, attributes=None, *, capacity_scale=1):
        if plan.scenario_digest is None:
            raise ValidationError(['plan without scenario digest'])
        self.scenario, self.plan = scenario, plan
        if type(capacity_scale) is not int or capacity_scale < 1:
            raise ValidationError(['positive capacity scale required'])
        self.capacity_scale = capacity_scale
        # Canonical sheet attributes the engine Scenario does not carry: {'operations': {id: {...}}, 'nests': {id: {...}}}
        # with ``material`` and ``thickness_mm`` as the source gives them. Absent means the source does not say.
        self.attributes = attributes or {}
        self.order_of = {o.id: o.order_id for o in scenario.operations}
        self.resources = {r.id: r for r in scenario.resources}
        self.loads = {r.resource_id: r for r in plan.resources}
        self.orders = {o.order_id: o for o in plan.orders}
        self._chain = None
        self._calendars = {}

    def _base(self):
        return {'scenario_digest': self.plan.scenario_digest, 'read_only': True, 'source_only': True,
                'production_approved': False}

    def _orders_of(self, operation_ids):
        return sorted({self.order_of[o] for o in operation_ids if o in self.order_of})

    def _sheet_attributes(self, task_id, operation_ids):
        """Material and thickness (mm) of a task: its nesting program, else its operations (one value, or a list)."""
        found = {}
        if task_id.startswith('nest:'):
            found = self.attributes.get('nests', {}).get(task_id[5:], {})
        if not found:
            operations = self.attributes.get('operations', {})
            for field in ('material', 'thickness_mm'):
                values = sorted({operations[o][field] for o in operation_ids if operations.get(o, {}).get(field)},
                                key=lambda v: (len(str(v)), str(v)))
                if values:
                    found[field] = values[0] if len(values) == 1 else values
        return {k: found[k] for k in ('material', 'thickness_mm') if found.get(k)}

    def _assignment_row(self, a):
        return {'task_id': a.task_id, 'operations': list(a.operation_ids)[:20],
                'orders': self._orders_of(a.operation_ids)[:20], 'start': _text(a.start), 'end': _text(a.end),
                'resources': [d.resource_id for d in a.demands], 'fixed': a.fixed,
                'mode': a.effective_mode, **self._sheet_attributes(a.task_id, a.operation_ids)}

    def chain(self):
        if self._chain is None:
            from planning_factory.engine.constraint_chain import constraint_chain
            self._chain = constraint_chain(self.scenario, self.plan)
        return self._chain

    def _require_order(self, order_id):
        _identifier(order_id, 'order_id')
        if order_id not in self.orders:
            raise ValidationError([f'order not found in this plan: {order_id!r}'])
        return self.orders[order_id]

    def order_detail(self, order_id):
        result = self._require_order(order_id)
        mine = [a for a in self.plan.assignments if order_id in self._orders_of(a.operation_ids)]
        mine.sort(key=lambda a: (a.start or self.scenario.start, a.task_id))
        unscheduled = [{'task_id': u.task_id, 'code': u.code, 'detail': u.detail[:300]}
                       for u in self.plan.unscheduled if order_id in self._orders_of(u.operation_ids)]
        return {**self._base(), 'order_id': order_id, 'due': _text(result.due), 'end': _text(result.end),
                'tardiness_seconds': result.tardiness_seconds, 'tardiness_hours': _hours(result.tardiness_seconds or 0),
                'complete': result.complete, 'operations': sum(1 for o in self.order_of.values() if o == order_id),
                'tasks': [self._assignment_row(a) for a in mine[:MAX_ROWS]], 'truncated': len(mine) > MAX_ROWS,
                'unscheduled': unscheduled[:MAX_ROWS]}

    def explain_order(self, order_id):
        result = self._require_order(order_id)
        chain = self.chain()
        entry = chain['orders'].get(order_id)
        assigned = {a.task_id: a for a in self.plan.assignments}
        path = []
        for step in (entry or {}).get('critical_path', [])[:40]:
            unit = chain['units'].get(step['unit'], {})
            a = assigned.get(step['unit'])
            row = {'unit': step['unit'], 'cause': step['cause'], 'text': _cause_text(step),
                   'start': unit.get('start'), 'end': unit.get('end'),
                   'resources': [d.resource_id for d in a.demands] if a else [],
                   'orders': self._orders_of(a.operation_ids)[:10] if a else []}
            for key in ('ref', 'resource', 'bound', 'calendar_wait_seconds', 'capacity_wait_seconds'):
                if key in unit or key in step:
                    row[key] = step.get(key, unit.get(key))
            if a and a.effective_mode == 'forward' and a.requested_mode in ('auto', 'backward') and any(
                    BACKWARD_FALLBACK in x for x in a.explanation):
                row['backward_fallback'] = True
                row['text'] += f'; {BACKWARD_FALLBACK_TEXT}'
            path.append(row)
        late = (result.tardiness_seconds or 0) > 0
        if not entry:
            summary = 'Nessuna fase della commessa è collocata nel piano: il percorso critico non è ricostruibile.'
        else:
            first = path[0] if path else None
            summary = (('In ritardo di ' + str(_hours(result.tardiness_seconds)) + ' h. ') if late else 'Nei termini. ')
            if first:
                summary += f"L'ultima fase {first['unit']} {first['text']}."
            fallen = [x['unit'] for x in path if x.get('backward_fallback')]
            if late and fallen:
                summary += (f" Attenzione: {', '.join(fallen)} ha ripiegato dal vincolo a ritroso (nota del motore, "
                            "non verificata in modo indipendente): l'attesa indicata è quella trovata dopo il ripiego, "
                            "non il motivo per cui il target a ritroso non era raggiungibile.")
        unscheduled = [{'task_id': u.task_id, 'code': u.code} for u in self.plan.unscheduled
                       if order_id in self._orders_of(u.operation_ids)]
        agreement = chain.get('scheduler_agreement')
        return {**self._base(), 'order_id': order_id, 'due': _text(result.due), 'end': _text(result.end),
                'tardiness_seconds': result.tardiness_seconds, 'late': late, 'summary': summary,
                'critical_path': path, 'unscheduled': unscheduled[:MAX_ROWS],
                'method': 'catena dei vincoli ricostruita dal piano verificato (#88), indipendente dal motore',
                'scheduler_agreement': {k: agreement[k] for k in ('checked', 'agree')} if agreement else None}

    def delays(self, risk_seconds=86400):
        """Late, unplaced and at-risk orders with the main cause read from the constraint chain.

        The cause is the first decisive step of the critical path, never a guess: a capacity wait on a
        resource, a release date (also material arrival), a due date already past at the start of the
        plan, a subcontract date, or a sequence of phases that starts at the beginning of the horizon.
        """
        if type(risk_seconds) is not int or not 0 <= risk_seconds <= 30 * 86400:
            raise ValidationError(['risk_seconds: integer 0..2592000 required'])
        chain = self.chain()
        start = self.scenario.start
        unplaced = {}
        for u in self.plan.unscheduled:
            for order in self._orders_of(u.operation_ids):
                unplaced.setdefault(order, []).append({'task_id': u.task_id, 'code': u.code})
        rows = []
        for result in self.plan.orders:
            late = (result.tardiness_seconds or 0) > 0
            missing = unplaced.get(result.order_id, [])
            slack = (result.due - result.end).total_seconds() if result.due and result.end else None
            path = (chain['orders'].get(result.order_id) or {}).get('critical_path', [])
            # Backward scheduling ends on the due date on purpose: zero margin there is the target, not a risk.
            risk = (not late and not missing and slack is not None and slack < risk_seconds
                    and not (path and path[0]['cause'] == 'backward'))
            if not (late or missing or risk):
                continue
            waits = [(chain['units'].get(s['unit'], {}).get('capacity_wait_seconds', 0), s) for s in path
                     if s['cause'] == 'resource']
            row = {'order_id': result.order_id, 'due': _text(result.due), 'end': _text(result.end),
                   'tardiness_seconds': result.tardiness_seconds, 'slack_seconds': int(slack) if slack is not None else None,
                   'status': 'late' if late else 'unplaced' if missing else 'risk', 'unplaced': missing[:20],
                   'path_length': len(path)}
            origin = path[-1]['cause'] if path else None
            if missing:
                row.update(cause='unplaced', unit=missing[0]['task_id'], detail=missing[0]['code'])
            elif result.due is not None and result.due <= start:
                row.update(cause='expired', detail='consegna già passata all’inizio del piano')
            elif waits:
                seconds, step = max(waits, key=lambda w: (w[0], w[1]['unit']))
                row.update(cause='capacity', unit=step['unit'], resource=step.get('resource'),
                           blocker=step.get('ref'), wait_seconds=seconds)
            elif origin == 'release':
                row.update(cause='release', unit=path[-1]['unit'])
            elif origin == 'external':
                row.update(cause='subcontract', unit=path[-1]['unit'])
            elif origin == 'horizon':
                row.update(cause='lead_time', unit=path[-1]['unit'])
            else:
                row.update(cause='other', unit=path[-1]['unit'] if path else None, detail=origin)
            rows.append(row)
        order = {'late': 0, 'unplaced': 1, 'risk': 2}
        rows.sort(key=lambda r: (order[r['status']], -(r['tardiness_seconds'] or 0), r['slack_seconds'] or 0, r['order_id']))
        counts = {}
        for r in rows:
            counts[r['cause']] = counts.get(r['cause'], 0) + 1
        return {**self._base(), 'risk_seconds': risk_seconds, 'counts': counts, 'total': len(rows),
                'rows': rows[:500], 'truncated': len(rows) > 500,
                'method': 'causa = primo vincolo decisivo del percorso critico ricostruito dal piano verificato (#88)'}

    def _calendar(self, calendar_id):
        if calendar_id not in self._calendars:
            calendar = next(c for c in self.scenario.calendars if c.id == calendar_id)
            self._calendars[calendar_id] = expand(calendar, self.scenario.start, self.scenario.end)
        return self._calendars[calendar_id]

    def _window(self, start, days):
        first = _day(start, 'start') if start is not None else self.scenario.start.astimezone(UTC).date()
        if type(days) is not int or not 1 <= days <= MAX_DAYS:
            raise ValidationError([f'days: integer 1..{MAX_DAYS} required'])
        return first, days

    def _daily(self, resource_ids, first, days):
        booked = {rid: [0] * days for rid in resource_ids}
        for a in self.plan.assignments:
            for d in a.demands:
                if d.resource_id in booked:
                    for segment in a.segments:
                        for index, seconds in _overlap_by_day(segment.start, segment.end, first, days):
                            booked[d.resource_id][index] += seconds * d.units
        rows = []
        for rid in resource_ids:
            resource = self.resources[rid]
            available = [0] * days
            for interval in self._calendar(resource.calendar_id):
                for index, seconds in _overlap_by_day(interval.start, interval.end, first, days):
                    available[index] += seconds
            capacity = resource.capacity
            available = [s * capacity for s in available] if capacity is not None else None
            rows.append({'resource_id': rid, 'kind': resource.kind, 'capacity': capacity / self.capacity_scale if capacity is not None else None,
                         'booked_hours': [_hours(s / self.capacity_scale) for s in booked[rid]],
                         'available_hours': [_hours(s / self.capacity_scale) for s in available] if available is not None else None,
                         'saturation_percent': [round(100 * b / s, 1) if s else None
                                                for b, s in zip(booked[rid], available)] if available is not None else None})
        return rows

    def daily_loads(self, start=None, days=14, kind='machine'):
        first, days = self._window(start, days)
        if kind not in ('machine', 'operator', 'tool', 'supplier', 'external', 'all'):
            raise ValidationError(['kind: machine, operator, tool, supplier, external or all'])
        ids = [r.id for r in self.scenario.resources if kind == 'all' or r.kind == kind]
        return {**self._base(), 'start': first.isoformat(), 'days': [(first + i * DAY).isoformat() for i in range(days)],
                'unit': 'ore-unità per giorno UTC; saturazione = prenotato / disponibile da calendario',
                'rows': self._daily(ids, first, days)}

    def resource_detail(self, resource_id, start=None, days=14):
        _identifier(resource_id, 'resource_id')
        if resource_id not in self.resources:
            raise ValidationError([f'resource not found in this scenario: {resource_id!r}'])
        first, days = self._window(start, days)
        lo, hi = _midnight(first), _midnight(first) + days * DAY
        bookings = sorted((a for a in self.plan.assignments if any(d.resource_id == resource_id for d in a.demands)
                           and any(s.end > lo and s.start < hi for s in a.segments)),
                          key=lambda a: (a.start, a.task_id))
        load = self.loads[resource_id]
        daily = self._daily([resource_id], first, days)[0]
        return {**self._base(), 'resource_id': resource_id, 'kind': self.resources[resource_id].kind,
                'capacity': daily['capacity'],
                'utilization_horizon_percent': round(100 * load.utilization, 1) if load.utilization is not None else None,
                'window': {'start': first.isoformat(), 'days': days},
                'daily': [{'day': (first + i * DAY).isoformat(), 'booked_hours': daily['booked_hours'][i],
                           'available_hours': daily['available_hours'][i] if daily['available_hours'] else None,
                           'saturation_percent': daily['saturation_percent'][i] if daily['saturation_percent'] else None}
                          for i in range(days)],
                'bookings': [self._assignment_row(a) for a in bookings[:MAX_ROWS]], 'total_bookings': len(bookings),
                'truncated': len(bookings) > MAX_ROWS}

    def nesting_detail(self, nesting_id=None):
        nestings = {n.id: n for n in self.scenario.nestings}
        if nesting_id is None:
            rows = [{'nesting_id': n.id, 'operations': len(n.operation_ids),
                     'orders': len(self._orders_of(n.operation_ids)), 'hours': _hours(n.duration_seconds),
                     **self._sheet_attributes('nest:' + n.id, n.operation_ids)}
                    for n in self.scenario.nestings]
            return {**self._base(), 'total': len(rows), 'rows': rows[:MAX_ROWS], 'truncated': len(rows) > MAX_ROWS}
        _identifier(nesting_id, 'nesting_id')
        if nesting_id not in nestings:
            raise ValidationError([f'nesting not found in this scenario: {nesting_id!r}'])
        n = nestings[nesting_id]
        a = next((x for x in self.plan.assignments if set(n.operation_ids) <= set(x.operation_ids)), None)
        return {**self._base(), 'nesting_id': n.id, 'operations': list(n.operation_ids)[:MAX_ROWS],
                'orders': self._orders_of(n.operation_ids), 'duration_hours': _hours(n.duration_seconds),
                'setup_hours': _hours(n.setup_seconds), 'booking': self._assignment_row(a) if a else None,
                **self._sheet_attributes('nest:' + n.id, n.operation_ids),
                'geometry': None,
                'note': 'Materiale e spessore (mm) vengono dalla fonte, se indicati; formati e lamiere non fanno parte '
                        'del contratto Scenario v1.'}

    def search(self, text):
        if not isinstance(text, str) or not 2 <= len(text.strip()) <= 120:
            raise ValidationError(['text: 2..120 characters required'])
        needle = text.strip().casefold()
        def find(values):
            hits = sorted(v for v in values if needle in v.casefold())
            return {'total': len(hits), 'ids': hits[:30]}
        return {**self._base(), 'text': text.strip(), 'orders': find(self.orders),
                'resources': find(self.resources), 'tasks': find(a.task_id for a in self.plan.assignments),
                'operations': find(self.order_of), 'nestings': find(n.id for n in self.scenario.nestings)}

    def quality(self):
        from planning_factory.engine.quality import assess_quality
        return {**self._base(), 'kpi': assess_quality(self.scenario, self.plan)}
