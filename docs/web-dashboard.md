# Planning Factory — Web Control Room (UI/UX v0.1)

> **Preview browser collegata al motore reale**, senza database o chiamate a
> Factory. Fonte UX: `mockup-riferimento-v4.pdf` (11 pagine),
> schermate dimostrative di un **cliente diverso da banco A**. Il PDF
> non è incluso in Git; non abbiamo copiato i nomi clienti, la geometria
> o altri dati dei suoi screenshot.

## Avvio

In un ambiente Python 3.12+ che contenga il repository e le dipendenze
del motore:

```bash
python -m pip install -e .
planning-factory web
```

Aprire **http://127.0.0.1:8766**. Il default carica la fixture
di esempio `examples/workshop.json`.
Non scarica il `.bak` SQL e non richiede Docker, ODBC, Node, React,
licenze software o servizi esterni.

Per alimentare l'app con il proprio set di file:

```bash
planning-factory web --fixture /percorso/verso/scenario.json --port 8766
# oppure dati canonici CSV con binding di installazione:
planning-factory web --fixture /percorso/verso/binding.json --port 8766
```

Un `binding.json` può fare riferimento **solo** a connettori `file`
o `canonical-json`: SQL e integrazioni esterne sono rifiutati da
questa applicazione. I domini obbligatori sono quelli del modello
canonico ADR004, con riferimenti e tempi convalidati.

### Da GitHub Codespaces, sempre senza PC locale

Usare un Codespace privato del repository
`repository privato dei dati di test` secondo la nuova configurazione
Python-only proposta nella sua PR #12. Se il backend frontend
non è ancora su `main`, selezionare il ramo
`feat/planning-dashboard-ui-20261022` nel checkout del motore,
installare `pip install -e .` e lanciare `planning-factory web`
dal terminale web. Aprire **la porta 8766 in modalità Private**,
mai Public. La UI usa origin/asset locali con una sola porta.

Per sicurezza il server Python di sviluppo ascolta **solamente
`127.0.0.1`**, non possiede login e non deve essere distribuito
direttamente come applicazione aziendale esposta su Internet. Per
produzione si utilizzerà un gateway autenticato davanti a un
servizio con ruoli, job isolati, TLS e audit, già parzialmente
implementato in `adapters/service.py`. Lo sviluppo corrente
non autorizza pubblicazione ERP o autenticazioni fittizie.

## Copertura del PDF di riferimento

| Pagina del PDF | Vista web | Copertura |
|---|---|---|
| 1 — Piano macchine | Piano macchine | Timeline multi-risorsa, ricerca, intervalli 4/7/14/30 giorni, dettaglio fase con percorso critico |
| 2 — Gantt commesse | Gantt commesse | Righe espandibili, fasi e consegne; nessuna durata nesting duplicata |
| 3 — Parametri di calcolo | Parametri | Modalità, soglia ritroso, slack, priorità; **ricalcolo reale e audit** |
| 4 — Nesting e piani di lavoro | Nesting | Modulo presente, elenca nesting canonici; **nessuna geometria CAD/CAM inventata** |
| 5 — Carichi macchine | Carichi | Heatmap in ore-unità/giorno o **saturazione %** da calendario, utilizzi complessivi del motore |
| 6 — Consegne e avvisi | Consegne | Date, ritardi, task non collocati e segnali prioritari |
| 7 — Calendari e turni | Calendari | Turni, eccezioni e festività in consultazione; chiusure reali nel motore |
| 8 — Assistente AI | Pannello Assistente + Assistente e analisi | Chat in ogni vista: deterministica di default, AI opzionale in `secure-web` con proposte applicabili come simulazioni; KPI di qualità |
| 9 — Assistente AI / memoria | Assistente e analisi | Percorso critico per commessa, insight deterministici e regole realmente confermate dalla fonte |
| 10 — Memoria aziendale | Memoria | Elenco delle regole applicate e scartate dal manifest canonico; vuoto onesto per Scenario JSON senza regole |
| 11 — Programma giornaliero | Programma giornaliero | Schede per risorsa con attività e intervalli del giorno selezionato |

### Decisioni UX

- Palette navy + blu per il sistema, teal per piano confermato dal
  calcolo, arancio/rosso per rischi e ritardi, viola per operatori.
- Sidebar stabile, pannello ispettore contestuale a destra su desktop,
  navigazione a scomparsa su mobile.
- Nessuna barra viene disegnata se il task non è stato collocato.
- Gli orari in preview sono marcati UTC. La successiva localizzazione
  locale/turni industriali richiede un contratto temporale approvato
  per ogni installazione; non assumere che tutte le risorse condividano
  lo stesso fuso.
- La vista carichi aggrega **ore-unità prenotate**, non le presenta come
  percentuale di capacità giornaliera. L'utilizzo percentuale globale
  viene letto direttamente da `plan.resources`.
- Importi, geometrie, nomi cliente, materiali e stati macchina non
  vengono generati a partire da screenshot di altro cliente.
- Export CSV del piano corrente dal browser con sanitizzazione anti
  formula injection. Nessuna chiamata remota.
