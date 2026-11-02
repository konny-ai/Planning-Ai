# Copertura e verifiche

| Area | Stato v0.2 | Evidenza |
|---|---|---|
| Dominio, ID, riferimenti e DAG | Implementata | test_validation, test_factory |
| Calendari settimanali, notturni, festività, eccezioni | Implementata nel contratto nuovo | test_calendars |
| Ora legale/solare e anni oltre 2037 | Implementata, endpoint ambigui rifiutati | test_calendars |
| Capacità, operatori e macchine simultanei | Implementata | test_engine |
| Alternative e cardinalità | Implementata con bundle/domande espliciti | test_engine |
| Precedenze FS/SS e lag | Implementata | test_engine, test_factory |
| Nesting multipli indivisibili | Implementata | test_engine |
| Setup una volta e interruzioni | Implementata; bundle conservato | test_engine |
| Avanti/ritroso/auto per fase | Implementata come euristica seriale | test_engine |
| Inizi/risorse fissi, storico e freeze | Implementata con semantica documentata | test_engine |
| Priorità e ripetibilità | Implementata nel profilo nuovo | test_engine |
| Ricerca opt-in di politiche di dispatch | Implementata; scelta verificata fra quattro euristiche, non ottimo globale | tests/test_optimization.py |
| Ricerca locale a budget limitato | Opt-in, migliora solo se il piano ricalcolato supera il baseline auditato | tests/test_local_search.py |
| Limite di tempo delle ricerche (P16) | Opzionale per optimize, local-search, setup-search; baseline sempre valutata, flag `time_limit_reached`; non ripetibile | tests/test_time_limit.py |
| Profilo obiettivi (P17) | Ordine lessicografico configurabile per optimize e local-search; default invariato; stabilità vs piano pubblicato | tests/test_objectives.py |
| Catena dei vincoli e percorso critico a capacità finita | Ricostruzione indipendente dal piano (precedenza, risorsa, calendario, rilascio/materiale, fisso); registrazione opt-in nello scheduler (`--record-constraints`) confrontata con la ricostruzione | tests/test_constraint_chain.py, docs/constraint-chain.md |
| Zona congelata (P1) | Implementata tra revisioni, con motivi delle esclusioni e verifica `check_fence` | tests/test_time_fence.py, docs/time-fence.md |
| Diagnosi P13, P18, P19 | Anticipo forzato da nesting, stabilità vs piano pubblicato, straordinari valutati in simulazione; nessuna modifica al piano | tests/test_diagnostics.py, docs/diagnostics.md |
| Alternativa preferita (P10) e attese tra fasi (P6) | P10 nella prenotazione con soglia di guadagno e spiegazione; P6 segnalato con KPI nel piano normale | tests/test_switch_gain_and_waits.py, docs/preferred-alternative-and-waits.md |
| Attesa massima imposta (P6 versione dura) | `enforce-waits`: posticipa i predecessori entro la loro consegna con rilasci, ripianifica a giri; ciò che non si ripara resta con un codice; ritardo prima/dopo | tests/test_max_lag.py, docs/preferred-alternative-and-waits.md |
| Modalità automatica per margine (P4) | `auto_rule = slack`: ritroso solo oltre `auto_slack_seconds` di margine stimato, nota nella spiegazione; default `due_distance` invariato | tests/test_auto_slack.py, docs/requirements.md |
| Efficienza delle risorse (P9) | `Resource.efficiency_bp`: tempo pianificato = standard ÷ efficienza, risorsa meno efficiente del gruppo, verificatore ricalcola; stima dallo storico senza valori inventati; default 100 % invariato | tests/test_efficiency.py, docs/resource-efficiency.md |
| Politiche di dispatch sul margine (P2) | min_slack, critical_ratio, atc opt-in in optimize; priority sempre baseline; default invariato | tests/test_slack_dispatch.py |
| Audit dei risultati e metriche | Implementata | AuditTests, test_adapters |
| Import/export, CLI, API HTTP locale | Implementata | test_adapters e flusso CI |
| Gantt HTML offline filtrabile e confronto piani | Implementati; output HTML statico senza dipendenze esterne | test_offline_workbench |
| Workbench desktop JSON offline | Tkinter opzionale, controller verificato headless, export con controllo digest | test_desktop_ui.py |
| Snapshot SQLite e concorrenza | Implementata | StorageTests |
| Stress sintetico deterministico | Generator seedato, audit a ogni run, limiti CLI e report SHA-256 | tests/test_stress_kit.py |
| Factory: identità, sequenze e LOCLP | Implementata su export normalizzato | test_factory |
| Export SQL Server e benchmark di banco | Fixture anonimizzata e query read-only implementate; connettore live e write-back ancora assenti | fixtures/factory-bench-a, tests/test_factory_bench |
| Parità completa M01–M16/103 difetti | Non certificata: golden di 348 fasi confrontabili, 104 esterne con fine precedente all'orizzonte | compare_bench.py, ADR 001 |
| Audit esplicito setup di sequenza | Opt-in su macchine unitarie: controlla i gap di calendario; non prenota cambi e non gestisce fasi interrotte | test_setup_audit, docs/setup-audit.md |
| Giostra, lookahead, KeepPart, serie di setup legacy | Non attivati; richiedono profilo/fixture cliente | fonti M04/M07 e capitolo 44 |
| Export MS Project (MSPDI) | Implementato: commesse, unità, collegamenti, risorse; apertura in Project da collaudare col cliente | tests/test_msproject.py, docs/msproject-export.md |
| Pubblicazione ERP e trigger del Salva | Non implementata; dipende dal contratto cliente | fonti M14 e docs/factory-adapter.md |
| Dati canonici e connettori (ADR 004) | Contratto v1, associazione per installazione, precedenze, freschezza, manifest; connettori `canonical-json` e `file` (CSV, Excel); rapporto qualità unico e corrispondenze esplicite tra sistemi. materiali con pegging e carenze, connettore `sql`, nesting da CAM con macchina assegnata ed evento `changes`, avanzamento con politiche di residuo, connettore `factory-export` con digest del banco invariato, regole con ambito confermate (P20), perimetro tipizzato (P15), margine di consegna (P3) e bonus lavori avviati (P12); trasporti riservati, altri connettori da fare | tests/test_canonical.py, tests/test_file_connector.py, tests/test_data_quality.py, tests/test_materials.py, tests/test_nesting.py, tests/test_progress.py, tests/test_factory_connector.py, tests/test_rules.py, tests/test_perimeter.py, tests/test_delivery.py, docs/canonical-data.md |
| Servizio di rete con login e ruoli | Token con hash, ruoli reparto/planner/admin, worker isolati con limite di tempo, revisioni, registro; TLS fuori da loopback | tests/test_service.py, docs/service.md |
| Interfaccia gestionale e deploy | Interfaccia rinviata dal committente; deploy non eseguito da questo repository | API documentata per la futura integrazione |

