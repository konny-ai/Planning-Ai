"""Scenario CLIENTE con le regole indicate dal cliente (risposte al questionario, 2026-10-23).

Regole:
 - fasi da pianificare: stato lavorazione 41/42/43 (A_STA, tipo L). FAPNF='N' = non pianificare, 'Y' = pianificare;
   una fase inserita in un nesting aperto si pianifica a prescindere da FAPNF;
 - fase di tipo 66 ("Pulizia", A_TFA): esclusa;
 - fasi senza operatore/macchina in L_LARE: candidati = tutte le risorse abilitate al tipo di fase (L_OPTF/L_MATF);
 - la sede (Sede A/Sede B) non si considera;
 - materiale: la fonte AS400 non c'e' ancora; il file materiali_simulati.csv e' SIMULATO (vedi 'nesting');
 - fase presente in piu' programmi di nesting (pezzi dello stesso lotto su lamiere diverse): il motore la mette
   in un solo nesting, quello del programma piu' urgente (consegna minima dei membri, X_MinDataConsegnaPdl),
   a parita' il workplanId minore (decisione del 23/10/2026).

Uso:  python build_scenario_cliente.py prepara <scenario.json> <out_dir>
      python build_scenario_cliente.py nesting <engine-scenario.json> <out.json> [<materiali_simulati.csv>]
(`nesting` va eseguito dopo to_engine_v02; legge i CSV di out/ prodotti da run_extract.py)
"""
import collections
import csv
import datetime as dt
import json
import os
import sys

OUT = os.environ.get('CLIENTE_EXTRACT_OUT') or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'out')
STATI = (41, 42, 43)
TIPO_ESCLUSO = 66


def _csv(*p):
    with open(os.path.join(OUT, *p), encoding='utf-8') as f:return list(csv.DictReader(f))


def prepara(src, dst):
    with open(src, encoding='utf-8') as f:s = json.load(f)
    nest_wp = {str(w['workplanId']) for w in s['workplans']}
    cap = collections.defaultdict(lambda: collections.defaultdict(set))
    for c in s['capabilities']:
        cap[c['phaseTypeId']][c['resourceKind']].add(c['resourceCode'])
    have = collections.defaultdict(lambda: collections.defaultdict(set))
    for r in s['operationResources']:
        have[r['operationId']][r['resourceKind']].add(r['resourceCode'])
    st = collections.Counter()
    filled = collections.Counter()
    for o in s['operations']:
        if o['phaseTypeId'] == TIPO_ESCLUSO:
            o['toPlan'] = ''
            o['unitSec'] = o['setupSec'] = o['theoreticalTotalSec'] = 0
            st['esclusa tipo 66'] += 1
            continue
        in_nest = str(o.get('workplanId')) in nest_wp
        plan = o['statusCode'] in STATI and (o['toPlan'] == 'Y' or in_nest)
        o['toPlan'] = 'Y' if plan else ''
        st['da pianificare' if plan else 'non pianificata'] += 1
        if not plan or o['executionCode'] != 'I' or o['theoreticalTotalSec'] <= 0:
            continue
        for kind, need in (('MACHINE', o['runMachines']), ('OPERATOR', o['runOperators'])):
            if need > len(have[o['operationId']][kind]):
                cands = sorted(cap.get(o['phaseTypeId'], {}).get(kind, ()))
                if not cands:
                    filled['senza abilitati per il tipo di fase: ' + kind] += 1
                    continue
                for code in cands:
                    if code not in have[o['operationId']][kind]:
                        s['operationResources'].append({
                            'assignmentId': 0, 'operationId': o['operationId'], 'resourceKind': kind,
                            'resourceCode': code, 'assignmentRule': 'ALLOWED', 'preferential': False,
                            'usedInSavedPlan': 0, 'usagePct': 100.0, 'allocationPct': 100.0, 'priority': 0,
                            'inefficiencyPct': 0, 'bonusTimeSec': 0, 'setupCapable': 0, 'calendarId': None,
                            'calendarModelId': None, 'savedBlockId': 0})
                filled['candidati da abilitazioni tipo fase: ' + kind] += 1
    s['scenarioId'] += '-REGOLE-CLIENTE'
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(dst, 'scenario.json'), 'w', encoding='utf-8') as f:json.dump(s,f,ensure_ascii=False)
    print(dict(st), dict(filled))


