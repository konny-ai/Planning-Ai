"""Pubblica in Factory il piano del motore, con le scritture del Salva legacy (docs/21 §21.4).

Ingressi: lo scenario.json di export_factory_scenario.py (NON anonimizzato), la cartella di
uscita di to_engine_v02.py (--engine-dir: engine-scenario.json e conversion.json) e il plan.json
calcolato su quell'engine-scenario. Il digest del piano e l'impronta dell'estrazione devono
coincidere: un piano non si pubblica con un'estrazione diversa da quella da cui e' stato calcolato.

Uscite (in --out):
  publish.sql         script T-SQL in un'unica transazione, rivedibile prima dell'esecuzione
  publish-report.md   conteggi, fasi non pubblicate e differenze volute rispetto al legacy

Per impostazione predefinita NON si collega al database. Con --execute esegue lo script e,
con --verify, rilegge il database e confronta il risultato con il piano. --execute richiede
--confirm-database uguale a --database e uguale al database da cui e' stato estratto lo
scenario: va usato solo su un clone (workflow Snapshot Factory, Codespace) finche' il
collaudo (#3) non autorizza la pubblicazione sul database del cliente.

Scritture, nell'ordine del legacy (CONUM, LOCOD, IDFAS):
  1. DELETE L_LAPN delle commesse pubblicate (il trigger azzera L_LARE.IDBLK);
  2. per fase: UPDATE A_LAV FPIAN='Y' e date pianificate; UPDATE L_LARE RIUSE/IDBLK per riga candidata
     (le fasi fuori piano ricevono solo le date, come milestone); INSERT L_LARE della risorsa usata
     quando e' un candidato aggiunto da una regola cliente (assignmentId 0, IDLARE=0: lo numera il trigger);
  3. per lotto: UPDATE A_LOT date; per commessa: UPDATE A_COM COPNF e date;
  4. INSERT L_LAPN, un blocco per segmento e per risorsa usata (IDINT=0: lo numera il trigger).
Nesting (--engine-scenario con i programmi, es. engine-scenario-nesting.json di build_scenario_cliente.py):
ogni fase membro riceve le date e gli stessi blocchi del programma, con IDPTC = programma A_NES e
RIPOC = impiego / numero di membri, come il Salva legacy.
Prima di scrivere, sotto lock, controlla che LASTO e LASTUPDATE delle fasi e le righe L_LARE
(risorsa, regola, percentuale) siano quelle dell'estrazione: se qualcosa e' cambiato annulla tutto
(THROW, nessuna scrittura). I blocchi delle fasi che il motore non ha pianificato restano intatti.
Un errore di comunicazione durante l'esecuzione rende l'esito incerto: si verifica con --verify
prima di ripetere.

Differenze volute rispetto al Salva legacy (riportate nel report):
  - transazione unica con XACT_ABORT, invece del commit per statement;
  - non riscrive le colonne caricate e invariate (tempi, n. risorse, FAPNF, priorita'), quindi
    non azzera LADTS/LASCH ne' L_CMPA.PASCH/PADTS: i vincoli dell'utente restano;
  - non ricrea JobPrevious (il motore non modifica i legami) e non tocca A_MAC.IDCAT;
  - DELETE L_LAPN per commessa, ma conservando i blocchi delle fasi non pianificate dal motore;
  - le fasi fuori piano (durata zero, FAPNF='N', gia' consuntivate) sono datate come milestone
    dai legami (fine del predecessore piu' tardivo); quelle isolate all'inizio della pianificazione;
  - le risorse scelte fra i candidati di una regola cliente (es. CLIENTE: abilitati al tipo fase) senza
    riga in L_LARE ricevono una nuova riga L_LARE usata (RIUSE='Y'); i candidati non usati no.
"""
import argparse
import collections
import hashlib
import json
import os
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

KIND = {'OP': 'OPERATOR', 'MAC': 'MACHINE', 'SUP': 'SUPPLIER'}
KIND_ORDER = {'MACHINE': 0, 'OPERATOR': 1, 'SUPPLIER': 2}
CHUNK = 900  # righe per INSERT ... VALUES (limite SQL Server: 1000)


class PublishError(Exception):
    pass


def local(value, tz):
    """Instant ISO del motore -> ora locale Factory (date/time senza fuso)."""
    moment = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if moment.tzinfo is None:
        raise PublishError(f'istante senza fuso nel piano: {value}')
    return moment.astimezone(ZoneInfo(tz)).replace(tzinfo=None)


def whole(value, name):
    if type(value) is not int:
        raise PublishError(f'{name}: intero atteso, trovato {value!r}')
    return value


