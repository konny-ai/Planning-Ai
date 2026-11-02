"""Versioned rule drafts, explicit planner decisions and canonical application.

Besides the canonical rules (an integer action on an explicit scope) the company memory keeps two kinds of
indication that are not canonical rules and live only here, with the same draft/confirm/revoke cycle:

* ``note``: a free-text indication ("per il cliente X si salda sempre prima di piegare"), optionally scoped.
  It never changes a calculation: confirmed notes are context the assistant receives in every conversation.
* ``ignore_material``: switches the material constraint off for the plan of the source (decision of the user,
  24/10/2026). The plan starts from the Scenario as built *before* the material releases; shortages stay
  listed in the source manifest and the evidence says which rule switched them off.
"""
from collections import defaultdict
from contextlib import closing
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
import json
import sqlite3
import uuid

from planning_factory.adapters.json_io import scenario_digest, primitive
from planning_factory.adapters.store import ConflictError
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.canonical.entities import ENTITIES, NORMALIZERS, normalize
from planning_factory.canonical.rules import apply_rules
from planning_factory.domain.validation import ValidationError

SCOPES = {'order_id', 'resource_id', 'customer_id', 'material', 'min_thickness_mm', 'max_thickness_mm'}
FIELDS = {'action', 'value', 'note', 'valid_from', 'valid_to'} | SCOPES
# Named order-priority levels (lower runs first, 0 = no rule). A convention of the memory, not a source value:
# with the CLIENTE weights (priority 10000 per point, 1 per day of due date) "massima" goes ahead of every order
# without a rule whatever its due date. The user can always give another integer.
PRIORITY_LEVELS = {'massima': -1000, 'alta': -100, 'normale': 0, 'bassa': 100}
GUIDANCE = {'note', 'ignore_material'}  # memory indications, never part of the canonical rule domain
ACTIONS = {'add_setup_seconds', 'set_priority', 'set_customer_priority'} | GUIDANCE


