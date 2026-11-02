"""Converte uno scenario.json di planning-engine-spec nel contratto del motore planning-factory v0.2.

Uscite (in --out):
  engine-scenario.json   Scenario nativo del motore (planning-factory validate/schedule)
  factory-template.json  stesso scenario senza operazioni, per adapters.factory.import_snapshot
  factory-snapshot.json  export normalizzato phases/lots/links (tutte le fasi, anche a durata zero)
  conversion-report.md   scelte di mappatura e casi non rappresentabili nel contratto v0.2
Con --anonymize i codici di operatori, fornitori, macchine e commesse vengono sostituiti
da identificativi sequenziali coerenti; la mappa resta solo in locale (anonymization-map.json).

Scelte (documentate nel report):
  - fasi da pianificare = toPlan 'Y' con durata teorica > 0; le altre (durata zero o FAPNF='N')
    sono rimosse e le precedenze vengono ricollegate attraverso di esse (bridge);
  - durata = ceil(unitSec * qty), setup = ceil(setupSec); con --progress-policy time entrambi sono
    ridotti dell'avanzamento a tempi del legacy, altrimenti nessun consuntivo (profilo 'none');
  - interne: un bundle per operatore candidato (runOperators=1) e per macchina se runMachines>0;
    esterne: un bundle per fornitore candidato, o il fornitore dell'ordine di conto lavoro;
    senza candidate: risorsa virtuale NO_RESOURCE a capacita' infinita sul calendario 0;
  - fornitori e NO_RESOURCE a capacita' infinita;
  - festivita' valide per tutti i calendari (la funzione Factory non filtra per modello);
  - calendari: giorno del ciclo = (data - TCSTD) mod N (anche cicli di N giorni), variazioni L_CATC per
    periodo (sovrapposte: TCSEQ piu' alto), cambi di assegnazione A_CAL per data, eccezioni A_ECC che
    sostituiscono il giorno; i calendari non settimanali sono espansi giorno per giorno nell'orizzonte;
  - giustificativi S_GSA: chiusure sul calendario proprio dell'operatore;
  - consegna = legacyDueDate (CMDTS) meno --delivery-margin-days (GiorniMargineConsegna), alle 24:00
    locali; release = arrivo materiali del lotto;
  - fasi interrompibili fra turni (splittable) come nel legacy;
  - modalita' auto con soglia 30 giorni (SogliaGGTipoPian del banco): a ritroso se CMDTS >= inizio + soglia,
    confronto per data come nel legacy; la soglia del motore e' riscritta per la consegna convertita.
"""
import argparse, hashlib, json, math, os, collections, itertools
from datetime import date, datetime, timedelta, time, timezone
from zoneinfo import ZoneInfo

NONE_ID = 'NO_RESOURCE'


def hhmm(sec):
    sec = int(sec) % 86400
    return f'{sec // 3600:02d}:{sec % 3600 // 60:02d}'


def local_iso(d: date, tz, days=0):
    return datetime.combine(d + timedelta(days=days), time(0), ZoneInfo(tz)).isoformat()


