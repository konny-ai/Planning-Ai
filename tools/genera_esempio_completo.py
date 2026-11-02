"""Genera l'Excel di esempio con dati sintetici piu' ricchi di bending.xlsx.

Serve a far vedere la Control Room "piena" senza usare dati aziendali: 10 macchine,
48 commesse, 240 fasi con alternative di macchina, precedenze e scadenze diverse.
I dati sono inventati e ripetibili (seme fisso).

Uso (dalla radice del repository):
    python tools/genera_esempio_completo.py
Scrive examples/planning-workbook/esempio-completo.xlsx.
"""
from dataclasses import replace
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from planning_factory.adapters.planning_workbook import export_scenario
from planning_factory.domain.models import Alternative, Calendar, Demand, Dependency, Resource
from tools.stress_kit import make_scenario

MACCHINE = ('Laser-1', 'Laser-2', 'Punzonatrice-1', 'Piega-1', 'Piega-2',
            'Saldatura-1', 'Saldatura-2', 'Verniciatura', 'Assemblaggio-1', 'Assemblaggio-2')


def main():
    base = make_scenario(seed=7, operations=240, machines=len(MACCHINE), horizon_days=45)
    rename = {f'm{i:02d}': nome for i, nome in enumerate(MACCHINE)}
    commesse = {}

    def commessa(vecchia):
        return commesse.setdefault(vecchia, f'C-{1001 + len(commesse)}')

    def fase(vecchia):
        numero = int(vecchia.rsplit('-', 1)[1])
        return f'{commessa(f"order-{numero // 5:04d}")}/{numero % 5 + 1:02d}'

    operazioni = tuple(replace(
        op,
        id=fase(op.id),
        order_id=commessa(op.order_id),
        alternatives=tuple(Alternative(tuple(Demand(rename[d.resource_id]) for d in alt.demands))
                           for alt in op.alternatives),
        predecessors=tuple(replace(dep, predecessor_id=fase(dep.predecessor_id)) for dep in op.predecessors),
    ) for op in base.operations)
    risorse = tuple(Resource(rename[r.id], r.calendar_id) for r in base.resources)
    scenario = replace(base, id='ESEMPIO-COMPLETO', resources=risorse, operations=operazioni)
    destinazione = Path(__file__).resolve().parents[1] / 'examples' / 'planning-workbook' / 'esempio-completo.xlsx'
    export_scenario(scenario, destinazione, extracted_at='2026-10-19T00:00:00Z', data_nature='simulati')
    print(destinazione)


if __name__ == '__main__':
    main()
