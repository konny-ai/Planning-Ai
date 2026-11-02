# Piano di sviluppo — Assistente conversazionale e viste PDF (CLIENTE)

Ramo: `feat/cliente-assistente-chat-v2`, base `main` al tag
`cliente-collaudo-2026-10-24`. Riferimento UX: `mockup-riferimento-v4.pdf`
(pagine 1–11, mappa in [web-dashboard.md](web-dashboard.md)).

## Obiettivo

Portare nella Control Room il chatbot migliore fra i progetti esistenti
(architettura della chat di Technical-Data-Engine) **sopra** il motore e le
garanzie di Planning Factory, e rendere visibili i calcoli che il motore già
produce ma l'interfaccia non mostra (catena dei vincoli, saturazione
giornaliera, qualità del piano).

Vincoli che restano invariati (AGENTS.md, ADR 002/004):

- l'assistente è in **sola lettura**: non salva, non approva, non pubblica;
- ogni proposta di modifica diventa una **simulazione** che l'utente applica
  con un clic e che il motore ricalcola e verifica (`verify_plan`);

  *Superati il 24/10/2026 su richiesta del cliente: l'assistente agisce con il
  ruolo dell'utente (viste, filtri, ricalcolo, simulazioni, regole,
  correzioni, revisioni, rigenerazione per gli admin) e le modifiche salvate
  si annullano dalla chat. Vedi [secure-control-room.md](secure-control-room.md),
  "Azioni". Restano: ogni piano verificato dal motore, nessuna scrittura su
  Factory.*
- nessun dato inventato: gli strumenti rispondono solo con dati del piano
  verificato o del manifest canonico, con digest dello scenario;
- AI spenta finché l'istanza non ha chiave e quote (Impostazioni AI);
  senza AI resta disponibile l'analisi deterministica;
- nessuna nuova dipendenza: Python standard library e JS senza build.

## Situazione di partenza (analisi)

| Area | Planning Factory (main) | Technical-Data-Engine |
|---|---|---|
| Conversazione | domanda singola, nessuno storico | storico ultimi 10 messaggi |
| Strumenti AI | 6, **senza argomenti**, 3 giri | ~20 con argomenti, fino a 10 giri |
| Modifiche | bozza simulazione in un `prompt()` separato | proposte "Applica / Scarta" in chat |
| Posizione | pagina "Assistente e analisi" | pannello laterale contestuale |
| Suggerimenti | 6 fissi | per scheda |
| Trasferimento | scenario+piano interi a ogni domanda (21 MB su CLIENTE) | contesto compatto |
| Calcoli non esposti | catena vincoli/percorso critico, qualità, saturazione % | — |

## Pacchetti di lavoro

### WP1 — Indice dei piani calcolati (backend)

`DashboardState` conserva gli ultimi piani verificati (baseline, ricalcoli,
simulazioni) indicizzati per digest, in memoria e con limite. Le richieste
dell'assistente e delle analisi possono indicare solo `scenario_digest`: niente
più upload di 21 MB per domanda. Il percorso con scenario+piano completi resta
per le revisioni archiviate e viene sempre riverificato.

### WP2 — Analisi del piano riusabili (`adapters/plan_insights.py`)

Funzioni pure, senza scritture, su `Scenario` + `Plan` verificati:

- `order_detail(order_id)`: consegna, fine, ritardo, fasi con risorse e orari,
  task non collocati della commessa;
- `explain_order(order_id)`: percorso critico a capacità finita da
  `engine/constraint_chain.py`, con cause in italiano (predecessore, risorsa
  occupata da…, rilascio/materiale, inizio fisso, ritroso, orizzonte);
- `resource_detail(resource_id, from, to)`: utilizzo, prenotazioni nel periodo,
  ore prenotate e **capacità giornaliera dal calendario** → saturazione %;
- `daily_loads(kind)`: matrice risorsa × giorno UTC (ore prenotate, ore
  disponibili, saturazione) per la vista Carichi;
- `nesting_detail(nesting_id)`, `search(text)`, `plan_quality()`
  (`engine/quality.py`).

### WP3 — Strumenti di lettura con argomenti (`planning_tools.py`)

