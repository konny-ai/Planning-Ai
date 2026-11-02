"""Anonimizza il golden del banco (jobs.tsv) con la mappa prodotta da to_engine_v02.py --anonymize.

Uso: python anonymize_bench.py jobs.tsv anonymization-map.json out.tsv
Mantiene solo le colonne utili al confronto; commesse, operatori, fornitori e macchine sono sostituiti.
"""
import csv, json, sys

COLS = ['CONUM', 'LOCOD', 'IDFAS', 'FASEQ', 'FAEXE', 'FPIAN', 'LOPRI', 'CMDTS', 'THEOR_SEC',
        'MACS', 'OPRS', 'SUPS', 'LOAD_SEQ', 'EXPECTED_START', 'EXPECTED_END']


def main(src, mapping, dst):
    m = json.load(open(mapping, encoding='utf-8'))
    def codes(kind, value):
        return ','.join(m[kind].get(c, 'UNKNOWN') for c in value.split(',') if c) if value else ''
    with open(src, encoding='utf-8') as f, open(dst, 'w', encoding='utf-8', newline='') as g:
        w = csv.writer(g, delimiter='\t', lineterminator='\n')
        w.writerow(COLS)
        for r in csv.DictReader(f, delimiter='\t'):
            r['CONUM'] = m['ORDER'][r['CONUM']]
            r['OPRS'] = codes('OPERATOR', r['OPRS'])
            r['SUPS'] = codes('SUPPLIER', r['SUPS'])
            r['MACS'] = codes('MACHINE', r['MACS'])
            w.writerow([r[c] for c in COLS])


if __name__ == '__main__':
    main(*sys.argv[1:4])
