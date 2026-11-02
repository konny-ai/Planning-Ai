"""Confronta un piano del motore v0.2 con il golden FSMo_SCH del banco (jobs.tsv, EXPECTED_START/END).

Uso: python compare_bench.py engine-scenario.json plan.json jobs.tsv [--out confronto.md] [--map anonymization-map.json]
Le date legacy sono ora locale Europe/Rome; le fasi a durata zero sono escluse (il motore non le pianifica).
"""
import argparse, csv, json, statistics, collections
from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo('Europe/Rome')


def parse_utc(v):
    return datetime.fromisoformat(v.replace('Z', '+00:00')).astimezone(TZ).replace(tzinfo=None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('scenario'); ap.add_argument('plan'); ap.add_argument('jobs')
    ap.add_argument('--out'); ap.add_argument('--map')
    ap.add_argument('--metrics-json', help='machine-readable cohort statistics')
    ap.add_argument('--min-compared', type=int, default=0,
                    help='fail when the comparison is missing expected active operations')
    a = ap.parse_args()
    sc = json.load(open(a.scenario, encoding='utf-8'))
    plan = json.load(open(a.plan, encoding='utf-8'))
    orders = (json.load(open(a.map, encoding='utf-8')).get('ORDER', {}) if a.map else {})
    conv = lambda c: orders.get(str(c), str(c))
    gold = {}
    for r in csv.DictReader(open(a.jobs, encoding='utf-8'), delimiter='\t'):
        oid = f"{conv(r['CONUM'])}/{r['LOCOD']}/{r['IDFAS']}"
        if oid in gold:
            raise ValueError(f'duplicate golden operation {oid}')
        gold[oid] = r
    due = {o['id']: o.get('due') for o in sc['operations']}
    eng = {}
    for x in plan['assignments']:
        # External promises have no resource segments, but ARE comparable to
        # legacy supplier-anchored dates. Completed actual history remains excluded.
        if x['segments']:
            begin, finish = parse_utc(x['segments'][0]['start']), parse_utc(x['segments'][-1]['end'])
        elif x.get('external_end'):
            begin = finish = parse_utc(x['external_end'])
        else:
            continue
        for oid in x['operation_ids']:
            if oid in eng:
                raise ValueError(f'duplicate engine operation assignment {oid}')
            eng[oid] = (begin, finish, x['effective_mode'], x['demands'])
    rows, dstart, dend = [], [], []
    for oid, (s, e, mode, dem) in sorted(eng.items()):
        g = gold.get(oid)
        if not g:
            continue
        gs, ge = datetime.fromisoformat(g['EXPECTED_START']), datetime.fromisoformat(g['EXPECTED_END'])
        dstart.append((s - gs).total_seconds() / 86400); dend.append((e - ge).total_seconds() / 86400)
        rows.append((oid, gs, ge, s, e, mode, '+'.join(d['resource_id'] for d in dem), g['MACS'] or g['OPRS'] or g['SUPS']))
    # per commessa: fine dell'ultima fase attiva, motore e legacy
    by = collections.defaultdict(lambda: [None, None, None])
    for oid, gs, ge, s, e, *_ in rows:
        c = oid.split('/')[0]; b = by[c]
        b[0] = max(b[0] or ge, ge); b[1] = max(b[1] or e, e)
        d = due.get(oid)
        if d:
            d = parse_utc(d); b[2] = min(b[2] or d, d)
    late = lambda i: sum(1 for b in by.values() if b[2] and b[i] > b[2])
    def dist(v):
        if not v:
            return 'nessuna fase confrontabile'
        av = [abs(x) for x in v]
        return (f'mediana {statistics.median(v):+.2f} gg, media |Δ| {statistics.mean(av):.2f} gg, '
                f'entro 1 gg {sum(x <= 1 for x in av)}/{len(v)}, entro 7 gg {sum(x <= 7 for x in av)}/{len(v)}, max |Δ| {max(av):.1f} gg')
    modes = collections.Counter(r[5] for r in rows)
    start0 = parse_utc(sc['start'])

    def cohort(items):
        delta = [(r[4] - r[2]).total_seconds() / 86400 for r in items]
        adelta = [abs(x) for x in delta]
        return {'count': len(items),
                'median_end_delta_days': statistics.median(delta) if delta else None,
                'mean_abs_end_delta_days': statistics.mean(adelta) if adelta else None,
                'within_1_day': sum(x <= 1 for x in adelta),
                'within_7_days': sum(x <= 7 for x in adelta),
                'max_abs_end_delta_days': max(adelta) if adelta else None}

    metrics = {
        'schema_version': 1,
        'scenario_id': sc['id'],
        'compared_operations': len(rows),
        'engine_scheduled_operations': len(eng),
        'golden_operations': len(gold),
        'unmatched_engine_operations': len(set(eng) - set(gold)),
        # Golden includes historical/zero-work phases that were not scheduled
        # by this v0.2 profile. This is an inventory, NOT a failure count.
        'golden_not_scheduled_operations': len(set(gold) - set(eng)),
        'cohorts': {
            'all': cohort(rows),
            'legacy_before_horizon': cohort([r for r in rows if r[1] < start0]),
            'legacy_in_horizon': cohort([r for r in rows if r[1] >= start0]),
        },
        'effective_modes': dict(sorted(modes.items())),
    }
    if a.metrics_json:
        with open(a.metrics_json, 'w', encoding='utf-8', newline='\n') as output:
            json.dump(metrics, output, indent=2, ensure_ascii=False, allow_nan=False)
            output.write('\n')
    if len(rows) < a.min_compared:
        raise SystemExit(f'benchmark: attese almeno {a.min_compared} fasi confrontate, presenti {len(rows)}')
    split = []
    for label, sel in (('legacy nel passato (prima dell\'inizio scenario)', lambda r: r[1] < start0),
                       ('legacy dentro l\'orizzonte', lambda r: r[1] >= start0)):
        sub = [(r[4] - r[2]).total_seconds() / 86400 for r in rows if sel(r)]
        if sub:
            split.append(f'  - {label}: {len(sub)} fasi, Δ fine {dist(sub)}')
    out = [f'# Confronto motore v0.2 vs FSMo_SCH (banco)\n',
           f'Scenario `{sc["id"]}`, piano {plan["scenario_digest"][:12]}; fasi confrontate {len(rows)} (golden {len(gold)}, motore {len(eng)}).\n',
           f'- Δ inizio (motore − legacy): {dist(dstart)}',
           f'- Δ fine (motore − legacy): {dist(dend)}',
           *split,
           f'- modalità effettive del motore: {dict(modes)}',
           f'- commesse con fasi attive: {len(by)}; in ritardo legacy {late(0)}, motore {late(1)}',
           f'- commesse che il motore chiude prima del legacy: {sum(1 for b in by.values() if b[1] < b[0])}, dopo: {sum(1 for b in by.values() if b[1] > b[0])}\n',
           '## Fasi con scarto di fine maggiore di 7 giorni (prime 40)\n',
           '| Fase | Legacy inizio | Legacy fine | Motore inizio | Motore fine | Modo | Risorsa motore | Candidate legacy |', '|---|---|---|---|---|---|---|---|']
    big = sorted((r for r in rows if abs((r[4] - r[2]).total_seconds()) > 7 * 86400), key=lambda r: -abs((r[4] - r[2]).total_seconds()))
    out += [f'| {r[0]} | {r[1]:%Y-%m-%d %H:%M} | {r[2]:%Y-%m-%d %H:%M} | {r[3]:%Y-%m-%d %H:%M} | {r[4]:%Y-%m-%d %H:%M} | {r[5]} | {r[6]} | {r[7][:40]} |' for r in big[:40]]
    out.append(f'\nFasi con scarto > 7 gg: {len(big)}.')
    text = '\n'.join(out) + '\n'
    if a.out:
        open(a.out, 'w', encoding='utf-8', newline='\n').write(text)
    print(text[:3000].encode('ascii', errors='backslashreplace').decode('ascii'))


if __name__ == '__main__':
    main()
