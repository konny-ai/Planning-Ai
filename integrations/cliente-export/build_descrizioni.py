"""Descrizioni di presentazione per la Control Room: nomi e testi letti da Factory, mai dal motore.

Il modello canonico porta solo identificativi e dati fisici (ADR 004): una macchina e' ``MAC:Panto.2``, una
commessa e' l'ID interno ``609607``. Per leggere il piano servono i testi che il planner conosce: nome della
macchina, codice commessa e cliente, articolo, materiale e spessore, fase, codice del programma di nesting.
Questo script li ricava dalla stessa estrazione Factory (scenario.json di export_factory_scenario.py) e dai CSV
dei programmi CAM (10_nesting_programmi, 12_nesting_lamiere), solo per gli ID presenti nello scenario
canonico. Il file ``descrizioni.json`` sta accanto al binding: la Control Room lo mostra, il motore non lo
legge e l'impronta dello scenario non cambia. Un testo assente resta assente: nessun valore inventato.

Uso:  python build_descrizioni.py <scenario Factory> <scenario canonico> <cartella out CSV> <descrizioni.json>
      [--cliente NOME] [--nota-materiali TESTO]
"""
import argparse
import csv
import json
import os
from decimal import Decimal, InvalidOperation

SCHEMA = 'planning-factory/descrizioni-v1'


def text(value):
    if value is None:
        return None
    value = ' '.join(str(value).split())
    return value or None


