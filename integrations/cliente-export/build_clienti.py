"""Cliente delle commesse nel bundle canonico Factory: order.customer_id da A_COM.CTCOD.

export-canonical parte dallo scenario motore, che porta solo le fasi: ogni commessa esce col solo ``id``
(decompose.py) e il cliente si perde. Qui si riprende dalla stessa estrazione Factory (``customerCode`` di
export_factory_scenario.py) e si scrive nel campo canonico ``customer_id``, cosi' perimetro, regole con ambito
cliente, Control Room e assistente lo vedono. La ragione sociale resta in descrizioni.json (testo di
presentazione). Una commessa senza cliente in Factory resta senza: nessun valore inventato, il conteggio e'
stampato.

Uso:  python build_clienti.py <factory-bundle.json> <scenario Factory>
"""
import json
import sys


def main(bundle_path, scenario_path):
    with open(bundle_path, encoding='utf-8') as f:
        bundle = json.load(f)
    with open(scenario_path, encoding='utf-8') as f:
        scenario = json.load(f)
    customers = {str(o['orderId']): str(o['customerCode']).strip() for o in scenario.get('orders', [])
                 if o.get('customerCode') is not None and str(o['customerCode']).strip()}
    missing = []
    for r in bundle['records']['order']:
        if r['id'] in customers:
            r['customer_id'] = customers[r['id']]
        else:
            r.pop('customer_id', None)
            missing.append(r['id'])
    with open(bundle_path, 'w', encoding='utf-8') as f:
        json.dump(bundle, f, ensure_ascii=False)
    orders = bundle['records']['order']
    print(f'clienti: {len(orders) - len(missing)} commesse su {len(orders)} con cliente, '
          f'{len({r["customer_id"] for r in orders if "customer_id" in r})} clienti distinti'
          + (f'; senza cliente in Factory: {", ".join(missing[:10])}' + (' …' if len(missing) > 10 else '') if missing else ''))
    return missing


if __name__ == '__main__':
    if len(sys.argv) != 3:
        raise SystemExit(__doc__.strip().splitlines()[-1])
    main(*sys.argv[1:])
