"""Avanzamento TRD nel perimetro del piano: operation_progress.csv canonico + diagnostica, a tempi o a pezzi.

15_avanzamento_trd.sql estrae i consuntivi di tutte le fasi aperte. Lo scenario contiene solo le fasi che le
regole CLIENTE mandano in pianificazione: le altre (tipo 66, durata zero, FAPNF='N' fuori nesting) non sono
"sconosciute" ma fuori perimetro. Non si scartano in silenzio: finiscono in avanzamento_diagnostica.csv con
il motivo, e i conteggi vengono stampati.

Politica (residual_policy del dominio progress, parametro -Progress di run_cliente_locale.ps1):
  time      residuo = durata teorica - secondi registrati sui ticket;
  quantity  residuo = (pezzi da fare - pezzi prodotti) x tempo unitario, setup ridotto dai secondi di setup.
            Serve operation.quantity: si aggiunge al bundle Factory da LAQTP (stessa estrazione, nessun valore
            inventato). Una fase con tutti i pezzi prodotti ma ancora aperta in Factory ha residuo zero, come nel
            legacy a pezzi: e' riportata completata alla fine dell'ultima attivita' TRD (GetJobEndTime) ed elencata
            in diagnostica come fase da chiudere in Factory. Se la fase e' membro di un nesting ancora aperto non
            si chiude (il motore non ammette fasi completate in un nesting attivo): resta col programma.

Uso:  python build_avanzamento_trd.py <factory-bundle.json> <regole/scenario.json> <trd.csv> <out_dir> <time|quantity|none>
"""
import collections
import csv
import datetime as dt
import json
import os
import sys
from decimal import Decimal
from zoneinfo import ZoneInfo

CANONICAL = ('operation_id', 'status', 'completed_at', 'produced_quantity', 'recorded_work_seconds',
             'recorded_setup_seconds', 'resource_id')


def reason(o):
    if o is None:
        return 'fase non estratta'
    if o['phaseTypeId'] == 66:
        return 'fuori perimetro: tipo fase 66 (Pulizia)'
    if o['toPlan'] != 'Y':
        return "fuori perimetro: non da pianificare (stato o FAPNF='N' fuori nesting)"
    if o['theoreticalTotalSec'] <= 0:
        return 'fuori perimetro: durata teorica zero'
    if o['executionCode'] != 'I':
        return 'fuori perimetro: fase esterna'
    return 'fuori perimetro: non convertita dal motore'


def main(bundle_path, scenario_path, trd_path, out_dir, policy):
    if policy not in ('time', 'quantity', 'none'):
        raise SystemExit('politica: time, quantity o none')
    with open(bundle_path, encoding='utf-8') as f:
        bundle = json.load(f)
    with open(scenario_path, encoding='utf-8') as f:
        scenario = json.load(f)
    ops = {f"{o['orderId']}/{o['lotId']}/{o['phaseId']}": o for o in scenario['operations']}
    tz = ZoneInfo(scenario.get('planning', {}).get('timeZone') or 'Europe/Rome')
    planned = {r['id']: r for r in bundle['records']['operation']}
    nested = {r['operation_id'] for r in bundle['records'].get('nest_member', [])}
    if policy == 'quantity':
        for key, r in planned.items():
            qty = ops.get(key, {}).get('qty')
            if qty and qty > 0:
                r['quantity'] = format(Decimal(str(qty)).normalize(), 'f')
        with open(bundle_path, 'w', encoding='utf-8') as f:
            json.dump(bundle, f, ensure_ascii=False)
    with open(trd_path, encoding='utf-8', newline='') as f:
        rows = list(csv.DictReader(f))
    kept, notes = [], []
    for r in rows:
        key = r['operation_id']
        if key not in planned:
            notes.append({**{k: r[k] for k in CANONICAL}, 'motivo': reason(ops.get(key))})
            continue
        row = {k: r[k] for k in CANONICAL}
        target = Decimal(r['planned_quantity'] or '0')
        if policy == 'quantity' and target > 0 and Decimal(r['produced_quantity']) >= target:
            if key in nested:
                # Il programma di nesting e' ancora aperto: la sua durata copre il taglio, la fase non si chiude.
                notes.append({**row, 'motivo': 'pezzi completati ma membro di un nesting aperto: '
                                               'pianificata con il programma'})
                continue
            if not r['last_activity_local']:
                notes.append({**row, 'motivo': 'pezzi completati senza data di fine attivita: pianificata intera'})
                continue
            end = dt.datetime.fromisoformat(r['last_activity_local']).replace(microsecond=0, tzinfo=tz)
            row.update(status='completed', completed_at=end.isoformat(), resource_id='')
            notes.append({**row, 'motivo': 'pezzi completati con fase aperta in Factory: completata nel piano '
                                           "alla fine dell'ultima attivita TRD, da chiudere in Factory"})
        kept.append(row)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, 'operation_progress.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, CANONICAL)
        w.writeheader()
        w.writerows(kept)
    with open(os.path.join(out_dir, 'avanzamento_diagnostica.csv'), 'w', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, [*CANONICAL, 'motivo'])
        w.writeheader()
        w.writerows(notes)
    print('avanzamento TRD', policy, len(rows), 'nel piano', len(kept),
          dict(collections.Counter(n['motivo'].split(':')[0] if n['motivo'].startswith('fuori') else n['motivo']
                                   for n in notes)))


if __name__ == '__main__':
    main(*sys.argv[1:])
