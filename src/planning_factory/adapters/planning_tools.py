"""Bounded read tools shared by the Claude backend and local MCP transport."""
from copy import deepcopy
from datetime import datetime
import json

from planning_factory.domain.validation import ValidationError

EMPTY = {'type': 'object', 'properties': {}, 'additionalProperties': False}
TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 256}
DAY = {'type': 'string', 'description': 'UTC day YYYY-MM-DD; default: start of the horizon'}
DAYS = {'type': 'integer', 'minimum': 1, 'maximum': 62}


def _schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


CATALOG = [
    {'name': name, 'description': description, 'inputSchema': schema}
    for name, description, schema in (
        ('get_plan_summary', 'Counts and digest of this exact plan; no inferred causes.', EMPTY),
        ('get_deliveries', 'Up to 100 late or incomplete orders, with total count, order code and customer '
                           '(code and company name) when the source gives them.', EMPTY),
        ('get_customers', 'Orders grouped by customer (code and company name): orders, late, incomplete, total '
                          'and worst tardiness, late orders whose due date was already past at the start of the '
                          'plan. Sorted by total tardiness.', EMPTY),
        ('get_resource_loads', 'Up to 100 resource loads, measured over the whole horizon.', EMPTY),
        ('get_materials', 'Source manifest pegging and shortages when attested (and whether the company memory switched the material constraint off); otherwise unavailable.', EMPTY),
        ('get_applied_rules', 'Rules in the source manifest; never adds or approves a rule.', EMPTY),
        ('get_data_quality', 'Source quality issues; does not certify freshness or business acceptance.', EMPTY),
        ('get_order', 'One order: code, customer, due date, planned end, tardiness, its scheduled tasks with '
                      'resources and times, and its unscheduled tasks.', _schema({'order_id': TEXT}, ('order_id',))),
        ('explain_order', 'Why an order ends when it ends: finite-capacity critical path reconstructed from '
                          'the verified plan (predecessor, occupied resource, release, fixed start); backward_fallback=true '
                          'is a scheduler note (not independently verified): the step missed its backward '
                          '(as-late-as-possible) target, and its wait is the one found after falling back, '
                          'not the reason the target was unreachable.',
         _schema({'order_id': TEXT}, ('order_id',))),
        ('get_resource', 'One resource: horizon utilization, daily booked and calendar-available hours with '
                         'saturation, and its bookings in a window of UTC days.',
         _schema({'resource_id': TEXT, 'start': DAY, 'days': DAYS}, ('resource_id',))),
        ('get_nesting', 'Without nesting_id: list of nesting programs. With nesting_id: member operations, '
                        'orders, shared duration and booking.', _schema({'nesting_id': TEXT})),
        ('search_plan', 'Find order, resource, task, operation and nesting identifiers, order codes and '
                        'customers (code or company name) containing a text.',
         _schema({'text': {'type': 'string', 'minLength': 2, 'maxLength': 120}}, ('text',))),
        ('get_plan_quality', 'Audited plan KPIs: service level, tardiness, makespan, bottlenecks.', EMPTY),
    )
]
_BY_NAME = {t['name']: t for t in CATALOG}
_INSIGHT_TOOLS = {'get_order', 'explain_order', 'get_resource', 'get_nesting', 'search_plan', 'get_plan_quality'}


def check_arguments(schema, arguments):
    """Strict subset of JSON Schema used by the catalog: declared keys only, typed and bounded."""
    if not isinstance(arguments, dict):
        raise ValidationError(['arguments must be an object'])
    properties = schema['properties']
    if set(arguments) - set(properties) or not set(schema.get('required', ())) <= set(arguments):
        raise ValidationError(['unknown or missing arguments'])
    for key, value in arguments.items():
        rule = properties[key]
        if rule['type'] == 'string':
            if not isinstance(value, str) or not rule.get('minLength', 0) <= len(value) <= rule.get('maxLength', 256):
                raise ValidationError([f'{key}: bounded string required'])
        elif rule['type'] == 'integer':
            if type(value) is not int or not rule.get('minimum', value) <= value <= rule.get('maximum', value):
                raise ValidationError([f'{key}: bounded integer required'])
        else:
            raise ValidationError([f'{key}: unsupported argument type'])