def milestones(scenario, phases, expected, skipped, unscheduled=frozenset()):
    """Date le fasi dell'universo fuori piano (durata zero, FAPNF='N', gia' consuntivate).

    Regola dominante del Salva legacy sul banco HF: istante unico (inizio = fine) alla fine del
    predecessore piu' tardivo, anche attraverso catene di fasi fuori piano; senza predecessori,
    all'inizio del successore piu' precoce. Le fasi isolate (in genere FAPNF='N') vanno all'inizio
    della pianificazione, come fa il legacy per la maggior parte di esse.
    """
    begin = scenario.get('planning', {}).get('start')
    begin = datetime.fromisoformat(begin).replace(tzinfo=None) if begin else None
    planned = {p['IDLAV']: p for p in phases}
    preds, succs = collections.defaultdict(set), collections.defaultdict(set)
    for link in scenario.get('precedences', []):
        preds[link['successorOperationId']].add(link['predecessorOperationId'])
        succs[link['predecessorOperationId']].add(link['successorOperationId'])
    pending = {o['operationId']: o for o in scenario['operations']
               if o['operationId'] not in planned and o['operationId'] not in unscheduled}

    def reach(oid, links, pick, attr, seen):
        values = []
        for other in links[oid]:
            if other in planned:
                values.append(planned[other][attr])
            elif other in pending and other not in seen:
                seen.add(other)
                found = reach(other, links, pick, attr, seen)
                if found is not None:
                    values.append(found)
        return pick(values) if values else None

    result = []
    for oid, o in sorted(pending.items()):
        moment = reach(oid, preds, max, 'end', {oid})
        if moment is None:
            moment = reach(oid, succs, min, 'start', {oid})
        if moment is None and begin is None:
            skipped['fasi fuori piano isolate senza inizio pianificazione nello scenario: date invariate'] += 1
            continue
        if moment is None:
            moment = begin
            skipped["fasi fuori piano isolate (senza legami con fasi pianificate): all'inizio della pianificazione"] += 1
        key = tuple(whole(o[k], k) for k in ('orderId', 'lotId', 'phaseId'))
        expected[whole(oid, 'IDLAV')] = (whole(o['statusCode'], 'LASTO'), o.get('sourceLastUpdate'))
        result.append({'key': key, 'IDLAV': oid, 'start': moment, 'end': moment, 'mode': 'milestone'})
    linked = len(result) - skipped.get("fasi fuori piano isolate (senza legami con fasi pianificate): all'inizio della pianificazione", 0)
    if linked:
        skipped['fasi fuori piano datate come milestone dai legami (inizio = fine)'] = linked
    return result


def usage_of(row):
    """RIPOC esportato (ISNULL(RIPOC, 100)): uno zero esplicito resta zero."""
    return 100.0 if row.get('usagePct') is None else float(row['usagePct'])


def same_scenario(engine_id, scenario_id):
    """Lo scenario motore e' quello dell'estrazione o un suo derivato dichiarato (es. '<id>-nesting')."""
    return bool(scenario_id) and (engine_id == scenario_id or str(engine_id).startswith(f'{scenario_id}-'))


def nest_program(assignment, operation):
    """(IDPTC, numero di membri) di un'assegnazione: per 'nest:NEST-<id>' il programma A_NES."""
    task = str(assignment.get('task_id', ''))
    if task.startswith('nest:'):
        program = task.split(':', 1)[1].removeprefix('NEST-')
        if not program.isdigit():
            raise PublishError(f'nesting {task}: il programma non e\' un IDNES numerico')
        return int(program), len(assignment['operation_ids'])
    return int(operation.get('workplanId') or 0), 1


def check_derived(base, derived):
    """Uno scenario derivato (nesting) deve avere esattamente le fasi della conversione."""
    if {o['id'] for o in base['operations']} != {o['id'] for o in derived['operations']}:
        raise PublishError("lo scenario con i nesting non ha le stesse fasi dell'engine-scenario convertito")


def check_binding(scenario, plan, engine_scenario, conversion, source_bytes):
    """Il piano deve venire dall'engine-scenario convertito proprio da questa estrazione."""
    from planning_factory.adapters.json_io import loads, scenario_digest
    if conversion.get('source_sha256') != hashlib.sha256(source_bytes).hexdigest():
        raise PublishError("l'engine-scenario non e' stato convertito da questo scenario.json (impronta diversa)")
    if not same_scenario(engine_scenario.get('id'), scenario.get('scenarioId')):
        raise PublishError('engine-scenario anonimizzato o di un altro scenario: riconvertire senza --anonymize')
    parsed = loads(json.dumps(engine_scenario))
    if plan.get('scenario_digest') != scenario_digest(parsed):
        raise PublishError("il piano non e' stato calcolato su questo engine-scenario (digest diverso)")
    # A matching scenario digest does not authenticate the assignments: verify the
    # entire plan independently before converting any assignment to SQL.
    from planning_factory.adapters.json_io import plan_loads
    from planning_factory.engine.verification import verify_plan
    try:
        parsed_plan = plan_loads(json.dumps(plan))
        errors = verify_plan(parsed, parsed_plan)
    except (ValueError, TypeError, KeyError) as exc:
        raise PublishError(f'piano non valido: {exc}') from exc
    if errors:
        raise PublishError(f'piano non valido: {errors[:5]}')


