"""Typed perimeter filters (#78 P15): which orders enter the scenario, for any source.

Replaces free SQL conditions (legacy ``WhereCond``) with typed criteria on the
canonical orders: ids, customers, due-date range. Records of excluded orders are
removed by following the reference annotations of the entity table, never by name.
A cut that changes the meaning of what remains is reported: a dependency from an
excluded operation blocks publication (the successor would otherwise start too early),
and a nesting program that keeps only part of its members is flagged.
"""
from .entities import ENTITIES
from .model import Issue

KEYS = ('order_ids', 'exclude_order_ids', 'customer_ids', 'exclude_customer_ids', 'due_from', 'due_to',
        'include_without_due')


def _kept(order: dict, p: dict) -> bool:
    if p.get('order_ids') is not None and order['id'] not in p['order_ids']:
        return False
    if order['id'] in p.get('exclude_order_ids', ()):
        return False
    customer = order.get('customer_id')
    if p.get('customer_ids') is not None and customer not in p['customer_ids']:
        return False
    if customer is not None and customer in p.get('exclude_customer_ids', ()):
        return False
    due = order.get('due')
    if due is None:
        return p.get('include_without_due', True) or (p.get('due_from') is None and p.get('due_to') is None)
    if p.get('due_from') is not None and due < p['due_from']:
        return False
    if p.get('due_to') is not None and due >= p['due_to']:
        return False
    return True


def apply_perimeter(records, perimeter: dict | None) -> tuple[list[Issue], dict | None]:
    if not perimeter:
        return [], None
    orders = {r['id'] for r in records['order'] if _kept(r, perimeter)}
    excluded_orders = {r['id'] for r in records['order']} - orders
    operations = {r['id'] for r in records['operation'] if r['order_id'] in orders}
    excluded_ops = {r['id'] for r in records['operation']} - operations
    issues = []
    for dep in records['operation_dependency']:
        if dep['operation_id'] in operations and dep['predecessor_id'] in excluded_ops:
            issues.append(Issue('block', 'perimeter_cut_dependency', 'routings',
                                f'predecessor {dep["predecessor_id"]} is outside the perimeter: the dependency is dropped',
                                entity='operation', key=dep['operation_id']))
    members = {}
    for m in records['nest_member']:
        members.setdefault(m['nest_id'], []).append(m['operation_id'] in operations)
    for nest_id, inside in sorted(members.items()):
        if any(inside) and not all(inside):
            issues.append(Issue('warn', 'perimeter_partial_nest', 'nesting',
                                'the program also cuts parts of orders outside the perimeter: shared time kept whole',
                                entity='nest', key=nest_id))
    removed = {}
    for entity, spec in ENTITIES.items():
        refs = [(f.name, f.ref) for f in spec.fields if f.ref in ('order', 'operation')]
        if not refs:
            continue
        kept = [r for r in records[entity]
                if not any((r.get(name) in excluded_orders) if ref == 'order' else (r.get(name) in excluded_ops)
                           for name, ref in refs)]
        if len(kept) != len(records[entity]):
            removed[entity] = len(records[entity]) - len(kept)
            records[entity] = kept
    nests_left = {m['nest_id'] for m in records['nest_member']}
    dropped_nests = [n['id'] for n in records['nest'] if n['id'] in members and n['id'] not in nests_left]
    if dropped_nests:
        records['nest'] = [n for n in records['nest'] if n['id'] not in dropped_nests]
        removed['nest'] = len(dropped_nests)
    return issues, {'orders_kept': len(orders), 'orders_excluded': sorted(excluded_orders), 'records_removed': removed}
