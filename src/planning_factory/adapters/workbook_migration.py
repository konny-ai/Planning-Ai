"""Materialize an effective canonical snapshot and its instance inputs in Excel.

No live connectors or instance writes. Revoked correction history belongs to
the archived instance; the inputs and evidence still in force travel with the
workbook. The caller must supply real units for material quantities.
"""
from copy import deepcopy
from decimal import Decimal

from planning_factory.adapters.planning_workbook import tables_from_records
from planning_factory.canonical.entities import ENTITIES
from planning_factory.domain.validation import ValidationError


def snapshot_tables(snapshot, *, timezone, material_items=(), resource_parameters=()):
    manifest = snapshot['manifest']
    records = deepcopy(snapshot['records'])
    entries = snapshot.get('company_rules', ())
    for entry in entries:
        rule = entry['rule']
        if entry['status'] == 'confirmed' and rule['action'] not in ('note', 'ignore_material'):
            if any(r['id'] == rule['id'] for r in records.get('rule', ())):
                raise ValidationError(['duplicate canonical/company rule id'])
            records.setdefault('rule', []).append(rule)
    tables = tables_from_records(records, scenario_id=manifest['scenario_id'],
        start=manifest['horizon']['start'], end=manifest['horizon']['end'],
        extracted_at=snapshot['built_at'], profile_id=manifest['profile'],
        dataset_id=manifest['installation'], domain_options=snapshot['options'])
    tables['scenario'][0].update(timezone=timezone,
        description='Fotografia completa dei dati di pianificazione; correzioni già materializzate.')
    by_id={r['id']:r for r in tables['operation']}
    for row in resource_parameters:
        if row['id'] not in by_id:
            raise ValidationError(['resource parameters outside the migrated operation scope'])
        by_id[row['id']].update({k:v for k,v in row.items() if k!='id'})
    # A migration cannot make an old extraction appear fresh.
    tables['source'] = []
    for domain, info in manifest['domains'].items():
        batches = info.get('sources', ())
        if isinstance(batches, dict):
            batches = list(batches.values())
        dates = [r.get('extracted_at') for r in batches if isinstance(r, dict) and r.get('extracted_at')]
        extracted = min(dates) if dates else info.get('extracted_at')
        if not extracted:
            raise ValidationError([f'{domain}: extraction timestamp required for migration'])
        sid = 'migration:' + domain
        tables['source'].append(dict(id=sid, system='Snapshot canonico e istanza locale',
            environment=manifest['installation'], extracted_at=extracted,
            data_nature='misti', content_hash=info['digest']))
        tables['domain_source'].append(dict(domain=domain, source_id=sid))
    sid = tables['source'][0]['id']
    for c in snapshot.get('corrections', ()):
        key = dict(zip(ENTITIES[c['entity']].key, c['key']))
        tables['provenance'].append(dict(id=f"CORRECTION:{c['id']}", entity=c['entity'],
            record_key=key, source_id=sid, external_key=str(c['id']), original_value=c,
            transformation='correzione_gia_materializzata', data_nature='dichiarato'))
        if c['action'] == 'exclude':
            tables['correction'].append(dict(id=str(c['id']), dataset_id=manifest['installation'],
                entity=c['entity'], record_key=key, field='*', action='escludi', status='attiva',
                reason=c['reason'], actor=c['actor'], at=c['created_at']))
        else:
            # Each changed/inserted value has one readable history row. The
            # original action is preserved above; effective inputs are never reapplied.
            for field, value in c['values'].items():
                if field in ENTITIES[c['entity']].key:
                    continue
                if field.endswith('_seconds') and value is not None:
                    value = Decimal(value) / 60
                tables['correction'].append(dict(id=f"{c['id']}:{field}", dataset_id=manifest['installation'],
                    entity=c['entity'], record_key=key, field=field,
                    action='imposta_null' if value is None else 'sostituisci', new_value=value,
                    status='attiva', reason=c['reason'], actor=c['actor'], at=c['created_at']))
    for entry in entries:
        rule = entry['rule']
        tables['provenance'].append(dict(id='MEMORY:'+entry['id'], entity='rule',
            record_key={'id':entry['id']}, source_id=sid, original_value=entry,
            transformation='memoria_istanza_migrata', data_nature='dichiarato'))
        if rule['action'] not in ('note', 'ignore_material'):
            continue
        values = {k:v for k,v in rule.items() if k in ('id','action','note','valid_from','valid_to','confirmed_by','confirmed_at')}
        scopes = [(k,rule[k]) for k in ('order_id','resource_id','customer_id','material','min_thickness_mm','max_thickness_mm') if rule.get(k) is not None]
        if len(scopes) > 1:
            raise ValidationError(['guidance with multiple scopes needs an explicit workbook mapping'])
        if scopes:
            values.update(scope_entity=scopes[0][0], scope_key=scopes[0][1])
        values['status'] = {'confirmed':'confermata','draft':'bozza','revoked':'revocata'}[entry['status']]
        tables['guidance'].append(values)
        if rule['action'] == 'ignore_material' and entry['status'] == 'confirmed':
            policy = next(p for p in tables['domain_policy'] if p['domain']=='materials')
            policy['material_policy'] = 'ignorati_con_regola'
    descriptions = snapshot.get('descriptions') or {}
    customers = {}
    for oid, row in descriptions.get('orders', {}).items():
        if row.get('customer_code') and row.get('customer'):
            customers[row['customer_code']] = row['customer']
    tables['customer'] = [dict(id=k,name=v) for k,v in sorted(customers.items())]
    for code, label in (('order','orders'),('operation','operations'),('resource','resources'),('nest','nests')):
        lookup = descriptions.get(label, {})
        for row in tables.get(code, ()):
            text = lookup.get(row['id'])
            if not text:
                continue
            if code in ('operation','resource'):
                description = text.get('name') if code=='resource' else text.get('phase')
                if description:
                    row['description'] = description
            if code == 'operation':
                if text.get('phase_code'):
                    row['phase_type'] = text['phase_code']
                if text.get('sequence') is not None:
                    row['display_sequence'] = str(text['sequence'])
                if text.get('uom'):
                    row['uom_id'] = text['uom']
            tables['provenance'].append(dict(id=f'DISPLAY:{code}:{row["id"]}',entity=code,
                record_key={'id':row['id']},source_id=sid,original_value=text,
                transformation='display_context',data_nature='dichiarato'))
    items = {r['id']:dict(r) for r in material_items}
    needed = {r['item_id'] for code in ('material_need','material_supply') for r in tables[code]}
    for item_id in needed:
        if not items.get(item_id,{}).get('base_uom'):
            raise ValidationError([f'{item_id}: actual material unit required; no inferred kg/pieces'])
    tables['item'] = list(items.values())
    for code in ('material_supply','material_need'):
        for row in tables[code]:
            row['uom_id']=items[row['item_id']]['base_uom']
    units = {r['base_uom'] for r in items.values()} | {
        r['uom_id'] for r in tables['operation'] if r.get('uom_id')}
    tables['uom'] = [dict(id=u,description=u) for u in sorted(units)]
    return tables