def _instant(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')) if value else None


class PlanningTools:
    """``snapshot`` holds the primitive ``plan`` and ``meta``; ``insights`` (or the snapshot's
    ``scenario``) enables the tools that need the Scenario, such as the constraint chain.
    ``order_texts`` maps an order id to its source ``code``, ``customer_code`` and ``customer``
    (company name): presentation texts, never inputs of the engine."""

    def __init__(self, snapshot, insights=None):
        self.snapshot = {k: deepcopy(snapshot[k]) for k in ('plan', 'meta') if k in snapshot}
        self.texts = deepcopy(snapshot.get('order_texts') or {})
        self._scenario = snapshot.get('scenario')
        self._insights = insights

    def _named(self, row):
        text = self.texts.get(row.get('order_id'), {})
        return {**row, **{('order_code' if k == 'code' else k): v for k, v in text.items()}}

    def _customers(self, plan):
        if not any(t.get('customer_code') or t.get('customer') for t in self.texts.values()):
            return {'available': False, 'reason': 'la fonte non indica il cliente delle commesse'}
        insights = self.insights()
        start = (insights.scenario.start if insights is not None
                 and insights.plan.scenario_digest == plan['scenario_digest'] else None)
        groups = {}
        for r in plan['orders']:
            text = self.texts.get(r['order_id'], {})
            key = text.get('customer_code') or text.get('customer')
            g = groups.setdefault(key, {'customer_code': text.get('customer_code'), 'customer': text.get('customer'),
                                        'orders': 0, 'late': 0, 'incomplete': 0, 'tardiness_seconds': 0,
                                        'worst_order_id': None, 'worst_tardiness_seconds': 0,
                                        **({'late_due_before_start': 0} if start is not None else {})})
            late = r.get('tardiness_seconds') or 0
            g['orders'] += 1
            g['incomplete'] += 0 if r['complete'] else 1
            if late > 0:
                g['late'] += 1
                g['tardiness_seconds'] += late
                if start is not None and r.get('due') and _instant(r['due']) <= start:
                    g['late_due_before_start'] += 1
                if late > g['worst_tardiness_seconds']:
                    g['worst_order_id'], g['worst_tardiness_seconds'] = r['order_id'], late
        rows = []
        for g in groups.values():
            g['tardiness_days'] = round(g['tardiness_seconds'] / 86400, 1)
            if g['worst_order_id'] is not None and self.texts.get(g['worst_order_id'], {}).get('code'):
                g['worst_order_code'] = self.texts[g['worst_order_id']]['code']
            rows.append({k: v for k, v in g.items() if v is not None})
        rows.sort(key=lambda g: (-g['tardiness_seconds'], -g['late'], -g['incomplete'],
                                 g.get('customer') or g.get('customer_code') or '￿'))
        return {'available': True, 'total': len(rows), 'rows': rows[:100], 'truncated': len(rows) > 100,
                'note': 'ritardo = somma della tardiness delle commesse in ritardo; incomplete = commesse con '
                        'fasi non collocate, ritardo non calcolabile; righe senza cliente = non indicato dalla fonte'}

    def _search_texts(self, result, text):
        needle = text.strip().casefold()
        # Same match as PlanInsights.search on the id, plus the order code: counted over every order of the plan.
        planned = {r['order_id'] for r in self.snapshot['plan']['orders']}
        ids = sorted(oid for oid in planned
                     if needle in oid.casefold() or needle in self.texts.get(oid, {}).get('code', '').casefold())
        result['orders'] = {'total': len(ids), 'ids': ids[:30]}
        found = {}
        for oid, t in sorted(self.texts.items()):
            if oid not in planned:
                continue
            if needle in t.get('customer', '').casefold() or needle in t.get('customer_code', '').casefold():
                row = found.setdefault(t.get('customer_code') or t.get('customer'), {
                    k: t[k] for k in ('customer_code', 'customer') if k in t} | {'order_ids': []})
                row['order_ids'].append(oid)
        rows = sorted(found.values(), key=lambda r: r.get('customer') or r.get('customer_code'))
        for row in rows:
            row['order_ids'] = row['order_ids'][:20]
        result['customers'] = {'total': len(rows), 'rows': rows[:30]}
        return result

    def list_tools(self):
        return [{**deepcopy(t), 'annotations': {'readOnlyHint': True, 'destructiveHint': False,
                                               'idempotentHint': True, 'openWorldHint': False}} for t in CATALOG]

    def insights(self):
        if self._insights is None:
            if self._scenario is None:
                return None
            from planning_factory.adapters.json_io import plan_loads, scenario_from_dict
            from planning_factory.adapters.plan_insights import PlanInsights
            self._insights = PlanInsights(scenario_from_dict(self._scenario), plan_loads(json.dumps(self.snapshot['plan'])))
        return self._insights

    def call(self, name, arguments):
        if not isinstance(name, str) or name not in _BY_NAME:
            raise ValidationError(['unknown read tool or invalid arguments'])
        check_arguments(_BY_NAME[name]['inputSchema'], arguments)
        plan = self.snapshot['plan']
        if name in _INSIGHT_TOOLS:
            insights = self.insights()
            if insights is None:
                return {'scenario_digest': plan['scenario_digest'], 'read_only': True,
                        'available': False, 'reason': 'scenario not available to this tool'}
            if insights.plan.scenario_digest != plan['scenario_digest']:
                raise ValidationError(['insights belong to another plan'])
            result = {
                'get_order': lambda: insights.order_detail(arguments['order_id']),
                'explain_order': lambda: insights.explain_order(arguments['order_id']),
                'get_resource': lambda: insights.resource_detail(arguments['resource_id'], arguments.get('start'),
                                                                 arguments.get('days', 14)),
                'get_nesting': lambda: insights.nesting_detail(arguments.get('nesting_id')),
                'search_plan': lambda: insights.search(arguments['text']),
                'get_plan_quality': insights.quality,
            }[name]()
            if name in ('get_order', 'explain_order'):
                result = self._named(result)
            elif name == 'search_plan':
                result = self._search_texts(result, arguments['text'])
            return self._bounded(result)
        meta = self.snapshot.get('meta') or {}
        canonical = meta.get('canonical') or {}
        result = {'scenario_digest': plan['scenario_digest'], 'read_only': True,
                  'source_only': True, 'production_approved': False}
        if name == 'get_plan_summary':
            result.update({'scenario_id': plan['scenario_id'], 'assignments': len(plan['assignments']),
                           'unscheduled': len(plan['unscheduled']), 'orders': len(plan['orders']),
                           'unscheduled_examples': plan['unscheduled'][:30]})
        elif name == 'get_deliveries':
            rows = [r for r in plan['orders'] if not r['complete'] or (r.get('tardiness_seconds') or 0) > 0]
            result.update({'total': len(rows), 'rows': [self._named(r) for r in rows[:100]], 'truncated': len(rows) > 100})
        elif name == 'get_customers':
            result.update(self._customers(plan))
        elif name == 'get_resource_loads':
            result.update({'total': len(plan['resources']), 'rows': plan['resources'][:100],
                           'truncated': len(plan['resources']) > 100})
        elif name == 'get_data_quality':
            issues = canonical.get('issues', [])
            result.update({'available': bool(canonical), 'issues': issues[:100], 'total': len(issues)})
        else:
            key = 'materials' if name == 'get_materials' else 'rules'
            value = canonical.get(key)
            if key == 'rules' and meta.get('company_memory') is not None:
                value = {'canonical_source': value, 'company_memory': meta['company_memory']}
            switched = ((meta.get('company_memory') or {}).get('material_constraint') or {})
            if key == 'materials' and switched.get('ignored'):
                # Shortages of the source stay visible, but this plan does not wait for the material.
                value = {'source': value, 'constraint_ignored_by_company_memory': switched}
            result.update({'available': value is not None, 'data': value})
        return self._bounded(result)

    def _bounded(self, result):
        # Never silently truncate unbounded source manifests into a misleading answer.
        if len(json.dumps(result, ensure_ascii=False).encode()) > 48000:
            return {'scenario_digest': self.snapshot['plan']['scenario_digest'], 'read_only': True,
                    'available': False, 'reason': 'source evidence exceeds tool output bound'}
        return result