## Verifica automatica

Suite unittest con casi deterministici e DAG casuali per 30 seed. Ogni DAG contiene 16 operazioni, alternative, SS/FS, lag, priorità, modalità miste e interruzioni. Gli audit di piani alterati verificano che sovrapposizioni, durate modificate, unità mancanti e metriche false non vengano accettati.

Il benchmark `tools/benchmark.py` usa 1000 operazioni sintetiche in catene da 5 fasi, 8 macchine e 100 giorni. Misurazione locale del 18/10/2026: 2,36 secondi, 1000 assegnate, 0 non collocate, audit incluso. Non è una previsione delle prestazioni su dati cliente o delle query ERP.

L'esempio `examples/workshop.json` include 8 operazioni originali, due unite in un nesting. Il piano contiene 7 unità (una storica), 0 non collocate. Il confronto golden verifica che modifiche future non cambino silenziosamente il caso completo.

## Golden Factory anonimizzato

Il banco include 769 fasi (348 a durata positiva) e un piano FSMo_SCH acquisito. E' possibile verificare che il motore produca un piano valido e misurare gli scarti rispetto al golden, ma questo **non equivale a parita' produttiva**: il confronto esistente non certifica ancora la parita' delle date promesse in produzione, il set completo di profili legacy e l'eventuale pubblicazione ERP.

`compare_bench.py --metrics-json ... --min-compared ...` genera statistiche per coorti (legacy prima/dentro l'orizzonte), utilizzabili come serie temporale di regressione senza confondere un miglioramento reale con un semplice cambio del campione confrontato.