def build_changeset(scenario, plan, tz=None):
    """Calcola le scritture senza toccare il database (funzione pura, testabile)."""
    tz = tz or scenario.get('planning', {}).get('timeZone') or 'Europe/Rome'
    if not same_scenario(plan.get('scenario_id'), scenario.get('scenarioId')):
        raise PublishError(f"il piano e' dello scenario {plan.get('scenario_id')!r}, "
                           f"non di {scenario.get('scenarioId')!r}: riconvertire senza --anonymize")
    ops = {}
    for o in scenario['operations']:
        ops[f"{o['orderId']}/{o['lotId']}/{o['phaseId']}"] = o
    rows = collections.defaultdict(list)
    # assignmentId 0 = candidato aggiunto da una regola cliente (es. CLIENTE: abilitati al tipo fase),
    # senza riga in L_LARE: non si aggiorna una riga che non esiste.
    derived = collections.defaultdict(dict)
    for r in scenario['operationResources']:
        if r['assignmentId']:
            rows[r['operationId']].append(r)
        else:
            derived[r['operationId']][(r['resourceKind'], str(r['resourceCode']))] = r
    lare_new = []
    ordered = collections.defaultdict(set)
    promised = collections.defaultdict(set)
    for so in scenario.get('subcontractOrders', []):
        ordered[so['operationId']].add(str(so['supplierCode']))
        if so.get('promisedDate'):
            promised[(so['operationId'], str(so['promisedDate'])[:10])].add(str(so['supplierCode']))

    phases, lare, blocks, skipped = [], [], [], collections.Counter()
    expected = {}
    seen = set()
    keep = set()   # fasi non pianificate dal motore: i loro blocchi salvati non si toccano
    for u in plan.get('unscheduled', []):
        for key in u.get('operation_ids', []):
            if key not in ops:
                raise PublishError(f'fase non pianificata {key} assente nello scenario')
            keep.add(key)
    for a in plan['assignments']:
        for key in a['operation_ids']:
            o = ops.get(key)
            if o is None:
                raise PublishError(f'fase {key} del piano assente nello scenario (piano anonimizzato o di un altro database?)')
            if key in seen:
                raise PublishError(f'fase {key} presente due volte nel piano')
            seen.add(key)
            if a.get('external_end'):
                start = end = local(a['external_end'], tz)
            elif a['segments']:
                start = local(a['segments'][0]['start'], tz)
                end = local(a['segments'][-1]['end'], tz)
            else:
                skipped['assegnazioni senza segmenti ne fine esterna'] += 1
                continue
            conum, locod, idfas = (whole(o[k], k) for k in ('orderId', 'lotId', 'phaseId'))
            idlav = whole(o['operationId'], 'IDLAV')
            expected[idlav] = (whole(o['statusCode'], 'LASTO'), o.get('sourceLastUpdate'))
            phases.append({'key': (conum, locod, idfas), 'IDLAV': idlav, 'start': start, 'end': end,
                           'mode': a['effective_mode']})
            used = set()
            for d in a['demands']:
                prefix, _, code = d['resource_id'].partition(':')
                if prefix in KIND:
                    used.add((KIND[prefix], code))
            segments = a['segments']
            if a.get('external_end'):
                # Come il legacy: il fornitore dell'ordine di conto lavoro (o il primo ammesso) e' usato,
                # con un blocco di durata zero alla data promessa.
                matching = promised[(o['operationId'], start.date().isoformat())]
                suppliers = sorted((r for r in rows[o['operationId']] if r['resourceKind'] == 'SUPPLIER'
                                    and r.get('assignmentRule') != 'EXCLUDED'),
                                   key=lambda r: (str(r['resourceCode']) not in matching,
                                                  str(r['resourceCode']) not in ordered[o['operationId']],
                                                  str(r['resourceCode']), r['assignmentId']))
                used = {('SUPPLIER', str(suppliers[0]['resourceCode']))} if suppliers else set()
                segments = [{'start': a['external_end'], 'end': a['external_end']}]
            candidates = sorted(rows[o['operationId']], key=lambda r: (KIND_ORDER.get(r['resourceKind'], 9),
                                                                        str(r['resourceCode']), r['assignmentId']))
            by_resource = {}
            for idblk, r in enumerate(candidates, 1):
                res = (r['resourceKind'], str(r['resourceCode']))
                flag = 'Y' if res in used and res not in by_resource else 'N'
                if flag == 'Y':
                    by_resource[res] = (idblk, r)
                lare.append({'IDLARE': whole(r['assignmentId'], 'IDLARE'), 'key': (conum, locod, idfas),
                             'RIUSE': flag, 'IDBLK': idblk, 'kind': r['resourceKind'], 'code': str(r['resourceCode']),
                             'rule': r.get('assignmentRule') or 'ALLOWED', 'usage': usage_of(r)})
            missing = used - set(by_resource)
            if missing and not a.get('external_end') and missing <= derived[o['operationId']].keys():
                # Risorsa scelta fra i candidati di una regola cliente: nuova riga L_LARE usata,
                # numerata dopo le candidate esistenti.
                for res in sorted(missing):
                    r = derived[o['operationId']][res]
                    idblk = len(candidates) + 1 + sum(1 for x in lare_new if x['key'] == (conum, locod, idfas))
                    by_resource[res] = (idblk, r)
                    lare_new.append({'key': (conum, locod, idfas), 'IDBLK': idblk, 'kind': res[0],
                                     'code': res[1], 'usage': usage_of(r)})
                missing = set()
            if a.get('external_end'):
                skipped['fasi esterne ancorate alla data promessa' +
                        (': blocco di durata zero sul fornitore' if used else ' senza fornitore in L_LARE: date senza blocchi')] += 1
            elif not used:
                skipped['fasi senza risorsa reale (NO_RESOURCE): date senza blocchi'] += 1
            elif missing and {kind for kind, _ in missing} == {'SUPPLIER'} and not by_resource:
                # Fornitore preso dall'ordine di conto lavoro: il legacy salva le date senza blocchi.
                skipped["fasi esterne con fornitore dall'ordine di conto lavoro (senza L_LARE): date senza blocchi"] += 1
            elif missing:
                raise PublishError(f'fase {key}: risorse del piano senza riga L_LARE {sorted(missing)}')
            # Nesting: stessi blocchi su ogni membro, IDPTC = programma, impiego ripartito sui membri.
            program, share = nest_program(a, o)
            for idblk, r in sorted(by_resource.values(), key=lambda x: x[0]):
                for seg in segments:
                    blocks.append({'key': (conum, locod, idfas), 'IDBLK': idblk,
                                   'RIPOC': round(usage_of(r) / share, 2), 'IDPTC': program,
                                   'start': local(seg['start'], tz), 'end': local(seg['end'], tz)})
    derived_milestones = milestones(scenario, phases, expected, skipped,
                                    frozenset(ops[k]['operationId'] for k in keep))
    phases += derived_milestones
    # A previously saved milestone must not retain RIUSE='Y' after its blocks
    # are deleted. Mark every exported candidate unused, as for regular phases.
    for p in derived_milestones:
        candidates = sorted(rows[p['IDLAV']], key=lambda r: (KIND_ORDER.get(r['resourceKind'], 9),
                                                              str(r['resourceCode']), r['assignmentId']))
        for idblk, r in enumerate(candidates, 1):
            lare.append({'IDLARE': whole(r['assignmentId'], 'IDLARE'), 'key': p['key'],
                         'RIUSE': 'N', 'IDBLK': idblk, 'kind': r['resourceKind'],
                         'code': str(r['resourceCode']),
                         'rule': r.get('assignmentRule') or 'ALLOWED', 'usage': usage_of(r)})
    phases.sort(key=lambda p: p['key'])
    lare.sort(key=lambda r: (r['key'], r['IDBLK']))
    blocks.sort(key=lambda b: (b['key'], b['IDBLK'], b['start']))
    lots, orders = {}, {}
    for p in phases:
        conum, locod, _ = p['key']
        for table, k in ((lots, (conum, locod)), (orders, conum)):
            cur = table.get(k)
            table[k] = (min(cur[0], p['start']), max(cur[1], p['end'])) if cur else (p['start'], p['end'])
    # COPNF: tipo dell'ultima lavorazione salvata della commessa (docs/21 §21.7), 1 = a ritroso.
    last_mode = {}
    for p in phases:
        if p['mode'] != 'milestone' or p['key'][0] not in last_mode:
            last_mode[p['key'][0]] = 1 if p['mode'] == 'backward' else 0
    unscheduled = [u.get('task_id') for u in plan.get('unscheduled', [])]
    keep = sorted(tuple(whole(ops[k][c], c) for c in ('orderId', 'lotId', 'phaseId')) for k in keep)
    return {'phases': phases, 'lare': lare, 'lare_new': lare_new, 'blocks': blocks, 'expected': expected,
            'keep': keep,
            'lots': [{'key': k, 'start': v[0], 'end': v[1]} for k, v in sorted(lots.items())],
            'orders': [{'CONUM': k, 'start': v[0], 'end': v[1], 'COPNF': last_mode[k]} for k, v in sorted(orders.items())],
            'skipped': dict(skipped), 'unscheduled': unscheduled,
            'scenario_id': scenario['scenarioId'], 'database': scenario.get('source', {}).get('database'),
            'plan_digest': plan.get('scenario_digest')}


