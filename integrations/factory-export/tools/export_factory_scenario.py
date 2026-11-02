"""Esporta uno scenario di pianificazione dal database Factory.

Esegue gli script di ../sql sulla stessa connessione (le tabelle temporanee
#U_* di 00_universo.sql restano visibili agli script successivi), converte i
result set nel formato canonico descritto in ../prd/10-contratto-scenario.md,
deriva le precedenze di ciclo e di distinta e scrive:

  <out>/scenario.json        scenario completo (schema ../schema/planning-scenario.schema.json)
  <out>/validation.md        controlli di coerenza e conteggi

Uso:
  python export_factory_scenario.py --database <DB_CLIENTE> --start 2026-10-19 \
      --horizon-days 120 --out ../data/private/<cliente> --customer-view

Richiede pyodbc e un driver ODBC per SQL Server. Autenticazione Windows
(Trusted_Connection) salvo --user/--password.
"""
import argparse
import datetime as dt
import decimal
import hashlib
import json
import os
import sys
from collections import defaultdict

SQL_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'sql')

# result set di ogni script, nell'ordine in cui li restituisce
RESULT_SETS = {
    '01_calendari.sql': ['calendarModels', 'calendarModelDays', 'dayTypeIntervals', 'holidays',
                         'calendarAssignments', 'calendarPeriods', 'calendarExceptions', 'operatorAbsences'],
    '02_risorse.sql': ['machines', 'operators', 'suppliers', 'tools', 'pallets', 'palletMachines',
                       'departments', 'departmentMembers', 'phaseTypes', 'capabilities'],
    '03_ordini_lotti.sql': ['customers', 'orders', 'orderLines', 'lots'],
    '04_lavorazioni.sql': ['operations'],
    '05_risorse_lavorazione.sql': ['operationResources', 'savedPlanBlocks', 'operationTools'],
    '06_vincoli_esterni.sql': ['explicitLinks', 'materialArrivals', 'subcontractOrders', 'lotComponents',
                               'workplans', 'workplanQuantities', 'setupClasses', 'setupClassParameters'],
}


def to_json_value(v):
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, str):
        return v.rstrip()
    return v


def fetch_all_sets(cursor):
    sets = []
    while True:
        if cursor.description is not None:
            cols = [c[0] for c in cursor.description]
            sets.append([{k: to_json_value(v) for k, v in zip(cols, row)} for row in cursor.fetchall()])
        if not cursor.nextset():
            break
    return sets


def run_script(cn, name, params):
    with open(os.path.join(SQL_DIR, name), encoding='utf-8') as f:
        body = f.read()
    header = ''.join(f'DECLARE {k} {t} = {v};\n' for k, (t, v) in params.items())
    cur = cn.cursor()
    cur.execute('SET NOCOUNT ON;\n' + header + body)
    sets = fetch_all_sets(cur)
    cur.close()
    return sets


def derive_precedences(ops, lots, bill_of_material=True):
    """Link consecutive SEQUENCE groups without ordering peers at the same FASEQ.

    When a lot's immediate parent has no operations, continue to the nearest
    ancestor with operations. All members of the child's final sequence group
    precede all members of that ancestor's first group.
    """
    by_lot = defaultdict(lambda: defaultdict(list))
    for o in ops:
        by_lot[(o['orderId'], o['lotId'])][o['sequence'] or 0].append(o)
    links = []
    first_last = {}
    for key, groups in by_lot.items():
        sequences = sorted(groups)
        first_last[key] = ([o['operationId'] for o in groups[sequences[0]]],
                           [o['operationId'] for o in groups[sequences[-1]]])
        for prev_seq, next_seq in zip(sequences, sequences[1:]):
            for previous in groups[prev_seq]:
                for current in groups[next_seq]:
                    parallel = (current.get('activationCode') or '').upper() == 'P'
                    links.append({
                        'predecessorOperationId': previous['operationId'],
                        'successorOperationId': current['operationId'],
                        'linkType': 'SS' if parallel else 'FS',
                        'lagSec': 0 if parallel else (previous.get('queueTimeSec') or 0),
                        'overlapPct': current.get('overlapPct') or 0, 'source': 'SEQUENCE'})
    if bill_of_material:
        parent_of = {(lot['orderId'], lot['lotId']): lot.get('parentLotId') for lot in lots}
        for child_key in first_last:
            order_id, lot_id = child_key
            parent_id = parent_of.get(child_key)
            seen = {lot_id}
            while parent_id is not None and parent_id not in seen:
                seen.add(parent_id)
                parent_key = (order_id, parent_id)
                if parent_key in first_last:
                    for predecessor in first_last[child_key][1]:
                        for successor in first_last[parent_key][0]:
                            links.append({
                                'predecessorOperationId': predecessor,
                                'successorOperationId': successor,
                                'linkType': 'FS', 'lagSec': 0,
                                'overlapPct': 0, 'source': 'LOT_TREE'})
                    break
                parent_id = parent_of.get(parent_key)
    return links