Catalogo esteso con schemi JSON stretti (solo chiavi dichiarate, stringhe
limitate, nessun argomento libero non previsto). I 6 strumenti esistenti
restano compatibili; MCP espone automaticamente il catalogo nuovo.
Nuovi: `get_order`, `explain_order`, `get_resource`, `get_nesting`,
`search_plan`, `get_plan_quality`.

### WP4 — Conversazione multi-turno e proposte (`claude_assistant.py`)

- `answer(question, snapshot, actor, history=…)`: storico limitato
  (10 turni, 4.000 caratteri per messaggio, ruoli validati) e giri di
  strumenti portati da 3 a 6;
- strumento riservato `propose_simulation` (fissare un task, fermo risorsa):
  produce **proposte**, mai modifiche; il server le convalida con
  `apply_whatif_edits` e scarta quelle non applicabili spiegando perché;
- risposta con `proposals`, `tools_used`, `usage`, digest.

### WP5 — Endpoint (`dashboard_api.py`, `secure_dashboard.py`)

- `POST /api/explain` `{scenario_digest, order_id}` → percorso critico
  (anche senza AI, anche nel `web` non autenticato);
- `POST /api/loads` `{scenario_digest}` → saturazione giornaliera;
- `POST /api/assistant` accetta `history` e la forma breve
  `{question, scenario_digest}`; restituisce le proposte convalidate.

### WP6 — Pannello chat contestuale (frontend)

- pulsante **Assistente** nell'intestazione, pannello laterale disponibile in
  ogni vista, contesto mostrato ("vista · fase selezionata");
- conversazione con storico nella sessione del browser e "Nuova chat";
- suggerimenti diversi per vista; formattazione sicura (testo escapato,
  grassetto ed elenchi soltanto);
- schede proposta "Applica come simulazione / Scarta": Applica imposta gli
  edit e ricalcola con `/api/simulate`; il confronto what-if esistente mostra
  gli effetti;
- senza AI: risposte deterministiche, incluso "perché la commessa X è in
  ritardo?" tramite `/api/explain`.

### WP7 — Viste del PDF

- pannello dettaglio (pag. 1–2): **Perché finisce qui** con il percorso
  critico della commessa della fase selezionata;
- Carichi (pag. 5): commutatore **ore prenotate / saturazione %**
  sui giorni UTC, con capacità dal calendario del motore;
- Assistente (pag. 8–9): pagina che riusa la stessa chat e mostra gli
  indicatori di qualità del piano (`POST /api/quality`).

### WP8 — Test, documentazione, git

- unit test per WP1–WP5 (provider simulato, nessuna chiamata a pagamento);
- regressioni esistenti aggiornate solo dove il contratto cambia
  (numero strumenti, giri);
- controllo sintassi JS (`node --check`) e prova browser con Playwright sul
  `web` locale (fixture banco A anonima): pannello chat, risposta
  deterministica, percorso critico, saturazione %;
- `tools/run_free_ci.py` verde;
- documentazione aggiornata (questo piano con esito, web-dashboard,
  secure-control-room, CHANGELOG); un commit per pacchetto, push del ramo.

## Fuori perimetro (motivato)

- orari locali nel programma giornaliero: serve un contratto temporale per
  installazione (web-dashboard.md, "Decisioni UX");
- geometria nesting/lamiere nella vista: le lamiere CLIENTE non sono nel
  contratto Scenario v1;
- prova con un modello reale: manca la chiave AI di CLIENTE (requisito aperto 3);
- pubblicazione su Factory dalla chat: esclusa per progetto.

## Criteri di accettazione

1. Senza AI: la chat risponde in modo deterministico in ogni vista e spiega il
   ritardo di una commessa con il percorso critico.
2. Con provider simulato: storico inviato, strumenti con argomenti eseguiti,
   strumenti sconosciuti rifiutati, proposte convalidate e applicabili.
3. Nessun endpoint scrive su file sorgente, store o Factory.
4. Suite completa e `run_free_ci.py` verdi; JS sintatticamente valido; prova
   browser senza errori di console.

## Esito

Sviluppo completato il 24/10/2026 sul ramo `feat/cliente-assistente-chat-v2`.

