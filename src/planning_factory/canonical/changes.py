"""Change events between two builds (#84, generic): which canonical domains changed and whether to recompute.

Domain digests depend on content only, so re-reading unchanged data from a source does
not trigger a recomputation, while a modified nesting program, a new material date or
a progress update does. The plans before and after are then compared with ``compare``.
"""


def manifest_changes(old: dict, new: dict) -> dict:
    before, after = old.get('domains', {}), new.get('domains', {})
    changed = sorted(d for d in set(before) & set(after) if before[d]['digest'] != after[d]['digest'])
    added, removed = sorted(set(after) - set(before)), sorted(set(before) - set(after))
    result = {
        'installation': new.get('installation'),
        'from': {'built_at': old.get('built_at'), 'scenario_digest': old.get('scenario_digest')},
        'to': {'built_at': new.get('built_at'), 'scenario_digest': new.get('scenario_digest')},
        'changed_domains': changed, 'added_domains': added, 'removed_domains': removed,
        'binding_changed': old.get('binding_digest') != new.get('binding_digest'),
        'profile_changed': old.get('profile') != new.get('profile'),
        'horizon_changed': old.get('horizon') != new.get('horizon'),
        'materials_changed': old.get('materials') != new.get('materials'),
        'scenario_changed': old.get('scenario_digest') != new.get('scenario_digest'),
    }
    result['recompute'] = bool(changed or added or removed or result['scenario_changed'] or result['binding_changed'])
    return result
