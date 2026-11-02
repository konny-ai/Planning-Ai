"""Anonimizza lo scenario.json completo di planning-engine-spec (contratto ricco, tutte le entita').

Uso: python anonymize_scenario.py scenario.json anonymization-map.json out.json
La mappa (da to_engine_v02.py --anonymize) viene estesa con clienti e articoli e riscritta.
- commesse, operatori, fornitori, macchine, clienti, articoli: codici sostituiti in modo coerente;
- nomi, descrizioni e riferimenti testuali: rimossi (null) o sostituiti dal codice anonimo;
- numeri di ordine d'acquisto/ODL, date, quantita', tempi, stati e chiavi numeriche restano invariati.
"""
import json, sys

DROP = {'description', 'partDescription', 'reference'}


def main(src, mapping, dst, label='BANCO-A'):
    s = json.load(open(src, encoding='utf-8'))
    m = json.load(open(mapping, encoding='utf-8'))
    for k in ('CUSTOMER', 'PART', 'DEPARTMENT'):
        m.setdefault(k, {})

    def get(kind, code, prefix):
        if code is None:
            return None
        code = str(code)
        table = m[kind]
        if code not in table:
            table[code] = f'{prefix}{len(table) + 1:04d}'
        return table[code]

    for c in sorted({c['code'] for c in s['customers']}):
        get('CUSTOMER', c, 'CUS')
    parts = set()
    for coll in ('orderLines', 'lots', 'materialArrivals', 'lotComponents'):
        parts |= {r['partCode'] for r in s[coll] if r.get('partCode')}
    for p in sorted(parts):
        get('PART', p, 'ART')
    kind_map = {'OPERATOR': 'OPERATOR', 'SUPPLIER': 'SUPPLIER', 'MACHINE': 'MACHINE'}

    def res(kind, code):
        k = kind_map.get(kind)
        return m[k].get(str(code), get(k, code, k[:3])) if k else code

    for key, rows in s.items():
        if not isinstance(rows, list):
            continue
        for r in rows:
            for f in DROP & set(r):
                r[f] = None
            if 'orderId' in r:
                r['orderId'] = m['ORDER'][str(r['orderId'])]
            if 'partCode' in r and r['partCode']:
                r['partCode'] = get('PART', r['partCode'], 'ART')
            if 'supplierCode' in r and r['supplierCode']:
                r['supplierCode'] = res('SUPPLIER', r['supplierCode'])
            if 'customerCode' in r and r['customerCode']:
                r['customerCode'] = get('CUSTOMER', r['customerCode'], 'CUS')
            if 'resourceCode' in r and 'resourceKind' in r:
                r['resourceCode'] = res(r['resourceKind'], r['resourceCode'])
            if r.get('operatorCode'):
                r['operatorCode'] = res('OPERATOR', r['operatorCode'])
            if r.get('currentOperatorCode'):
                r['currentOperatorCode'] = res('OPERATOR', r['currentOperatorCode'])
            if r.get('currentMachineCode'):
                r['currentMachineCode'] = res('MACHINE', r['currentMachineCode'])
            if key == 'departmentMembers':
                r['departmentCode'] = get('DEPARTMENT', r['departmentCode'], 'REP')
    for r in s['operators']:
        r['code'] = res('OPERATOR', r['code']); r['name'] = r['code']
    for r in s['suppliers']:
        r['code'] = res('SUPPLIER', r['code']); r['name'] = r['code']
    for r in s['machines']:
        r['code'] = res('MACHINE', r['code']); r['name'] = r['code']
    for r in s['customers']:
        r['code'] = get('CUSTOMER', r['code'], 'CUS'); r['name'] = r['code']
    for r in s['departments']:
        r['code'] = get('DEPARTMENT', r['code'], 'REP'); r['name'] = r['code']; r['shortName'] = None
    for r in s['orders']:
        r['code'] = r['orderId']
    s['scenarioId'] = f"factory-bench-a-{s['planning']['start'][:10]}"
    s['source'].update(server=None, database=label)
    s['planning']['extraction']['database'] = label
    json.dump(s, open(dst, 'w', encoding='utf-8', newline='\n'), ensure_ascii=False, indent=1)
    json.dump(m, open(mapping, 'w', encoding='utf-8', newline='\n'), ensure_ascii=False, indent=1)


if __name__ == '__main__':
    main(*sys.argv[1:4])