def number(value):
    """Decimale normalizzato come stringa ('12', '2.5'), None se assente, zero o non numerico."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not d.is_finite() or d <= 0:
        return None
    return format(d.normalize(), 'f')


def compact(row):
    return {k: v for k, v in row.items() if v not in (None, '', [], {})}


def read_csv(folder, name):
    path = os.path.join(folder, name, '1.csv')
    if not os.path.exists(path):
        return []
    with open(path, encoding='utf-8-sig', newline='') as fh:
        return list(csv.DictReader(fh))


def build(factory, canonical, csv_dir, customer_name=None, materials_note=None):
    used_resources = {r['id'] for r in canonical['resources']}
    used_ops = {o['id'] for o in canonical['operations']}
    used_orders = {o['order_id'] for o in canonical['operations'] if o.get('order_id')}
    used_nests = {n['id'] for n in canonical.get('nestings', [])}

    departments = {}
    for m in factory.get('departmentMembers', []):
        departments.setdefault((m['resourceKind'], m['resourceCode']), m['departmentCode'])
    resources = {}
    for prefix, kind, rows, key in (('MAC:', 'MACHINE', factory.get('machines', []), 'code'),
                                    ('OP:', 'OPERATOR', factory.get('operators', []), 'code'),
                                    ('SUP:', 'SUPPLIER', factory.get('suppliers', []), 'code')):
        for r in rows:
            rid = prefix + str(r[key])
            if rid in used_resources:
                resources[rid] = compact({'code': str(r[key]), 'name': text(r.get('name')),
                                          'group': text(r.get('groupCode')) or departments.get((kind, str(r[key])))})

    customers = {c['code']: text(c.get('name')) for c in factory.get('customers', [])}
    lines = {}
    for line in factory.get('orderLines', []):
        lines.setdefault(str(line.get('orderId')), []).append(line)
    orders = {}
    for o in factory.get('orders', []):
        oid = str(o['orderId'])
        if oid in used_orders:
            rows = lines.get(oid, [])
            dues = [r['dueDate'] for r in rows if r.get('dueDate')]
            requested = [r['requestedDate'] for r in rows if r.get('requestedDate')]
            orders[oid] = compact({'code': text(o.get('code')), 'customer': customers.get(o.get('customerCode')),
                                   'customer_code': text(o.get('customerCode')), 'description': text(o.get('description')),
                                   'reference': text(o.get('reference')),
                                   # Date e totali delle righe d'ordine: solo da mostrare, il motore usa le fasi.
                                   'issue_date': text(o.get('issueDate')), 'due_min': min(dues) if dues else None,
                                   'due_max': max(dues) if dues else None, 'requested_min': min(requested) if requested else None,
                                   'lines': len(rows) or None})

    lots = {(l['orderId'], l['lotId']): l for l in factory.get('lots', [])}
    # Riga d'ordine di ogni lotto finale: numero riga e, se il lotto non ha testo, la descrizione della riga.
    line_of = {(l['orderId'], l['finalLotId']): l for l in factory.get('orderLines', []) if l.get('finalLotId') is not None}
    # Stato di riga (L_CMPA.STCON) -> testo di A_STA, letto da 16_stati: un codice senza testo resta senza stato.
    states = {r['statusCode']: text(r['description']) for r in read_csv(csv_dir, '16_stati')}
    operations = {}
    for o in factory.get('operations', []):
        oid = f"{o['orderId']}/{o['lotId']}/{o['phaseId']}"
        if oid not in used_ops:
            continue
        lot = lots.get((o['orderId'], o['lotId']), {})
        size = [number(lot.get('dimX')), number(lot.get('dimY'))]
        line = line_of.get((o['orderId'], o['lotId']), {})
        operations[oid] = compact({
            'phase': text(o.get('description')) or text(o.get('phaseCode')),
            'phase_code': text(o.get('phaseCode')), 'sequence': o.get('sequence'),
            'lot': o['lotId'], 'line': line.get('lineNumber'),
            # Riga d'ordine (L_CMPA) e lotto (A_LOT): quantita' ordinata, quantita' del lotto e consegna della riga.
            'row_quantity': number(line.get('qtyOrdered')), 'row_due': text(line.get('dueDate')),
            'lot_quantity': number(lot.get('qty')),
            'row_status': states.get(str(line.get('deliveryStatusCode'))),
            'part': text(lot.get('partCode')) or text(line.get('partCode')),
            'part_description': text(lot.get('partDescription')) or text(line.get('partDescription')),
            'material': text(lot.get('materialCode')), 'thickness_mm': number(lot.get('dimZ')),
            'size_mm': '×'.join(size) if all(size) else None,
            'quantity': number(o.get('qty')), 'uom': text(lot.get('uom')),
            'external': o.get('executionCode') not in (None, 'I'),
        })
        if operations[oid].get('external') is False:
            del operations[oid]['external']

    programs = {r['workplanId']: r for r in read_csv(csv_dir, '10_nesting_programmi')}
    sheets = {}
    for r in read_csv(csv_dir, '12_nesting_lamiere'):
        sheets.setdefault(r['workplanId'], []).append(r)
    nests = {}
    for nid in sorted(used_nests):
        wid = nid.removeprefix('NEST-')
        p = programs.get(wid, {})
        rows = sheets.get(wid, [])
        first = rows[0] if rows else {}
        size = [number(first.get('widthMm')), number(first.get('lengthMm'))]
        used = number(first.get('usedPct'))
        nests[nid] = compact({
            'code': text(p.get('code')) or text(p.get('description')),
            'machine': text(p.get('machineCode')),
            'sheet': text(first.get('sheetDescription')), 'sheet_part': text(first.get('sheetPartCode')),
            'material': text(first.get('material')), 'thickness_mm': number(first.get('thicknessMm')),
            'size_mm': '×'.join(size) if all(size) else None,
            'sheets': sum(int(Decimal(r['sheetsRequired'])) for r in rows if number(r.get('sheetsRequired'))) or None,
            'used_pct': used, 'warehouse': text(first.get('warehouse')), 'location': text(first.get('location')),
        })

    source = factory.get('source', {})
    return {
        'schema': SCHEMA,
        'note': 'Testi di presentazione letti da Factory: non entrano nel motore e non cambiano lo scenario.',
        'installation': compact({'customer': text(customer_name)}),
        'source': compact({'system': text(source.get('system')), 'database': text(source.get('database')),
                           'captured_at': text(source.get('capturedAt')),
                           'planning_start': text(factory.get('planning', {}).get('start')),
                           'materials_note': text(materials_note)}),
        'resources': resources, 'orders': orders, 'operations': operations, 'nests': nests,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('factory_scenario')
    parser.add_argument('canonical_scenario')
    parser.add_argument('csv_dir')
    parser.add_argument('out')
    parser.add_argument('--cliente', help='nome del cliente mostrato in testata (configurazione, non dato Factory)')
    parser.add_argument('--nota-materiali', help="avvertenza sulla fonte materiali mostrata in testata (es. 'simulati')")
    args = parser.parse_args()
    with open(args.factory_scenario, encoding='utf-8') as fh:
        factory = json.load(fh)
    with open(args.canonical_scenario, encoding='utf-8') as fh:
        canonical = json.load(fh)
    result = build(factory, canonical, args.csv_dir, args.cliente, args.nota_materiali)
    with open(args.out, 'w', encoding='utf-8') as fh:
        json.dump(result, fh, ensure_ascii=False, separators=(',', ':'), sort_keys=True)
    counts = {k: len(result[k]) for k in ('resources', 'orders', 'operations', 'nests')}
    print('descrizioni:', ', '.join(f'{k} {v}' for k, v in counts.items()))


if __name__ == '__main__':
    main()
