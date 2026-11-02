"""Check/export the complete canonical-to-workbook field map without client data."""
import argparse
from pathlib import Path

from planning_factory.adapters.workbook_io import catalogue
from planning_factory.canonical.entities import ENTITIES

TARGET = Path(__file__).resolve().parents[1] / 'docs/workbook-field-coverage.md'


def render():
    model = catalogue()
    mappings = [f for f in model['fields'] if f['mapping'] and f['mapping'].split('.')[0] in ENTITIES]
    expected = {f'{e.name}.{f.name}' for e in ENTITIES.values() for f in e.fields}
    if len(mappings) != len(expected) or {f['mapping'] for f in mappings} != expected:
        raise SystemExit('Canonical workbook coverage is missing or duplicated')
    lines = ['# Copertura campi del workbook', '',
             'Generato con `python tools/workbook_coverage.py`; verificare con `--check`.', '',
             f"{len(model['tables'])} tabelle e {len(model['fields'])} campi raccolti; "
             f'{len(expected)} campi canonici hanno esattamente una collocazione.', '',
             'La mappatura non implica che ogni estensione sia calcolabile. Limiti e',
             'distinzione fra dati operativi e contesto: [planning-workbook.md](planning-workbook.md).', '',
             '| Campo canonico | Tabella Excel | Colonna stabile | Unità Excel |',
             '| --- | --- | --- | --- |']
    tables = {t['code']:t for t in model['tables']}
    for field in sorted(mappings, key=lambda f:f['mapping']):
        lines.append(f"| `{field['mapping']}` | {tables[field['table']]['sheet']} | "
                     f"`{field['code']}` | {field['unit'] or '—'} |")
    lines += ['', 'La domanda `operation_demand` è espansa dalla configurazione condivisa',
              '`phase_group` → `resource_group` → `group_demand`; non viene duplicata su',
              'ogni fase nell’Excel. I tempi canonici in secondi sono esposti in minuti.', '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    content = render()
    if args.check:
        if not TARGET.exists() or TARGET.read_text(encoding='utf-8') != content:
            raise SystemExit('Workbook field coverage is stale')
    else:
        TARGET.write_text(content, encoding='utf-8')


if __name__ == '__main__':
    main()
