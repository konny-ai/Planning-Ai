"""Pianificazione Factory con un solo comando: estrazione, conversione, piano, verifica e pubblicazione.

Esegue in sequenza gli strumenti di questa cartella e il motore, nella cartella --out:

  estrazione/     scenario.json + validation.md          (export_factory_scenario.py, sola lettura)
  motore/         engine-scenario.json, plan.json, report.html, plan.csv, conversion-report.md
  pubblicazione/  publish.sql + publish-report.md        (publish_factory_plan.py)
  riepilogo.md    esito di ogni passo

Senza --execute il database non viene scritto: la pubblicazione produce solo lo script da rivedere.
Con --execute (solo su cloni, finche' il collaudo #3 non autorizza il database del cliente)
pubblica in un'unica transazione e rilegge il risultato; serve --confirm-database uguale a --database.

Esempio (prova su un clone, con il comportamento del legacy):
  python run_factory_planning.py --database Factory_BANCOA --start 2026-10-05 --out run \\
      --customer-view --subcontract-policy anchored --delivery-margin-days 1 --progress-policy time
"""
import argparse
import json
import os
import subprocess
import sys
import time

TOOLS = os.path.dirname(os.path.abspath(__file__))
UNIVERSE = "fasi nell'universo"


def describe(name, stdout):
    """Riga leggibile per il riepilogo, dalle uscite JSON degli strumenti."""
    try:
        if name == 'conversione':
            counts = json.loads(stdout)
            return f"{counts.get('fasi pianificate dal motore', 0)} fasi da pianificare su {counts.get(UNIVERSE, 0)}"
        if name == 'pianificazione':
            counts = json.loads(stdout.strip().splitlines()[-1])
            return f"{counts['assigned']} pianificate, {counts['unscheduled']} non pianificate"
        if name == 'verifica':
            report = json.loads(stdout)
            return 'piano valido' if report.get('valid') else f"{len(report.get('errors', []))} errori"
    except (ValueError, KeyError, IndexError):
        return None
    return None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    db = ap.add_argument_group('database')
    db.add_argument('--server', default='.')
    db.add_argument('--database', required=True)
    db.add_argument('--user')
    db.add_argument('--password')
    db.add_argument('--driver', default='ODBC Driver 17 for SQL Server')
    ex = ap.add_argument_group('estrazione')
    ex.add_argument('--start', required=True, help='inizio pianificazione AAAA-MM-GG')
    ex.add_argument('--horizon-days', type=int, default=365)
    ex.add_argument('--customer-view', action='store_true')
    ex.add_argument('--scenario', help='usa uno scenario.json gia\' estratto invece di leggere il database')
    cv = ap.add_argument_group('conversione')
    cv.add_argument('--subcontract-policy', choices=['resources', 'anchored'], default='resources')
    cv.add_argument('--delivery-margin-days', type=int, default=0)
    cv.add_argument('--progress-policy', choices=['none', 'time'], default='none')
    cv.add_argument('--auto-backward-days', type=int, default=30)
    cv.add_argument('--missing-resource-policy', choices=['fail', 'available'], default='fail')
    pb = ap.add_argument_group('pubblicazione')
    pb.add_argument('--execute', action='store_true', help='scrive il piano nel database (solo cloni)')
    pb.add_argument('--confirm-database')
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    paths = {k: os.path.join(a.out, k) for k in ('estrazione', 'motore', 'pubblicazione')}
    scenario = a.scenario or os.path.join(paths['estrazione'], 'scenario.json')
    engine = os.path.join(paths['motore'], 'engine-scenario.json')
    plan = os.path.join(paths['motore'], 'plan.json')
    connection = ['--server', a.server, '--database', a.database, '--driver', a.driver]
    if a.user:
        connection += ['--user', a.user, '--password', a.password or '']
    tool = lambda name: [sys.executable, os.path.join(TOOLS, name)]
    steps = []
    if not a.scenario:
        steps.append(('estrazione', tool('export_factory_scenario.py') + connection + [
            '--start', a.start, '--horizon-days', str(a.horizon_days), '--out', paths['estrazione']]
            + (['--customer-view'] if a.customer_view else []), (0,)))
    steps += [
        ('conversione', tool('to_engine_v02.py') + [
            scenario, '--out', paths['motore'], '--horizon-days', str(a.horizon_days),
            '--subcontract-policy', a.subcontract_policy, '--delivery-margin-days', str(a.delivery_margin_days),
            '--progress-policy', a.progress_policy, '--auto-backward-days', str(a.auto_backward_days),
            '--missing-resource-policy', a.missing_resource_policy], (0,)),
        # schedule esce con 2 se restano operazioni non pianificate: il piano e' comunque valido.
        ('pianificazione', [sys.executable, '-m', 'planning_factory', 'schedule', engine, '-o', plan,
                            '--html', os.path.join(paths['motore'], 'report.html'),
                            '--csv', os.path.join(paths['motore'], 'plan.csv')], (0, 2)),
        ('verifica', [sys.executable, '-m', 'planning_factory', 'verify', engine, plan], (0,)),
        ('pubblicazione', tool('publish_factory_plan.py') + [
            scenario, plan, '--engine-dir', paths['motore'], '--out', paths['pubblicazione']]
            + (connection + ['--confirm-database', a.confirm_database or '', '--execute', '--verify']
               if a.execute else []), (0,)),
    ]
    summary = [f'# Pianificazione {a.database} dal {a.start}', '',
               f"Conto lavoro {a.subcontract_policy}, margine consegna {a.delivery_margin_days} gg, "
               f"avanzamento {a.progress_policy}, pubblicazione {'ESEGUITA' if a.execute else 'solo anteprima'}.", '',
               '| Passo | Esito | Secondi | Dettaglio |', '|---|---|---|---|']
    failed = None
    for name, command, accepted in steps:
        started = time.monotonic()
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace')
        elapsed = time.monotonic() - started
        lines = [x for x in (result.stdout + result.stderr).strip().splitlines() if x.strip()]
        detail = lines[-1] if lines else ''
        if result.returncode in accepted:
            detail = describe(name, result.stdout) or detail
        ok = result.returncode in accepted
        summary.append(f"| {name} | {'OK' if ok else 'ERRORE'} | {elapsed:.0f} | {detail.replace('|', '/')[:200]} |")
        print(f"[{'OK' if ok else 'ERRORE'}] {name} ({elapsed:.0f} s) {detail}", flush=True)
        if not ok:
            failed = name
            with open(os.path.join(a.out, f'{name}.log'), 'w', encoding='utf-8', newline='\n') as f:
                f.write(result.stdout + '\n' + result.stderr)
            summary += ['', f'Interrotto al passo **{name}**: dettagli in `{name}.log`.']
            break
    if not failed:
        summary += ['', f"Report del piano: `motore/report.html`. Script di pubblicazione: `pubblicazione/publish.sql`."]
    with open(os.path.join(a.out, 'riepilogo.md'), 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(summary) + '\n')
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
