"""File materiali SIMULATO per CLIENTE, in formato canonico (material_supply.csv + material_need.csv).

La fonte AS400 (FACTO00F) non e' ancora disponibile: questo file la sostituisce SOLO per le prove.
Tutto cio' che non viene dal database Factory e' marcato SIM- nell'id e 'SIMULATO' nel riferimento.

 - giacenza (L_MLPA, magazzini abilitati)            -> supply kind=stock, available_at = inizio piano
 - righe ordine fornitore 2026 aperte (L_OFPA)       -> supply kind=purchase_order, available_at = data promessa
 - formato senza giacenza ne' ordine (SIMULATO)      -> supply kind=purchase_order, available_at = inizio + 7 giorni
 - fabbisogno: per ogni nesting del piano, le lamiere richieste, sul primo membro (evita doppio conteggio)

Uso: python build_materiali_simulati.py <engine-scenario-nesting.json> <out_dir>
"""
import collections
import csv
import datetime as dt
import json
import os
import sys

OUT = os.environ.get('CLIENTE_EXTRACT_OUT') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')


def _csv(*p):
    return list(csv.DictReader(open(os.path.join(OUT, *p), encoding='utf-8')))


def main(src, dst):
    s = json.load(open(src, encoding='utf-8'))
    start = dt.datetime.fromisoformat(s['start'])
    nests = s['nestings']
    wp_of = {n['id'].replace('NEST-', ''): n for n in nests}
    sheets = collections.defaultdict(dict)  # workplanId -> {sheetPartCode: sheetsRequired}
    for r in _csv('12_nesting_lamiere', '1.csv'):
        if r['workplanId'] in wp_of and r['sheetPartCode']:
            sheets[r['workplanId']][r['sheetPartCode']] = float(r['sheetsRequired'] or 0)
    items = sorted({p for d in sheets.values() for p in d})
    stock = {r['sheetPartCode']: float(r['stockAvailable'] or 0) for r in _csv('13_materiali_giacenze_arrivi', '1.csv')}
    orders = collections.defaultdict(list)
    for r in _csv('13_materiali_giacenze_arrivi', '2.csv'):
        if r['promisedDate'] and r['promisedDate'][:4] >= '2026' and r['partCode'] in items:
            orders[r['partCode']].append(r)
    supply = []
    for it in items:
        if stock.get(it, 0) > 0:
            supply.append((f'STK-{it}', it, stock[it], start.isoformat(), 'stock', 'Factory L_MLPA'))
        for r in orders.get(it, ()):
            d = dt.datetime.fromisoformat(r['promisedDate']).replace(tzinfo=start.tzinfo)
            supply.append((f"PO-{r['purchaseOrderCode']}-{r['rowNo']}", it, float(r['qty'] or 0),
                           max(start, d).isoformat(), 'purchase_order', f"Factory L_OFPA {r['purchaseOrderCode']}"))
        if stock.get(it, 0) <= 0 and not orders.get(it):
            tot = sum(d.get(it, 0) for d in sheets.values())
            supply.append((f'SIM-{it}', it, max(tot, 1), (start + dt.timedelta(days=7)).isoformat(), 'purchase_order', 'SIMULATO'))
    need = []
    for w, d in sheets.items():
        first = wp_of[w]['operation_ids'][0]
        for it, q in sorted(d.items()):
            need.append((first, it, q if q > 0 else 1))
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, 'material_supply.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['id', 'item_id', 'quantity', 'available_at', 'kind', 'reference'])
        w.writerows(supply)
    with open(os.path.join(dst, 'material_need.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['operation_id', 'item_id', 'quantity'])
        w.writerows(need)
    k = collections.Counter(x[4] if not x[0].startswith('SIM-') else 'simulato' for x in supply)
    print('formati', len(items), 'supply', len(supply), dict(k), 'need', len(need))


if __name__ == '__main__':
    main(*sys.argv[1:])