def d(value):
    return f"'{value.date().isoformat()}'"


def t(value):
    return f"'{value.time().replace(microsecond=0).isoformat()}'"


def text(value):
    return "N'" + str(value).replace("'", "''") + "'"


def lastupdate(value):
    if value is None:
        return 'NULL'
    parsed = datetime.fromisoformat(value)
    return f"CONVERT(datetime2(6), '{parsed.replace(tzinfo=None).isoformat(timespec='microseconds')}', 126)"


def values(rows):
    for i in range(0, len(rows), CHUNK):
        yield ',\n  '.join(rows[i:i + CHUNK])


def render_sql(cs):
    """Script T-SQL autosufficiente: una transazione, controlli di concorrenza, scritture legacy."""
    out = [f"-- Pubblicazione piano {cs['scenario_id']} (digest scenario motore {cs['plan_digest']})",
           f"-- {len(cs['phases'])} fasi, {len(cs['lare'])} righe L_LARE, {len(cs['lare_new'])} nuove righe L_LARE, "
           f"{len(cs['blocks'])} blocchi L_LAPN, "
           f"{len(cs['lots'])} lotti, {len(cs['orders'])} commesse",
           'SET NOCOUNT ON;', 'SET XACT_ABORT ON;', 'BEGIN TRANSACTION;',
           'DECLARE @msg nvarchar(400);',
           'DECLARE @lav TABLE (IDLAV int PRIMARY KEY, LASTO smallint NOT NULL, LASTUPDATE datetime2(6) NULL);']
    rows = [f'({idlav},{lasto},{lastupdate(lu)})' for idlav, (lasto, lu) in sorted(cs['expected'].items())]
    out += [f'INSERT INTO @lav (IDLAV, LASTO, LASTUPDATE) VALUES\n  {chunk};' for chunk in values(rows)]
    out.append('DECLARE @lare TABLE (IDLARE int PRIMARY KEY, CONUM int NOT NULL, LOCOD int NOT NULL, IDFAS int NOT NULL, '
               'KIND nvarchar(10) NOT NULL, CODE nvarchar(100) NOT NULL, RULE_ nvarchar(10) NOT NULL, RIPOC numeric(6,2) NOT NULL);')
    rows = [f"({r['IDLARE']},{r['key'][0]},{r['key'][1]},{r['key'][2]},{text(r['kind'])},{text(r['code'])},"
            f"{text(r['rule'])},{r['usage']:.2f})" for r in cs['lare']]
    out += [f'INSERT INTO @lare (IDLARE, CONUM, LOCOD, IDFAS, KIND, CODE, RULE_, RIPOC) VALUES\n  {chunk};' for chunk in values(rows)]
    out.append('DECLARE @publish TABLE (CONUM int NOT NULL, LOCOD int NOT NULL, IDFAS int NOT NULL, PRIMARY KEY (CONUM, LOCOD, IDFAS));')
    rows = [f"({p['key'][0]},{p['key'][1]},{p['key'][2]})" for p in cs['phases']]
    out += [f'INSERT INTO @publish (CONUM, LOCOD, IDFAS) VALUES\n  {chunk};' for chunk in values(rows)]
    out.append('DECLARE @keep TABLE (CONUM int NOT NULL, LOCOD int NOT NULL, IDFAS int NOT NULL, PRIMARY KEY (CONUM, LOCOD, IDFAS));')
    rows = [f'({c},{l},{f})' for c, l, f in cs['keep']]
    out += [f'INSERT INTO @keep (CONUM, LOCOD, IDFAS) VALUES\n  {chunk};' for chunk in values(rows)]
    out += [
        '-- Concorrenza: le fasi e le risorse devono essere ancora quelle dell\'estrazione.',
        'DECLARE @stale int = (SELECT COUNT(*) FROM @lav e LEFT JOIN dbo.A_LAV l WITH (UPDLOCK, HOLDLOCK) ON l.IDLAV = e.IDLAV',
        '  WHERE l.IDLAV IS NULL OR l.LASTO <> e.LASTO',
        '     OR (l.LASTUPDATE IS NULL AND e.LASTUPDATE IS NOT NULL) OR (l.LASTUPDATE IS NOT NULL AND e.LASTUPDATE IS NULL)',
        # Confronto per intervallo: DATEDIFF(millisecond) va in overflow oltre ~24 giorni di distanza.
        '     OR CAST(l.LASTUPDATE AS datetime2(6)) < DATEADD(millisecond, -3, e.LASTUPDATE)',
        '     OR CAST(l.LASTUPDATE AS datetime2(6)) > DATEADD(millisecond, 3, e.LASTUPDATE));',
        'IF @stale > 0',
        'BEGIN',
        "  SET @msg = CONCAT(@stale, N' fasi modificate dopo l''estrazione: ripetere estrazione e pianificazione');",
        '  THROW 50001, @msg, 1;',
        'END;',
        'DECLARE @lost int = (SELECT COUNT(*) FROM @lare e LEFT JOIN dbo.L_LARE r WITH (UPDLOCK, HOLDLOCK) ON r.IDLARE = e.IDLARE',
        '  AND r.CONUM = e.CONUM AND r.LOCOD = e.LOCOD AND r.IDFAS = e.IDFAS WHERE r.IDLARE IS NULL',
        "     OR ISNULL(CASE WHEN r.MACOD IS NOT NULL THEN N'MACHINE' WHEN r.OPCOD IS NOT NULL THEN N'OPERATOR'",
        "                    WHEN r.RECOD IS NOT NULL THEN N'SUPPLIER' END, N'') <> e.KIND",
        "     OR ISNULL(RTRIM(COALESCE(r.MACOD, r.OPCOD, r.RECOD)), N'') <> e.CODE",
        "     OR CASE ISNULL(r.RITPU, 0) WHEN 1 THEN N'FORCED' WHEN 2 THEN N'EXCLUDED' ELSE N'ALLOWED' END <> e.RULE_",
        '     OR ISNULL(r.RIPOC, 100) <> e.RIPOC);',
        'IF @lost > 0',
        'BEGIN',
        "  SET @msg = CONCAT(@lost, N' righe L_LARE non piu'' presenti o cambiate: ripetere estrazione e pianificazione');",
        '  THROW 50002, @msg, 1;',
        'END;',
        'DECLARE @added int = (SELECT COUNT(*) FROM dbo.L_LARE r WITH (UPDLOCK, HOLDLOCK)',
        '  INNER JOIN @publish p ON p.CONUM = r.CONUM AND p.LOCOD = r.LOCOD AND p.IDFAS = r.IDFAS',
        '  LEFT JOIN @lare e ON e.IDLARE = r.IDLARE WHERE e.IDLARE IS NULL);',
        'IF @added > 0',
        'BEGIN',
        "  SET @msg = CONCAT(@added, N' nuove righe L_LARE dopo l''estrazione: ripetere estrazione e pianificazione');",
        '  THROW 50003, @msg, 1;',
        'END;',
    ]
    out.append('DELETE b FROM dbo.L_LAPN b INNER JOIN @publish p '
               'ON b.CONUM = p.CONUM AND b.LOCOD = p.LOCOD AND b.IDFAS = p.IDFAS;')
    lare = collections.defaultdict(list)
    for r in cs['lare']:
        lare[r['key']].append(r)
    lare_new = collections.defaultdict(list)
    for r in cs['lare_new']:
        lare_new[r['key']].append(r)
    for p in cs['phases']:
        c, l, f = p['key']
        out.append(f"UPDATE dbo.A_LAV SET FPIAN='Y', LADIP={d(p['start'])}, LAHIP={t(p['start'])}, "
                   f"LADFP={d(p['end'])}, LAHFP={t(p['end'])} WHERE CONUM={c} AND LOCOD={l} AND IDFAS={f};")
        for r in lare[p['key']]:
            out.append(f"UPDATE dbo.L_LARE SET RIUSE='{r['RIUSE']}', IDBLK={r['IDBLK']} WHERE IDLARE={r['IDLARE']};")
        for r in lare_new[p['key']]:
            # Valori delle righe standard Factory (RIPAS 10000, contatori a 0); IDLARE=0 lo numera il trigger.
            codes = {'MACHINE': 'NULL,{},NULL', 'OPERATOR': 'NULL,NULL,{}', 'SUPPLIER': '{},NULL,NULL'}[r['kind']]
            out.append('INSERT INTO dbo.L_LARE (CONUM,LOCOD,IDFAS,RECOD,MACOD,OPCOD,RIPAS,RIUSE,IDBLK,IDNUM,RIPOC,'
                       'OPATR,TMBNS,INEFF,RPRIO,RITPU,IDLARE) '
                       f"VALUES ({c},{l},{f},{codes.format(text(r['code']))},10000,'Y',{r['IDBLK']},0,"
                       f"{r['usage']:.2f},0,0,0,0,0,0);")
    # Recompute parent envelopes from every dated phase in the database, not
    # only the subset exported for this scenario. Kept and filtered-out phases
    # must not be erased from A_LOT/A_COM dates by a partial publication.
    for lot in cs['lots']:
        c, l = lot['key']
        out.append(
            'UPDATE dst SET LODIP = first_.LADIP, LOHIP = first_.LAHIP, '
            'LODFP = last_.LADFP, LOHFP = last_.LAHFP '
            'FROM dbo.A_LOT dst '
            'OUTER APPLY (SELECT TOP (1) LADIP, LAHIP FROM dbo.A_LAV '
            f'WHERE CONUM={c} AND LOCOD={l} AND LADIP IS NOT NULL AND LAHIP IS NOT NULL '
            'ORDER BY LADIP, LAHIP) first_ '
            'OUTER APPLY (SELECT TOP (1) LADFP, LAHFP FROM dbo.A_LAV '
            f'WHERE CONUM={c} AND LOCOD={l} AND LADFP IS NOT NULL AND LAHFP IS NOT NULL '
            'ORDER BY LADFP DESC, LAHFP DESC) last_ '
            f'WHERE dst.CONUM={c} AND dst.LOCOD={l};')
    for o in cs['orders']:
        c = o['CONUM']
        out.append(
            f'UPDATE dst SET COPNF=CASE WHEN NOT EXISTS (SELECT 1 FROM dbo.A_LAV q '
            f'WHERE q.CONUM={c} AND NOT EXISTS (SELECT 1 FROM @publish p '
            'WHERE p.CONUM=q.CONUM AND p.LOCOD=q.LOCOD AND p.IDFAS=q.IDFAS)) '
            f"THEN {o['COPNF']} ELSE dst.COPNF END, "
            'CODIP = first_.LADIP, COHIP = first_.LAHIP, '
            'CODFP = last_.LADFP, COHFP = last_.LAHFP '
            'FROM dbo.A_COM dst '
            'OUTER APPLY (SELECT TOP (1) LADIP, LAHIP FROM dbo.A_LAV '
            f'WHERE CONUM={c} AND LADIP IS NOT NULL AND LAHIP IS NOT NULL '
            'ORDER BY LADIP, LAHIP) first_ '
            'OUTER APPLY (SELECT TOP (1) LADFP, LAHFP FROM dbo.A_LAV '
            f'WHERE CONUM={c} AND LADFP IS NOT NULL AND LAHFP IS NOT NULL '
            'ORDER BY LADFP DESC, LAHFP DESC) last_ '
            f'WHERE dst.CONUM={c};')
    for b in cs['blocks']:
        c, l, f = b['key']
        out.append('INSERT INTO dbo.L_LAPN (CONUM,LOCOD,IDFAS,IDBLK,IDFRZ,IDINT,IDPTC,RIPOC,BLDIP,BLHIP,BLDFP,BLHFP) '
                   f"VALUES ({c},{l},{f},{b['IDBLK']},0,0,{b['IDPTC']},{b['RIPOC']:.2f},"
                   f"{d(b['start'])},{t(b['start'])},{d(b['end'])},{t(b['end'])});")
    out.append('COMMIT TRANSACTION;')
    return '\n'.join(out) + '\n'