- La UI non mostra pulsanti fittizi di salvataggio/push ERP e non rende
  permanenti le simulazioni di parametri.
- I controlli e i testi hanno etichette leggibili, navigazione a
  tastiera, focus visibile e layout responsivo.

## Contratto API della preview

`GET /api/health` — salute e modalità file.

`GET /api/bootstrap` — restituisce coppia immutabile:
`scenario` + `plan`, tracciata da digest. Il backend calcola
`plan(scenario)` e chiama obbligatoriamente `verify_plan`.
Un fallimento del verificatore impedisce il ritorno del risultato.

`POST /api/recalculate` — riceve
`{"settings":{"mode":"auto","auto_backward_days":10}}`.
Accetta solo chiavi e valori numerici dichiarati, costruisce un
**nuovo Scenario**, esegue planner e verificatore e restituisce
la nuova coppia; non modifica i file e non registra il piano in Factory.
Un piano parziale si riconosce da `plan.unscheduled`.

`GET /api/plan.csv` — export della baseline dal backend; la UI
invece esporta dal browser il piano attualmente visualizzato.

Il limite HTTP demo di recalc è 16 KiB. Le risposte includono
`Cache-Control: no-store`, restrizioni CSP, blocco degli Origin
esterni e nessun log dei payload produttivi. **Nessuna
autenticazione integrata**: obbligatorio loopback/Codespace Private.

## Stato e gate

Questa è una **UI alpha collegata all'engine v0.2 principale**:
non un rilascio produttivo. Le PR v0.3 per MRP, setup avanzati,
prenotazioni congelate e WIP sono ancora da integrare e verificare:
non si presume il loro funzionamento nello Scenario demo.
La geometria nesting rimane esplicitamente non collegata finché sorgenti
e contratti non esistono. L'**AI generativa** esiste solo nella Control Room
autenticata (`secure-web`), spenta finché l'istanza non ha chiave e quote;
il `web` di questa pagina usa sempre l'assistente deterministico.
La memoria legge invece i record **approvati/scartati del manifest**
quando la dashboard parte da un binding CSV/JSON canonico; il suo
contenuto resta riferito alla fonte originaria anche dopo una simulazione.

Regressioni backend/frontend predisposte ma **non eseguite**
per precedente richiesta esplicita del committente.
Nessun workflow GitHub Actions avviato; nessun SQL Server o
Desktop Commander impiegato. Merge e deploy richiedono una
verifica tecnica e una decisione distinta sulla sicurezza.

## Gestione what-if e memoria canonica (PR successive)

Quando è applicata anche la PR what-if, il pannello dettaglio permette
di bloccare/spostare un task individuale; la vista Calendari permette di
aggiungere fermi **solo simulati**, e un pulsante annulla tutti gli edit.
Ogni proposta viene ripianificata e verificata; le variazioni nei ritardi
e nei task non collocati sono mostrate rispetto al piano originale.
Specifiche: [dashboard-whatif.md](dashboard-whatif.md).

Quando lo Scenario deriva dal connettore file canonico, l'API conserva
nel solo contesto di consultazione il manifest di origine. La pagina
Memoria mostra soltanto `rules.applied` e `rules.skipped` prodotti
dalla composizione: regola, azione, valore, fasi collegate, autore
dichiarato e motivi delle esclusioni. Non crea **nuove** conferme e non
registra nuove regole senza autorizzazione.

## Materiali: prova del dominio canonico, non giacenze inventate

La sezione **Disponibilità materiali** compare nel menu della
Control Room ed è alimentata **solo** dal manifest
`canonical.materials` del binding file (`pegs`, `shortages`,
`allocation_order`). È una rappresentazione consultiva delle
assegnazioni per unità/articolo/lotto e delle quantità mancanti:
non ricalcola peg nel browser e non inventa scorte.

Una sorgente con shortage può produrre uno Scenario valido ai fini
della simulazione, ma **non pubblicabile** secondo il builder canonico.
L'API lascia visualizzare il piano auditato per diagnosi, conserva
`canonical.publishable=false` e gli issue di qualità e mostra un
avviso esplicito nella UI. Non abilita alcuna pubblicazione.

In assenza di un binding con dominio `materials`, il dataset banco A
mostra uno stato vuoto significativo: non è prova che le scorte siano
sufficienti, né che Factory contenga informazioni materiali complete.

Le nuove PR v0.3 per impegni esterni, consuntivi WIP, multi-lotto
ed audit MRP non sono automaticamente rappresentate da questo
manifest v0.2; servirà integrare lo stack e associare fonti certificate.

## Consuntivi WIP — separare registrazioni e stato macchina

Con la vista **Avanzamento e consuntivi**, quando la fonte è un binding
canonico `file` o `canonical-json` con dominio `progress`, il server
riporta fino a 1.000 record `operation_progress` con la sorgente di
origine, stato dichiarato, secondi di lavoro/setup registrati,
quantità prodotta e risorsa dichiarata.

I secondi residui mostrati provengono dal **modello Scenario
normalizzato** dal builder, non da una sottrazione arbitraria in UI.
Il collegamento ai residui viene effettuato solo se l'ID sorgente
coincide esattamente con l'operazione canonica: quando una mappa
identità non è applicabile alla presentazione, compare
**ID non riconciliato** e il residuo non viene inventato.