def lot_cycles(lots):
    parent = {(l['orderId'], l['lotId']): l.get('parentLotId') for l in lots}
    cycles = []
    for (o, l) in parent:
        seen, cur = set(), l
        while cur is not None and (o, cur) in parent:
            if cur in seen:
                cycles.append((o, l))
                break
            seen.add(cur)
            cur = parent[(o, cur)]
    return cycles


def validate(sc):
    issues = []
    ops = sc['operations']
    op_ids = {o['operationId'] for o in ops}
    cand = defaultdict(list)
    for r in sc['operationResources']:
        cand[r['operationId']].append(r)
    res_codes = {('MACHINE', m['code']) for m in sc['machines']} | {('OPERATOR', o['code']) for o in sc['operators']} \
        | {('SUPPLIER', s['code']) for s in sc['suppliers']}
    cal_ids = {a['calendarId'] for a in sc['calendarAssignments']}
    lot_keys = {(l['orderId'], l['lotId']) for l in sc['lots']}

    no_res = [o['operationId'] for o in ops if o['toPlan'] == 'Y' and not cand[o['operationId']]]
    issues.append(('Fasi da pianificare senza risorse candidate in L_LARE', len(no_res), no_res[:20]))
    unknown_res = sorted({(r['resourceKind'], r['resourceCode']) for r in sc['operationResources']} - res_codes)
    issues.append(('Risorse candidate non presenti nelle anagrafiche esportate', len(unknown_res), unknown_res[:20]))
    res_no_cal = [(k, x['code']) for k, lst in (('MACHINE', sc['machines']), ('OPERATOR', sc['operators']))
                  for x in lst if x.get('calendarId') not in cal_ids]
    issues.append(('Risorse con calendario (IDNUM) senza assegnazione in A_CAL', len(res_no_cal), res_no_cal[:20]))
    missing_parent = [(l['orderId'], l['lotId'], l['parentLotId']) for l in sc['lots']
                      if l.get('parentLotId') is not None and (l['orderId'], l['parentLotId']) not in lot_keys]
    issues.append(('Lotti con padre LOCLP inesistente', len(missing_parent), missing_parent[:20]))
    cyc = lot_cycles(sc['lots'])
    issues.append(('Cicli nella gerarchia dei lotti', len(cyc), cyc[:20]))
    dangling = [l for l in sc['explicitLinks'] if l['predecessorOperationId'] not in op_ids]
    issues.append(('Legami JobPrevious verso fasi fuori universo', len(dangling), dangling[:20]))
    no_due = [o['operationId'] for o in ops if not o.get('legacyDueDate')]
    issues.append(('Fasi senza data di consegna (MIN CMDTS del lotto finale)', len(no_due), no_due[:20]))
    zero = [o['operationId'] for o in ops if o['toPlan'] == 'Y' and not o['theoreticalTotalSec']]
    issues.append(('Fasi da pianificare con durata teorica zero', len(zero), zero[:20]))
    ext_no_order = [o['operationId'] for o in ops if o['executionCode'] == 'E'
                    and o['operationId'] not in {s['operationId'] for s in sc['subcontractOrders']}]
    issues.append(('Fasi esterne senza ordine di conto lavoro aperto', len(ext_no_order), ext_no_order[:20]))
    return issues