def render_report(cs):
    lines = [f"# Pubblicazione del piano {cs['scenario_id']}", '',
             f"Database di origine `{cs['database']}`; digest scenario motore `{cs['plan_digest']}`.", '',
             '| Voce | Valore |', '|---|---|',
             f"| fasi pubblicate (A_LAV) | {len(cs['phases'])} |",
             f"| righe risorsa aggiornate (L_LARE) | {len(cs['lare'])} |",
             f"| di cui usate (RIUSE='Y') | {sum(1 for r in cs['lare'] if r['RIUSE'] == 'Y')} |",
             f"| righe L_LARE inserite (risorse da regola cliente) | {len(cs['lare_new'])} |",
             f"| blocchi inseriti (L_LAPN) | {len(cs['blocks'])} |",
             f"| lotti (A_LOT) | {len(cs['lots'])} |",
             f"| commesse (A_COM) | {len(cs['orders'])} |",
             f"| commesse a ritroso (COPNF=1) | {sum(o['COPNF'] for o in cs['orders'])} |",
             f"| operazioni non pianificate dal motore (non pubblicate) | {len(cs['unscheduled'])} |"]
    lines += [f'| {k} | {v} |' for k, v in sorted(cs['skipped'].items())]
    differences = __doc__.split('Differenze volute rispetto al Salva legacy (riportate nel report):')[1]
    items = [x.strip() for x in ' '.join(differences.split()).lstrip('- ').split(' - ')]
    lines += ['', '## Differenze volute rispetto al Salva legacy', '', *[f'- {x}' for x in items], '']
    return '\n'.join(lines)


