"""Scenario v1 -> canonical bundle. Used to migrate existing scenarios and as round-trip oracle in tests.

Order-level values are not inferred from operations: every operation keeps its own
due, priority and mode, and each order is emitted with its id only.
"""
from datetime import UTC, datetime

from planning_factory.adapters.json_io import primitive
from planning_factory.domain.models import Scenario
from .entities import ENTITIES, SCHEMA_VERSION


def _p(value):
    return primitive(value)


def scenario_to_bundle(s: Scenario, extracted_at: datetime | None = None, profile_id: str = 'default') -> dict:
    r = {name: [] for name in ENTITIES}
    for c in s.calendars:
        r['calendar'].append({'id': c.id, 'timezone': c.timezone})
        r['calendar_shift'] += [{'calendar_id': c.id, 'weekday': x.weekday, 'start': _p(x.start), 'end': _p(x.end)}
                                for x in c.shifts]
        r['calendar_holiday'] += [{'calendar_id': c.id, 'day': _p(d)} for d in c.holidays]
        for e in c.exceptions:
            r['calendar_exception'].append({'calendar_id': c.id, 'day': _p(e.day)})
            r['calendar_exception_shift'] += [{'calendar_id': c.id, 'day': _p(e.day), 'start': _p(a), 'end': _p(b)}
                                              for a, b in e.shifts]
        r['calendar_closure'] += [{'calendar_id': c.id, 'start': _p(x.start), 'end': _p(x.end)} for x in c.closures]
    r['resource'] = [{'id': x.id, 'calendar_id': x.calendar_id, 'capacity': x.capacity, 'kind': x.kind,
                      **({'efficiency_bp': x.efficiency_bp} if x.efficiency_bp != 10000 else {})}
                     for x in s.resources]
    r['order'] = [{'id': o} for o in sorted({o.order_id for o in s.operations})]
    for o in s.operations:
        op = {'id': o.id, 'order_id': o.order_id, 'duration_seconds': o.duration_seconds,
              'setup_seconds': o.setup_seconds, 'priority': o.priority, 'customer_priority': o.customer_priority,
              'phase_priority': o.phase_priority, 'splittable': o.splittable}
        if o.preferred_alternative is not None:
            op['preferred_alternative'] = o.preferred_alternative
        for field in ('due', 'release', 'mode'):
            if getattr(o, field) is not None:
                op[field] = _p(getattr(o, field))
        r['operation'].append(op)
        for index, alternative in enumerate(o.alternatives):
            r['operation_demand'] += [{'operation_id': o.id, 'alternative': index, 'resource_id': d.resource_id,
                                       'units': d.units} for d in alternative.demands]
        r['operation_dependency'] += [{'operation_id': o.id, 'predecessor_id': d.predecessor_id, 'kind': d.kind,
                                       'lag_seconds': d.lag_seconds,
                                       **({'max_lag_seconds': d.max_lag_seconds} if d.max_lag_seconds is not None else {})}
                                      for d in o.predecessors]
        if o.external_end is not None:
            r['operation_external_end'].append({'operation_id': o.id, 'external_end': _p(o.external_end)})
        state = {k: _p(getattr(o, k)) for k in ('completed_at', 'fixed_start', 'fixed_alternative')
                 if getattr(o, k) is not None}
        if state:
            r['operation_state'].append({'operation_id': o.id, **state})
    for n in s.nestings:
        r['nest'].append({'id': n.id, 'duration_seconds': n.duration_seconds, 'setup_seconds': n.setup_seconds})
        r['nest_member'] += [{'nest_id': n.id, 'operation_id': op} for op in n.operation_ids]
    st = s.settings
    r['profile'] = [{'id': profile_id, 'mode': st.mode, 'auto_backward_days': st.auto_backward_days,
                     'priority_weight': st.priority_weight, 'due_weight': st.due_weight,
                     'customer_weight': st.customer_weight,
                     **({'alternative_switch_min_gain_seconds': st.alternative_switch_min_gain_seconds}
                        if st.alternative_switch_min_gain_seconds else {}),
                     **({'auto_rule': st.auto_rule} if st.auto_rule != 'due_distance' else {}),
                     **({'auto_slack_seconds': st.auto_slack_seconds} if st.auto_slack_seconds else {})}]
    bundle = {'schema_version': SCHEMA_VERSION, 'records': {k: v for k, v in r.items() if v}}
    if extracted_at is not None:
        bundle['extracted_at'] = extracted_at.astimezone(UTC).isoformat().replace('+00:00', 'Z')
    return bundle
