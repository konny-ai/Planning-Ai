/* Planning Factory browser UI — zero frameworks, zero third-party requests.
 * The API returns independently audited Scenario + Plan from a trusted file.
 * All displayed identifiers are escaped. No write action reaches Factory.
 */
(function () {
  'use strict';

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => Array.from(document.querySelectorAll(selector));
  const esc = (value) => String(value === undefined || value === null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const day = 86400000;
  const number = (value) => Number.isFinite(Number(value)) ? Number(value) : 0;
  const min = (a, b) => a < b ? a : b;
  const max = (a, b) => a > b ? a : b;
  const dt = (value) => value ? new Date(value) : null;
  const ms = (value) => { const d = dt(value); return d && Number.isFinite(d.getTime()) ? d.getTime() : NaN; };
  // Dates are shown in the plant's time zone (the calendars' IANA zone), days start at local midnight.
  let displayTz = 'UTC';
  const formatters = new Map();
  const formatter = (options) => {
    const key = displayTz + JSON.stringify(options);
    if (!formatters.has(key)) formatters.set(key, new Intl.DateTimeFormat('it-IT', {timeZone:displayTz, ...options}));
    return formatters.get(key);
  };
  const zoneParts = (t) => {
    const parts = {};
    for (const p of formatter({year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'})
      .formatToParts(new Date(t))) parts[p.type] = p.value;
    return parts;
  };
  const zoneOffset = (t) => {
    const p = zoneParts(t);
    return Date.UTC(+p.year, +p.month - 1, +p.day, +p.hour, +p.minute, +p.second) - Math.floor(t / 1000) * 1000;
  };
  const atMidnight = (value) => {
    const p = zoneParts((value instanceof Date ? value : new Date(value)).getTime());
    const wall = Date.UTC(+p.year, +p.month - 1, +p.day);
    return new Date(wall - zoneOffset(wall - zoneOffset(wall)));
  };
  const iso = (date) => { const p = zoneParts((date instanceof Date ? date : new Date(date)).getTime()); return p.year + '-' + p.month + '-' + p.day; };
  const weekday = (date) => new Date(atMidnight(date).getTime() + 12 * 3600000).getUTCDay();
  const fmt = (v, type) => {
    const d = dt(v);
    if (!d || !Number.isFinite(d.getTime())) return '—';
    // Le date si mostrano sempre come gg/mm/aaaa (mai mese a lettere né anno a due cifre).
    if (type === 'day' || type === 'date') return formatter({day:'2-digit', month:'2-digit', year:'numeric'}).format(d);
    if (type === 'dm') return formatter({day:'2-digit', month:'2-digit'}).format(d);
    if (type === 'monthDay') return formatter({day:'2-digit', month:'short'}).format(d).replace(/\.$/,'');
    if (type === 'monthDayYear') return formatter({day:'2-digit', month:'short', year:'numeric'}).format(d).replace(/\.$/,'');
    if (type === 'monthYear') return formatter({month:'long', year:'numeric'}).format(d);
    if (type === 'weekday') return formatter({weekday:'short'}).format(d);
    if (type === 'time') return formatter({hour:'2-digit', minute:'2-digit', hour12:false}).format(d);
    return formatter({day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false}).format(d).replace(', ', ' ');
  };
  function calendarBandGroups(dates, kind) {
    const groups=[];
    for (let i=0;i<dates.length;i++) {
      const d=dates[i];
      const key=kind==='month' ? iso(d).slice(0,7) : iso(shiftDay(d,-((weekday(d)+6)%7)));
      let group=groups[groups.length-1];
      if (!group || group.key!==key) {
        group={key,start:i,end:i+1,first:d,last:d};
        groups.push(group);
      } else { group.end=i+1; group.last=d; }
    }
    return groups;
  }
  function calendarBandLabel(group, kind) {
    return kind==='month' ? fmt(group.first,'monthYear') :
      'SETTIMANA · '+fmt(group.first,'monthDay')+' – '+fmt(group.last,'monthDay');
  }
  // Data ISO (aaaa-mm-gg, anche con ora) → gg/mm/aaaa [hh:mm], solo per testo mostrato.
  const isoText = (value) => {
    const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2}))?/.exec(String(value || ''));
    return m ? m[3] + '/' + m[2] + '/' + m[1] + (m[4] ? ' ' + m[4] + ':' + m[5] : '') : String(value || '');
  };
  const hrs = (seconds) => {
    const h = number(seconds) / 3600;
    return h >= 10 ? h.toLocaleString('it-IT', {maximumFractionDigits:0}) + ' h' :
      h.toLocaleString('it-IT', {minimumFractionDigits:0, maximumFractionDigits:1}) + ' h';
  };
  const pct = (value) => value === null || value === undefined ? '—' :
    (number(value)*100).toLocaleString('it-IT', {maximumFractionDigits:1}) + '%';
  const short = (value, limit=22) => {
    const s = String(value === undefined ? '' : value);
    return s.length > limit ? s.slice(0, limit - 1) + '…' : s;
  };
  let opsCache = null;
  const operationsById = () => {
    if (!opsCache || opsCache.scenario !== data.scenario)
      opsCache = {scenario:data.scenario, map:new Map(data.scenario.operations.map(o => [o.id, o]))};
    return opsCache.map;
  };
  const resourcesById = () => new Map(data.scenario.resources.map(r => [r.id, r]));
  const assignmentStart = (a) => a.segments && a.segments.length ? a.segments[0].start : a.completed_at || a.external_end;
  const assignmentEnd = (a) => a.segments && a.segments.length ?
    a.segments[a.segments.length - 1].end : a.completed_at || a.external_end;
  const taskLabel = (a) => a.task_id.startsWith('job:') ? a.task_id.slice(4) : a.task_id.startsWith('nest:') ? 'Nesting ' + a.task_id.slice(5) : a.task_id;
  const taskOrders = (a) => {
    const ops = operationsById();
    return [...new Set(a.operation_ids.map(id => ops.get(id) && ops.get(id).order_id).filter(Boolean))];
  };
  /* Presentation texts (descrizioni.json): names read from the source system for display only.
   * Without them every label falls back to the canonical identifier: nothing is made up. */
  let labels = null;
  const label = (kind, id) => labels && labels[kind] && labels[kind][id] || null;
  const resourceCode = (id) => String(id).replace(/^(MAC|OP|SUP):/, '');
  const resourceName = (id) => { const r = label('resources', id); return r && r.name || null; };
  const resourceTitle = (id) => resourceCode(id) + (resourceName(id) ? ' · ' + resourceName(id) : '');
  const orderCode = (id) => { const o = label('orders', id); return o && o.code || String(id); };
  /* Customer of an order: the canonical customer_id when the source sends it (corrections included),
   * otherwise the code in the presentation texts. The company name is looked up by that code. */
  let customerCache = null;
  const customerIndex = () => {
    const scope = data && data.meta && data.meta.rule_scope;  // the data screens open before the plan exists
    if (customerCache && customerCache.labels === labels && customerCache.scope === scope) return customerCache;
    const described = labels && labels.orders || {}, names = new Map(), codes = new Map();
    for (const o of Object.values(described)) if (o && o.customer_code && o.customer) names.set(o.customer_code, o.customer);
    // A canonical source is authoritative even where it gives no customer (e.g. after a correction removed it).
    const canonical = scope && Array.isArray(scope.orders);
    if (canonical) for (const o of scope.orders) { if (o.customer_id) codes.set(o.id, o.customer_id); }
    else for (const [id, o] of Object.entries(described)) if (o && o.customer_code) codes.set(id, o.customer_code);
    return customerCache = {labels, scope, names, codes};
  };
  const orderCustomerCode = (id) => customerIndex().codes.get(id) || null;
  const customerName = (code) => code ? customerIndex().names.get(code) || null : null;
  const orderCustomer = (id) => { const code = orderCustomerCode(id); return code ? customerName(code) || 'Cliente ' + code : null; };
  const taskCustomers = (a) => [...new Set(taskOrders(a).map(orderCustomer).filter(Boolean))];
  const customersText = (a, limit) => {
    const list = taskCustomers(a);
    return list.slice(0, limit).join(', ') + (list.length > limit ? ' +' + (list.length - limit) : '');
  };
  const nestId = (a) => a.task_id.startsWith('nest:') ? a.task_id.slice(5) : null;
  const nestInfo = (a) => nestId(a) ? label('nests', nestId(a)) : null;
  const opInfo = (id) => label('operations', id);
  const thickness = (row) => row && row.thickness_mm ? 'sp.' + row.thickness_mm : '';
  const materialText = (row) => row ? [row.sheet || row.material, thickness(row)].filter(Boolean).join(' ') : '';
  const taskTitle = (a) => {
    const n = nestInfo(a);
    if (nestId(a)) return n && n.code || nestId(a);
    const orders = taskOrders(a);
    return orders.length ? orders.map(orderCode).join(', ') : taskLabel(a);
  };
  const taskSubtitle = (a) => {
    const orders = taskOrders(a);
    if (nestId(a)) return [materialText(nestInfo(a)), orders.length + ' comm.'].filter(Boolean).join(' · ');
    const op = opInfo(a.operation_ids[0]);
    return op ? [op.phase, op.quantity ? op.quantity + ' ' + (op.uom || 'pz').toLowerCase() : '', thickness(op)].filter(Boolean).join(' · ') : taskLabel(a);
  };
  const phaseFamily = (a) => {
    if (nestId(a)) return 'cut';
    const op = opInfo(a.operation_ids[0]);
    const text = op ? (op.phase || '') + ' ' + (op.phase_code || '') : '';
    if (op && op.external || !a.segments.length) return 'ext';
    if (/TAGLI|OSSI|PLASMA|LASER|CUT|PANTO/i.test(text)) return 'cut';
    if (/PIEG|CALAND|RADDRIZ/i.test(text)) return 'bend';
    return 'finish';
  };
  let evidenceCache = null;
  const evidence = () => {
    if (evidenceCache && evidenceCache.meta === data.meta) return evidenceCache;
    const canonical = data.meta && data.meta.canonical || {};
    const short = new Map(), pegged = new Set(), progress = new Map();
    for (const issue of canonical.issues || [])
      if (issue.code === 'material_shortage') for (const unit of issue.units || []) short.set(unit, issue.detail || '');
    for (const peg of (canonical.materials && canonical.materials.pegs) || []) pegged.add(peg.unit);
    for (const row of (canonical.progress && canonical.progress.rows) || []) progress.set(row.operation_id, row);
    evidenceCache = {meta:data.meta, short, pegged, progress};
    return evidenceCache;
  };
  // Material of a plan unit: 'short' (declared shortage), 'ok' (lot assigned by the pegging), null (no data).
  const materialState = (a) => evidence().short.has(a.task_id) ? 'short' : evidence().pegged.has(a.task_id) ? 'ok' : null;
  /* Material detail. The plan only says which units are covered (pegs) or short (shortages) in the source manifest;
   * what each part needs and which supplies feed it comes from /api/materials (needs and supplies of the active data).
   * Nothing is made up: a supplier, a date or a text the source does not give is shown as missing. */
  let materialFeed = {meta:null, value:null};
  const materialData = () => {
    if (materialFeed.meta === data.meta) return materialFeed.value;
    const mine = materialFeed = {meta:data.meta, value:null};
    // An archived revision keeps its own manifest but the needs and supplies are the active ones: no detail rather than a wrong one.
    if (state.archived || state.archiveBase) { mine.value = {needs:[], supplies:[], unavailable:true}; return mine.value; }
    fetch('/api/materials', {cache:'no-store'}).then(r => r.ok ? r.json() : null).catch(() => null).then(v => {
      mine.value = v && v.available ? v : {needs:[], supplies:[], unavailable:true};
      if (materialFeed === mine) { updateInspector(); refreshMaterialDialog(); }
    });
    return null;
  };
  const qtyText = (v) => Number(v).toLocaleString('it-IT', {maximumFractionDigits:3});
  let itemTextCache = null;
  // Description and storage of an article, known only for the sheets of the nesting programs (descrizioni.json).
  const itemTexts = () => {
    if (itemTextCache && itemTextCache.labels === labels) return itemTextCache.map;
    const map = new Map();
    for (const nest of Object.values(labels && labels.nests || {}))
      if (nest && nest.sheet_part) map.set(nest.sheet_part, {
        text:[nest.sheet, nest.thickness_mm ? 'sp. ' + nest.thickness_mm : '', nest.size_mm ? nest.size_mm.replace(/(\d+)(\.\d+)?/g, '$1') + ' mm' : ''].filter(Boolean).join(' · '),
        where:[nest.warehouse ? 'Mag. ' + nest.warehouse : '', nest.location && nest.location !== nest.warehouse ? 'ubic. ' + nest.location : ''].filter(Boolean).join(' · ')});
    itemTextCache = {labels, map};
    return map;
  };
  // Parts needed by a set of plan units and what covers them: needs, pegs and shortages summed per article.
  const unitParts = (tasks) => {
    const feed = materialData(), mat = data.meta && data.meta.canonical && data.meta.canonical.materials || {};
    const units = new Set(tasks.map(a => a.task_id)), ops = new Set(tasks.flatMap(a => a.operation_ids));
    const all = feed && feed.supplies || [], supplies = new Map(all.map(s => [s.id, s]));
    const parts = new Map();
    const part = (item) => { if (!parts.has(item)) parts.set(item, {item, need:0, missing:0, cover:[]}); return parts.get(item); };
    for (const n of feed && feed.needs || []) if (ops.has(n.operation_id)) part(n.item_id).need += Number(n.quantity);
    for (const peg of mat.pegs || []) if (units.has(peg.unit))
      part(peg.item_id).cover.push({supply:supplies.get(peg.supply_id) || {id:peg.supply_id}, quantity:Number(peg.quantity)});
    for (const s of mat.shortages || []) if (units.has(s.unit)) part(s.item_id).missing += Number(s.missing);
    return [...parts.values()].map(p => {
      const covered = p.cover.reduce((sum, c) => sum + c.quantity, 0);
      const ordered = new Map();
      for (const c of p.cover) if (c.supply.kind === 'purchase_order') ordered.set(c.supply.id, {supply:c.supply, used:(ordered.has(c.supply.id) ? ordered.get(c.supply.id).used : 0) + c.quantity});
      const fromOrders = [...ordered.values()].reduce((sum, o) => sum + o.used, 0);
      return Object.assign(p, {need:Math.max(p.need, covered + p.missing), stock:covered - fromOrders, ordered:[...ordered.values()],
        others:all.filter(s => s.item_id === p.item && s.kind === 'purchase_order' && !ordered.has(s.id))});
    }).sort((x, y) => (y.missing > 0) - (x.missing > 0) || x.item.localeCompare(y.item));
  };
  // Colour of the dot of a group of phases: all covered, all short or mixed.
  const groupMaterial = (as) => {
    let ok = 0, short = 0;
    for (const a of as) { const m = materialState(a); if (m === 'short') short++; else if (m === 'ok') ok++; }
    return ok + short ? {ok, short, kind:!short ? 'ok' : !ok ? 'short' : 'mixed'} : null;
  };
  // Dot next to a Gantt bar (a sibling, not a child: the bar is already a button). ``kind``: ok | short | mixed.
  const materialDot = (kind, attr, style, title) => '<button type="button" class="material-dot gx-mdot '+kind+'" '+attr+' style="'+style+'" title="'+esc(title)+'" aria-label="'+esc(title.replace(/\n/g,'. '))+'"></button>';
  const SIMULATED_SUPPLY ='SIMULATO';  // reference the CLIENTE export gives to the arrival dates it simulates (no AS400 source yet)
  function supplyOrderCard(supply, used) {
    const when = supply.available_at, late = when && ms(when) > ms(data.scenario.start), simulated = supply.reference === SIMULATED_SUPPLY;
    return '<article class="supply-order accent-'+(simulated?'warn':late?'neutral':'ok')+'"><div class="supply-order-head"><strong>Ord. '+esc(supply.id)+'</strong>'+
      (simulated?'<span class="supply-order-state" title="Data di arrivo simulata dall’export: manca la fonte degli ordini a fornitore">Simulato</span>':
        supply.reference?'<span class="supply-order-state" title="Riferimento della fonte">'+esc(supply.reference)+'</span>':'')+
      '<span class="supply-status '+(late?'open':when?'fulfilled':'missing')+'">'+(when?late?'In arrivo':'Già disponibile':'Data non indicata')+'</span></div>'+
      '<p class="supply-supplier">Fornitore non indicato dalla fonte <span class="supply-order-dates">· arrivo previsto '+esc(when?fmt(when,'date'):'—')+'</span></p>'+
      '<p class="supply-quantity">Quantità ordinata '+esc(qtyText(supply.quantity))+(used!==undefined?' · impegnata da questa selezione '+esc(qtyText(used)):'')+'</p></article>';
  }
  let materialDialogSpec = null;
  function materialDialogHTML(spec) {
    const tasks = spec.scope === 'order' ? [...new Map(data.plan.assignments.filter(a => taskOrders(a).includes(spec.id)).map(a => [a.task_id, a])).values()] :
      data.plan.assignments.filter(a => a.task_id === spec.id);
    const first = tasks[0], isOrder = spec.scope === 'order';
    const eyebrow = isOrder ? 'MATERIALE · ' + orderCode(spec.id) + (orderCustomer(spec.id) ? ' · ' + orderCustomer(spec.id) : '') : 'MATERIALE DELLA FASE · ' + (first ? taskOrders(first).map(orderCode).join(', ') : '');
    const title = isOrder ? orderCode(spec.id) + (orderCustomer(spec.id) ? ' · ' + orderCustomer(spec.id) : '') : first ? taskTitle(first) : 'Fase';
    const subtitle = isOrder ? tasks.length + (tasks.length === 1 ? ' lavorazione' : ' lavorazioni') + ' · materiale dell’intera commessa' : first ? taskSubtitle(first) : '';
    const head = '<button class="close" type="button" data-close-material aria-label="Chiudi">×</button><p class="eyebrow">'+esc(eyebrow)+'</p><h2>'+esc(title)+'</h2><p>'+esc(subtitle)+'</p>';
    const shell = (body) => '<div class="modal-backdrop" id="material-dialog" data-close-material role="presentation"><aside class="modal material-modal" role="dialog" aria-modal="true" aria-label="Materiale">'+head+body+'</aside></div>';
    if (materialData() === null) return shell('<p class="modal-muted">Carico le parti…</p>');
    const feed = materialData(), parts = unitParts(tasks);
    if (feed.unavailable && !parts.length) return shell('<p class="modal-muted">Fabbisogni e ordini non disponibili per questa fonte o per una revisione archiviata.</p>');
    if (!parts.length) return shell('<p class="modal-muted">Nessuna parte d’acquisto collegata '+(isOrder?'a questa commessa.':'a questa fase.')+'</p>');
    const shortN = parts.filter(p => p.missing > 0).length, byOrder = parts.filter(p => p.missing <= 0 && p.ordered.length).length;
    const simulated = parts.some(p => p.ordered.some(o => o.supply.reference === SIMULATED_SUPPLY));
    const shared = tasks.some(a => nestId(a) && taskOrders(a).length > 1);
    const rows = parts.map((p, i) => {
      const text = itemTexts().get(p.item), key = 'mat-' + i, cls = p.missing > 0 ? 'short' : p.ordered.length ? '' : 'available';
      const orderCell = p.ordered.length ?
        '<button type="button" class="supply-status supply-status-btn '+(p.missing>0?'partial':'open')+'" data-mat-toggle="'+key+'" aria-expanded="false">'+
          esc(p.ordered.length === 1 ? 'Ordine ' + p.ordered[0].supply.id + (p.ordered[0].supply.available_at ? ' · arriva ' + fmt(p.ordered[0].supply.available_at,'date') : '') : p.ordered.length + ' ordini in arrivo')+'</button>' :
        p.missing > 0 ? '<span class="supply-status none">'+(p.others.length ? 'Ordini già impegnati da altre fasi (' + p.others.length + ')' : 'Nessun ordine a fornitore collegato')+'</span>' :
        '<span class="supply-status fulfilled" title="Disponibile a magazzino: nessun ordine necessario">Da magazzino · nessun ordine</span>';
      const cards = p.ordered.map(o => supplyOrderCard(o.supply, o.used)).join('') + (p.missing > 0 ? p.others.map(o => supplyOrderCard(o)).join('') : '');
      return '<tr class="'+cls+'"><td>'+esc(p.item)+'</td><td>'+(text ? esc(text.text) + (text.where ? '<small class="cell-sub">'+esc(text.where)+'</small>' : '') : '—')+'</td>'+
        '<td class="num">'+esc(qtyText(p.need))+'</td><td class="num">'+(p.stock > 0 ? esc(qtyText(p.stock)) : '—')+'</td><td class="num">'+(p.missing > 0 ? esc(qtyText(p.missing)) : '—')+'</td><td>'+orderCell+'</td></tr>'+
        (cards ? '<tr class="mat-order-detail-row" id="'+key+'" hidden><td colspan="6"><div class="mat-orders-detail">'+cards+'</div></td></tr>' : '');
    }).join('');
    return shell('<p class="mat-recap '+(shortN?'short':'ok')+'">'+parts.length+(isOrder?(parts.length===1?' codice parte':' codici parte'):(parts.length===1?' parte':' parti'))+' · '+(shortN?shortN+' con materiale mancante':'tutte coperte')+
      (isOrder?' · fabbisogno sommato su tutte le fasi':'')+'</p>'+
      (byOrder?'<p class="mat-recap covered">'+byOrder+(byOrder===1?' parte coperta':' parti coperte')+' da ordini a fornitore in arrivo</p>':'')+
      (simulated?'<p class="mat-recap simulated">Gli ordini «Simulato» hanno data di arrivo simulata: manca la fonte degli ordini a fornitore (AS400).</p>':'')+
      (shared?'<p class="mat-recap neutral">I programmi di nesting condivisi fra più commesse sono contati per intero.</p>':'')+
      '<div class="mat-table-scroll"><table class="mat-table"><thead><tr><th>Codice</th><th>Descrizione</th><th class="num">Fabbisogno</th><th class="num">Disponibile</th><th class="num">Mancante</th><th>Ordine a fornitore</th></tr></thead><tbody>'+rows+'</tbody></table></div>');
  }
  function closeMaterialDialog() {
    materialDialogSpec = null;
    const el = document.getElementById('material-dialog');
    if (el) el.remove();
  }
  function refreshMaterialDialog() {
    if (!materialDialogSpec) return;
    const el = document.getElementById('material-dialog');
    if (el) el.outerHTML = materialDialogHTML(materialDialogSpec);
  }
  function openMaterialDialog(spec) {
    closeMaterialDialog();
    materialDialogSpec = spec;
    document.body.insertAdjacentHTML('beforeend', materialDialogHTML(spec));
    const close = document.querySelector('#material-dialog .close');
    if (close) close.focus();
  }
  // Produced share of a started phase from the progress domain (pieces), null when not attested.
  const progressShare = (a) => {
    let done = 0, total = 0;
    for (const id of a.operation_ids) {
      const row = evidence().progress.get(id), op = opInfo(id);
      if (!row || !op || !op.quantity || row.produced_quantity === null || row.produced_quantity === undefined) continue;
      done += Number(row.produced_quantity); total += Number(op.quantity);
    }
    return total > 0 ? Math.max(0, Math.min(1, done / total)) : null;
  };
  const isLateTask = (a) => {
    const ops = operationsById();
    const end = ms(assignmentEnd(a));
    return a.operation_ids.some(id => ops.get(id) && ops.get(id).due &&
      Number.isFinite(end) && end > ms(ops.get(id).due));
  };
  const modeLabel = (mode) => ({auto:'Automatica',forward:'Al più presto',backward:'A ritroso',fixed:'Fissata',
    completed:'Completata',external:'Data fornitore'}[mode] || mode || 'Non indicata');
  function appliedModeLabel(assignments) {
    const modes=[...new Set(assignments.map(a=>a.effective_mode).filter(Boolean))];
    if(modes.includes('forward')&&modes.includes('backward'))return 'Mista · al più presto e a ritroso';
    return modes.map(modeLabel).join(' · ')||'Non indicata';
  }
  const relativeDate = (step) => atMidnight(new Date(state.anchor.getTime() + step * day + 12 * 3600000));
  // Visible horizon: a day, a working week (Mon–Fri), 14 days, a calendar month or a free from–to range.
  const MAX_RANGE_DAYS = 92;
  const shiftDay = (from, n) => atMidnight(new Date(from.getTime() + n * day + 12 * 3600000));
  const monthStart = (date, offset) => {
    const [y, m] = iso(date).split('-').map(Number);
    return atMidnight(new Date(Date.UTC(y, m - 1 + (offset || 0), 1, 12)));
  };
  const monthLength = (date) => { const [y, m] = iso(date).split('-').map(Number); return new Date(Date.UTC(y, m, 0)).getUTCDate(); };
  const modeForSpan = (n) => n === 1 ? 'day' : n === 5 ? 'week' : n === 14 ? 'fortnight' : 'range';
  function setHorizon(mode, from, to) {
    state.mode = mode;
    if (mode === 'day') state.span = 1;
    else if (mode === 'week') {
      state.anchor = shiftDay(state.anchor, -((weekday(state.anchor) + 6) % 7));
      state.span = 5;
    }
    else if (mode === 'fortnight') state.span = 14;
    else if (mode === 'month') { state.anchor = monthStart(state.anchor); state.span = monthLength(state.anchor); }
    else if (mode === 'range' && from) {
      state.anchor = from;
      const last = to && to.getTime() >= from.getTime() ? to : from;
      state.span = Math.min(MAX_RANGE_DAYS, Math.round((last.getTime() - from.getTime()) / day) + 1);
    }
  }
  function stepHorizon(direction) {
    if (state.mode === 'week') state.anchor = shiftDay(state.anchor, 7 * direction);
    else if (state.mode === 'month') { state.anchor = monthStart(state.anchor, direction); state.span = monthLength(state.anchor); }
    else state.anchor = relativeDate(direction * state.span);
  }
  const viewTitle = {
    machines:['Piano macchine','Le lavorazioni assegnate alle risorse, con disponibilità e precedenze verificate.'],
    orders:['Gantt commesse','Una vista per commessa, con fasi, consegne e dipendenze temporali.'],
    nesting:['Nesting e piani di lavoro','Programmi condivisi fra commesse, senza duplicare il tempo di taglio.'],
    loads:['Carichi macchine','Ore prenotate e carichi delle risorse nel periodo selezionato.'],
    materials:['Disponibilità materiali','Fabbisogni, lotti impegnati e carenze dai dati canonici, se disponibili.'],
    progress:['Avanzamento e consuntivi','Registrazioni provenienti dalla fonte, distinte dai residui pianificati.'],
    staged:['Setup / run v0.3','Anteprima tecnica di attrezzaggi e lavorazioni con audit indipendente e fonti esplicite.'],
    deliveries:['Consegne e avvisi','Ritardi, date previste e criticità di pianificazione.'],
    calendars:['Calendari e turni','Disponibilità dichiarata per risorsa e turni del modello canonico.'],
    daily:['Programma giornaliero','La lista operativa delle lavorazioni in programma per ogni risorsa.'],
    parameters:['Parametri di calcolo','Simula i criteri di pianificazione senza sovrascrivere i dati sorgente.'],
    bottlenecks:['Colli di bottiglia e problemi','Dove si formano le code, quali risorse frenano il piano e cosa va risolto per recuperare i ritardi.'],
    assistant:['Assistente e analisi','Indicatori spiegabili ricavati dal piano e, se collegata, analisi AI in sola lettura.'],
    memory:['Memoria aziendale','Regole con valori e indicazioni testuali, confermate dai pianificatori e con origine tracciata.']
  };
  const extraViews = window.PFData || null;
  if (extraViews) Object.assign(viewTitle, extraViews.titles);
  const isExtra = (view) => Boolean(extraViews && extraViews.handles(view));
  let data = null;
  const state = {
    view:'machines', span:5, mode:'week', anchor:new Date(Date.UTC(2026,9,5)),
    query:'', resourceKind:'machine', showAll:false, selected:null,
    expanded:new Set(), selectedOrder:null, dailyDate:null, settings:{}, busy:false, toastTimer:0,
    edits:{fixed_tasks:[],closures:[]}, baselinePlan:null,
    chat:{open:false,busy:false,messages:[],id:null,menu:null,checked:new Set(),speaking:null,utterance:null}, explain:null, loadsUnit:'hours', loadsCache:null, quality:null,
    stagedResult:null, stagedError:null, stagedBusy:false, delays:null, delayCause:null, delayCustomer:null, showAllCustomers:false, loadCell:null,
    sessionRole:null, savedRevision:0, archived:false, archiveBase:null
  };
  const LAYOUT_KEY = 'planning-factory-layout-v1';
  const layout = (() => {
    const defaults = {navCollapsed:false, navSections:{}, inspectorCollapsed:false, inspectorDrawerOpen:false, summaryOpen:true,
      inspectorWidth:null, chatWidth:null};
    try {
      const saved = JSON.parse(localStorage.getItem(LAYOUT_KEY) || '{}');
      return {...defaults, ...saved};
    } catch (_) { return defaults; }
  })();
  function saveLayout() {
    try { localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout)); } catch (_) {}
  }
  function applyLayout() {
    document.body.classList.toggle('nav-collapsed', layout.navCollapsed);
    $$('.nav-group[data-nav-section]').forEach(group => {
      const collapsed = Boolean(layout.navSections && layout.navSections[group.dataset.navSection]);
      group.classList.toggle('section-collapsed', collapsed);
      const toggle = group.querySelector('.nav-section-toggle');
      if (toggle) toggle.setAttribute('aria-expanded', String(!collapsed));
    });
    document.body.classList.toggle('inspector-collapsed', layout.inspectorCollapsed);
    if (Number.isFinite(layout.inspectorWidth)) document.documentElement.style.setProperty('--inspector-size', layout.inspectorWidth + 'px');
    if (Number.isFinite(layout.chatWidth)) document.documentElement.style.setProperty('--assistant-size', layout.chatWidth + 'px');
    const section = $('#summary-section');
    if (section) section.open = layout.summaryOpen;
    syncLayoutControls();
  }
  function syncLayoutControls() {
    const menu = $('#menu-toggle'), sidebar = $('#sidebar');
    const mobileNav = window.innerWidth <= 760;
    const navExpanded = mobileNav ? sidebar.classList.contains('open') : !layout.navCollapsed;
    menu.setAttribute('aria-expanded', String(navExpanded));
    menu.setAttribute('aria-label', navExpanded ? 'Comprimi navigazione' : 'Espandi navigazione');
    menu.title = navExpanded ? 'Comprimi navigazione' : 'Espandi navigazione';
    $$('.nav-item').forEach(item => {
      const collapsed = !mobileNav && layout.navCollapsed;
      if (!collapsed) { item.removeAttribute('title'); return; }
      const label = item.cloneNode(true);
      label.querySelectorAll('.nav-counter').forEach(counter => counter.remove());
      item.title = label.textContent.replace(/\s+/g, ' ').trim();
    });
    const inspector = $('#inspector'), toggle = $('#inspector-toggle');
    const drawer = window.innerWidth <= 1100 || document.body.classList.contains('wide-view');
    const inspectorExpanded = state.chat.open ? false : drawer ? inspector.classList.contains('open') : !layout.inspectorCollapsed;
    toggle.setAttribute('aria-expanded', String(inspectorExpanded));
    toggle.setAttribute('aria-label', inspectorExpanded ? 'Chiudi dettaglio' : 'Apri dettaglio');
    toggle.title = inspectorExpanded ? 'Chiudi dettaglio' : 'Apri dettaglio';
    const section = $('#summary-section');
    const hint = section && section.querySelector('.summary-toggle-hint');
    if (hint) hint.textContent = section.open ? 'Comprimi' : 'Espandi';
  }
  function setInspectorOpen(open) {
    const inspector = $('#inspector');
    const drawer = window.innerWidth <= 1100 || document.body.classList.contains('wide-view');
    layout.inspectorCollapsed = !open;
    document.body.classList.toggle('inspector-collapsed', !open);
    if (drawer) {
      layout.inspectorDrawerOpen = open;
      inspector.classList.toggle('open', open);
    } else {
      layout.inspectorDrawerOpen = false;
    }
    saveLayout();
    syncLayoutControls();
  }
  function setSidebarOpen(open) {
    const sidebar = $('#sidebar');
    if (window.innerWidth <= 760) {
      sidebar.classList.toggle('open', open);
    } else {
      layout.navCollapsed = !open;
      document.body.classList.toggle('nav-collapsed', !open);
      saveLayout();
    }
    syncLayoutControls();
  }
  function setNavSectionCollapsed(section, collapsed) {
    layout.navSections = {...(layout.navSections || {}), [section]:collapsed};
    const group = document.querySelector('.nav-group[data-nav-section="' + section + '"]');
    if (group) {
      group.classList.toggle('section-collapsed', collapsed);
      const toggle = group.querySelector('.nav-section-toggle');
      if (toggle) toggle.setAttribute('aria-expanded', String(!collapsed));
    }
    saveLayout();
  }
  function bindPanelResize(handleId, panelId, variable, prefKey, minimum, maximum) {
    const handle = $('#' + handleId), panel = $('#' + panelId);
    let drag = null;
    const bounds = () => {
      const hi = Math.min(maximum, Math.max(minimum, window.innerWidth - 32));
      return [Math.min(minimum, hi), hi];
    };
    const setWidth = value => {
      const [lo, hi] = bounds();
      const width = Math.round(Math.max(lo, Math.min(hi, value)));
      document.documentElement.style.setProperty(variable, width + 'px');
      handle.setAttribute('aria-valuenow', String(width));
      return width;
    };
    const persist = width => { layout[prefKey] = width; saveLayout(); };
    handle.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      event.preventDefault();
      drag = {id:event.pointerId, x:event.clientX, width:panel.getBoundingClientRect().width};
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener('pointermove', event => {
      if (!drag || drag.id !== event.pointerId) return;
      setWidth(drag.width + drag.x - event.clientX);
    });
    const finish = event => {
      if (!drag || drag.id !== event.pointerId) return;
      persist(setWidth(panel.getBoundingClientRect().width));
      drag = null;
    };
    handle.addEventListener('pointerup', finish);
    handle.addEventListener('pointercancel', finish);
    handle.addEventListener('keydown', event => {
      const current = panel.getBoundingClientRect().width;
      const [lo, hi] = bounds();
      let next = current;
      if (event.key === 'ArrowLeft') next += 16;
      else if (event.key === 'ArrowRight') next -= 16;
      else if (event.key === 'Home') next = lo;
      else if (event.key === 'End') next = hi;
      else return;
      event.preventDefault();
      persist(setWidth(next));
    });
  }

  function setToast(msg, timeout=4500) {
    const box = $('#toast');
    box.textContent = msg;
    box.hidden = false;
    clearTimeout(state.toastTimer);
    state.toastTimer = setTimeout(() => box.hidden = true, timeout);
  }
  function dayRange() {
    return Array.from({length:state.span}, (_,i) => relativeDate(i));
  }
  function viewWindow() {
    return [state.anchor.getTime(), relativeDate(state.span).getTime()];
  }
  function topCards() {
    const p = data.plan;
    const active = p.assignments.filter(a => a.segments.length);
    const late = p.orders.filter(o => o.tardiness_seconds !== null && o.tardiness_seconds > 0);
    // Machines actually used by the plan: operators and idle equipment would dilute the average to nothing.
    const kinds = new Map(data.scenario.resources.map(r => [r.id, r.kind]));
    const machines = p.resources.filter(r => r.utilization !== null && r.booked_unit_seconds > 0 && kinds.get(r.resource_id) === 'machine');
    const near = kpiSaturation();
    const avg = near.rows ? near.avg : machines.length ? machines.reduce((sum, r) => sum + r.utilization, 0)/machines.length : null;
    const top = near.rows ? near.top : machines.slice().sort((a,b) => b.utilization - a.utilization)[0];
    const nests = active.filter(a => a.task_id.startsWith('nest:')).length;
    const list = [
      ['Lavorazioni pianificate', active.length.toLocaleString('it-IT'),
       nests + ' programmi di nesting · ' + (active.length - nests) + ' fasi', '▦', ''],
      ['Commesse in ritardo', late.length.toLocaleString('it-IT'),
       'su ' + p.orders.length + ' commesse nel piano', '⏱',late.length?'red':'mint'],
      ['Saturazione macchine' + (near.rows ? ' · 4 settimane' : ''), pct(avg), (near.rows ? near.count : machines.length) + ' macchine impegnate' + (top ? ' · max ' + resourceCode(top.resource_id) + ' ' + pct(top.utilization) : ''), '◫',''],
      ['Non collocate', p.unscheduled.length.toLocaleString('it-IT'),
       'Materiale, vincoli o capacità non soddisfatti', '⚠',p.unscheduled.length?'warn':'mint'],
    ];
    $('#summary-cards').innerHTML = list.map(c => (
      '<article class="summary-card '+ c[4] +'"><div><span class="label">'+esc(c[0])+'</span>'+
      '<strong class="big">'+esc(c[1])+'</strong><p class="sub">'+esc(c[2])+'</p></div>'+
      '<span class="metric-icon" aria-hidden="true">'+c[3]+'</span></article>'
    )).join('');
    const count = $('#nav-alert-count');
    count.textContent = String(late.length + p.unscheduled.length);
    count.hidden = late.length + p.unscheduled.length === 0;
  }
  // Machine saturation over the first four weeks: booked / available hours from the engine's calendars.
  function kpiSaturation() {
    const digest=data.plan.scenario_digest;
    if(state.kpi&&state.kpi.digest===digest)return state.kpi;
    state.kpi={digest};
    postAnalysis('/api/loads',{scenario_digest:digest,start:iso(new Date(ms(data.scenario.start)+3600000)),days:28,kind:'machine'}).then(result=>{
      if(!state.kpi||state.kpi.digest!==digest)return;
      let booked=0,available=0,count=0,top=null;
      for(const row of result.rows){
        const b=row.booked_hours.reduce((s,x)=>s+x,0),a=(row.available_hours||[]).reduce((s,x)=>s+x,0);
        if(b<=0||!a)continue;
        booked+=b;available+=a;count++;
        if(!top||b/a>top.utilization)top={resource_id:row.resource_id,utilization:b/a};
      }
      state.kpi={digest,rows:true,avg:available?booked/available:null,top,count};
      if(data&&data.plan.scenario_digest===digest&&!isExtra(state.view))topCards();
    }).catch(()=>{});
    return state.kpi;
  }
  function setHeading() {
    const title = viewTitle[state.view] || viewTitle.machines;
    $('#page-title').textContent = title[0];
    $('#page-subtitle').textContent = title[1];
    $('#snapshot-label').textContent = data.scenario.id + (hasWhatif()?' · simulazione':'');
    $('#dataset-age').textContent = 'Scenario ' + fmt(data.scenario.start, 'day');
    $('#scenario-hash').textContent = 'SHA ' + short(data.plan.scenario_digest, 14);
    $('#page-eyebrow').textContent = state.view==='parameters'?'CALCOLO E SCENARI':'PRODUCTION CONTROL CENTER';
    $('#reset-button').hidden=!hasWhatif();
  }
  function setView(next) {
    if (!viewTitle[next]) return;
    state.view = next;
    state.query = '';
    if (next === 'daily' || next === 'calendars' || next === 'machines') state.resourceKind='machine';
    $$('.nav-item').forEach(el => {
      const active = el.dataset.view === next;
      el.classList.toggle('active', active);
      if (active) el.setAttribute('aria-current','page');
      else el.removeAttribute('aria-current');
    });
    $('#sidebar').classList.remove('open');
    layout.inspectorDrawerOpen = false;
    $('#inspector').classList.remove('open');
    saveLayout();
    syncLayoutControls();
    try { history.replaceState({}, '', '#' + next); } catch (_) {}
    render();
    if (window.innerWidth <= 760) $('#main').focus({preventScroll:true});
  }

  function toolBar(extra) {
    const d = dayRange();
    return '<div class="panel-title"><div><h2>Orizzonte visibile</h2><p class="panel-sub">'+
      esc(fmt(d[0], 'monthDayYear'))+' — '+esc(fmt(d[d.length-1],'monthDayYear'))+
      ' · ora locale '+esc(displayTz)+'</p></div><div class="panel-tools">'+
      '<button class="icon-button" type="button" data-action="prev" aria-label="Periodo precedente">‹</button>'+
      '<button class="icon-button" type="button" data-action="next" aria-label="Periodo successivo">›</button>'+
      '<div class="segmented" role="group" aria-label="Orizzonte visualizzato">'+
      [['day','Giorno','1 giorno'],['week','Settimana','5 giorni, lun–ven'],['fortnight','14 giorni','14 giorni'],['month','Mese','mese di calendario']]
        .map(m => '<button type="button" data-mode="'+m[0]+'" title="'+m[2]+'" aria-pressed="'+(state.mode===m[0])+'" class="'+(state.mode===m[0]?'active':'')+'">'+m[1]+'</button>').join('')+
      '</div>'+
      '<div class="range-picker'+(state.mode==='range'?' active':'')+'" role="group" aria-label="Intervallo personalizzato">'+
      '<label>Dal <span class="range-date-readable">'+esc(fmt(d[0],'monthDayYear'))+'</span><input type="date" id="range-from" class="toolbar-select" aria-label="Dal '+esc(fmt(d[0],'monthDayYear'))+'" value="'+esc(iso(d[0]))+'"></label>'+
      '<label>al <span class="range-date-readable">'+esc(fmt(d[d.length-1],'monthDayYear'))+'</span><input type="date" id="range-to" class="toolbar-select" aria-label="Al '+esc(fmt(d[d.length-1],'monthDayYear'))+'" value="'+esc(iso(d[d.length-1]))+'" min="'+esc(iso(d[0]))+'"></label></div>'+
      (extra||'') +'</div></div>';
  }

  function markerHTML(windowStart, windowEnd) {
    const now = Date.now();
    if (now < windowStart || now >= windowEnd) return '';
    const left = ((now-windowStart)/(windowEnd-windowStart))*100;
    return '<span class="today-marker" style="left:'+left.toFixed(5)+'%" title="Ora attuale"></span>';
  }
  // Tooltip line with what is cut or worked: article code, steel/description, thickness, sheet size.
  function materialLine(a) {
    const n = nestId(a) ? nestInfo(a) : null;
    if (n) return 'Materiale: '+[n.sheet_part, n.sheet || n.material, n.thickness_mm ? 'spessore '+n.thickness_mm+' mm' : '', n.size_mm ? n.size_mm.replace(/(\d+)(\.\d+)?/g,'$1')+' mm' : ''].filter(Boolean).join(' · ');
    const op = opInfo(a.operation_ids[0]);
    if (!op || !(op.material || op.thickness_mm)) return '';
    return 'Materiale: '+[op.material, op.thickness_mm ? 'spessore '+op.thickness_mm+' mm' : ''].filter(Boolean).join(' · ');
  }
  // Cutting programs lead with what is being cut: thickness chip, steel grade and article code.
  function nestBookingBody(a, nest, roomy, width) {
    const thk = nest.thickness_mm ? '<span class="nest-thk">'+esc(nest.thickness_mm)+(roomy?' mm':'')+'</span>' : '';
    if (!roomy) return width >= 2.5 ? thk.replace('nest-thk','nest-thk solo') : '';
    const lock = a.fixed ? '<span class="lock" aria-hidden="true">🔒</span>' : '';
    return '<strong>'+thk+lock+esc(taskTitle(a))+'</strong>'+
      '<small class="nest-mat">'+esc(nest.sheet || nest.material || '—')+'</small>'+
      '<small>'+esc([nest.sheet_part, customersText(a,1)].filter(Boolean).join(' · '))+'</small>';
  }
  function bookHTML(a, segment, start, end, kind) {
    const lower = max(ms(segment.start),start);
    const upper = min(ms(segment.end),end);
    if (!Number.isFinite(lower) || !Number.isFinite(upper) || lower>=upper) return '';
    const left = 100*(lower-start)/(end-start);
    const width = 100*(upper-lower)/(end-start);
    const family = kind==='operator'?'operator':kind==='supplier'?'ext':phaseFamily(a);
    const material = materialState(a), done = progressShare(a);
    const nest = nestId(a) ? (nestInfo(a) || {}) : null;
    // Below ~60px a label is unreadable: the bar keeps only colour and markers, the tooltip has the text.
    const roomy = width >= 6;
    const who = customersText(a, 4);
    const matLine = materialLine(a);
    const tip = taskTitle(a)+(who?' · '+who:'')+'\n'+taskSubtitle(a)+(matLine?'\n'+matLine:'')+'\n'+fmt(segment.start)+' → '+fmt(segment.end)+
      (isLateTask(a)?'\nFine oltre la consegna':'')+(a.fixed?'\nConfermata: non spostata dal ricalcolo':'')+
      (material==='short'?'\nMateriale mancante':material==='ok'?'\nMateriale assegnato':'')+
      (done!==null?'\nAvanzamento '+Math.round(done*100)+'% (TRD)':'');
    return '<button type="button" class="booking fam-'+family+(isLateTask(a)?' late':'')+(a.fixed?' fixed':'')+
      (roomy?'':' tiny')+(state.selected===a.task_id?' selected':'')+
      '" data-task="'+esc(a.task_id)+'" style="left:'+left.toFixed(6)+'%;width:'+width.toFixed(6)+'%'+
      (done!==null?';--done:'+(done*100).toFixed(1)+'%':'')+'" title="'+esc(tip)+'">'+
      (done!==null?'<span class="booking-progress" aria-hidden="true"></span>':'')+
      // A button cannot nest in a button: the dot is a span that the delegated click handler treats as a link to the parts dialog.
      (material?'<span class="material-dot '+material+'" role="button" data-material-task="'+esc(a.task_id)+'" title="'+
        (material==='short'?'Materiale mancante':'Materiale disponibile')+' · clic per il dettaglio delle parti" aria-label="Dettaglio materiale"></span>':'')+
      (nest?nestBookingBody(a,nest,roomy,width):
      roomy?'<strong>'+(a.fixed?'<span class="lock" aria-hidden="true">🔒</span>':'')+esc(taskTitle(a))+'</strong>'+
        '<small>'+esc([customersText(a,1),taskSubtitle(a)].filter(Boolean).join(' · '))+'</small>':'')+'</button>';
  }
  // Short phases next to each other become one readable block; a click on it opens that day hour by hour.
  function trackHTML(list, start, end, kind) {
    const items=[];
    for(const a of list)for(const seg of a.segments){
      const lo=max(ms(seg.start),start),hi=min(ms(seg.end),end);
      if(lo<hi)items.push({a,seg,lo,hi,width:100*(hi-lo)/(end-start)});
    }
    items.sort((x,y)=>x.lo-y.lo);
    const near=(end-start)*0.012, out=[];
    let group=[];
    const flush=()=>{
      if(!group.length)return;
      const width=100*(group[group.length-1].hi-group[0].lo)/(end-start);
      const single=new Set(group.map(x=>x.a)).size===1;
      if(group.length<2||width<6||state.span===1)out.push(...group.map(x=>bookHTML(x.a,x.seg,start,end,kind)));
      else if(single)out.push(bookHTML(group[0].a,{start:new Date(group[0].lo).toISOString(),end:new Date(group[group.length-1].hi).toISOString()},start,end,kind));
      else out.push(clusterHTML(group,start,end,kind));
      group=[];
    };
    for(const item of items){
      const tiny=item.width<6&&state.span>1;
      if(tiny&&group.length&&item.lo-group[group.length-1].hi<=near&&
         atMidnight(new Date(item.lo)).getTime()===atMidnight(new Date(group[0].lo)).getTime())group.push(item);
      else{flush();if(tiny)group.push(item);else out.push(bookHTML(item.a,item.seg,start,end,kind));}
    }
    flush();
    return out.join('');
  }
  function clusterHTML(group, start, end, kind) {
    const lo=group[0].lo,hi=group[group.length-1].hi,span=hi-lo;
    const tasks=[...new Set(group.map(x=>x.a))];
    const orders=[...new Set(tasks.flatMap(taskOrders))];
    const late=tasks.some(isLateTask),short=tasks.some(a=>materialState(a)==='short');
    const nests=tasks.filter(a=>nestId(a)).length;
    const stripes=group.map(x=>'<span class="stripe fam-'+(kind==='operator'?'operator':phaseFamily(x.a))+(isLateTask(x.a)?' late':'')+
      '" style="left:'+(100*(x.lo-lo)/span).toFixed(3)+'%;width:'+Math.max(.6,100*(x.hi-x.lo)/span).toFixed(3)+'%"></span>').join('');
    const tip=tasks.length+' lavorazioni · '+fmt(lo,'time')+' → '+fmt(hi,'time')+'\n'+
      tasks.slice(0,12).map(a=>[taskTitle(a),customersText(a,2),taskSubtitle(a)].filter(Boolean).join(' · ')).join('\n')+(tasks.length>12?'\n…':'')+'\nClic: apri il giorno ora per ora';
    return '<button type="button" class="booking cluster'+(late?' late':'')+'" data-action="zoom-day" data-day="'+esc(iso(new Date(lo)))+
      '" style="left:'+(100*(lo-start)/(end-start)).toFixed(6)+'%;width:'+(100*span/(end-start)).toFixed(6)+'%" title="'+esc(tip)+'">'+
      '<span class="stripes" aria-hidden="true">'+stripes+'</span>'+(short?'<span class="material-dot short" aria-hidden="true"></span>':'')+
      '<strong>'+tasks.length+' fasi'+(nests?' · '+nests+' nest':'')+'</strong>'+
      '<small>'+esc(orders.slice(0,3).map(orderCode).join(', ')+(orders.length>3?' +'+(orders.length-3):''))+'</small></button>';
  }
  function resourceLabel(r) {
    const display = r.kind==='operator'?'Operatore':r.kind==='supplier'?'Fornitore':
      r.kind==='tool'?'Attrezzatura':'Risorsa produttiva';
    const name = resourceName(r.id);
    const capacityScale = data.meta && data.meta.workbook && data.meta.workbook.capacity_scale || 1;
    const displayCapacity = r.capacity === null ? null : r.capacity / capacityScale;
    return '<div class="machine-label"><strong title="'+esc(r.id)+'">'+esc(resourceCode(r.id))+'</strong>'+
      '<small title="'+esc(name||display)+'">'+esc(name||display)+(displayCapacity===null?' · capacità ∞':displayCapacity!==1?' · capacità '+displayCapacity:'')+'</small></div>';
  }
  function filteredResources() {
    const kinds = state.resourceKind;
    const used = new Set(data.plan.assignments.flatMap(a => a.demands.map(d => d.resource_id)));
    const loads = new Map(data.plan.resources.map(r => [r.resource_id,r.booked_unit_seconds]));
    return data.scenario.resources
      .filter(r => used.has(r.id))
      .filter(r => kinds==='all' || (kinds==='machine' ?
        !['operator','tool','supplier','external'].includes(r.kind) : r.kind===kinds))
      .sort((a,b) => (number(loads.get(b.id))-number(loads.get(a.id))) || a.id.localeCompare(b.id));
  }
  function renderMachines() {
    const resources = filteredResources();
    const [start,end] = viewWindow();
    const ds = dayRange();
    const query = state.query.trim().toLocaleLowerCase();
    const filtered = resources.filter(r => !query || (r.id+' '+(resourceName(r.id)||'')).toLocaleLowerCase().includes(query) ||
      data.plan.assignments.some(a => a.demands.some(d=>d.resource_id===r.id) &&
        (a.task_id+' '+a.operation_ids.join(' ')+' '+taskOrders(a).join(' ')+' '+taskTitle(a)+' '+
          taskOrders(a).map(o=>orderCustomer(o)||'').join(' ')).toLocaleLowerCase().includes(query)));
    const shown = state.showAll ? filtered : filtered.slice(0, 22);
    const selects = '<label class="sr-only" for="resource-kind">Tipo risorsa</label>'+
      '<select id="resource-kind" class="toolbar-select" aria-label="Filtro tipo risorsa">'+
      [['machine','Macchine'],['operator','Operatori'],['tool','Attrezzature'],['supplier','Fornitori'],['all','Tutte le risorse']]
        .map(p => '<option value="'+p[0]+'" '+(state.resourceKind===p[0]?'selected':'')+'>'+p[1]+'</option>').join('')+'</select>'+
      '<div class="search-wrap"><input id="quick-search" class="toolbar-search" value="'+esc(state.query)+'" placeholder="Cerca commessa, fase, risorsa…" aria-label="Cerca lavorazioni"></div>';
    const hours = state.span===1;
    const columns = hours ? Array.from({length:24},(_,h)=>h) : ds;
    const machineBands = hours ? '' : '<div class="timeline-bands"><div class="timeline-band-corner"></div><div class="timeline-band-lane" style="grid-template-columns:repeat('+ds.length+',minmax(0,1fr))">'+
      calendarBandGroups(ds,'month').map(g=>'<span class="calendar-band month-band" style="grid-column:span '+(g.end-g.start)+'">'+esc(calendarBandLabel(g,'month'))+'</span>').join('')+
      calendarBandGroups(ds,'week').map(g=>'<span class="calendar-band week-band" style="grid-column:span '+(g.end-g.start)+'">'+esc(calendarBandLabel(g,'week'))+'</span>').join('')+
      '</div></div>';
    let out = '<article class="panel">'+toolBar(selects)+
      '<div class="timeline-scroll"><div class="timeline-layout">'+machineBands+'<div class="timeline-header'+(hours?'':' with-calendar-bands')+'">'+
      '<div class="timeline-corner">Macchina</div><div class="day-grid" style="grid-template-columns:repeat('+columns.length+',minmax(0,1fr))">'+
      (hours ? columns.map(h=>'<div class="day-col hour-col"><span>'+String(h).padStart(2,'0')+'</span></div>').join('') :
      ds.map(d=>{
        const busy = shown.reduce((n,r)=>n+(allocationByDay(r.id,d)>0?1:0),0);
        return '<div class="day-col '+([0,6].includes(weekday(d))?'weekend':'')+'">'+
          '<span>'+esc(fmt(d,'weekday'))+' '+esc(fmt(d,'monthDay'))+'</span><small>'+busy+(busy===1?' macchina':' macchine')+' al lavoro</small></div>';
      }).join(''))+'</div></div>';
    for (const r of shown) {
      const bookingList = data.plan.assignments.filter(a =>
        a.demands.some(d => d.resource_id===r.id) &&
        a.segments.some(seg => ms(seg.end)>start && ms(seg.start)<end))
        .sort((a,b)=>ms(assignmentStart(a))-ms(assignmentStart(b)));
      out += '<div class="machine-row">'+resourceLabel(r)+
        '<div class="timeline-track" style="--day-width:calc(100% / '+columns.length+')">'+
        trackHTML(bookingList,start,end,r.kind)+
        markerHTML(start,end)+'</div></div>';
    }
    out += '</div></div>';
    if (filtered.length>shown.length) out+='<div class="filters-line"><button class="btn btn-light btn-small" type="button" data-action="all-resources">Mostra tutte le '+filtered.length+' risorse</button></div>';
    if (!shown.length) out+=emptyState('Nessuna risorsa trovata','Prova un periodo differente o cambia i filtri.','▤');
    out+='<div class="timeline-legend">'+
      '<span class="legend-chip"><span class="legend-square fam-cut"></span>Taglio (nesting: attività unica, più commesse)</span>'+
      '<span class="legend-chip"><span class="legend-square fam-bend"></span>Piegatura</span>'+
      '<span class="legend-chip"><span class="legend-square fam-finish"></span>Altre lavorazioni</span>'+
      '<span class="legend-chip"><span class="legend-square fam-ext"></span>Esterno / fornitore</span>'+
      '<span class="legend-chip"><span class="legend-square late"></span>Oltre la consegna</span>'+
      '<span class="legend-chip">🔒 Confermata, non spostata dal ricalcolo</span>'+
      '<span class="legend-chip"><span class="legend-square progress"></span>Avanzamento TRD</span>'+
      '<span class="legend-chip"><span class="material-dot ok static"></span>Materiale assegnato</span>'+
      '<span class="legend-chip"><span class="material-dot short static"></span>Materiale mancante</span></div>'+
      '<p class="section-note">I blocchi sono gli intervalli prenotati dal motore; il testo compare quando la barra è abbastanza larga (usa “Giorno” per leggere tutte le fasi). Un nesting occupa la macchina una sola volta anche se serve più commesse.</p></article>';
    return out;
  }
  function emptyState(title, description, symbol) {
    return '<div class="empty-state"><div class="empty-icon">'+(symbol||'◇')+'</div><h2>'+esc(title)+'</h2><p>'+esc(description)+'</p></div>';
  }
  // Gantt commesse: una barra di sintesi per commessa (dalla prima all'ultima fase), espandibile nelle singole fasi.
  // La scala tempo comprime sabati e domeniche (come nei Gantt di produzione) per dare spazio ai giorni di lavoro.
  function ganttScale() {
    const [start,end] = viewWindow();
    const cols = [];
    if (state.span===1) for (let h=0;h<24;h++) cols.push({s:start+h*3600000,e:start+(h+1)*3600000,w:1,label:String(h).padStart(2,'0')});
    else {
      const ds = dayRange();
      ds.forEach((d,i) => {
        const we = [0,6].includes(weekday(d));
        const ghost = we && state.span<=14;
        const num = state.span>14 ? fmt(d,'dm').split('/')[0] : fmt(d,'dm');
        cols.push({s:d.getTime(),e:i+1<ds.length?ds[i+1].getTime():end,w:ghost?.34:1,ghost,
          label:state.span>14||(ghost&&state.span>7)?num:fmt(d,'weekday')+' '+num});
      });
    }
    const total = cols.reduce((s,c)=>s+c.w,0);
    const at = (t) => {
      if (t<=start) return 0;
      if (t>=end) return 100;
      let acc = 0;
      for (const c of cols) {
        if (t>=c.e) { acc+=c.w; continue; }
        acc += c.w*(t-c.s)/(c.e-c.s);
        break;
      }
      return acc/total*100;
    };
    return {start,end,cols,at};
  }
  // Espandere o comprimere ridisegna il Gantt: si rimette lo scorrimento dov'era.
  function renderKeepGantt() {
    const box = document.querySelector('.gx-scroll');
    const top = box ? box.scrollTop : 0, left = box ? box.scrollLeft : 0;
    render();
    const next = document.querySelector('.gx-scroll');
    if (next) { next.scrollTop = top; next.scrollLeft = left; }
  }
  function renderOrders() {
    const query = state.query.trim().toLocaleLowerCase();
    const sc = ganttScale(), start = sc.start, end = sc.end;
    const dates = dayRange();
    const ganttBands = state.span===1 ? '' : '<div class="gx-bands"><div class="gx-band-corner">Calendario</div><div class="gx-band-lane">'+
      ['month','week'].map(kind=>calendarBandGroups(dates,kind).map(g=>{
        const left=100*sc.at(dates[g.start].getTime());
        const right=g.end<dates.length?100*sc.at(dates[g.end].getTime()):100;
        return '<span class="gx-band '+kind+'" style="left:'+left.toFixed(4)+'%;width:'+Math.max(0,right-left).toFixed(4)+'%">'+esc(calendarBandLabel(g,kind))+'</span>';
      }).join('')).join('')+'</div></div>';
    const orderMap = new Map();
    for (const a of data.plan.assignments) {
      for (const oid of taskOrders(a)) {
        if (!orderMap.has(oid)) orderMap.set(oid,[]);
        orderMap.get(oid).push(a);
      }
    }
    let orders = data.plan.orders.filter(o=>!query||(o.order_id+' '+orderCode(o.order_id)+' '+(orderCustomer(o.order_id)||'')).toLocaleLowerCase().includes(query)||
      (orderMap.get(o.order_id)||[]).some(a=>a.task_id.toLocaleLowerCase().includes(query)));
    // Prima le commesse con lavorazioni nell'orizzonte, in ordine di inizio; poi le altre.
    const info = new Map();
    for (const o of orders) {
      const all = (orderMap.get(o.order_id)||[]).sort((a,b)=>ms(assignmentStart(a))-ms(assignmentStart(b)));
      const placed = all.filter(a=>a.segments.length);
      const first = placed.length?Math.min(...placed.map(a=>ms(assignmentStart(a)))):null;
      const last = placed.length?Math.max(...placed.map(a=>ms(assignmentEnd(a)))):null;
      const visible = placed.some(a=>a.segments.some(s=>ms(s.end)>start&&ms(s.start)<end));
      info.set(o.order_id,{as:all,first,last,visible});
    }
    orders = orders.map((o,i)=>({o,i})).sort((x,y)=>{
      const a=info.get(x.o.order_id),b=info.get(y.o.order_id);
      if (a.visible!==b.visible) return a.visible?-1:1;
      return a.visible?(a.first-b.first)||(x.i-y.i):x.i-y.i;
    }).map(x=>x.o);
    const nVisible = orders.filter(o=>info.get(o.order_id).visible).length;
    const orderDues = (o, as) => {
      const ops = operationsById(), set = new Set();
      for (const a of as) for (const id of a.operation_ids) {
        const op = ops.get(id);
        if (op && op.order_id===o.order_id && op.due) set.add(ms(op.due));
      }
      if (!set.size && o.due) set.add(ms(o.due));
      return [...set].filter(Number.isFinite).sort((x,y)=>x-y);
    };
    const tools='<div class="search-wrap"><input id="quick-search" class="toolbar-search" value="'+esc(state.query)+'" placeholder="Commessa o fase…" aria-label="Cerca commessa"></div>';
    const pos = (lo,hi) => {
      const l = sc.at(Math.max(lo,start)), r = sc.at(Math.min(hi,end));
      return 'left:'+l.toFixed(4)+'%;width:'+Math.max(r-l,.35).toFixed(4)+'%';
    };
    let out = '<article class="panel">'+toolBar(tools)+'<div class="filters-line"><span class="panel-sub">'+orders.length+' commesse · '+nVisible+
      ' con lavorazioni nell’orizzonte · clic sulla barra per i dettagli</span>'+
      '<span class="gx-expand"><button type="button" class="btn btn-light btn-small" data-action="expand-all">Apri tutto</button>'+
      '<button type="button" class="btn btn-light btn-small" data-action="collapse-all">Collassa tutto</button></span></div>'+
      '<p class="gx-legend"><span class="legend-chip"><span class="gx-key ok"></span>pianificazione regolare</span>'+
      '<span class="legend-chip"><span class="gx-key late"></span>oltre consegna</span>'+
      '<span class="legend-chip"><span class="gx-key off"></span>incompleta</span><span class="gx-sep"></span>'+
      '<span class="legend-chip"><span class="gx-key fam-cut"></span>taglio</span>'+
      '<span class="legend-chip"><span class="gx-key fam-bend"></span>piegatura</span>'+
      '<span class="legend-chip"><span class="gx-key fam-finish"></span>altre lavorazioni</span>'+
      '<span class="legend-chip"><span class="gx-key fam-ext"></span>esterno</span><span class="gx-sep"></span>'+
      '<span class="legend-chip"><span class="gx-due-key"></span>consegna</span>'+
      '<span class="gx-sep"></span><span class="legend-chip"><span class="material-dot ok static"></span>materiale disponibile</span>'+
      '<span class="legend-chip"><span class="material-dot short static"></span>materiale mancante</span>'+
      '<span class="legend-chip"><span class="material-dot mixed static"></span>materiale misto sul gruppo</span></p>'+
      '<div class="gx-scroll"><div class="gx-inner">'+ganttBands+
      '<div class="gx-head'+(state.span===1?'':' with-calendar-bands')+'"><div class="gx-corner">Commessa / fase</div><div class="gx-ticks">'+
      sc.cols.map(c=>'<span class="gx-tick'+(c.ghost?' ghost':'')+'" style="left:'+sc.at(c.s).toFixed(4)+'%">'+esc(c.label)+'</span>').join('')+'</div></div>'+
      '<div class="gx-body"><div class="gx-overlay" aria-hidden="true">'+
      sc.cols.map(c=>(c.ghost?'<span class="gx-weekend" style="left:'+sc.at(c.s).toFixed(4)+'%;width:'+(sc.at(c.e)-sc.at(c.s)).toFixed(4)+'%"></span>':
        '<span class="gx-gridline" style="left:'+sc.at(c.s).toFixed(4)+'%"></span>')).join('')+
      (Date.now()>=start&&Date.now()<end?'<span class="today-marker" style="left:'+sc.at(Date.now()).toFixed(4)+'%" title="Ora attuale"></span>':'')+'</div>';
    for (const o of orders) {
      const inf = info.get(o.order_id), as = inf.as;
      const expanded = state.expanded.has(o.order_id) || (query && as.some(a=>a.task_id.includes(query)));
      const late = o.tardiness_seconds>0;
      const dueMs = o.due?ms(o.due):null;
      let track = '';
      const gm = groupMaterial(as);
      if (inf.visible) {
        const lo = inf.first, hi = inf.last;
        const wide = sc.at(Math.min(hi,end))-sc.at(Math.max(lo,start));
        const txt = as.length+(as.length===1?' lavorazione':' lavorazioni');
        const dues = orderDues(o,as), firstDue = dues[0];
        const dueTxt = firstDue?(dues.length>1?'prima consegna ':'consegna ')+fmt(firstDue,'date'):'';
        track += '<button type="button" class="gx-bar gx-sum '+(late?'late':!o.complete?'off':'ok')+(lo<start?' cut-left':'')+(hi>end?' cut-right':'')+
          (state.selectedOrder===o.order_id?' selected':'')+(gm?' has-mat':'')+'" data-order-detail="'+esc(o.order_id)+
          '" style="'+pos(lo,hi)+'" title="'+esc(txt+'\n'+fmt(lo)+' → '+fmt(hi)+(dueTxt?'\n'+dueTxt:'')+(late?'\nRitardo '+gap(o.tardiness_seconds):'')+'\nClic: dettagli nella barra laterale')+'">'+
          (wide>20&&dueTxt?esc(txt+' · '+dueTxt):wide>7?esc(txt):'')+'</button>'+
          (gm?materialDot(gm.kind,'data-material-order="'+esc(o.order_id)+'"','left:calc('+sc.at(Math.max(lo,start)).toFixed(4)+'% + 6px)',
            'Materiale della commessa: '+gm.ok+' fasi coperte, '+gm.short+' con materiale mancante\nClic per il dettaglio di tutte le parti della commessa'):'');
      } else if (inf.first!==null) {
        track += inf.first>=end?'<button type="button" class="gx-off-chip right" data-action="goto-day" data-day="'+esc(iso(new Date(inf.first)))+'" title="Vai a questo periodo">dal '+esc(fmt(inf.first,'day'))+' →</button>':
          '<button type="button" class="gx-off-chip left" data-action="goto-day" data-day="'+esc(iso(new Date(inf.last)))+'" title="Vai a questo periodo">← fino al '+esc(fmt(inf.last,'day'))+'</button>';
      } else track += '<span class="gx-off-chip left">non pianificata</span>';
      // One red line per order-line due date (not only the order's earliest one) that falls inside the horizon.
      const lineDues = new Set(orderDues(o,as));
      if (dueMs!==null) lineDues.add(dueMs);
      for (const d of [...lineDues].sort((x,y)=>x-y)) {
        if (d<start || d>=end) continue;
        const lateLine = late && inf.last!==null && inf.last>d;
        track += '<span class="gx-due'+(lateLine?' late':'')+'" style="left:'+sc.at(d).toFixed(4)+'%" title="Consegna riga '+esc(fmt(d,'date'))+'"><i>'+esc(fmt(d,'day'))+'</i></span>';
      }
      out += '<section class="gx-group"><div class="gx-row gx-sumrow">'+
        '<button type="button" class="gx-label" data-order="'+esc(o.order_id)+'" aria-expanded="'+(expanded?'true':'false')+'" title="Modalità applicata: '+esc(appliedModeLabel(as))+' · strategia: '+esc(modeLabel(data.scenario.settings.mode))+'">'+
        '<span class="arrow">'+(expanded?'▾':'▸')+'</span><span class="gx-label-text"><span class="gx-title"><strong>'+esc(orderCode(o.order_id))+'</strong>'+
        (orderCustomer(o.order_id)?'<span class="gx-cust"> · '+esc(orderCustomer(o.order_id))+'</span>':'')+'</span>'+
        '<small>'+as.length+(as.length===1?' fase':' fasi')+' · consegna '+esc(fmt(o.due,'date'))+
        ' · calcolo: '+esc(appliedModeLabel(as))+'</small></span>'+
        '<span class="status-pill '+(late?'bad':!o.complete?'neutral':'')+'">'+(late?'+'+gap(o.tardiness_seconds):o.complete?'ok':'incompleta')+'</span></button>'+
        '<div class="gx-track">'+track+'</div></div>';
      if (expanded) {
        for (const a of as) {
          const seg = a.segments.filter(x=>ms(x.end)>start && ms(x.start)<end);
          const op = nestId(a)?null:opInfo(a.operation_ids.find(id=>(operationsById().get(id)||{}).order_id===o.order_id)||a.operation_ids[0]);
          const material = materialState(a), done = progressShare(a), lateTask = isLateTask(a);
          const tip = taskTitle(a)+'\n'+taskSubtitle(a)+(a.segments.length?'\n'+fmt(assignmentStart(a))+' → '+fmt(assignmentEnd(a)):'\nNon collocata')+
            (lateTask?'\nFine oltre la consegna':'')+(a.fixed?'\nConfermata: non spostata dal ricalcolo':'')+
            (material==='short'?'\nMateriale mancante':material==='ok'?'\nMateriale assegnato':'')+(done!==null?'\nAvanzamento '+Math.round(done*100)+'% (TRD)':'');
          out += '<div class="gx-row gx-child"><div class="gx-label child" title="'+esc(taskLabel(a))+'"><span class="gx-label-text">'+
            esc(short(nestId(a)?taskTitle(a):(op&&op.phase||taskLabel(a)),30))+
            '<small>'+esc(modeLabel(a.effective_mode))+' · '+esc(a.demands.filter(d=>!/^OP:/.test(d.resource_id)).map(d=>resourceCode(d.resource_id)).join(', '))+'</small></span></div>'+
            '<div class="gx-track">'+seg.map((x,segIndex)=>{
              const lo=ms(x.start),hi=ms(x.end);
              const small=sc.at(Math.min(hi,end))-sc.at(Math.max(lo,start))<9, spanText=hrs((hi-lo)/1000)+' · '+fmt(x.start,'time')+'–'+fmt(x.end,'time');
              return (small?'<button type="button" class="gx-note" data-task="'+esc(a.task_id)+'" style="left:'+sc.at(Math.min(hi,end)).toFixed(4)+'%" title="'+esc(tip)+'">'+esc(spanText)+'</button>':'')+'<button type="button" class="gx-bar gx-phase fam-'+phaseFamily(a)+(lateTask?' late':'')+(a.fixed?' fixed':'')+(state.selected===a.task_id?' selected':'')+(material?' has-mat':'')+
                '" data-task="'+esc(a.task_id)+'" style="'+pos(lo,hi)+(done!==null?';--done:'+(done*100).toFixed(1)+'%':'')+'" title="'+esc(tip)+'">'+
                (done!==null?'<span class="booking-progress" aria-hidden="true"></span>':'')+
                (small?'':'<span class="gx-bar-text">'+(a.fixed?'🔒 ':'')+esc(spanText)+'</span>')+'</button>'+
                // The material belongs to the task, not to each of its segments: one dot, on the first one.
                (material&&segIndex===0?materialDot(material,'data-material-task="'+esc(a.task_id)+'"','left:calc('+sc.at(Math.max(lo,start)).toFixed(4)+'% + 5px)',
                  (material==='short'?'Materiale mancante':'Materiale disponibile')+'\nClic per il dettaglio delle parti'):'');
            }).join('')+
            // Due date(s) of the order lines this phase serves, so the deadline is visible on the phase row too.
            [...new Set(a.operation_ids.map(id=>operationsById().get(id)).filter(p=>p&&p.order_id===o.order_id&&p.due).map(p=>ms(p.due)))]
              .filter(d=>Number.isFinite(d)&&d>=start&&d<end).sort((x,y)=>x-y)
              .map(d=>'<span class="gx-due'+(lateTask?' late':'')+'" style="left:'+sc.at(d).toFixed(4)+'%" title="Consegna riga '+esc(fmt(d,'date'))+'"><i>'+esc(fmt(d,'day'))+'</i></span>').join('')+
            '</div></div>';
        }
      }
      out += '</section>';
    }
    out += '</div></div></div>';
    if (!orders.length) out += emptyState('Nessuna commessa','Cambia filtro o finestra temporale.','≡');
    return out+'<p class="section-note">Sabati e domeniche sono compressi. Le righe condivise di nesting possono comparire sotto più commesse per tracciare i collegamenti, ma il carico fisico viene contato una sola volta nel piano risorse.</p></article>';
  }
  function allocationByDay(resourceId, dateStart) {
    const start=dateStart.getTime(),end=start+day;
    let total=0;
    for(const a of data.plan.assignments){
      const demands=a.demands.filter(d=>d.resource_id===resourceId);
      if(!demands.length)continue;
      for(const seg of a.segments) {
        const overlap=max(0,min(ms(seg.end),end)-max(ms(seg.start),start));
        if(overlap>0)total+=overlap/3600000*demands.reduce((s,d)=>s+number(d.units),0);
      }
    }
    const capacityScale = data.meta && data.meta.workbook && data.meta.workbook.capacity_scale || 1;
    return total / capacityScale;
  }
  function renderLoads() {
    const ds=dayRange();
    const resources=filteredResources();
    const used=resources.slice(0,state.showAll?resources.length:16);
    const saturation=loadsSaturation();
    const satRows=saturation&&saturation.rows;
    const off=(d)=>[0,6].includes(weekday(d));
    const cell=(r,d,i)=>{
      const row=satRows&&satRows.get(r.id);
      const booked=row?number(row.booked_hours[i]):allocationByDay(r.id,d);
      const avail=row&&row.available_hours?number(row.available_hours[i]):null;
      const ghost=off(d)?' ghost':'';
      if(booked<.05)return '<div class="load-cell empty'+ghost+'"></div>';
      const ratio=avail>0?booked/avail:null;
      const level=ratio===null?'supplier':ratio>1?'overload':ratio<.5?'low':'normal';
      const fill=ratio===null?100:Math.min(100,Math.max(5,ratio*100));
      const text=hrs(booked*3600),sub=ratio===null?(row?'senza cap.':''):Math.round(ratio*100)+'%';
      const title=resourceTitle(r.id)+' · '+fmt(d,'monthDayYear')+' · '+text+(ratio===null?'':', '+Math.round(ratio*100)+'% della capacità')+'. Apri il dettaglio delle lavorazioni';
      return '<button type="button" class="load-cell '+level+ghost+'" data-action="load-cell" data-resource="'+esc(r.id)+'" data-day="'+esc(iso(d))+'" title="'+esc(title)+'" aria-label="'+esc(title)+'">'+
        '<span class="load-bar" aria-hidden="true"><span class="load-bar-fill" style="width:'+fill.toFixed(0)+'%"></span></span><b>'+esc(text)+'</b><small>'+esc(sub)+'</small></button>';
    };
    const monthGroups=calendarBandGroups(ds,'month'),weekGroups=calendarBandGroups(ds,'week');
    let out='<div class="stack"><article class="panel">'+toolBar(
      '<select id="resource-kind" class="toolbar-select" aria-label="Tipo risorsa">'+
      [['machine','Macchine'],['operator','Operatori'],['all','Tutte']].map(p=>'<option value="'+p[0]+'" '+(state.resourceKind===p[0]?'selected':'')+'>'+p[1]+'</option>').join('')+'</select>'
    )+'<p class="load-legend"><span class="key low"></span> carico basso <span class="key normal"></span> entro capacità <span class="key overload"></span> oltre capacità <span class="key supplier"></span> senza capacità da calendario</p>'+
      '<div class="heatmap"><div class="heatmap-grid load-grid" style="grid-template-columns:200px '+ds.map(d=>off(d)?'46px':'minmax(96px,1fr)').join(' ')+';min-width:'+(232+ds.reduce((s,d)=>s+(off(d)?46:96),0))+'px">'+
      '<div class="heatmap-corner calendar-corner">Calendario</div>'+monthGroups.map(g=>'<div class="load-calendar-band month" style="grid-column:span '+(g.end-g.start)+'">'+esc(calendarBandLabel(g,'month'))+'</div>').join('')+
      '<div class="heatmap-corner calendar-corner"></div>'+weekGroups.map(g=>'<div class="load-calendar-band week" style="grid-column:span '+(g.end-g.start)+'">'+esc(calendarBandLabel(g,'week'))+'</div>').join('')+
      '<div class="heatmap-corner">Risorsa</div>'+
      ds.map(d=>'<div class="heatmap-day'+(off(d)?' ghost':'')+'"><span>'+esc(fmt(d,'weekday'))+'</span><b>'+esc(fmt(d,'monthDay'))+'</b></div>').join('');
    for(const r of used) {
      out+='<div class="heatmap-name"><strong>'+esc(resourceCode(r.id))+'</strong>'+(resourceName(r.id)?'<small>'+esc(resourceName(r.id))+'</small>':'')+'</div>';
      ds.forEach((d,i)=>{out+=cell(r,d,i);});
    }
    out+='</div></div><p class="section-note">'+(satRows?
      'Ogni cella mostra le <strong>ore-unità prenotate per giorno UTC</strong> e la <strong>saturazione</strong> rispetto alle ore disponibili dal calendario della risorsa (turni, festività, fermi). Clicca una cella per le lavorazioni che la compongono, poi una lavorazione per il suo dettaglio.':
      saturation&&saturation.error?'Saturazione non disponibile: '+esc(saturation.error)+'. Valori in ore-unità prenotate.':
      'Calcolo della saturazione dal calendario del motore…')+
      ' Le percentuali sull’intero orizzonte sono quelle calcolate dal motore.</p></article>';
    const shownKinds=new Set(resources.map(r=>r.id));
    const rankings=data.plan.resources.filter(r=>r.utilization!==null&&shownKinds.has(r.resource_id)).sort((a,b)=>b.utilization-a.utilization).slice(0,12);
    out+='<div class="split-grid"><article class="panel"><div class="panel-title"><h2>Utilizzo sulle risorse</h2></div><div class="mini-bars">'+rankings.map(r=>(
      '<div class="mini-bar"><span title="'+esc(resourceTitle(r.resource_id))+'">'+esc(short(resourceTitle(r.resource_id),26))+'</span>'+
      '<div class="rail"><div class="fill '+(r.utilization>.95?'high':'')+'" style="width:'+
      Math.min(100,Math.max(0,100*r.utilization)).toFixed(1)+'%"></div></div>'+
      '<strong>'+esc(pct(r.utilization))+'</strong></div>')).join('')+'</div></article>';
    const overloaded=rankings.filter(r=>r.utilization>.9);
    out+='<article class="panel"><div class="panel-title"><h2>Osservazioni di capacità</h2></div><div class="detail-list">'+
      (overloaded.length?overloaded.map(r=>'<div class="alert-row"><span class="alert-icon">↗</span><div><strong>'+esc(resourceTitle(r.resource_id))+' · '+esc(pct(r.utilization))+'</strong><p>Risorsa impegnata per oltre il 90% del proprio tempo disponibile nell’orizzonte.</p></div></div>').join(''):
      '<div class="alert-row"><span class="alert-icon">✓</span><div><strong>Nessuna risorsa oltre il 90%</strong><p>Verifica comunque i picchi giornalieri: la media sull’orizzonte non li descrive.</p></div></div>')+
      '</div></article></div></div>';
    return out;
  }
  function loadDialogHTML(spec) {
    const idx = dayRange().findIndex(d => iso(d) === spec.day);
    const sat = state.loadsCache && state.loadsCache.rows ? state.loadsCache.rows.get(spec.resource) : null;
    const first = atMidnight(new Date(spec.day+'T12:00:00Z'));
    // The loads API aggregates UTC days; the detail must use the same boundary.
    const start = sat ? ms(spec.day+'T00:00:00Z') : first.getTime();
    const end = sat ? start+24*3600000 : atMidnight(new Date(start+36*3600000)).getTime();
    const items = [];
    for (const a of data.plan.assignments) {
      const demands = a.demands.filter(d => d.resource_id === spec.resource);
      if (!demands.length) continue;
      const scale = data.meta && data.meta.workbook && data.meta.workbook.capacity_scale || 1;
      const units = demands.reduce((s, d) => s + number(d.units), 0) / scale;
      const overlap = a.segments.reduce((s, x) => s + max(0, min(ms(x.end), end) - max(ms(x.start), start)), 0);
      if (overlap > 0) items.push({a, hours: overlap/3600000*units, total: a.segments.reduce((s, x) => s + max(0, ms(x.end) - ms(x.start)), 0)/1000});
    }
    items.sort((x, y) => y.hours - x.hours);
    const sum = items.reduce((s, x) => s + x.hours, 0);
    const total = sat && idx >= 0 ? number(sat.booked_hours[idx]) : sum;
    const capacity = sat && sat.available_hours && idx >= 0 ? number(sat.available_hours[idx]) : null;
    const over = capacity !== null && capacity > 0 && total > capacity;
    const h = (v) => hrs(v*3600);
    const kind = resourcesById().get(spec.resource);
    const eyebrow = (kind && kind.kind === 'operator' ? 'OPERATORE' : kind && kind.kind === 'machine' ? 'MACCHINA' : 'RISORSA')+' · '+fmt(first,'weekday')+' '+fmt(first,'day')+(sat?' · giorno UTC':'');
    const recap = capacity === null ? 'Totale pianificato '+h(total)+' · capacità non disponibile'
      : capacity > 0 ? 'Totale pianificato '+h(total)+' · capacità '+h(capacity)+(over?' · oltre di '+h(total-capacity):'')
      : 'Totale pianificato '+h(total)+' · nessuna capacità da calendario in questo giorno';
    const body = items.length ? '<p class="mat-recap '+(over?'short':'ok')+'">'+esc(recap)+'</p><div class="mat-table-scroll"><table class="mat-table"><thead><tr><th>Lavorazione</th><th>Commesse / cliente</th><th>Finestra pianificata</th><th class="num">Ore sul giorno</th><th class="num">Ore fase</th></tr></thead><tbody>'+
      items.map(x => {
        const a = x.a;
        return '<tr class="row-link" data-task="'+esc(a.task_id)+'" tabindex="0" role="button" aria-label="'+esc(taskTitle(a)+', '+h(x.hours)+' sul giorno. Apri il dettaglio')+'">'+
          '<td>'+esc(taskTitle(a))+'<small class="cell-sub">'+esc(taskSubtitle(a))+'</small></td>'+
          '<td>'+esc(taskOrders(a).map(o => orderCode(o)+(orderCustomer(o) ? ' · '+orderCustomer(o) : '')).slice(0,3).join(' | '))+'</td>'+
          '<td>'+esc(fmt(assignmentStart(a)))+' → '+esc(fmt(assignmentEnd(a)))+(isLateTask(a) ? ' <span class="status-pill bad">Oltre consegna</span>' : '')+'</td>'+
          '<td class="num">'+esc(h(x.hours))+'</td><td class="num">'+esc(hrs(x.total))+'</td></tr>';
      }).join('')+'</tbody></table></div>' : '<p class="modal-muted">Nessuna lavorazione prenotata su questa risorsa nel giorno selezionato.</p>';
    return '<div class="modal-backdrop" id="load-dialog" data-close-load role="presentation"><aside class="modal" role="dialog" aria-modal="true" aria-label="Carico di '+esc(resourceTitle(spec.resource))+'">'+
      '<button class="close" type="button" data-close-load aria-label="Chiudi">×</button><p class="eyebrow">'+esc(eyebrow)+'</p><h2>'+esc(resourceTitle(spec.resource))+'</h2>'+
      '<p>'+items.length+(items.length === 1 ? ' lavorazione' : ' lavorazioni')+' · '+esc(h(total))+' sul giorno'+(capacity ? ' su '+esc(h(capacity))+' di capacità' : '')+'</p>'+body+'</aside></div>';
  }
  function closeLoadDialog() {
    const el = document.getElementById('load-dialog');
    if (el) el.remove();
  }
  function openLoadDialog(spec) {
    closeLoadDialog();
    document.body.insertAdjacentHTML('beforeend', loadDialogHTML(spec));
    const close = document.querySelector('#load-dialog .close');
    if (close) close.focus();
  }
  function loadsSaturation() {
    const digest=data.plan.scenario_digest,start=iso(state.anchor);
    const kind=['machine','operator'].includes(state.resourceKind)?state.resourceKind:'all';
    const key=[digest,start,state.span,kind].join('|');
    const cache=state.loadsCache;
    if(cache&&cache.key===key)return cache;
    state.loadsCache={key};
    postAnalysis('/api/loads',{scenario_digest:digest,start,days:state.span,kind}).then(result=>{
      if(state.loadsCache&&state.loadsCache.key===key){
        state.loadsCache={key,rows:new Map(result.rows.map(r=>[r.resource_id,r]))};
        if(state.view==='loads')render();
      }
    }).catch(error=>{
      if(state.loadsCache&&state.loadsCache.key===key){state.loadsCache={key,error:error.message};if(state.view==='loads')render();}
    });
    return state.loadsCache;
  }
  function renderMaterials() {
    const provenance=data.meta&&data.meta.canonical;
    const mrp=provenance&&provenance.materials;
    if(!mrp) return '<article class="panel">'+emptyState(
      'Nessuna fonte materiali nel dataset',
      'Questo Scenario non contiene disponibilità e fabbisogni MRP con provenienza verificabile. Nessuna scorta, lotto o impegno viene dedotto da altri dati. Carica un binding canonico file-only con dominio materials per abilitare questa vista.','▥')+
      '<p class="section-note">Il motore non ricava giacenze dagli screenshot, dai nomi delle macchine o dalle fasi. Nessuna quantità o data materiale viene inventata.</p></article>';
    const pegs=Array.isArray(mrp.pegs)?mrp.pegs:[];
    const shortage=Array.isArray(mrp.shortages)?mrp.shortages:[];
    const items=new Set([...pegs.map(p=>p.item_id),...shortage.map(x=>x.item_id)]);
    const units=new Set([...pegs.map(p=>p.unit),...shortage.map(x=>x.unit)]);
    let out='<div class="insight-grid" style="margin-bottom:14px">'+
      '<article class="insight-card"><span class="insight-icon">▥</span><h3>Lotti assegnati</h3><p><strong>'+pegs.length+
      '</strong> legami lotto → unità dal pegging. Articoli distinti: <strong>'+items.size+'</strong>.</p></article>'+
      '<article class="insight-card"><span class="insight-icon">⚠</span><h3>Carenze dichiarate</h3><p><strong>'+shortage.length+
      '</strong> righe senza copertura. Unità coinvolte in assegnazioni/carenze: <strong>'+units.size+'</strong>.</p></article></div>'+
      '<div class="info-panel" style="margin-bottom:14px"><strong>Fotografia di origine: '+esc(provenance.source_manifest_digest?
        short(provenance.source_manifest_digest,15):'manifest canonico')+'</strong>. '+
      'Politica di assegnazione: <strong>'+esc(mrp.allocation_order||'non dichiarata')+'</strong>. '+
      'I dati provengono dal manifest del binding file; non sono uno stock live. '+
      'In caso di what-if, descrivono la fonte originaria e non sono ricalcolati dal browser.</div>'+
      '<div class="split-grid"><article class="panel"><div class="panel-title"><div><h2>Assegnazioni dei lotti</h2>'+
      '<p class="panel-sub">'+pegs.length+' impegni materiale dal pegging canonico</p></div></div>'+
      '<div class="table-scroll"><table class="data-table"><thead><tr><th>Unità del piano</th><th>Articolo</th><th>Lotto / fornitura</th><th>Quantità</th></tr></thead><tbody>'+
      (pegs.length?pegs.map(row=>'<tr><td><strong>'+esc(row.unit)+'</strong></td><td>'+esc(row.item_id)+'</td>'+
       '<td>'+esc(row.supply_id)+'</td><td>'+esc(row.quantity)+'</td></tr>').join(''):
       '<tr><td colspan="4">Nessun lotto assegnato nella fonte.</td></tr>')+
      '</tbody></table></div></article><article class="panel"><div class="panel-title"><div><h2>Fabbisogni scoperti</h2>'+
      '<p class="panel-sub">Eventuali mancanze impediscono la disponibilità della lavorazione.</p></div></div>'+
      '<div class="table-scroll"><table class="data-table"><thead><tr><th>Unità</th><th>Articolo</th><th>Mancante</th></tr></thead><tbody>'+
      (shortage.length?shortage.map(row=>'<tr><td><strong>'+esc(row.unit)+'</strong></td><td>'+esc(row.item_id)+'</td>'+
       '<td><span class="status-pill bad">'+esc(row.missing)+'</span></td></tr>').join(''):
       '<tr><td colspan="3">Nessuna carenza dichiarata dal manifest.</td></tr>')+
      '</tbody></table></div></article></div>';
    return out;
  }
  function renderProgress() {
    const origin=data.meta&&data.meta.canonical;
    const progress=origin&&origin.progress;
    const rows=progress&&Array.isArray(progress.rows)?progress.rows:[];
    if(!rows.length) return '<article class="panel">'+emptyState(
      'Nessun consuntivo attestato nel file',
      'La fonte attuale non contiene righe operation_progress verificabili. Le durate dello Scenario rappresentano lavoro ancora da pianificare, non dimostrano quando, dove o quanto lavoro sia stato eseguito.','◷')+
      '<p class="section-note">Per vedere consuntivi reali usa un binding canonico CSV con dominio progress e residual_policy dichiarata. Questa vista non stabilisce che una macchina sia attualmente occupata.</p></article>';
    const started=rows.filter(r=>r.status==='started').length;
    const completed=rows.filter(r=>r.status==='completed').length;
    const unmatched=rows.filter(r=>!r.canonical_id_matched).length;
    const report='<div class="insight-grid" style="margin-bottom:14px">'+
      '<article class="insight-card"><span class="insight-icon">◷</span><h3>Avanzamenti dichiarati</h3><p><strong>'+
      started+'</strong> fasi avviate · <strong>'+completed+'</strong> fasi chiuse, nelle sole fonti associate.</p></article>'+
      '<article class="insight-card"><span class="insight-icon">◇</span><h3>Corrispondenza fase</h3><p><strong>'+
      (rows.length-unmatched)+'</strong> righe con ID esattamente uguale al modello; <strong>'+
      unmatched+'</strong> senza collegamento certo. Nessuna mappatura ipotizzata.</p></article></div>';
    let out=report+'<div class="info-panel" style="margin-bottom:14px"><strong>Consuntivi ≠ stato attuale macchina.</strong> '+
      'Le ore lavorate e la risorsa dichiarata sono registrazioni dalla fonte, non prove che il macchinario sia ancora occupato ora. '+
      'Il residuo è mostrato solo quando l’identificativo corrisponde esattamente allo Scenario canonico. '+
      (progress.truncated?'Elenco limitato a '+rows.length+' di '+progress.total_rows+' registrazioni.':'')+
      '</div><article class="panel"><div class="panel-title"><div><h2>Fasi con avanzamento registrato</h2>'+
      '<p class="panel-sub">'+esc(progress.total_rows)+' consuntivi sorgente · nessun accesso ERP live</p></div></div>'+
      '<div class="table-scroll"><table class="data-table"><thead><tr>'+
      '<th>Fase sorgente</th><th>Stato</th><th>Lavoro registrato</th><th>Setup registrato</th>'+
      '<th>Run residuo</th><th>Setup residuo</th><th>Risorsa dichiarata</th><th>Fonte</th>'+
      '</tr></thead><tbody>';
    for(const row of rows) {
      const status=row.status==='completed'?'Chiusa':row.status==='started'?'Avviata':String(row.status||'Non specificato');
      const fmtSeconds=(value)=>value===null||value===undefined?'Non attestato':hrs(value);
      out+='<tr><td><strong>'+esc(row.operation_id)+'</strong>'+
        (row.canonical_id_matched?'':' <span class="status-pill neutral">ID non riconciliato</span>')+'</td>'+
        '<td><span class="status-pill '+(row.status==='completed'?'':'neutral')+'">'+esc(status)+'</span></td>'+
        '<td>'+esc(fmtSeconds(row.recorded_work_seconds))+'</td>'+
        '<td>'+esc(fmtSeconds(row.recorded_setup_seconds))+'</td>'+
        '<td>'+esc(fmtSeconds(row.planned_remaining_run_seconds))+'</td>'+
        '<td>'+esc(fmtSeconds(row.planned_remaining_setup_seconds))+'</td>'+
        '<td>'+esc(row.declared_resource_id||'Non attestata')+'</td>'+
        '<td>'+esc(row.source)+'</td></tr>';
    }
    out+='</tbody></table></div>'+
      '<p class="section-note">La politica residuale e i controlli di coerenza sono applicati dal builder canonico. Questo elenco non rappresenta una validazione MES dello stato as-of, delle prenotazioni congelate né delle quantità materiali residue.</p></article>';
    return out;
  }
  function renderStagedTimeline(result) {
    const bookings=result.stage_bookings||[];
    const resources=new Map(data.scenario.resources.map(r=>[r.id,r.kind]));
    const weight=new Map();
    for(const b of bookings)for(const a of b.allocations||[])
      weight.set(a.resource_id,(weight.get(a.resource_id)||0)+1);
    const ordered=[...weight].sort((a,b)=>
      (resources.get(a[0])==='machine'?0:resources.get(a[0])==='operator'?1:2)-
      (resources.get(b[0])==='machine'?0:resources.get(b[0])==='operator'?1:2) ||
      b[1]-a[1] || a[0].localeCompare(b[0])).map(r=>r[0]);
    const shown=ordered.slice(0,18),[first,last]=viewWindow(),dates=dayRange();
    let out='<article class="panel" style="margin-bottom:14px">'+
      toolBar('<span class="status-pill neutral">Setup + run · risorse condivise</span>')+
      '<div class="timeline-scroll"><div class="timeline-layout">'+
      '<div class="timeline-header"><div class="timeline-corner">Risorsa / stage</div>'+
      '<div class="day-grid">'+dates.map(d=>
        '<div class="day-col"><span>'+esc(fmt(d,'weekday'))+'</span><small>'+esc(fmt(d,'monthDay'))+'</small></div>').join('')+
      '</div></div>';
    for(const rid of shown){
      const kind=resources.get(rid)||'resource';
      out+='<div class="machine-row"><div class="machine-label"><strong>'+
        esc(short(rid,24))+'</strong><small>'+esc(kind)+
        ' · occupazione dichiarata</small></div>'+
        '<div class="timeline-track stage-track" style="--day-width:calc(100% / '+state.span+')">';
      for(const b of bookings) {
        const allocation=(b.allocations||[]).find(a=>a.resource_id===rid);
        if(!allocation)continue;
        for(const segment of b.segments||[]){
          const start=Math.max(first,ms(segment.start));
          const end=Math.min(last,ms(segment.end));
          if(!Number.isFinite(start)||!Number.isFinite(end)||end<=start)continue;
          const left=(100*(start-first)/(last-first)).toFixed(5);
          const width=(100*(end-start)/(last-first)).toFixed(5);
          const percentage=(number(allocation.occupancy_bps)/100).toLocaleString('it-IT',{maximumFractionDigits:2});
          const label=b.stage==='setup'?'Attrezzaggio':'Lavorazione';
          out+='<span class="stage-block '+(b.stage==='setup'?'is-setup':'is-run')+
            '" style="left:'+left+'%;width:'+width+'%" title="'+
            esc(b.phase_id+' · '+label+' · '+percentage+'% · '+
              fmt(segment.start)+' → '+fmt(segment.end))+'">'+
            '<strong>'+esc(short(b.phase_id,19))+'</strong>'+
            '<small>'+esc(label)+' · '+percentage+'%</small></span>';
        }
      }
      out+='</div></div>';
    }
    out+='</div></div>'+
      '<div class="timeline-legend">'+
      '<span class="legend-chip"><span class="stage-key setup-key"></span>Setup effettivo</span>'+
      '<span class="legend-chip"><span class="stage-key run-key"></span>Run effettivo</span>'+
      '<span class="legend-chip">Quote percentuali per singola risorsa (bps / 100)</span></div>'+
      (ordered.length>shown.length?
        '<p class="section-note">Sono visibili '+shown.length+' di '+ordered.length+
        ' risorse, scelte per categoria e numero di prenotazioni. Il dettaglio completo è nella tabella seguente.</p>':
        '')+'</article>';
    return out;
  }
  function renderStaged() {
    if(!data.meta||!data.meta.v03_available) return '<article class="panel">'+emptyState(
      'Contratto setup/run v0.3 non caricato',
      'L’anteprima avanzata richiede un file --v03-spec con digest esatto dello Scenario, setup per fase, sequenza macchina e convenzione scorte attestata. Questi fatti non vengono dedotti dallo Scenario.','▣')+
      '<p class="section-note">Usare planning-factory web --fixture scenario.json --v03-spec stage-spec.json in ambiente privato. Nessuna inferenza dallo storico o da immagini di un altro cliente.</p></article>';
    if(hasWhatif()) return '<article class="panel">'+emptyState(
      'Ripristina prima lo Scenario di origine',
      'L’anteprima setup/run avanzata è calcolata soltanto sullo Scenario sorgente il cui digest coincide col contratto v0.3. Annulla i vincoli what-if per evitare di confrontare revisioni non equivalenti.','▣')+'</article>';
    if(state.stagedBusy) return '<article class="panel">'+emptyState(
      'Verifica tecnica in elaborazione',
      'Sto usando la pipeline materiali → setup/run → audit del file esplicitamente selezionato. Nessun ERP viene contattato.','▣')+'</article>';
    if(state.stagedError) return '<article class="panel">'+emptyState(
      'Il contratto v0.3 non è stato accettato',
      state.stagedError,'⚠')+
      '<div class="filters-line"><button type="button" class="btn btn-light" data-action="load-stage">Riprova la verifica tecnica</button></div></article>';
    const result=state.stagedResult;
    if(!result)return '<article class="panel">'+
      '<div class="panel-title"><h2>Pipeline avanzata disponibile</h2><span class="status-pill neutral">Solo file · opt-in</span></div>'+
      '<div class="info-panel" style="margin:16px">Questa funzione usa dati di macchina, stock, setup e consuntivi forniti esplicitamente. '+
      'Non li costruisce se mancano. Il calcolo non pubblica risultati ERP.</div>'+
      '<div class="parameter-footer"><span class="panel-sub">Il risultato sarà un piano tecnico separato da quello standard.</span>'+
      '<button type="button" class="btn btn-primary" data-action="load-stage">Calcola e verifica setup/run v0.3</button></div></article>';
    const all=result.stage_bookings||[];
    const setups=all.filter(x=>x.stage==='setup');
    const runs=all.filter(x=>x.stage==='run');
    const bookings=all.slice(0,250);
    let out='<div class="insight-grid" style="margin-bottom:14px">'+
      '<article class="insight-card"><span class="insight-icon">⚙</span><h3>Attrezzaggi</h3><p><strong>'+setups.length+
      '</strong> prenotazioni setup in questo calcolo tecnico.</p></article>'+
      '<article class="insight-card"><span class="insight-icon">▣</span><h3>Lavorazioni</h3><p><strong>'+runs.length+
      '</strong> prenotazioni run. Pegging: <strong>'+number(result.material_pegs&&result.material_pegs.length)+'</strong> righe.</p></article></div>'+
      '<div class="info-panel" style="margin-bottom:14px"><strong>Verifiche indipendenti eseguite nel motore.</strong> '+
      'Convenzione scorte: '+esc(result.stock_accounting==='net'?'scorte nette':'scorte lorde con impegni espliciti')+
      '. Modalità: '+esc(result.mode)+'. Digest sorgente: '+esc(short(result.source_scenario_digest,18))+
      '. <strong>Questo NON è un piano autorizzato per Factory.</strong> '+ 
      (result.partial_plan?'<strong>Attenzione: '+number(result.unscheduled_count)+' task non collocati.</strong>':'')+
      '</div>'+
      renderStagedTimeline(result)+
      '<article class="panel"><div class="panel-title"><h2>Sequenza setup e run per risorsa</h2>'+
      '<span class="status-pill neutral">'+bookings.length+' di '+all.length+' stage mostrati</span></div>'+
      '<div class="table-scroll"><table class="data-table"><thead><tr><th>Fase</th><th>Stage</th><th>Inizio UTC</th><th>Fine UTC</th>'+
      '<th>Tempo prenotato</th><th>Risorse e occupazione</th></tr></thead><tbody>';
    for(const bk of bookings){
      const segments=bk.segments||[];
      const seconds=segments.reduce((n,seg)=>n+Math.max(0,(ms(seg.end)-ms(seg.start))/1000),0);
      const resources=(bk.allocations||[]).map(r=>r.resource_id+' '+(number(r.occupancy_bps)/100).toLocaleString('it-IT',{maximumFractionDigits:2})+'%').join(', ');
      out+='<tr><td><strong>'+esc(bk.phase_id)+'</strong></td><td><span class="status-pill '+(bk.stage==='setup'?'neutral':'')+'">'+esc(bk.stage)+'</span></td>'+
        '<td>'+esc(fmt(segments.length?segments[0].start:null))+'</td>'+
        '<td>'+esc(fmt(segments.length?segments[segments.length-1].end:null))+'</td>'+
        '<td>'+esc(hrs(seconds))+'</td><td>'+esc(resources)+'</td></tr>';
    }
    out+='</tbody></table></div>'+
      '<p class="section-note">'+esc(result.warning||'Anteprima tecnica non pubblicabile.')+
      ' Gli eventuali setup/segmenti multipli sono quelli restituiti dal motore, non stime ricavate dalla UI.</p></article>';
    return out;
  }
  async function loadStaged() {
    if(state.stagedBusy || hasWhatif())return;
    state.stagedBusy=true;state.stagedError=null;render();
    try{
      const reply=await fetch('/api/v03-preview',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:'{}',cache:'no-store'
      });
      const output=await reply.json();
      if(!reply.ok)throw new Error(output.error||'Anteprima avanzata non riuscita');
      if(output.technical_only!==true||output.audited!==true||!output.stage_bookings)
        throw new Error('La pipeline non ha fornito un risultato tecnico verificato');
      state.stagedResult=output;
      setToast('Anteprima v0.3 calcolata: non pubblicabile su Factory.');
    }catch(error){
      state.stagedError=String(error.message).slice(0,750);
      state.stagedResult=null;
    }finally{
      state.stagedBusy=false;render();
    }
  }
  const CAUSES={
    capacity:['Capacità satura','Una risorsa occupata da altre fasi ritarda la commessa','◫'],
    expired:['Consegna già scaduta','La data di consegna in Factory è precedente all’inizio del piano','⌛'],
    release:['Rilascio / materiale','La prima fase non può partire prima della data di rilascio o di arrivo del materiale','▥'],
    lead_time:['Ciclo lungo','Le fasi in sequenza dall’inizio del piano non stanno nei tempi','⇢'],
    subcontract:['Conto lavoro','Vale la data di rientro dichiarata dal fornitore','⇄'],
    unplaced:['Non collocata','Almeno una fase non trova spazio (materiale, calendario o vincoli)','×'],
    other:['Da classificare','Il percorso critico non indica un vincolo prevalente','?']
  };
  function delaysAnalysis() {
    const digest=data.plan.scenario_digest,cache=state.delays;
    if(cache&&cache.digest===digest)return cache;
    state.delays={digest,pending:true};
    postAnalysis('/api/delays',{scenario_digest:digest}).then(result=>{
      if(state.delays&&state.delays.digest===digest){state.delays={digest,result};if(state.view==='deliveries'||state.view==='bottlenecks')render();}
    }).catch(error=>{
      if(state.delays&&state.delays.digest===digest){state.delays={digest,error:error.message};if(state.view==='deliveries'||state.view==='bottlenecks')render();}
    });
    return state.delays;
  }
  const gap=(seconds)=>{
    if(seconds===null||seconds===undefined)return '—';
    const abs=Math.abs(seconds),sign=seconds>0?'+':seconds<0?'−':'';
    return sign+(abs>=86400?Math.round(abs/86400).toLocaleString('it-IT')+' g':Math.round(abs/3600).toLocaleString('it-IT')+' h');
  };
  function causeText(row) {
    const blocker=row.blocker&&data.plan.assignments.find(a=>a.task_id===row.blocker);
    if(row.cause==='capacity')return 'Attende <strong>'+esc(row.resource?resourceTitle(row.resource):'una risorsa')+'</strong>'+
      (row.wait_seconds?' · '+esc(hrs(row.wait_seconds))+' di coda':'')+(blocker?' dopo '+esc(taskTitle(blocker)):'');
    if(row.cause==='unplaced')return 'Fase non collocata <strong>'+esc(row.unit||'')+'</strong>'+(row.detail?' · '+esc(row.detail):'');
    if(row.cause==='expired')return 'Consegna del '+esc(fmt(row.due,'date'))+', già passata all’inizio del piano';
    return esc(CAUSES[row.cause]?CAUSES[row.cause][1]:'Vincolo non determinato');
  }
  function renderDeliveries() {
    const analysis=delaysAnalysis();
    if(!analysis.result)return '<article class="panel">'+(analysis.error?emptyState('Analisi dei ritardi non disponibile',analysis.error,'⚠'):
      '<div class="skeleton"><span></span><span></span><p>Ricostruzione delle cause dal percorso critico…</p></div>')+'</article>'+
      (analysis.error?renderDeliveriesBasic():'');
    const r=analysis.result,filter=state.delayCause,who=state.delayCustomer;
    const rows=r.rows.filter(x=>(!filter||x.cause===filter)&&(!who||orderCustomerCode(x.order_id)===who));
    const lanes=Object.keys(CAUSES).filter(k=>r.counts[k]).map(k=>
      '<button type="button" class="cause-lane cause-'+k+(filter===k?' active':'')+'" data-action="delay-cause" data-cause="'+k+'" aria-pressed="'+(filter===k)+'">'+
      '<span class="cause-icon" aria-hidden="true">'+CAUSES[k][2]+'</span><span><strong>'+r.counts[k]+'</strong> '+esc(CAUSES[k][0])+'</span></button>').join('');
    const status=(x)=>x.status==='late'?'<span class="status-pill bad">In ritardo</span>':x.status==='unplaced'?'<span class="status-pill bad">Non collocata</span>':'<span class="status-pill warn">A rischio</span>';
    let out='<div class="deliveries-grid"><div class="stack"><article class="panel"><div class="panel-title"><div><h2>Consegne in ritardo o a rischio</h2>'+
      '<p class="panel-sub">'+(filter||who?rows.length+' di ':'')+r.total+' commesse · causa dal percorso critico del piano verificato · a rischio = margine sotto 1 giorno'+
      (who?' · cliente <strong>'+esc(customerName(who)||who)+'</strong>':'')+'</p></div>'+
      (filter?'<button type="button" class="btn btn-light btn-small" data-action="delay-cause" data-cause="">Mostra tutte le cause</button>':'')+
      (who?'<button type="button" class="btn btn-light btn-small" data-action="delay-customer" data-customer="">Tutti i clienti</button>':'')+'</div>'+
      '<div class="cause-lanes">'+lanes+'</div>'+
      '<div class="table-scroll"><table class="data-table deliveries-table"><thead><tr><th>Commessa</th><th>Cliente</th><th>Consegna</th><th>Fine prevista</th><th>Scarto</th><th>Vincolo principale</th><th>Stato</th></tr></thead><tbody>'+
      rows.slice(0,150).map(x=>{
        const unit=x.unit&&data.plan.assignments.find(a=>a.task_id===x.unit);
        const ref=(label('orders',x.order_id)||{}).description||(label('orders',x.order_id)||{}).reference||'';
        return '<tr><td>'+(unit?'<button type="button" class="link-button" data-task="'+esc(unit.task_id)+'"><strong>'+esc(orderCode(x.order_id))+'</strong></button>':'<strong>'+esc(orderCode(x.order_id))+'</strong>')+
          (ref?'<small class="cell-sub">'+esc(short(ref,34))+'</small>':'')+'</td>'+
          '<td>'+esc(orderCustomer(x.order_id)||'—')+'</td><td>'+esc(fmt(x.due,'date'))+'</td><td>'+esc(x.end?fmt(x.end):'—')+'</td>'+
          '<td><span class="gap '+(x.status==='risk'?'risk':'late')+'">'+esc(gap(x.status==='risk'?-x.slack_seconds:x.tardiness_seconds))+'</span></td>'+
          '<td class="cause-cell"><span class="cause-tag cause-'+x.cause+'">'+esc(CAUSES[x.cause][0])+'</span> '+causeText(x)+'</td><td>'+status(x)+'</td></tr>';
      }).join('')+'</tbody></table></div>'+
      (rows.length>150?'<p class="section-note">Mostrate le prime 150 di '+rows.length+' commesse.</p>':'')+
      '<p class="section-note">'+esc(r.method)+'. La causa è il primo vincolo che decide la data: non è una stima della UI.</p></article>'+
      customersPanel()+'</div>'+dataChecks()+'</div>';
    return out;
  }
  // Orders of the plan grouped by customer: the same tardiness the KPIs sum, nothing estimated.
  function customersPanel() {
    const start=ms(data.scenario.start),groups=new Map();
    for(const o of data.plan.orders){
      const code=orderCustomerCode(o.order_id);
      if(!groups.has(code))groups.set(code,{code,orders:0,late:0,expired:0,incomplete:0,tardiness:0,worst:null});
      const g=groups.get(code),late=number(o.tardiness_seconds);
      g.orders++;if(!o.complete)g.incomplete++;
      if(late>0){g.late++;g.tardiness+=late;if(o.due&&ms(o.due)<=start)g.expired++;
        if(!g.worst||late>number(g.worst.tardiness_seconds))g.worst=o;}
    }
    if(groups.size===1&&groups.has(null))return '';
    const rows=[...groups.values()].sort((x,y)=>y.tardiness-x.tardiness||y.late-x.late||y.incomplete-x.incomplete||
      String(customerName(x.code)||x.code||'~').localeCompare(String(customerName(y.code)||y.code||'~')));
    const shown=state.showAllCustomers?rows:rows.slice(0,12);
    return '<article class="panel"><div class="panel-title"><div><h2>Clienti</h2><p class="panel-sub">'+rows.length+
      ' clienti · ordinati per ritardo totale · clic per filtrare le consegne</p></div></div>'+
      '<div class="table-scroll"><table class="data-table deliveries-table"><thead><tr><th>Cliente</th><th>Commesse</th><th>In ritardo</th><th>Incomplete</th><th>Ritardo totale</th><th>Più in ritardo</th></tr></thead><tbody>'+
      shown.map(g=>'<tr'+(state.delayCustomer===g.code&&g.code?' class="selected"':'')+'><td>'+
        (g.code?'<button type="button" class="link-button" data-action="delay-customer" data-customer="'+esc(g.code)+'"><strong>'+esc(customerName(g.code)||'Cliente '+g.code)+'</strong></button><small class="cell-sub">'+esc(g.code)+'</small>':
          '<span class="muted-line">Cliente non indicato nella fonte</span>')+'</td>'+
        '<td>'+g.orders+'</td><td>'+(g.late?'<span class="gap late">'+g.late+'</span>'+(g.expired?'<small class="cell-sub">'+g.expired+' già scadute a inizio piano</small>':''):'0')+'</td>'+
        '<td>'+(g.incomplete||'0')+'</td><td>'+(g.tardiness?esc(gap(g.tardiness)):'—')+'</td>'+
        '<td>'+(g.worst?esc(orderCode(g.worst.order_id))+'<small class="cell-sub">'+esc(gap(g.worst.tardiness_seconds))+'</small>':'—')+'</td></tr>').join('')+
      '</tbody></table></div>'+
      (rows.length>shown.length?'<div class="filters-line"><button class="btn btn-light btn-small" type="button" data-action="all-customers">Mostra tutti i '+rows.length+' clienti</button></div>':'')+
      '<p class="section-note">Ritardo totale = somma dei ritardi delle commesse del cliente nel piano verificato. Le commesse incomplete hanno fasi non collocate: il loro ritardo non è calcolabile.</p></article>';
  }
  const shortageText=(detail)=>{const m=/^(\S+) missing ([\d.]+)/.exec(detail||'');return m?'mancano '+m[2]+' di '+m[1]:short(detail||'',60);};
  const ISSUE_TEXT={due_in_past:'Consegna precedente all’inizio del piano',due_before_release:'Consegna precedente al rilascio',
    extracted_in_future:'Data di estrazione successiva al calcolo',material_shortage:'Materiale mancante',not_nested:'Fase senza programma di nesting',
    nest_excluded:'Programma di nesting escluso',supply_without_date:'Disponibilità senza data',release_after_horizon:'Rilascio oltre l’orizzonte'};
  function dataChecks() {
    const canonical=data.meta&&data.meta.canonical||{};
    const shortages=(canonical.issues||[]).filter(i=>i.code==='material_shortage');
    const other=(canonical.issues||[]).filter(i=>i.code!=='material_shortage');
    const progress=canonical.progress, unmatched=progress&&progress.rows?progress.rows.filter(x=>!x.canonical_id_matched).length:0;
    const expired=data.plan.orders.filter(o=>o.due&&ms(o.due)<=ms(data.scenario.start)).length;
    const group=(icon,title,count,items)=>'<section class="check-group"><h3><span aria-hidden="true">'+icon+'</span> '+esc(title)+'<span class="count">'+count+'</span></h3>'+items+'</section>';
    const item=(sym,html)=>'<div class="check-row"><span class="check-icon">'+sym+'</span><div>'+html+'</div></div>';
    return '<article class="panel checks-panel"><div class="panel-title"><h2>Dati mancanti o da verificare</h2><span class="panel-sub">controlli al ricalcolo</span></div>'+
      group('▥','Materiale',shortages.length,shortages.slice(0,6).map(i=>{
        const a=data.plan.assignments.find(x=>x.task_id===(i.units||[])[0]);
        const who=(i.units||[]).map(u=>{const t=data.plan.unscheduled.find(x=>x.task_id===u)||data.plan.assignments.find(x=>x.task_id===u);
          return t?taskTitle({...t,segments:t.segments||[]}):u;}).join(', ');
        const m=/^(\S+) missing ([\d.]+)/.exec(i.detail||'');
        return item('!','<strong>'+esc(who)+'</strong> · '+(m?'mancano '+esc(m[2])+' di <strong>'+esc(m[1])+'</strong>: non pianificabile finché il materiale non arriva':esc(short(i.detail,110))));
      }).join('')||item('✓','Nessuna carenza dichiarata dalla fonte materiali.'))+
      group('⌛','Consegne già scadute in Factory',expired,expired?item('?',expired+' commesse hanno la consegna prima dell’inizio del piano ('+esc(fmt(data.scenario.start,'day'))+'): risultano in ritardo per definizione. Aggiornare le date in Factory o chiudere le commesse evase.'):item('✓','Nessuna.'))+
      group('◷','Avanzamenti TRD',unmatched,progress?item(unmatched?'!':'✓',(progress.total_rows||0)+' fasi con consuntivo'+(unmatched?', '+unmatched+' senza corrispondenza esatta nello scenario':', tutte collegate alle fasi del piano')+'.'):item('?','Nessuna fonte di avanzamento collegata.'))+
      (other.length?group('⚠','Altri controlli della fonte',other.length,Object.entries(other.reduce((m,i)=>{m[i.code]=(m[i.code]||0)+1;return m;},{}))
        .sort((x,y)=>y[1]-x[1]).map(([code,count])=>item(count>99?'99+':String(count),'<strong>'+esc(ISSUE_TEXT[code]||code)+'</strong>'+
          '<small class="cell-sub">'+esc(short((other.find(i=>i.code===code)||{}).detail||'',90))+'</small>')).join('')):'')+
      '</article>';
  }
  function renderDeliveriesBasic() {
    const orders=[...data.plan.orders].sort((a,b)=>number(b.tardiness_seconds)-number(a.tardiness_seconds));
    const late=orders.filter(o=>o.tardiness_seconds>0);
    let out='<div class="split-grid"><article class="panel"><div class="panel-title"><div><h2>Consegne e fine prevista</h2><p class="panel-sub">'+orders.length+' commesse nell’orizzonte</p></div>'+
      '<span class="status-pill '+(late.length?'bad':'')+'">'+late.length+' in ritardo</span></div><div class="table-scroll"><table class="data-table">'+
      '<thead><tr><th>Commessa</th><th>Consegna richiesta</th><th>Fine prevista</th><th>Stato</th></tr></thead><tbody>';
    for(const o of orders) {
      const status=o.tardiness_seconds>0?'<span class="status-pill bad">+'+hrs(o.tardiness_seconds)+'</span>':
        o.complete?'<span class="status-pill">Nei termini</span>':'<span class="status-pill neutral">Non completata</span>';
      out+='<tr><td><strong>'+esc(orderCode(o.order_id))+'</strong>'+(orderCustomer(o.order_id)?'<small class="cell-sub">'+esc(orderCustomer(o.order_id))+'</small>':'')+'</td><td>'+esc(fmt(o.due))+'</td><td>'+esc(fmt(o.end))+'</td><td>'+status+'</td></tr>';
    }
    out+='</tbody></table></div></article><div class="stack"><article class="panel"><div class="panel-title"><h2>Avvisi prioritari</h2></div><div class="detail-list">';
    if(late.length) out+=late.slice(0,8).map(o=>'<div class="alert-row"><span class="alert-icon">!</span><div><strong>'+esc([orderCode(o.order_id),orderCustomer(o.order_id)].filter(Boolean).join(' · '))+'</strong><p>Ritardo calcolato: '+esc(hrs(o.tardiness_seconds))+'. Fine '+esc(fmt(o.end))+'</p></div></div>').join('');
    if(data.plan.unscheduled.length)out+=data.plan.unscheduled.slice(0,8).map(u=>'<div class="alert-row"><span class="alert-icon">×</span><div><strong>'+esc(u.task_id)+'</strong><p>'+esc(u.code+': '+u.detail)+'</p></div></div>').join('');
    if(!late.length&&!data.plan.unscheduled.length)out+='<div class="alert-row"><span class="alert-icon">✓</span><div><strong>Nessuna criticità segnalata</strong><p>Il piano è stato verificato, ma l’esito non costituisce una promessa di consegna.</p></div></div>';
    return out+'</div></article><div class="info-panel">Gli avvisi si basano esclusivamente su consegne e vincoli del file attuale. Non rappresentano notifiche live del gestionale.</div></div></div>';
  }
  function renderCalendars() {
    const calendars=new Map(data.scenario.calendars.map(c=>[c.id,c]));
    const ds=dayRange().slice(0,14);
    const monthGroups=calendarBandGroups(ds,'month'),weekGroups=calendarBandGroups(ds,'week');
    const filtered=filteredResources().slice(0,20);
    let out='<article class="panel">'+toolBar()+
      '<div class="heatmap"><div class="heatmap-grid" style="--days:'+ds.length+'">'+
      '<div class="heatmap-corner calendar-corner">Calendario</div>'+monthGroups.map(g=>'<div class="calendar-band-cell month" style="grid-column:span '+(g.end-g.start)+'">'+esc(calendarBandLabel(g,'month'))+'</div>').join('')+
      '<div class="heatmap-corner calendar-corner"></div>'+weekGroups.map(g=>'<div class="calendar-band-cell week" style="grid-column:span '+(g.end-g.start)+'">'+esc(calendarBandLabel(g,'week'))+'</div>').join('')+
      '<div class="heatmap-corner">Risorsa</div>'+ds.map(d=>'<div class="heatmap-day">'+esc(fmt(d,'monthDay'))+'</div>').join('');
    for(const r of filtered) {
      const c=calendars.get(r.calendar_id);
      out+='<div class="heatmap-name">'+esc(short(r.id,24))+'</div>';
      for(const d of ds) {
        if(!c){out+='<div class="heat-cell">?</div>';continue;}
        const parts=new Intl.DateTimeFormat('en-GB',{timeZone:c.timezone,year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(d);
        const part=(name)=>parts.find(x=>x.type===name).value;
        const localDate=part('year')+'-'+part('month')+'-'+part('day');
        const local=new Date(localDate+'T12:00:00Z');
        const wd=(local.getUTCDay()+6)%7;
        const ex=(c.exceptions||[]).find(e=>e.day===localDate);
        const holiday=(c.holidays||[]).includes(localDate);
        const shifts=holiday?[]:ex?ex.shifts:(c.shifts||[]).filter(s=>s.weekday===wd).map(s=>[s.start,s.end]);
        const label=holiday?'Festivo':!shifts.length?'Riposo':shifts.map(a=>a.join('–')).join(', ');
        out+='<div class="heat-cell '+(holiday?'l5':shifts.length?'l2':'')+'" title="'+esc(label)+'">'+(holiday?'F':shifts.length?'●':'·')+'</div>';
      }
    }
    out+='</div></div><div class="timeline-legend"><span class="legend-chip"><span class="legend-square"></span>Turno presente</span><span class="legend-chip"><span class="legend-square grey"></span>Riposo</span><span class="legend-chip"><span class="legend-square red"></span>Festivo</span></div>'+
      '<p class="section-note">Questa vista riepiloga turni settimanali, eccezioni e festività. Le chiusure assolute incidono sul motore e sull’audit ma non sono tradotte in ore qui: nessuna disponibilità viene inventata.</p></article>';
    const baseDate=iso(state.anchor);
    const resourceOptions=data.scenario.resources.map(r=>'<option value="'+esc(r.id)+'">'+esc(r.id)+'</option>').join('');
    out+='<article class="panel" style="margin-top:14px"><div class="panel-title"><div><h2>Simula un fermo risorsa</h2>'+
      '<p class="panel-sub">Nuova indisponibilità solo in memoria: il motore ripianificherà i task e li ri-verificherà.</p></div>'+
      '<span class="status-pill neutral">'+state.edits.closures.length+' fermi simulati</span></div>'+
      '<form id="closure-form"><div class="field-grid"><div class="field"><label for="closure-resource">Risorsa</label>'+
      '<select class="field-select" id="closure-resource" name="resource_id" required>'+resourceOptions+'</select></div>'+
      '<div class="field"><label for="closure-start">Inizio (UTC)</label>'+
      '<input class="field-input" type="datetime-local" id="closure-start" name="start" required value="'+baseDate+'T08:00"></div>'+
      '<div class="field"><label for="closure-end">Fine (UTC)</label>'+
      '<input class="field-input" type="datetime-local" id="closure-end" name="end" required value="'+baseDate+'T12:00"></div>'+
      '<div class="field"><label for="closure-reason">Motivo</label>'+
      '<input class="field-input" type="text" id="closure-reason" maxlength="160" name="reason" required placeholder="Manutenzione programmata"></div></div>'+
      '<div class="parameter-footer"><span class="panel-sub">È un’ipotesi di scenario, non un fermo registrato su Factory.</span>'+
      '<button type="submit" class="btn btn-primary">Simula fermo e ricalcola</button></div></form></article>';
    return out;
  }
  function renderDaily() {
    const date=state.dailyDate || state.anchor;
    const start=atMidnight(date).getTime(),end=start+day;
    const used=filteredResources().filter(r=>state.resourceKind==='all'||
      !['operator','tool','supplier','external'].includes(r.kind));
    const cards=used.map(r=>{
      const tasks=data.plan.assignments.filter(a=>a.demands.some(d=>d.resource_id===r.id) &&
        a.segments.some(s=>ms(s.end)>start&&ms(s.start)<end))
        .sort((a,b)=>ms(assignmentStart(a))-ms(assignmentStart(b)));
      return {r,tasks};
    }).filter(x=>x.tasks.length);
    let out='<article class="panel"><div class="panel-title"><div><h2>Programma '+esc(fmt(date,'monthDayYear'))+'</h2><p class="panel-sub">Attività che intersecano il giorno selezionato (ora locale)</p></div>'+
      '<div class="panel-tools"><button data-action="daily-prev" class="icon-button" type="button" aria-label="Giorno precedente">‹</button>'+
      '<input id="daily-picker" class="toolbar-select" type="date" value="'+esc(iso(date))+'" aria-label="Giorno del programma">'+
      '<button data-action="daily-next" class="icon-button" type="button" aria-label="Giorno successivo">›</button></div></div></article>';
    if(!cards.length) return out+emptyState('Nessuna lavorazione in questo giorno','Scegli un’altra data per consultare il programma.','☷');
    out+='<div class="daily-grid" style="margin-top:14px">'+cards.map(c=>{
      return '<article class="daily-card"><header><strong title="'+esc(c.r.id)+'">'+esc(resourceTitle(c.r.id))+'</strong><span class="status-pill">'+c.tasks.length+' lavorazioni</span></header>'+
        '<div class="daily-contents">'+c.tasks.slice(0,7).map(a=>
          '<div class="daily-row"><div><button type="button" class="link-button" data-task="'+esc(a.task_id)+'">'+esc(short(taskTitle(a),28))+'</button>'+
          (customersText(a,1)?'<p><strong>'+esc(short(customersText(a,1),40))+'</strong></p>':'')+
          '<p>'+esc(short(taskSubtitle(a),40))+'</p></div><div class="daily-time">'+esc(fmt(assignmentStart(a),'time'))+
          '<br>'+esc(fmt(assignmentEnd(a),'time'))+'</div></div>'
        ).join('')+(c.tasks.length>7?'<p class="panel-sub">+'+(c.tasks.length-7)+' lavorazioni</p>':'')+'</div></article>';
    }).join('')+'</div>';
    return out;
  }
  function renderNesting() {
    const ns=data.scenario.nestings || [];
    if(!ns.length)return '<article class="panel">'+emptyState('Nessun programma CAM in questa fixture',
      'Questo Scenario non contiene programmi di nesting. Quando sarà disponibile una sorgente CAM, i programmi saranno collegati alle fasi senza duplicare durate o quantità.','◈')+
      '<p class="section-note">Riferimento funzionale: pagina 4 del PDF di riferimento. Nessuna geometria o sfrido viene inventato.</p></article>';
    const ops=operationsById();
    const booked=new Map(data.plan.assignments.filter(a=>a.task_id.startsWith('nest:')).map(a=>[a.task_id.slice(5),a]));
    const query=state.query.trim().toLocaleLowerCase();
    const rows=ns.map(n=>({n,info:label('nests',n.id)||{},orders:[...new Set(n.operation_ids.map(id=>ops.get(id)&&ops.get(id).order_id).filter(Boolean))],a:booked.get(n.id)}))
      .filter(x=>!query||(x.n.id+' '+(x.info.code||'')+' '+(x.info.sheet||'')+' '+x.orders.map(o=>orderCode(o)+' '+(orderCustomer(o)||'')).join(' ')).toLocaleLowerCase().includes(query))
    // Sortable columns; a missing value always sorts last, whatever the direction.
    const machineOf=x=>x.a?x.a.demands.filter(d=>!/^OP:/.test(d.resource_id)).map(d=>resourceTitle(d.resource_id)).join(', '):'';
    const startOf=x=>x.a?ms(assignmentStart(x.a)):null;
    const columns=[
      ['code','Programma',x=>x.info.code||x.n.id,'text'],
      ['part','Cod. materiale',x=>x.info.sheet_part||null,'text'],
      ['sheet','Descrizione',x=>x.info.sheet||null,'text'],
      ['thickness','Spessore (mm)',x=>x.info.thickness_mm!==undefined&&x.info.thickness_mm!==''?Number(x.info.thickness_mm):null,'num'],
      ['size','Formato (mm)',x=>x.info.size_mm?x.info.size_mm.replace(/(\d+)(\.\d+)?/g,'$1'):null,'text'],
      ['machine','Macchina',x=>machineOf(x)||null,'text'],
      ['start','Inizio',startOf,'num'],
      ['duration','Taglio',x=>number(x.n.duration_seconds),'num'],
      ['orders','Commesse',x=>x.orders.map(orderCode).join(', ')||null,'text'],
      ['material','Materiale',x=>x.a?materialState(x.a)||null:null,'text']
    ];
    const sort=state.nestSort||{key:'start',dir:1};
    const col=columns.find(c=>c[0]===sort.key)||columns[6];
    rows.sort((x,y)=>{
      const a=col[2](x),b=col[2](y);
      if(a===null||a===undefined||Number.isNaN(a))return (b===null||b===undefined||Number.isNaN(b))?0:1;
      if(b===null||b===undefined||Number.isNaN(b))return -1;
      return sort.dir*(col[3]==='num'?a-b:String(a).localeCompare(String(b),'it',{numeric:true}));
    });
    const head=columns.map(c=>'<th aria-sort="'+(c[0]===col[0]?(sort.dir>0?'ascending':'descending'):'none')+'"><button type="button" class="sort-button'+(c[0]===col[0]?' active':'')+
      '" data-action="nest-sort" data-key="'+c[0]+'">'+esc(c[1])+'<span aria-hidden="true">'+(c[0]===col[0]?(sort.dir>0?' ▲':' ▼'):' ↕')+'</span></button></th>').join('');
    return '<article class="panel"><div class="panel-title"><div><h2>Programmi di taglio</h2><p class="panel-sub">'+rows.length+' programmi · clic sull’intestazione per ordinare · lamiere dai programmi Alma</p></div>'+
      '<div class="panel-tools"><div class="search-wrap"><input id="quick-search" class="toolbar-search" value="'+esc(state.query)+'" placeholder="Programma, lamiera, commessa…" aria-label="Cerca programma"></div></div></div>'+
      '<div class="table-scroll"><table class="data-table"><thead><tr>'+head+'</tr></thead><tbody>'+
      rows.slice(0,200).map(x=>{
        const m=x.a?materialState(x.a):null;
        return '<tr><td>'+(x.a?'<button type="button" class="link-button" data-task="'+esc(x.a.task_id)+'"><strong>'+esc(x.info.code||x.n.id)+'</strong></button>':'<strong>'+esc(x.info.code||x.n.id)+'</strong>')+'</td>'+
          '<td>'+esc(x.info.sheet_part||'—')+'</td>'+
          '<td>'+esc(x.info.sheet||'—')+'</td>'+
          '<td class="num">'+esc(x.info.thickness_mm?x.info.thickness_mm:'—')+'</td>'+
          '<td>'+esc(x.info.size_mm?x.info.size_mm.replace(/(\d+)(\.\d+)?/g,'$1'):'—')+(x.info.used_pct?'<small class="cell-sub">utilizzo '+esc(x.info.used_pct)+'%</small>':'')+'</td>'+
          '<td>'+esc(machineOf(x)||'Non collocato')+'</td>'+
          '<td>'+esc(x.a?fmt(assignmentStart(x.a)):'—')+'</td><td>'+esc(hrs(x.n.duration_seconds))+'</td>'+
          '<td>'+esc(x.orders.slice(0,4).map(orderCode).join(', '))+(x.orders.length>4?' +'+(x.orders.length-4):'')+
          (x.a&&customersText(x.a,3)?'<small class="cell-sub">'+esc(customersText(x.a,3))+'</small>':'')+'</td>'+
          '<td>'+(m==='short'?'<span class="status-pill bad">Mancante</span>':m==='ok'?'<span class="status-pill">Assegnato</span>':'—')+'</td></tr>';
      }).join('')+'</tbody></table></div>'+(rows.length>200?'<p class="section-note">Mostrati i primi 200 di '+rows.length+'.</p>':'')+
      '<p class="section-note">Un programma è una sola attività di taglio: la durata vale una volta per tutte le commesse collegate.</p></article>';
  }
  function renderParameters() {
    const current={...data.scenario.settings,...state.settings};
    const fields=[
      ['mode','Strategia principale',[['auto','Automatica'],['forward','In avanti'],['backward','A ritroso']],'La scelta si applica alle fasi che non forzano una modalità propria.'],
      ['auto_rule','Criterio automatico',[['due_distance','Distanza dalla consegna'],['slack','Margine residuo']],'La modalità automatica usa un criterio dichiarato, non una regola implicita.'],
      ['auto_backward_days','Soglia giorni per ritroso',null,'Giorni prima di scegliere il calcolo a ritroso.'],
      ['auto_slack_seconds','Soglia slack (secondi)',null,'Usata solo con la regola di margine residuo.'],
      ['priority_weight','Peso priorità commessa',null,'Peso esplicito nella selezione dei task pronti.'],
      ['due_weight','Peso data di consegna',null,'Influenza il dispatch delle attività pronte.'],
      ['customer_weight','Peso priorità cliente',null,'Influenza il dispatch delle attività pronte.'],
      ['alternative_switch_min_gain_seconds','Guadagno minimo per cambiare alternativa (secondi)',null,'Un\'alternativa non preferita si usa solo se fa finire prima di almeno questi secondi.'],
      ['delivery_buffer_days','Margine di consegna (giorni)',null,'Si pianifica contro la consegna meno questi giorni; le regole di margine per cliente o commessa restano.']
    ];
    const bufferDefault=((data.meta&&data.meta.canonical&&data.meta.canonical.delivery_buffer_seconds)||0);
    current.delivery_buffer_days=(current.delivery_buffer_seconds!==undefined?current.delivery_buffer_seconds:bufferDefault)/86400;
    return '<article class="panel"><div class="panel-title"><div><h2>Profilo di calcolo</h2>'+
      '<p class="panel-sub">Le modifiche sono simulazioni temporanee e non vengono salvate nel gestionale o nel file sorgente.</p></div>'+
      '<span class="status-pill neutral">Simulazione</span></div><form id="parameters-form"><div class="field-grid">'+fields.map(f=>{
      const control=f[2] ? '<select class="field-select" name="'+f[0]+'">'+f[2].map(opt=>(
        '<option value="'+opt[0]+'" '+(current[f[0]]===opt[0]?'selected':'')+'>'+esc(opt[1])+'</option>'
      )).join('')+'</select>' :
      f[0]==='delivery_buffer_days' ?
        '<input class="field-input" type="number" name="'+f[0]+'" min="0" max="90" step="0.25" value="'+esc(Number((current[f[0]]||0).toFixed(2)))+'">' :
      '<input class="field-input" type="number" name="'+f[0]+'" min="0" max="1000000" step="1" value="'+esc(current[f[0]]||0)+'">';
      return '<div class="field"><label for="param-'+f[0]+'">'+esc(f[1])+'</label>'+
        control.replace('name="'+f[0]+'"','name="'+f[0]+'" id="param-'+f[0]+'"')+'<small>'+esc(f[3])+'</small></div>';
    }).join('')+'</div><div class="parameter-footer"><span class="panel-sub">Il calcolo è indipendente dal database Factory.</span>'+
      '<button class="btn btn-primary" type="submit">↻ Applica e ricalcola</button></div></form></article>'+
      '<div class="info-panel" style="margin-top:14px">Questa è una prima interfaccia ai parametri effettivamente supportati dal motore. I controlli di setup avanzato, memoria e disponibilità materiali richiedono i rispettivi contratti canonici, non vengono simulati con campi fittizi.</div>';
  }
  function answerOperationalQuestion(question) {
    const q=question.toLocaleLowerCase('it-IT').trim();
    const p=data.plan;
    const canonical=data.meta&&data.meta.canonical;
    const late=p.orders.filter(o=>number(o.tardiness_seconds)>0)
      .sort((a,b)=>number(b.tardiness_seconds)-number(a.tardiness_seconds));
    const ref={source:'Piano calcolato · SHA '+short(p.scenario_digest,16)};
    if(/ritard|consegn|scadenz/.test(q)){
      const list=late.slice(0,7).map(o=>o.order_id+' · '+hrs(o.tardiness_seconds)+' di ritardo');
      return {...ref,title:'Consegne e ritardi',
        text:late.length+' commesse hanno un ritardo calcolato su '+p.orders.length+
          ' commesse nel risultato. Non posso determinarne la causa solo da questo indicatore.',
        facts:list};
    }
    if(/caric|satur|colli|macchin|risors/.test(q)){
      const list=p.resources.filter(x=>x.utilization!==null)
        .sort((a,b)=>number(b.utilization)-number(a.utilization))
        .slice(0,7).map(x=>x.resource_id+' · '+pct(x.utilization));
      return {...ref,title:'Utilizzo delle risorse',
        text:'Classifica delle risorse con disponibilità finita. Le percentuali si riferiscono all’intero orizzonte, non ai picchi quotidiani.',
        facts:list};
    }
    if(/non colloc|non pianific|impossib|esclus|blocc|error/.test(q)){
      return {...ref,title:'Task non collocati',
        text:p.unscheduled.length+' task risultano senza prenotazione nel piano corrente. I motivi riportati sono quelli dell’engine, non diagnosi aggiunte dall’assistente.',
        facts:p.unscheduled.slice(0,7).map(x=>x.task_id+' · '+x.code+(x.detail?' · '+short(x.detail,90):''))};
    }
    if(/material|magazz|giacenz|lotto|fabbisogn/.test(q)){
      const mrp=canonical&&canonical.materials;
      if(!mrp)return {...ref,title:'Materiali non documentati',
        text:'Lo Scenario non contiene un manifest MRP con quantità e lotti tracciabili. Non posso dichiarare alcuna disponibilità o carenza.',facts:[]};
      return {...ref,source:'Manifest canonico di origine, non MRP live',
        title:'Copertura dei materiali',
        text:(mrp.pegs||[]).length+' assegnazioni lotto-fabbisogno e '+
          (mrp.shortages||[]).length+' carenze nella fonte originale. Politica: '+(mrp.allocation_order||'non dichiarata')+'.',
        facts:(mrp.shortages||[]).slice(0,7).map(x=>x.unit+' · '+x.item_id+' · manca '+x.missing)};
    }
    if(/consunt|avanz|lavorat|wip|produc/.test(q)){
      const wip=canonical&&canonical.progress;
      return {...ref,source:'Record di avanzamento sorgente (non stato MES live)',
        title:'Avanzamento documentato',
        text:wip?wip.total_rows+' record di avanzamento presenti nella fonte. Non posso dedurre se una macchina sia ancora occupata, né attestare lo stato di setup.':
          'Non è presente un dominio di consuntivi attestabili in questa fixture.',
        facts:wip?(wip.rows||[]).slice(0,5).map(x=>x.operation_id+' · '+x.status+
          ' · '+(x.recorded_work_seconds===null||x.recorded_work_seconds===undefined?
          'tempo non presente':hrs(x.recorded_work_seconds))):[]};
    }
    if(/regol|priorit|memori/.test(q)){
      const rules=canonical&&canonical.rules;
      const applied=rules&&Array.isArray(rules.applied)?rules.applied:[];
      return {...ref,source:'Manifest delle regole canoniche',
        title:'Regole applicate',
        text:applied.length+' regole approvate e applicate risultano nel manifest sorgente. Nessuna nuova regola è stata creata.',
        facts:applied.slice(0,7).map(x=>x.rule+' · '+x.action+' · confermata da '+(x.confirmed_by||'non riportato'))};
    }
    return {...ref,title:'Domanda non supportata da questa analisi',
      text:'Posso leggere ritardi, consegne, carichi, non collocate, materiali, consuntivi e regole dal piano attuale. Non dispongo di una AI generativa collegata e non posso dedurre cause o azioni non dimostrate.',
      facts:[]};
  }

  function renderAssistant() {
    const connected=Boolean(data.meta&&data.meta.llm_available);
    const p=data.plan,late=p.orders.filter(o=>o.tardiness_seconds>0).sort((a,b)=>b.tardiness_seconds-a.tardiness_seconds);
    const overloaded=p.resources.filter(r=>r.utilization!==null && r.utilization>.9).sort((a,b)=>b.utilization-a.utilization);
    const insights=[
      ['Consegne a rischio',late.length?
        late.length+' commesse presentano un ritardo calcolato. La più critica è '+late[0].order_id+' ('+hrs(late[0].tardiness_seconds)+').':
        'Non risultano commesse in ritardo nel piano attuale. Verificare comunque le date sorgente.','↗'],
      ['Colli di bottiglia',overloaded.length?
        overloaded.length+' risorse superano il 90% di utilizzo sull’orizzonte. La prima è '+overloaded[0].resource_id+' ('+pct(overloaded[0].utilization)+').':
        'Nessuna risorsa supera il 90% di utilizzo medio. I picchi giornalieri possono comunque richiedere attenzione.','◫'],
      ['Fasi non collocate',p.unscheduled.length?
        p.unscheduled.length+' task non hanno una prenotazione. Prima ragione: '+p.unscheduled[0].code+'.':
        'Tutti i task ammessi allo scenario hanno un risultato di pianificazione.','⚑'],
      ['Integrità del piano','Il backend usa verify_plan indipendentemente dal calcolo. Questo risultato appartiene allo scenario con SHA '+short(p.scenario_digest,14)+'.','✓']
    ];
    const rules=data.meta&&data.meta.canonical&&data.meta.canonical.rules;
    if(rules&&Array.isArray(rules.applied)&&rules.applied.length){
      insights.push(['Regole aziendali',
        rules.applied.length+' regole confermate risultano applicate nel manifest della fonte. Sono consultabili nella sezione Memoria aziendale.','◇']);
    }
    const suggestions=[
      'Quali commesse sono in ritardo?',
      'Quali macchine sono più cariche?',
      'Quali attività non sono pianificate?',
      'Ci sono carenze di materiale?',
      'Quali consuntivi sono disponibili?',
      'Quali regole aziendali sono applicate?'
    ];
    return '<article class="panel assist-workbench"><div class="panel-title"><div><h2>Chiedi al piano</h2>'+
      '<p class="panel-sub">'+(connected?'AI collegata dalle Impostazioni: la domanda e i dati del piano richiesti vengono inviati al fornitore scelto.':'Analisi deterministica basata sui dati verificati, senza connessione ad AI esterne.')+'</p></div>'+
      '<div class="panel-tools">'+(extraViews&&extraViews.session&&extraViews.session.role!=='reparto'?
        '<button class="btn btn-light btn-small" type="button" data-view="impostazioni">'+(connected?'Impostazioni AI':'Collega un modello AI →')+'</button>':'')+
      '<span class="status-pill neutral">Sola lettura</span></div></div>'+
      '<div class="assistant-inner"><div class="info-panel"><strong>Ambito:</strong> indicatori, scadenze, carichi, materiali, consuntivi, regole e percorso critico delle commesse. '+
      'Le risposte compaiono nel pannello <strong>Assistente</strong>, disponibile in ogni vista. Non posso attribuire cause senza evidenze né modificare Factory.</div>'+
      '<div class="assistant-suggestions">'+suggestions.map((q,i)=>
        '<button type="button" class="assistant-chip" data-assistant-prompt="'+i+'">'+esc(q)+'</button>').join('')+'</div>'+
      '<form id="assistant-form" class="assistant-form"><label for="assistant-input">La tua domanda</label>'+
      '<div><input id="assistant-input" name="question" required maxlength="280" autocomplete="off" '+
      'placeholder="Es. perché la commessa più in ritardo finisce così tardi?">'+
      '<button type="submit" class="btn btn-primary" '+(state.chat.busy?'disabled':'')+'>Chiedi all’assistente →</button></div></form>'+
      '</div></article>'+qualityCard()+'<div class="insight-grid" style="margin-top:14px">'+insights.map(x=>
        '<article class="insight-card"><span class="insight-icon">'+x[2]+'</span>'+
        '<h3>'+esc(x[0])+'</h3><p>'+esc(x[1])+'</p></article>').join('')+'</div>';
  }
  function qualityCard() {
    const digest=data.plan.scenario_digest,q=state.quality;
    if(!q||q.digest!==digest){
      state.quality={digest,pending:true};
      const done=(value)=>{
        if(!state.quality||state.quality.digest!==digest)return;
        state.quality=value;if(state.view==='assistant')render();
      };
      postAnalysis('/api/quality',{scenario_digest:digest}).then(result=>done({digest,kpi:result.kpi}))
        .catch(error=>done({digest,error:error.message}));
    }
    if(state.quality.pending)return '<article class="panel quality-card"><p class="panel-sub">Indicatori di qualità in calcolo…</p></article>';
    if(state.quality.error)return '<article class="panel quality-card"><p class="panel-sub">Indicatori di qualità non disponibili: '+esc(state.quality.error)+'</p></article>';
    const o=state.quality.kpi.orders,top=(state.quality.kpi.resource_load.highest_utilization_finite||[])[0];
    const tiles=[['Livello di servizio',o.service_level_percent===null?'—':o.service_level_percent.toLocaleString('it-IT')+'%',o.complete_on_time+' su '+o.complete_with_due+' commesse complete con consegna'],
      ['Ritardo totale',hrs(o.total_tardiness_seconds),o.complete_late+' commesse complete in ritardo'],
      ['Durata del piano',hrs(state.quality.kpi.makespan_seconds),'Dall’inizio dell’orizzonte all’ultima fine'],
      ['Collo di bottiglia',top?short(top.resource_id,18):'—',top?top.utilization_percent.toLocaleString('it-IT')+'% sull’orizzonte':'Nessuna risorsa finita']];
    return '<article class="panel quality-card"><div class="panel-title"><div><h2>Qualità del piano</h2><p class="panel-sub">KPI verificati da engine/quality.py sul piano mostrato</p></div></div>'+
      '<div class="quality-grid">'+tiles.map(t=>'<div class="quality-tile"><small>'+esc(t[0])+'</small><strong>'+esc(t[1])+'</strong><span>'+esc(t[2])+'</span></div>').join('')+'</div></article>';
  }
  /* Bottlenecks and problems: nothing estimated here. Queue and cause come from /api/delays (critical path of the
   * verified plan), saturation from /api/loads (booked vs calendar), the rest is counted from the plan itself. */
  const BN_DAYS=62;
  function bottleneckLoads() {
    const digest=data.plan.scenario_digest,cache=state.bottlenecks;
    if(cache&&cache.digest===digest)return cache;
    let first=null;
    for(const a of data.plan.assignments)for(const s of a.segments){const t=ms(s.start);if(first===null||t<first)first=t;}
    const start=new Date(first===null?ms(data.scenario.start):first).toISOString().slice(0,10);
    state.bottlenecks={digest,start,pending:true};
    const done=(value)=>{if(state.bottlenecks&&state.bottlenecks.digest===digest){state.bottlenecks={digest,start,...value};if(state.view==='bottlenecks')render();}};
    postAnalysis('/api/loads',{scenario_digest:digest,start,days:BN_DAYS,kind:'all'})
      .then(result=>done({rows:new Map(result.rows.map(r=>[r.resource_id,r])),days:result.days}))
      .catch(error=>done({error:error.message}));
    return state.bottlenecks;
  }
  function bottleneckRows(loads,delays) {
    const ops=operationsById(),lateOrders=new Set(data.plan.orders.filter(o=>number(o.tardiness_seconds)>0).map(o=>o.order_id));
    const rows=new Map();
    const row=(id)=>{
      if(!rows.has(id))rows.set(id,{id,kind:(resourcesById().get(id)||{}).kind||'',booked:0,lateSeconds:0,blocked:0,wait:0,peak:null,over:0,high:0,sat:null});
      return rows.get(id);
    };
    for(const a of data.plan.assignments){
      const late=a.operation_ids.some(id=>ops.get(id)&&lateOrders.has(ops.get(id).order_id));
      const seconds=a.segments.reduce((s,x)=>s+max(0,ms(x.end)-ms(x.start)),0)/1000;
      for(const d of a.demands){const r=row(d.resource_id);r.booked+=seconds*number(d.units);if(late)r.lateSeconds+=seconds*number(d.units);}
    }
    for(const x of (delays.rows||[]))if(x.cause==='capacity'&&x.resource){const r=row(x.resource);r.blocked++;r.wait+=number(x.wait_seconds);}
    if(loads.rows)for(const [id,l] of loads.rows){
      if(!l.saturation_percent)continue;
      const r=row(id),vals=l.saturation_percent.filter(v=>v!==null);
      const booked=l.booked_hours.reduce((s,v)=>s+number(v),0),avail=(l.available_hours||[]).reduce((s,v)=>s+number(v),0);
      r.peak=vals.length?Math.max(...vals):null;r.over=vals.filter(v=>v>100).length;r.high=vals.filter(v=>v>=90).length;
      r.sat=avail>0?100*booked/avail:null;r.daily=l.saturation_percent;
    }
    return [...rows.values()].filter(r=>r.kind!=='supplier'&&r.kind!=='external'&&(r.booked>0||r.blocked))
      .sort((a,b)=>b.blocked-a.blocked||b.wait-a.wait||b.over-a.over||(b.peak||0)-(a.peak||0)||b.lateSeconds-a.lateSeconds);
  }
  function bnStrip(r,loads) {
    if(!r.daily)return '<span class="muted-line">—</span>';
    return '<span class="bn-strip" role="img" aria-label="Saturazione giornaliera, primi '+BN_DAYS+' giorni">'+r.daily.map((v,i)=>{
      const level=v===null?'none':v>100?'over':v>=90?'high':v>=50?'mid':v>0?'low':'zero';
      return '<i class="bn-'+level+'" title="'+esc(loads.days[i]+' · '+(v===null?'senza capacità':v.toLocaleString('it-IT')+'%'))+'"></i>';
    }).join('')+'</span>';
  }
  function renderBottlenecks() {
    const delays=delaysAnalysis(),loads=bottleneckLoads();
    if(delays.pending||loads.pending)return '<article class="panel"><div class="skeleton"><span></span><span></span><p>Analisi di code, saturazione e cause in corso…</p></div></article>';
    if(delays.error)return '<article class="panel">'+emptyState('Analisi non disponibile',delays.error,'⚠')+'</article>';
    const r=delays.result,p=data.plan,canonical=data.meta&&data.meta.canonical||{};
    const rows=bottleneckRows(loads,r),kind=state.bnKind||'all';
    const shown=rows.filter(x=>kind==='all'||x.kind===kind);
    const late=p.orders.filter(o=>number(o.tardiness_seconds)>0),expired=p.orders.filter(o=>o.due&&ms(o.due)<=ms(data.scenario.start)&&number(o.tardiness_seconds)>0);
    const shortages=(canonical.issues||[]).filter(i=>i.code==='material_shortage');
    const risk=r.rows.filter(x=>x.status==='risk').length;
    const causes=Object.keys(CAUSES).filter(k=>r.counts[k]).sort((a,b)=>r.counts[b]-r.counts[a]);
    const topCause=causes[0],topRes=rows.find(x=>x.blocked);
    const tiles=[
      ['Commesse in ritardo',late.length+' / '+p.orders.length,hrs(late.reduce((s,o)=>s+number(o.tardiness_seconds),0))+' di ritardo totale'+(risk?' · '+risk+' a rischio':'')],
      ['Causa prevalente',topCause?CAUSES[topCause][0]:'—',topCause?r.counts[topCause]+' commesse su '+r.total+' con criticità':'Nessuna criticità'],
      ['Risorsa che blocca di più',topRes?short(resourceCode(topRes.id),18):'—',topRes?topRes.blocked+' commesse in coda · '+hrs(topRes.wait)+' di attesa':'Nessuna coda su risorse'],
      ['Da sbloccare',String(p.unscheduled.length+shortages.length),p.unscheduled.length+' fasi non collocate · '+shortages.length+' carenze materiale']];
    const sev={bad:'Critico',warn:'Attenzione',ok:'In ordine'};
    const problems=[
      [p.unscheduled.length?'bad':'ok','Fasi non collocate',p.unscheduled.length?p.unscheduled.length+' fasi senza prenotazione (materiale, calendario o vincoli). Le commesse collegate non hanno una fine calcolabile.':'Tutte le fasi ammesse hanno una prenotazione.','deliveries','Vedi consegne'],
      [shortages.length?'bad':'ok','Carenze di materiale',shortages.length?shortages.length+' fabbisogni non coperti dalla fonte materiali. Se una regola aziendale spegne il vincolo materiale il piano non li attende: le date reali dipendono dagli arrivi.':'Nessuna carenza dichiarata dalla fonte.','materials','Vedi materiali'],
      [expired.length?'warn':'ok','Consegne già scadute a inizio piano',expired.length?expired.length+' commesse hanno la consegna precedente al '+fmt(data.scenario.start,'day')+': risultano in ritardo per definizione ('+hrs(expired.reduce((s,o)=>s+number(o.tardiness_seconds),0))+'). Probabilmente date da aggiornare o commesse da chiudere in Factory.':'Nessuna.','deliveries','Vedi consegne'],
      [(r.counts.capacity||0)?'warn':'ok','Code su risorse sature',(r.counts.capacity||0)?r.counts.capacity+' commesse hanno come vincolo principale l’attesa di una risorsa occupata. Sono i veri colli di bottiglia di capacità.':'Nessuna commessa è frenata da una coda.','loads','Vedi carichi'],
      [(r.counts.release||0)?'warn':'ok','Rilasci e arrivi materiale',(r.counts.release||0)?r.counts.release+' commesse partono tardi per data di rilascio o arrivo del materiale.':'Nessuna commessa è frenata dal rilascio.','materials','Vedi materiali'],
      [(r.counts.subcontract||0)?'warn':'ok','Conto lavoro',(r.counts.subcontract||0)?r.counts.subcontract+' commesse dipendono dalla data di rientro dichiarata dal fornitore.':'Nessuna dipendenza dal conto lavoro.','deliveries','Vedi consegne']
    ].sort((a,b)=>['bad','warn','ok'].indexOf(a[0])-['bad','warn','ok'].indexOf(b[0]));
    const widest=Math.max(1,...causes.map(k=>r.counts[k]));
    let out='<article class="panel quality-card"><div class="panel-title"><div><h2>Sintesi</h2><p class="panel-sub">Dal piano verificato · cause dal percorso critico, saturazione dai calendari delle risorse</p></div></div>'+
      '<div class="quality-grid">'+tiles.map(t=>'<div class="quality-tile"><small>'+esc(t[0])+'</small><strong>'+esc(t[1])+'</strong><span>'+esc(t[2])+'</span></div>').join('')+'</div></article>';
    out+='<div class="split-grid" style="margin-top:14px"><article class="panel"><div class="panel-title"><div><h2>Problemi da risolvere</h2><p class="panel-sub">In ordine di gravità</p></div></div><div class="detail-list">'+
      problems.map(x=>'<div class="alert-row bn-'+x[0]+'"><span class="alert-icon">'+(x[0]==='ok'?'✓':x[0]==='bad'?'!':'↗')+'</span><div><strong>'+esc(x[1])+' <span class="status-pill '+(x[0]==='bad'?'bad':x[0]==='warn'?'warn':'')+'">'+sev[x[0]]+'</span></strong><p>'+esc(x[2])+'</p>'+
        (x[0]==='ok'?'':'<button type="button" class="link-button" data-view="'+x[3]+'">'+esc(x[4])+' →</button>')+'</div></div>').join('')+'</div></article>'+
      '<article class="panel"><div class="panel-title"><div><h2>Cause dei ritardi</h2><p class="panel-sub">'+r.total+' commesse in ritardo, non collocate o a rischio · clic per filtrarle</p></div></div><div class="mini-bars">'+
      (causes.length?causes.map(k=>'<button type="button" class="bn-cause" data-action="bn-cause" data-cause="'+k+'" title="'+esc(CAUSES[k][1])+'"><span>'+esc(CAUSES[k][0])+'</span>'+
        '<div class="rail"><div class="fill '+(k==='capacity'?'high':'')+'" style="width:'+(100*r.counts[k]/widest).toFixed(1)+'%"></div></div><strong>'+r.counts[k]+'</strong></button>').join(''):
        '<p class="modal-muted">Nessuna commessa in ritardo o a rischio.</p>')+'</div></article></div>';
    out+='<article class="panel" style="margin-top:14px"><div class="panel-title"><div><h2>Risorse che frenano il piano</h2><p class="panel-sub">Ordinate per commesse in coda, poi attesa, poi giorni sopra capacità · saturazione sui primi '+BN_DAYS+' giorni dal '+esc(isoText(loads.start))+'</p></div>'+
      '<div class="panel-tools"><select id="bn-kind" class="toolbar-select" aria-label="Tipo risorsa">'+
      [['all','Macchine e operatori'],['machine','Macchine'],['operator','Operatori']].map(o=>'<option value="'+o[0]+'" '+(kind===o[0]?'selected':'')+'>'+o[1]+'</option>').join('')+'</select></div></div>'+
      '<p class="load-legend"><span class="bn-key bn-over"></span> oltre capacità <span class="bn-key bn-high"></span> 90–100% <span class="bn-key bn-mid"></span> 50–90% <span class="bn-key bn-low"></span> sotto 50%</p>'+
      '<div class="table-scroll"><table class="data-table"><thead><tr><th>Risorsa</th><th>Commesse in coda</th><th>Attesa totale</th><th>Picco</th><th>Giorni &gt;100%</th><th>Ore su commesse in ritardo</th><th>Saturazione giornaliera</th></tr></thead><tbody>'+
      shown.slice(0,state.showAll?shown.length:15).map(x=>'<tr><td><strong>'+esc(resourceCode(x.id))+'</strong>'+(resourceName(x.id)?'<small class="cell-sub">'+esc(short(resourceName(x.id),34))+'</small>':'')+
        '<small class="cell-sub">'+(x.kind==='operator'?'Operatore':x.kind==='machine'?'Macchina':esc(x.kind))+'</small></td>'+
        '<td>'+(x.blocked?'<span class="gap late">'+x.blocked+'</span>':'0')+'</td><td>'+(x.wait?esc(hrs(x.wait)):'—')+'</td>'+
        '<td>'+(x.peak===null?'—':'<span class="gap '+(x.peak>100?'late':x.peak>=90?'risk':'')+'">'+Math.round(x.peak).toLocaleString('it-IT')+'%</span>')+'</td>'+
        '<td>'+(x.over||'0')+'</td><td>'+(x.lateSeconds?esc(hrs(x.lateSeconds)):'—')+'</td><td>'+bnStrip(x,loads)+'</td></tr>').join('')+
      '</tbody></table></div>'+(shown.length>15?'<div class="filters-line"><button class="btn btn-light btn-small" type="button" data-action="bn-all">'+(state.showAll?'Mostra solo le prime 15':'Mostra tutte le '+shown.length+' risorse')+'</button></div>':'')+
      '<p class="section-note">Commesse in coda e attesa: la risorsa è il vincolo decisivo sul percorso critico della commessa. Il picco e i giorni sopra capacità riguardano solo la finestra mostrata; l’utilizzo sull’intero orizzonte è in Carichi macchine. Le ore su commesse in ritardo sommano il tempo prenotato dalle fasi delle commesse che finiscono dopo la consegna.</p></article>';
    return out;
  }
  const RULE_ACTIONS={set_priority:'Priorità ordine',set_customer_priority:'Priorità cliente',add_setup_seconds:'Setup aggiuntivo (s)',
    note:'Indicazione testuale',ignore_material:'Ignora vincolo materiale'};
  const PRIORITY_LEVELS=[['massima',-1000],['alta',-100],['normale',0],['bassa',100]];
  function ruleScopeText(rule) {
    const parts=[];
    if(rule.order_id)parts.push('commessa '+([orderCode(rule.order_id),orderCustomer(rule.order_id)].filter(Boolean).join(', ')||rule.order_id));
    if(rule.customer_id)parts.push('cliente '+(customerName(rule.customer_id)?customerName(rule.customer_id)+' ('+rule.customer_id+')':rule.customer_id));
    if(rule.resource_id)parts.push('risorsa '+resourceTitle(rule.resource_id));
    if(rule.material)parts.push('materiale '+rule.material);
    if(rule.min_thickness_mm)parts.push('spessore oltre '+rule.min_thickness_mm+' mm');
    if(rule.max_thickness_mm)parts.push('spessore fino a '+rule.max_thickness_mm+' mm');
    if(rule.valid_from)parts.push('dal '+isoText(String(rule.valid_from).slice(0,10)));
    if(rule.valid_to)parts.push('fino al '+isoText(String(rule.valid_to).slice(0,16)));
    return parts.length?parts.join(', '):'tutto il piano';
  }
  function ruleValueText(rule) {
    if(rule.action==='note'||rule.action==='ignore_material')return RULE_ACTIONS[rule.action];
    const level=(rule.action==='set_priority'||rule.action==='set_customer_priority')&&PRIORITY_LEVELS.find(l=>l[1]===rule.value);
    return (RULE_ACTIONS[rule.action]||rule.action)+' = '+rule.value+(level?' ('+level[0]+')':'');
  }
  function renderMemory() {
    const manifest=data.meta&&data.meta.canonical;
    const local=data.meta&&data.meta.company_memory;
    const origin=manifest&&manifest.rules;
    const rules={applied:[...(origin&&origin.applied||[]),...(local&&local.applied||[])],
      skipped:[...(origin&&origin.skipped||[]),...(local&&local.skipped||[])]};
    const applied=rules&&Array.isArray(rules.applied)?rules.applied:[];
    const skipped=rules&&Array.isArray(rules.skipped)?rules.skipped:[];
    const notes=local&&Array.isArray(local.notes)?local.notes:[];
    const switched=local&&local.material_constraint&&local.material_constraint.ignored?local.material_constraint:null;
    const manage=state.sessionRole!=='reparto'?'<button class="btn btn-muted" type="button" data-open-rules>Gestisci regole</button>':'';
    let guidance='';
    if(switched)guidance+='<div class="info-panel warn-panel"><strong>Vincolo materiale spento</strong> dalla memoria aziendale ('+
      esc(switched.rules.join(', '))+'): il piano non attende il materiale, '+esc(switched.operations_released)+
      ' fasi liberate. Le mancanze restano nella vista Materiali come informazione della fonte.</div>';
    guidance+='<article class="panel"><div class="panel-title"><div><h2>Indicazioni per l’assistente</h2><p class="panel-sub">'+
      'Note confermate: l’assistente le riceve in ogni conversazione e le segue quando ripianifica. Non cambiano da sole il calcolo.</p></div>'+
      '<span class="status-pill">'+notes.length+' attive</span>'+manage+'</div>';
    guidance+=notes.length?'<div class="detail-list">'+notes.map(n=>'<div class="alert-row"><span class="alert-icon">✎</span><div><strong>'+
      esc(n.note)+'</strong><p>'+esc([ruleScopeText(n),'confermata da '+(n.confirmed_by||'—'),n.id].filter(Boolean).join(' · '))+'</p></div></div>').join('')+'</div>':
      emptyState('Nessuna indicazione','Scrivi in Gestisci regole o in chat cosa deve ricordare l’assistente, come lo diresti a un collega.','✎');
    guidance+='</article>';
    if(!applied.length&&!skipped.length)return '<div class="stack">'+guidance+'</div>';
    let out='<div class="stack">'+guidance+'<div class="info-panel"><strong>Memoria con provenienza verificabile.</strong> '+
      'Questi dati provengono dal manifest delle fonti canoniche e descrivono il risultato di regole esplicitamente confermate. '+ 
      'Nelle simulazioni what-if il manifest resta riferito allo Scenario sorgente, non costituisce nuova approvazione.</div>'+
      '<article class="panel"><div class="panel-title"><div><h2>Regole applicate</h2><p class="panel-sub">'+applied.length+
      ' regole con audit sul dominio canonico</p></div><span class="status-pill">'+applied.length+' attive nella fonte</span></div>';
    if(!applied.length)out+=emptyState('Nessuna regola applicata','La fonte non riporta regole applicate.','◇');
    else {
      out+='<div class="table-scroll"><table class="data-table"><thead><tr><th>Regola</th><th>Azione</th><th>Valore</th><th>Fasi</th><th>Confermata da</th></tr></thead><tbody>';
      for(const rule of applied)out+='<tr><td><strong>'+esc(rule.rule)+'</strong></td><td>'+esc(RULE_ACTIONS[rule.action]||rule.action)+'</td>'+
        '<td>'+esc(rule.value===null||rule.value===undefined?'—':rule.value)+'</td><td>'+esc((rule.operations||[]).length)+'</td><td>'+esc(rule.confirmed_by||'—')+'</td></tr>';
      out+='</tbody></table></div>';
    }
    out+='</article><article class="panel"><div class="panel-title"><h2>Regole sospese o non applicate</h2>'+
      '<span class="status-pill neutral">'+skipped.length+' in attesa</span></div>';
    if(!skipped.length)out+=emptyState('Nessuna regola esclusa','Le verifiche del manifest non indicano esclusioni.','✓');
    else out+='<div class="detail-list">'+skipped.map(rule=>'<div class="alert-row"><span class="alert-icon">i</span>'+
      '<div><strong>'+esc(rule.rule)+'</strong><p>'+esc(rule.reason||'Motivo non specificato')+'</p></div></div>').join('')+'</div>';
    return out+'</article></div>';
  }
  function hasWhatif() {
    return state.edits.fixed_tasks.length>0 || state.edits.closures.length>0;
  }
  function editableAlternative(a) {
    if(!a.task_id.startsWith('job:') || a.operation_ids.length!==1 ||
       !a.segments.length || a.completed_at || a.external_end) return -1;
    const op=operationsById().get(a.operation_ids[0]);
    if(!op || !Array.isArray(op.alternatives)) return -1;
    const bundle=a.demands.map(d=>d.resource_id+':'+d.units).sort().join('|');
    return op.alternatives.findIndex(alt=>alt.demands.map(d=>d.resource_id+':'+d.units).sort().join('|')===bundle);
  }
  async function applyFixedTask(tid, when, alternative) {
    if(alternative<0 || !Number.isFinite(ms(when))){setToast('Scegli una data UTC valida e una fase con risorse ammissibili.');return;}
    const previous=state.edits;
    const rows=previous.fixed_tasks.filter(x=>x.task_id!==tid);
    state.edits={...previous,fixed_tasks:rows.concat([{task_id:tid,start:when,alternative}])};
    const ok=await recalculate(state.settings);
    if(!ok){state.edits=previous;render();}
  }
  async function unpinTask(tid) {
    const previous=state.edits;
    state.edits={...previous,fixed_tasks:previous.fixed_tasks.filter(x=>x.task_id!==tid)};
    const ok=await recalculate(state.settings);
    if(!ok){state.edits=previous;render();}
  }
  function inspectorEditor(a) {
    const idx=editableAlternative(a);
    if(idx<0) return '<div class="detail-section"><h3>MODIFICHE AL PIANO</h3><p class="panel-sub">Questo task è condiviso, storico o privo di un bundle modificabile; non è consentito spostarlo da questa preview.</p></div>';
    const existing=state.edits.fixed_tasks.find(x=>x.task_id===a.task_id);
    const value=(existing?existing.start:assignmentStart(a)).slice(0,16);
    return '<div class="detail-section"><h3>SIMULAZIONE / WHAT-IF</h3>'+
      '<p class="panel-sub">L’orario è espresso in UTC. Ogni proposta è ricalcolata e verificata dal motore, senza salvataggio.</p>'+
      '<label class="field" for="move-at" style="margin:12px 0"><span>Inizio proposto (UTC)</span>'+
      '<input id="move-at" type="datetime-local" class="field-input" value="'+esc(value)+'"></label>'+
      '<div class="inspector-buttons">'+
      '<button class="btn btn-primary btn-small" data-action="move-task" data-task-action="'+esc(a.task_id)+'" type="button">Simula spostamento</button>'+
      (existing?'<button class="btn btn-muted btn-small" data-action="unpin-task" data-task-action="'+esc(a.task_id)+'" type="button">Rimuovi vincolo</button>':
      '<button class="btn btn-muted btn-small" data-action="pin-task" data-task-action="'+esc(a.task_id)+'" type="button">Blocca qui</button>')+
      '</div></div>';
  }

  function updateInspector() {
    const selected=data.plan.assignments.find(a=>a.task_id===state.selected);
    const title=$('#inspector-title'),root=$('#inspector-body');
    if(!selected&&state.selectedOrder&&data.plan.orders.some(o=>o.order_id===state.selectedOrder)) {
      title.textContent='Commessa '+orderCode(state.selectedOrder);
      root.innerHTML=orderInspector(state.selectedOrder);
      return;
    }
    if(!selected) {
      title.textContent='Seleziona una lavorazione';
      root.innerHTML='<div class="inspector-empty"><span class="empty-illustration">▤</span><strong>La produzione, nel dettaglio.</strong><p>Seleziona una fase dal Gantt per visualizzarne risorse, tempi e commessa.</p></div>';
      return;
    }
    title.textContent=(nestId(selected)?'Nesting ':'')+taskTitle(selected);
    const duration=selected.segments.reduce((sum,s)=>sum+(ms(s.end)-ms(s.start))/1000,0);
    const late=isLateTask(selected);
    const related=taskOrders(selected);
    const ops=operationsById();
    const nest=nestInfo(selected), material=materialState(selected), done=progressShare(selected);
    const family={cut:nestId(selected)?'Nesting Alma':'Taglio',bend:'Piegatura',finish:'Lavorazione',ext:'Esterno'}[phaseFamily(selected)];
    const key=(k,v)=>v?'<div class="detail-key"><span>'+esc(k)+'</span><span>'+v+'</span></div>':'';
    const machines=selected.demands.filter(d=>!/^OP:/.test(d.resource_id)).map(d=>resourceTitle(d.resource_id));
    const people=selected.demands.filter(d=>/^OP:/.test(d.resource_id)).map(d=>resourceTitle(d.resource_id));
    const pieces=selected.operation_ids.map(id=>opInfo(id)).filter(o=>o&&o.quantity);
    const produced=selected.operation_ids.map(id=>evidence().progress.get(id)).filter(r=>r&&r.produced_quantity!==null&&r.produced_quantity!==undefined);
    const orderCards=related.map(oid=>{
      const result=data.plan.orders.find(o=>o.order_id===oid)||{};
      const mine=selected.operation_ids.filter(id=>ops.get(id)&&ops.get(id).order_id===oid);
      const qty=mine.map(id=>opInfo(id)).filter(o=>o&&o.quantity).reduce((s,o)=>s+Number(o.quantity),0);
      const part=[...new Set(mine.map(id=>opInfo(id)).filter(o=>o&&o.part).map(o=>o.part))].slice(0,2).join(', ');
      const after=data.plan.assignments.filter(x=>x!==selected&&x.segments.length&&taskOrders(x).includes(oid)&&
        ms(assignmentStart(x))>=ms(assignmentEnd(selected))).sort((x,y)=>ms(assignmentStart(x))-ms(assignmentStart(y)))[0];
      const lateOrder=number(result.tardiness_seconds)>0;
      return '<div class="order-card"><div class="order-card-head"><strong>'+esc(orderCode(oid))+'</strong>'+
        (qty?'<small> · '+esc(qty.toLocaleString('it-IT'))+' pz</small>':'')+
        '<span class="status-pill '+(lateOrder?'bad':'')+'">Consegna '+esc(fmt(result.due,'date'))+'</span></div>'+
        '<p>'+esc([orderCustomer(oid),part].filter(Boolean).join(' · ')||'Cliente non indicato nella fonte')+'</p>'+
        (after?'<button type="button" class="link-button next-step" data-task="'+esc(after.task_id)+'">↳ '+
          esc((opInfo(after.operation_ids[0])||{}).phase||taskLabel(after))+' '+
          esc(after.demands.filter(d=>!/^OP:/.test(d.resource_id)).map(d=>resourceCode(d.resource_id)).join(', '))+' · '+
          esc(fmt(assignmentStart(after),'day'))+' '+esc(fmt(assignmentStart(after),'time'))+'</button>':
          '<small class="muted-line">'+(lateOrder?'Ultima fase: fine '+esc(fmt(result.end))+', oltre la consegna':'Ultima fase della commessa')+'</small>')+'</div>';
    }).join('');
    const article=nest?'':[...new Set(selected.operation_ids.map(id=>opInfo(id)).filter(o=>o&&o.part).map(o=>esc(o.part+(o.part_description?' · '+o.part_description:''))))].slice(0,3).join('<br>');
    const lots=nest?'':[...new Set(selected.operation_ids.map(id=>opInfo(id)).filter(o=>o&&o.phase).map(o=>(o.lot!==undefined?o.lot+' · ':'')+o.phase))].slice(0,3).map(esc).join('<br>');
    const dues=[...new Set(selected.operation_ids.map(id=>ops.get(id)&&ops.get(id).due).filter(Boolean).map(ms))].filter(Number.isFinite).sort((x,y)=>x-y);
    const previous=[...new Set(selected.operation_ids.flatMap(id=>((ops.get(id)||{}).predecessors||[]).map(p=>p.predecessor_id)).filter(id=>!selected.operation_ids.includes(id)).map(id=>{const i=opInfo(id);return i&&i.phase||id;}))];
    const recorded=selected.operation_ids.map(id=>evidence().progress.get(id)).filter(r=>r&&(r.recorded_work_seconds!==null&&r.recorded_work_seconds!==undefined));
    const parts=unitParts([selected]), shortParts=parts.filter(p=>p.missing>0);
    const supplies=[...new Map(parts.flatMap(p=>p.ordered).map(o=>[o.supply.id,o])).values()];
    const arrivals=supplies.map(o=>ms(o.supply.available_at)).filter(Number.isFinite).sort((x,y)=>x-y);
    const materialKey=material?'<span class="status-pill '+(material==='short'?'bad':'')+'">'+(material==='short'?'Mancante':'Coperto')+'</span> '+
      esc(parts.length?parts.length+(parts.length===1?' parte':' parti')+(shortParts.length?' · '+shortParts.length+' mancanti':'')+(arrivals.length?' · ordine in arrivo '+fmt(arrivals[0],'date'):''):
        material==='short'?shortageText(evidence().short.get(selected.task_id)):'lotto assegnato')+
      ' <button type="button" class="mat-link" data-material-task="'+esc(selected.task_id)+'">vedi dettaglio</button>':'';
    const supplySection=supplies.length||shortParts.length?'<div class="detail-section supply-orders"><h3>ORDINE FORNITORE</h3>'+
      supplies.map(o=>supplyOrderCard(o.supply,o.used)).join('')+
      (shortParts.some(p=>!p.ordered.length)?'<div class="supply-alert danger"><span class="supply-alert-icon" aria-hidden="true">⚠</span>Parti mancanti senza ordine a fornitore collegato</div>':'')+'</div>':'';
    const detail='<div class="detail-badges"><span class="badge fam-'+phaseFamily(selected)+'">'+esc(family)+'</span>'+
      '<span class="badge '+(late?'bad':done!==null?'run':'')+'">'+(late?'Oltre la consegna':done!==null?'In corso':'Pianificata')+'</span>'+
      (selected.fixed?'<span class="badge lock">🔒 Confermata</span>':'')+'</div>'+
      '<p class="detail-where">'+esc(machines.concat(people).join(' · ')||'Nessuna risorsa (storico o esterno)')+'</p>'+
      requiredResourcesSection([selected])+
      '<div class="detail-section kv">'+
      (nest?key('Lamiera',esc([nest.sheet,nest.thickness_mm?'sp. '+nest.thickness_mm+' mm':'',nest.size_mm?nest.size_mm.replace(/(\d+)(\.\d+)?/g,'$1')+' mm':''].filter(Boolean).join(' · '))):'')+
      (nest?key('Prelievo',esc([nest.warehouse?'Mag. '+nest.warehouse:'',nest.location&&nest.location!==nest.warehouse?'ubic. '+nest.location:'',nest.sheet_part].filter(Boolean).join(' · '))):'')+
      (nest&&nest.used_pct?key('Utilizzo lamiera',esc(Number(nest.used_pct).toLocaleString('it-IT',{maximumFractionDigits:1})+' %')):'')+
      key('Articolo',article)+key('Lotto / fase',lots)+
      key('Fornitura',nest?'Interna':phaseFamily(selected)==='ext'?'Esterna':'Interna')+
      key('Pianificato',esc(fmt(assignmentStart(selected)))+' → '+esc(fmt(assignmentEnd(selected),
        iso(assignmentStart(selected))===iso(assignmentEnd(selected))?'time':undefined)))+
      key('Modalità applicata',esc(modeLabel(selected.effective_mode)))+
      key('Modalità richiesta',esc(modeLabel(selected.requested_mode)))+
      (dues.length?key('Consegna',esc(fmt(dues[0],'date'))+(dues.length>1?' <small>(prima di '+dues.length+' date)</small>':'')):'')+
      (phaseFamily(selected)==='ext'&&selected.external_end?key('Rientro esterno',esc(fmt(selected.external_end,'date'))):'')+
      key(nest?'Tempo taglio':'Tempo prenotato','<strong>'+esc(hrs(duration))+'</strong>'+(nest?' · contato una volta':'')+
        (selected.segments.length>1?' · '+selected.segments.length+' frazioni':''))+
      (pieces.length&&!nest?key('Quantità',esc(pieces.reduce((s,o)=>s+Number(o.quantity),0).toLocaleString('it-IT'))+' pz'):'')+
      (produced.length?key('Avanzamento',esc(produced.reduce((s,r)=>s+Number(r.produced_quantity),0).toLocaleString('it-IT'))+
        (pieces.length?' / '+esc(pieces.reduce((s,o)=>s+Number(o.quantity),0).toLocaleString('it-IT')):'')+' pz versati (TRD)'):'')+
      (recorded.length?key('Ore effettive',esc(hrs(recorded.reduce((s,r)=>s+number(r.recorded_work_seconds)+number(r.recorded_setup_seconds),0)))+' · registrate (TRD)'):'')+
      (previous.length?key('Fase precedente',previous.slice(0,3).map(esc).join('<br>')):'')+
      key('Materiale',materialKey||'Nessuna parte d’acquisto collegata')+
      '</div>'+supplySection+
      (orderCards?'<div class="detail-section"><h3>COMMESSE COLLEGATE ('+related.length+')</h3>'+orderCards+'</div>':'')+
      '<details class="detail-section raw"><summary>Identificativi tecnici</summary>'+
      '<div class="detail-key"><span>Task</span><span>'+esc(selected.task_id)+'</span></div>'+
      selected.operation_ids.map(oid=>{
        const op=ops.get(oid),info=opInfo(oid);
        return '<div class="detail-key"><span>'+esc(oid)+(info&&info.phase?' · '+esc(info.phase):'')+'</span><span>Setup '+esc(hrs(op&&op.setup_seconds))+' · Run '+esc(hrs(op&&op.duration_seconds))+'</span></div>';
      }).join('')+'</details>'+
      explainSection(selected)+
      inspectorEditor(selected)+
      (selected.explanation&&selected.explanation.length?
        '<div class="detail-related"><strong>Motivazione del motore</strong><br>'+esc(selected.explanation.slice(0,5).join(' · '))+'</div>':
        '<div class="detail-related">Dettaglio derivato dal risultato verificato del motore. Nessuna prenotazione ERP è stata modificata.</div>');
    root.innerHTML=detail;
  }
  function requiredResources(a) {
    const ops=operationsById(),chosen=new Set(a.demands.map(d=>d.resource_id)),seen=new Map();
    for(const id of a.operation_ids)
      for(const alt of ((ops.get(id)||{}).alternatives||[]))
        for(const d of alt.demands)if(!seen.has(d.resource_id))seen.set(d.resource_id,d.units);
    for(const d of a.demands)if(!seen.has(d.resource_id))seen.set(d.resource_id,d.units);
    const list=[...seen].map(([id,units])=>({id,units,chosen:chosen.has(id)}));
    return {machines:list.filter(r=>/^MAC:/.test(r.id)),people:list.filter(r=>/^OP:/.test(r.id)),
      suppliers:list.filter(r=>/^SUP:/.test(r.id))};
  }
  function resourceList(title,list) {
    if(!list.length)return '';
    return '<div class="req-res-group"><span class="req-res-kind">'+esc(title)+'</span>'+list.map(r=>
      '<div class="req-item'+(r.chosen?' chosen':'')+'"><strong>'+esc(resourceCode(r.id))+'</strong>'+
      (resourceName(r.id)?' · '+esc(resourceName(r.id)):'')+(r.units>1?' ×'+r.units:'')+
      (r.chosen?' <span class="req-pick">✓ scelta dal piano</span>':'')+'</div>').join('')+'</div>';
  }
  function requiredResourcesSection(tasks) {
    const groups=new Map();
    for(const a of tasks){
      const r=requiredResources(a),nest=nestInfo(a),isNest=!!nestId(a),info=opInfo(a.operation_ids[0])||{};
      const phase=[info.phase_code,info.phase&&info.phase!==info.phase_code?info.phase:''].filter(Boolean).join(' · ');
      const cut=isNest||phaseFamily(a)==='cut',src=isNest?(nest||{}):info;
      const material=cut?[src.sheet||src.material,src.thickness_mm?'sp. '+src.thickness_mm+' mm':''].filter(Boolean).join(' · '):'';
      const names=isNest?[(nest&&nest.code)||nestId(a)]:
        [...new Set(a.operation_ids.map(id=>opInfo(id)).filter(Boolean).map(o=>[o.part,o.part_description].filter(Boolean).join(' · ')).filter(Boolean))];
      const sig=[isNest?'nest':'job',phase,material,r.machines.map(x=>x.id+x.chosen).join(),
        r.people.map(x=>x.id+x.chosen).join(),r.suppliers.map(x=>x.id+x.chosen).join()].join('|');
      if(!groups.has(sig))groups.set(sig,{isNest,phase,material,r,names:[],count:0});
      const g=groups.get(sig);g.count++;
      for(const n of names)if(!g.names.includes(n))g.names.push(n);
    }
    const rows=[...groups.values()].map(g=>
      '<div class="req-res-row"><div class="req-res-head"><strong>'+esc(g.isNest?'Nesting'+(g.phase?' · '+g.phase:''):g.phase||'Fase')+'</strong>'+
      (g.count>1?'<span class="status-pill neutral">× '+g.count+'</span>':'')+'</div>'+
      (g.names.length?'<small class="muted-line">'+esc(g.names.slice(0,4).join(' ; ')+(g.names.length>4?' +'+(g.names.length-4):''))+'</small>':'')+
      (g.material?'<div class="detail-key"><span>Materiale e spessore</span><span>'+esc(g.material)+'</span></div>':'')+
      resourceList('Macchine',g.r.machines)+resourceList('Operatori ammessi',g.r.people)+resourceList('Fornitori (lavorazione esterna)',g.r.suppliers)+
      (g.r.people.length||g.r.suppliers.length?'':'<small class="muted-line">Nessun operatore richiesto.</small>')+'</div>').join('');
    return '<details class="detail-section req-res"><summary>Risorse richieste ('+groups.size+(groups.size===1?' voce':' voci')+')</summary>'+
      '<small class="muted-line">✓ = scelta dal piano; le altre sono alternative ammesse.</small>'+rows+'</details>';
  }
  // Riepilogo di una commessa nella barra laterale: consegne, periodo, fasi per tipo ed elenco cliccabile delle fasi.
  function orderInspector(oid) {
    const o = data.plan.orders.find(x=>x.order_id===oid)||{};
    const ops = operationsById();
    const as = data.plan.assignments.filter(a=>taskOrders(a).includes(oid)).sort((a,b)=>ms(assignmentStart(a))-ms(assignmentStart(b)));
    const placed = as.filter(a=>a.segments.length);
    const dues = new Set();
    for (const a of as) for (const id of a.operation_ids) { const op = ops.get(id); if (op && op.order_id===oid && op.due) dues.add(ms(op.due)); }
    const dueList = [...dues].filter(Number.isFinite).sort((x,y)=>x-y);
    const late = number(o.tardiness_seconds)>0;
    const key = (k,v) => v?'<div class="detail-key"><span>'+esc(k)+'</span><span>'+v+'</span></div>':'';
    const families = {cut:'Taglio / nesting',bend:'Piegatura',finish:'Altre lavorazioni',ext:'Esterno'};
    const counts = {};
    for (const a of as) counts[phaseFamily(a)] = (counts[phaseFamily(a)]||0)+1;
    const nShort = as.filter(a=>materialState(a)==='short').length, gm = groupMaterial(as);
    const lateTasks = placed.filter(isLateTask).length;
    const hours = placed.reduce((s,a)=>s+a.segments.reduce((t,x)=>t+(ms(x.end)-ms(x.start))/1000,0),0);
    const rows = as.slice(0,60).map(a => {
      const op = nestId(a)?null:opInfo(a.operation_ids.find(id=>(ops.get(id)||{}).order_id===oid)||a.operation_ids[0]);
      return '<button type="button" class="gx-phase-row" data-task="'+esc(a.task_id)+'"><span class="gx-dot fam-'+phaseFamily(a)+'"></span><span><strong>'+
        esc(short(nestId(a)?taskTitle(a):(op&&op.phase||taskLabel(a)),28))+'</strong><small>'+
        esc(modeLabel(a.effective_mode))+' · '+esc(a.demands.filter(d=>!/^OP:/.test(d.resource_id)).map(d=>resourceCode(d.resource_id)).join(', ')||'senza risorsa')+
        (a.segments.length?' · '+esc(fmt(assignmentStart(a),'day'))+' '+esc(fmt(assignmentStart(a),'time')):' · non collocata')+
        '</small></span>'+
        (isLateTask(a)?'<span class="status-pill bad">oltre</span>':'')+'</button>';
    }).join('');
    return '<div class="detail-badges"><span class="badge '+(late?'bad':'')+'">'+(late?'Ritardo '+esc(gap(o.tardiness_seconds)):o.complete?'Pianificata':'Incompleta')+'</span></div>'+
      '<p class="detail-where">'+esc(orderCustomer(oid)||'Cliente non indicato nella fonte')+'</p>'+
      (as.length?requiredResourcesSection(as.slice(0,60)):'')+
      '<div class="detail-section kv">'+
      key('Consegna',dueList.length?esc(fmt(dueList[0],'date'))+(dueList.length>1?' <small>(prima di '+dueList.length+' date)</small>':''):esc(fmt(o.due,'date')))+
      (dueList.length>1?key('Ultima consegna',esc(fmt(dueList[dueList.length-1],'date'))):'')+
      (placed.length?key('Pianificato',esc(fmt(assignmentStart(placed[0])))+' → '+esc(fmt(o.end||Math.max(...placed.map(a=>ms(assignmentEnd(a))))))):'')+
      key('Modalità applicata',esc(appliedModeLabel(as)))+
      key('Strategia configurata',esc(modeLabel(data.scenario.settings.mode)))+
      key('Fasi',as.length+' · '+placed.length+' collocate')+
      key('Tempo prenotato','<strong>'+esc(hrs(hours))+'</strong>')+
      key('Per tipo',esc(Object.keys(counts).map(k=>counts[k]+' '+families[k].toLowerCase()).join(' · ')))+
      (lateTasks?key('Fasi oltre consegna','<span class="status-pill bad">'+lateTasks+'</span>'):'')+
      (gm?key('Materiale','<span class="status-pill '+(nShort?'bad':'')+'">'+(nShort?nShort+' fasi con materiale mancante':'coperto')+'</span> '+
        '<button type="button" class="mat-link" data-material-order="'+esc(oid)+'">vedi dettaglio</button>'):'')+
      '</div>'+
      '<div class="detail-section"><h3>FASI ('+as.length+')</h3><div class="gx-phase-list">'+rows+'</div>'+(as.length>60?'<small class="muted-line">Mostrate le prime 60 fasi.</small>':'')+'</div>'+
      '<div class="detail-section"><h3>PERCHÉ FINISCE QUI</h3>'+explainBody(oid,null)+'</div>';
  }
  function explainSection(a) {
    const order=taskOrders(a)[0];
    if(!order)return '';
    return '<div class="detail-section"><h3>PERCHÉ FINISCE QUI</h3>'+explainBody(order,a)+'</div>';
  }
  // Percorso critico della commessa; con una fase selezionata la evidenzia nell'elenco.
  function explainBody(order,a) {
    const e=state.explain,digest=data.plan.scenario_digest;
    let body='<button type="button" class="btn btn-light btn-small" data-explain-order="'+esc(order)+'">Mostra il percorso critico di '+esc(orderCode(order))+'</button>';
    if(e&&e.order===order&&e.digest===digest){
      if(e.pending)body='<p class="panel-sub">Ricostruzione della catena dei vincoli…</p>';
      else if(e.error)body='<p class="panel-sub">'+esc(e.error)+'</p>';
      else body='<p class="explain-summary">'+esc(e.result.summary)+'</p><ol class="critical-path">'+
        e.result.critical_path.slice(0,8).map(step=>'<li class="'+(a&&step.unit===a.task_id?'current':'')+'"><strong>'+esc(taskLabel({task_id:step.unit}))+'</strong>'+
          '<span>'+esc(step.text)+'</span><small>'+esc(fmt(step.start))+' → '+esc(fmt(step.end))+'</small></li>').join('')+'</ol>'+
        '<button type="button" class="btn btn-light btn-small" data-chat-explain="'+esc(order)+'">Chiedi all’assistente</button>';
    }
    return body;
  }
  async function explainOrder(order) {
    const digest=data.plan.scenario_digest;
    state.explain={order,digest,pending:true};updateInspector();
    let next;
    try{next={order,digest,result:await postAnalysis('/api/explain',{scenario_digest:digest,order_id:order})};}
    catch(error){next={order,digest,error:error.code==='plan_not_indexed'?'Piano non più in memoria del server: ricalcola per analizzarlo.':error.message};}
    if(state.explain&&state.explain.order===order&&state.explain.digest===digest){state.explain=next;updateInspector();}
  }
  function simulationComparison() {
    if(!hasWhatif()||!state.baselinePlan)return '';
    const before=state.baselinePlan,after=data.plan;
    const totalLate=(p)=>p.orders.reduce((s,o)=>s+Math.max(0,number(o.tardiness_seconds)),0);
    const diff=(a,b)=>a===b?'0':((b-a)>0?'+':'')+(b-a).toLocaleString('it-IT');
    return '<article class="comparison-card"><div class="compare-icon">◇</div>'+
      '<div><strong>Scenario what-if</strong><p>'+state.edits.fixed_tasks.length+' lavorazioni fissate · '+
      state.edits.closures.length+' fermi simulati. Nessuna modifica ERP.</p></div>'+
      '<div class="comparison-metrics">'+
      '<div><small>Δ non collocate</small><strong>'+esc(diff(before.unscheduled.length,after.unscheduled.length))+'</strong></div>'+
      '<div><small>Δ ritardo totale</small><strong>'+esc(hrs(totalLate(after)-totalLate(before)))+'</strong></div>'+
      '<div><small>Δ commesse in ritardo</small><strong>'+esc(diff(
        before.orders.filter(o=>o.tardiness_seconds>0).length,
        after.orders.filter(o=>o.tardiness_seconds>0).length))+'</strong></div>'+
      '</div></article>';
  }
  function updateSourceWarnings() {
    const notice=$('#notice');
    if(!notice)return;
    const message=notice.querySelector('span:nth-child(2)');
    if(!message)return;
    const sourceBlocked=data.meta&&data.meta.source_publishable===false;
    notice.classList.toggle('notice-blocked',sourceBlocked);
    if(sourceBlocked) {
      const issues=data.meta.canonical&&data.meta.canonical.issues||[];
      const blocks=issues.filter(x=>x.severity==='block').length;
      message.textContent='Attenzione: il dataset canonico presenta '+blocks+
        ' blocchi di qualità/materiali. La simulazione è consultabile, ma NON è pubblicabile e non va usata come piano operativo.';
      const switched=data.meta.company_memory&&data.meta.company_memory.material_constraint;
      if(switched&&switched.ignored)message.textContent+=' Vincolo materiale spento dalla memoria aziendale ('+switched.rules.join(', ')+
        '): il piano non attende il materiale ('+switched.operations_released+' fasi liberate). Revoca la regola per riaccenderlo.';
    } else if(data.meta&&data.meta.dataset==='Scenario di esempio') {
      message.textContent='Scenario di esempio sintetico: questo piano non è collegato al gestionale Factory e non può essere pubblicato.';
    } else {
      message.textContent='Piano tecnico calcolato da file ('+(data.meta&&data.meta.file_name||'Scenario')+
        '): non è collegato al gestionale Factory e non viene pubblicato.';
    }
  }
  function renderExtra() {
    const title=viewTitle[state.view];
    document.body.classList.remove('wide-view');
    $('#summary-section').hidden=true;
    $('#summary-cards').hidden=true;
    $('#page-title').textContent=title[0];
    $('#page-subtitle').textContent=title[1];
    $('#page-eyebrow').textContent=state.view.startsWith('dati-')?'DATI IN INGRESSO':'AMMINISTRAZIONE';
    $('#reset-button').hidden=true;
    if(data){
      $('#snapshot-label').textContent=data.scenario.id;
      $('#scenario-hash').textContent='SHA '+short(data.plan.scenario_digest,14);
    }
    extraViews.mount(state.view,$('#view-root'),{setToast,customerName,orderCode,resourceTitle,orderLabel:(id)=>[orderCode(id),orderCustomer(id)].filter(Boolean).join(' · ')});
    syncLayoutControls();
  }
  function render() {
    if(isExtra(state.view)){renderExtra();return;}
    if(extraViews)extraViews.unmount();
    $('#summary-section').hidden=false;
    $('#summary-cards').hidden=false;
    if(!data) {
      $('#view-root').innerHTML='<div class="skeleton"><span></span><span></span><span></span><p>Calcolo del piano in corso… le sezioni Dati e Amministrazione sono già consultabili.</p></div>';
      return;
    }
    $('#summary-cards').hidden=false;
    topCards();setHeading();updateSourceWarnings();decorateHeader();
    const root=$('#view-root');
    const renderers={machines:renderMachines,orders:renderOrders,nesting:renderNesting,loads:renderLoads,
      materials:renderMaterials,progress:renderProgress,staged:renderStaged,deliveries:renderDeliveries,calendars:renderCalendars,daily:renderDaily,
      parameters:renderParameters,bottlenecks:renderBottlenecks,assistant:renderAssistant,memory:renderMemory};
    // Only the machine plan and the daily programme keep the detail column; elsewhere it opens as a drawer.
    const wide=!['machines','daily'].includes(state.view);
    document.body.classList.toggle('wide-view',wide);
    const drawer=wide||window.innerWidth<=1100;
    $('#inspector').classList.toggle('open', drawer&&layout.inspectorDrawerOpen);
    document.body.classList.toggle('inspector-collapsed',layout.inspectorCollapsed);
    root.innerHTML=simulationComparison()+renderers[state.view]();
    updateInspector();renderChat();syncLayoutControls();
  }

  async function recalculate(settings) {
    if(state.archived||state.sessionRole==='reparto') {
      setToast('Questa vista è in sola lettura. Riapri la fonte per pianificare.');return false;
    }
    if(state.busy)return false;
    state.busy=true;
    const btn=$('#calculate-button');
    btn.disabled=true;btn.textContent='Calcolo in corso…';
    const simulated=hasWhatif();
    try {
      const response=await fetch(state.archiveBase?'/api/snapshots/preview':simulated?'/api/simulate':'/api/recalculate',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify(state.archiveBase?{...state.archiveBase,settings:settings||state.settings,edits:state.edits}:
            simulated?{settings:settings||state.settings,edits:state.edits}:{settings:settings||state.settings})
      });
      const output=await response.json();
      if(!response.ok) throw new Error(output.error||'Piano non valido');
      data=output;
      // The delivery margin is not an engine setting: it comes back only if this request carried it.
      const sent=settings||state.settings;
      state.settings={...data.scenario.settings,...(sent.delivery_buffer_seconds!==undefined&&!state.archiveBase?{delivery_buffer_seconds:sent.delivery_buffer_seconds}:{})};
      if(!simulated&&!state.archiveBase)document.dispatchEvent(new CustomEvent('pf:plan-recalculated'));
      render();
      setToast((simulated?'Scenario what-if':'Piano')+
        ' ricalcolato e verificato. '+data.plan.unscheduled.length+
        ' unità non collocate. Nessun dato ERP modificato.');
      return true;
    } catch(error) {
      setToast('Calcolo non riuscito: '+error.message,7000);
      return false;
    } finally {
      state.busy=false;
      btn.disabled=false;
      btn.textContent='↻ Ricalcola piano';
    }
  }
  /* Conversational assistant: contextual side panel on every view, read-only.
   * Answers come from the server's AI when configured, otherwise from the
   * deterministic analyses below. Proposals are applied only as simulations. */
  const CHAT_KEY='pf-chat-v2', CHAT_LIMIT=40;
  /* Conversation history: kept in this browser (localStorage), one entry per conversation. The tab remembers
   * which one is open (sessionStorage), so a reload continues it; "Nuova chat" starts another and the previous
   * one stays in the history menu until deleted. */
  const HIST_KEY='pf-chat-history-v1', HIST_LIMIT=60, CHAT_ID_KEY='pf-chat-current';
  const validMessage=m=>m&&(m.role==='user'||m.role==='assistant')&&typeof m.text==='string';
  function histRead() {
    try {
      const list=JSON.parse(localStorage.getItem(HIST_KEY)||'[]');
      return Array.isArray(list)?list.filter(c=>c&&typeof c.id==='string'&&Array.isArray(c.messages)):[];
    } catch(error) { return []; }
  }
  function histWrite(list) {
    try { localStorage.setItem(HIST_KEY,JSON.stringify(list.slice(0,HIST_LIMIT))); } catch(error) { /* storage is a convenience */ }
  }
  function chatLoad() {
    try {
      const id=sessionStorage.getItem(CHAT_ID_KEY),found=id&&histRead().find(c=>c.id===id);
      if(found){state.chat.id=found.id;state.chat.messages=found.messages.filter(validMessage).slice(-CHAT_LIMIT);return;}
      const legacy=JSON.parse(sessionStorage.getItem(CHAT_KEY)||'[]');
      if(Array.isArray(legacy))state.chat.messages=legacy.filter(validMessage).slice(-CHAT_LIMIT);
    } catch(error) { state.chat.messages=[]; }
  }
  function chatSave() {
    const messages=state.chat.messages.filter(m=>!m.welcome).slice(-CHAT_LIMIT);
    if(!messages.length)return;
    if(!state.chat.id)state.chat.id='c'+Date.now().toString(36)+Math.random().toString(36).slice(2,6);
    const first=messages.find(m=>m.role==='user');
    // Re-read before writing: another tab may have added or deleted conversations meanwhile.
    const list=histRead().filter(c=>c.id!==state.chat.id);
    list.unshift({id:state.chat.id,title:short(first?first.text:'Conversazione',70),updated:Date.now(),messages});
    histWrite(list);
    try { sessionStorage.setItem(CHAT_ID_KEY,state.chat.id);sessionStorage.removeItem(CHAT_KEY); } catch(error) { /* idem */ }
  }
  function newConversation() {
    if(state.chat.busy)return;
    stopSpeaking();
    state.chat.id=null;state.chat.messages=[];state.chat.menu=null;
    try { sessionStorage.removeItem(CHAT_ID_KEY);sessionStorage.removeItem(CHAT_KEY); } catch(error) { /* idem */ }
    renderChatMenu();renderChat();
  }
  function openConversation(id) {
    if(state.chat.busy)return;
    const found=histRead().find(c=>c.id===id);
    if(!found)return;
    stopSpeaking();
    state.chat.id=found.id;state.chat.messages=found.messages.filter(validMessage).slice(-CHAT_LIMIT);
    try { sessionStorage.setItem(CHAT_ID_KEY,found.id); } catch(error) { /* idem */ }
    state.chat.menu=null;renderChatMenu();renderChat();
  }
  function deleteConversations(ids) {
    if(!ids.length||state.chat.busy)return;
    const label=ids.length===1?'questa conversazione':'le '+ids.length+' conversazioni selezionate';
    if(!window.confirm('Eliminare '+label+'? L’operazione non si può annullare.'))return;
    const gone=new Set(ids);
    histWrite(histRead().filter(c=>!gone.has(c.id)));
    state.chat.checked=new Set([...state.chat.checked].filter(id=>!gone.has(id)));
    if(gone.has(state.chat.id)){
      state.chat.id=null;state.chat.messages=[];
      try { sessionStorage.removeItem(CHAT_ID_KEY); } catch(error) { /* idem */ }
      stopSpeaking();renderChat();
    }
    renderHistoryList();
    setToast(ids.length===1?'Conversazione eliminata.':ids.length+' conversazioni eliminate.');
  }
  function renderHistoryList() {
    const list=$('#chat-hist-list');
    if(!list)return;
    const all=histRead(),query=($('#chat-hist-search').value||'').trim().toLocaleLowerCase('it-IT');
    const has=(c)=>c.title.toLocaleLowerCase('it-IT').includes(query)||c.messages.some(m=>String(m.text).toLocaleLowerCase('it-IT').includes(query));
    const shown=query?all.filter(has):all;
    const checked=state.chat.checked;
    for(const id of [...checked])if(!all.some(c=>c.id===id))checked.delete(id);
    list.innerHTML=shown.length?shown.map(c=>{
      const when=new Date(c.updated).toLocaleString('it-IT',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
      const count=c.messages.filter(m=>m.role==='user').length;
      return '<div class="chat-hist-row'+(c.id===state.chat.id?' current':'')+'">'+
        '<input type="checkbox" data-hist-check="'+esc(c.id)+'" aria-label="Seleziona '+esc(c.title)+'"'+(checked.has(c.id)?' checked':'')+'>'+
        '<button type="button" class="chat-hist-open" data-hist-open="'+esc(c.id)+'"><strong>'+esc(c.title)+'</strong>'+
        '<small>'+esc(when)+' · '+count+(count===1?' domanda':' domande')+(c.id===state.chat.id?' · aperta':'')+'</small></button>'+
        '<button type="button" class="chat-hist-del" data-hist-del="'+esc(c.id)+'" aria-label="Elimina la conversazione" title="Elimina">🗑</button></div>';
    }).join(''):'<p class="chat-hist-empty">'+(all.length?'Nessuna conversazione corrisponde alla ricerca.':'Nessuna conversazione salvata: compare qui dopo la prima domanda.')+'</p>';
    const all$=$('#chat-hist-all'),del=$('#chat-hist-delete');
    all$.disabled=!shown.length;
    all$.checked=Boolean(shown.length)&&shown.every(c=>checked.has(c.id));
    del.disabled=!checked.size;
    del.textContent='🗑 Elimina'+(checked.size?' ('+checked.size+')':'');
    $('#chat-hist-count').textContent=all.length+(all.length===1?' conversazione':' conversazioni');
  }

  /* Voice: dictation (browser speech recognition) and read-aloud (speech synthesis). Both are optional
   * conveniences of the browser; settings live in this browser only. */
  const VOICE_KEY='pf-chat-voice-v1';
  const voice={auto:false,send:false,rate:1,uri:''};
  const synth=window.speechSynthesis||null;
  const Recognition=window.SpeechRecognition||window.webkitSpeechRecognition||null;
  try { const saved=JSON.parse(localStorage.getItem(VOICE_KEY)||'{}');
    if(saved&&typeof saved==='object')Object.assign(voice,{auto:Boolean(saved.auto),send:Boolean(saved.send),
      rate:Math.min(1.5,Math.max(0.7,Number(saved.rate)||1)),uri:typeof saved.uri==='string'?saved.uri:''}); } catch(error) { /* defaults */ }
  function voiceSave() {
    try { localStorage.setItem(VOICE_KEY,JSON.stringify(voice)); } catch(error) { /* storage is a convenience */ }
  }
  function plainSpeech(text) {
    return String(text).replace(/\*\*/g,'').replace(/^\s*(#+|[-•*])\s+/gm,'').replace(/[ \t]+/g,' ').trim();
  }
  function pickVoice() {
    const voices=synth?synth.getVoices():[];
    return voices.find(v=>v.voiceURI===voice.uri)||voices.find(v=>/^it/i.test(v.lang))||null;
  }
  function stopSpeaking() {
    state.chat.utterance=null;state.chat.speaking=null;
    if(synth)synth.cancel();
  }
  function speak(text,index) {
    if(!synth||!String(text||'').trim())return;
    synth.cancel();
    const u=new SpeechSynthesisUtterance(plainSpeech(text).slice(0,4000)),v=pickVoice();
    if(v){u.voice=v;u.lang=v.lang;}else u.lang='it-IT';
    u.rate=voice.rate;
    state.chat.utterance=u;state.chat.speaking=index===undefined?-1:index;
    // The cancelled utterance reports its end after the new one has started: only the current one counts.
    u.onend=u.onerror=()=>{if(state.chat.utterance===u){state.chat.utterance=null;state.chat.speaking=null;renderChat();}};
    synth.speak(u);
    renderChat();
  }
  let recognizer=null;
  function syncMic() {
    const mic=$('#chat-mic');
    if(!mic)return;
    mic.hidden=!Recognition;
    mic.classList.toggle('listening',Boolean(recognizer));
    mic.setAttribute('aria-pressed',String(Boolean(recognizer)));
    mic.textContent=recognizer?'⏹':'🎤';
    mic.title=recognizer?'Ferma la dettatura':'Detta la domanda a voce';
  }
  function toggleDictation() {
    if(!Recognition||state.chat.busy)return;
    if(recognizer){recognizer.stop();return;}
    stopSpeaking();renderChat();   // the microphone would pick up the assistant's voice
    const input=$('#chat-input'),rec=new Recognition(),base=input.value?input.value.replace(/\s+$/,'')+' ':'';
    let spoken='',failed=false;
    rec.lang='it-IT';rec.interimResults=true;rec.continuous=false;
    rec.onresult=e=>{
      let final='',interim='';
      for(let k=0;k<e.results.length;k++){const t=e.results[k][0].transcript;if(e.results[k].isFinal)final+=t;else interim+=t;}
      spoken=final.trim();
      input.value=(base+final+interim).slice(0,1500);
    };
    rec.onerror=e=>{
      failed=true;
      setToast(e.error==='not-allowed'||e.error==='service-not-allowed'?'Microfono non consentito: abilitalo per questo sito dalle impostazioni del browser.':
        e.error==='no-speech'?'Non ho sentito nulla: riprova.':e.error==='network'?'La dettatura usa il servizio vocale del browser e richiede la connessione.':
        'Dettatura non riuscita ('+e.error+').',6000);
    };
    rec.onend=()=>{
      recognizer=null;syncMic();
      if(voice.send&&spoken&&!failed&&!state.chat.busy){const q=input.value;input.value='';sendChat(q);}
      else input.focus();
    };
    try { rec.start();recognizer=rec; } catch(error) { setToast('Dettatura non avviata: '+error.message);recognizer=null; }
    syncMic();
  }
  function renderVoiceMenu() {
    const menu=$('#chat-menu');
    if(!synth&&!Recognition){menu.innerHTML='<p class="chat-hist-empty">Questo browser non offre dettatura né lettura vocale. Usa Chrome o Edge.</p>';return;}
    const voices=synth?synth.getVoices():[];
    const opt=v=>'<option value="'+esc(v.voiceURI)+'"'+(v.voiceURI===voice.uri?' selected':'')+'>'+esc(v.name+' · '+v.lang)+'</option>';
    const italian=voices.filter(v=>/^it/i.test(v.lang)),others=voices.filter(v=>!/^it/i.test(v.lang));
    menu.innerHTML='<div class="chat-menu-title"><strong>Voce</strong><small>Impostazioni di questo browser</small></div>'+
      (synth?'<label class="chat-opt"><input type="checkbox" data-voice="auto"'+(voice.auto?' checked':'')+'> Leggi ad alta voce ogni risposta</label>':'')+
      (Recognition?'<label class="chat-opt"><input type="checkbox" data-voice="send"'+(voice.send?' checked':'')+'> Invia subito la domanda dettata</label>':'')+
      (synth?'<label class="chat-opt col">Voce di lettura<select data-voice="uri"><option value="">Automatica (italiano)</option>'+
        (italian.length?'<optgroup label="Italiano">'+italian.map(opt).join('')+'</optgroup>':'')+
        (others.length?'<optgroup label="Altre lingue">'+others.map(opt).join('')+'</optgroup>':'')+'</select></label>'+
        '<label class="chat-opt col">Velocità: <output id="chat-rate-out">'+voice.rate.toFixed(1)+'×</output>'+
        '<input type="range" data-voice="rate" min="0.7" max="1.5" step="0.1" value="'+voice.rate+'"></label>'+
        '<div class="chat-menu-actions"><button type="button" class="btn btn-light btn-small" data-voice-test>▶ Prova la voce</button>'+
        '<button type="button" class="btn btn-light btn-small" data-voice-stop>■ Ferma la lettura</button></div>':'')+
      '<p class="chat-hint">'+(Recognition?'Dettatura: tocca 🎤 accanto a Invia. Usa il riconoscimento vocale del browser: serve il permesso del microfono e la connessione.':
        'La dettatura non è disponibile in questo browser (serve Chrome o Edge).')+(synth?' Su ogni risposta c’è il pulsante 🔊 Ascolta.':'')+'</p>';
  }
  function renderChatMenu() {
    const menu=$('#chat-menu');
    if(!menu)return;
    const kind=state.chat.open?state.chat.menu:null;
    menu.hidden=!kind;
    $('#chat-history-toggle').setAttribute('aria-expanded',String(kind==='history'));
    $('#chat-voice-toggle').setAttribute('aria-expanded',String(kind==='voice'));
    if(!kind){menu.innerHTML='';return;}
    menu.style.top=($('#chat-head').offsetHeight+$('#chat-context').offsetHeight)+'px';
    if(kind==='voice'){renderVoiceMenu();return;}
    menu.innerHTML='<div class="chat-menu-title"><strong>Cronologia</strong><small id="chat-hist-count"></small></div>'+
      '<input id="chat-hist-search" type="search" placeholder="Cerca nelle conversazioni" aria-label="Cerca nelle conversazioni" autocomplete="off">'+
      '<div class="chat-hist-bar"><label class="chat-opt"><input id="chat-hist-all" type="checkbox"> Seleziona tutte</label>'+
      '<button id="chat-hist-delete" type="button" class="btn btn-light btn-small chat-danger" disabled>🗑 Elimina</button></div>'+
      '<div id="chat-hist-list" class="chat-hist-list"></div>';
    renderHistoryList();
    $('#chat-hist-search').focus();
  }
  function toggleChatMenu(kind) {
    state.chat.menu=state.chat.menu===kind?null:kind;
    state.chat.checked=new Set();
    renderChatMenu();
  }
  function richText(text) {
    // Escape first, then allow only bold and simple lists: model output is never HTML.
    const lines=esc(text).replace(/\*\*([^*]+)\*\*/g,'<strong>$1</strong>').split(/\n/);
    let out='',list=false;
    for(const raw of lines){
      const line=raw.trim();
      const item=/^([-•*]|\d+[.)])\s+(.*)$/.exec(line);
      if(item){if(!list){out+='<ul>';list=true;}out+='<li>'+item[2]+'</li>';continue;}
      if(list){out+='</ul>';list=false;}
      if(line)out+='<p>'+line.replace(/^#+\s*/,'')+'</p>';
    }
    return out+(list?'</ul>':'');
  }
  function lateOrders() {
    return data.plan.orders.filter(o=>number(o.tardiness_seconds)>0).sort((a,b)=>number(b.tardiness_seconds)-number(a.tardiness_seconds));
  }
  function selectedOrder() {
    const a=state.selected&&data.plan.assignments.find(x=>x.task_id===state.selected);
    return a?taskOrders(a)[0]||null:null;
  }
  function chatContext() {
    if(!data)return 'Piano in calcolo…';
    const title=(viewTitle[state.view]||viewTitle.machines)[0];
    const bits=['Vista: '+title+' ('+state.view+')'];
    const a=state.selected&&data.plan.assignments.find(x=>x.task_id===state.selected);
    if(a)bits.push('fase '+a.task_id+(taskOrders(a).length?' (commessa '+taskOrders(a).map(orderCode).join(', ')+')':''));
    const sheet=isExtra(state.view)&&extraViews.context?extraViews.context():null;
    if(sheet)bits.push('tabella '+sheet.entity+(sheet.query?', ricerca "'+sheet.query+'"':'')+(sheet.status?', stato '+sheet.status:''));
    else {
      if(state.query)bits.push('ricerca "'+state.query+'"');
      if(['machines','daily','loads','calendars'].includes(state.view))bits.push('risorse '+state.resourceKind);
      if(state.view==='deliveries'&&state.delayCause)bits.push('causa '+state.delayCause);
      if(state.view==='deliveries'&&state.delayCustomer)bits.push('cliente '+(customerName(state.delayCustomer)||'')+' ('+state.delayCustomer+')');
      if(state.anchor)bits.push('finestra dal '+fmt(state.anchor,'day')+' per '+state.span+' gg');
    }
    if(hasWhatif())bits.push('simulazione attiva: '+state.edits.closures.length+' fermi, '+state.edits.fixed_tasks.length+' fasi fissate');
    if(state.archived)bits.push('revisione archiviata in sola lettura');
    return bits.join(' · ');
  }
  function uiState() {
    return {view:state.view,edits:state.edits,settings:state.settings,archived:Boolean(state.archived||state.archiveBase)};
  }
  function chatSuggestions() {
    if(!data)return [];
    const late=lateOrders()[0], order=selectedOrder();
    const why=(id)=>'Perché la commessa '+id+' finisce quando finisce?';
    const byView={
      machines:['Quali macchine sono più cariche?','Quali attività non sono pianificate?'],
      orders:['Quali commesse sono in ritardo?'],
      loads:['Quali macchine sono più cariche?'],
      deliveries:['Quali commesse sono in ritardo?','Quali attività non sono pianificate?'],
      materials:['Ci sono carenze di materiale?'],
      progress:['Quali consuntivi sono disponibili?'],
      memory:['Quali regole aziendali sono applicate?'],
      nesting:['Quali attività non sono pianificate?']
    };
    const list=[];
    if(order)list.push(why(order));
    if(late&&late.order_id!==order)list.push('Perché la commessa '+late.order_id+' è in ritardo?');
    list.push(...(byView[state.view]||['Quali commesse sono in ritardo?','Quali macchine sono più cariche?']));
    if(data.meta&&data.meta.llm_available&&state.sessionRole!=='reparto'){
      const busiest=data.plan.resources.filter(r=>r.utilization!==null).sort((a,b)=>b.utilization-a.utilization)[0];
      if(busiest)list.push('Simula un fermo di 4 ore di '+busiest.resource_id+' il primo giorno del piano');
    }
    return [...new Set(list)].slice(0,4);
  }
  function proposalLines(edits) {
    return (edits.closures||[]).map(c=>'Fermo '+c.resource_id+' · '+fmt(c.start)+' → '+fmt(c.end)+' · '+c.reason)
      .concat((edits.fixed_tasks||[]).map(f=>'Fissa '+f.task_id+' · inizio '+fmt(f.start)+' · alternativa '+f.alternative));
  }
  function regenerationNote() {
    const r=state.regeneration;
    if(!r||!r.state||r.state==='idle')return '';
    const last=(r.log_tail||[]).filter(Boolean).slice(-1)[0]||'';
    const head=r.state==='running'?'⟳ Rigenerazione dal '+isoText(r.start)+' in corso…':r.state==='done'?'✓ Fonte rigenerata dal '+isoText(r.start):
      '⚠ Rigenerazione dal '+isoText(r.start)+' non riuscita: '+(r.error||'');
    return '<div class="chat-msg assistant"><div class="bubble"><p><strong>'+esc(head)+'</strong></p>'+(last?'<small class="chat-source">'+esc(short(last,160))+'</small>':'')+'</div></div>';
  }
  function renderChat() {
    const panel=$('#chat-panel');
    if(!panel)return;
    panel.hidden=!state.chat.open;
    $('#chat-toggle').setAttribute('aria-expanded',String(state.chat.open));
    document.body.classList.toggle('chat-open',state.chat.open);
    syncLayoutControls();
    if(!state.chat.open)return;
    const connected=Boolean(data&&data.meta&&data.meta.llm_available);
    const acting=connected&&state.sessionRole&&state.sessionRole!=='reparto'&&!state.archived;
    $('#chat-mode').textContent=!connected?'Analisi deterministica · nessuna AI esterna':
      acting?'AI collegata · agisce sulla Control Room con il tuo ruolo · annullabile':'AI collegata · sola lettura per questo ruolo o vista';
    $('#chat-context').textContent=chatContext();
    const digest=data&&data.plan.scenario_digest;
    const messages=state.chat.messages.length?state.chat.messages:[{role:'assistant',text:
      'Posso spiegare ritardi, carichi, consegne, attività non collocate, materiali, consuntivi e regole del piano mostrato. '+
      'Per un ritardo ricostruisco il percorso critico della commessa.'+(acting?' Chiedimi anche di agire: apro schermate e filtri, '+
      'ricalcolo con altri parametri, simulo fermi e fasi fissate, gestisco regole, correzioni ai dati e revisioni. Ogni modifica salvata si annulla da qui.':
      connected?' Posso anche proporre simulazioni (fermi, fasi fissate) che applica un pianificatore.':''),welcome:true}];
    $('#chat-log').innerHTML=messages.map((m,i)=>{
      if(m.role==='user')return '<div class="chat-msg user"><div class="bubble">'+esc(m.text)+'</div></div>';
      const stale=m.digest&&digest&&m.digest!==digest;
      const facts=(m.facts||[]).length?'<ul class="chat-facts">'+m.facts.map(f=>'<li>'+esc(f)+'</li>').join('')+'</ul>':'';
      const proposals=(m.proposals||[]).map((p,j)=>{
        const done=p.state==='applied'?'<span class="status-pill">✓ Applicata come simulazione</span>':
          p.state==='dropped'?'<span class="status-pill neutral">Scartata</span>':
          !p.valid?'<span class="status-pill bad">Non applicabile: '+esc(short(p.error||'',140))+'</span>':
          (state.sessionRole==='reparto'||state.archived)?'<span class="status-pill neutral">Solo i pianificatori possono simulare</span>':
          '<div class="chat-proposal-actions"><button type="button" class="btn btn-primary btn-small" data-chat-apply="'+i+':'+j+'">Applica come simulazione</button>'+
          '<button type="button" class="btn btn-light btn-small" data-chat-drop="'+i+':'+j+'">Scarta</button></div>';
        return '<div class="chat-proposal"><strong>◇ '+esc(p.title)+'</strong><ul>'+proposalLines(p.edits).map(x=>'<li>'+esc(x)+'</li>').join('')+'</ul>'+done+'</div>';
      }).join('');
      const done=(m.done||[]).length?'<ul class="chat-done">'+m.done.map(d=>'<li>✓ '+esc(d)+'</li>').join('')+'</ul>':'';
      const undo=(m.undo||[]).map((u,j)=>u.state==='undone'?'<span class="status-pill neutral">Annullato: '+esc(u.label)+'</span>':
        u.state==='failed'?'<span class="status-pill bad">Non annullabile: '+esc(short(u.error||'',120))+'</span>':
        '<button type="button" class="btn btn-light btn-small chat-undo" data-chat-undo="'+i+':'+j+'">↶ Annulla · '+esc(u.label)+'</button>').join('');
      const speaking=state.chat.speaking===i;
      const tools=synth&&!m.welcome&&!m.error?'<div class="chat-msg-tools"><button type="button" class="chat-tool" data-chat-speak="'+i+'" aria-pressed="'+speaking+'">'+
        (speaking?'■ Ferma':'🔊 Ascolta')+'</button></div>':'';
      return '<div class="chat-msg assistant'+(m.error?' error':'')+'"><div class="bubble">'+richText(m.text)+done+
        (undo?'<div class="chat-proposal-actions">'+undo+'</div>':'')+facts+proposals+
        (m.source?'<small class="chat-source">'+esc(m.source)+(stale?' · riferita a un piano precedente':'')+'</small>':'')+tools+'</div></div>';
    }).join('')+regenerationNote()+(state.chat.busy?'<div class="chat-msg assistant"><div class="bubble typing" role="status"><span class="spinner" aria-hidden="true"></span>Sto pensando…</div></div>':'');
    const log=$('#chat-log');log.scrollTop=log.scrollHeight;
    const busy=state.chat.busy;
    $('#chat-suggestions').innerHTML=chatSuggestions().map((q,i)=>'<button type="button" class="assistant-chip" data-chat-suggestion="'+i+'"'+(busy?' disabled':'')+'>'+esc(q)+'</button>').join('');
    // While the assistant works the conversation is locked: only Stop is live.
    $('#chat-log').querySelectorAll('button').forEach(b=>{b.disabled=busy;});
    const input=$('#chat-input'),wasLocked=input.disabled;
    input.disabled=busy;
    if(wasLocked&&!busy)input.focus();
    $('#chat-send').hidden=busy;
    $('#chat-stop').hidden=!busy;
    $('#chat-mic').disabled=busy;
    syncMic();
  }
  function openChat(open=true) {
    state.chat.open=open;
    if(!open){state.chat.menu=null;if(recognizer)recognizer.abort();stopSpeaking();renderChatMenu();}
    renderChat();
    if(open){const input=$('#chat-input');if(input)input.focus();}
  }
  function findOrder(question) {
    const q=question.toLocaleLowerCase('it-IT');
    const hit=data.plan.orders.map(o=>o.order_id).filter(id=>q.includes(String(id).toLocaleLowerCase('it-IT')))
      .sort((a,b)=>String(b).length-String(a).length)[0];
    return hit||null;
  }
  async function postAnalysis(path,body) {
    const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const value=await response.json();
    if(!response.ok){const error=Error(value.error||'Analisi non disponibile');error.code=value.code;throw error;}
    return value;
  }
  function explainFacts(result) {
    return result.critical_path.slice(0,8).map(s=>s.unit+' · '+fmt(s.start)+' → '+fmt(s.end)+' · '+s.text+
      (s.capacity_wait_seconds?' (attesa '+hrs(s.capacity_wait_seconds)+')':''));
  }
  async function localAnswer(question) {
    const q=question.toLocaleLowerCase('it-IT');
    const digest=data.plan.scenario_digest;
    const order=findOrder(question)||(/quest|selezion/.test(q)?selectedOrder():null);
    if(order&&/perch|ritard|spieg|causa|critic|finisc|quando/.test(q)){
      const result=await postAnalysis('/api/explain',{scenario_digest:digest,order_id:order});
      return {text:'**Commessa '+order+'** · consegna '+fmt(result.due)+' · fine prevista '+fmt(result.end)+'\n'+result.summary+
        (result.unscheduled.length?'\n'+result.unscheduled.length+' fasi della commessa non sono collocate.':''),
        facts:explainFacts(result),source:'Percorso critico · '+result.method+' · SHA '+short(digest,12)};
    }
    if(order){
      const o=data.plan.orders.find(x=>x.order_id===order);
      return {text:'**Commessa '+order+'** · consegna '+fmt(o.due)+' · fine prevista '+fmt(o.end)+' · '+
        (number(o.tardiness_seconds)>0?'ritardo '+hrs(o.tardiness_seconds):o.complete?'nei termini':'non completata')+
        '\nChiedi "perché '+order+' è in ritardo?" per il percorso critico.',facts:[],source:'Piano calcolato · SHA '+short(digest,12)};
    }
    const answer=answerOperationalQuestion(question);
    return {text:'**'+answer.title+'**\n'+answer.text,facts:answer.facts,source:answer.source};
  }
  function chatHistory() {
    return state.chat.messages.filter(m=>!m.welcome&&!m.error&&m.text).slice(-20)
      .map(m=>({role:m.role,text:String(m.text).slice(0,4000)}));
  }
  let chatAbort=null;  // aborts the request of the question being answered (Stop button)
  async function remoteAnswer(question,history,signal) {
    const digest=data.plan.scenario_digest;
    const context='[Contesto interfaccia: '+chatContext()+']\n';
    const body={question:(context+question).slice(0,2000),history,evidence_id:data.meta.evidence_id,ui:uiState()};
    let response=await fetch('/api/assistant',{method:'POST',headers:{'Content-Type':'application/json'},signal,
      body:JSON.stringify({...body,scenario_digest:digest})});
    let answer=await response.json();
    if(response.status===409&&answer.code==='plan_not_indexed'){
      // Archived revisions are not held by the server: send the verified pair once.
      response=await fetch('/api/assistant',{method:'POST',headers:{'Content-Type':'application/json'},signal,
        body:JSON.stringify({...body,scenario:data.scenario,plan:data.plan})});
      answer=await response.json();
    }
    if(!response.ok)throw Error(answer.error||'AI non disponibile');
    const actions=answer.actions||[];
    if(answer.scenario_digest!==digest&&!actions.some(a=>a.type==='plan'))throw Error('Il piano è cambiato: ripeti la domanda.');
    return {text:answer.answer,facts:[],proposals:(answer.proposals||[]).map(p=>({...p,state:'open'})),actions,
      undo:(answer.undo||[]).map(u=>({...u,state:'open'})),
      source:'AI '+(answer.model||'')+' · da verificare · strumenti: '+(answer.tools_used.join(', ')||'nessuno')+' · SHA '+short(answer.scenario_digest||digest,12)};
  }
  /* What the assistant did, replayed in the interface in order: screens and filters change here, plans are
   * loaded through the usual endpoints (the server reuses the plan it has just verified). */
  async function runActions(actions) {
    const done=[];
    for(const a of actions||[]){
      try{
        if(a.type==='view'){setView(a.view);done.push('Aperta: '+(viewTitle[a.view]||[a.view])[0]);}
        else if(a.type==='filter'){
          if(a.entity&&extraViews&&extraViews.filter){
            extraViews.filter(a.entity,a.text,a.data_status);
            if(state.view!==a.view)setView(a.view);else render();
            done.push('Filtro su '+a.entity+(a.text?': "'+a.text+'"':''));
            continue;
          }
          if(a.view&&a.view!==state.view)setView(a.view);
          if(a.text!==undefined)state.query=a.text;
          if(a.resource_kind)state.resourceKind=a.resource_kind;
          if(a.delay_cause!==undefined)state.delayCause=a.delay_cause||null;
          if(a.customer_code!==undefined)state.delayCustomer=a.customer_code||null;
          state.showAll=false;render();
          done.push('Filtro'+(a.text?' "'+a.text+'"':'')+(a.customer_code?' cliente '+(customerName(a.customer_code)||a.customer_code):'')+
            (a.delay_cause?' causa '+((CAUSES[a.delay_cause]||[a.delay_cause])[0]):'')+(a.resource_kind?' risorse '+a.resource_kind:''));
        }
        else if(a.type==='focus'){
          if(a.order_id){if(state.view!=='orders')setView('orders');state.expanded.add(a.order_id);
            const first=data.plan.assignments.filter(x=>taskOrders(x).includes(a.order_id)).sort((x,y)=>ms(assignmentStart(x))-ms(assignmentStart(y)))[0];
            if(first&&!a.task_id)state.selected=first.task_id;}
          if(a.task_id)state.selected=a.task_id;
          const task=state.selected&&data.plan.assignments.find(x=>x.task_id===state.selected);
          if(a.day)state.anchor=atMidnight(new Date(a.day+'T12:00:00Z'));
          else if(task&&(a.task_id||a.order_id)&&assignmentStart(task))state.anchor=atMidnight(new Date(ms(assignmentStart(task))));
          if(a.span_days){state.span=Math.max(1,Math.min(MAX_RANGE_DAYS,Number(a.span_days)||1));state.mode=modeForSpan(state.span);}
          render();
          if(task)setInspectorOpen(true);
          done.push('Mostrato '+(a.order_id?'commessa '+orderCode(a.order_id):a.task_id?taskLabel({task_id:a.task_id}):'periodo dal '+fmt(state.anchor,'day')));
        }
        else if(a.type==='plan'){
          const before={edits:state.edits,settings:state.settings};
          state.edits={fixed_tasks:a.edits.fixed_tasks||[],closures:a.edits.closures||[]};
          state.settings={...state.settings,...a.settings};
          if(!await recalculate(state.settings)){Object.assign(state,before);render();done.push('Piano non caricato');}
          else done.push(hasWhatif()?'Simulazione applicata e verificata':'Piano ricalcolato e verificato');
        }
        else if(a.type==='rules'){if(state.view==='memory')render();done.push('Memoria aziendale aggiornata');}
        else if(a.type==='data'){if(extraViews&&extraViews.refresh)await extraViews.refresh();done.push('Dati in ingresso aggiornati');}
        else if(a.type==='revision'){state.savedRevision=a.revision;done.push('Revisione '+a.revision+' salvata');}
        else if(a.type==='job'){state.regeneration={state:'running',start:''};pollRegeneration();done.push('Rigenerazione avviata');}
      }catch(error){done.push('Azione non riuscita: '+error.message);}
    }
    return done;
  }
  async function undoAction(ref) {
    const [i,j]=ref.split(':').map(Number);
    const message=state.chat.messages[i],item=message&&message.undo&&message.undo[j];
    if(!item||item.state!=='open'||state.chat.busy)return;
    state.chat.busy=true;renderChat();
    try{
      const response=await fetch('/api/assistant/undo',{method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({token:item.token,ui:uiState()})});
      const result=await response.json();
      if(!response.ok)throw Error(result.error||'Annullamento non riuscito');
      item.state='undone';
      await runActions(result.actions);
      setToast('Annullato: '+result.undone);
    }catch(error){item.state='failed';item.error=error.message;setToast(error.message,7000);}
    finally{state.chat.busy=false;chatSave();renderChat();}
  }
  // Rebuild of the source from the ERP (administrators): progress in the chat, new plan when it ends.
  let regenerationTimer=0;
  async function pollRegeneration() {
    clearTimeout(regenerationTimer);
    let status;
    try{status=await (await fetch('/api/regeneration',{cache:'no-store'})).json();}catch(error){status=null;}
    const watched=state.regeneration&&state.regeneration.state==='running';
    state.regeneration=status;renderChat();
    if(status&&status.state==='running'){regenerationTimer=setTimeout(pollRegeneration,4000);return;}
    if(!watched)return;  // an outcome seen only now belongs to an earlier page: nothing to reload
    if(status&&status.state==='done'){
      try{await reloadSource();setToast('Fonte rigenerata dal '+isoText(status.start)+': nuovo piano verificato.',8000);}
      catch(error){setToast('Fonte rigenerata, ma il nuovo piano non si carica: '+error.message,9000);}
    }
    else if(status&&status.state==='failed')setToast('Rigenerazione non riuscita: '+(status.error||''),9000);
  }
  async function reloadSource() {
    const [descriptions,response]=await Promise.all([
      fetch('/api/descriptions',{cache:'no-store'}).then(r=>r.ok?r.json():null).catch(()=>null),
      fetch('/api/bootstrap',{cache:'no-store'})]);
    if(!response.ok)throw Error('HTTP '+response.status);
    data=await response.json();labels=descriptions&&descriptions.descriptions||labels;
    state.edits={fixed_tasks:[],closures:[]};state.settings={...data.scenario.settings};state.baselinePlan=data.plan;
    state.selected=null;state.archived=false;state.archiveBase=null;
    // A rebuild may change the scenario id: the next save starts from the revision of the new scenario.
    state.savedRevision=data.meta&&data.meta.saved_revision||0;
    state.anchor=atMidnight(new Date(ms(data.scenario.start)+3600000));state.dailyDate=state.anchor;setHorizon(state.mode);
    state.delays=null;state.loadsCache=null;
    if(extraViews&&extraViews.refresh)await extraViews.refresh();
    decorateHeader();render();
  }
  document.addEventListener('pf:source-synced',()=>{
    reloadSource().then(()=>setToast('Factory sincronizzata: Excel aggiornato e nuovo piano verificato.',9000))
      .catch(error=>setToast('Excel aggiornato; caricamento piano non riuscito: '+error.message,12000));
  });
  async function sendChat(raw) {
    const question=String(raw||'').trim().slice(0,1500);
    if(!question||state.chat.busy)return;
    if(!data){setToast('Il piano è ancora in calcolo.');return;}
    const history=chatHistory();
    state.chat.messages.push({role:'user',text:question});
    if(recognizer)recognizer.abort();
    state.chat.menu=null;renderChatMenu();
    state.chat.busy=true;renderChat();
    const digest=data.plan.scenario_digest;
    let reply;
    chatAbort=new AbortController();
    try{
      reply=data.meta&&data.meta.llm_available?await remoteAnswer(question,history,chatAbort.signal):await localAnswer(question);
    }catch(error){
      if(error.name==='AbortError'){
        state.chat.messages.push({role:'assistant',text:'Interrotto. Se l’assistente aveva già salvato qualcosa, non è annullabile da qui: controlla in Gestisci regole e nelle revisioni.',error:true});
        chatAbort=null;state.chat.busy=false;chatSave();renderChat();
        return;
      }
      let fallback=null;
      try{if(digest===data.plan.scenario_digest)fallback=await localAnswer(question);}catch(inner){fallback=null;}
      reply=fallback?{...fallback,text:fallback.text+'\n(AI non disponibile: '+error.message+' · risposta deterministica)'}:
        {text:error.code==='plan_not_indexed'?'Questo piano non è più in memoria del server: ricalcolalo per analizzarlo.':error.message,error:true};
    }
    chatAbort=null;
    const message={role:'assistant',digest,...reply};
    state.chat.messages.push(message);
    state.chat.messages=state.chat.messages.slice(-CHAT_LIMIT);
    if(reply.actions&&reply.actions.length){
      renderChat();
      message.done=await runActions(reply.actions);
      message.digest=data.plan.scenario_digest;
      delete message.actions;
    }
    state.chat.busy=false;chatSave();renderChat();
    if(voice.auto&&!reply.error&&reply.text)speak(reply.text,state.chat.messages.indexOf(message));
  }
  async function applyProposal(ref) {
    const [i,j]=ref.split(':').map(Number);
    const message=state.chat.messages[i],proposal=message&&message.proposals&&message.proposals[j];
    if(!proposal||proposal.state!=='open'||!proposal.valid)return;
    if(message.digest!==data.plan.scenario_digest){setToast('La proposta si riferisce a un piano precedente: chiedi di nuovo.');return;}
    const previous=state.edits;
    const fixed=previous.fixed_tasks.filter(x=>!proposal.edits.fixed_tasks.some(f=>f.task_id===x.task_id))
      .concat(proposal.edits.fixed_tasks);
    state.edits={fixed_tasks:fixed,closures:previous.closures.concat(proposal.edits.closures)};
    const ok=await recalculate(state.settings);
    if(!ok){state.edits=previous;render();return;}
    proposal.state='applied';chatSave();renderChat();
  }
  function askQuestion(question) {
    openChat(true);
    sendChat(question);
  }
  function downloadSnapshot(kind) {
    if (!data || !data.meta || data.meta.engine_verified !== true) {
      setToast('Non è disponibile un piano con audit tecnico superato.');return;
    }
    const payload=kind==='scenario'?data.scenario:data.plan;
    if (!payload) return;
    const id=String(data.scenario.id||'planning')
      .replace(/[^a-zA-Z0-9_-]/g,'_').slice(0,70)||'planning';
    const flags=(hasWhatif()?'-whatif':'')+
      (data.meta.source_publishable===false?'-NON_PUBBLICABILE':'');
    const digest=String(data.plan.scenario_digest||'').replace(/[^a-f0-9]/gi,'').slice(0,12);
    const filename=id+flags+'-'+digest+'-'+kind+'.json';
    const blob=new Blob([JSON.stringify(payload,null,2)+'\n'],{type:'application/json;charset=utf-8'});
    const link=document.createElement('a');
    const url=URL.createObjectURL(blob);
    link.href=url;link.download=filename;
    document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    setToast('File '+filename+' esportato. Mantieni lo Scenario insieme al Piano: il digest ne garantisce la corrispondenza.');
  }
  function enableSnapshotStorage() {
    if(!data.meta||!data.meta.authenticated)return;
    state.sessionRole=data.meta.role;
    const area=$('#more-list');
    const addButton=(id,label,callback)=>{
      const button=document.createElement('button');
      button.id=id;button.type='button';button.className='menu-item';
      button.textContent=label;button.addEventListener('click',callback);area.append(button);
    };
    if(state.sessionRole!=='reparto')addButton('save-snapshot','Salva revisione',async()=>{
      try {
        const response=await fetch('/api/snapshots',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({scenario:data.scenario,plan:data.plan,expected_revision:state.savedRevision,evidence_id:data.meta.evidence_id})});
        const result=await response.json();
        if(!response.ok)throw Error(result.error||'Salvataggio non riuscito');
        state.savedRevision=result.revision;setToast('Revisione '+result.revision+' salvata e verificata.'+
          (result.audit_file_written===false?' Registro audit su file indisponibile: contatta l’amministratore. Autore e data della revisione sono conservati.':''),7000);
      }catch(error){setToast(error.message,7000);}
    });
    addButton('open-snapshots','Revisioni salvate',async()=>{
      try {
        const response=await fetch('/api/snapshots',{cache:'no-store'});
        if(!response.ok)throw Error('Impossibile leggere le revisioni');
        const result=await response.json(),dialog=document.createElement('dialog');
        dialog.innerHTML='<h2>Revisioni salvate</h2><p>Riapri un piano per consultarlo o avvia una simulazione dalla sua revisione. Le evidenze conservate non certificano le fonti.</p>';
        for(const item of result.revisions){
          const button=document.createElement('button');button.type='button';button.className='btn btn-muted';
          button.textContent=item.scenario_id+' · revisione '+item.revision;
          button.addEventListener('click',async()=>{
            try{
              const source=await fetch('/api/snapshots/'+encodeURIComponent(item.scenario_id)+'/revisions/'+item.revision,{cache:'no-store'});
              if(!source.ok)throw Error('Impossibile riaprire il piano');
              data=await source.json();state.savedRevision=item.revision;state.archived=true;state.archiveBase=null;state.settings={...data.scenario.settings};
              state.edits={fixed_tasks:[],closures:[]};state.baselinePlan=data.plan;
              state.stagedResult=null;state.selected=null;
              state.anchor=atMidnight(data.scenario.start);state.dailyDate=state.anchor;setHorizon(state.mode);
              $('#calculate-button').disabled=true;dialog.close();dialog.remove();render();
              setToast('Snapshot archiviato in sola lettura. Usa Riapri fonte per tornare al calcolo.');
            }catch(error){setToast(error.message,7000);}
          });dialog.append(button);
          if(item.has_workbook){
            const input=document.createElement('a');input.className='btn btn-muted';input.textContent='Scarica Excel di ingresso';
            input.href='/api/snapshots/'+encodeURIComponent(item.scenario_id)+'/revisions/'+item.revision+'/input.xlsx';
            input.download='input-pianificazione.xlsx';dialog.append(input);
          }
        }
        if(!result.revisions.length)dialog.append(document.createTextNode('Nessuna revisione salvata.'));
        const close=document.createElement('button');close.type='button';close.className='btn btn-muted';close.textContent='Chiudi';
        close.addEventListener('click',()=>{dialog.close();dialog.remove();});dialog.append(close);
        dialog.addEventListener('cancel',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
      }catch(error){setToast(error.message,7000);}
    });
    if(state.sessionRole!=='reparto')addButton('edit-revision','Simula da revisione',()=>{
      if(!state.archived||!data.meta.saved_revision){setToast('Apri prima una revisione salvata.');return;}
      state.archiveBase={scenario_id:data.scenario.id,revision:data.meta.saved_revision};
      state.archived=false;state.edits={fixed_tasks:[],closures:[]};state.baselinePlan=data.plan;
      $('#calculate-button').disabled=false;render();
      setToast('Simulazione dalla revisione '+state.archiveBase.revision+'. Le regole correnti non saranno applicate. Salva crea una nuova revisione.');
    });
    addButton('reopen-source','Riapri fonte',()=>location.assign('/'));
    if(state.sessionRole!=='reparto')addButton('manage-rules','Gestisci regole',openRuleMemory);
    if(state.sessionRole!=='reparto'&&data.meta.llm_available)addButton('suggest-simulation','Proponi simulazione',async()=>{
      const question=window.prompt('Descrivi una simulazione con macchina/task e orari completi di fuso. Nessun piano sarà pubblicato.');
      if(!question)return;const digest=data.plan.scenario_digest;
      try{
        const response=await fetch('/api/simulations/suggest',{method:'POST',headers:{'Content-Type':'application/json'},
          body:JSON.stringify({question,scenario:data.scenario,plan:data.plan,evidence_id:data.meta.evidence_id})});
        const proposed=await response.json();if(!response.ok)throw Error(proposed.error||'Proposta non disponibile');
        if(!proposed.supported){setToast(proposed.reason,8000);return;}
        if(proposed.source_digest!==digest||data.plan.scenario_digest!==digest)throw Error('Il piano è cambiato: ripeti la richiesta.');
        const dialog=document.createElement('dialog'),title=document.createElement('h2'),pre=document.createElement('pre');
        title.textContent='Anteprima simulazione · nessuna pubblicazione';pre.textContent=JSON.stringify({edits:proposed.edits,comparison:proposed.comparison},null,2);
        dialog.append(title,pre);
        const accept=document.createElement('button');accept.type='button';accept.className='btn btn-primary';accept.textContent='Mostra il piano simulato';
        accept.addEventListener('click',()=>{
          if(data.plan.scenario_digest!==digest){setToast('Il piano è cambiato: ripeti la richiesta.');return;}
          const meta=data.meta;state.baselinePlan=data.plan;data=proposed.preview;data.meta={...meta,...data.meta};
          state.archived=true;state.archiveBase=null;state.selected=null;state.stagedResult=null;
          state.edits={fixed_tasks:[],closures:[]};$('#calculate-button').disabled=true;
          dialog.close();dialog.remove();render();setToast('Simulazione verificata. Puoi esportarla o salvarla; Riapri fonte ripristina il calcolo.');
        });
        const close=document.createElement('button');close.type='button';close.className='btn btn-muted';close.textContent='Scarta';
        close.addEventListener('click',()=>{dialog.close();dialog.remove();});dialog.append(accept,close);
        dialog.addEventListener('cancel',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
      }catch(error){setToast(error.message,8000);}
    });
    if(!(extraViews&&extraViews.session&&extraViews.session.local))addButton('logout','Esci',async()=>{
      try{await fetch('/api/logout',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});location.assign('/');}
      catch(error){setToast('Disconnessione non riuscita.');}
    });
    if(state.sessionRole==='reparto')$('#calculate-button').hidden=true;
  }
  async function openRuleMemory() {
    if(state.archived||state.archiveBase){setToast('Riapri la fonte attiva prima di gestire le regole.');return;}
    const post=async(path,body)=>{
      const response=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
      const value=await response.json();if(!response.ok)throw Error(value.error||'Operazione non riuscita');return value;
    };
    try{
      const response=await fetch('/api/rules',{cache:'no-store'});if(!response.ok)throw Error('Regole non disponibili');
      const value=await response.json(),dialog=document.createElement('dialog');
      if(value.editable===false){
        dialog.className='rule-dialog';
        dialog.innerHTML='<h2>Regole nell’Excel</h2><p>Modifica regole e indicazioni nel file, poi premi Rileggi Excel nelle Fonti e ricalcola.</p>'+
          value.rules.map(item=>'<section><p><strong>'+esc(ruleValueText(item.rule))+'</strong> · '+esc(ruleScopeText(item.rule))+
            ' · '+esc({draft:'bozza',confirmed:'confermata',revoked:'revocata'}[item.status]||item.status)+
            (item.rule.note?'<br>'+esc(item.rule.note):'')+'</p></section>').join('');
        const close=document.createElement('button');close.type='button';close.className='btn btn-muted';close.textContent='Chiudi';
        close.addEventListener('click',()=>{dialog.close();dialog.remove();});dialog.append(close);
        dialog.addEventListener('cancel',()=>dialog.remove());document.body.append(dialog);dialog.showModal();return;
      }
      dialog.className='rule-dialog';
      dialog.innerHTML='<h2>Memoria aziendale</h2><p>Le bozze non cambiano il piano: valgono dopo la conferma e si revocano quando vuoi. '+
        'Le regole con un valore cambiano il calcolo; le indicazioni testuali le riceve l’assistente e le segue quando ripianifica.</p>'+
        '<div id="rule-text-slot"></div>'+
        '<form id="rule-form"><label>Tipo <select name="action">'+Object.entries(RULE_ACTIONS).map(([k,v])=>'<option value="'+k+'">'+esc(
          k==='set_priority'?'Priorità ordine (minore = prima)':k==='set_customer_priority'?'Priorità cliente (pesa come un giorno di consegna)':
          k==='note'?'Indicazione testuale per l’assistente':k==='ignore_material'?'Ignora il vincolo materiale nel piano':v)+'</option>').join('')+'</select></label>'+
        '<label data-for-value>Livello <select name="level"><option value="">valore libero</option>'+PRIORITY_LEVELS.map(l=>'<option value="'+l[1]+'">'+l[0]+' ('+l[1]+')</option>').join('')+'</select></label>'+
        '<label data-for-value>Valore <input name="value" type="number" step="1" min="-1000000" max="1000000"></label>'+
        '<label class="rule-note">Testo <textarea name="note" rows="2" maxlength="2000" placeholder="La frase da ricordare, o il motivo della regola"></textarea></label>'+
        '<fieldset data-scope><legend>Ambito</legend><label>Ordine <select name="order_id"><option value="">Qualsiasi ordine</option></select></label>'+
        '<label>Risorsa <select name="resource_id"><option value="">Qualsiasi risorsa</option></select></label>'+
        '<label>Cliente <select name="customer_id"><option value="">Qualsiasi cliente</option></select></label>'+
        '<label>Materiale <select name="material"><option value="">Qualsiasi materiale</option></select></label>'+
        '<label>Spessore oltre (mm) <input name="min_thickness_mm" type="text" inputmode="decimal" placeholder="es. 40.5"></label>'+
        '<label>Spessore fino a incluso (mm) <input name="max_thickness_mm" type="text" inputmode="decimal"></label>'+
        '<label>Valida da (UTC) <input name="valid_from" type="datetime-local"></label>'+
        '<label>Valida fino a (UTC) <input name="valid_to" type="datetime-local"></label>'+
        '<p data-scope-hint></p></fieldset>'+
        '<button class="btn btn-muted" type="submit">Crea bozza</button></form>';
      const orders=[...new Set(data.scenario.operations.map(o=>o.order_id))].sort();
      for(const id of orders){const option=document.createElement('option');option.value=id;option.textContent=[orderCode(id),orderCustomer(id)].filter(Boolean).join(' · ');dialog.querySelector('[name="order_id"]').append(option);}
      const scope=data.meta.rule_scope||{};
      for(const [field,values] of [['resource_id',data.scenario.resources.map(r=>r.id)],
          ['customer_id',(scope.orders||[]).map(o=>o.customer_id).filter(Boolean)],
          ['material',(scope.operations||[]).map(o=>o.material).filter(Boolean)]]){
        const select=dialog.querySelector('[name="'+field+'"]');
        const text=(id)=>field==='customer_id'&&customerName(id)?customerName(id)+' · '+id:field==='resource_id'?resourceTitle(id):id;
        for(const id of [...new Set(values)].sort((x,y)=>text(x).localeCompare(text(y)))){const option=document.createElement('option');option.value=id;option.textContent=text(id);select.append(option);}
        if(!values.length)select.disabled=true;
      }
      if(!(scope.operations||[]).some(o=>o.thickness_mm!==undefined)){
        for(const field of ['min_thickness_mm','max_thickness_mm'])dialog.querySelector('[name="'+field+'"]').disabled=true;
      }
      const formEl=dialog.querySelector('#rule-form');
      const sync=()=>{
        const action=formEl.elements.action.value,numeric=action!=='note'&&action!=='ignore_material';
        dialog.querySelectorAll('[data-for-value]').forEach(el=>el.hidden=!numeric);
        dialog.querySelector('[name="level"]').closest('label').hidden=!(action==='set_priority'||action==='set_customer_priority');
        formEl.elements.value.required=numeric;formEl.elements.note.required=action==='note';
        dialog.querySelector('[data-scope]').hidden=action==='ignore_material';
        dialog.querySelector('[data-scope-hint]').textContent=action==='note'?'Ambito facoltativo: senza ambito l’indicazione vale per tutto il piano.':
          'Specificare almeno un ambito. I filtri si combinano; cliente/materiale/spessore richiedono attributi della fonte.';
      };
      formEl.elements.action.addEventListener('change',sync);
      formEl.elements.level.addEventListener('change',()=>{if(formEl.elements.level.value!=='')formEl.elements.value.value=formEl.elements.level.value;});
      sync();
      const refresh=()=>{dialog.close();dialog.remove();openRuleMemory();};
      dialog.querySelector('form').addEventListener('submit',async event=>{
        event.preventDefault();const form=new FormData(event.target);
        const action=String(form.get('action')),rule={action},note=String(form.get('note')||'').trim();
        if(action!=='note'&&action!=='ignore_material')rule.value=Number(form.get('value'));
        if(note)rule.note=note;
        for(const field of action==='ignore_material'?[]:['order_id','resource_id','customer_id','material','min_thickness_mm','max_thickness_mm']){
          const value=String(form.get(field)||'').trim();if(value)rule[field]=value;
        }
        for(const field of ['valid_from','valid_to']){const value=String(form.get(field)||'');if(value)rule[field]=value+':00Z';}
        try{await post('/api/rules',{rule});refresh();}
        catch(error){setToast(error.message,7000);}
      });
      if(data.meta.llm_available){
        const slot=dialog.querySelector('#rule-text-slot');
        const text=document.createElement('textarea');text.maxLength=2000;text.rows=2;
        text.placeholder='Es. fino a dicembre priorità massima al cliente X · non considerare il materiale · per X si salda sempre prima di piegare';
        const label=document.createElement('label');label.textContent='Scrivilo come lo diresti a un collega';label.append(text);slot.append(label);
        const suggest=document.createElement('button');suggest.type='button';suggest.className='btn';suggest.textContent='Proponi bozza con Claude';
        suggest.addEventListener('click',async()=>{if(!text.value.trim())return;suggest.disabled=true;try{const proposed=await post('/api/rules/suggest',{question:text.value});
          if(!proposed.supported){setToast(proposed.reason,8000);return;}refresh();}catch(error){setToast(error.message,7000);}finally{suggest.disabled=false;}});slot.append(suggest);
        const or=document.createElement('p');or.className='section-note';or.textContent='Oppure compila la regola a mano:';slot.append(or);
      }
      for(const item of value.rules){
        const row=document.createElement('section'),title=document.createElement('p');
        const status={draft:'bozza',confirmed:'confermata',revoked:'revocata'}[item.status]||item.status;
        title.innerHTML='<strong>'+esc(ruleValueText(item.rule))+'</strong> · '+esc(ruleScopeText(item.rule))+' · <span class="status-pill'+
          (item.status==='confirmed'?'':' neutral')+'">'+esc(status)+'</span>'+(item.rule.note?'<br>«'+esc(item.rule.note)+'»':'')+
          '<br><small>'+esc(item.id+' · v'+item.version+' · '+(item.actor||''))+'</small>';row.append(title);
        const button=(label,callback)=>{const b=document.createElement('button');b.type='button';b.className='btn btn-muted';b.textContent=label;b.addEventListener('click',callback);row.append(b);};
        if(item.status==='draft')button('Anteprima',async()=>{try{const preview=await post('/api/rules/preview',{rule_id:item.id});
          const pre=document.createElement('pre');pre.textContent=JSON.stringify(preview,null,2);row.append(pre);}catch(error){setToast(error.message,7000);}});
        if(item.status==='draft'||item.status==='confirmed')button(item.status==='draft'?'Conferma':'Revoca',async()=>{
          const decision=item.status==='draft'?'confirm':'revoke';
          if(!window.confirm('Confermi la modifica della regola '+item.id+'? Il piano della fonte sarà ricalcolato.'))return;
          try{await post('/api/rules/decision',{rule_id:item.id,expected_version:item.version,decision});location.assign('/');}
          catch(error){setToast(error.message,7000);}
        });
        if(item.status==='draft')button('Scarta',async()=>{
          try{await post('/api/rules/decision',{rule_id:item.id,expected_version:item.version,decision:'revoke'});refresh();}
          catch(error){setToast(error.message,7000);}
        });dialog.append(row);
      }
      const close=document.createElement('button');close.type='button';close.className='btn btn-muted';close.textContent='Chiudi';
      close.addEventListener('click',()=>{dialog.close();dialog.remove();});dialog.append(close);
      dialog.addEventListener('cancel',()=>dialog.remove());document.body.append(dialog);dialog.showModal();
    }catch(error){setToast(error.message,7000);}
  }
  function csvCell(v) {
    let str=String(v===null||v===undefined?'':v);
    if(/^[\s]*[=+\-@]/.test(str)||/^[\t\r]/.test(str)) str="'"+str;
    return '"'+str.replace(/"/g,'""')+'"';
  }
  function exportCSV() {
    if(!data)return;
    const lines=[['Task','Commessa','Risorsa','Inizio UTC','Fine UTC','Ore-unità','Vincolo fisso']];
    for(const a of data.plan.assignments) {
      for(const seg of a.segments) for(const d of a.demands) {
        lines.push([a.task_id,taskOrders(a).join('|'),d.resource_id,seg.start,seg.end,
          ((ms(seg.end)-ms(seg.start))/3600000*d.units/(data.meta && data.meta.workbook && data.meta.workbook.capacity_scale || 1)).toFixed(3),a.fixed?'Sì':'No']);
      }
    }
    const csv='\ufeff'+lines.map(line=>line.map(csvCell).join(';')).join('\r\n')+'\r\n';
    const blob=new Blob([csv],{type:'text/csv;charset=utf-8'});
    const url=URL.createObjectURL(blob);
    const link=document.createElement('a');
    link.href=url;link.download='planning-factory-'+data.scenario.id.replace(/[^a-zA-Z0-9_-]/g,'_')+'.csv';
    document.body.append(link);link.click();link.remove();
    setTimeout(()=>URL.revokeObjectURL(url),1000);
    setToast('CSV esportato dal piano corrente. I dati restano nel browser.');
  }
  function wire() {
    bindPanelResize('inspector-resize','inspector','--inspector-size','inspectorWidth',260,680);
    bindPanelResize('chat-resize','chat-panel','--assistant-size','chatWidth',280,680);
    document.addEventListener('keydown',e=>{
      if(e.key==='Escape'&&document.getElementById('load-dialog'))closeLoadDialog();
      else if(e.key==='Escape'&&materialDialogSpec)closeMaterialDialog();
      else if((e.key==='Enter'||e.key===' ')&&e.target.matches&&e.target.matches('#load-dialog tr.row-link')){e.preventDefault();e.target.click();}
    });
    $('#summary-section').addEventListener('toggle',()=>{
      layout.summaryOpen=$('#summary-section').open;
      saveLayout();syncLayoutControls();
    });
    document.addEventListener('click', e=>{
      const menu=$('#more-menu');
      if(menu&&menu.open&&(e.target.closest('.menu-item')||!e.target.closest('#more-menu')))menu.open=false;
      if(e.target.closest('[data-close-load]')&&(e.target.matches('[data-close-load]')||e.target.closest('.close'))){closeLoadDialog();return;}
      if(e.target.closest('[data-close-material]')&&(e.target.matches('[data-close-material]')||e.target.closest('.close'))){closeMaterialDialog();return;}
      const toggle=e.target.closest('[data-mat-toggle]');
      if(toggle){
        const row=document.getElementById(toggle.dataset.matToggle);
        if(row){row.hidden=!row.hidden;toggle.setAttribute('aria-expanded',row.hidden?'false':'true');}
        return;
      }
      const target=e.target.closest('[data-material-task],[data-material-order],[data-view],[data-task],[data-mode],[data-action],[data-order],[data-order-detail],[data-assistant-prompt],[data-chat-apply],[data-chat-drop],[data-chat-suggestion],[data-explain-order],[data-chat-explain],[data-chat-undo],[data-chat-speak]');
      if(!target)return;
      if(state.chat.busy&&(target.dataset.chatApply||target.dataset.chatUndo||target.dataset.chatDrop||target.dataset.chatSuggestion!==undefined||target.dataset.chatExplain||target.dataset.assistantPrompt!==undefined))return;
      if(target.dataset.materialTask){openMaterialDialog({scope:'task',id:target.dataset.materialTask});return;}
      if(target.dataset.materialOrder){openMaterialDialog({scope:'order',id:target.dataset.materialOrder});return;}
      if(target.dataset.chatApply){applyProposal(target.dataset.chatApply);return;}
      if(target.dataset.chatUndo){undoAction(target.dataset.chatUndo);return;}
      if(target.dataset.chatSpeak!==undefined){const i=Number(target.dataset.chatSpeak),m=state.chat.messages[i];if(state.chat.speaking===i){stopSpeaking();renderChat();}else if(m)speak(m.text,i);return;}
      if(target.dataset.chatDrop){
        const [i,j]=target.dataset.chatDrop.split(':').map(Number);
        const p=state.chat.messages[i]&&state.chat.messages[i].proposals&&state.chat.messages[i].proposals[j];
        if(p){p.state='dropped';chatSave();renderChat();}
        return;
      }
      if(target.dataset.chatSuggestion!==undefined){sendChat(target.textContent.trim());return;}
      if(target.dataset.explainOrder){explainOrder(target.dataset.explainOrder);return;}
      if(target.dataset.chatExplain){askQuestion('Perché la commessa '+target.dataset.chatExplain+' finisce quando finisce?');return;}
      if(target.dataset.view){setView(target.dataset.view);return;}
      if(target.dataset.assistantPrompt!==undefined){
        askQuestion(target.textContent.trim().slice(0,280));
        return;
      }
      if(target.dataset.orderDetail){
        state.selectedOrder=target.dataset.orderDetail;state.selected=null;
        $$('[data-task]').forEach(el=>el.classList.remove('selected'));
        $$('[data-order-detail]').forEach(el=>el.classList.toggle('selected',el.dataset.orderDetail===state.selectedOrder));
        updateInspector();renderChat();
        if(window.innerWidth<=1100||document.body.classList.contains('wide-view'))setInspectorOpen(true);
        return;
      }
      if(target.dataset.task){
        closeLoadDialog();
        state.selected=target.dataset.task;state.selectedOrder=null;
        $$('[data-order-detail]').forEach(el=>el.classList.remove('selected'));
        $$('[data-task]').forEach(el=>el.classList.toggle('selected',el.dataset.task===state.selected));
        updateInspector();renderChat();
        if(window.innerWidth<=1100||document.body.classList.contains('wide-view'))setInspectorOpen(true);
        return;
      }
      if(target.dataset.mode){
        setHorizon(target.dataset.mode);
        state.showAll=false;render();return;
      }
      if(target.dataset.order){
        if(state.expanded.has(target.dataset.order))state.expanded.delete(target.dataset.order);
        else state.expanded.add(target.dataset.order);
        renderKeepGantt();return;
      }
      const action=target.dataset.action;
      if(action==='expand-all'){state.expanded=new Set(data.plan.orders.map(o=>o.order_id));renderKeepGantt();return;}
      if(action==='collapse-all'){state.expanded=new Set();renderKeepGantt();return;}
      if(action==='prev'){stepHorizon(-1);render();}
      else if(action==='next'){stepHorizon(1);render();}
      else if(action==='all-resources'){state.showAll=true;render();}
      else if(action==='nest-sort'){
        const cur=state.nestSort||{key:'start',dir:1},key=target.dataset.key;
        state.nestSort={key,dir:cur.key===key?-cur.dir:1};render();
      }
      else if(action==='goto-day'){
        // Same horizon type, moved to the period that contains the day (week → its Monday, month → its 1st).
        state.anchor=atMidnight(new Date(target.dataset.day+'T12:00:00Z'));
        if(state.mode!=='range')setHorizon(state.mode);
        render();
      }
      else if(action==='zoom-day'){state.anchor=atMidnight(new Date(target.dataset.day+'T12:00:00Z'));state.mode='day';state.span=1;render();}
      else if(action==='delay-cause'){state.delayCause=target.dataset.cause||null;render();}
      else if(action==='bn-cause'){state.delayCause=target.dataset.cause||null;setView('deliveries');}
      else if(action==='bn-all'){state.showAll=!state.showAll;render();}
      else if(action==='delay-customer'){state.delayCustomer=target.dataset.customer||null;render();}
      else if(action==='all-customers'){state.showAllCustomers=true;render();}
      else if(action==='load-cell'){openLoadDialog({resource:target.dataset.resource,day:target.dataset.day});}
      else if(action==='load-stage'){loadStaged();}
      else if(action==='daily-prev'){state.dailyDate=atMidnight(new Date((state.dailyDate||state.anchor).getTime()-12*3600000));render();}
      else if(action==='daily-next'){state.dailyDate=atMidnight(new Date((state.dailyDate||state.anchor).getTime()+36*3600000));render();}
      else if(action==='pin-task'){
        const a=data.plan.assignments.find(x=>x.task_id===target.dataset.taskAction);
        if(a)applyFixedTask(a.task_id,assignmentStart(a),editableAlternative(a));
      }
      else if(action==='move-task'){
        const a=data.plan.assignments.find(x=>x.task_id===target.dataset.taskAction);
        const input=$('#move-at');
        if(!a||!input||!input.value)return setToast('Inserisci un orario di inizio UTC.');
        applyFixedTask(a.task_id,input.value+':00Z',editableAlternative(a));
      }
      else if(action==='unpin-task'){unpinTask(target.dataset.taskAction);}
    });
    $('#view-root').addEventListener('change',e=>{
      if(e.target.id==='bn-kind'){state.bnKind=e.target.value;state.showAll=false;render();return;}
      if(e.target.id==='resource-kind'){state.resourceKind=e.target.value;state.showAll=false;render();}
      else if(e.target.id==='range-from'||e.target.id==='range-to'){
        const read=id=>{const el=$('#'+id);const p=el&&el.value?new Date(el.value+'T12:00:00Z'):null;return p&&Number.isFinite(p.getTime())?atMidnight(p):null;};
        const from=read('range-from'),to=read('range-to');
        if(from){
          // Moving "dal" past "al" drags "al" along, so the range never ends up empty.
          const last=e.target.id==='range-from'&&(!to||to.getTime()<from.getTime())?from:to;
          setHorizon('range',from,last);state.showAll=false;render();
        }
      }
      else if(e.target.id==='daily-picker'){
        const parsed=new Date(e.target.value+'T12:00:00Z');
        if(Number.isFinite(parsed.getTime())){state.dailyDate=atMidnight(parsed);render();}
      }
    });
    $('#view-root').addEventListener('input',e=>{
      if(e.target.id==='quick-search') {
        const pos=e.target.selectionStart;
        state.query=e.target.value;
        render();
        const input=$('#quick-search');
        if(input){input.focus();input.setSelectionRange(pos,pos);}
      }
    });
    $('#view-root').addEventListener('submit',async e=>{
      if(e.target.id==='assistant-form') {
        e.preventDefault();
        const question=String(new FormData(e.target).get('question')||'').trim().slice(0,280);
        if(!question){setToast('Scrivi una domanda sul piano.');return;}
        askQuestion(question);
        return;
      }
      if(e.target.id==='closure-form') {
        e.preventDefault();
        const form=new FormData(e.target);
        const start=String(form.get('start')||'')+':00Z';
        const end=String(form.get('end')||'')+':00Z';
        if(!Number.isFinite(ms(start))||!Number.isFinite(ms(end))||ms(start)>=ms(end)) {
          setToast('Intervallo non valido: verifica inizio e fine (UTC).');return;
        }
        const previous=state.edits;
        const closure={resource_id:String(form.get('resource_id')),start,end,reason:String(form.get('reason'))};
        state.edits={...previous,closures:previous.closures.concat([closure])};
        const ok=await recalculate(state.settings);
        if(!ok){state.edits=previous;render();}
        return;
      }
      if(e.target.id!=='parameters-form')return;
      e.preventDefault();
      const form=new FormData(e.target);
      const patch={};
      for(const [k,v] of form.entries())patch[k]=k==='mode'||k==='auto_rule'?v:Number(v);
      // The margin is sent only when it differs from the one the source was built with.
      const days=patch.delivery_buffer_days;delete patch.delivery_buffer_days;
      const built=(data.meta&&data.meta.canonical&&data.meta.canonical.delivery_buffer_seconds)||0;
      const seconds=Math.round(days*86400);
      if(Number.isFinite(seconds)&&seconds!==built&&!state.archiveBase&&!state.archived)patch.delivery_buffer_seconds=seconds;
      recalculate(patch);
    });
    $('#chat-toggle').addEventListener('click',()=>openChat(!state.chat.open));
    $('#chat-close').addEventListener('click',()=>openChat(false));
    $('#chat-reset').addEventListener('click',newConversation);
    $('#chat-history-toggle').addEventListener('click',()=>toggleChatMenu('history'));
    $('#chat-voice-toggle').addEventListener('click',()=>toggleChatMenu('voice'));
    $('#chat-mic').addEventListener('click',toggleDictation);
    const menu=$('#chat-menu');
    menu.addEventListener('click',e=>{
      const t=e.target.closest('[data-hist-open],[data-hist-del],#chat-hist-delete,[data-voice-test],[data-voice-stop]');
      if(!t)return;
      if(t.dataset.histOpen)openConversation(t.dataset.histOpen);
      else if(t.dataset.histDel)deleteConversations([t.dataset.histDel]);
      else if(t.id==='chat-hist-delete')deleteConversations([...state.chat.checked]);
      else if('voiceTest' in t.dataset)speak('Questa è la voce dell’assistente di pianificazione.');
      else if('voiceStop' in t.dataset){stopSpeaking();renderChat();}
    });
    menu.addEventListener('change',e=>{
      const t=e.target;
      if(t.dataset.histCheck){t.checked?state.chat.checked.add(t.dataset.histCheck):state.chat.checked.delete(t.dataset.histCheck);renderHistoryList();}
      else if(t.id==='chat-hist-all'){
        const query=($('#chat-hist-search').value||'').trim().toLocaleLowerCase('it-IT');
        for(const c of histRead())if(!query||c.title.toLocaleLowerCase('it-IT').includes(query)||c.messages.some(m=>String(m.text).toLocaleLowerCase('it-IT').includes(query)))
          t.checked?state.chat.checked.add(c.id):state.chat.checked.delete(c.id);
        renderHistoryList();
      }
      else if(t.dataset.voice==='auto'){voice.auto=t.checked;voiceSave();}
      else if(t.dataset.voice==='send'){voice.send=t.checked;voiceSave();}
      else if(t.dataset.voice==='uri'){voice.uri=t.value;voiceSave();}
    });
    menu.addEventListener('input',e=>{
      const t=e.target;
      if(t.id==='chat-hist-search')renderHistoryList();
      else if(t.dataset.voice==='rate'){voice.rate=Number(t.value)||1;$('#chat-rate-out').textContent=voice.rate.toFixed(1)+'×';voiceSave();}
    });
    if(synth)synth.addEventListener('voiceschanged',()=>{if(state.chat.open&&state.chat.menu==='voice'&&!menu.contains(document.activeElement))renderVoiceMenu();});
    // The menus exist only while called: a click elsewhere or Esc closes them.
    document.addEventListener('pointerdown',e=>{
      if(state.chat.menu&&!e.target.closest('#chat-menu,#chat-history-toggle,#chat-voice-toggle')){state.chat.menu=null;renderChatMenu();}
    });
    $('#chat-panel').addEventListener('keydown',e=>{
      if(e.key==='Escape'&&state.chat.menu){e.stopPropagation();state.chat.menu=null;renderChatMenu();$('#chat-history-toggle').focus();}
    });
    $('#chat-stop').addEventListener('click',()=>{if(chatAbort)chatAbort.abort();});
    $('#chat-form').addEventListener('submit',e=>{
      e.preventDefault();
      const input=$('#chat-input');const question=input.value;input.value='';sendChat(question);
    });
    $('#chat-input').addEventListener('keydown',e=>{
      if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#chat-form').requestSubmit();}
    });
    document.addEventListener('keydown',e=>{if(e.key==='Escape'&&state.chat.open)openChat(false);});
    $('#calculate-button').addEventListener('click',()=>recalculate(state.settings));
    $('#stale-recalculate').addEventListener('click',()=>recalculate(state.settings));
    window.addEventListener('hashchange',()=>{
      const next=location.hash.slice(1);
      if(next!==state.view&&viewTitle[next])setView(next);
    });
    document.addEventListener('pf:ai-changed',e=>{
      if(data&&data.meta)data.meta.llm_available=Boolean(e.detail&&e.detail.active);
    });
    $('#reset-button').addEventListener('click',async()=>{
      const before=state.edits;
      state.edits={fixed_tasks:[],closures:[]};
      const ok=await recalculate(state.settings);
      if(!ok){state.edits=before;render();}
      else setToast('Simulazioni annullate: ripristinato lo Scenario sorgente.');
    });
    $('#download-button').addEventListener('click',exportCSV);
    $('#download-scenario').addEventListener('click',()=>downloadSnapshot('scenario'));
    $('#download-plan').addEventListener('click',()=>downloadSnapshot('plan'));
    $('#dismiss-notice').addEventListener('click',()=>$('#notice').hidden=true);
    document.addEventListener('click',event=>{if(event.target.closest&&event.target.closest('[data-open-rules]'))openRuleMemory();});
    $('#menu-toggle').addEventListener('click',()=>{
      const open=window.innerWidth<=760?!$('#sidebar').classList.contains('open'):layout.navCollapsed;
      setSidebarOpen(open);
    });
    $$('.nav-section-toggle').forEach(toggle=>toggle.addEventListener('click',()=>{
      const group=toggle.closest('.nav-group');
      if(group)setNavSectionCollapsed(group.dataset.navSection,!group.classList.contains('section-collapsed'));
    }));
    $('#inspector-toggle').addEventListener('click',()=>{
      const drawer=window.innerWidth<=1100||document.body.classList.contains('wide-view');
      const open=drawer?$('#inspector').classList.contains('open'):!layout.inspectorCollapsed;
      if(state.chat.open)openChat(false);
      setInspectorOpen(!open);
    });
    $('#close-inspector').addEventListener('click',()=>setInspectorOpen(false));
    window.addEventListener('pf:inspector-open',event=>setInspectorOpen(event.detail!==false));
    window.addEventListener('resize',()=>{
      const drawer=window.innerWidth<=1100||document.body.classList.contains('wide-view');
      $('#inspector').classList.toggle('open',drawer&&layout.inspectorDrawerOpen);
      document.body.classList.toggle('inspector-collapsed',layout.inspectorCollapsed);
      syncLayoutControls();
    });
    window.addEventListener('keydown',e=>{
      if(e.key==='Escape'){
        setSidebarOpen(false);
        if($('#inspector').classList.contains('open')||!layout.inspectorCollapsed)setInspectorOpen(false);
      }
    });
  }
  function decorateHeader() {
    const canonical=data.meta&&data.meta.canonical, domains=canonical&&canonical.domains||{};
    const install=labels&&labels.installation||{}, source=labels&&labels.source||{};
    const site=$('#site-switch');
    if(site)site.innerHTML='<strong>'+esc(install.customer||data.meta&&data.meta.dataset||'Scenario da file')+'</strong>'+
      (source.database?'<span> · '+esc(source.database)+'</span>':'');
    const chips=[];
    const startDay=source.planning_start||data.scenario.start;
    chips.push(['ok','Factory · dati al '+fmt(startDay,'day')]);
    if(domains.nesting)chips.push(['ok','Nesting Alma · '+(data.scenario.nestings||[]).length+' programmi']);
    if(domains.materials){
      const shortages=(canonical.issues||[]).filter(i=>i.code==='material_shortage').length;
      const note=source.materials_note;
      chips.push([shortages||note?'warn':'ok','Materiali'+(note?' '+note:'')+' · '+(shortages?shortages+' carenze':'coperti')]);
    } else chips.push(['off','Materiali · fonte assente']);
    if(domains.progress)chips.push(['ok','Avanzamento TRD · '+((canonical.progress&&canonical.progress.total_rows)||0)+' fasi']);
    $('#source-chips').innerHTML=chips.map(c=>'<span class="source-chip '+c[0]+'"><span class="dot"></span>'+esc(c[1])+'</span>').join('');
    const foot=$('#plan-footnote');
    if(foot)foot.innerHTML='Piano calcolato il '+esc(fmt(canonical&&canonical.built_at||new Date().toISOString()))+
      '<br>Scenario <strong>'+esc(data.scenario.id)+'</strong><br>Strategia: '+
      esc({auto:'automatica',forward:'al più presto',backward:'a ritroso'}[data.scenario.settings.mode]||data.scenario.settings.mode)+
      (data.scenario.settings.auto_backward_days?' · ritroso oltre '+data.scenario.settings.auto_backward_days+' gg':'');
  }
  async function init() {
    applyLayout();
    wire();chatLoad();
    const first=location.hash.slice(1);
    if(isExtra(first)){
      // Data and administration screens do not need the plan: open them while it is computed.
      state.view=first;
      $$('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.view===first));
      render();
    } else render();
    const descriptionsRequest=fetch('/api/descriptions',{cache:'no-store'})
      .then(r=>r.ok?r.json():null).then(v=>v&&v.descriptions||null).catch(()=>null);
    try {
      const response=await fetch('/api/bootstrap',{cache:'no-store'});
      if(!response.ok)throw new Error('HTTP '+response.status);
      data=await response.json();
      if(!data.scenario||!data.plan)throw new Error('Risposta del motore incompleta');
      state.baselinePlan=data.plan;
      enableSnapshotStorage();
      labels=await descriptionsRequest;
      decorateHeader();
      // This is a historical anonymized sample: frame the beginning of
      // the planning horizon, not wall-clock today.
      displayTz=(data.scenario.calendars.find(c=>c.timezone)||{}).timezone||'UTC';
      state.anchor=atMidnight(new Date(ms(data.scenario.start)+3600000));
      state.dailyDate=state.anchor;
      setHorizon(state.mode);
      state.settings={...data.scenario.settings};
      state.expanded=new Set(data.plan.orders.slice(0,4).map(o=>o.order_id));
      const initial=location.hash.slice(1);
      if(viewTitle[initial])state.view=initial;
      if(extraViews)await extraViews.ready;
      if(extraViews&&extraViews.planStale)$('#data-stale').hidden=false;
      $$('.nav-item').forEach(el=>{
        const active=el.dataset.view===state.view;
        el.classList.toggle('active',active);
        if(active)el.setAttribute('aria-current','page');
        else el.removeAttribute('aria-current');
      });
      render();
      // A rebuild started earlier (another page, before a reload) keeps reporting here until it ends.
      fetch('/api/regeneration',{cache:'no-store'}).then(r=>r.ok?r.json():null)
        .then(s=>{if(s&&s.state==='running'){state.regeneration=s;pollRegeneration();}}).catch(()=>{});
    } catch(error) {
      if(!isExtra(state.view))$('#view-root').innerHTML='<article class="panel">'+emptyState(
        'Impossibile leggere lo Scenario','Il backend Python non è disponibile: '+error.message,'⚠')+'</article>';
      $('#summary-cards').innerHTML='';
      setToast('Impossibile calcolare il piano: '+error.message,7000);
    }
  }
  // Pannelli collassabili: la chiusura si ricorda per titolo (solo preferenza di visualizzazione di questo browser).
  (function collapsiblePanels(){
    const TITLES=['Consegne in ritardo o a rischio','Dati mancanti o da verificare','Clienti','Orizzonte visibile',
      'Utilizzo sulle risorse','Osservazioni di capacità','Sorgenti collegate','Domini del modello canonico','Diagnostica dei dati'];
    const KEY='pf-collapsed-panels';
    const load=()=>{try{return new Set(JSON.parse(localStorage.getItem(KEY)||'[]'));}catch(e){return new Set();}};
    const save=set=>{try{localStorage.setItem(KEY,JSON.stringify([...set]));}catch(e){}};
    const root=document.getElementById('view-root');
    if(!root)return;
    function apply(panel,title,collapsed){
      panel.classList.toggle('is-collapsed',collapsed);
      const btn=panel.querySelector(':scope > .panel-title > .panel-collapse');
      if(btn){btn.setAttribute('aria-expanded',String(!collapsed));btn.title=collapsed?'Espandi':'Comprimi';}
    }
    function decorate(){
      const collapsed=load();
      root.querySelectorAll('.panel').forEach(panel=>{
        if(panel.dataset.collapsible)return;
        const head=panel.querySelector(':scope > .panel-title');
        const h2=head&&head.querySelector('h2');
        const title=h2&&h2.textContent.trim();
        if(!title||!TITLES.includes(title))return;
        panel.dataset.collapsible='1';
        const btn=document.createElement('button');
        btn.type='button';btn.className='panel-collapse';btn.setAttribute('aria-label','Comprimi o espandi: '+title);
        btn.innerHTML='<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><path d="M2 4.5 6 8.5 10 4.5" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
        head.appendChild(btn);
        head.classList.add('panel-title-collapsible');
        apply(panel,title,collapsed.has(title));
        const toggle=()=>{
          const set=load(),now=!panel.classList.contains('is-collapsed');
          if(now)set.add(title);else set.delete(title);
          save(set);
          // Stessa sezione con lo stesso titolo (es. viste diverse) resta coerente al prossimo render.
          apply(panel,title,now);
        };
        btn.addEventListener('click',e=>{e.stopPropagation();toggle();});
        head.addEventListener('click',e=>{if(!e.target.closest('button,a,input,select,label'))toggle();});
      });
    }
    new MutationObserver(decorate).observe(root,{childList:true,subtree:true});
    decorate();
  })();

  init();
})();
