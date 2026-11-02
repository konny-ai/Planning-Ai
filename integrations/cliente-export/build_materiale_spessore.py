"""Materiale e spessore di fasi e programmi di nesting nel bundle canonico Factory.

export-canonical parte dallo scenario motore, che non porta attributi di lamiera: fasi e programmi escono
senza ``material`` e ``thickness_mm`` (decompose.py). Per taglio laser, ossitaglio, plasma, punzonatura e
nesting sono i dati base: regole con ambito materiale/spessore, Control Room e assistente li devono vedere
come campi canonici, non solo come testo di presentazione. Qui si riprendono dalle stesse fonti di
build_descrizioni.py (fase: lotto A_LOT, codice materiale e dimensione Z; programma: lamiera Alma della
prima riga di 12_nesting_lamiere) e si scrivono nei campi canonici ``operation`` e ``nest``. Un dato
assente in Factory resta assente: nessun valore inventato, i conteggi sono stampati.

Uso:  python build_materiale_spessore.py <factory-bundle.json> <scenario Factory> <cartella out CSV>
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_descrizioni import build  # noqa: E402


def main(bundle_path, scenario_path, csv_dir):
    with open(bundle_path, encoding='utf-8') as f:
        bundle = json.load(f)
    with open(scenario_path, encoding='utf-8') as f:
        factory = json.load(f)
    records = bundle['records']
    canonical = {'resources': records.get('resource', []), 'operations': records['operation'],
                 'nestings': records.get('nest', [])}
    described = build(factory, canonical, csv_dir)
    counts = {}
    for entity, texts in (('operation', described['operations']), ('nest', described['nests'])):
        rows = records.get(entity, [])
        found = {'material': 0, 'thickness_mm': 0}
        for r in rows:
            text = texts.get(r['id'], {})
            for field in found:
                if text.get(field) is not None:
                    r[field] = text[field]
                    found[field] += 1
                else:
                    r.pop(field, None)
        counts[entity] = (len(rows), found)
    with open(bundle_path, 'w', encoding='utf-8') as f:
        json.dump(bundle, f, ensure_ascii=False)
    for entity, (total, found) in counts.items():
        print(f'materiale/spessore {entity}: {found["material"]} con materiale, {found["thickness_mm"]} con spessore '
              f'su {total}')
    return counts


if __name__ == '__main__':
    if len(sys.argv) != 4:
        raise SystemExit(__doc__.strip().splitlines()[-1])
    main(*sys.argv[1:])
