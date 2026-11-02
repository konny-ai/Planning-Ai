/* Planning Factory — data screens, sources and AI settings.
 * Every value shown here comes from the server: the canonical input records after
 * the local corrections, the binding of the installation and the AI settings. Keys
 * never reach the browser. All identifiers are escaped before reaching the DOM.
 */
(function () {
  'use strict';

  const esc = (value) => String(value === undefined || value === null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  const $ = (selector, root) => (root || document).querySelector(selector);
  const nf = (n) => Number(n || 0).toLocaleString('it-IT');

  const GROUPS = [
    {view:'dati-ordini', label:'Commesse e fasi', icon:'≡', entities:['operation','order','operation_demand','operation_dependency'],
     sub:'Commesse con le loro righe, i lotti e le fasi di lavorazione: articolo, quantità, lavorazione, durate e consegne.'},
    {view:'dati-risorse', label:'Macchine e risorse', icon:'▤', entities:['resource'],
     sub:'Macchine, operatori e fornitori con calendario, capacità ed efficienza.'},
    {view:'dati-calendari', label:'Calendari', icon:'▧', entities:['calendar','calendar_shift','calendar_holiday','calendar_exception','calendar_exception_shift','calendar_closure'],
     sub:'Turni settimanali, festività, giorni speciali e fermi per calendario.'},
    {view:'dati-nesting', label:'Programmi di nesting', icon:'◈', entities:['nest','nest_member'],
     sub:'Programmi di taglio condivisi e fasi che contengono.'},
    {view:'dati-materiali', label:'Materiali', icon:'▥', entities:['material_supply','material_need','allocation_rank'],
     sub:'Giacenze e arrivi, fabbisogni delle fasi e ordine di assegnazione.'},
    {view:'dati-avanzamento', label:'Consuntivi', icon:'◷', entities:['operation_progress','operation_state'],
     sub:'Avanzamento registrato: fasi avviate o chiuse, pezzi e tempi.'},
    {view:'dati-contolavoro', label:'Conto lavoro', icon:'⇄', entities:['operation_external_end'],
     sub:'Date di fine dichiarate dai fornitori di conto lavoro.'},
    {view:'dati-regole', label:'Profili e regole', icon:'◇', entities:['profile','rule'],
     sub:'Profilo di calcolo e regole confermate arrivate con la fonte.'},
  ];
  const ADMIN = {
    fonti:['Fonti dati','Da dove arrivano i dati, quando sono stati letti e cosa segnala la diagnostica.'],
    correzioni:['Registro correzioni','Tutte le modifiche ai dati in ingresso, con autore, motivo e stato.'],
    impostazioni:['Impostazioni AI','Fornitore, modello per ruolo e limiti di spesa. Le chiavi restano sul server.'],
  };
  const ENT = {
    order:['Commessa','Commesse'], operation:['Fase','Fasi'], operation_demand:['Risorsa ammessa','Risorse per fase'],
    operation_dependency:['Precedenza','Precedenze'], resource:['Risorsa','Risorse'], calendar:['Calendario','Calendari'],
    calendar_shift:['Turno','Turni settimanali'], calendar_holiday:['Festività','Festività'],
    calendar_exception:['Giorno speciale','Giorni speciali'], calendar_exception_shift:['Turno speciale','Turni speciali'],
    calendar_closure:['Chiusura','Chiusure e fermi'], nest:['Programma','Programmi'], nest_member:['Fase nel programma','Fasi nei programmi'],
    material_supply:['Disponibilità','Disponibilità'], material_need:['Fabbisogno','Fabbisogni'],
    allocation_rank:['Precedenza materiale','Ordine di assegnazione'], operation_progress:['Consuntivo','Consuntivi'],
    operation_state:['Stato fase','Stati fase'], operation_external_end:['Fine conto lavoro','Fine conto lavoro'],
    profile:['Profilo','Profili di calcolo'], rule:['Regola','Regole sorgente'],
  };
  const DESC = {
    order:'Commessa: consegna, rilascio e priorità valgono per le fasi che non le dichiarano',
    operation:'Commesse, righe e lotti con le loro fasi di lavorazione, durata residua e setup',
    operation_demand:'Risorse ammesse da ogni alternativa di una fase',
    operation_dependency:'Una fase non può iniziare prima della precedente, più l’eventuale ritardo',
    resource:'Capacità vuota = illimitata ma soggetta al calendario; efficienza 10000 = 100 %',
    calendar:'Calendario con fuso orario', calendar_shift:'Turno di ogni giorno della settimana; se finisce prima di iniziare, termina il giorno dopo',
    calendar_holiday:'Giorno di chiusura', calendar_exception:'Giorno con turni diversi dal solito (anche nessuno)',
    calendar_exception_shift:'Turno di un giorno speciale', calendar_closure:'Fermo assoluto, ad esempio manutenzione',
    nest:'Programma di taglio: una sola lavorazione con tempo condiviso fra le fasi',
    nest_member:'Fase contenuta in un programma di taglio',
    material_supply:'Giacenza o arrivo previsto; data vuota = data non nota',
    material_need:'Materiale che una fase consuma', allocation_rank:'Ordine esplicito di assegnazione del materiale, minore prima',
    operation_progress:'Consuntivo: fase avviata o chiusa, con pezzi e tempi registrati',
    operation_state:'Fase chiusa, oppure avviata e bloccata su inizio e alternativa',
    operation_external_end:'Fine dichiarata dal fornitore di conto lavoro (non è un consuntivo)',
    profile:'Parametri del motore di calcolo', rule:'Regola con ambito confermata dal pianificatore',
  };
  const FIELD = {
    id:'Codice', order_id:'Commessa', operation_id:'Fase', resource_id:'Risorsa', calendar_id:'Calendario',
    nest_id:'Programma', predecessor_id:'Fase precedente', item_id:'Articolo', due:'Consegna',
    release:'Rilascio (non prima di)', priority:'Priorità', customer_priority:'Priorità cliente', mode:'Strategia',
    customer_id:'Cliente', duration_seconds:'Durata lavorazione', setup_seconds:'Setup', phase_priority:'Priorità fase',
    preferred_alternative:'Alternativa preferita', splittable:'Frazionabile', quantity:'Quantità', material:'Materiale',
    thickness_mm:'Spessore (mm)', alternative:'Alternativa', units:'Unità', kind:'Tipo', lag_seconds:'Ritardo',
    max_lag_seconds:'Attesa massima', status:'Stato', version:'Versione', sheets:'Lamiere', preview:'Anteprima',
    external_end:'Fine dichiarata', completed_at:'Chiusa il', fixed_start:'Inizio bloccato',
    fixed_alternative:'Alternativa bloccata', produced_quantity:'Pezzi prodotti',
    recorded_work_seconds:'Lavoro registrato', recorded_setup_seconds:'Setup registrato', timezone:'Fuso orario',
    capacity:'Capacità', efficiency_bp:'Efficienza (punti base)', weekday:'Giorno', start:'Inizio', end:'Fine',
    day:'Data', available_at:'Disponibile dal', reference:'Riferimento', rank:'Posizione', action:'Azione',
    value:'Valore', note:'Nota', min_thickness_mm:'Spessore oltre (mm)', max_thickness_mm:'Spessore fino a (mm)',
    profile:'Profilo', valid_from:'Valida da', valid_to:'Valida fino a', confirmed_by:'Confermata da',
    confirmed_at:'Confermata il', auto_backward_days:'Giorni soglia ritroso', priority_weight:'Peso priorità',
    issue_date:'Data inserimento', due_min:'Consegna (min righe)', due_max:'Consegna ultima riga',
    requested_min:'Data richiesta', lines:'Righe', phases:'Fasi', remaining_seconds:'Lavoro residuo',
    reference:'Riferimento cliente', description:'Descrizione',
    line:'Riga', lot:'Lotto', part:'Cod. articolo', part_description:'Descrizione articolo', phase_code:'Cod. lavorazione',
    phase:'Descrizione lavorazione', sequence:'Seq.', uom:'UM', size_mm:'Dimensioni (mm)', order_code:'Cod. commessa', customer:'Cliente', row_quantity:'Qtà ordinata (riga)', lot_quantity:'Qtà lotto', row_due:'Consegna riga', row_status:'Stato riga',
    due_weight:'Peso consegna', customer_weight:'Peso cliente',
    alternative_switch_min_gain_seconds:'Guadagno minimo per cambiare alternativa', auto_rule:'Criterio automatico',
    auto_slack_seconds:'Soglia di margine', delivery_buffer_seconds:'Margine di consegna',
    wip_priority_bonus:'Bonus lavori avviati', map:'Mappa', external_id:'Codice esterno', canonical_id:'Codice canonico',
  };
  const ENUM = {
    mode:{forward:'In avanti', backward:'A ritroso', auto:'Automatica'},
    dependency:{FS:'Fine → inizio', SS:'Inizio → inizio'},
    supply:{stock:'Giacenza', purchase_order:'Ordine fornitore', transfer:'Trasferimento', production:'Produzione'},
    nest_status:{draft:'Bozza', released:'Rilasciato', cut:'Tagliato'},
    auto_rule:{due_distance:'Distanza dalla consegna', slack:'Margine residuo'},
    progress_status:{started:'Avviata', completed:'Chiusa'},
    rule_action:{add_setup_seconds:'Setup aggiuntivo', set_priority:'Priorità ordine', set_customer_priority:'Priorità cliente',
      set_delivery_buffer_seconds:'Margine di consegna'},
    bool:{true:'Sì', false:'No'},
  };
  const KINDS = {machine:'Macchina', operator:'Operatore', tool:'Attrezzatura', supplier:'Fornitore', external:'Esterno'};
  const WEEKDAYS = ['Lunedì','Martedì','Mercoledì','Giovedì','Venerdì','Sabato','Domenica'];
  const STATUS = {source:['Fonte','neutral'], updated:['Modificato','amber'], inserted:['Aggiunto','green'], excluded:['Escluso','red']};
  const ACTION = {update:'Modifica', insert:'Aggiunta', exclude:'Esclusione'};
  const DOMAIN = {resources:'Risorse', calendars:'Calendari', orders:'Commesse', routings:'Cicli e fasi', nesting:'Nesting',
    materials:'Materiali', subcontract:'Conto lavoro', progress:'Avanzamento', rules:'Regole e profili', identity:'Corrispondenze codici'};
  const ISSUE = {
    material_shortage:'Materiale insufficiente', due_in_past:'Consegna già passata', due_before_release:'Consegna prima del rilascio',
    source_unavailable:'Sorgente non leggibile', duplicate_key:'Chiave duplicata', invalid_record:'Record non valido',
    source_conflict:'Sorgenti in disaccordo', source_override:'Valore scelto per precedenza', stale_data:'Dati troppo vecchi',
    correction_orphan:'Correzione senza record', correction_insert_exists:'Aggiunta già presente nella fonte',
    correction_invalid:'Correzione non valida', correction_unbound:'Correzione su dominio non collegato',
    unknown_reference:'Riferimento sconosciuto', missing_reference:'Riferimento mancante',
  };
  const TAB = {operation:'Commesse e fasi', order:'Solo testate commessa', operation_demand:'Risorse per fase', operation_dependency:'Precedenze'};
  const tabLabel = (entity) => TAB[entity] || plural(entity);
  const label = (entity, field) => FIELD[field] || field;
  const plural = (entity) => (ENT[entity] || [entity, entity])[1];
  const singular = (entity) => (ENT[entity] || [entity, entity])[0];

  const api = async (path, body) => {
    const response = await fetch(path, body === undefined ? {cache:'no-store'} :
      {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)});
    let value = {};
    try { value = await response.json(); } catch (_) { value = {}; }
    if (!response.ok) {
      const error = new Error(value.error || ('Errore ' + response.status));
      error.errors = value.errors || [value.error || ('Errore ' + response.status)];
      error.status = response.status;
      throw error;
    }
    return value;
  };

  // ------------------------------------------------------------------ formatting
  const dateTimeFormat = new Intl.DateTimeFormat('it-IT', {day:'2-digit', month:'2-digit', year:'numeric', hour:'2-digit', minute:'2-digit', hour12:false});
  const dateTime = {format: (d) => dateTimeFormat.format(d).replace(', ', ' ')};
  const dateOnly = new Intl.DateTimeFormat('it-IT', {day:'2-digit', month:'2-digit', year:'numeric'});
  function duration(seconds) {
    const s = Number(seconds);
    if (!Number.isFinite(s)) return '—';
    const sign = s < 0 ? '−' : '', a = Math.abs(s);
    if (a < 60) return sign + a + ' s';
    const h = Math.floor(a / 3600), m = Math.round((a % 3600) / 60);
    if (!h) return sign + m + ' min';
    return sign + h.toLocaleString('it-IT') + ' h' + (m ? ' ' + String(m).padStart(2, '0') + ' min' : '');
  }
  const customerText = (code) => S.ctx && S.ctx.customerName ? S.ctx.customerName(String(code)) : null;
  const orderText = (id) => {
    const text = S.ctx && S.ctx.orderLabel ? S.ctx.orderLabel(String(id)) : null;
    return text && text !== String(id) ? text : null;
  };
  const orderCodeText = (id) => {
    const code = S.ctx && S.ctx.orderCode ? S.ctx.orderCode(String(id)) : null;
    return code && code !== String(id) ? code : null;
  };
  function show(field, value, entity) {
    if (value === null || value === undefined || value === '') return '<span class="dv-empty">—</span>';
    const t = field.type;
    if (t === 'instant') { const d = new Date(value); return Number.isFinite(d.getTime()) ? '<span title="' + esc(value) + ' (UTC)">' + esc(dateTime.format(d)) + '</span>' : esc(value); }
    if (t === 'date') { const d = new Date(value + 'T00:00:00'); return Number.isFinite(d.getTime()) ? esc(dateOnly.format(d)) : esc(value); }
    if (t === 'time') return esc(String(value).slice(0, 5));
    if (t === 'weekday') return esc(WEEKDAYS[value] || value);
    if (t === 'bool') return value ? 'Sì' : 'No';
    if (ENUM[t]) return esc(ENUM[t][value] || value);
    if (/_seconds$/.test(field.name)) return '<span title="' + esc(nf(value)) + ' secondi">' + esc(duration(value)) + '</span>';
    if (t === 'decimal' || t === 'decimal0') return esc(String(value).replace('.', ','));
    if (field.name === 'kind' && KINDS[value]) return esc(KINDS[value]);
    if (field.name === 'efficiency_bp') return '<span title="' + esc(value) + ' punti base">' + esc((value / 100).toLocaleString('it-IT', {maximumFractionDigits:2})) + ' %</span>';
    // Company name and order code are presentation texts of the source: shown next to the value, never stored.
    const named = field.name === 'customer_id' ? customerText(value) : field.name === 'order_id' ? orderText(value) :
      entity === 'order' && field.name === 'id' ? orderCodeText(value) : null;
    if (named) return '<code>' + esc(value) + '</code> <span class="dv-named">' + esc(named) + '</span>';
    if (field.ref || t === 'id') return '<code>' + esc(value) + '</code>';
    return esc(value);
  }
  const plainValue = (field, value) => {
    if (value === null || value === undefined || value === '') return 'vuoto';
    const box = document.createElement('div'); box.innerHTML = show(field, value); return box.textContent;
  };
  function toLocalInput(value) {
    const d = new Date(value);
    if (!Number.isFinite(d.getTime())) return '';
    const p = (n) => String(n).padStart(2, '0');
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
  }

  // ------------------------------------------------------------------ state
  const S = {
    session:null, catalog:null, entities:{}, ctx:null, root:null, view:null,
    tab:{}, folded:new Set(), detail:null, query:{}, page:{}, sort:{}, status:{}, rows:{}, loading:false,
    selected:null, form:null, history:null, busy:false, planStale:false,
    sources:null, factorySource:null, factoryTest:null, factoryCreate:null, factoryOpen:new Set(), syncedJob:null, ai:null, models:{}, modelFilter:{provider:null, q:'', tools:true, images:false, free:false, order:'recenti'},
    aiDraft:null, ledger:null, tests:{},
  };
  const groupOf = (view) => GROUPS.find(g => g.view === view);
  const titles = {};
  for (const g of GROUPS) titles[g.view] = [g.label, g.sub];
  Object.assign(titles, ADMIN);

  async function boot() {
    try { S.session = await api('/api/session'); } catch (_) { S.session = null; return; }
    decorateShell();
    if (!S.session.data_screens) return;
    try { await loadCatalog(); } catch (error) { toast('Maschere dati non disponibili: ' + error.message); }
  }
  let catalogInFlight = null;
  function loadCatalog() {
    if (!catalogInFlight) catalogInFlight = fetchCatalog().finally(() => { catalogInFlight = null; });
    return catalogInFlight;
  }
  async function fetchCatalog() {
    S.catalog = await api('/api/data/catalog');
    S.entities = Object.fromEntries(S.catalog.entities.map(e => [e.name, e]));
    renderNav();
    if (S.catalog.startup_error) toast('Le correzioni salvate non si applicano più ai dati attuali: aprile dal Registro correzioni.', 9000);
  }
  function decorateShell() {
    const s = S.session; if (!s) return;
    const avatar = $('.avatar');
    if (avatar) {
      const initials = String(s.name || '?').split(/[\s._-]+/).filter(Boolean).slice(0, 2).map(x => x[0].toUpperCase()).join('') || '?';
      avatar.textContent = initials; avatar.title = s.name + ' · ' + s.role; avatar.setAttribute('aria-label', avatar.title);
    }
    const pill = $('.environment-pill');
    if (pill) pill.innerHTML = '<span class="environment-dot"></span> ' + (s.local ? 'ACCESSO LOCALE' : 'ACCESSO PERSONALE');
    const note = $('.sidebar-bottom p');
    if (note) note.innerHTML = esc(s.name) + ' · ' + esc({admin:'amministratore', planner:'pianificatore', reparto:'reparto'}[s.role] || s.role) +
      '<br>' + (s.input_editable === false ? 'Excel come sorgente unica' : 'Fonti in sola lettura · correzioni tracciate');
    const admin = $('#admin-nav-group');
    if (admin) admin.hidden = false;
    const settings = document.querySelector('[data-view="impostazioni"]');
    if (settings && s.role === 'reparto') settings.hidden = true;
  }
  function renderNav() {
    const nav = $('#data-nav'), group = $('#data-nav-group');
    if (!nav || !group) return;
    const visible = GROUPS.filter(g => g.entities.some(e => S.entities[e]));
    nav.innerHTML = visible.map(g => {
      const count = S.session.input_editable === false ? (S.entities[g.entities[0]]?.records || 0) :
        g.entities.reduce((n, e) => n + (S.entities[e] ? S.entities[e].records : 0), 0);
      const fixes = g.entities.reduce((n, e) => n + (S.entities[e] ? S.entities[e].corrections : 0), 0);
      return '<button class="nav-item' + (S.view === g.view ? ' active' : '') + '" data-view="' + g.view + '" type="button">' +
        '<span class="nav-icon">' + g.icon + '</span> ' + esc(g.label) +
        (fixes ? '<span class="nav-counter amber" title="Correzioni attive">' + fixes + '</span>' :
          '<span class="nav-count">' + esc(count > 9999 ? Math.round(count / 1000) + 'k' : nf(count)) + '</span>') + '</button>';
    }).join('');
    group.hidden = !visible.length;
    const ledger = document.querySelector('[data-view="correzioni"] .nav-counter');
    if (ledger) { ledger.textContent = String(S.catalog.corrections_active); ledger.hidden = !S.catalog.corrections_active; }
  }
  function toast(message, timeout) { if (S.ctx) S.ctx.setToast(message, timeout || 6000); }
  function markStale() {
    S.planStale = true;
    const banner = $('#data-stale'); if (banner) banner.hidden = false;
    document.dispatchEvent(new CustomEvent('pf:data-changed'));
  }
  document.addEventListener('pf:plan-recalculated', () => { S.planStale = false; const b = $('#data-stale'); if (b) b.hidden = true; });

  // ------------------------------------------------------------------ data grids
  function currentEntity(group) {
    const available = group.entities.filter(e => S.entities[e]);
    const tab = S.tab[group.view];
    return available.includes(tab) ? tab : available[0];
  }
  async function loadRows(entity) {
    const page = S.page[entity] || {offset:0, limit:50};
    const sort = S.sort[entity] || {};
    const params = new URLSearchParams({entity, offset:String(page.offset), limit:String(page.limit)});
    if (S.query[entity]) params.set('q', S.query[entity]);
    if (S.status[entity]) params.set('status', S.status[entity]);
    if (sort.field) { params.set('sort', sort.field); if (sort.desc) params.set('dir', 'desc'); }
    S.loading = true;
    try { S.rows[entity] = await api('/api/data/rows?' + params.toString()); }
    finally { S.loading = false; }
  }
  function canEdit() { return S.session && S.session.role !== 'reparto' && S.session.input_editable !== false; }

  function gridHTML(group) {
    const entity = currentEntity(group);
    if (!entity) return '<article class="panel">' + empty('Dominio non collegato', 'Questa installazione non legge questi dati.', '▤') + '</article>';
    const spec = S.entities[entity], data = S.rows[entity];
    const tabs = group.entities.filter(e => S.entities[e]);
    const status = S.status[entity] || '';
    const sort = S.sort[entity] || {};
    const allFields = spec.fields.concat((spec.extras || []).map(x => Object.assign({extra:true}, x)));
    const emptyCols = allFields.filter(f => !spec.key.includes(f.name) && !f.filled);
    let columns = S.showEmpty ? allFields : allFields.filter(f => !emptyCols.includes(f));
    // Commesse e fasi: without an explicit sort the phases are grouped under order and lot headers.
    const grouped = entity === 'operation' && !sort.field;
    if (grouped) {
      const shown = columns;
      columns = ['phase_code', 'phase', 'sequence', 'quantity', 'uom', 'material', 'thickness_mm', 'size_mm', 'duration_seconds',
        'setup_seconds', 'release', 'due', 'priority'].map(n => allFields.find(f => f.name === n)).filter(f => f && shown.includes(f));
    }
    let out = '<article class="panel dv-panel">';
    if(S.session.input_editable === false) out += '<div class="info-panel">Dati letti dall’Excel. Per modificarli salva il file e premi Rileggi Excel nelle Fonti.</div>';
    out += '<div class="panel-title"><div><h2>' + esc(tabLabel(entity)) + '</h2><p class="panel-sub">' + esc(DESC[entity] || spec.description) +
      ' · chiave: ' + spec.key.map(k => esc(label(entity, k))).join(' + ') + '</p></div><div class="panel-tools">' +
      (canEdit() ? '<button class="btn btn-primary btn-small" type="button" data-dv="new">＋ Nuova ' + esc(singular(entity).toLowerCase()) + '</button>' : '') +
      '</div></div>';
    if (tabs.length > 1) out += '<div class="dv-tabs" role="tablist">' + tabs.map(e => '<button type="button" role="tab" aria-selected="' + (e === entity) +
      '" class="' + (e === entity ? 'active' : '') + '" data-dv-tab="' + e + '">' + esc(tabLabel(e)) + ' <span>' + esc(nf(S.entities[e].records)) + '</span>' +
      (S.entities[e].corrections ? '<i class="dv-dot" title="Correzioni attive"></i>' : '') + '</button>').join('') + '</div>';
    out += '<div class="dv-toolbar"><label class="dv-search"><span aria-hidden="true">⌕</span><input id="dv-search" type="search" autocomplete="off" ' +
      'placeholder="Cerca in ' + esc(plural(entity).toLowerCase()) + '…" value="' + esc(S.query[entity] || '') + '" aria-label="Cerca"></label>' +
      '<div class="segmented" role="group" aria-label="Filtra per stato">' +
      [['', 'Tutti'], ['corrected', 'Corretti'], ['updated', 'Modificati'], ['inserted', 'Aggiunti'], ['excluded', 'Esclusi']].map(([v, l]) =>
        '<button type="button" data-dv-status="' + v + '" class="' + (status === v ? 'active' : '') + '">' + l + '</button>').join('') + '</div>' +
      (emptyCols.length ? '<label class="dv-toggle" title="' + esc(emptyCols.map(f => label(entity, f.name)).join(', ')) + '"><input type="checkbox" id="dv-show-empty"' +
        (S.showEmpty ? ' checked' : '') + '> ' + (emptyCols.length === 1 ? 'mostra 1 colonna vuota' : 'mostra ' + emptyCols.length + ' colonne vuote') + '</label>' : '') +
      '<span class="dv-total">' + (data ? esc(nf(data.total)) + ' righe' : 'Caricamento…') + '</span></div>';
    if (!data) return out + '<div class="dv-loading"><span></span><span></span><span></span></div></article>';
    if (!data.rows.length) return out + empty(S.query[entity] || status ? 'Nessuna riga corrisponde' : 'Nessun dato',
      S.query[entity] || status ? 'Cambia la ricerca o il filtro di stato.' : 'La fonte non contiene record per questa tabella. Puoi aggiungerne uno con “Nuova”.', '⌕') + '</article>';
    out += '<div class="table-scroll dv-scroll"><table class="data-table dv-table"><thead><tr><th class="dv-state-col">Stato</th>' +
      columns.map(f => {
        const active = sort.field === f.name;
        return '<th aria-sort="' + (active ? (sort.desc ? 'descending' : 'ascending') : 'none') + '"><button type="button" class="dv-sort' + (active ? ' active' : '') +
          '" data-dv-sort="' + f.name + '" title="Ordina per ' + esc(label(entity, f.name)) + '">' + esc(label(entity, f.name)) +
          (spec.key.includes(f.name) ? ' <span class="dv-key" title="Parte della chiave">⚿</span>' : '') +
          '<span class="dv-arrow">' + (active ? (sort.desc ? '▼' : '▲') : '↕') + '</span></button></th>';
      }).join('') + '</tr></thead><tbody>';
    let lastOrder = null, lastLot = null, folded = false;
    for (const row of data.rows) {
      const id = JSON.stringify(row.key);
      const st = STATUS[row.status];
      if (grouped) {
        const order = String(row.values.order_id), x = row.extra || {}, lot = order + '/' + x.lot;
        if (order !== lastOrder) {
          lastOrder = order; lastLot = null; folded = S.folded.has(order);
          out += '<tr class="dv-group dv-g-order" data-dv-fold="' + esc(order) + '" tabindex="0"><td colspan="' + (columns.length + 1) + '"><span class="dv-fold">' +
            (folded ? '▸' : '▾') + '</span> <strong>' + esc(orderText(order) || [x.order_code, x.customer].filter(Boolean).join(' · ') || order) + '</strong> <span class="dv-named">commessa ' + esc(order) + '</span></td></tr>';
        }
        if (folded) continue;
        if (lot !== lastLot) {
          lastLot = lot;
          const q = row.values.quantity, dims = [row.values.material, row.values.thickness_mm ? row.values.thickness_mm + ' mm' : null, x.size_mm].filter(Boolean);
          out += '<tr class="dv-group dv-g-lot"><td></td><td colspan="' + columns.length + '">' +
            (x.line ? 'Riga ' + esc(x.line) + ' · ' : '') + 'Lotto ' + esc(x.lot) + ' · ' + (x.part ? '<code>' + esc(x.part) + '</code>' : '<span class="dv-empty">articolo non indicato</span>') +
            (x.part_description ? ' <span class="dv-named">' + esc(x.part_description) + '</span>' : '') +
            (x.row_quantity ? ' · ordinata <strong>' + esc(String(x.row_quantity).replace('.', ',')) + (x.uom ? ' ' + esc(x.uom) : '') + '</strong>' : '') +
            (x.lot_quantity ? ' · lotto <strong>' + esc(String(x.lot_quantity).replace('.', ',')) + (x.uom ? ' ' + esc(x.uom) : '') + '</strong>' :
              q ? ' · <strong>' + esc(String(q).replace('.', ',')) + (x.uom ? ' ' + esc(x.uom) : '') + '</strong>' : '') +
            (x.row_due ? ' · consegna riga <span class="dv-named">' + esc(x.row_due.split('-').reverse().join('/')) + '</span>' : '') +
            (x.row_status ? ' · <span class="dv-named">' + esc(x.row_status) + '</span>' : '') +
            (dims.length ? ' · <span class="dv-named">' + esc(dims.join(' · ')) + '</span>' : '') + '</td></tr>';
        }
      }
      const selected = S.selected && S.selected.entity === entity && JSON.stringify(S.selected.key) === id;
      out += '<tr class="dv-row dv-' + row.status + (selected ? ' selected' : '') + '" tabindex="0" data-dv-row="' + esc(id) + '">' +
        '<td class="dv-state-col"><span class="dv-badge ' + st[1] + '">' + st[0] + '</span></td>' +
        columns.map(f => '<td class="' + (spec.key.includes(f.name) ? 'dv-keycell' : '') + '">' +
          show(f, f.extra ? (row.extra || {})[f.name] : row.values[f.name], entity) + '</td>').join('') + '</tr>';
    }
    out += '</tbody></table></div>';
    const from = data.offset + 1, to = data.offset + data.rows.length;
    out += '<div class="dv-pager"><span>' + esc(nf(from)) + '–' + esc(nf(to)) + ' di ' + esc(nf(data.total)) + '</span>' +
      '<label>Righe per pagina <select id="dv-limit">' + [25, 50, 100, 200].map(n => '<option ' + (n === data.limit ? 'selected' : '') + '>' + n + '</option>').join('') + '</select></label>' +
      '<div class="dv-pages"><button class="icon-button" type="button" data-dv="first" ' + (data.offset ? '' : 'disabled') + ' aria-label="Prima pagina">«</button>' +
      '<button class="icon-button" type="button" data-dv="prev" ' + (data.offset ? '' : 'disabled') + ' aria-label="Pagina precedente">‹</button>' +
      '<button class="icon-button" type="button" data-dv="next" ' + (to < data.total ? '' : 'disabled') + ' aria-label="Pagina successiva">›</button>' +
      '<button class="icon-button" type="button" data-dv="last" ' + (to < data.total ? '' : 'disabled') + ' aria-label="Ultima pagina">»</button></div></div>';
    return out + '</article>';
  }
  function empty(title, text, icon) {
    return '<div class="empty-state"><div class="empty-icon">' + esc(icon || 'ⓘ') + '</div><h3>' + esc(title) + '</h3><p>' + esc(text) + '</p></div>';
  }
  function dataIntro(group) {
    if(S.session.input_editable === false) return '';
    const entity = currentEntity(group);
    const fixes = group.entities.reduce((n, e) => n + (S.entities[e] ? S.entities[e].corrections : 0), 0);
    return '<div class="dv-intro"><div class="info-panel"><strong>Dati in ingresso al motore.</strong> Le righe arrivano dalle fonti in sola lettura; ' +
      'ogni modifica diventa una <em>correzione</em> con autore e motivo, verificata ricostruendo l’intero scenario, che resta valida anche dopo una nuova estrazione. ' +
      (fixes ? '<a href="#correzioni" data-view="correzioni">' + fixes + ' correzioni attive in questa sezione →</a>' : 'Nessuna correzione attiva in questa sezione.') +
      '</div>' + (entity && S.entities[entity].excluded ? '<span class="status-pill bad">' + nf(S.entities[entity].excluded) + ' esclusi</span>' : '') + '</div>';
  }

  // ------------------------------------------------------------------ editor (inspector)
  function inspector(html, title) {
    $('#inspector-title').textContent = title;
    $('#inspector-body').innerHTML = html;
    document.body.classList.add('dv-editing');
    if (window.innerWidth <= 1100) window.dispatchEvent(new CustomEvent('pf:inspector-open', {detail:true}));
  }
  function inspectorIdle(group) {
    $('#inspector-title').textContent = 'Seleziona una riga';
    $('#inspector-body').innerHTML = '<div class="inspector-empty"><span class="empty-illustration">✎</span><strong>' + (S.session.input_editable === false ? 'Consulta i dati.' : 'Vedi e correggi i dati.') + '</strong>' +
      '<p>Seleziona una riga per vederne tutti i campi, la provenienza e lo storico delle correzioni' + (canEdit() ? ', oppure crea una nuova riga.' : '.') + '</p></div>';
  }
  function inputFor(entity, field, value, mode) {
    const id = 'dv-f-' + field.name;
    const isKey = S.entities[entity].key.includes(field.name);
    const locked = !canEdit() || (mode === 'update' && isKey) || mode === 'view';
    const dis = locked ? ' disabled' : '';
    const req = field.required && !field.nullable;
    const t = field.type;
    let control;
    const v = value === null || value === undefined ? '' : value;
    if (ENUM[t] || t === 'weekday' || t === 'bool') {
      const options = t === 'weekday' ? WEEKDAYS.map((w, i) => [String(i), w]) :
        t === 'bool' ? [['true', 'Sì'], ['false', 'No']] : Object.entries(ENUM[t]);
      control = '<select class="field-select" id="' + id + '" name="' + field.name + '"' + dis + '>' +
        (req ? '' : '<option value="">— vuoto —</option>') +
        options.map(([k, l]) => '<option value="' + esc(k) + '"' + (String(v) === k ? ' selected' : '') + '>' + esc(l) + '</option>').join('') + '</select>';
    } else if (t === 'instant') {
      control = '<input class="field-input" type="datetime-local" id="' + id + '" name="' + field.name + '" value="' + esc(v ? toLocalInput(v) : '') + '"' + dis + '>';
    } else if (t === 'date') {
      control = '<input class="field-input" type="date" id="' + id + '" name="' + field.name + '" value="' + esc(v) + '"' + dis + '>';
    } else if (t === 'time') {
      control = '<input class="field-input" type="time" step="1" id="' + id + '" name="' + field.name + '" value="' + esc(v) + '"' + dis + '>';
    } else if (['int', 'nonneg', 'positive'].includes(t)) {
      control = '<input class="field-input" type="number" step="1" ' + (t === 'int' ? '' : 'min="' + (t === 'positive' ? 1 : 0) + '"') +
        ' id="' + id + '" name="' + field.name + '" value="' + esc(v) + '"' + dis + '>';
    } else if (t === 'decimal' || t === 'decimal0') {
      control = '<input class="field-input" type="text" inputmode="decimal" id="' + id + '" name="' + field.name + '" value="' + esc(String(v).replace('.', ',')) + '"' + dis + '>';
    } else {
      const list = field.ref ? ' list="dv-list-' + field.ref + '"' : field.name === 'kind' ? ' list="dv-list-kind"' : '';
      control = '<input class="field-input' + (field.ref || t === 'id' ? ' mono' : '') + '" type="text" id="' + id + '" name="' + field.name + '" value="' + esc(v) + '"' +
        list + (field.ref ? ' data-dv-ref="' + field.ref + '"' : '') + ' autocomplete="off"' + dis + '>';
    }
    let hint = '';
    if (/_seconds$/.test(field.name)) hint = '<small class="dv-hint" data-dv-dur="' + field.name + '">' + (v !== '' ? '= ' + esc(duration(v)) : 'in secondi') + '</small>';
    else if (t === 'instant') hint = '<small class="dv-hint">ora locale</small>';
    else if (field.name === 'efficiency_bp') hint = '<small class="dv-hint">10000 = 100 %</small>';
    else if (field.name === 'customer_id' && v !== '' && customerText(v)) hint = '<small class="dv-hint">' + esc(customerText(v)) + '</small>';
    else if (field.name === 'order_id' && v !== '' && orderText(v)) hint = '<small class="dv-hint">' + esc(orderText(v)) + '</small>';
    else if (field.nullable) hint = '<small class="dv-hint">vuoto = non dichiarato</small>';
    else if (field.default !== null && field.default !== undefined) hint = '<small class="dv-hint">vuoto = ' + esc(plainValue(field, field.default)) + '</small>';
    return '<div class="dv-field' + (isKey ? ' is-key' : '') + '"><label for="' + id + '">' + esc(label(entity, field.name)) +
      (req ? ' <b title="Obbligatorio">*</b>' : '') + (isKey ? ' <span class="dv-key" title="Parte della chiave">⚿</span>' : '') + '</label>' + control + hint + '</div>';
  }
  function readField(field, raw) {
    const t = field.type, text = String(raw === undefined ? '' : raw).trim();
    if (text === '') return null;
    if (t === 'bool') return text === 'true';
    if (['int', 'nonneg', 'positive', 'weekday'].includes(t)) { const n = Number(text); return Number.isInteger(n) ? n : text; }
    if (t === 'instant') { const d = new Date(text); return Number.isFinite(d.getTime()) ? d.toISOString().replace(/\.\d{3}Z$/, 'Z') : text; }
    if (t === 'time') return text.length === 5 ? text + ':00' : text;
    if (t === 'decimal' || t === 'decimal0') return text.replace(/\s/g, '').replace(',', '.');
    return text;
  }
  async function openRow(entity, key) {
    const data = S.rows[entity];
    const row = data && data.rows.find(r => JSON.stringify(r.key) === JSON.stringify(key));
    if (!row) return;
    S.selected = {entity, key, row};
    S.history = null;
    S.detail = null;
    renderEditor();
    if (entity === 'operation') {
      loadOperationDetail(String(key[0])).then(detail => {
        if (S.selected && S.selected.row === row) { S.detail = detail; renderEditor(); highlight(); }
      }).catch(() => { if (S.selected && S.selected.row === row) { S.detail = {demand:[], dependency:[]}; renderEditor(); } });
    }
    try { S.history = (await api('/api/data/history?entity=' + encodeURIComponent(entity) + '&key=' + encodeURIComponent(JSON.stringify(key)))).history; }
    catch (_) { S.history = []; }
    if (S.selected && S.selected.row === row) renderEditor();
    highlight();
  }
  async function loadOperationDetail(id) {
    const one = async (entity) => !S.entities[entity] ? [] :
      (await api('/api/data/rows?entity=' + entity + '&limit=200&f.operation_id=' + encodeURIComponent(id))).rows.filter(r => r.values.operation_id === id);
    const [demand, dependency] = await Promise.all([one('operation_demand'), one('operation_dependency')]);
    return {demand, dependency};
  }
  function operationFacts(entity, row) {
    const x = row && row.extra;
    if (entity !== 'operation' || !x) return '';
    const v = row.values, qty = v.quantity ? String(v.quantity).replace('.', ',') + (x.uom ? ' ' + x.uom : '') : null;
    const dims = [v.material, v.thickness_mm ? v.thickness_mm + ' mm' : null, x.size_mm].filter(Boolean).join(' · ');
    const item = (k, html) => html ? '<div><dt>' + k + '</dt><dd>' + html + '</dd></div>' : '';
    return '<dl class="dv-facts">' +
      item('Commessa', esc(orderText(v.order_id) || v.order_id)) +
      item('Riga · lotto', esc([x.line ? 'riga ' + x.line : null, x.lot ? 'lotto ' + x.lot : null].filter(Boolean).join(' · '))) +
      item('Articolo', (x.part ? '<code>' + esc(x.part) + '</code>' : '') + (x.part_description ? ' ' + esc(x.part_description) : '')) +
      item('Quantità', esc(qty) + (x.row_quantity ? ' <span class="dv-named">ordinata ' + esc(String(x.row_quantity).replace('.', ',')) + ' · lotto ' + esc(String(x.lot_quantity || '—').replace('.', ',')) + '</span>' : '')) +
      item('Lavorazione', (x.phase_code ? '<code>' + esc(x.phase_code) + '</code>' : '') + (x.phase && x.phase !== x.phase_code ? ' ' + esc(x.phase) : '') +
        (x.sequence ? ' <span class="dv-named">seq. ' + esc(x.sequence) + '</span>' : '')) +
      item('Stato riga', esc(x.row_status)) +
      item('Materiale', esc(dims)) + '</dl>';
  }
  function operationDetail(sel) {
    if (sel.entity !== 'operation' || !sel.row) return '';
    const d = S.detail, id = String(sel.key[0]);
    const open = (entity) => S.entities[entity] ? '<button type="button" class="btn btn-muted btn-small" data-dv-open="' + esc(JSON.stringify([entity, [id]])) + '">Apri nella tabella</button>' : '';
    let html = '<div class="detail-section dv-detail"><h3>RISORSE AMMESSE</h3>';
    if (!d) return html + '<p class="panel-sub">Caricamento…</p></div>';
    if (!d.demand.length) html += '<p class="panel-sub">Nessuna risorsa indicata dalla fonte.</p>';
    else {
      // One alternative per machine/operator pair would repeat the machine a hundred times: list each resource once, by kind.
      const kinds = {MAC:[], OP:[], other:[]}, seen = new Set();
      for (const r of d.demand) {
        if (seen.has(r.values.resource_id)) continue;
        seen.add(r.values.resource_id);
        kinds[r.values.resource_id.startsWith('MAC:') ? 'MAC' : r.values.resource_id.startsWith('OP:') ? 'OP' : 'other'].push(r.values);
      }
      const title = (r) => S.ctx && S.ctx.resourceTitle ? S.ctx.resourceTitle(r.resource_id) : r.resource_id;
      const chips = (list) => list.map(r => '<span class="dv-chip">' + esc(title(r)) + (r.units > 1 ? ' ×' + esc(r.units) : '') + '</span>').join('');
      const alternatives = new Set(d.demand.map(r => r.values.alternative)).size;
      if (kinds.MAC.length) html += '<div class="dv-alt"><strong>Macchine</strong> ' + chips(kinds.MAC) + '</div>';
      if (kinds.OP.length) html += '<details class="dv-alt"><summary><strong>Operatori ammessi</strong> ' + kinds.OP.length + '</summary><div class="dv-alt">' + chips(kinds.OP) + '</div></details>';
      if (kinds.other.length) html += '<div class="dv-alt"><strong>Altre risorse</strong> ' + chips(kinds.other) + '</div>';
      html += '<p class="panel-sub">' + esc(alternatives) + (alternatives === 1 ? ' alternativa' : ' alternative') + ' di assegnazione (macchina + operatore).</p>';
    }
    html += open('operation_demand') + '</div><div class="detail-section dv-detail"><h3>PRECEDENZE</h3>';
    if (!d.dependency.length) html += '<p class="panel-sub">Nessuna fase precedente.</p>';
    else html += d.dependency.map(r => '<div class="dv-alt"><code>' + esc(r.values.predecessor_id) + '</code> <span class="dv-named">' +
      esc(ENUM.dependency[r.values.kind] || r.values.kind) + (r.values.lag_seconds ? ' · ritardo ' + esc(duration(r.values.lag_seconds)) : '') + '</span></div>').join('');
    return html + open('operation_dependency') + '</div>';
  }
  function highlight() {
    document.querySelectorAll('.dv-row').forEach(el => el.classList.toggle('selected',
      Boolean(S.selected && S.selected.key && el.dataset.dvRow === JSON.stringify(S.selected.key))));
  }
  function renderEditor(errors) {
    const sel = S.selected; if (!sel) return;
    const spec = S.entities[sel.entity];
    const isNew = !sel.row;
    const row = sel.row || {values:{}, status:'new', sources:[], corrections:[]};
    const mode = isNew ? 'insert' : row.status === 'excluded' ? 'view' : 'update';
    const title = isNew ? 'Nuova ' + singular(sel.entity).toLowerCase() : singular(sel.entity) + ' ' + sel.key.join(' · ');
    let html = '';
    if (!isNew) {
      const st = STATUS[row.status];
      html += '<div class="dv-origin"><span class="dv-badge ' + st[1] + '">' + st[0] + '</span><span>Fonte: <strong>' +
        esc(row.sources.length ? row.sources.join(', ') : 'nessuna, aggiunta a mano') + '</strong></span>' +
        (row.corrections.length ? '<span>Correzioni: ' + row.corrections.map(i => '#' + i).join(', ') + '</span>' : '') + '</div>';
    } else {
      html += '<p class="panel-sub dv-lead">Compila i campi: quelli con * sono obbligatori. La riga si aggiunge ai dati della fonte come correzione.</p>';
    }
    if (row.status === 'excluded') html += '<div class="info-panel dv-warn">Questa riga è esclusa dal calcolo. Ripristinala per farla tornare nel piano.</div>';
    html += operationFacts(sel.entity, row);
    html += '<form id="dv-form" novalidate>' + spec.fields.map(f => inputFor(sel.entity, f, row.values[f.name], mode)).join('');
    if (canEdit()) {
      html += '<div class="dv-field dv-reason"><label for="dv-reason">Motivo della modifica <b>*</b></label>' +
        '<textarea id="dv-reason" name="reason" rows="2" maxlength="500" placeholder="Es. data di consegna confermata dal cliente il 23/10"></textarea></div>';
      if (errors && errors.length) html += '<div class="dv-errors" role="alert"><strong>Correzione non salvata</strong><ul>' +
        errors.slice(0, 12).map(e => '<li>' + esc(e) + '</li>').join('') + '</ul></div>';
      html += '<div class="inspector-buttons dv-actions">';
      if (S.busy) html += '<button class="btn btn-primary" type="button" disabled>Verifica sull’intero scenario…</button>';
      else if (mode === 'insert') html += '<button class="btn btn-primary" type="submit">Aggiungi riga</button><button class="btn btn-muted" type="button" data-dv="cancel">Annulla</button>';
      else if (mode === 'update') html += '<button class="btn btn-primary" type="submit">Salva correzione</button>' +
        '<button class="btn btn-danger" type="button" data-dv="exclude">Escludi dal calcolo</button>';
      if (!isNew && row.corrections.length && !S.busy) html += '<button class="btn btn-muted" type="button" data-dv="restore">' +
        (row.status === 'inserted' ? 'Elimina riga aggiunta' : 'Ripristina originale') + '</button>';
      html += '</div>' + (S.busy ? '<p class="dv-note">La correzione viene provata ricostruendo tutti i dati: richiede qualche secondo.</p>' : '');
    } else {
      html += '<p class="dv-note">Profilo di sola lettura: le correzioni richiedono il ruolo pianificatore.</p>';
    }
    html += '</form>';
    if (!isNew) {
      html += operationDetail(sel);
      html += '<div class="detail-section dv-history"><h3>STORICO CORREZIONI</h3>';
      if (S.history === null) html += '<p class="panel-sub">Caricamento…</p>';
      else if (!S.history.length) html += '<p class="panel-sub">Nessuna correzione: valori come nella fonte.</p>';
      else html += S.history.map(h => historyItem(h, spec)).join('');
      html += '</div>';
    }
    inspector(html, title);
  }
  function historyItem(h, spec) {
    const fields = Object.keys(h.values || {});
    const change = h.action === 'update' ? fields.map(name => {
      const f = spec ? spec.fields.find(x => x.name === name) : {name, type:'text'};
      const before = h.previous ? h.previous[name] : undefined;
      return '<li><span>' + esc(label(h.entity, name)) + '</span> ' + (before !== undefined ? esc(plainValue(f || {name}, before)) + ' → ' : '') +
        '<strong>' + esc(plainValue(f || {name}, h.values[name])) + '</strong></li>';
    }).join('') : '';
    return '<div class="dv-hist' + (h.active ? '' : ' revoked') + '"><div class="dv-hist-head"><strong>#' + h.id + ' · ' + esc(ACTION[h.action] || h.action) + '</strong>' +
      '<span class="dv-badge ' + (h.active ? 'green' : 'neutral') + '">' + (h.active ? 'attiva' : 'annullata') + '</span></div>' +
      '<p>' + esc(h.actor) + ' · ' + esc(dateTime.format(new Date(h.created_at))) + '</p>' +
      (change ? '<ul>' + change + '</ul>' : '') + '<p class="dv-why">“' + esc(h.reason) + '”</p>' +
      (h.active ? '' : '<p class="dv-revoked">Annullata da ' + esc(h.revoked_by) + ' · ' + esc(dateTime.format(new Date(h.revoked_at))) + ' · “' + esc(h.revoke_reason) + '”</p>') + '</div>';
  }
  async function submitEditor(kind) {
    const sel = S.selected; if (!sel || S.busy) return;
    const spec = S.entities[sel.entity];
    const form = $('#dv-form'); if (!form) return;
    const reason = String($('#dv-reason').value || '').trim();
    const local = [];
    if (reason.length < 3) local.push('Scrivi il motivo della modifica (almeno 3 caratteri).');
    let body;
    if (kind === 'restore') {
      if (local.length) { renderEditor(local); $('#dv-reason').focus(); return; }
      body = null;
    } else if (kind === 'exclude') {
      body = {entity:sel.entity, action:'exclude', key:sel.key, values:{}, reason};
    } else if (!sel.row) {
      const values = {};
      for (const f of spec.fields) {
        const value = readField(f, form.elements[f.name] && form.elements[f.name].value);
        if (value === null) { if (f.required && !f.nullable) local.push(label(sel.entity, f.name) + ': obbligatorio.'); else if (f.nullable && f.required) values[f.name] = null; continue; }
        values[f.name] = value;
      }
      body = {entity:sel.entity, action:'insert', values, reason};
    } else {
      const values = {};
      for (const f of spec.fields) {
        if (spec.key.includes(f.name) || !form.elements[f.name]) continue;
        const value = readField(f, form.elements[f.name].value);
        const before = sel.row.values[f.name] === undefined ? null : sel.row.values[f.name];
        const same = f.type === 'instant' && value && before ? new Date(value).getTime() === new Date(before).getTime() :
          JSON.stringify(value) === JSON.stringify(before) || (f.type.startsWith('decimal') && value !== null && before !== null && Number(value) === Number(before));
        if (!same) values[f.name] = value;
      }
      if (!Object.keys(values).length) local.push('Nessun campo è cambiato.');
      body = {entity:sel.entity, action:'update', key:sel.key, values, reason};
    }
    if (local.length) { const keep = captureForm(); renderEditor(local); restoreForm(keep); return; }
    const keep = captureForm();
    S.busy = true; renderEditor(); restoreForm(keep);
    try {
      let result;
      if (kind === 'restore') result = await api('/api/data/corrections/revoke', {ids:sel.row.corrections, reason});
      else result = await api('/api/data/corrections', body);
      S.busy = false;
      toast(kind === 'restore' ? 'Riga ripristinata: le correzioni sono annullate e restano nel registro.' :
        'Correzione #' + result.correction.id + ' salvata e verificata sull’intero scenario. Ricalcola il piano per vederne l’effetto.', 8000);
      markStale();
      await loadCatalog();
      await loadRows(sel.entity);
      const key = body && body.action === 'insert' ? result.correction.key : sel.key;
      const fresh = S.rows[sel.entity].rows.find(r => JSON.stringify(r.key) === JSON.stringify(key));
      if (fresh) await openRow(sel.entity, key);
      else { S.selected = null; inspectorIdle(); }
      paint();
    } catch (error) {
      S.busy = false;
      renderEditor(error.errors || [error.message]); restoreForm(keep);
    }
  }
  function captureForm() {
    const form = $('#dv-form'); if (!form) return null;
    return Array.from(form.elements).filter(e => e.name).map(e => [e.name, e.value]);
  }
  function restoreForm(values) {
    const form = $('#dv-form'); if (!form || !values) return;
    for (const [name, value] of values) if (form.elements[name] && !form.elements[name].disabled) form.elements[name].value = value;
  }
  const refCache = {};
  async function suggestIds(input) {
    const ref = input.dataset.dvRef, q = input.value;
    let list = document.getElementById('dv-list-' + ref);
    if (!list) { list = document.createElement('datalist'); list.id = 'dv-list-' + ref; document.body.append(list); }
    const cacheKey = ref + '|' + q;
    try {
      const ids = refCache[cacheKey] || (refCache[cacheKey] = (await api('/api/data/ids?ref=' + encodeURIComponent(ref) + '&q=' + encodeURIComponent(q))).ids);
      list.innerHTML = ids.map(id => '<option value="' + esc(id) + '">').join('');
    } catch (_) { /* suggestions are a convenience */ }
  }

  // ------------------------------------------------------------------ sources page
  function bytes(n) { return n > 1048576 ? (n / 1048576).toLocaleString('it-IT', {maximumFractionDigits:1}) + ' MB' : Math.max(1, Math.round(n / 1024)).toLocaleString('it-IT') + ' kB'; }
  function ago(iso) {
    if (!iso) return '—';
    const d = new Date(iso), diff = (Date.now() - d.getTime()) / 1000;
    const hours = Math.round(diff / 3600), days = Math.round(diff / 86400);
    const rel = diff < 90 ? 'adesso' : diff < 5400 ? Math.round(diff / 60) + ' min fa' :
      diff < 129600 ? (hours === 1 ? '1 ora fa' : hours + ' ore fa') : (days === 1 ? 'ieri' : days + ' giorni fa');
    return '<span title="' + esc(dateTime.format(d)) + '">' + esc(dateTime.format(d)) + ' <small>(' + rel + ')</small></span>';
  }
  function sourcesHTML() {
    const s = S.sources;
    if (!s) return '<div class="dv-loading"><span></span><span></span><span></span></div>';
    const counts = s.issue_counts || {};
    let out = '<div class="dv-cards">' +
      card('Installazione', esc(s.installation), 'Scenario <code>' + esc(s.scenario_id) + '</code>') +
      card('Orizzonte', s.horizon ? esc(dateOnly.format(new Date(s.horizon.start))) + ' → ' + esc(dateOnly.format(new Date(s.horizon.end))) : '—', 'Periodo pianificato') +
      card('Ultima lettura dei file', s.read_at ? esc(dateTime.format(new Date(s.read_at))) : '—', 'Calcolo dati ' + (s.built_at ? esc(dateTime.format(new Date(s.built_at))) : '—')) +
      card('Stato dei dati', s.publishable ? '<span class="dv-ok">Pubblicabile</span>' : s.validation_accepted ? '<span class="dv-ok">Calcolabile con indicazione confermata</span>' : '<span class="dv-bad">Non pubblicabile</span>',
        nf(counts.error) + ' errori · ' + nf(counts.block) + ' blocchi · ' + nf(counts.warn) + ' avvisi') +
      card('Correzioni attive', nf(s.corrections.active), s.corrections.active ? '<a href="#correzioni" data-view="correzioni">Apri il registro →</a>' : 'Dati come nelle fonti') +
      '</div>';
    const workbook = s.source_kind === 'planning_workbook';
    out += '<article class="panel"><div class="panel-title"><div><h2>' + (workbook ? 'Excel di pianificazione' : 'Sorgenti collegate') + '</h2><p class="panel-sub">' + (workbook ? 'Sorgente unica: ' : 'Collegamento definito nel file ') + '<code>' + esc(s.binding_file) +
      '</code></p></div><div class="panel-tools">' + (workbook && S.session.workbook_open_available && S.session.role !== 'reparto' ? '<button class="btn btn-muted btn-small" type="button" data-dv="open-workbook">Apri l’Excel</button>' : '') +
      (canEdit() || (workbook && S.session.role !== 'reparto') ? '<button class="btn btn-primary btn-small" type="button" data-dv="reload-sources">↻ ' + (workbook ? 'Rileggi Excel' : 'Rileggi i file') + '</button>' : '') +
      '</div></div><div class="dv-sources">' + s.sources.map(src => {
        const missing = src.files.filter(f => !f.exists).length;
        const domains = Object.entries(src.domains);
        return '<section class="dv-source"><header><span class="dv-src-icon">' + (src.connector === 'canonical-json' ? '{ }' : '▦') + '</span><div><h3>' + esc(src.name) + '</h3>' +
          '<p>' + (src.connector === 'planning-workbook' ? 'Excel completo' : src.connector === 'canonical-json' ? 'Pacchetto canonico JSON' : 'File CSV / Excel') + (src.extracted_at ? ' · letto ' + ago(src.extracted_at) : '') + '</p></div>' +
          '<span class="dv-badge ' + (missing || src.errors.length ? 'red' : 'green') + '">' + (missing ? missing + ' file mancanti' : src.errors.length ? 'errori di lettura' : 'leggibile') + '</span></header>' +
          '<ul class="dv-files">' + src.files.map(f => '<li class="' + (f.exists ? '' : 'missing') + '"><span class="dv-file-label">' + esc(ENT[f.label] ? plural(f.label) : f.label === 'bundle' ? 'Pacchetto' : f.label) + '</span>' +
            '<code title="' + esc(f.path) + '">' + esc(f.name || f.path || '—') + '</code><span>' + (f.exists ? esc(bytes(f.bytes)) + ' · modificato ' + ago(f.modified) : 'file non trovato') + '</span></li>').join('') + '</ul>' +
          (domains.length ? '<div class="dv-feeds">' + domains.map(([d, ents]) => '<div><strong>' + esc(DOMAIN[d] || d) + '</strong>' +
            Object.entries(ents).map(([e, n]) => '<span>' + esc(plural(e)) + ' <b>' + esc(nf(n)) + '</b></span>').join('') + '</div>').join('') + '</div>' : '') +
          (src.errors.length ? '<div class="dv-errors"><ul>' + src.errors.map(e => '<li>' + esc(e) + '</li>').join('') + '</ul></div>' : '') + '</section>';
      }).join('') + '</div><div class="info-panel dv-howto">' + (workbook ?
      'Modifica e salva l’Excel, poi premi <strong>Rileggi Excel</strong> e ricalcola il piano. Dati, regole e correzioni provengono dal file; l’archivio conserva i risultati.' :
      'Dopo una nuova estrazione premi <strong>Rileggi i file</strong>: i dati si ricaricano e le correzioni restano applicate.') + '</div></article>';
    if(S.factorySource?.available) out += factorySyncHTML(S.factorySource);
    out += '<article class="panel"><div class="panel-title"><div><h2>Domini del modello canonico</h2><p class="panel-sub">Quale sorgente alimenta cosa, con le politiche scelte per l’installazione</p></div></div>' +
      '<div class="table-scroll"><table class="data-table"><thead><tr><th>Dominio</th><th>Sorgenti</th><th>Record</th><th>Politiche</th><th>Impronta</th></tr></thead><tbody>' +
      s.domains.map(d => '<tr><td><strong>' + esc(DOMAIN[d.domain] || d.domain) + '</strong></td><td>' + d.sources.map(x => '<code>' + esc(x) + '</code>').join(' ') + '</td>' +
        '<td>' + esc(nf(d.records)) + '</td><td>' + policies(d) + '</td><td><code title="' + esc(d.digest) + '">' + esc(String(d.digest || '').slice(0, 10)) + '</code></td></tr>').join('') +
      '</tbody></table></div></article>';
    out += '<article class="panel"><div class="panel-title"><div><h2>Diagnostica dei dati</h2><p class="panel-sub">Segnalazioni del controllo di qualità: gli errori impediscono il calcolo, i blocchi la pubblicazione</p></div></div>' +
      (s.issues.length ? '<div class="dv-issues">' + s.issues.map((g, i) => '<details' + (i < 2 ? ' open' : '') + '><summary><span class="dv-badge ' +
        ({error:'red', block:'amber', warn:'neutral'}[g.severity]) + '">' + ({error:'Errore', block:'Blocco', warn:'Avviso'}[g.severity]) + '</span><strong>' + esc(ISSUE[g.code] || g.code) +
        '</strong><span class="dv-issue-domain">' + esc(DOMAIN[g.domain] || g.domain) + '</span><span class="dv-issue-count">' + esc(nf(g.count)) + '</span></summary><ul>' +
        g.examples.map(x => '<li>' + (x.key ? '<code>' + esc(x.key) + '</code> ' : '') + esc(x.detail) + '</li>').join('') + (g.count > g.examples.length ? '<li class="dv-more">… e altre ' + nf(g.count - g.examples.length) + '</li>' : '') +
        '</ul></details>').join('') + '</div>' : empty('Nessuna segnalazione', 'I dati letti superano tutti i controlli di qualità.', '✓')) + '</article>';
    return out;
  }
  function card(title, value, sub) {
    return '<article class="summary-card dv-card"><div><span class="label">' + esc(title) + '</span><strong class="big">' + value + '</strong><p class="sub">' + sub + '</p></div></article>';
  }

  function factorySyncHTML(f) {
    const editing=S.session?.role==='admin', running=f.state==='running'||f.state==='publishing'||f.worker_active;
    const disabled=!editing||running?' disabled':'';
    const input=(name,value,type='text')=>'<input type="'+type+'" name="factory-'+name+'" value="'+esc(value||'')+'"'+disabled+'>';
    const areas=f.view_contract||[], total=areas.length||Object.keys(f.views||{}).length;
    let html='<article class="panel factory-sync"><h2>Factory → Excel → Piano</h2><p>Le '+total+' viste complete popolano l’Excel, nesting compresi. Il piano e tutte le viste del sistema leggono il file Excel verificato.</p>';
    if(f.state==='done') html+='<p class="info-panel">Sincronizzazione completata: '+esc(f.operations)+' fasi, '+esc(f.orders)+' commesse, '+esc(f.unscheduled)+' attività non collocate.</p>';
    if(f.source_coverage?.contract_version===f.contract_version) html+='<p>'+esc(Object.keys(f.source_coverage.view_rows||{}).length)+' viste conservate nell’Excel; '+
      esc(f.source_coverage.nesting?.scenario_nestings)+' nesting su '+esc(f.source_coverage.nesting?.source_programs)+' programmi sorgente. '+
      esc(f.source_coverage.nesting?.review_count)+' programmi con segnalazioni. <a href="/api/factory-source/report" download="copertura-e-riconciliazione-factory.json">Scarica copertura e riconciliazione</a>.</p>';
    else if(f.source_coverage) html+='<p>L’Excel attuale proviene dal collegamento precedente. Il prossimo aggiornamento userà le '+total+' viste complete elencate qui sotto.</p>';
    if(running) html+='<p class="info-panel" role="status">'+esc(f.phase||'Sincronizzazione in corso…')+'</p>';
    if(f.state==='failed') html+='<p class="info-panel" role="alert">Aggiornamento non applicato: '+esc(f.error)+'</p>';
    if(f.state==='cancelled') html+='<p class="info-panel">Sincronizzazione annullata. Excel attivo conservato.</p>';
    if(f.state==='conflicts'){
      html+='<p class="info-panel">'+esc(f.conflict_count)+' conflitti: lo stesso dato è cambiato sia in Excel sia in Factory. Il file attivo resta invariato. Sono mostrati i primi 100. <a href="/api/factory-source/report" download="conflitti-sincronizzazione.json">Scarica il confronto completo</a>.</p>'+
        '<div class="table-scroll"><table class="data-table"><thead><tr><th>Tabella / codice</th><th>Campo</th><th>Excel</th><th>Factory</th></tr></thead><tbody>'+
        (f.conflicts||[]).map(c=>'<tr><td>'+esc(plural(c.table))+' · '+esc(c.key.join(' / '))+'</td><td>'+esc(label(c.table,c.field))+'</td><td>'+esc(c.excel_absent?'eliminato':JSON.stringify(c.excel))+'</td><td>'+esc(c.factory_absent?'eliminato':JSON.stringify(c.factory))+'</td></tr>').join('')+'</tbody></table></div>'+
        (editing?'<div class="panel-tools"><button type="button" class="btn" data-factory="excel">Mantieni i valori Excel in conflitto</button><button type="button" class="btn btn-muted" data-factory="factory">Usa i valori Factory in conflitto</button></div>':'');
    }
    html+='<div class="factory-sync-grid"><label>Server / istanza'+input('server',f.server)+'</label><label>Database'+input('database',f.database)+'</label>'+
      '<label>Utente SQL (vuoto = Windows)'+input('user',f.user)+'</label><label>Password SQL'+input('password','','password')+'</label>'+
      '<label>Inizio orizzonte'+input('start',f.start,'date')+'</label><label>Driver<select name="factory-driver"'+disabled+'>'+[17,18].map(n=>'<option'+(f.driver?.includes(' '+n+' ')?' selected':'')+'>ODBC Driver '+n+' for SQL Server</option>').join('')+'</select></label></div>'+
      '<div class="factory-views-title"><h3>Le '+total+' viste che alimentano tutto l’Excel</h3><p>Ogni area conserva più tipi di record, distinti dalla colonna TipoRecord. Apri il contenuto per consultarne il dettaglio.</p></div>'+
      '<div class="factory-views">'+areas.map(a=>{
        const count=f.source_coverage?.datasets?.[a.key], rows=f.source_coverage?.view_rows?.[a.key];
        return '<section class="factory-view"><header><h4>'+esc(a.label)+'</h4><span>'+esc(a.datasets.length)+(a.datasets.length===1?' tipo di record':' tipi di record')+(rows===undefined?'':' · '+nf(rows)+' righe')+'</span></header>'+
          '<p>'+esc(a.description)+'</p><label>Vista SQL'+input('view:'+a.key,f.views[a.key])+'</label>'+
          '<p class="factory-view-feeds">Dati nell’Excel: '+esc(a.sheets.join(', '))+'.</p>'+
          '<details data-factory-area="'+esc(a.key)+'"'+(S.factoryOpen.has(a.key)?' open':'')+'><summary>Contenuto dell’area</summary><ul>'+a.datasets.map(d=>'<li><strong>'+esc(d.tipo)+'</strong>'+(count?.[d.name]===undefined?'':' · '+nf(count[d.name])+' righe')+'<p>'+esc(d.description)+'</p></li>').join('')+'</ul></details></section>';
      }).join('')+'</div><p class="factory-sql-note">Le tabelle operative dell’Excel alimentano il piano. I dettagli originali e i record fuori perimetro restano in Provenienza record.</p>';
    if(S.factoryTest) html+='<p class="info-panel">'+esc(S.factoryTest.busy?'Verifica SQL e struttura delle viste in corso…':S.factoryTest.ok?'Collegamento riuscito: '+S.factoryTest.checked_views+'/'+total+' viste accessibili e conformi al contratto.':S.factoryTest.error)+'</p>';
    if(S.factoryCreate) html+='<p class="info-panel" role="status">'+esc(S.factoryCreate.busy?'Creazione delle viste mancanti in corso…':factoryCreateText(S.factoryCreate))+'</p>';
    html+='<div class="panel-tools"><button class="btn btn-muted" type="button" data-factory="save"'+disabled+'>Salva collegamento</button>'+
      '<button class="btn btn-muted" type="button" data-factory="test"'+disabled+'>Verifica SQL e viste</button>'+
      '<button class="btn '+(S.factoryTest?.missing_views?.length?'btn-primary':'btn-muted')+'" type="button" data-factory="create"'+disabled+' title="Crea nel database le viste che mancano, dai modelli SQL del progetto. Non modifica quelle esistenti.">Crea viste mancanti</button>'+
      '<button class="btn btn-primary" type="button" data-factory="run"'+disabled+'>Sincronizza Factory → Excel → Piano</button>'+
      (editing&&running?'<button class="btn btn-muted" type="button" data-factory="cancel">Annulla sincronizzazione</button>':'')+'</div>'+
      '<p>Factory viene letta, senza scritture: l’unica eccezione è «Crea viste mancanti», che su richiesta aggiunge al database solo le viste non ancora presenti. La password non viene salvata. Prima di ogni aggiornamento viene conservata una copia dell’Excel; le revisioni dei piani restano nell’archivio.</p></article>';
    return html;
  }
  function factoryCreateText(r){
    const names=list=>list.map(n=>n.replace(/^dbo\./,'')).join(', ');
    if(r.error&&!(r.created||[]).length&&!(r.failed||[]).length) return r.error;
    let text=(r.created||[]).length?'Create '+r.created.length+' viste: '+names(r.created)+'. ':'Nessuna vista da creare. ';
    if((r.present||[]).length) text+=r.present.length+' già presenti, lasciate invariate. ';
    if((r.failed||[]).length) text+='Non create: '+r.failed.map(f=>f.view.replace(/^dbo\./,'')+' ('+f.error+')').join('; ')+'.';
    return text;
  }
  let syncTimer=0;
  async function syncStatus(){
    const previous=S.factorySource;
    S.factorySource=await api('/api/factory-source');
    if(S.factorySource.state==='done' && S.syncedJob!==S.factorySource.job_id){
      S.syncedJob=S.factorySource.job_id;S.rows={};await loadCatalog();S.sources=await api('/api/sources');
      // Initial mounting already loads the current workbook. Reloading the
      // app here would replace the root while Fonti is still being mounted.
      if(previous)document.dispatchEvent(new CustomEvent('pf:source-synced'));
    }
    if(S.view==='fonti')paint();
    clearTimeout(syncTimer);
    if(['running','publishing'].includes(S.factorySource.state)||S.factorySource.worker_active)syncTimer=setTimeout(()=>syncStatus().catch(e=>toast(e.message)),2000);
  }
  function policies(d) {
    const p = d.policies || {}, parts = [];
    if (p.residual_policy) parts.push('Residuo: ' + ({quantity:'a pezzi', time:'a tempi', none:'nessuno'}[p.residual_policy] || p.residual_policy));
    if (p.allocation_order) parts.push('Materiale: ' + ({due:'per consegna', rank:'per posizione'}[p.allocation_order] || p.allocation_order));
    if (p.material_policy === 'ignorati_con_regola') parts.push('Vincolo materiale escluso da indicazione confermata');
    if (p.include_statuses) parts.push('Stati nesting: ' + p.include_statuses.map(x => ENUM.nest_status[x] || x).join(', '));
    if (d.max_age_minutes) parts.push('Età massima ' + d.max_age_minutes + ' min');
    if (Object.keys(d.precedence || {}).length) parts.push('Precedenze su ' + Object.keys(d.precedence).length + ' campi');
    return parts.length ? esc(parts.join(' · ')) : '<span class="dv-empty">—</span>';
  }

  // ------------------------------------------------------------------ ledger page
  function ledgerHTML() {
    const list = S.ledger;
    if (!list) return '<div class="dv-loading"><span></span><span></span><span></span></div>';
    const active = list.filter(h => h.active).length;
    let out = '<article class="panel"><div class="panel-title"><div><h2>Correzioni ai dati in ingresso</h2><p class="panel-sub">' + nf(active) + ' attive · ' +
      nf(list.length - active) + ' annullate. Annullare non cancella nulla: la riga resta nel registro.</p></div></div>';
    if (!list.length) return out + empty('Nessuna correzione', 'I dati in ingresso sono quelli delle fonti, senza modifiche.', '✓') + '</article>';
    out += '<div class="table-scroll"><table class="data-table dv-ledger"><thead><tr><th>#</th><th>Quando</th><th>Chi</th><th>Tabella</th><th>Riga</th><th>Azione</th><th>Cambiamenti</th><th>Motivo</th><th>Stato</th><th></th></tr></thead><tbody>' +
      list.map(h => {
        const spec = S.entities[h.entity];
        const changes = h.action === 'update' ? Object.keys(h.values).map(n => esc(label(h.entity, n)) + ': ' +
          (h.previous && h.previous[n] !== undefined ? esc(plainValue(fieldOf(spec, n), h.previous[n])) + ' → ' : '') + '<strong>' + esc(plainValue(fieldOf(spec, n), h.values[n])) + '</strong>').join('<br>') :
          h.action === 'insert' ? 'nuova riga' : 'esclusa dal calcolo';
        return '<tr class="' + (h.active ? '' : 'dv-revoked-row') + '"><td>' + h.id + '</td><td>' + esc(dateTime.format(new Date(h.created_at))) + '</td><td>' + esc(h.actor) + '</td>' +
          '<td>' + esc(plural(h.entity)) + '</td><td><code>' + esc(h.key.join(' · ')) + '</code></td><td>' + esc(ACTION[h.action]) + '</td><td class="dv-wrap">' + changes + '</td>' +
          '<td class="dv-wrap">' + esc(h.reason) + (h.active ? '' : '<br><small>Annullata da ' + esc(h.revoked_by) + ': ' + esc(h.revoke_reason) + '</small>') + '</td>' +
          '<td><span class="dv-badge ' + (h.active ? 'green' : 'neutral') + '">' + (h.active ? 'attiva' : 'annullata') + '</span></td>' +
          '<td class="dv-row-actions">' + (S.revoking === h.id ?
            '<input class="field-input dv-revoke-input" id="dv-revoke-reason" maxlength="500" placeholder="Motivo dell’annullamento" aria-label="Motivo dell’annullamento">' +
            '<button class="btn btn-danger btn-small" type="button" data-dv-revoke-confirm="' + h.id + '">Conferma</button>' +
            '<button class="btn btn-muted btn-small" type="button" data-dv="revoke-cancel">Indietro</button>' :
            '<button class="btn btn-muted btn-small" type="button" data-dv-open="' + esc(JSON.stringify([h.entity, h.key])) + '">Apri</button>' +
            (h.active && canEdit() ? '<button class="btn btn-muted btn-small" type="button" data-dv-revoke="' + h.id + '">Annulla</button>' : '')) + '</td></tr>';
      }).join('') + '</tbody></table></div></article>';
    return out;
  }
  const fieldOf = (spec, name) => (spec && spec.fields.find(f => f.name === name)) || {name, type:'text'};

  // ------------------------------------------------------------------ AI settings page
  function aiHTML() {
    const a = S.ai;
    if (!a) return '<div class="dv-loading"><span></span><span></span><span></span></div>';
    const d = S.aiDraft;
    const edit = a.can_edit && !a.environment_locked;
    const dis = edit ? '' : ' disabled';
    const keysReady = connectedProviders(a).length > 0;
    let out = '<div class="ai-hero ' + (a.active ? 'on' : 'off') + '"><div class="ai-hero-icon">' + (a.active ? '✧' : '○') + '</div><div><h2>' +
      (a.active ? 'AI collegata' : d.enabled ? 'AI attivata ma non pronta' : 'AI spenta') + '</h2><p>' +
      (a.active ? 'Assistente: ' + esc(a.providers[a.roles.assistant.provider] || '') + ' · <code>' + esc(a.roles.assistant.model) + '</code>. Le risposte sono da verificare: il motore resta indipendente.' :
        d.enabled ? esc(Object.values(a.roles).map(r => r.problem).filter(Boolean)[0] || 'Completa la configurazione.') :
          'Il piano e le analisi deterministiche funzionano senza AI. Per collegarla bastano i due passi qui sotto, senza modificare file.') + '</p></div>' +
      '<label class="ai-switch"><input type="checkbox" id="ai-enabled"' + (d.enabled ? ' checked' : '') + dis + '><span></span> Attiva</label></div>';
    if (a.environment_locked) out += '<div class="info-panel dv-warn">L’AI è configurata con le variabili PLANNING_CLAUDE_* sul server: questa pagina è in sola lettura.</div>';
    if (!a.can_edit) out += '<div class="info-panel">Solo un amministratore può modificare queste impostazioni.</div>';
    out += connectHTML(a, edit);
    if (edit && keysReady && !a.active) out += activateHTML(a);
    out += '<details class="ai-advanced" id="ai-advanced"' + (a.active || S.aiAdvanced ? ' open' : '') + '><summary>Impostazioni avanzate <span>limiti, modello per ruolo, catalogo dei modelli</span></summary>';
    out += '<article class="panel"><div class="panel-title"><div><h2>Limiti di spesa</h2><p class="panel-sub">Tetti giornalieri di chiamate al modello, obbligatori per attivare l’AI</p></div></div>' +
      '<div class="field-grid ai-quota"><div class="field"><label for="ai-quota-user">Chiamate al giorno per utente</label><input class="field-input" type="number" min="1" max="10000" id="ai-quota-user" value="' +
      esc(d.quota_per_user || '') + '" placeholder="es. 50"' + dis + '><small>Una domanda all’assistente usa da 1 a 3 chiamate.</small></div>' +
      '<div class="field"><label for="ai-quota-total">Chiamate al giorno in totale</label><input class="field-input" type="number" min="1" max="10000" id="ai-quota-total" value="' +
      esc(d.quota_total || '') + '" placeholder="es. 200"' + dis + '><small>' + (a.used_today ? 'Usate oggi: ' + nf(a.used_today.total) + (a.used_today.user !== null ? ' (tu: ' + nf(a.used_today.user) + ')' : '') : 'Nessun limite impostato') + '</small></div>' +
      '<div class="field"><label for="ai-version">Versione API Anthropic</label><input class="field-input" id="ai-version" value="' + esc(d.api_version || a.default_api_version) + '"' + dis + '>' +
      '<small>Predefinita ' + esc(a.default_api_version) + '. Cambiala solo se il fornitore lo richiede.</small></div></div></article>';
    out += '<article class="panel"><div class="panel-title"><div><h2>Modello per ruolo</h2><p class="panel-sub">Ogni passaggio può usare un fornitore e un modello diversi; vuoto = come l’assistente</p></div>' +
      '<div class="panel-tools"><label class="ai-inline">Fornitore predefinito <select class="field-select" id="ai-provider"' + dis + '><option value="">—</option>' +
      Object.entries(a.providers).map(([k, l]) => '<option value="' + k + '"' + (d.provider === k ? ' selected' : '') + '>' + esc(l) + '</option>').join('') + '</select></label></div></div>' +
      '<div class="ai-roles">' + Object.entries(a.roles).map(([role, info]) => {
        const r = d.roles[role] || {};
        const test = S.tests[role];
        return '<section class="ai-role"><div class="ai-role-head"><div><h3>' + esc(info.label) + '</h3><p>' + esc(info.description) + '</p></div>' +
          '<span class="dv-badge ' + (info.ready ? 'green' : 'neutral') + '">' + esc(info.ready ? 'pronto' : info.problem) + '</span></div>' +
          '<div class="ai-role-fields"><label>Fornitore <select class="field-select" data-ai-role="' + role + '" data-ai-field="provider"' + dis + '><option value="">' +
          (role === 'assistant' ? 'predefinito' : 'come l’assistente') + '</option>' + Object.entries(a.providers).map(([k, l]) => '<option value="' + k + '"' + (r.provider === k ? ' selected' : '') + '>' + esc(l) + '</option>').join('') +
          '</select></label><label class="ai-model">Modello <input class="field-input mono" list="ai-models-list" data-ai-role="' + role + '" data-ai-field="model" value="' + esc(r.model || '') + '" placeholder="' +
          (role === 'assistant' ? 'scegli dal catalogo qui sotto' : 'come l’assistente' + (info.model ? ': ' + esc(info.model) : '')) + '"' + dis + '></label>' +
          (a.can_edit ? '<button class="btn btn-muted btn-small" type="button" data-ai-test="' + role + '"' + (test === 'busy' ? ' disabled' : '') + '>' + (test === 'busy' ? 'Prova in corso…' : 'Prova collegamento') + '</button>' : '') + '</div>' +
          (test && test !== 'busy' ? '<p class="ai-test ' + (test.ok ? 'ok' : 'bad') + '">' + (test.ok ? '✓ Risponde in ' + nf(test.latency_ms) + ' ms con strumenti abilitati' + (test.reply ? ' · “' + esc(test.reply) + '”' : '') : '✕ ' + esc(test.error)) + '</p>' : '') +
          '</section>';
      }).join('') + '</div>' +
      (edit ? '<div class="parameter-footer"><span class="panel-sub">' + (a.updated_by ? 'Ultima modifica: ' + esc(a.updated_by) + ' · ' + esc(dateTime.format(new Date(a.updated_at))) : 'Mai salvate') +
        '</span><button class="btn btn-primary" type="button" data-ai="save">Salva impostazioni</button></div>' : '') + '</article>';
    out += catalogHTML() + '</details>';
    out += '<div class="info-panel">Con l’AI attiva, la domanda e i dati del piano richiesti dagli strumenti vengono inviati al fornitore scelto. Il modello legge in sola lettura: ' +
      'regole e simulazioni proposte restano bozze da confermare, e nulla viene pubblicato su Factory.' + (keysReady ? '' : ' Nessun fornitore collegato: l’AI non può partire.') + '</div>';
    return out;
  }
  const PROVIDER_CARD = {
    anthropic:{title:'Claude · chiave Anthropic', sub:'Diretto da Anthropic, a consumo sul credito della console Claude.', page:'la console Claude',
      how:'Accedi, premi <b>Create Key</b>, dagli un nome (per esempio “Planning CLIENTE”) e copia la chiave.', placeholder:'sk-ant-…'},
    openrouter:{title:'OpenRouter · Claude e altri modelli', sub:'Un solo conto prepagato per Claude e gli altri fornitori; accesso con un clic.', page:'le chiavi OpenRouter',
      how:'Premi <b>Create Key</b> e copia la chiave.', placeholder:'sk-or-…'},
  };
  function connectHTML(a, edit) {
    const link = (href, text, cls) => '<a class="' + (cls || 'ai-link') + '" href="' + esc(href) + '" target="_blank" rel="noopener noreferrer">' + text + ' ↗</a>';
    const card = (p) => {
      const k = a.keys[p], c = PROVIDER_CARD[p], env = k.origin === 'ambiente', gateway = p === 'openrouter' && k.gateway;
      const open = edit && (!k.present || S.replacing === p), busy = S.connecting === p;
      let body;
      if (!open) {
        body = k.present ? '<p class="ai-linked">✓ Chiave <code>' + esc(k.hint || '') + '</code>' + (env ? ' dalle variabili d’ambiente del server' : ' salvata sul server') +
            (p === 'anthropic' && k.workspace ? ' · workspace impostato' : '') + (gateway ? ' · tramite gateway <code>' + esc(k.gateway) + '</code>' : '') + '</p>' +
            '<div class="ai-card-actions">' + (edit && !env ? '<button class="btn btn-muted btn-small" type="button" data-ai="replace" data-provider="' + p + '">Sostituisci chiave</button>' +
              '<button class="btn btn-muted btn-small" type="button" data-ai="disconnect" data-provider="' + p + '">Scollega</button>' : '') + (gateway ? '' : link(k.credit_page, 'Credito e consumi')) + '</div>'
          : '<p class="ai-linked muted">Non collegato.</p>';
      } else {
        body = (p === 'openrouter' && !gateway ? '<button class="btn btn-primary ai-oauth" type="button" data-ai="oauth-openrouter"' + (busy ? ' disabled' : '') + '>' +
            (busy ? 'Apertura di OpenRouter…' : 'Accedi con OpenRouter') + '</button><p class="ai-or">Si apre OpenRouter: accedi (anche con Google), premi <b>Authorize</b> e torni qui già collegato. ' +
            'Oppure crea una chiave e incollala:</p>' : '') +
          '<ol class="ai-steps">' + (gateway ? '' : '<li>' + link(k.keys_page, 'Apri ' + c.page, 'btn btn-muted btn-small') + '<span>' + c.how + '</span></li>') +
          '<li><input class="field-input mono" type="password" autocomplete="off" spellcheck="false" id="ai-key-' + p + '" placeholder="Incolla qui la chiave (' + (gateway ? 'chiave del gateway' : c.placeholder) + ')" aria-label="Chiave ' + esc(k.label) + '"></li>' +
          (p === 'anthropic' ? '<li><details class="ai-ws"><summary>La chiave appartiene a un workspace con identità?</summary><input class="field-input mono" id="ai-ws-anthropic" autocomplete="off" spellcheck="false" placeholder="wrkspc_… (facoltativo)" aria-label="Id del workspace Anthropic">' +
            '<small>Serve solo se la console lo chiede: Settings › Workspaces.</small></details></li>' : '') +
          '<li class="ai-step-go"><button class="btn btn-primary btn-small" type="button" data-ai="connect" data-provider="' + p + '"' + (busy ? ' disabled' : '') + '>' + (busy ? 'Verifica in corso…' : 'Verifica e collega') + '</button>' +
            (S.replacing === p ? '<button class="btn btn-muted btn-small" type="button" data-ai="replace-cancel">Annulla</button>' : '') +
            '<span>La chiave viene provata con il fornitore e salvata solo sul server; questa pagina non la rilegge.</span></li></ol>';
      }
      return '<section class="ai-card' + (k.present ? ' ok' : '') + '"><div class="ai-card-head"><span class="ai-key-led"></span><div><h3>' + esc(c.title) + '</h3><p>' + esc(c.sub) + '</p></div>' +
        '<span class="dv-badge ' + (k.present ? 'green' : 'neutral') + '">' + (k.present ? 'collegato' : 'da collegare') + '</span></div>' + body + '</section>';
    };
    const recent = a.last_connect && Date.now() - Date.parse(a.last_connect.at) < 600000;
    const last = recent && !a.last_connect.ok ?'<div class="info-panel dv-warn">' + esc(a.last_connect.message) + '</div>' : '';
    return '<article class="panel ai-connect"><div class="panel-title"><div><h2>1 · Collega un fornitore</h2><p class="panel-sub">Ne basta uno. Nessun file da modificare: ' +
      (a.subscription ? 'l’accesso e le chiavi li gestisce il server.' : 'la chiave la verifica e la salva il server.') + '</p></div></div>' + last +
      (a.subscription ? '<div class="ai-cards ai-cards-sub">' + subscriptionCard(a, edit) + '</div>' : '') +
      '<div class="ai-cards">' + ['anthropic', 'openrouter'].map(card).join('') + '</div>' +
      (a.subscription ? '</article>' : '<details class="ai-why"><summary>Perché qui non si usa l’abbonamento Claude (Pro o Max)?</summary><p>Tecnicamente si potrebbe: con Claude Code o un gateway locale le richieste passano sull’abbonamento. ' +
      'Però i termini di Anthropic ammettono l’abbonamento solo per uso personale, nelle sue app e in Claude Code. Un programma usato da altre persone, come questo pianificatore, deve usare una chiave API. ' +
      'Con l’abbonamento l’account rischierebbe il blocco, le domande di tutto il reparto consumerebbero i limiti di una sola persona e i dati dell’azienda passerebbero da un account privato. ' +
      'La chiave API invece si paga a consumo sul conto dell’azienda, e i limiti giornalieri del passo 2 tengono la spesa sotto controllo.</p></details></article>');
  }
  const connectedProviders = (a) => (a.subscription && a.subscription.logged_in ? ['abbonamento'] : [])
    .concat(Object.keys(a.keys).filter(p => a.keys[p].present));
  function subscriptionCard(a, edit) {
    const s = a.subscription, busy = S.connecting === 'abbonamento';
    let body;
    if (!s.available) body = '<p class="ai-linked muted">' + esc(s.problem) + '</p><div class="ai-card-actions"><a class="ai-link" href="https://claude.com/download" target="_blank" rel="noopener noreferrer">Scarica Claude Code ↗</a></div>';
    else if (s.logged_in) body = '<p class="ai-linked">✓ Collegato al tuo account Claude' + (s.subscription ? ' (piano ' + esc(s.subscription) + ')' : '') + '</p>' +
      (edit ? '<div class="ai-card-actions"><button class="btn btn-muted btn-small" type="button" data-ai="sub-logout">Scollega</button></div>' : '');
    else if (s.login_running && edit) body = '<ol class="ai-steps"><li>Si è aperta una scheda di claude.ai: accedi con il tuo account e premi <b>Autorizza</b>.' +
        (s.login_url ? ' Se non si è aperta: <a class="ai-link" href="' + esc(s.login_url) + '" target="_blank" rel="noopener noreferrer">apri la pagina di accesso ↗</a>' : '') + '</li>' +
      '<li>Se claude.ai ti mostra un codice, copialo qui. Altrimenti questa pagina si aggiorna da sola.<input class="field-input mono" id="ai-sub-code" autocomplete="off" spellcheck="false" placeholder="Incolla il codice di claude.ai" aria-label="Codice di accesso claude.ai"></li>' +
      '<li class="ai-step-go"><button class="btn btn-primary btn-small" type="button" data-ai="sub-code"' + (busy ? ' disabled' : '') + '>' + (busy ? 'Verifica…' : 'Conferma il codice') + '</button>' +
      '<button class="btn btn-muted btn-small" type="button" data-ai="sub-login">Ricomincia</button></li></ol>';
    else body = (s.login_error ? '<p class="ai-test bad">✕ ' + esc(s.login_error) + '</p>' : '') +
      (edit ? '<button class="btn btn-primary ai-oauth" type="button" data-ai="sub-login"' + (busy ? ' disabled' : '') + '>' + (busy ? 'Apertura di claude.ai…' : 'Accedi con il tuo abbonamento') + '</button>' +
        '<p class="ai-or">Si apre claude.ai: accedi come fai di solito e autorizza Claude Code. Il collegamento resta in questa istanza, separato dal tuo Claude Code.</p>' : '<p class="ai-linked muted">Non collegato.</p>');
    return '<section class="ai-card ai-card-sub' + (s.logged_in ? ' ok' : '') + '"><div class="ai-card-head"><span class="ai-key-led"></span><div><h3>Abbonamento Claude · Pro o Max</h3>' +
      '<p>Il tuo abbonamento personale, tramite Claude Code su questo PC. Senza chiavi e senza costi a consumo, entro i limiti del tuo piano. ' +
      'Vale solo per te con l’accesso locale: un’installazione usata da altre persone deve usare una chiave API.</p></div>' +
      '<span class="dv-badge ' + (s.logged_in ? 'green' : 'neutral') + '">' + (s.logged_in ? 'collegato' : 'da collegare') + '</span></div>' + body + '</section>';
  }
  let subPoll = 0;
  function watchSubscription() {
    // while the claude.ai tab is open, notice when the login completes there
    clearTimeout(subPoll);
    if (S.view !== 'impostazioni' || !S.ai || !S.ai.subscription || !S.ai.subscription.login_running) return;
    subPoll = setTimeout(async () => {
      if (S.view !== 'impostazioni') return;
      try {
        const fresh = await api('/api/settings/ai');
        const was = S.ai.subscription.logged_in;
        readAIDraft(); const draft = S.aiDraft; S.ai = fresh; S.aiDraft = draft;
        if (fresh.subscription && fresh.subscription.logged_in && !was) subscriptionConnected();
        else if (!document.activeElement || document.activeElement.id !== 'ai-sub-code') paint();
      } catch (_) { /* next round */ }
      watchSubscription();
    }, 3000);
  }
  async function subscriptionConnected() {
    S.modelFilter.provider = 'abbonamento'; S.setup = {provider:'abbonamento'}; S.models.abbonamento = null; S.tests.setup = null;
    toast('Abbonamento Claude collegato.', 6000);
    paint(); await loadCatalogModels('abbonamento', true); paint();
    const next = document.getElementById(S.ai.active ? 'ai-advanced' : 'ai-activate'); if (next) next.scrollIntoView({block:'start', behavior:'smooth'});
  }
  function activateHTML(a) {
    const ready = connectedProviders(a);
    const st = S.setup || {};
    const provider = ready.includes(st.provider) ? st.provider : ready.includes(S.modelFilter.provider) ? S.modelFilter.provider : ready[0];
    const cat = S.models[provider];
    const model = (st.provider === provider && st.model) || (cat && cat.recommended) || '';
    const info = cat && cat.models ? cat.models.find(m => m.id === model) : null;
    const test = S.tests.setup;
    const num = (v, fallback) => v !== undefined && v !== '' ? v : fallback;
    return '<article class="panel ai-activate" id="ai-activate"><div class="panel-title"><div><h2>2 · Attiva l’assistente</h2><p class="panel-sub">Controlla la proposta e premi “Attiva e prova”: prima si prova il modello, poi si salva.</p></div></div>' +
      '<div class="field-grid ai-activate-grid"><div class="field"><label for="ai-setup-provider">Fornitore</label><select class="field-select" id="ai-setup-provider">' +
      ready.map(p => '<option value="' + p + '"' + (p === provider ? ' selected' : '') + '>' + esc(a.providers[p]) + '</option>').join('') + '</select></div>' +
      '<div class="field ai-model"><label for="ai-setup-model">Modello dell’assistente</label><input class="field-input mono" id="ai-setup-model" list="ai-models-list" value="' + esc(model) + '" placeholder="' +
      (cat ? 'scegli dal catalogo' : 'lettura del catalogo…') + '"><small>' + (cat && cat.error ? esc(cat.error) : model && cat && model === cat.recommended ?
        'Proposta: ' + esc(info ? info.name : model) + ', buon equilibrio fra qualità e ' + (provider === 'abbonamento' ? 'velocità' : 'costo') + '. Puoi sceglierne un altro.' : 'Gli altri modelli sono nel catalogo delle impostazioni avanzate.') + '</small></div>' +
      '<div class="field"><label for="ai-setup-user">Chiamate al giorno per utente</label><input class="field-input" type="number" min="1" max="10000" id="ai-setup-user" value="' + esc(num(st.user, S.aiDraft.quota_per_user || 50)) + '"></div>' +
      '<div class="field"><label for="ai-setup-total">Chiamate al giorno in totale</label><input class="field-input" type="number" min="1" max="10000" id="ai-setup-total" value="' + esc(num(st.total, S.aiDraft.quota_total || 200)) + '">' +
      '<small>Una domanda all’assistente usa da 1 a 3 chiamate.</small></div></div>' +
      (test && test !== 'busy' && !test.ok ? '<p class="ai-test bad ai-activate-msg">✕ ' + esc(test.error) + (/credito/i.test(test.error) && a.keys[provider] ?' <a class="ai-link" href="' + esc(a.keys[provider].credit_page) + '" target="_blank" rel="noopener noreferrer">Ricarica il credito ↗</a>' : '') + '</p>' : '') +
      '<div class="parameter-footer"><span class="panel-sub">Le impostazioni si possono cambiare in ogni momento qui sotto.</span><button class="btn btn-primary" type="button" data-ai="activate"' + (test === 'busy' || !cat ? ' disabled' : '') + '>' +
      (test === 'busy' ? 'Prova del modello…' : 'Attiva e prova') + '</button></div></article>';
  }
  function catalogHTML() {
    const f = S.modelFilter, cat = S.models[f.provider];
    let models = cat && cat.models || [];
    if (f.q) { const q = f.q.toLowerCase(); models = models.filter(m => (m.id + ' ' + m.name).toLowerCase().includes(q)); }
    if (f.tools) models = models.filter(m => m.tools !== false);
    if (f.images) models = models.filter(m => m.images);
    if (f.free) models = models.filter(m => m.free);
    if (f.order === 'prezzo') models = models.slice().sort((a, b) => (a.price_in ?? 1e9) - (b.price_in ?? 1e9));
    if (f.order === 'contesto') models = models.slice().sort((a, b) => (b.context || 0) - (a.context || 0));
    const stamp = (m) => typeof m.created === 'number' ? m.created * 1000 : Date.parse(m.created || '') || 0;
    if (f.order === 'recenti') models = models.slice().sort((a, b) => stamp(b) - stamp(a));
    const shown = models.slice(0, 80);
    let out = '<article class="panel"><div class="panel-title"><div><h2>Catalogo modelli</h2><p class="panel-sub">Letto dal fornitore; il pianificatore richiede modelli con strumenti</p></div>' +
      '<div class="panel-tools ai-filters"><select class="field-select" id="ai-cat-provider">' +
      Object.entries(S.ai.providers).map(([k, l]) => '<option value="' + k + '"' + (f.provider === k ? ' selected' : '') + '>' + esc(l) + '</option>').join('') + '</select>' +
      '<input class="field-input" id="ai-cat-q" type="search" placeholder="Cerca modello…" value="' + esc(f.q) + '">' +
      '<label><input type="checkbox" id="ai-cat-tools"' + (f.tools ? ' checked' : '') + '> strumenti</label>' +
      '<label><input type="checkbox" id="ai-cat-images"' + (f.images ? ' checked' : '') + '> immagini</label>' +
      '<label><input type="checkbox" id="ai-cat-free"' + (f.free ? ' checked' : '') + '> gratuiti</label>' +
      '<select class="field-select" id="ai-cat-order">' + [['nome', 'per nome'], ['prezzo', 'per prezzo'], ['contesto', 'per contesto'], ['recenti', 'più recenti']].map(([v, l]) =>
        '<option value="' + v + '"' + (f.order === v ? ' selected' : '') + '>' + l + '</option>').join('') + '</select></div></div>';
    if (!S.ai.can_edit) return out + empty('Catalogo riservato', 'Il catalogo si consulta con il ruolo amministratore.', '✧') + '</article>';
    if (!cat) return out + '<div class="dv-loading"><span></span><span></span><span></span></div></article>';
    if (cat.error) return out + empty('Catalogo non disponibile', cat.error, '⚠') + '</article>';
    out += '<datalist id="ai-models-list">' + (cat.models || []).filter(m => m.tools !== false).slice(0, 400).map(m => '<option value="' + esc(m.id) + '">' + esc(m.name) + '</option>').join('') + '</datalist>';
    if (!shown.length) return out + empty('Nessun modello', 'Nessun modello corrisponde ai filtri.', '⌕') + '</article>';
    const price = (v) => v === null || v === undefined ? '—' : v === 0 ? 'gratis' : '$' + v.toLocaleString('it-IT', {maximumFractionDigits:2});
    out += '<div class="table-scroll ai-catalog"><table class="data-table"><thead><tr><th>Modello</th><th>Capacità</th><th>Contesto</th><th title="Dollari per milione di token, ingresso / uscita">$ / M token</th><th>Usa per</th></tr></thead><tbody>' +
      shown.map(m => '<tr><td><strong>' + esc(m.name) + '</strong><br><code>' + esc(m.id) + '</code></td><td>' +
        (m.tools ? '<span class="dv-badge green">strumenti</span> ' : m.tools === null ? '<span class="dv-badge amber" title="Il gateway non dichiara le capacità: usa Prova collegamento">strumenti da verificare</span> ' :
          '<span class="dv-badge red">senza strumenti</span> ') +
        (m.images ? '<span class="dv-badge neutral">immagini</span>' : '') + '</td><td>' + (m.context ? esc(nf(Math.round(m.context / 1000))) + 'k' : '—') + '</td>' +
        '<td>' + esc(price(m.price_in)) + ' / ' + esc(price(m.price_out)) + '</td><td class="dv-row-actions">' +
        (m.tools !== false ? Object.entries(S.ai.roles).map(([role, info]) => '<button class="btn btn-muted btn-small" type="button" data-ai-use="' + esc(JSON.stringify([role, f.provider, m.id])) + '">' +
          esc({assistant:'Assistente', rules:'Regole', simulation:'Simulazioni'}[role]) + '</button>').join('') : '<span class="panel-sub">non adatto</span>') + '</td></tr>').join('') +
      '</tbody></table></div>' + (models.length > shown.length ? '<p class="dv-note">Mostrati 80 di ' + nf(models.length) + ': restringi la ricerca.</p>' : '') + '</article>';
    return out;
  }
  async function loadAI() {
    S.ai = await api('/api/settings/ai');
    S.aiDraft = JSON.parse(JSON.stringify(S.ai.settings));
    if (!S.modelFilter.provider) {
      // browse the catalog of the provider whose key is present (OpenRouter's catalog is public)
      const k = S.ai.keys;
      S.modelFilter.provider = S.aiDraft.roles.assistant.provider || S.aiDraft.provider ||
        (S.ai.subscription && S.ai.subscription.logged_in ? 'abbonamento' : k.anthropic.present && !k.openrouter.present ? 'anthropic' : 'openrouter');
    }
  }
  async function loadCatalogModels(provider, force) {
    if (!S.ai || !S.ai.can_edit) return;
    if (S.models[provider] && !force) return;
    S.models[provider] = null;
    try { S.models[provider] = await api('/api/settings/ai/models?provider=' + provider); }
    catch (error) { S.models[provider] = {error:error.message, models:[]}; }
  }
  function readAIDraft() {
    const d = S.aiDraft;
    const value = (id) => { const el = document.getElementById(id); return el ? el.value : ''; };
    const enabled = document.getElementById('ai-enabled');
    if (enabled) d.enabled = enabled.checked;
    const num = (id) => { const v = value(id).trim(); return v === '' ? null : Number(v); };
    d.quota_per_user = num('ai-quota-user'); d.quota_total = num('ai-quota-total');
    d.api_version = value('ai-version').trim() || null;
    d.provider = value('ai-provider') || null;
    document.querySelectorAll('[data-ai-role]').forEach(el => {
      const role = el.dataset.aiRole, field = el.dataset.aiField;
      d.roles[role] = d.roles[role] || {provider:null, model:null};
      d.roles[role][field] = el.value.trim() || null;
    });
    // the guided step keeps what the user typed across repaints
    if (document.getElementById('ai-setup-provider')) {
      S.setup = {provider:value('ai-setup-provider'), model:value('ai-setup-model').trim(), user:value('ai-setup-user'), total:value('ai-setup-total')};
    }
  }
  function afterOAuth() {
    // back from openrouter.ai: the server already stored the key; show the outcome once
    const params = new URLSearchParams(location.search);
    const outcome = params.get('ai');
    if (!outcome) return;
    history.replaceState(null, '', location.pathname + location.hash);
    const last = S.ai && S.ai.last_connect;
    if (outcome === 'ok') { S.modelFilter.provider = 'openrouter'; S.setup = {provider:'openrouter'}; toast((last && last.message) || 'OpenRouter collegato.', 7000); }
    else toast((last && last.message) || 'Accesso OpenRouter non completato.', 10000);
  }
  async function connectProvider(p) {
    const input = document.getElementById('ai-key-' + p), ws = document.getElementById('ai-ws-anthropic');
    const key = input ? input.value.trim() : '', workspace = p === 'anthropic' && ws ? ws.value.trim() : '';
    if (!key) { toast('Incolla prima la chiave copiata dalla pagina del fornitore.'); if (input) input.focus(); return; }
    readAIDraft(); S.connecting = p; paint();
    try {
      const body = {provider:p, key}; if (workspace) body.workspace = workspace;
      const status = await api('/api/settings/ai/connect', body);
      S.ai = status; S.aiDraft = JSON.parse(JSON.stringify(status.settings)); S.replacing = null; S.connecting = null;
      S.modelFilter.provider = p; S.setup = {provider:p}; S.models[p] = null; S.tests.setup = null;
      toast(status.last_connect ? status.last_connect.message : 'Fornitore collegato.', 6000);
      document.dispatchEvent(new CustomEvent('pf:ai-changed', {detail:{active:status.active}}));
      paint(); await loadCatalogModels(p, true); paint();
      const next = document.getElementById(status.active ? 'ai-advanced' : 'ai-activate'); if (next) next.scrollIntoView({block:'start', behavior:'smooth'});
    } catch (error) {
      S.connecting = null; paint(); toast((error.errors || [error.message]).join(' · '), 10000);
      // keep what was pasted so a typo can be fixed without copying the key again
      const again = document.getElementById('ai-key-' + p); if (again) { again.value = key; again.focus(); }
      const againWs = document.getElementById('ai-ws-anthropic'); if (againWs && workspace) { againWs.value = workspace; againWs.closest('details').open = true; }
    }
  }
  async function submitSubscriptionCode() {
    const input = document.getElementById('ai-sub-code'), code = input ? input.value.trim() : '';
    if (!code) { toast('Incolla il codice che claude.ai mostra dopo l’autorizzazione.'); if (input) input.focus(); return; }
    readAIDraft(); S.connecting = 'abbonamento'; paint();
    try {
      const status = await api('/api/settings/ai/subscription/code', {code});
      S.ai = status; S.aiDraft = JSON.parse(JSON.stringify(status.settings)); S.connecting = null;
      document.dispatchEvent(new CustomEvent('pf:ai-changed', {detail:{active:status.active}}));
      await subscriptionConnected();
    } catch (error) {
      S.connecting = null; paint(); toast((error.errors || [error.message]).join(' · '), 10000);
      try { S.ai = Object.assign(S.ai, await api('/api/settings/ai')); paint(); watchSubscription(); } catch (_) {}
    }
  }
  async function activateAssistant() {
    readAIDraft();
    const st = S.setup || {};
    const quotaUser = Number(st.user), quotaTotal = Number(st.total);
    if (!st.provider || !st.model) { S.tests.setup = {ok:false, error:'Scegli il modello dell’assistente.'}; paint(); return; }
    if (!Number.isInteger(quotaUser) || !Number.isInteger(quotaTotal) || quotaUser < 1 || quotaTotal < quotaUser) {
      S.tests.setup = {ok:false, error:'I tetti di chiamate sono interi da 1 a 10.000 e quello per utente non supera il totale.'}; paint(); return;
    }
    S.tests.setup = 'busy'; paint();
    try { await api('/api/settings/ai/test', {provider:st.provider, model:st.model}); }
    catch (error) { S.tests.setup = {ok:false, error:error.message}; paint(); return; }
    const d = JSON.parse(JSON.stringify(S.aiDraft));
    d.enabled = true; d.provider = st.provider; d.quota_per_user = quotaUser; d.quota_total = quotaTotal;
    d.roles.assistant = {provider:st.provider, model:st.model};
    try {
      S.ai = await api('/api/settings/ai', d); S.aiDraft = JSON.parse(JSON.stringify(S.ai.settings)); S.tests.setup = null; S.setup = null;
      toast(S.ai.active ? 'AI collegata: l’assistente è pronto. Regole e simulazioni usano lo stesso modello finché non ne scegli un altro.' : 'Impostazioni salvate.', 8000);
      document.dispatchEvent(new CustomEvent('pf:ai-changed', {detail:{active:S.ai.active}}));
    } catch (error) { S.tests.setup = {ok:false, error:(error.errors || [error.message]).join(' · ')}; }
    paint();
  }

  // ------------------------------------------------------------------ mounting
  function paint() {
    if (!S.root || !S.view) return;
    const g = groupOf(S.view);
    if (g) S.root.innerHTML = dataIntro(g) + gridHTML(g);
    else if (S.view === 'fonti') {
      const focusedArea=document.activeElement?.matches('summary')?document.activeElement.parentElement.dataset.factoryArea:null;
      // Polling replaces the panel. Capture the live DOM before rendering so
      // even a click just before an update keeps the user's expanded areas.
      S.root.querySelectorAll('details[data-factory-area]').forEach(el => {
        if(el.open)S.factoryOpen.add(el.dataset.factoryArea);
        else S.factoryOpen.delete(el.dataset.factoryArea);
      });
      S.root.innerHTML = sourcesHTML();
      if(focusedArea)S.root.querySelector('details[data-factory-area="'+CSS.escape(focusedArea)+'"] summary')?.focus({preventScroll:true});
    }
    else if (S.view === 'correzioni') S.root.innerHTML = ledgerHTML();
    else if (S.view === 'impostazioni') S.root.innerHTML = aiHTML();
    document.querySelectorAll('#data-nav .nav-item').forEach(el => el.classList.toggle('active', el.dataset.view === S.view));
  }
  async function mount(view, root, ctx) {
    const changed = S.view !== view;
    S.view = view; S.root = root; S.ctx = ctx;
    if (changed) { S.selected = null; }
    document.body.classList.toggle('dv-mode', true);
    document.body.classList.toggle('dv-admin', !groupOf(view));
    if (!S.session) await ready;
    if (!S.session) { root.innerHTML = '<article class="panel">' + empty('Accesso richiesto', 'Le maschere dati sono disponibili nella Control Room con accesso (anche locale senza token).', '⚿') + '</article>'; return; }
    const g = groupOf(view);
    if (g || view === 'fonti' || view === 'correzioni') {
      if (!S.session.data_screens) { root.innerHTML = '<article class="panel">' + empty('Maschere dati non disponibili', 'La fonte caricata è uno Scenario JSON, non un binding canonico: non ci sono dati in ingresso da mostrare.', '▤') + '</article>'; return; }
      if (!S.catalog) { root.innerHTML = '<div class="dv-loading"><span></span><span></span><span></span></div>'; try { await loadCatalog(); } catch (error) { root.innerHTML = '<article class="panel">' + empty('Dati non disponibili', error.message, '⚠') + '</article>'; return; } }
    }
    if (S.view !== view) return;
    if (changed && g) inspectorIdle(g);
    else if (changed) { $('#inspector-title').textContent = titles[view][0]; $('#inspector-body').innerHTML = '<div class="inspector-empty"><span class="empty-illustration">' + (view === 'impostazioni' ? '✧' : view === 'fonti' ? '⇣' : '✎') + '</span><strong>' + esc(titles[view][0]) + '</strong><p>' + esc(titles[view][1]) + '</p></div>'; }
    paint();
    try {
      if (g) { const entity = currentEntity(g); if (entity && (changed || !S.rows[entity])) { await loadRows(entity); } }
      else if (view === 'fonti') { S.sources = await api('/api/sources'); await syncStatus(); }
      else if (view === 'correzioni') S.ledger = (await api('/api/data/history')).history;
      else if (view === 'impostazioni') { await loadAI(); afterOAuth(); paint(); watchSubscription(); await loadCatalogModels(S.modelFilter.provider); }
    } catch (error) { toast(error.message, 8000); }
    if (S.view === view) paint();
  }
  function unmount() { document.body.classList.remove('dv-mode', 'dv-admin', 'dv-editing'); S.view = null; S.root = null; }

  // ------------------------------------------------------------------ events
  let searchTimer = 0;
  document.addEventListener('click', async (e) => {
    if (!S.view) return;
    const t = e.target.closest('[data-factory],[data-dv],[data-dv-tab],[data-dv-status],[data-dv-sort],[data-dv-row],[data-dv-fold],[data-dv-open],[data-dv-revoke],[data-dv-revoke-confirm],[data-ai],[data-ai-test],[data-ai-use]');
    if (!t) return;
    if(t.dataset.factory){
      const panel=t.closest('.factory-sync'),value=n=>panel.querySelector('[name="factory-'+n+'"]')?.value||'';
      const action=t.dataset.factory, password=value('password'),start=value('start');
      const config={server:value('server').trim(),database:value('database').trim(),user:value('user').trim(),driver:value('driver'),
        views:Object.fromEntries(Object.keys(S.factorySource.views||{}).map(k=>[k,value('view:'+k).trim()]))};
      if(action==='create'&&!confirm('Verranno create nel database «'+config.database+'» sul server «'+config.server+'» le viste Factory che mancano (solo CREATE VIEW, nessun dato modificato, viste già presenti non toccate). Procedere?')) return;
      panel.querySelector('[name="factory-password"]').value='';t.disabled=true;
      try{
        if(action==='test'){
          S.factorySource={...S.factorySource,...config};
          S.factoryCreate=null;S.factoryTest={busy:true};paint();S.factoryTest=await api('/api/factory-source/test',{...config,password});
        }else if(action==='create'){
          S.factorySource={...S.factorySource,...config};
          S.factoryCreate={busy:true};paint();S.factoryCreate=await api('/api/factory-source/create-views',{...config,password});
          S.factoryTest=S.factoryCreate.check||null;
        }else if(action==='cancel') S.factorySource=await api('/api/factory-source/cancel',{});
        else if(action==='excel'||action==='factory') S.factorySource=await api('/api/factory-source/resolve',{job_id:S.factorySource.job_id,decision:action});
        else{
          S.factorySource=await api('/api/factory-source/config',config);
          if(action==='run') S.factorySource=await api('/api/factory-source/run',{start,password});
          else toast('Collegamento Factory salvato.');
        }
        paint();if(S.factorySource.state==='running')syncTimer=setTimeout(()=>syncStatus().catch(e=>toast(e.message)),1000);
      }catch(error){S.factoryTest=null;S.factoryCreate=null;toast(error.message,12000);paint();}
      return;
    }
    const g = groupOf(S.view), entity = g && currentEntity(g);
    if (t.dataset.dvFold !== undefined) {
      const order = t.dataset.dvFold;
      if (S.folded.has(order)) S.folded.delete(order); else S.folded.add(order);
      paint(); return;
    }
    if (t.dataset.dvTab) { S.tab[g.view] = t.dataset.dvTab; S.selected = null; inspectorIdle(g); paint(); await loadRows(t.dataset.dvTab); paint(); return; }
    if (t.dataset.dvStatus !== undefined) { S.status[entity] = t.dataset.dvStatus; S.page[entity] = {offset:0, limit:(S.page[entity] || {}).limit || 50}; await loadRows(entity); paint(); return; }
    if (t.dataset.dvSort) {
      const cur = S.sort[entity] || {};
      S.sort[entity] = cur.field === t.dataset.dvSort ? (cur.desc ? {} : {field:cur.field, desc:true}) : {field:t.dataset.dvSort};
      await loadRows(entity); paint(); return;
    }
    if (t.dataset.dvRow) { openRow(entity, JSON.parse(t.dataset.dvRow)); return; }
    if (t.dataset.dvOpen) {
      const [ent, key] = JSON.parse(t.dataset.dvOpen);
      const target = GROUPS.find(x => x.entities.includes(ent));
      if (!target) return;
      S.tab[target.view] = ent; S.query[ent] = String(key[0]); S.status[ent] = '';
      location.hash = target.view; document.querySelector('[data-view="' + target.view + '"]').click();
      return;
    }
    if (t.dataset.dvRevoke) {
      S.revoking = Number(t.dataset.dvRevoke); paint();
      const input = document.getElementById('dv-revoke-reason'); if (input) input.focus();
      return;
    }
    if (t.dataset.dvRevokeConfirm) {
      const reason = String((document.getElementById('dv-revoke-reason') || {}).value || '').trim();
      if (reason.length < 3) { toast('Scrivi il motivo dell’annullamento (almeno 3 caratteri).'); return; }
      t.disabled = true; t.textContent = 'Verifica…';
      try { await api('/api/data/corrections/revoke', {ids:[Number(t.dataset.dvRevokeConfirm)], reason}); toast('Correzione annullata. Ricalcola il piano per vederne l’effetto.'); markStale();
        S.revoking = null; S.ledger = (await api('/api/data/history')).history; S.rows = {}; await loadCatalog(); paint(); }
      catch (error) { toast(error.message, 9000); t.disabled = false; t.textContent = 'Conferma'; }
      return;
    }
    const action = t.dataset.dv;
    if (action === 'new') { S.selected = {entity, key:null, row:null}; S.history = []; renderEditor(); highlight(); return; }
    if (action === 'cancel') { S.selected = null; inspectorIdle(g); highlight(); return; }
    if (action === 'revoke-cancel') { S.revoking = null; paint(); return; }
    if (action === 'exclude') {
      // two steps instead of a browser dialog: the first click asks, the second confirms
      if (t.dataset.armed) { submitEditor('exclude'); return; }
      t.dataset.armed = '1'; t.textContent = 'Conferma esclusione'; t.classList.add('armed');
      setTimeout(() => { if (t.isConnected) { delete t.dataset.armed; t.textContent = 'Escludi dal calcolo'; t.classList.remove('armed'); } }, 5000);
      return;
    }
    if (action === 'restore') { submitEditor('restore'); return; }
    if (['first', 'prev', 'next', 'last'].includes(action)) {
      const data = S.rows[entity], page = S.page[entity] || {offset:0, limit:50};
      const lastOffset = Math.max(0, Math.floor((data.total - 1) / page.limit) * page.limit);
      page.offset = action === 'first' ? 0 : action === 'prev' ? Math.max(0, page.offset - page.limit) : action === 'next' ? page.offset + page.limit : lastOffset;
      S.page[entity] = page; await loadRows(entity); paint(); S.root.querySelector('.dv-scroll') && S.root.querySelector('.dv-panel').scrollIntoView({block:'start'}); return;
    }
    if (action === 'open-workbook') {
      t.disabled = true;
      try { await api('/api/sources/open-workbook', {}); toast('Excel aperto. Dopo le modifiche salva il file e premi Rileggi Excel.'); }
      catch (error) { toast(error.message, 9000); }
      t.disabled = false; return;
    }
    if (action === 'reload-sources') {
      t.disabled = true; t.textContent = 'Rilettura in corso…';
      try { S.sources = await api('/api/sources/reload', {}); S.rows = {}; await loadCatalog(); markStale(); toast(S.sources.source_kind === 'planning_workbook' ? 'Excel riletto. Ricalcola il piano.' : 'File riletti e dati ricostruiti con le correzioni attive. Ricalcola il piano.'); }
      catch (error) { toast(error.message, 9000); }
      paint(); return;
    }
    if (t.dataset.ai === 'save') {
      readAIDraft();
      t.disabled = true; t.textContent = 'Salvataggio…';
      try { S.ai = await api('/api/settings/ai', S.aiDraft); S.aiDraft = JSON.parse(JSON.stringify(S.ai.settings)); toast(S.ai.active ? 'Impostazioni salvate: AI collegata.' : 'Impostazioni salvate.'); document.dispatchEvent(new CustomEvent('pf:ai-changed', {detail:{active:S.ai.active}})); }
      catch (error) { toast((error.errors || [error.message]).join(' · '), 9000); }
      paint(); return;
    }
    if (t.dataset.ai === 'connect') { await connectProvider(t.dataset.provider); return; }
    if (t.dataset.ai === 'sub-login') {
      readAIDraft(); S.connecting = 'abbonamento'; paint();
      try { await api('/api/settings/ai/subscription/login', {}); S.ai = Object.assign(S.ai, await api('/api/settings/ai')); }
      catch (error) { toast((error.errors || [error.message]).join(' · '), 10000); }
      S.connecting = null; paint(); watchSubscription();
      const code = document.getElementById('ai-sub-code'); if (code) code.focus();
      return;
    }
    if (t.dataset.ai === 'sub-code') { await submitSubscriptionCode(); return; }
    if (t.dataset.ai === 'sub-logout') {
      if (!t.dataset.armed) {
        t.dataset.armed = '1'; t.textContent = 'Conferma: scollega'; t.classList.add('armed');
        setTimeout(() => { if (t.isConnected) { delete t.dataset.armed; t.textContent = 'Scollega'; t.classList.remove('armed'); } }, 5000);
        return;
      }
      readAIDraft(); t.disabled = true;
      try {
        const status = await api('/api/settings/ai/subscription/logout', {});
        S.ai = status; S.aiDraft = JSON.parse(JSON.stringify(status.settings)); S.models.abbonamento = null;
        toast('Abbonamento scollegato da questa istanza.', 6000);
        document.dispatchEvent(new CustomEvent('pf:ai-changed', {detail:{active:status.active}}));
      } catch (error) { toast((error.errors || [error.message]).join(' · '), 9000); }
      paint(); return;
    }
    if (t.dataset.ai === 'replace') { readAIDraft(); S.replacing = t.dataset.provider; paint(); const el = document.getElementById('ai-key-' + S.replacing); if (el) el.focus(); return; }
    if (t.dataset.ai === 'replace-cancel') { readAIDraft(); S.replacing = null; paint(); return; }
    if (t.dataset.ai === 'activate') { await activateAssistant(); return; }
    if (t.dataset.ai === 'oauth-openrouter') {
      readAIDraft(); S.connecting = 'openrouter'; paint();
      try { const go = await api('/api/settings/ai/openrouter', {}); location.assign(go.url); }
      catch (error) { S.connecting = null; paint(); toast((error.errors || [error.message]).join(' · '), 9000); }
      return;
    }
    if (t.dataset.ai === 'disconnect') {
      // two steps, as for excluding a row: the first click asks, the second confirms
      if (!t.dataset.armed) {
        t.dataset.armed = '1'; t.textContent = 'Conferma: scollega'; t.classList.add('armed');
        setTimeout(() => { if (t.isConnected) { delete t.dataset.armed; t.textContent = 'Scollega'; t.classList.remove('armed'); } }, 5000);
        return;
      }
      readAIDraft(); t.disabled = true;
      try {
        const status = await api('/api/settings/ai/disconnect', {provider:t.dataset.provider});
        S.ai = status; S.aiDraft = JSON.parse(JSON.stringify(status.settings)); S.models[t.dataset.provider] = null;
        toast('Chiave rimossa dal server.' + (status.active ? '' : ' L’AI resta spenta finché non colleghi un fornitore.'), 7000);
        document.dispatchEvent(new CustomEvent('pf:ai-changed', {detail:{active:status.active}}));
      } catch (error) { toast((error.errors || [error.message]).join(' · '), 9000); }
      paint(); return;
    }
    if (t.dataset.ai === 'reload-keys') {
      readAIDraft();
      try { const fresh = await api('/api/settings/ai/reload', {}); const draft = S.aiDraft; S.ai = fresh; S.aiDraft = draft; S.models = {}; await loadCatalogModels(S.modelFilter.provider); toast('Chiavi ricontrollate.'); }
      catch (error) { toast(error.message, 8000); }
      paint(); return;
    }
    if (t.dataset.aiTest) {
      readAIDraft();
      const role = t.dataset.aiTest, r = S.aiDraft.roles[role] || {}, base = S.aiDraft.roles.assistant || {};
      const model = r.model || base.model, provider = (r.model ? r.provider : base.provider) || S.aiDraft.provider;
      if (!model || !provider) { S.tests[role] = {ok:false, error:'Scegli fornitore e modello prima della prova.'}; paint(); return; }
      S.tests[role] = 'busy'; paint();
      try { S.tests[role] = await api('/api/settings/ai/test', {provider, model}); }
      catch (error) { S.tests[role] = {ok:false, error:error.message}; }
      paint(); return;
    }
    if (t.dataset.aiUse) {
      readAIDraft();
      const [role, provider, model] = JSON.parse(t.dataset.aiUse);
      S.aiDraft.roles[role] = {provider, model};
      if (!S.aiDraft.provider) S.aiDraft.provider = provider;
      S.tests[role] = null;
      paint(); toast('Modello impostato per il ruolo: ricordati di salvare.');
      const el = document.querySelector('[data-ai-role="' + role + '"][data-ai-field="model"]'); if (el) el.scrollIntoView({block:'center'});
    }
  });
  document.addEventListener('submit', (e) => {
    // getAttribute: a form with a control named "id" shadows the id property.
    if (!e.target.getAttribute || e.target.getAttribute('id') !== 'dv-form') return;
    e.preventDefault(); submitEditor('save');
  });
  document.addEventListener('keydown', (e) => {
    if (!S.view) return;
    const row = e.target.closest && e.target.closest('[data-dv-row]');
    if (row && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); row.click(); }
  });
  document.addEventListener('input', (e) => {
    if (!S.view) return;
    if (e.target.id === 'dv-search') {
      const g = groupOf(S.view), entity = currentEntity(g), value = e.target.value;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(async () => {
        S.query[entity] = value; S.page[entity] = {offset:0, limit:(S.page[entity] || {}).limit || 50};
        await loadRows(entity);
        const pos = (document.getElementById('dv-search') || {}).selectionStart;
        paint();
        const input = document.getElementById('dv-search'); if (input) { input.focus(); try { input.setSelectionRange(pos, pos); } catch (_) {} }
      }, 260);
      return;
    }
    if (e.target.dataset && e.target.dataset.dvRef) { suggestIds(e.target); return; }
    const hint = e.target.name && document.querySelector('[data-dv-dur="' + e.target.name + '"]');
    if (hint) hint.textContent = e.target.value === '' ? 'in secondi' : '= ' + duration(e.target.value);
    if (e.target.id === 'ai-cat-q') {
      const pos = e.target.selectionStart; S.modelFilter.q = e.target.value; readAIDraft(); paint();
      const input = document.getElementById('ai-cat-q'); if (input) { input.focus(); input.setSelectionRange(pos, pos); }
    }
  });
  document.addEventListener('focusin', (e) => { if (S.view && e.target.dataset && e.target.dataset.dvRef) suggestIds(e.target); });
  document.addEventListener('change', async (e) => {
    if (!S.view) return;
    if (e.target.id === 'dv-show-empty') { S.showEmpty = e.target.checked; paint(); return; }
    if (e.target.id === 'dv-limit') {
      const entity = currentEntity(groupOf(S.view));
      S.page[entity] = {offset:0, limit:Number(e.target.value)}; await loadRows(entity); paint(); return;
    }
    const f = S.modelFilter;
    if (e.target.id === 'ai-cat-provider') { readAIDraft(); f.provider = e.target.value; paint(); await loadCatalogModels(f.provider); paint(); return; }
    if (['ai-cat-tools', 'ai-cat-images', 'ai-cat-free'].includes(e.target.id)) { readAIDraft(); f[e.target.id.slice(7)] = e.target.checked; paint(); return; }
    if (e.target.id === 'ai-cat-order') { readAIDraft(); f.order = e.target.value; paint(); return; }
    if (e.target.id === 'ai-enabled' || e.target.id === 'ai-provider') { readAIDraft(); paint(); }
    if (e.target.id === 'ai-setup-provider') {
      readAIDraft(); S.setup = {provider:e.target.value}; f.provider = e.target.value; S.tests.setup = null;
      paint(); await loadCatalogModels(f.provider); paint();
    }
  });
  document.addEventListener('toggle', (e) => { if (e.target.id === 'ai-advanced') S.aiAdvanced = e.target.open; }, true);
  document.addEventListener('keydown', (e) => {
    // Enter in the key field means "Verifica e collega"
    if (e.key === 'Enter' && e.target.id && e.target.id.startsWith('ai-key-')) { e.preventDefault(); connectProvider(e.target.id.slice(7)); }
    if (e.key === 'Enter' && e.target.id === 'ai-sub-code') { e.preventDefault(); submitSubscriptionCode(); }
  });

  const ready = boot();
  window.PFData = {
    titles, ready,
    handles: (view) => Boolean(groupOf(view) || ADMIN[view]),
    mount, unmount,
    // For the assistant: what the user is looking at, a filter to apply before the screen opens, a refresh
    // after a correction made from the chat.
    context: () => {
      const g = S.view && groupOf(S.view), entity = g && currentEntity(g);
      return entity ? {entity, query: S.query[entity] || '', status: S.status[entity] || ''} : null;
    },
    filter: (entity, query, status) => {
      const g = GROUPS.find(x => x.entities.includes(entity));
      if (!g) return null;
      S.tab[g.view] = entity;
      if (query !== undefined) S.query[entity] = query;
      if (status !== undefined) S.status[entity] = status;
      S.page[entity] = {offset:0, limit:(S.page[entity] || {}).limit || 50};
      delete S.rows[entity];
      return g.view;
    },
    refresh: async () => {
      S.rows = {}; S.sources = null; S.ledger = null; S.planStale = false;
      const banner = $('#data-stale'); if (banner) banner.hidden = true;
      if (!S.session || !S.session.data_screens) return;
      try { await fetchCatalog(); } catch (_) { /* the screens report it when opened */ }
      if (S.view && S.root) await mount(S.view, S.root, S.ctx);
    },
    get session() { return S.session; },
    get planStale() { return S.planStale; },
  };
  // kind suggestions for resources
  const kinds = document.createElement('datalist'); kinds.id = 'dv-list-kind';
  kinds.innerHTML = Object.entries(KINDS).map(([k, l]) => '<option value="' + k + '">' + l + '</option>').join('');
  document.addEventListener('DOMContentLoaded', () => document.body.append(kinds));
})();