def main():
    import pyodbc

    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--server', default='.')
    ap.add_argument('--database', required=True)
    ap.add_argument('--user', default=os.environ.get('CLIENTE_USER'))
    ap.add_argument('--password', default=os.environ.get('CLIENTE_PASSWORD'))
    ap.add_argument('--driver', default=os.environ.get('CLIENTE_DRIVER','ODBC Driver 17 for SQL Server'))
    ap.add_argument('--start', required=True, help='inizio pianificazione AAAA-MM-GG')
    ap.add_argument('--horizon-days', type=int, default=120)
    ap.add_argument('--out', required=True)
    ap.add_argument('--customer-view', action='store_true', help='filtra con dbo.Xv_ElementiInPlanning')
    ap.add_argument('--exclude-blocked', action='store_true', help='esclude le commesse bloccate (COSTO 22)')
    ap.add_argument('--only-to-plan', action='store_true', help="solo fasi con FAPNF='Y'")
    ap.add_argument('--no-actuals', action='store_true', help='non calcola quantità prodotte e tempi consuntivati')
    ap.add_argument('--current-resource', action='store_true', help='legge la macchina/operatore della sessione aperta')
    ap.add_argument('--no-bom-links', action='store_true', help='non deriva i legami di distinta tra lotti')
    a = ap.parse_args()

    start = dt.date.fromisoformat(a.start)
    end = start + dt.timedelta(days=a.horizon_days)
    def odbc_value(value):
        return '{' + str(value).replace('}', '}}') + '}'
    auth = (f'UID={odbc_value(a.user)};PWD={odbc_value(a.password or "")}' if a.user
            else 'Trusted_Connection=yes')
    cn = pyodbc.connect(f'DRIVER={{{a.driver}}};SERVER={odbc_value(a.server)};DATABASE={odbc_value(a.database)};'
                        f'{auth};ApplicationIntent=ReadOnly;Encrypt=yes;TrustServerCertificate=yes',
                        autocommit=True, readonly=True)

    params = {
        '@IncludiBloccate': ('bit', 0 if a.exclude_blocked else 1),
        '@UsaVistaCliente': ('bit', 1 if a.customer_view else 0),
        '@SoloDaPianificare': ('bit', 1 if a.only_to_plan else 0),
    }
    universe = run_script(cn, '00_universo.sql', params)[-1][0]
    params.update({
        '@DataInizio': ('date', f"'{start.isoformat()}'"),
        '@DataFine': ('date', f"'{end.isoformat()}'"),
        '@CalcolaConsuntivi': ('bit', 0 if a.no_actuals else 1),
        '@UsaRisorsaCorrente': ('bit', 1 if a.current_resource else 0),
    })

    sc = {}
    for script, names in RESULT_SETS.items():
        t0 = dt.datetime.now()
        sets = run_script(cn, script, params)
        if len(sets) != len(names):
            sys.exit(f'{script}: attesi {len(names)} result set, ricevuti {len(sets)}')
        sc.update(zip(names, sets))
        print(f'{script}: ' + ', '.join(f'{n}={len(s)}' for n, s in zip(names, sets)) +
              f' ({(dt.datetime.now() - t0).total_seconds():.1f}s)')

    derived = derive_precedences(sc['operations'], sc['lots'], not a.no_bom_links)
    explicit = [dict(l, lagSec=0, overlapPct=0, source='EXPLICIT') for l in sc['explicitLinks']
                if l['predecessorOperationId'] is not None]
    sc['precedences'] = derived + [{k: l[k] for k in ('predecessorOperationId', 'successorOperationId', 'linkType',
                                                       'lagSec', 'overlapPct', 'source')} for l in explicit]

    config = {'database': a.database, 'start': a.start, 'horizonDays': a.horizon_days,
              'customerView': a.customer_view, 'includeBlocked': not a.exclude_blocked,
              'onlyToPlan': a.only_to_plan, 'actuals': not a.no_actuals,
              'currentResource': a.current_resource, 'bomLinks': not a.no_bom_links}
    scenario = {
        'schemaVersion': '1.0',
        'scenarioId': f"factory-{a.database.lower()}-{start.isoformat()}",
        'source': {'system': 'Factory ERP', 'server': a.server, 'database': a.database,
                   'capturedAt': dt.datetime.now().astimezone().isoformat(timespec='seconds'),
                   'extractor': 'planning-engine-spec/tools/export_factory_scenario.py',
                   'configFingerprint': hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()[:16]},
        'planning': {'start': f'{start.isoformat()}T00:00:00', 'horizonDays': a.horizon_days,
                     'timeZone': 'Europe/Rome', 'extraction': config},
        'universe': universe,
    }
    scenario.update(sc)

    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'scenario.json'), 'w', encoding='utf-8') as f:
        json.dump(scenario, f, ensure_ascii=False, indent=1)

    issues = validate(sc)
    with open(os.path.join(a.out, 'validation.md'), 'w', encoding='utf-8') as f:
        f.write(f"# Validazione scenario {scenario['scenarioId']}\n\n")
        f.write(f"Estratto il {scenario['source']['capturedAt']} da `{a.database}`.\n\n")
        f.write('## Conteggi\n\n| Entità | Righe |\n|---|---|\n')
        for k, v in sc.items():
            f.write(f'| {k} | {len(v)} |\n')
        f.write('\n## Controlli\n\n| Controllo | Casi | Esempi |\n|---|---|---|\n')
        for title, n, ex in issues:
            f.write(f"| {title} | {n} | {', '.join(map(str, ex[:5]))}{' …' if n > 5 else ''} |\n")
    print('Scritto', os.path.abspath(a.out))
    for title, n, _ in issues:
        print(f'  {n:6d}  {title}')


if __name__ == '__main__':
    main()