def connect(a):
    import pyodbc
    auth = f'UID={a.user};PWD={a.password}' if a.user else 'Trusted_Connection=yes'
    return pyodbc.connect(f'DRIVER={{{a.driver}}};SERVER={a.server};DATABASE={a.database};{auth}', autocommit=True)


class UncertainOutcome(Exception):
    """La connessione e' caduta durante l'esecuzione: il COMMIT puo' essere avvenuto o no."""


def execute(cn, script, cs):
    """Esegue lo script e ritorna il riepilogo. Un errore SQL prima del COMMIT annulla tutto
    (XACT_ABORT); un errore di comunicazione (SQLSTATE 08xxx, HYT00) rende l'esito incerto."""
    cur = cn.cursor()
    try:
        cur.execute(script)
        while cur.nextset():
            pass
    except Exception as exc:
        state = str(exc.args[0]) if getattr(exc, 'args', None) else ''
        if state.startswith('08') or state in ('HYT00', 'HYT01'):
            raise UncertainOutcome(str(exc)) from exc
        raise
    return {'fasi': len(cs['phases']), 'righe_L_LARE': len(cs['lare']), 'nuove_righe_L_LARE': len(cs['lare_new']),
            'blocchi_L_LAPN': len(cs['blocks']),
            'lotti': len(cs['lots']), 'commesse': len(cs['orders'])}