L'elenco **non è uno snapshot MES as-of**: un record `started` o
un campo `resource_id` non garantiscono che quella macchina sia
ancora occupata, che la sua configurazione/setup sia valida, né che
la risorsa sia stata liberata. I nuovi contratti v0.3 per occupazioni
attive, setup congelati e stati macchina richiedono attestazioni e
consolidamento separati (#53, PR #132–133).

Se la fixture JSON banco A contiene soltanto lo Scenario già
pianificato e non le righe grezze del dominio `progress`, la vista
rimane volutamente vuota: non pretende di ricostruire consuntivi
originali che non sono nel file.

## Assistente conversazionale (pannello in ogni vista)

Il pulsante **Assistente** nell'intestazione apre un pannello laterale
disponibile in tutte le viste (affiancato al contenuto su desktop, a tutta
larghezza su telefono). Mostra il contesto (vista e fase selezionata),
suggerimenti specifici della vista, lo storico della conversazione nella
sessione del browser e **Nuova chat**. Il testo delle risposte è sempre
escapato; sono ammessi solo grassetto ed elenchi.

**Senza AI** (default, e sempre nel `web` non autenticato) le risposte sono
deterministiche e riportano identificativi e digest del piano:

- commesse in ritardo, carichi, task non collocati, materiali, consuntivi e
  regole, solo se presenti nei dati strutturati;
- **perché una commessa finisce quando finisce**: percorso critico a capacità
  finita ricostruito dal piano verificato (`engine/constraint_chain.py`, #88),
  con la causa di ogni passo (fase precedente, risorsa occupata da…, rilascio,
  inizio fisso, ritroso, orizzonte).

**Con AI** (solo `secure-web`, vedi [secure-control-room.md](secure-control-room.md))
il modello usa gli stessi strumenti di lettura con argomenti e può proporre
simulazioni; le proposte compaiono come schede **Applica come simulazione /
Scarta** e diventano un what-if calcolato e verificato dal motore, con il
confronto Δ esistente. Nessuna proposta tocca file, revisioni o Factory.

Le domande fuori perimetro ricevono un messaggio di mancata disponibilità:
il sistema **non inventa** cause, rimedi, stati macchina, scorte o
informazioni su altri clienti.

## Analisi per digest: percorso critico, saturazione, qualità

Il server conserva in memoria gli ultimi 4 piani che ha calcolato e
verificato (baseline, ricalcoli, simulazioni, revisioni aperte), indicizzati
per `scenario_digest`. Il browser invia solo il digest:

- `POST /api/explain` `{scenario_digest, order_id}`: percorso critico della
  commessa; usato dalla sezione **Perché finisce qui** del dettaglio fase;
- `POST /api/loads` `{scenario_digest, start?, days≤62, kind?}`: ore-unità
  prenotate e disponibili da calendario per giorno UTC; la vista **Carichi**
  offre il commutatore **Ore / Saturazione %**;
- `POST /api/quality` `{scenario_digest}`: KPI di `engine/quality.py`
  (livello di servizio, ritardo totale, durata, colli di bottiglia), nella
  pagina Assistente.

Un digest non più in memoria riceve `409 plan_not_indexed`: basta
ricalcolare. Le analisi non modificano il piano e non scrivono nulla.

## Archiviazione di scenari e piani senza database

La Control Room permette ora di esportare direttamente dal browser:

- **Scenario JSON**, conforme al contratto `Scenario v1` e
  riapribile dalla CLI `planning-factory validate` e `schedule`;
- **Piano JSON**, conforme al contratto `Plan v1`, con
  `scenario_digest` corrispondente allo Scenario esportato;
- **Piano CSV**, per consultazione tabellare, con protezione da
  formule attivabili in applicazioni spreadsheet.

I due JSON utilizzano lo **stesso prefisso SHA-256 del digest Scenario**
nel nome file; le simulazioni includono `whatif`. Se la fonte canonica
presenta blocchi di pubblicabilità, entrambi riportano anche
`NON_PUBBLICABILE` nel nome. Scaricare questi file non conferisce
alcuna autorizzazione a pubblicare il piano su Factory.

Conservare insieme i due JSON per riprodurre la verifica indipendente:

```bash
planning-factory verify scenario.json plan.json
```

L'output non salva automaticamente i file in un repository GitHub e
non deve essere caricato in un repository pubblico quando contiene
informazioni reali o riservate di un cliente.

## Host di sviluppo ammessi

La Control Room demo risponde soltanto a richieste indirizzate a
`127.0.0.1`, `localhost` o al proxy GitHub Codespaces
`*.app.github.dev`, e respinge le letture provenienti da pagine
cross-origin per ridurre rischi di DNS rebinding e consumo non richiesto.
Le scritture di scenario non esistono; i POST what-if sono verificati
per stessa origine e limiti del payload. La porta Codespaces deve
rimanere **Private**. Questo controllo non sostituisce un login o una
policy enterprise per un eventuale deployment multiutente.