def nesting(src, dst, mat_csv=None, *, apply_material_release=True):
    with open(src, encoding='utf-8') as f:s = json.load(f)
    ops = {o['id']: o for o in s['operations']}
    alt = {i: {json.dumps(a, sort_keys=True) for a in o['alternatives']} for i, o in ops.items()}
    progs = {r['workplanId']: r for r in _csv('10_nesting_programmi', '1.csv')}
    mem = collections.defaultdict(list)
    for r in _csv('11_nesting_membri', '1.csv'):
        oid = f"{r['orderId']}/{r['lotId']}/{r['phaseId']}"
        if oid in ops:
            mem[r['workplanId']].append(oid)
    start = dt.datetime.fromisoformat(s['start'])
    stock = {r['sheetPartCode']: float(r['stockAvailable'] or 0) for r in _csv('13_materiali_giacenze_arrivi', '1.csv')}
    arrivi = collections.defaultdict(list)
    for r in _csv('13_materiali_giacenze_arrivi', '2.csv'):
        if r['promisedDate'] and r['promisedDate'][:4] >= '2026':
            arrivi[r['partCode']].append(dt.datetime.fromisoformat(r['promisedDate']).replace(tzinfo=start.tzinfo))

    def disp(part):
        # SIMULATO: giacenza -> subito; ordine aperto 2026 -> data promessa; altrimenti inizio + 7 giorni
        if stock.get(part, 0) > 0:
            return start, 'giacenza'
        if arrivi.get(part):
            return max(start, min(arrivi[part])), 'ordine fornitore'
        return start + dt.timedelta(days=7), 'SIMULATA +7gg'

    sheets = collections.defaultdict(set)
    for r in _csv('12_nesting_lamiere', '1.csv'):
        if r['sheetPartCode']:
            sheets[r['workplanId']].add(r['sheetPartCode'])
    used, nests, skip, rows = set(), [], collections.Counter(), []
    # Il programma piu' urgente prende per primo le fasi condivise con altri programmi.
    urgency = sorted(progs.items(), key=lambda wp: (wp[1]['minMemberDueDate'] or '9999-12-31', int(wp[0])))
    for w, p in urgency:
        cut = float(p['cutSec'] or 0)
        if cut <= 0:
            skip['cutSec=0 (non e un taglio)'] += 1
            continue
        m = [o for o in dict.fromkeys(mem.get(w, [])) if o not in used]
        if not m:
            skip['senza membri nel piano'] += 1
            continue
        if not set.intersection(*(alt[o] for o in m)):
            skip['nessun bundle comune'] += 1
            continue
        used.update(m)
        nests.append({'id': 'NEST-' + w, 'duration_seconds': int(-(-cut // 1)),
                      'setup_seconds': int(float(p['setupSec'] or 0)), 'operation_ids': m})
        ds = [(disp(x), x) for x in sorted(sheets.get(w, ()))] if apply_material_release else []
        if ds:
            rel = max(d[0][0] for d in ds)
            for x in m:
                ops[x]['release'] = rel.isoformat()
            for (d, how), x in ds:
                rows.append((w, x, d.date().isoformat(), how))
    s['nestings'] = nests
    s['id'] += '-nesting'
    with open(dst, 'w', encoding='utf-8') as f:json.dump(s,f,ensure_ascii=False)
    if mat_csv:
        with open(mat_csv, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['workplanId', 'sheetPartCode', 'availableDate', 'source'])
            w.writerows(rows)
    print('nesting', len(nests), 'membri', sum(len(n['operation_ids']) for n in nests), dict(skip),
          'righe materiale simulato:', len(rows))


if __name__ == '__main__':
    {'prepara': prepara, 'nesting': nesting}[sys.argv[1]](*sys.argv[2:])