def verify(cn, cs):
    """Rilegge il database e conta le differenze rispetto al piano pubblicato."""
    cur = cn.cursor()
    problems = collections.Counter()
    expected = {p['key']: p for p in cs['phases']}
    seen_phases = set()
    for i in range(0, len(cs['phases']), CHUNK):
        ids = ','.join(str(p['IDLAV']) for p in cs['phases'][i:i + CHUNK])
        cur.execute(f'SELECT CONUM, LOCOD, IDFAS, FPIAN, LADIP, LAHIP, LADFP, LAHFP FROM dbo.A_LAV WHERE IDLAV IN ({ids})')
        for c, l, f, fpian, ladip, lahip, ladfp, lahfp in cur.fetchall():
            seen_phases.add((c, l, f))
            p = expected[(c, l, f)]
            if fpian != 'Y':
                problems['A_LAV.FPIAN diverso da Y'] += 1
            if (ladip, lahip) != (p['start'].date(), p['start'].time().replace(microsecond=0)):
                problems['A_LAV inizio diverso dal piano'] += 1
            if (ladfp, lahfp) != (p['end'].date(), p['end'].time().replace(microsecond=0)):
                problems['A_LAV fine diversa dal piano'] += 1
    if missing_phases := len(set(expected) - seen_phases):
        problems['A_LAV righe mancanti'] += missing_phases
    want = {r['IDLARE']: r for r in cs['lare']}
    seen_lare = set()
    ids = sorted(want)
    for i in range(0, len(ids), CHUNK):
        cur.execute(f"SELECT IDLARE, RIUSE, IDBLK FROM dbo.L_LARE WHERE IDLARE IN ({','.join(map(str, ids[i:i + CHUNK]))})")
        for idlare, riuse, idblk in cur.fetchall():
            seen_lare.add(idlare)
            if (riuse, idblk) != (want[idlare]['RIUSE'], want[idlare]['IDBLK']):
                problems['L_LARE RIUSE/IDBLK diversi'] += 1
    if missing_resources := len(set(want) - seen_lare):
        problems['L_LARE righe mancanti'] += missing_resources
    column = {'MACHINE': 'MACOD', 'OPERATOR': 'OPCOD', 'SUPPLIER': 'RECOD'}
    for r in cs['lare_new']:
        c, l, f = r['key']
        cur.execute(f"SELECT COUNT(*) FROM dbo.L_LARE WHERE CONUM={c} AND LOCOD={l} AND IDFAS={f} "
                    f"AND RTRIM({column[r['kind']]})=? AND RIUSE='Y' AND IDBLK=? AND IDLARE>0", r['code'], r['IDBLK'])
        if cur.fetchone()[0] != 1:
            problems['L_LARE righe inserite mancanti o duplicate'] += 1
    # Independently compare parent dates to the full persisted phase envelope.
    # Including phases outside the exported subset prevents false acceptance of
    # silently shortened order/lot dates.
    for lot in cs['lots']:
        c, l = lot['key']
        cur.execute(
            'SELECT dst.LODIP, dst.LOHIP, dst.LODFP, dst.LOHFP, '
            'first_.LADIP, first_.LAHIP, last_.LADFP, last_.LAHFP '
            'FROM dbo.A_LOT dst '
            'OUTER APPLY (SELECT TOP (1) LADIP, LAHIP FROM dbo.A_LAV '
            f'WHERE CONUM={c} AND LOCOD={l} AND LADIP IS NOT NULL AND LAHIP IS NOT NULL '
            'ORDER BY LADIP, LAHIP) first_ '
            'OUTER APPLY (SELECT TOP (1) LADFP, LAHFP FROM dbo.A_LAV '
            f'WHERE CONUM={c} AND LOCOD={l} AND LADFP IS NOT NULL AND LAHFP IS NOT NULL '
            'ORDER BY LADFP DESC, LAHFP DESC) last_ '
            f'WHERE dst.CONUM={c} AND dst.LOCOD={l}')
        row = cur.fetchone()
        if row is None:
            problems['A_LOT righe mancanti'] += 1
        elif tuple(row[:4]) != tuple(row[4:]):
            problems['A_LOT date diverse dalle fasi'] += 1

    published_counts = collections.Counter(p['key'][0] for p in cs['phases'])
    for order in cs['orders']:
        c = order['CONUM']
        cur.execute(
            'SELECT dst.COPNF, dst.CODIP, dst.COHIP, dst.CODFP, dst.COHFP, '
            'first_.LADIP, first_.LAHIP, last_.LADFP, last_.LAHFP '
            'FROM dbo.A_COM dst '
            'OUTER APPLY (SELECT TOP (1) LADIP, LAHIP FROM dbo.A_LAV '
            f'WHERE CONUM={c} AND LADIP IS NOT NULL AND LAHIP IS NOT NULL '
            'ORDER BY LADIP, LAHIP) first_ '
            'OUTER APPLY (SELECT TOP (1) LADFP, LAHFP FROM dbo.A_LAV '
            f'WHERE CONUM={c} AND LADFP IS NOT NULL AND LAHFP IS NOT NULL '
            'ORDER BY LADFP DESC, LAHFP DESC) last_ '
            f'WHERE dst.CONUM={c}')
        row = cur.fetchone()
        if row is None:
            problems['A_COM righe mancanti'] += 1
            continue
        if tuple(row[1:5]) != tuple(row[5:9]):
            problems['A_COM date diverse dalle fasi'] += 1
        cur.execute(f'SELECT COUNT(*) FROM dbo.A_LAV WHERE CONUM={c}')
        if cur.fetchone()[0] == published_counts[c] and row[0] != order['COPNF']:
            problems['A_COM COPNF diverso dal piano'] += 1

    conums = sorted({p['key'][0] for p in cs['phases']})
    kept = set(cs.get('keep', ()))
    got = collections.Counter()
    idints = []
    for i in range(0, len(conums), CHUNK):
        cur.execute('SELECT CONUM, LOCOD, IDFAS, IDBLK, IDINT, BLDIP, BLHIP, BLDFP, BLHFP, RIPOC, IDPTC FROM dbo.L_LAPN '
                    f"WHERE CONUM IN ({','.join(map(str, conums[i:i + CHUNK]))})")
        for c, l, f, idblk, idint, bldip, blhip, bldfp, blhfp, ripoc, idptc in cur.fetchall():
            idints.append(idint)
            if (c, l, f) in kept or (c, l, f) not in expected:
                continue
            got[(c, l, f, idblk, bldip, blhip, bldfp, blhfp, round(float(ripoc or 0), 2), idptc or 0)] += 1
    want_blocks = collections.Counter(
        (*b['key'], b['IDBLK'], b['start'].date(), b['start'].time().replace(microsecond=0),
         b['end'].date(), b['end'].time().replace(microsecond=0), round(b['RIPOC'], 2), b['IDPTC']) for b in cs['blocks'])
    if got != want_blocks:
        problems['L_LAPN blocchi diversi dal piano (fase, IDBLK, date, RIPOC, IDPTC)'] += sum(
            ((got - want_blocks) + (want_blocks - got)).values())
    if any(not x for x in idints) or len(set(idints)) != len(idints):
        problems['L_LAPN IDINT nulli o duplicati'] += 1
    return dict(problems), sum(got.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('scenario', help='scenario.json di export_factory_scenario.py')
    ap.add_argument('plan', help='plan.json del motore')
    ap.add_argument('--engine-dir', required=True,
                    help='cartella di to_engine_v02.py con engine-scenario.json e conversion.json del piano')
    ap.add_argument('--engine-scenario', default='engine-scenario.json',
                    help="file dello scenario motore nella --engine-dir su cui e' calcolato il piano "
                         '(es. engine-scenario-nesting.json: stesse fasi piu\' i programmi di nesting)')
    ap.add_argument('--out', required=True)
    ap.add_argument('--execute', action='store_true', help='esegue lo script sul database (solo cloni)')
    ap.add_argument('--verify', action='store_true', help='rilegge il database e lo confronta col piano (anche da solo, per un esito incerto)')
    ap.add_argument('--confirm-database', help='deve coincidere con --database per eseguire')
    ap.add_argument('--server', default='.')
    ap.add_argument('--database')
    ap.add_argument('--user')
    ap.add_argument('--password')
    ap.add_argument('--driver', default='ODBC Driver 17 for SQL Server')
    a = ap.parse_args()
    with open(a.scenario, 'rb') as f:
        source = f.read()
    scenario = json.loads(source.decode('utf-8'))
    with open(a.plan, encoding='utf-8') as f:
        plan = json.load(f)
    try:
        with open(os.path.join(a.engine_dir, 'engine-scenario.json'), encoding='utf-8') as f:
            engine_scenario = json.load(f)
        if a.engine_scenario != 'engine-scenario.json':
            with open(os.path.join(a.engine_dir, a.engine_scenario), encoding='utf-8') as f:
                derived = json.load(f)
            check_derived(engine_scenario, derived)
            engine_scenario = derived
        with open(os.path.join(a.engine_dir, 'conversion.json'), encoding='utf-8') as f:
            conversion = json.load(f)
    except FileNotFoundError as exc:
        sys.exit(f'ERRORE: {exc.filename} mancante: riconvertire con to_engine_v02.py aggiornato')
    except PublishError as exc:
        sys.exit(f'ERRORE: {exc}')
    try:
        check_binding(scenario, plan, engine_scenario, conversion, source)
        cs = build_changeset(scenario, plan)
    except PublishError as exc:
        sys.exit(f'ERRORE: {exc}')
    os.makedirs(a.out, exist_ok=True)
    script = render_sql(cs)
    with open(os.path.join(a.out, 'publish.sql'), 'w', encoding='utf-8', newline='\n') as f:
        f.write(script)
    report = render_report(cs)
    print(f"{len(cs['phases'])} fasi, {len(cs['lare'])} righe L_LARE, {len(cs['blocks'])} blocchi, "
          f"{len(cs['lots'])} lotti, {len(cs['orders'])} commesse -> {os.path.join(a.out, 'publish.sql')}")
    def save(text_):
        with open(os.path.join(a.out, 'publish-report.md'), 'w', encoding='utf-8', newline='\n') as f:
            f.write(text_)

    if a.execute or a.verify:
        if not a.database or a.confirm_database != a.database:
            sys.exit('ERRORE: --execute/--verify richiedono --database e --confirm-database con lo stesso nome')
        if cs['database'] != a.database:
            sys.exit(f"ERRORE: lo scenario viene da {cs['database']!r}, non da {a.database!r}")
        source_server = scenario.get('source', {}).get('server')
        if source_server and source_server.strip().casefold() != a.server.strip().casefold():
            sys.exit(f"ERRORE: server diverso dall'estrazione: {source_server!r} != {a.server!r}")
        cn = connect(a)
    if a.execute:
        try:
            summary = execute(cn, script, cs)
        except UncertainOutcome as exc:
            report += f'\n## Esecuzione\n\nEsito INCERTO (connessione interrotta): {exc}\n'
            save(report)
            sys.exit('ERRORE: esito incerto, il COMMIT potrebbe essere avvenuto: controllare con --verify '
                     'prima di ripetere la pubblicazione')
        except Exception as exc:  # errore SQL prima del COMMIT: XACT_ABORT ha annullato tutto
            report += f'\n## Esecuzione\n\nAnnullata, nessuna scrittura: {exc}\n'
            save(report)
            sys.exit(f'ERRORE: pubblicazione annullata: {exc}')
        report += f'\n## Esecuzione\n\nConfermata su `{a.database}`: {summary}\n'
        print('Pubblicato:', summary)
    if a.verify:
        problems, blocks = verify(cn, cs)
        report += f'\n## Verifica\n\nBlocchi L_LAPN riletti: {blocks}. Differenze: {problems or "nessuna"}\n'
        print('Verifica:', problems or 'nessuna differenza', f'({blocks} blocchi)')
        if problems:
            save(report)
            sys.exit('ERRORE: il database non corrisponde al piano pubblicato')
    save(report)


if __name__ == '__main__':
    main()
