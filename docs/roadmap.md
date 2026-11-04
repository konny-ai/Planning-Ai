# Roadmap e stato del lavoro

## Esecuzione attuale — 23 ottobre 2026

Il committente ha autorizzato esecuzione e test per completare CLIENTE.
Il precedente blocco ai test è superato per il lavoro corrente; hosted CI
e produzione restano separati. La PR #153 è riunita agli adapter CLIENTE
#146/#154. Verifiche, risultati e limiti sono mantenuti in
EXECUTION-CLIENTE-2026-10-23.md.
I checkpoint successivi prevalgono sulle checklist storiche sottostanti.

- [x] Primo flusso sintetico CSV → API HTTP → what-if → export e audit.
- [x] Verifica locale della suite e banco banco A JSON/CSV.
- [x] Riconciliazione tecnica dei nesting, con prove sintetiche.
- [ ] Esecuzione del riconciliatore su snapshot CLIENTE autorizzato e aggiornato.
- [x] Accessi, revisioni atomiche, backup/ripristino SQLite: prove HTTP locali.
- [x] Strumenti MCP stdio, backend Claude e quote: prove con provider simulato.
- [x] Bozze, conferma/revoca/versioni regole e simulazioni proposte: prove API.
- [x] Memoria con filtri risorsa/cliente/materiale/spessore da attributi canonici, bozze manuali e Claude.
- [x] Evidenze per revisione e simulazione da archivio, senza regole retroattive.
- [x] Pacchetto wheel e venv pulito: CLI, HTTP autenticato, backup, MCP stdio.
- [x] Matrice riproducibile di 14 casi sintetici materiali/setup/WIP.
- [ ] QA browser, Claude/MCP client reale e accesso aziendale TLS/proxy.
- [ ] SQL su clone CLIENTE e accettazione industriale separata.

## Completato — Motore nuovo v0.2

- [x] Collegare la progettazione alle sezioni applicabili delle fonti.
- [x] Contratti di dominio immutabili e JSON versionato.
- [x] Validazione di ID, riferimenti, tempi, precedenze e cicli dopo collasso nesting.
- [x] Calendari con turni, fusi, festività, eccezioni e indisponibilità.
- [x] Capacità finita/infinita, domande e bundle alternativi simultanei.
- [x] Nesting atomici multicommesse con durata condivisa una volta.
- [x] Precedenze FS/SS, lag, release, consegne e priorità deterministiche.
- [x] Modalità forward/backward/auto per task e diagnostica dei ripieghi.
- [x] Inizi/risorse fissati, storico, residui, freeze e confronto simulazioni.
- [x] Audit indipendente di prenotazioni, capacità, precedenze e metriche.
- [x] CLI, API Python, HTTP locale, snapshot SQLite e controllo revisioni.
- [x] Adapter di importazione Factory normalizzata e ricostruzione LOCLP/FASEQ/JobPrevious.
- [x] Export JSON/CSV, report Gantt offline, esempio di officina e benchmark.
- [x] Test e CI per Python 3.12/3.13.

## Integrazione Factory — banco clone disponibile, rilascio cliente bloccato

Il repository privato `repository privato dei dati di test` contiene il backup autorizzato
`Factory_BANCOA_2026-10-20.bak` e un workflow SQL Server 2022 usa-e-getta.
Il collaudo di pubblicazione e rilettura del 20/10/2026 (run
39440561563)
ha scritto **solo sul clone**: 769 fasi, 2.375 candidate L_LARE, 721 blocchi
L_LAPN, 600 lotti e 126 commesse; verifica successiva senza differenze.