def fixed_local_iso(value, tz):
    """Factory SQL datetimes are local wall time; preserve their fixed instant."""
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(tz))
    return parsed.astimezone(ZoneInfo(tz)).isoformat()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('scenario')
    ap.add_argument('--out', required=True)
    ap.add_argument('--horizon-days', type=int, default=365)
    ap.add_argument('--auto-backward-days', type=int, default=30,
                    help='SogliaGGTipoPian: a ritroso se CMDTS >= inizio + N giorni')
    ap.add_argument('--delivery-margin-days', type=int, default=0,
                    help='GiorniMargineConsegna: la consegna del motore anticipa CMDTS di N giorni')
    ap.add_argument('--mode', default='auto', choices=['auto', 'forward', 'backward'])
    ap.add_argument('--anonymize', action='store_true')
    ap.add_argument('--missing-resource-policy', choices=['fail', 'available'], default='fail',
                    help='fasi con meno candidate del fabbisogno FANML/FANPL: errore (default) oppure '
                         'pianificate con le candidate disponibili, contate nel report')
    ap.add_argument('--progress-policy', choices=['none', 'time'], default='none',
                    help='time: durate residue dai consuntivi come ProgressManagementMode "Con tempi"')
    ap.add_argument('--subcontract-policy', choices=['resources', 'anchored'], default='resources',
                    help='opt-in: represent open purchase-order promised dates as external milestones')
    a = ap.parse_args()
    with open(a.scenario, 'rb') as f:
        source_bytes = f.read()
    s = json.loads(source_bytes.decode('utf-8'))
    tz = s['planning']['timeZone']
    start_d = date.fromisoformat(s['planning']['start'][:10])
    end_d = start_d + timedelta(days=a.horizon_days)
    notes, counts = [], collections.Counter()
    # Il motore confronta la consegna convertita (24:00 di CMDTS - margine) con inizio + soglia:
    # con questa soglia equivale alla regola legacy CMDTS >= inizio + SogliaGGTipoPian.
    engine_backward_days = a.auto_backward_days + 1 - a.delivery_margin_days
    if a.delivery_margin_days < 0 or engine_backward_days < 0:
        raise SystemExit('margine di consegna negativo o maggiore della soglia')

    # --- anonimizzazione coerente --------------------------------------------------
    amap = collections.defaultdict(dict)

    def anon(kind, code, prefix):
        code = str(code)
        if not a.anonymize:
            return code
        m = amap[kind]
        if code not in m:
            m[code] = f'{prefix}{len(m) + 1:03d}'
        return m[code]

    def rid(kind, code):
        return {'OPERATOR': 'OP:', 'SUPPLIER': 'SUP:', 'MACHINE': 'MAC:'}[kind] + anon(kind, code, kind[:3])

    def order_id(oid):
        return anon('ORDER', oid, 'ORD') if a.anonymize else str(oid)

    # Codici assegnati in ordine alfabetico: stesso universo -> stessa mappa fra estrazioni.
    for code in sorted({str(o['code']) for o in s['operators']} | {r['resourceCode'] for r in s['operationResources'] if r['resourceKind'] == 'OPERATOR'}):
        anon('OPERATOR', code, 'OPE')
    for code in sorted({str(f['code']) for f in s['suppliers']}):
        anon('SUPPLIER', code, 'SUP')
    for code in sorted({str(m['code']) for m in s['machines']}):
        anon('MACHINE', code, 'MAC')
    for code in sorted({o['orderId'] for o in s['operations']} | {l['orderId'] for l in s['lots']}):
        order_id(code)

    # --- calendari -----------------------------------------------------------------
    models = {m['modelId']: m for m in s['calendarModels']}
    intervals = collections.defaultdict(list)
    for i in s['dayTypeIntervals']:
        if i['startSec'] == i['endSec'] == 0 and not i.get('crossesMidnight'):
            continue
        intervals[i['dayTypeId']].append(i)
    days = collections.defaultdict(list)
    for d in s['calendarModelDays']:
        days[d['modelId']].append(d)
    holidays = []
    for h in s['holidays']:
        hd = date.fromisoformat(h['date'])
        if h['recurrence'] == 'YEARLY':
            for y in range(start_d.year - 1, end_d.year + 2):
                try:
                    holidays.append(date(y, hd.month, hd.day))
                except ValueError:
                    pass
        else:
            holidays.append(hd)
    holidays = sorted({d.isoformat() for d in holidays if start_d - timedelta(days=2) <= d <= end_d + timedelta(days=2)})
    # Modello completo del calendario Factory (RE M08 §5.2, dossier risorse 20): giorno del ciclo
    # = (data - TCSTD) mod N; variazioni L_CATC per periodo; eccezioni A_ECC che sostituiscono il
    # giorno ([0,0] = chiuso); giustificativi S_GSA come chiusure del solo operatore.
    exceptions_of = collections.defaultdict(lambda: collections.defaultdict(list))
    for e in s['calendarExceptions']:
        exceptions_of[e['calendarId']][e['date'][:10]].append((e['startSec'], e['endSec']))
    periods_of = collections.defaultdict(list)
    for p in s['calendarPeriods']:
        if p['modelId'] not in models:
            raise SystemExit(f"variazione L_CATC del calendario {p['calendarId']}: modello {p['modelId']} inesistente")
        periods_of[p['calendarId']].append(p)
    window = [start_d + timedelta(days=i) for i in range(-2, (end_d - start_d).days + 3)]

    assigned = collections.defaultdict(list)
    for x in sorted(s['calendarAssignments'], key=lambda x: x['validFrom']):
        if x['modelId'] not in models:
            raise SystemExit(f"assegnazione A_CAL del calendario {x['calendarId']}: modello {x['modelId']} inesistente")
        assigned[x['calendarId']].append(x)

    def model_of(cal_id, day=None):
        day = (day or start_d).isoformat()
        covering = [p for p in periods_of[cal_id] if p['fromDate'][:10] <= day <= p['toDate'][:10]]
        if covering:  # precedenza fra variazioni sovrapposte non dimostrata: vince TCSEQ piu' alto
            return max(covering, key=lambda p: (p['seq'] or 0, p['fromDate']))['modelId']
        rows = [x for x in assigned[cal_id] if x['validFrom'][:10] <= day]
        return rows[-1]['modelId'] if rows else None

    def day_shifts(mid, day):
        m = models[mid]
        index = (day - date.fromisoformat(m['cycleStartDate'][:10])).days % m['cycleDays']
        return sorted((hhmm(i['startSec']), hhmm(i['endSec'])) for d in days[mid] if d['dayIndex'] == index
                      for i in intervals.get(d['dayTypeId'], ()))

    calendars = {}

    def calendar(cal_id):
        cid = f'CAL{cal_id}'
        if cid in calendars:
            return cid
        mid = model_of(cal_id)
        models_used = {model_of(cal_id, day) for day in window}
        if models_used == {None} and not assigned[cal_id] and not periods_of[cal_id]:
            if cal_id == 0:
                raise SystemExit('calendario 0 senza assegnazione: impossibile creare la disponibilita di fallback')
            notes.append(f'calendario {cal_id} senza assegnazione: usato il calendario 0')
            return calendar(0)
        daily = (None in models_used or len(models_used) > 1 or models[mid]['cycleDays'] != 7
                 or any(day.isoformat() in exceptions_of[cal_id] for day in window))
        shifts, exceptions = [], []
        if not daily:
            m = models[mid]
            base = date.fromisoformat(m['cycleStartDate'][:10]).weekday()
            for d in days[mid]:
                for i in intervals.get(d['dayTypeId'], ()):
                    shifts.append({'weekday': (base + d['dayIndex']) % 7, 'start': hhmm(i['startSec']), 'end': hhmm(i['endSec'])})
        else:
            counts['calendari espansi giorno per giorno (ciclo non settimanale, variazioni o eccezioni)'] += 1
            for day in window:
                rows = exceptions_of[cal_id].get(day.isoformat())
                if rows is not None:
                    counts['giorni sostituiti da eccezioni A_ECC'] += 1
                    # [0,0] = giorno chiuso; fine < inizio = intervallo che passa la mezzanotte.
                    pairs = sorted((hhmm(a), hhmm(b)) for a, b in rows if a != b)
                else:
                    current = model_of(cal_id, day)
                    pairs = day_shifts(current, day) if current is not None else []
                exceptions.append({'day': day.isoformat(), 'shifts': [list(x) for x in pairs]})
        calendars[cid] = {'id': cid, 'timezone': tz, 'shifts': sorted(shifts, key=lambda x: (x['weekday'], x['start'])),
                          'holidays': holidays, 'exceptions': exceptions, 'closures': []}
        return cid

    def absence_instant(day, seconds):
        # An S_GSA local wall time must be unique and existent. replace(tzinfo=...)
        # silently accepts both a spring gap and an autumn repeated hour.
        wall = day + timedelta(seconds=seconds)
        zone = ZoneInfo(tz)
        first = wall.replace(tzinfo=zone, fold=0)
        second = wall.replace(tzinfo=zone, fold=1)
        roundtrip = first.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if first.utcoffset() != second.utcoffset() or roundtrip != wall:
            raise SystemExit(f'giustificativo S_GSA: ora locale ambigua o inesistente {wall.isoformat()} in {tz}')
        return first.isoformat()

    absences = collections.defaultdict(list)
    for g in s['operatorAbsences']:
        if g['startSec'] < g['endSec']:
            day = datetime.combine(date.fromisoformat(g['date'][:10]), time(0))
            absences[str(g['operatorCode'])].append(
                {'start': absence_instant(day, g['startSec']),
                 'end': absence_instant(day, g['endSec'])})

    def operator_calendar(o):
        cid = calendar(o['calendarId'])
        closures = absences.get(str(o['code']))
        if not closures:
            return cid
        own = f"{cid}-{rid('OPERATOR', o['code'])}"
        calendars[own] = dict(calendars[cid], id=own, closures=sorted(closures, key=lambda x: x['start']))
        counts['operatori con giustificativi S_GSA (calendario proprio)'] += 1
        counts['giustificativi S_GSA'] += len(closures)
        return own

    # --- risorse -------------------------------------------------------------------
    resources = {}
    for o in s['operators']:
        resources[rid('OPERATOR', o['code'])] = {'id': rid('OPERATOR', o['code']), 'calendar_id': operator_calendar(o), 'capacity': 1, 'kind': 'operator'}
    for m in s['machines']:
        resources[rid('MACHINE', m['code'])] = {'id': rid('MACHINE', m['code']), 'calendar_id': calendar(m['calendarId']), 'capacity': 1, 'kind': 'machine'}
    for f in s['suppliers']:
        resources[rid('SUPPLIER', f['code'])] = {'id': rid('SUPPLIER', f['code']), 'calendar_id': calendar(f['calendarId'] or 0), 'capacity': None, 'kind': 'supplier'}
    resources[NONE_ID] = {'id': NONE_ID, 'calendar_id': calendar(0), 'capacity': None, 'kind': 'virtual'}

    # --- operazioni ----------------------------------------------------------------
    ops = {o['operationId']: o for o in s['operations']}
    key = {oid: f"{order_id(o['orderId'])}/{o['lotId']}/{o['phaseId']}" for oid, o in ops.items()}
    cand = collections.defaultdict(list)
    excluded = collections.defaultdict(lambda: collections.defaultdict(set))
    for r in s['operationResources']:
        if r['assignmentRule'] == 'EXCLUDED':
            excluded[r['operationId']][r['resourceKind']].add(str(r['resourceCode']))
            counts['candidate esplicitamente escluse'] += 1
            continue
        if r['calendarId'] is not None or r['calendarModelId'] is not None:
            counts['candidate con calendario proprio (ignorato)'] += 1
        cand[r['operationId']].append(r)
    sub = collections.defaultdict(list)
    promises = collections.defaultdict(list)
    for so in s['subcontractOrders']:
        sub[so['operationId']].append(so['supplierCode'])
        if so.get('promisedDate'):
            promises[so['operationId']].append(so['promisedDate'])
    arrivals = {}
    for m in s['materialArrivals']:
        if m['availableDate']:
            k = (m['orderId'], m['lotId'])
            arrivals[k] = max(arrivals.get(k, m['availableDate']), m['availableDate'])
    lots = {(l['orderId'], l['lotId']): l for l in s['lots']}
    known = set(resources)

    def permitted(rows, kind):
        matching = [r for r in rows if r['resourceKind'] == kind]
        forced = [r for r in matching if r['assignmentRule'] == 'FORCED']
        selected = forced if forced else matching
        return sorted({rid(kind, r['resourceCode']) for r in selected
                       if rid(kind, r['resourceCode']) in known})

    def bundles(o, *, enforce_requirements=True):
        oid = o['operationId']
        rows = cand[oid]
        if o['executionCode'] == 'E':
            sup = permitted(rows, 'SUPPLIER')
            if not sup:
                sup = sorted({rid('SUPPLIER', c) for c in sub[oid]
                              if str(c) not in excluded[oid]['SUPPLIER']
                              and rid('SUPPLIER', c) in known})
                if sup:
                    counts["esterne: fornitore preso dall'ordine di conto lavoro"] += 1
            return [[x] for x in sup]
        opsr = permitted(rows, 'OPERATOR')
        macs = permitted(rows, 'MACHINE')
        machines_required = int(o['runMachines'] or 0)
        operators_required = int(o['runOperators'] or 0)
        if machines_required < 0 or operators_required < 0:
            raise SystemExit(f'fase {oid}: fabbisogno risorse negativo')
        if not opsr and not macs:
            if enforce_requirements and (machines_required or operators_required):
                # Never bypass the default fail-closed policy just because ALL
                # candidates are missing. A virtual infinite resource would
                # conceal a genuine mandatory machine/operator requirement.
                if a.missing_resource_policy == 'fail':
                    raise SystemExit(f'fase {oid}: risorse obbligatorie mancanti '
                                     f'(macchine 0/{machines_required}, '
                                     f'operatori 0/{operators_required}); '
                                     'con --missing-resource-policy available '
                                     'il ripiego virtuale e esplicito e diagnostico')
                counts['senza candidate ma con fabbisogno dichiarato: ripiego virtuale opt-in'] += 1
            return []  # Zero-demand work or explicitly opted-in virtual fallback.
        if len(macs) < machines_required or len(opsr) < operators_required:
            if enforce_requirements and a.missing_resource_policy == 'fail':
                raise SystemExit(f'fase {oid}: risorse obbligatorie mancanti '
                                 f'(macchine {len(macs)}/{machines_required}, '
                                 f'operatori {len(opsr)}/{operators_required}); '
                                 'con --missing-resource-policy available si usano le candidate disponibili')
            if enforce_requirements:
                counts['fabbisogno ridotto alle candidate disponibili (--missing-resource-policy available)'] += 1
            machines_required = min(machines_required, len(macs))
            operators_required = min(operators_required, len(opsr))
        groups = []
        if machines_required:
            groups.append(itertools.combinations(macs, machines_required))
        if operators_required:
            groups.append(itertools.combinations(opsr, operators_required))
        if not groups:
            return []
        combos = [[]]
        for group in groups:
            combos = [c + list(choice) for c in combos for choice in group]
        return combos

    def remaining(o):
        """Quota da pianificare (0..1). Con --progress-policy time segue l'avanzamento a tempi
        del legacy (dossier dati04 §5.2): p = massimo dei rapporti consuntivo/teorico per macchine
        e operatori, con il teorico moltiplicato per numero di risorse e percentuale d'impiego."""
        if a.progress_policy == 'none':
            return 1.0
        ratios = []
        for kind in ('Machine', 'Operator'):
            den = o['theoreticalTotalSec'] * (o[f'run{kind}s'] or 0) * o.get(f'run{kind}Pct', 100) / 100
            done = (o.get(f'actualSetup{kind}Sec') or 0) + (o.get(f'actualWork{kind}Sec') or 0)
            if den > 0:
                ratios.append(done / den)
        return 1.0 - min(1.0, max(0.0, max(ratios, default=0.0)))

    share = {oid: remaining(o) for oid, o in ops.items()}
    if a.progress_policy != 'none':
        counts['fasi con consuntivo: durata ridotta'] = sum(1 for oid, o in ops.items()
                                                            if 0 < share[oid] < 1 and o['theoreticalTotalSec'] > 0)
        counts['fasi rimosse: consuntivo completo'] = sum(1 for oid, o in ops.items()
                                                          if share[oid] == 0 and o['theoreticalTotalSec'] > 0)
    active = {oid for oid, o in ops.items() if o['toPlan'] == 'Y' and o['theoreticalTotalSec'] > 0 and share[oid] > 0}
    counts['fasi nell\'universo'] = len(ops)
    counts['fasi pianificate dal motore'] = len(active)
    counts['fasi rimosse: durata zero'] = sum(1 for o in ops.values() if o['theoreticalTotalSec'] <= 0)
    counts['fasi rimosse: FAPNF=N con durata > 0'] = sum(1 for oid, o in ops.items() if o['toPlan'] != 'Y' and o['theoreticalTotalSec'] > 0)

    preds = collections.defaultdict(list)
    for p in s['precedences']:
        kind = 'SS' if p['linkType'] == 'SS' else 'FS'
        if p['overlapPct'] not in (0, 100):
            counts['sovrapposizione parziale approssimata'] += 1
        preds[p['successorOperationId']].append((p['predecessorOperationId'], kind, int(p['lagSec'] or 0),
                                                    p.get('source') == 'EXPLICIT'))

    def active_preds(oid, seen=frozenset()):
        """Bridge removed phases, retaining the upstream FS/SS rule and provenance."""
        out = set()
        seen = seen | {oid}
        for pid, kind, lag, explicit in preds[oid]:
            if pid not in ops or pid in seen:
                continue
            if pid in active:
                out.add((pid, kind, lag, explicit))
            else:
                for previous, upstream_kind, upstream_lag, upstream_explicit in active_preds(pid, seen):
                    out.add((previous, upstream_kind, lag + upstream_lag,
                             explicit or upstream_explicit))
        return out

    # Regola legacy R4 (docs/28 §28.8, CheckJobDeliveryDate=1): per lotto si ancora solo la
    # prima fase esterna, in ordine FASEQ/IDFAS, con un fornitore tra le candidate L_LARE;
    # le commesse pianificate a ritroso si collocano dalla consegna e non vengono ancorate.
    first_supplied = {}
    for oid in sorted(ops, key=lambda x: (ops[x]['sequence'], ops[x]['phaseId'])):
        o = ops[oid]
        if o['executionCode'] == 'E' and any(r['resourceKind'] == 'SUPPLIER' for r in cand[oid]):
            first_supplied.setdefault((o['orderId'], o['lotId']), oid)

    def backward(o):
        if a.mode != 'auto':
            return a.mode == 'backward'
        due = o['legacyDueDate']
        return bool(due) and (date.fromisoformat(due) - start_d).days >= a.auto_backward_days

    operations, phases = [], []
    for oid in sorted(ops, key=lambda x: key[x]):
        o = ops[oid]
        # Retain snapshot metadata for inactive zero/completed phases, but
        # enforce resource demands only for actual schedulable work.
        alts = bundles(o, enforce_requirements=(oid in active))
        anchor = None
        if (a.subcontract_policy == 'anchored' and oid in active
                and o['executionCode'] == 'E'):
            if not promises[oid]:
                counts['esterne senza data promessa: risorse'] += 1
            elif first_supplied.get((o['orderId'], o['lotId'])) != oid:
                counts['esterne con promessa non ancorate: non prima fase esterna con fornitore del lotto'] += 1
            elif backward(o):
                counts['esterne con promessa non ancorate: commessa a ritroso'] += 1
            else:
                anchor = fixed_local_iso(max(promises[oid]), tz)
                if o.get('fixedStartFlag'):
                    raise SystemExit(f'fase {oid}: inizio fisso confligge con data promessa fornitore')
                counts['esterne ancorate a data promessa'] += 1
        lot = lots.get((o['orderId'], o['lotId']), {})
        due = (local_iso(date.fromisoformat(o['legacyDueDate']), tz, 1 - a.delivery_margin_days)
               if o['legacyDueDate'] else None)
        arr = arrivals.get((o['orderId'], o['lotId']))
        release = local_iso(date.fromisoformat(arr), tz) if arr and date.fromisoformat(arr) > start_d else None
        unit, setup_raw = o['unitSec'], o['setupSec']
        if share[oid] < 1:
            unit, setup_raw = unit * share[oid], setup_raw * share[oid]
        work = math.ceil(round(unit * o['qty'], 6))
        setup = math.ceil(round(setup_raw, 6))
        phase = {'CONUM': order_id(o['orderId']) if a.anonymize else o['orderId'], 'LOCOD': o['lotId'], 'IDFAS': o['phaseId'],
                 'FASEQ': o['sequence'], 'FAHLV': 0, 'FASLV': unit, 'FAHAT': 0, 'FASAT': setup_raw, 'LAQTP': o['qty'],
                 'LOPRI': int(lot.get('priority') or 0), 'LAPRI': int(o['priority'] or 0), 'splittable': True,
                 'resource_bundles': alts or [[NONE_ID]]}
        if o.get('fixedStartFlag'):
            if not o.get('fixedStartAt'):
                raise SystemExit(f'fase {oid}: inizio fisso senza data')
            phase['fixed_start'] = fixed_local_iso(o['fixedStartAt'], tz)
        if due:
            phase['CMDTS'] = due
        if release and not anchor:
            phase['release'] = release
        if anchor:
            phase['resource_bundles'] = []
            phase['splittable'] = False
            phase['external_end'] = anchor
        phases.append(phase)
        if oid not in active:
            continue
        if not alts and not anchor:
            counts['senza candidate: NO_RESOURCE'] += 1
            alts = [[NONE_ID]]
        if not due:
            counts['senza consegna'] += 1
        if release and not anchor:
            counts['con release da arrivo materiali'] += 1
        op = {'id': key[oid], 'order_id': order_id(o['orderId']), 'duration_seconds': work, 'setup_seconds': setup,
              'alternatives': [{'demands': [{'resource_id': r, 'units': 1} for r in b]} for b in alts],
              'predecessors': [{'predecessor_id': key[p], 'kind': k, 'lag_seconds': l} for p, k, l, _ in sorted(active_preds(oid), key=lambda x: (key[x[0]], x[1], x[2]))],
              'priority': int(lot.get('priority') or 0), 'phase_priority': int(o['priority'] or 0), 'splittable': True}
        if 'fixed_start' in phase:
            op['fixed_start'] = phase['fixed_start']
        if due:
            op['due'] = due
        if release and not anchor:
            op['release'] = release
        if anchor:
            op.update(duration_seconds=0, setup_seconds=0, alternatives=[],
                      splittable=False, external_end=anchor)
        operations.append(op)
    counts['precedenze nel motore'] = sum(len(o['predecessors']) for o in operations)
    counts['precedenze sorgente'] = len(s['precedences'])

    sid = f'factory-bench-a-{start_d}' if a.anonymize else s['scenarioId']
    source = 'banco A (anonimizzato)' if a.anonymize else s['source']['database']
    scenario = {'schema_version': 1, 'id': sid,
                'start': local_iso(start_d, tz), 'end': local_iso(end_d, tz),
                'calendars': sorted(calendars.values(), key=lambda c: c['id']),
                'resources': sorted(resources.values(), key=lambda r: r['id']),
                'operations': operations, 'nestings': [],
                'settings': {'mode': a.mode, 'auto_backward_days': engine_backward_days,
                             'priority_weight': 10000, 'due_weight': 1, 'customer_weight': 1}}
    os.makedirs(a.out, exist_ok=True)
    dump = lambda name, v: json.dump(v, open(os.path.join(a.out, name), 'w', encoding='utf-8', newline='\n'), ensure_ascii=False, indent=1)
    dump('engine-scenario.json', scenario)
    dump('factory-template.json', dict(scenario, operations=[]))
    # Source fingerprint is required for transactional publishing of this exact snapshot.
    dump('conversion.json', {'source_sha256': hashlib.sha256(source_bytes).hexdigest(), 'scenario_id': sid,
                             'captured_at': s.get('source', {}).get('capturedAt'),
                             'options': {k: v for k, v in sorted(vars(a).items()) if k not in ('scenario', 'out')}})
    snap_lots = [{'CONUM': order_id(l['orderId']) if a.anonymize else l['orderId'], 'LOCOD': l['lotId'], 'LOCLP': l['parentLotId'] or 0} for l in s['lots']]
    # Preserve explicit JobPrevious links in the normalized snapshot, including
    # bridges across zero-duration phases. The snapshot adapter builds the
    # implicit sequence/lot links independently.
    snap_links = []
    for oid in sorted(active):
        current = ops[oid]
        for pid, kind, lag, contains_explicit in sorted(active_preds(oid)):
            if not contains_explicit:
                continue
            previous = ops[pid]
            snap_links.append({
                'CONUM': order_id(current['orderId']) if a.anonymize else current['orderId'],
                'LOCOD': current['lotId'], 'IDFAS': current['phaseId'],
                'Prev_CONUM': order_id(previous['orderId']) if a.anonymize else previous['orderId'],
                'Prev_LOCOD': previous['lotId'], 'Prev_IDFAS': previous['phaseId'],
                'LinkType': 1 if kind == 'SS' else 0,
                'lag_seconds': lag})
    dump('factory-snapshot.json', {'phases': phases, 'lots': snap_lots, 'links': snap_links})
    if a.anonymize:
        dump('anonymization-map.json', amap)
    with open(os.path.join(a.out, 'conversion-report.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(f"# Conversione {sid} -> contratto motore v0.2\n\n")
        f.write(f"Sorgente `{source}` estratta il {s['source']['capturedAt']}; inizio {start_d}, orizzonte {a.horizon_days} giorni, "
                f"modalita' {a.mode}, conto lavoro {a.subcontract_policy} (soglia {a.auto_backward_days} gg, margine consegna {a.delivery_margin_days} gg, avanzamento {a.progress_policy}){', anonimizzata' if a.anonymize else ''}.\n\n")
        f.write('| Voce | Valore |\n|---|---|\n')
        f.write(f'| calendari | {len(calendars)} |\n| risorse | {len(resources)} |\n')
        for k, v in sorted(counts.items()):
            f.write(f'| {k} | {v} |\n')
        f.write('\n' + (__doc__.split('Scelte (documentate nel report):')[1]) + '\n')
        for n in sorted(set(notes)):
            f.write(f'- {n}\n')
    print(json.dumps(counts, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
