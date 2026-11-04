"""Offline HTML report of a setup/run stage plan: no script, no network, every
value escaped. Shows setup and run as distinct bars per resource, the gap between
a setup and its run (ADR 005 option A), phases that are not planned (ADR 006
option B), KPIs and independent audit findings.
"""
from datetime import UTC
from html import escape

_CSS = """
:root{--setup:#b7791f;--run:#2b6cb0;--ink:#1a202c;--muted:#4a5568;--line:#cbd5e0;--bg:#fff;--bad:#c53030}
@media (prefers-color-scheme:dark){:root{--setup:#ecc94b;--run:#63b3ed;--ink:#edf2f7;--muted:#a0aec0;--line:#4a5568;--bg:#1a202c;--bad:#fc8181}}
body{font:14px/1.45 system-ui,sans-serif;color:var(--ink);background:var(--bg);margin:0;padding:16px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:24px 0 8px}p.meta{color:var(--muted);margin:0 0 12px}
table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid var(--line);padding:4px 8px;text-align:left}
.lane{display:grid;grid-template-columns:120px 1fr;align-items:center;gap:8px;margin:2px 0}
.track{position:relative;height:22px;border:1px solid var(--line);border-radius:3px;overflow:hidden}
.bar{position:absolute;top:2px;bottom:2px;border-radius:2px;min-width:2px}
.bar.setup{background:var(--setup)}.bar.run{background:var(--run)}
.legend span{display:inline-block;width:12px;height:12px;border-radius:2px;margin:0 4px 0 12px;vertical-align:-1px}
.bad{color:var(--bad);font-weight:600}
@media (max-width:600px){.lane{grid-template-columns:80px 1fr}}
"""


def _fmt(value):
    return escape(value.astimezone(UTC).strftime('%Y-%m-%d %H:%M'))


def stage_html_report(bookings, *, title='Piano a stadi', sequence=(), kpis=None,
                      unplanned=(), setup_gaps=(), audit_violations=()):
    """Return a standalone HTML page. ``bookings`` are StageBooking objects."""
    segments = [(b, i) for b in bookings for i in b.segments]
    lo = min((i.start for _, i in segments), default=None)
    hi = max((i.end for _, i in segments), default=None)
    span = (hi - lo).total_seconds() if lo and hi and hi > lo else 1
    lanes = {}
    for b, interval in segments:
        for a in b.allocations:
            lanes.setdefault(a.resource_id, []).append((b, interval, a.occupancy_bps))
    out = ['<!doctype html><html lang="it"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           f'<title>{escape(title)}</title><style>{_CSS}</style></head><body>',
           f'<h1>{escape(title)}</h1>',
           f'<p class="meta">Sequenza: {escape(", ".join(sequence)) or "—"}</p>',
           '<p class="legend"><span style="background:var(--setup)"></span>attrezzaggio'
           '<span style="background:var(--run)"></span>lavorazione</p>']
    if audit_violations:
        out.append('<h2 class="bad">Violazioni dell\'audit indipendente</h2><ul>' +
                   ''.join(f'<li class="bad">{escape(v)}</li>' for v in audit_violations) + '</ul>')
    else:
        out.append('<p>Audit indipendente: nessuna violazione.</p>')
    if kpis:
        out.append('<h2>Indicatori</h2><table><tbody>' + ''.join(
            f'<tr><th>{escape(str(k))}</th><td>{escape(str(v))}</td></tr>' for k, v in sorted(kpis.items())) +
            '</tbody></table>')
    out.append('<h2>Risorse nel tempo</h2>')
    if lo:
        out.append(f'<p class="meta">Da {_fmt(lo)} a {_fmt(hi)} (UTC)</p>')
    for resource_id in sorted(lanes):
        bars = []
        for b, interval, bps in sorted(lanes[resource_id], key=lambda x: x[1].start):
            left = (interval.start - lo).total_seconds() / span * 100
            width = max((interval.end - interval.start).total_seconds() / span * 100, 0.2)
            tip = escape(f'{b.phase_id} {b.stage} {interval.start.astimezone(UTC):%Y-%m-%d %H:%M}–'
                         f'{interval.end.astimezone(UTC):%Y-%m-%d %H:%M} {bps / 100:g}%', quote=True)
            bars.append(f'<div class="bar {escape(b.stage)}" style="left:{left:.3f}%;width:{width:.3f}%" '
                        f'title="{tip}"></div>')
        out.append(f'<div class="lane"><div>{escape(resource_id)}</div><div class="track">{"".join(bars)}</div></div>')
    out.append('<h2>Prenotazioni</h2><table><thead><tr><th>Fase</th><th>Stadio</th><th>Inizio</th><th>Fine</th>'
               '<th>Risorse (quota)</th></tr></thead><tbody>')
    for b in sorted(bookings, key=lambda x: (x.segments[0].start if x.segments else lo, x.phase_id, x.stage)):
        resources = ', '.join(f'{a.resource_id} ({a.occupancy_bps / 100:g}%)' for a in b.allocations)
        for interval in b.segments:
            out.append(f'<tr><td>{escape(b.phase_id)}</td><td>{escape(b.stage)}</td><td>{_fmt(interval.start)}</td>'
                       f'<td>{_fmt(interval.end)}</td><td>{escape(resources)}</td></tr>')
    out.append('</tbody></table>')
    if setup_gaps:
        out.append('<h2>Distanza attrezzaggio → lavorazione</h2><table><thead><tr><th>Fase</th><th>Gap (min)</th>'
                   '</tr></thead><tbody>' + ''.join(
                       f'<tr><td>{escape(p)}</td><td>{g / 60:g}</td></tr>' for p, g in setup_gaps) + '</tbody></table>')
    if unplanned:
        out.append('<h2 class="bad">Fasi non pianificate</h2><table><thead><tr><th>Operazione</th><th>Codice</th>'
                   '<th>Motivo</th></tr></thead><tbody>' + ''.join(
                       f'<tr><td>{escape(u["operation_id"])}</td><td>{escape(u["code"])}</td>'
                       f'<td>{escape(u["detail"])}</td></tr>' for u in unplanned) + '</tbody></table>')
    out.append('</body></html>')
    return ''.join(out)
