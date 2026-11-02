"""Portable, offline HTML dashboards, with only one static inline filter script."""
import csv
from html import escape
from io import StringIO
from .json_io import dumps


def csv_report(plan) -> str:
    out = StringIO(newline='')
    writer = csv.writer(out, lineterminator='\n')
    writer.writerow(('task_id', 'operation_ids', 'resource_id', 'units', 'start', 'end', 'seconds', 'fixed'))
    def cell(value):
        # Prevent spreadsheet formula execution when opening an exported CSV.
        text = str(value)
        return "'" + text if (text.lstrip(' \t\r\n')[:1] in ('=', '+', '-', '@') or text[:1] in ('\t', '\r')) else text
    for a in plan.assignments:
        for seg in a.segments:
            for d in a.demands:
                writer.writerow(tuple(cell(x) for x in (a.task_id, '|'.join(a.operation_ids), d.resource_id,
                                                       d.units, seg.start.isoformat(), seg.end.isoformat(), seg.seconds, a.fixed)))
    return out.getvalue()


FILTER_SCRIPT = """
<script type="text/javascript">
(function () {
  'use strict';
  const input = document.getElementById('task-filter');
  const rows = Array.from(document.querySelectorAll('#gantt-body tr'));
  const count = document.getElementById('visible-count');
  if (!input || !count) return;
  function applyFilter() {
    const query = input.value.trim().toLocaleLowerCase();
    let visible = 0;
    for (const row of rows) {
      const show = row.dataset.filter.toLocaleLowerCase().includes(query);
      row.hidden = !show;
      if (show) visible++;
    }
    count.textContent = String(visible);
  }
  input.addEventListener('input', applyFilter);
  applyFilter();
})();
</script>
"""