| Pacchetto | Stato | Dove |
|---|---|---|
| WP1 indice piani | fatto | `DashboardState.remember_plan/insights`, max 4 piani |
| WP2 analisi | fatto | `adapters/plan_insights.py` |
| WP3 strumenti | fatto | `adapters/planning_tools.py` (12 strumenti, anche MCP) |
| WP4 conversazione | fatto | `adapters/claude_assistant.py` (storico, 6 giri, `propose_simulation`) |
| WP5 endpoint | fatto | `/api/explain`, `/api/loads`, `/api/quality`, `/api/assistant` per digest |
| WP6 pannello chat | fatto | `web/index.html`, `web/app.js`, `web/styles.css` |
| WP7 viste PDF | fatto | dettaglio "Perché finisce qui", Carichi %, KPI qualità |
| WP8 test e documenti | fatto | vedi sotto |

**Verifiche eseguite (Linux, Python 3.13).**

- Suite completa: 666 test OK, 1 skipped (SQL Server non configurato);
  18 test nuovi in `tests/test_conversation_assistant.py`, contratti
  aggiornati in `test_planning_assistant.py` (12 strumenti, 6 giri) e
  `test_dashboard_assets.py`.
- `tools/run_free_ci.py`: 7 controlli su 7 verdi, sorgenti invariate
  durante le prove.
- Prova browser riproducibile (`tools/qa/chat_server.py` +
  `tools/qa/chat_browser.js`, Chromium/Playwright): 19 controlli su 19 su
  fixture banco A anonima. `web` senza AI: risposte deterministiche,
  percorso critico in chat e nel dettaglio, saturazione %, KPI, storico
  dopo ricarica, Nuova chat. `secure-web --local-user` con **provider
  simulato** (non un collegamento Claude reale): risposta che usa
  `explain_order`, proposta di fermo applicata come simulazione verificata
  con confronto Δ. Telefono 390 px senza scorrimento orizzontale. Nessun
  errore di console, a parte la sonda `/api/session` del `web` non
  autenticato, che risponde 404 per progetto anche su `main`.
- Tempi su scenario sintetico di 1.000 fasi e 32 macchine (`stress_kit`):
  catena dei vincoli 0,21 s (una volta per digest), saturazione 30 giorni
  0,02 s, KPI 0,10 s.

**Prova con modello reale (24/10/2026, solo test).** Con
`tools/qa/chat_server.py --claude-cli` l'assistente usa l'abbonamento
personale dello sviluppatore tramite la CLI `claude`
(`tools/qa/claude_cli_transport.py`, modello predefinito
`claude-sonnet-5-5`), sulla fixture banco A anonima:

- "Perché ORD069 è in ritardo?" → strumenti `explain_order`, `get_order`;
  risposta con digest, fatti e limiti dichiarati (circa 12 s);
- secondo turno con storico → `get_resource`, `get_order`, `explain_order`,
  `propose_simulation`: proposta pre-verificata `valid`; applicata dal
  browser come simulazione, il motore la verifica e il ritardo totale scende
  di circa 1.000 h senza nuove commesse in ritardo (circa 40 s);
- nessun errore di pagina.

Il trasporto CLI è **solo per prove personali**: un'installazione usata da
altre persone (CLIENTE) richiede la chiave API del cliente.

Osservazione emersa dalla prova, comportamento conforme a
`requirements.md`: in modalità `auto` ogni fase usa la **propria** consegna
(ORD069/40/1 scade il 9/12 ed è pianificata a ritroso verso quella data),
mentre il ritardo della commessa si misura sulla consegna **più vicina** fra
le sue fasi (4/9). Da chiarire con CLIENTE quale consegna debba guidare le fasi
di una commessa già in ritardo.

**Non verificato.** Modello AI con chiave API di CLIENTE (manca la chiave), dati
`Factory_Cliente` (privati, fuori dal repository: la catena
`run_cliente_locale.ps1` va rieseguita su Windows), browser diversi da
Chromium.

**Come provarlo sui dati CLIENTE.** Rigenerare i dati e avviare come da
EXECUTION-CLIENTE, poi aprire
`http://localhost:8765` e il pulsante **Assistente**.