Resta bloccata l'autorizzazione alla scrittura sul database di produzione:
servono accettazione del **nuovo** comportamento rispetto ai bisogni reali della fabbrica,
verifica degli effetti dei trigger e decisione esplicita del cliente (#3).
La parita' con il planning storico non e' richiesta.

## Direzione prodotto dal 20/10/2026 — motore indipendente

**Decisione approvata:** [ADR 002](decisions/002-independent-factory-planning.md). FSMo_SCH rimane soltanto una fonte storica di spunti; nessuna imitazione dei suoi errori e nessun obiettivo di parita' del Gantt. La correttezza si misura con hard constraints, audit indipendente e requisiti reali; la qualita' con KPI concordati (consegne, saturazione, setup, stabilita' e tempi).

Priorita' di sviluppo corrente: algoritmi di schedulazione/ottimizzazione, modellazione produttiva, attrezzaggi, dati risorsa, audit, benchmark e indicatori misurabili. **Aggiornamento 22/10/2026: la UI/UX web è stata autorizzata esplicitamente** (la sospensione del 20/10 è superata). Pianificazione ammissibile prima, ottimizzazione iterativa dopo; ogni nuovo comportamento ha test e benchmark riproducibili.

La UI gestionale e l'esposizione autenticata in rete saranno progetti d'integrazione sul contratto API del motore, dopo la validazione sul banco.


## Banco Factory: evidenze tecniche e confronto storico (non vincolante)

**Archivio storico:** i numeri di confronto con il vecchio programma riportati nelle sezioni seguenti servono unicamente a spiegare il reverse engineering passato. Non sono indicatori di correttezza o traguardi di prodotto; le discrepanze non aprono automaticamente bug.

La fixture anonimizzata il banco A (non distribuito nel repository pubblico) è stata integrata con PR #5: 769 fasi (348 con durata positiva, 421 a zero), 126 commesse e golden legacy del 15/10. La PR #6 aggiunge `zero_phase_policy='skip'` solo per scenari esplorativi: non sostituisce la semantica delle milestone. La parità resta **non certificata**: 104 fasi esterne del golden sono ancorate prima dell'orizzonte, mentre il motore v0.2 lavora dentro l'orizzonte.

Il confronto `integrations/factory-export/tools/compare_bench.py` può ora emettere un file JSON di metriche con `--metrics-json metrics.json --min-compared 348`: distingue coorti entro/prima dell'orizzonte, segnala quante operazioni sono effettivamente confrontabili e fallisce se il campione minimo non è disponibile. Le metriche registrano differenze, **non attestano la compatibilità**. La suite usa il golden anonimizzato per verificare che il confronto resti disponibile anche dopo modifiche al motore.

## Incremento: date promesse fornitori (19/10/2026)

- [x] Distinzione esplicita fra fine promessa del conto lavoro e fine realmente consuntivata.
- [x] Operazione `external_end` priva di booking; esito `external` verificato dall'audit.
- [x] Profilo opt-in in esportazione Factory basato sulle righe aperte e `MAX(PADCR)`.
- [x] Confronto golden capace di includere milestone esterne senza segmenti.
- [x] Misurare il profilo `anchored` sul banco: fixture `2026-10-05-banco-anchored`, 104/104 fasi pre-orizzonte entro 1 giorno dal golden.
- [x] Restringere l'ancoraggio alle sole fasi che il legacy ancora (regola R4: prima fase esterna del lotto con fornitore L_LARE, commessa in avanti): banco 189/348 fasi entro 1 giorno dal golden, 104/104 pre-orizzonte.
- [x] Soglia Automatica confrontata per data come il legacy e margine di consegna `--delivery-margin-days` (`GiorniMargineConsegna`): banco 217/348 fasi entro 1 giorno dal golden.
- [x] Non replicare `CheckForwardStartDate`: il ripiego deve rispettare i criteri espliciti del nuovo motore (ADR 002), senza emulare scelte del planner storico.
- [x] Durate residue dai consuntivi come il legacy (`--progress-policy time`, avanzamento a tempi): 4/4 fasi del golden.
- [x] Non riprodurre il "secondo giro" legacy: non e' un requisito di prodotto. Migliorare le priorita' e le regole di dispatch solo in base ai KPI approvati.
- [ ] Convalidare semanticamente le date promesse e le precedenze nel cliente su un clone SQL Server.
- [x] Pubblicazione del piano con le scritture del Salva legacy (`publish_factory_plan.py`): transazione unica, controllo di concorrenza, rilettura di verifica; provata su SQL Server con schema sintetico in CI.
- [x] Collaudo di pubblicazione sul clone banco A (20/10/2026): run 39440561563, 769 fasi e 721 blocchi; `--verify` senza differenze. Restano da validare le scelte di business del cliente.
- [ ] Autorizzare nel collaudo (#3) la pubblicazione sul database cliente **solo** dopo accettazione funzionale del nuovo motore e verifica tecnica dei trigger, senza richiedere parita' legacy.
- [x] Gestione eccezioni e variazioni calendario Factory nel convertitore (PR #38).
- [x] Strategia esplicita per le fasi senza risorse candidate: opzione B ([ADR 006](decisions/006-fasi-senza-risorse-candidate-proposta.md)), opt-in con `setup-stages --partial`.
- [x] Stato macchina: dichiarato nel file e valido a inizio orizzonte (confermato, ADR 006); nessun codice nuovo.
- [ ] Preattrezzaggio esterno (setup mentre la macchina lavora un altro pezzo): domanda aperta in ADR 006.

## Ottimizzazione indipendente dal DB Factory (incremento sperimentale)

- [x] Ricerca opt-in deterministica fra politiche di dispatch con baseline immutato e audit indipendente.
- [x] Ranking dichiarato per operazioni non collocate, commesse in ritardo, secondi di ritardo e makespan.
- [x] CLI e API locale con metriche trasparenti e test sintetici di regressione.
- [x] Modello esplicito opt-in e audit offline dei tempi di cambio sequenza su macchine a capacita' unitaria (non e' ancora booking).
- [x] Profilo opt-in di pianificazione con attrezzaggi espliciti, ricalcolo e audit entro budget limitato; scenario con release derivate esportato separatamente.
- [x] Ricerca setup-aware tra alternative macchina e bundle simultanei, con selezione deterministica, budget esplicito, audit indipendente e test sui nesting (PR #41, issue #27).
- [x] Audit attrezzaggi su attivita' interrompibili **non intercalate**: continuita' della famiglia macchina fra frammenti; rifiuto esplicito di task intercalati.
- [x] Audit indipendente di cambi attrezzaggio fra frammenti intercalati di task differenti: ogni ritorno richiede una transizione direzionale esplicita, con ancoraggio temporale al frammento precedente.
- [x] Proiezione opt-in di setup su slot macchina liberi: selezione esplicita
  atomic/splittable ed earliest/latest, verifica di durata, calendario,
  disponibilita' e assenza di sovrapposizioni, report JSON separato.
- [x] ADR 003 approvata: tempo di setup unico per fase; distinzione setup/run,
  machine/operator/tool con quote %, skip solo quando lo stato macchina
  conosciuto coincide, turni interrotti e preparazione prima del materiale.
- [x] Contratto canonico setup v1 indipendente da Factory + JSON Schema,
  parser rigoroso e risolutore degli stati macchina (PR #60/#61/#64).
- [x] Audit indipendente dei bundle percentuali delle fasi di setup/run
  con turni, nesting indivisibili e anti-doppio conteggio (PR #62).
- [x] Scheduler opt-in tecnico di due fasi, capacità percentuale finita e
  sequenza macchina fornita, con verifiche indipendenti (PR #63).
- [x] Sequenza macchina iniziale automatica (EDD con precedenze) e mosse di
  reinserimento nella ricerca di sequenza, sempre auditate.
- [x] Fasi già completate come storico nello scheduler a due stadi (stato macchina, precedenze, audit, KPI).
- [x] Scadenze fornitore (external_end) come vincolo FS sulle fasi successive nello scheduler a due stadi.
- [x] Inizi fissi (fixed_start) nello scheduler a due stadi, con audit dell'ancoraggio.
- [ ] Rendere completo lo scheduler setup/run: sequenza macchina ottimizzata,
  congelamenti/consuntivi, tutti i vincoli di dominio e adapter ERP.
  Il piano di stage e' tecnico e non pubblicabile in Factory (#53).

- [x] Ricerca locale a budget limitato con ricalcolo e audit indipendente di ogni candidato, confrontata al baseline su scenari sintetici.
- [x] Report offline con Gantt filtrabile, carico risorse, ritardi e confronto HTML fra due piani.
- [x] Workbench desktop iniziale: editor JSON, validazione, piano, ottimizzazione, ricerca locale ed export verificato, senza login ERP.
- [ ] **RINVIATO, NON ATTIVO** — interfaccia evoluta con form, drag-and-drop e schermate gestionali. Non fa parte del lavoro corrente sul motore.
- [x] Kit di stress sintetico offline con generazione seedata, audit, impronte deterministiche e limiti espliciti; nessuna garanzia SLA cliente.
- [x] Report KPI verificato sul piano: consegne, commesse incomplete, cause non pianificate, carico risorse e ripetibilità baseline, senza soglie arbitrarie (docs/plan-quality.md).
- [x] Benchmark sintetico v0.3 con 12 attività e 3 macchine, 1 operatore
  e 1 attrezzo condivisi, occupazioni percentuali, due forniture in date
  diverse, compatibilità tecnica su spessore, ricerca sequenze con audit
  indipendente e ripetibilità. È un test di integrazione sintetico e **non**
  un'accettazione industriale cliente.
- [ ] Validare gli indicatori su diversi snapshot rappresentativi, scegliere obiettivi e pesi con la fabbrica e misurare miglioramenti prima/dopo.

La roadmap di sviluppo autonomo non comprende collegamento o scrittura sul database Factory reale, esplicitamente esclusi dal perimetro corrente.

## Backlog parametri del motore (proposta 21/10/2026, non pianificato)

Catalogo che unisce i parametri del motore nuovo, degli adapter Factory e del planner storico, con la valutazione di ciascuno e 20 proposte di sviluppo (P1–P20): [parametri del motore](parametri-motore.md), issue #78. Sono voci di backlog: nessuna è implementata e ognuna richiede una issue con criteri di accettazione prima dello sviluppo.

## Dati canonici, connettori e prodotto (ADR 004, 21/10/2026)

Il motore legge solo dati canonici per dominio. Le sorgenti (ERP, CAM, AS400, MES, file) si collegano con connettori scelti per installazione: [ADR 004](decisions/004-canonical-data-and-source-connectors.md).

- [x] Contratto dei domini, connettori e costruttore di scenario: #81 ([v1](canonical-data.md); `transport` riservato)
- [x] Connettore file per tutti i domini: #82 (CSV, Excel `.xlsx`)
- [ ] Connettore Factory (migrazione dell'export attuale): #83 — primo passo fatto: connettore `factory-export` sull'export normalizzato
- [x] Dominio nesting e connettore CAM: #84 (CAM via connettore `sql`; collaudo CAM reale in #94)
- [x] Connettore disponibilità materiale (AS400/DB2, SQL, file): #85
- [x] Connettore avanzamento: #86 (dominio e politiche di residuo; sorgenti via `sql`/`file`; congelamento con P1 di #78)
- [x] Rapporto qualità dati e corrispondenze tra sistemi: #87 (materiali senza data con #85)
- [ ] Catena dei vincoli e percorso critico a capacità finita: #88
- [ ] Export per MS Project: #89
- [ ] Servizio di rete con autenticazione: #90
- [ ] Assistente AI in sola lettura: #92
- [ ] Memoria aziendale lato utente: #93 (lato motore: P20 di #78)
- [ ] **Rinviato:** interfaccia web del planner, #91

Verticalizzazioni cliente come configurazione e collaudo: Cliente di esempio #94.

## Nuovo percorso: web e dati file-first (22/10/2026)

- [x] Modello canonico ADR004 indipendente dalle sorgenti SQL.
- [x] Fixture banco A anonimizzate già nel repository.
- [ ] Integrare e verificare le PR file-only #134, backend dashboard #138,
  frontend #139, simulazioni what-if #140, e memoria canonica successiva.
- [ ] Verificare static asset e UI responsive/tastiera, con dataset reale
  anonimizzato e casi limite; nessun test eseguito finché non autorizzato.
- [ ] Riunificare stack v0.3 WIP/MRP/setup #123-133 e adapter canonical file,
  senza inventare booking/stati macchina o materiale.
- [ ] Aprire la Control Room via **Codespaces Private**, non su una porta
  pubblica priva di autenticazione. Per servizio aziendale usare gateway
  con ruoli e logging autorizzato.
- [ ] Attivare in seguito integrazioni CAM e geometrie nesting su fonti
  specifiche del cliente lamiera; non sostituire con i CSV banco A.
- [ ] Connettore reale a memoria aziendale confermata; il manifest ADR004
  fornisce già le regole applicate/scartate in sola lettura.

Il backup SQL storico `hf-2026-10-20` è stato rimosso dalle release
su richiesta esplicita dell'utente: il collaudo ordinario **non**
richiede SQL Server; l'estrazione Factory e la pubblicazione ERP
restano percorsi separati da abilitare e verificare solo quando necessari.