class RuleMemory:
    def __init__(self, database, *, source=None, scope_context=None, material_free=None):
        self.database = str(database)
        self.source = source
        self.scope_context = json.loads(json.dumps(scope_context)) if scope_context else None
        # (digest of the source, the same Scenario before the material releases) or None when the
        # source has no separate materials domain: then there is no material constraint to switch off.
        self.material_free = material_free
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS company_rules (id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, PRIMARY KEY(id,version))')

    def create_draft(self, values, actor):
        if isinstance(values, dict) and isinstance(values.get('action'), str) and values['action'] in GUIDANCE:
            return self._insert(self._guidance(values), actor)
        if not isinstance(values, dict) or set(values) - FIELDS or not {'action', 'value'} <= set(values) or not SCOPES.intersection(values):
            raise ValidationError(['rule needs action, integer value and an explicit scope; confirmation fields are server-owned'])
        if not isinstance(values['action'], str) or values['action'] not in ACTIONS or type(values['value']) is not int or abs(values['value']) > 1000000:
            raise ValidationError(['unsupported action or unbounded integer value'])
        if values['action'] == 'add_setup_seconds' and values['value'] < 0:
            raise ValidationError(['additional setup must be nonnegative'])
        for key in SCOPES.intersection(values):
            if not isinstance(values[key], str) or not 1 <= len(values[key].strip()) <= 256:
                raise ValidationError([f'explicit {key} required'])
        if values.get('note') is not None and (not isinstance(values['note'], str) or len(values['note']) > 2000):
            raise ValidationError(['note must be at most 2000 characters'])
        rule_id = 'RULE-' + uuid.uuid4().hex
        normalized, errors = normalize(ENTITIES['rule'], {'id': rule_id, **values})
        if errors:
            raise ValidationError(errors)
        if normalized.get('min_thickness_mm') is not None and normalized.get('max_thickness_mm') is not None and Decimal(normalized['min_thickness_mm']) >= Decimal(normalized['max_thickness_mm']):
            raise ValidationError(['thickness interval must be increasing (min exclusive, max inclusive)'])
        self.validate_scope(normalized)
        if normalized.get('valid_from') and normalized.get('valid_to') and normalized['valid_from'] >= normalized['valid_to']:
            raise ValidationError(['rule validity interval must be increasing'])
        return self._insert(normalized, actor)

    def _insert(self, normalized, actor):
        at = datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO company_rules VALUES (?,?,?,?,?,?)', (normalized['id'], 1, 'draft', json.dumps(normalized), actor, at))
        return {'id': normalized['id'], 'version': 1, 'status': 'draft', 'rule': normalized, 'actor': actor, 'at': at}

    def _guidance(self, values):
        """A note (free text, optional scope) or the switch of the material constraint (whole plan, no value)."""
        action = values['action']
        allowed = {'action', 'note', 'valid_from', 'valid_to'} | (SCOPES if action == 'note' else set())
        if set(values) - allowed:
            raise ValidationError([f'{action}: fields {", ".join(sorted(set(values) - allowed))} not allowed' +
                                   (' (it applies to the whole plan, without scope or value)' if action == 'ignore_material'
                                    else ' (a note has text, optional scope and validity, no value)')])
        note = values.get('note')
        if note is not None and (not isinstance(note, str) or len(note) > 2000):
            raise ValidationError(['note must be at most 2000 characters'])
        if action == 'note' and not (note or '').strip():
            raise ValidationError(['a note needs its text in note'])
        if action == 'ignore_material' and self.material_free is None:
            raise ValidationError(['the source has no separate materials domain: there is no material constraint to switch off'])
        for key in SCOPES.intersection(values):
            if not isinstance(values[key], str) or not 1 <= len(values[key].strip()) <= 256:
                raise ValidationError([f'explicit {key} required'])
        rule = {'id': 'RULE-' + uuid.uuid4().hex, 'action': action, **{k: v for k, v in values.items() if k != 'action'}}
        if note is not None:
            rule['note'] = note.strip()
        for key in ('valid_from', 'valid_to'):
            if rule.get(key) is not None:
                try:
                    rule[key] = NORMALIZERS['instant'](rule[key])
                except (TypeError, ValueError) as exc:
                    raise ValidationError([f'{key}: {exc}']) from None
        if rule.get('min_thickness_mm') is not None or rule.get('max_thickness_mm') is not None:
            normalized, errors = normalize(ENTITIES['rule'], {**{k: rule[k] for k in SCOPES if k in rule},
                                                              'id': rule['id'], 'action': 'set_priority', 'value': 0})
            if errors:
                raise ValidationError(errors)
            rule.update({k: normalized[k] for k in ('min_thickness_mm', 'max_thickness_mm') if normalized.get(k) is not None})
        self.validate_scope(rule)
        if rule.get('valid_from') and rule.get('valid_to') and rule['valid_from'] >= rule['valid_to']:
            raise ValidationError(['rule validity interval must be increasing'])
        return rule

    @staticmethod
    def _in_window(rule, now):
        return (not rule.get('valid_from') or rule['valid_from'] <= now) and (not rule.get('valid_to') or now < rule['valid_to'])

    def guidance(self, now=None):
        """Every confirmed rule and indication in its validity window: what the assistant must keep in mind."""
        now = now or datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        return [{'id': x['id'], 'action': x['rule']['action'],
                 **{k: x['rule'][k] for k in ('value', 'note', *sorted(SCOPES), 'valid_from', 'valid_to', 'confirmed_by')
                    if x['rule'].get(k) is not None}}
                for x in self.list() if x['status'] == 'confirmed' and self._in_window(x['rule'], now)]

    def validate_scope(self, rule):
        context = self.scope_context or {}
        if self.source:
            for key, available in (('order_id', {o.order_id for o in self.source.operations}),
                                   ('resource_id', {r.id for r in self.source.resources})):
                if rule.get(key) is not None and rule[key] not in available:
                    raise ValidationError([f'{key} does not exist in source'])
        for key, entity, field in (('customer_id', 'orders', 'customer_id'), ('material', 'operations', 'material')):
            if rule.get(key) is not None and rule[key] not in {r.get(field) for r in context.get(entity, [])}:
                raise ValidationError([f'{key} has no authoritative source attribute'])
        if any(rule.get(k) is not None for k in ('min_thickness_mm', 'max_thickness_mm')) and not any(
                r.get('thickness_mm') is not None for r in context.get('operations', [])):
            raise ValidationError(['thickness has no authoritative source attribute'])

    def scope_catalog(self):
        context = self.scope_context or {}
        return {'resource_id': sorted(r.id for r in self.source.resources) if self.source else [],
                'customer_id': sorted({r['customer_id'] for r in context.get('orders', []) if r.get('customer_id')}),
                'material': sorted({r['material'] for r in context.get('operations', []) if r.get('material')}),
                'thickness_available': any(r.get('thickness_mm') is not None for r in context.get('operations', [])),
                'priority_levels': dict(PRIORITY_LEVELS)}

    def list(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            rows = db.execute('SELECT a.id,a.version,a.status,a.payload,a.actor,a.at FROM company_rules a WHERE a.version=(SELECT MAX(b.version) FROM company_rules b WHERE b.id=a.id) ORDER BY a.id').fetchall()
        return [{'id': rid, 'version': version, 'status': status, 'rule': json.loads(payload), 'actor': actor, 'at': at}
                for rid, version, status, payload, actor, at in rows]

    def cache_key(self, now=None):
        now = now or datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        return tuple((r['id'], r['version'], r['status'], self._in_window(r['rule'], now)) for r in self.list())

    def decide(self, rule_id, expected_version, decision, actor, *, validate=None):
        if type(expected_version) is not int or expected_version < 1 or decision not in ('confirm', 'revoke'):
            raise ValidationError(['expected_version and confirm/revoke required'])
        with closing(sqlite3.connect(self.database, timeout=10)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT version,status,payload FROM company_rules WHERE id=? ORDER BY version DESC LIMIT 1', (rule_id,)).fetchone()
            if row is None:
                raise KeyError(rule_id)
            version, status, payload = row
            if version != expected_version:
                raise ConflictError(f'expected rule version {expected_version}, found {version}')
            # Revoking a draft discards it; a draft never changed a plan.
            if (decision, status) not in (('confirm', 'draft'), ('revoke', 'confirmed'), ('revoke', 'draft')):
                raise ValidationError(['invalid rule state transition'])
            if decision == 'confirm' and validate is not None:
                # The write lock serializes competing confirmations while the
                # caller checks the complete set of currently confirmed rules.
                validate()
            at = datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
            values = json.loads(payload)
            if decision == 'confirm':
                values.update({'confirmed_by': actor, 'confirmed_at': at})
            target = 'confirmed' if decision == 'confirm' else 'revoked'
            db.execute('INSERT INTO company_rules VALUES (?,?,?,?,?,?)',
                       (rule_id, version + 1, target, json.dumps(values), actor, at))
        return {'id': rule_id, 'version': version + 1, 'status': target, 'rule': values, 'actor': actor, 'at': at}

    def apply(self, scenario, *, preview_rule=None):
        entries = self.list()
        rules = [x['rule'] for x in entries if x['status'] == 'confirmed']
        if preview_rule:
            record = next((x for x in entries if x['id'] == preview_rule), None)
            if record is None or record['status'] != 'draft':
                raise ValidationError(['preview requires an existing draft'])
            rules.append({**record['rule'], 'confirmed_by': 'PREVIEW_ONLY',
                          'confirmed_at': datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')})
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        guidance = [r for r in rules if r['action'] in GUIDANCE]
        rules = [r for r in rules if r['action'] not in GUIDANCE]
        switches = sorted(r['id'] for r in guidance if r['action'] == 'ignore_material' and self._in_window(r, now))
        source = scenario
        if switches:
            # The constraint is switched off by planning the same source as built before the material releases.
            if self.material_free is None or self.material_free[0] != scenario_digest(scenario):
                raise ValidationError(['the Scenario without material releases belongs to another source revision'])
            scenario = self.material_free[1]
        records = defaultdict(list, scenario_to_bundle(scenario)['records'])
        if self.scope_context:
            if self.scope_context['source_scenario_digest'] != scenario_digest(source):
                raise ValidationError(['rule scope attributes belong to another scenario revision'])
            attrs = {r['id']: r for r in self.scope_context['operations']}
            customers = {r['id']: r for r in self.scope_context['orders']}
            records['operation'] = [{**r, **{k: attrs[r['id']][k] for k in ('material', 'thickness_mm') if k in attrs.get(r['id'], {})}}
                                    for r in records['operation']]
            records['order'] = [{**r, **{'customer_id': customers[r['id']]['customer_id']}} if customers.get(r['id'], {}).get('customer_id') is not None else r
                               for r in records['order']]
        if not self.scope_context and any(any(r.get(k) is not None for k in ('customer_id', 'material', 'min_thickness_mm', 'max_thickness_mm')) for r in rules):
            raise ValidationError(['confirmed scoped rules need authoritative source attributes'])
        records['rule'] = rules
        ops = {r['id']: dict(r) for r in records['operation']}
        progressed = set()
        for operation in scenario.operations:
            ops[operation.id]['alternatives'] = primitive(operation.alternatives)
            if operation.completed_at is not None:
                ops[operation.id]['completed_at'] = operation.completed_at.isoformat()
            if operation.external_end is not None:
                ops[operation.id]['external_end'] = operation.external_end.isoformat()
            if operation.fixed_start is not None:
                progressed.add(operation.id)
        members = {oid for n in scenario.nestings for oid in n.operation_ids}
        issues, evidence = apply_rules(ops, records, None, now, members, progressed)
        if any(x.severity in ('error', 'block') for x in issues):
            raise ValidationError([str(x) for x in issues])
        adjusted = replace(scenario, operations=tuple(replace(o, **{k: ops[o.id][k] for k in
                           ('setup_seconds', 'priority', 'customer_priority')}) for o in scenario.operations))
        released = {o.id: o.release for o in scenario.operations}
        freed = sorted(o.id for o in source.operations if o.release != released[o.id]) if switches else []
        for rule in sorted(guidance, key=lambda r: r['id']):
            if not self._in_window(rule, now):
                evidence['skipped'].append({'rule': rule['id'], 'reason': 'outside its validity window'})
            elif rule['action'] == 'ignore_material':
                evidence['applied'].append({'rule': rule['id'], 'action': 'ignore_material', 'value': None,
                                            'operations': freed, 'confirmed_by': rule['confirmed_by']})
        return adjusted, {**evidence, 'issues': [i.to_dict() for i in issues],
                          'notes': [{k: r[k] for k in ('id', 'note', *sorted(SCOPES), 'confirmed_by') if r.get(k) is not None}
                                    for r in sorted(guidance, key=lambda r: r['id'])
                                    if r['action'] == 'note' and self._in_window(r, now)],
                          'material_constraint': {'ignored': bool(switches), 'rules': switches,
                                                  'operations_released': len(freed)},
                          'versions': [{'id': x['id'], 'version': x['version'], 'status': x['status']} for x in entries],
                          'source_scenario_digest': scenario_digest(source),
                          'preview_only': preview_rule is not None}
