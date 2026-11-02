"""Scoped rules (#78 P20, base of the company memory #93): confirmed, traceable adjustments.

A rule says *what* to change (action, value) and *where* (scope: resource, order,
customer, material, thickness range, profile, validity window). Only rules confirmed
by a planner (``confirmed_by`` + ``confirmed_at``) are applied; the others are listed
and ignored. Every application is traced per operation in the manifest, so the plan
can be explained ("+45 min setup by rule R-12") and audited. Turning natural language
into rules is out of scope here: this module is the deterministic part.
"""
from decimal import Decimal

from .model import Issue

ACTIONS = ('add_setup_seconds', 'set_priority', 'set_customer_priority')


def _instant_le(a: str, b: str) -> bool:
    return a <= b  # canonical instants are UTC 'Z' strings of equal length


def apply_rules(operations: dict, records, profile: str | None, now: str, nest_members: set,
                progressed: set, buffers: dict | None = None) -> tuple[list[Issue], dict]:
    issues, applied, skipped = [], [], []
    orders = {r['id']: r for r in records['order']}
    attributes = {r['id']: r for r in records['operation']}
    priorities: dict[tuple[str, str], tuple[int, str]] = {}
    for rule in sorted(records['rule'], key=lambda r: r['id']):
        rid = rule['id']
        if rule.get('confirmed_by') is None or rule.get('confirmed_at') is None:
            skipped.append({'rule': rid, 'reason': 'not confirmed by a planner'})
            issues.append(Issue('warn', 'rule_not_confirmed', 'rules', 'rule ignored until a planner confirms it',
                                entity='rule', key=rid))
            continue
        if rule.get('profile') is not None and rule['profile'] != profile:
            skipped.append({'rule': rid, 'reason': f'profile {rule["profile"]} not selected'})
            continue
        if rule.get('valid_from') is not None and not _instant_le(rule['valid_from'], now) or \
                rule.get('valid_to') is not None and not now < rule['valid_to']:
            skipped.append({'rule': rid, 'reason': 'outside its validity window'})
            continue
        hits, partial = [], []
        for op_id in sorted(operations):
            op, attrs = operations[op_id], attributes[op_id]
            if 'completed_at' in op or 'external_end' in op:
                continue
            order = orders.get(op['order_id'], {})
            if rule.get('order_id') is not None and op['order_id'] != rule['order_id']:
                continue
            if rule.get('customer_id') is not None and order.get('customer_id') != rule['customer_id']:
                continue
            if rule.get('material') is not None and attrs.get('material') != rule['material']:
                continue
            thickness = attrs.get('thickness_mm')
            if (rule.get('min_thickness_mm') is not None or rule.get('max_thickness_mm') is not None) and thickness is None:
                continue
            if rule.get('min_thickness_mm') is not None and Decimal(thickness) <= Decimal(rule['min_thickness_mm']):
                continue
            if rule.get('max_thickness_mm') is not None and Decimal(thickness) > Decimal(rule['max_thickness_mm']):
                continue
            if rule.get('resource_id') is not None:
                uses = [any(d['resource_id'] == rule['resource_id'] for d in a['demands']) for a in op['alternatives']]
                if not any(uses):
                    continue
                if not all(uses):
                    partial.append(op_id)  # setup/priority are per operation, not per alternative
                    continue
            if rule['action'] == 'add_setup_seconds' and (op_id in nest_members or op_id in progressed):
                partial.append(op_id)
                continue
            hits.append(op_id)
        if not hits and not partial:
            issues.append(Issue('warn', 'rule_matches_nothing', 'rules', 'confirmed rule with no operation in scope',
                                entity='rule', key=rid))
        for op_id in partial:
            issues.append(Issue('warn', 'rule_not_applicable', 'rules',
                                f'{rid} matches {op_id} only on some alternatives, in a nest or on started work: not applied',
                                entity='operation', key=op_id))
        for op_id in hits:
            op = operations[op_id]
            if rule['action'] == 'add_setup_seconds':
                op['setup_seconds'] += rule['value']
            elif rule['action'] == 'set_delivery_buffer_seconds':
                if rule['value'] < 0:
                    issues.append(Issue('error', 'rule_invalid', 'rules', 'a delivery buffer cannot be negative',
                                        entity='rule', key=rid))
                    break
                previous = priorities.get((op_id, 'buffer'))
                if previous is not None and previous[0] != rule['value']:
                    issues.append(Issue('error', 'rule_conflict', 'rules',
                                        f'{previous[1]} and {rid} set different delivery buffers on {op_id}',
                                        entity='operation', key=op_id))
                    continue
                priorities[(op_id, 'buffer')] = (rule['value'], rid)
                if buffers is not None:
                    buffers[op_id] = rule['value']
            else:
                field = 'priority' if rule['action'] == 'set_priority' else 'customer_priority'
                previous = priorities.get((op_id, field))
                if previous is not None and previous[0] != rule['value']:
                    issues.append(Issue('error', 'rule_conflict', 'rules',
                                        f'{previous[1]} and {rid} set different {field} on {op_id}', entity='operation',
                                        key=op_id))
                    continue
                priorities[(op_id, field)] = (rule['value'], rid)
                op[field] = rule['value']
        applied.append({'rule': rid, 'action': rule['action'], 'value': rule['value'], 'operations': hits,
                        'confirmed_by': rule['confirmed_by']})
    return issues, {'applied': applied, 'skipped': skipped}