def html_report(scenario, plan, *, capacity_scale=1) -> str:
    """Self-contained offline dashboard; all imported labels are HTML escaped."""
    if type(capacity_scale) is not int or capacity_scale < 1:
        raise ValueError('positive capacity scale required')
    def display_units(value):
        return str(value) if capacity_scale == 1 else f'{value / capacity_scale:g}'
    lo, hi = scenario.start.timestamp(), scenario.end.timestamp()
    span = hi - lo
    order_by_op = {o.id: o.order_id for o in scenario.operations}
    rows = []
    milestones = []
    for a in sorted(plan.assignments, key=lambda a: (a.start, a.task_id)):
        orders = ', '.join(sorted({order_by_op.get(oid, '') for oid in a.operation_ids}))
        resources = ', '.join(d.resource_id for d in a.demands)
        if not a.segments:
            note = 'Promessa fornitore (non consuntivata)' if a.external_end else 'Storico completato'
            milestones.append(f'<tr><th scope="row">{escape(a.task_id)}</th><td>{escape(note)}</td>'
                              f'<td>{escape(a.end.isoformat())}</td></tr>')
            continue
        bars = []
        for seg in a.segments:
            left = (seg.start.timestamp() - lo) / span * 100
            width = seg.seconds / span * 100
            tooltip = escape(f'{seg.start.isoformat()} → {seg.end.isoformat()} | {seg.seconds}s')
            bars.append(f'<span class="bar" style="left:{left:.6f}%;width:{width:.6f}%" title="{tooltip}"></span>')
        searchable = escape(' '.join((a.task_id, *a.operation_ids, orders, resources)), quote=True)
        rows.append(f'<tr data-filter="{searchable}"><th scope="row">{escape(a.task_id)}'
                    f'<small>Commessa: {escape(orders)} · Risorse: {escape(resources)}</small></th>'
                    f'<td class="timeline">{"".join(bars)}</td>'
                    f'<td>{escape(a.start.isoformat())}</td><td>{escape(a.end.isoformat())}</td></tr>')
    failures = ''.join(f'<li><strong>{escape(u.task_id)}</strong> — {escape(u.code)}: {escape(u.detail)}</li>'
                       for u in plan.unscheduled)
    deliveries = ''.join(
        f'<tr><th scope="row">{escape(o.order_id)}</th>'
        f'<td>{escape(o.end.isoformat() if o.end else "Non pianificata")}</td>'
        f'<td>{o.tardiness_seconds if o.tardiness_seconds is not None else "—"}</td></tr>'
        for o in plan.orders)
    loads = ''.join(
        f'<tr><th scope="row">{escape(x.resource_id)}</th>'
        f'<td>{display_units(x.booked_unit_seconds)}</td>'
        f'<td>{display_units(x.available_unit_seconds) if x.available_unit_seconds is not None else "Illimitata"}</td>'
        f'<td>{f"{x.utilization:.1%}" if x.utilization is not None else "—"}</td></tr>'
        for x in plan.resources)
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Planning Factory — {escape(scenario.id)}</title>
<style>
:root{{--ink:#172b3a;--accent:#087f8c;--border:#dae2e8}}
body{{font:15px system-ui,sans-serif;max-width:1500px;margin:30px auto;padding:0 20px;color:var(--ink);background:#f5f7fa}}
h1{{color:#124c5a}}p,small{{line-height:1.5}}.card{{display:inline-block;background:white;border:1px solid var(--border);padding:12px 16px;margin:4px;border-radius:8px}}
.table-wrap{{overflow-x:auto}}table{{width:100%;border-collapse:collapse;background:white;margin:14px 0}}td,th{{padding:11px;text-align:left;border-bottom:1px solid var(--border)}}
th{{min-width:170px}}small{{display:block;font-weight:400;color:#526779}}
.timeline{{position:relative;height:30px;min-width:250px;background:repeating-linear-gradient(90deg,#fff 0%,#fff 9.9%,#e6edf2 10%)}}
.bar{{position:absolute;top:10px;height:20px;min-width:2px;border-radius:3px;background:var(--accent)}}
input{{font:inherit;padding:10px;border-radius:6px;border:1px solid #8d9da8;width:min(600px,94%)}}
code{{overflow-wrap:anywhere}}li{{margin:10px 0}}tr[hidden]{{display:none}}caption{{text-align:left;font-weight:600;padding:6px}}
</style></head><body>
<h1>Planning Factory · Piano offline</h1>
<p>Scenario: <strong>{escape(scenario.id)}</strong> · Motore {escape(plan.engine_version)} · Intervallo UTC:
{escape(scenario.start.isoformat())} — {escape(scenario.end.isoformat())}</p>
<section aria-label="Indicatori">
<span class="card"><strong>{len(plan.assignments)}</strong> unità assegnate</span>
<span class="card"><strong>{len(plan.unscheduled)}</strong> non collocate</span>
<span class="card"><strong>{sum(o.tardiness_seconds is not None and o.tardiness_seconds > 0 for o in plan.orders)}</strong> commesse in ritardo</span>
</section>
<h2>Gantt delle lavorazioni</h2>
<label for="task-filter">Filtra per fase, nesting, commessa o risorsa</label><br>
<input id="task-filter" type="search" autocomplete="off" placeholder="Cerca per codice…" aria-controls="gantt-body">
<p>Righe visibili: <output id="visible-count">{len(rows)}</output> su {len(rows)}</p>
<div class="table-wrap"><table aria-label="Gantt delle lavorazioni"><thead><tr>
<th>Unità e risorse</th><th>Intervalli (dettagli al passaggio)</th><th>Inizio UTC</th><th>Fine UTC</th>
</tr></thead><tbody id="gantt-body">{''.join(rows)}</tbody></table></div>
<h2>Carico delle risorse</h2>
<div class="table-wrap"><table><thead><tr><th>Risorsa</th><th>Secondi unità prenotati</th><th>Capacità nel periodo</th><th>Utilizzo</th></tr></thead><tbody>{loads}</tbody></table></div>
<h2>Consegne</h2><div class="table-wrap"><table><thead><tr><th>Commessa</th><th>Fine prevista (UTC)</th><th>Ritardo (secondi)</th></tr></thead><tbody>{deliveries}</tbody></table></div>
<h2>Milestone senza prenotazioni</h2>
<div class="table-wrap"><table><thead><tr><th>Unità</th><th>Origine</th><th>Data (UTC)</th></tr></thead><tbody>{''.join(milestones) or '<tr><td colspan="3">Nessuna</td></tr>'}</tbody></table></div>
<h2>Operazioni non collocate</h2><ul>{failures or '<li>Nessuna</li>'}</ul>
<p>Impronta dello scenario: <code>{escape(plan.scenario_digest)}</code></p>
{FILTER_SCRIPT}</body></html>'''


def html_comparison(before, after) -> str:
    """Portable plan-to-plan difference report with escaped dynamic values."""
    from planning_factory.engine.simulation import compare
    if before.scenario_id != after.scenario_id:
        raise ValueError('cannot compare plans from different scenario IDs')
    diff = compare(before, after)
    def instant(value):
        return escape(value.isoformat()) if value else '—'
    def resources(value):
        return escape(', '.join(f'{d.resource_id} ({d.units})' for d in value)) or '—'
    rows = ''.join(
        f'<tr><th scope="row">{escape(row["operation_id"])}</th>'
        f'<td>{instant(row["before_start"])}</td><td>{instant(row["after_start"])}</td>'
        f'<td>{row["shift_seconds"] if row["shift_seconds"] is not None else "—"}</td>'
        f'<td>{resources(row["before_resources"])}</td><td>{resources(row["after_resources"])}</td></tr>'
        for row in diff['changed_operations'])
    return f'''<!doctype html><html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Planning Factory · confronto piani</title>
<style>body{{font:15px system-ui,sans-serif;max-width:1200px;margin:36px auto;padding:0 20px;background:#f5f7fa;color:#172b3a}}
table{{width:100%;border-collapse:collapse;background:white}}th,td{{padding:12px;text-align:left;border-bottom:1px solid #dae2e8}}
.wrap{{overflow-x:auto}}code{{overflow-wrap:anywhere}}</style></head><body>
<h1>Confronto piani · {escape(before.scenario_id)}</h1>
<p>Le variazioni si riferiscono alle operazioni originali. Gli spostamenti sono in secondi UTC.
I due digest possono differire in un confronto fra simulazioni dello stesso scenario.</p>
<p>Prima: <code>{escape(before.scenario_digest)}</code><br>Dopo: <code>{escape(after.scenario_digest)}</code></p>
<p>{len(diff['changed_operations'])} operazioni variate · Non collocate:
{diff['before_unscheduled']} → {diff['after_unscheduled']} · Ritardi totali:
{diff['before_tardiness_seconds']} → {diff['after_tardiness_seconds']} secondi.</p>
<div class="wrap"><table><thead><tr><th>Operazione</th><th>Prima (UTC)</th><th>Dopo (UTC)</th>
<th>Spostamento (s)</th><th>Risorse prima</th><th>Risorse dopo</th></tr></thead>
<tbody>{rows or '<tr><td colspan="6">Nessuna variazione</td></tr>'}</tbody></table></div>
</body></html>'''
