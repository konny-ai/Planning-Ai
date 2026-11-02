# Parametri del motore: catalogo unificato, valutazione e sviluppi futuri

**Data:** 21 ottobre 2026. **Stato:** proposta, **non approvata**. Nessuna voce
marcata "da sviluppare" è implementata: questo documento è backlog, non
descrizione del comportamento attuale.

**Fonti unite:**
- parametri del motore nuovo: `Settings`, campi di `Operation`/`Nesting`/`Resource`/`Dependency`,
  opzioni di `optimize`, `local-search`, `setup-plan`, `setup-search`
  ([requisiti](requirements.md), [API](api.md));
- opzioni dell'adapter Factory (`export_factory_scenario.py`, `to_engine_v02.py`);
- chiavi `FSM.INI [Planning_Pref]`, `SchedData` e costanti del planner storico
  ricavate in reverse engineering: **solo fonte storica** ([ADR 002](decisions/002-independent-factory-planning.md));
- finestra "Parametri di calcolo" disegnata per una pre-offerta di carpenteria
  metallica (due sedi, nesting multicommessa, materiale con data arrivo).

## 1. Quando un parametro è "intelligente"

Un parametro entra nel catalogo solo se:

1. esprime una **regola produttiva** verificabile, non un artefatto
   dell'algoritmo (tentativi, iterazioni, ordinamenti interni);
2. ha **unità e semantica univoche**: ore lavorative o trascorse, giorni
   civili o lavorativi, a quale calendario si riferisce;
3. il suo effetto è **spiegato nel piano** (`explanation`, diagnostica) e
   **misurabile** con i KPI prima/dopo sullo stesso scenario;
4. **non spegne un vincolo duro** (calendario, precedenze, materiale, nesting,
   lavori fissati), salvo profili what-if dichiarati e non pubblicabili;
5. **non duplica** un altro parametro o un dato che appartiene all'anagrafica.

Legenda della valutazione:

| Simbolo | Significato |
|---|---|
| ✅ | Mantieni: presente e sensato |
| 🔁 | Rivedi: concetto valido, forma da cambiare (rimanda a una proposta P#) |
| 🆕 | Da sviluppare: nuovo parametro proposto (sezione 4) |
| 📇 | Dato, non parametro: appartiene all'anagrafica o all'adapter |
| 🗑 | Scarta: motivo indicato |
| ⏸ | Sospeso: dipende da una decisione aperta |

## 2. Architettura proposta dei parametri

Quattro livelli distinti, per evitare la lista piatta di oltre cento chiavi
del planner storico:

| Livello | Contenuto | Dove vive | Esempi |
|---|---|---|---|
| **Dati** | Ciò che la fabbrica *è* | Scenario, alimentato dagli adapter | calendari, capacità, alternative, tempi, nesting, date materiale |
| **Profilo di calcolo** | Come si vuole pianificare | `PlanningProfile` versionato (P20) | modalità, soglie, pesi, time fence, ordine obiettivi |
| **Regole con ambito** | Eccezioni note al planner ("memoria aziendale") | `ScopedRule` tipizzate con validità (P20) | priorità di un cliente fino a una data, setup extra oltre uno spessore |
| **Budget di ricerca** | Quanto cercare, non cosa | opzioni di ottimizzazione | candidati, limite di tempo |

Ogni piano registra revisione del profilo e regole applicate, così resta
riproducibile dal solo digest dei suoi input.

## 3. Catalogo unificato

### 3.1 Orizzonte e modalità

| Parametro canonico | Chiavi storiche | Oggi | Val. | Note |
|---|---|---|---|---|
| `start` | `StartDateTimeSchedulingFunction` | `Scenario.start` | ✅ | Istante esplicito; 🗑 la stored procedure dal nome configurabile |
| `end` / orizzonte | `EndDateSchedulingFunction`, `MaxRitardo` (180 gg) | `Scenario.end`, `--horizon-days` | ✅ | Limite 3660 giorni |
| `mode` | tipo schedulazione | `Settings.mode`, `Operation.mode` | ✅ | Avanti / ritroso / automatica, anche per fase |
| `auto_backward_days` | `SogliaGGTipoPian` | `Settings` | ✅ | Default invariato; con `auto_rule = slack` e `auto_slack_seconds` decide il margine stimato (P4) |
| margine di consegna | `GiorniMargineConsegna` | solo adapter, giorni civili | 🔁 P3 | Portarlo nel motore, in ore lavorative, con override |
| fonte della consegna | `UseDueDate` | adapter | 📇 | Riga o commessa: scelta di mapping |
| ripiego ritroso → avanti | (lista avvisi) | `effective_mode` + `explanation` | ✅ | |
| `CheckForwardStartDate` | idem | — | 🗑 | Già escluso in roadmap (ADR 002) |
| `IgnoreHoliday` | idem | — | 🗑 | Il calendario è la verità; per prove usare un calendario alternativo |

### 3.2 Avanzamento, congelamento e stabilità

| Parametro canonico | Chiavi storiche | Oggi | Val. | Note |
|---|---|---|---|---|
| politica avanzamento | `Progress`, `ProgressManagementMode` | adapter `--progress-policy none\|time` | 🔁 | Manca la variante "a pezzi" (residuo = pezzi mancanti × tempo unitario) |
| `fixed_start`, `fixed_alternative` | `EnableFixedStartLot`, blocco planner | `Operation` | ✅ | |
| `freeze()` | — | API simulazione | ✅ | |
| zona congelata | `AcceptTheoreticalStartPlannedJobs`, `...OpenedJobs`, `...Threshold` | — | 🔁 P1 | Tre chiavi sostituite da una finestra temporale |
| `FixedStartIgnore` | idem | — | 🗑 | Ignorare i blocchi in silenzio è pericoloso; i conflitti sono già diagnosticati |
| stabilità rispetto al piano pubblicato | — | `compare()` solo a posteriori | 🆕 P18 | |
| avanzamento attrezzaggio | `GestAvanzAttr` | ADR 003 (stato e storico setup) | 📇 | |

### 3.3 Nesting

| Parametro canonico | Chiavi storiche | Oggi | Val. | Note |
|---|---|---|---|---|
| nesting come attività unica | `UseWorkplaneMachine`, `UseWorkplaneTime`, `MultiOdl`, `MachineNoChangingOnWorkplane` | sempre attivo | 🗑 come parametri | È il modello (ADR 002/003), non un'opzione |
| setup del nesting | `WorkPlane_SumSetupTime`, `Workplan_SumJobTime` | `Nesting.setup_seconds` sostituisce i membri | 🗑 come parametri | Nessun doppio conteggio |
| priorità del nesting | `PrioritaPDL` | minimo dei membri | ✅ | Sensato; aggiungere la diagnosi P13 |
| anticipo forzato dei membri | — | — | 🆕 P13 | |
| taglio non ancora nestato | — | — | 🆕 P14 | Stima marcata come tale |

### 3.4 Materiali, fasi esterne e trasferimenti

| Parametro canonico | Chiavi storiche | Oggi | Val. | Note |
|---|---|---|---|---|
| vincolo materiale | `CheckSuppliersDate`, `JobsWithExtParts`, `CheckExtPartsDateOnBackward`, `ExtPartsIgnoraInizioTeo` | `release` + pegging v0.3 | 🔁 P8 | I toggle storici spengono un vincolo duro |
| data promessa conto lavoro | `CheckJobDeliveryDate` | `external_end`, `--subcontract-policy anchored` | ✅ | |
| coda parti/lavori esterni | `TailTimeExternalParts`, `TailTimeExternalJobs` | `lag_seconds` (tempo trascorso) | 🔁 P5 | Serve un ritardo misurato su un calendario |
| capacità fornitori | `SuppliersCapacity` | `capacity: null` per risorsa | ✅ | Per risorsa, non come interruttore globale |
| trasferimenti tra sedi | — | — | 🆕 P5 | Giorni di viaggio ammessi e durata |

### 3.5 Perimetro del calcolo

| Parametro canonico | Chiavi storiche | Oggi | Val. | Note |
|---|---|---|---|---|
| escludi bloccate | `ExcludeBlockedOrders` | `--exclude-blocked` | ✅ | |
| solo fasi da pianificare | — | `--only-to-plan` | ✅ | |
| vista cliente | — | `--customer-view` | ✅ | |
| legami di distinta | `BillOfMaterial` | `--no-bom-links`, `production_flow` v0.3 | ✅ | |
| `WhereCond` | idem | — | 🗑 → P15 | SQL libero in configurazione: rischio di sicurezza e risultati non riproducibili |

### 3.6 Capacità e risorse

| Parametro canonico | Chiavi storiche | Oggi | Val. | Note |
|---|---|---|---|---|
| capacità per risorsa | `InternalResourcesCapacity` | `Resource.capacity` | ✅ | Interruttore globale "infinita" ammesso solo in what-if (analisi di carico grezza) |
| inefficienza | `Inefficiency`, `InefficiencyValue`, `InefficiencyFromResources` | `Resource.efficiency_bp`, `engine/efficiency.py` | ✅ | Dichiarata o misurata sullo storico (P9); default 100 % |
| alternative di risorsa | `ResourcesChangeDisable`, `UseDepartmentRes` | `alternatives` | ✅ | |
| soglia cambio macchina | `ResourcesChangeThreshold` | — | 🔁 P10 | |
| risorse diverse setup/lavoro | `ResourcesChangeBetweenSetupAndWork` | ADR 003, issue #53 | 🔁 | In sviluppo |
| macchina/operatore corrente | `UseCurrentMachine`, `UseCurrentOperator` | `--current-resource` | ✅ | |
| continuità operatore | `NoOpChangingOnJob`, `TolleranzaCambioOperatore`, `KeepRes*`, `KeepPart*` | i frammenti mantengono lo stesso bundle | ✅ / 🗑 | Il resto non ha un requisito confermato |
| frazionamento | `EnableSplitting`, `FrazionaTempoUnitario` | `Operation.splittable` | 📇 | Dato di fase, non interruttore globale |
| centri macchina, giostra | `MacCentre*`, `AmpiezzaGiostra`, `MaxElemGiostra`, `GiostraFrazioni`, `NumMinRipGiostra` | — | 🗑 | Sostituiti dall'ottimizzazione esplicita (3.9) |

### 3.7 Sovrapposizioni, attese e attrezzaggi

| Parametro canonico | Chiavi storiche | Oggi | Val. | Note |
|---|---|---|---|---|
| sovrapposizione fasi | `Overlay`, `OverlayBatches` | dipendenze `SS` + lag | 🔁 P7 | Manca il lotto di trasferimento |
| attesa massima tra fasi | `MaxDaysBetweenPhases` (+ `Step`, `OptimLevel`, `DISABLE*`) | `Dependency.max_lag_seconds`, `waits`, `enforce-waits` | ✅ | Avviso e KPI; versione dura che posticipa i predecessori o dà il motivo (P6) |
| raggruppamento attrezzaggi | `SeriesSetup`, `OttClasseAttr`, `RangeOttClasseAttr`, `CheckDueDateClasseAttr`, `PercMaxRitAttr` | `setup-search` opt-in, ADR 003 | 🔁 P11 | Riformulato sullo stato macchina esplicito |
| attrezzaggio mascherato | `TempAttrMascherato` | — | ⏸ | Preattrezzaggio: punto aperto di ADR 003 |

### 3.8 Priorità

| Parametro canonico | Chiavi storiche | Oggi | Val. | Note |
|---|---|---|---|---|
| `priority_weight` | `PesoPriorita` | 10000 | ✅ | |
| `due_weight` | `PesoData` | 1 | ✅ | |
| `customer_weight` | `CustomerPriorityWeight` | 1 | ✅ | |
| `phase_priority` | — | `Operation` | ✅ | |
| bonus lavori avviati | `PesoAperte`, `PesoLottiAperti` (duplicato), `PesoFattibili` | — | 🔁 P12 | Un solo peso |
| fattore durata | `FattorePrioDurata` | politica `shortest_processing` | 🗑 | Già coperto da `optimize` |
| fattore pallet | `FattorePrioPallet` | — | 🗑 | Nessun requisito |
| funzione utente | `PlanningUserPriorityFunc` (SQL) | — | 🗑 → P20 | Regole tipizzate al posto di codice SQL |

**Valutazione della formula attuale**
(`priority × 10000 + giorni alla consegna × 1 + priorità cliente × 1 + priorità fase`).

- *Pregio:* è trasparente, deterministica e spiegabile.
- *Limite 1:* con peso 10000 la consegna diventa uno spareggio. Funziona solo se
  la priorità lotto è riservata a poche vere urgenze.
- *Limite 2:* i giorni alla consegna ignorano il lavoro residuo. Due commesse
  con la stessa consegna e cicli di 2 ore e 5 giorni hanno lo stesso punteggio.
  → P2.
- *Limite 3:* l'arrotondamento al giorno crea molti pareggi, risolti per ID.

Si propone di tenerla come baseline e di aggiungere politiche basate sul margine (P2).

### 3.9 Ricerca e ottimizzazione

| Parametro canonico | Chiavi storiche | Oggi | Val. | Note |
|---|---|---|---|---|
| tentativi e limiti interni | `MaxTentativi`, `MaxTentativiSecFase`, `MaxRetryRitardo`, `MaxDayBefore/After`, `SchedAsGroupMaxDaysOfLate`, `AcceptAllJob`, `Split*` | — | 🗑 | Artefatti dell'algoritmo storico, non regole di fabbrica |
| ottimizzazione lavori | `JobOptimization*` | — | 🗑 | Sostituita dalle voci seguenti |
| politiche di dispatch | — | `optimize`, 4 politiche + frontiera di Pareto | ✅ | |
| ricerca locale | — | `local-search --budget 1..64` | ✅ | |
| ricerca attrezzaggi | — | `setup-search budget/repair_budget 1..32` | ✅ | |
| limite di tempo | — | — | 🆕 P16 | |
| ordine degli obiettivi | — | fisso: non collocate, commesse in ritardo, secondi di ritardo, makespan | 🔁 P17 | |

### 3.10 Log e visualizzazione

`EnableLog`, `CalcPercComp`, `ShowInternalResourceCapacity`, `ShowScheduleType`,
`ShowHiddenPhases`, `SortPlan`, `PlanSort`, `LavSort` e `Frame*` sono 🗑 per il
motore: riguardano interfaccia e log. `UseLock` è già coperto (✅) dalle revisioni
di `SnapshotStore`.

**Bilancio del merge:**
- circa 100 chiavi storiche;
- circa 25 parametri e dati già presenti nel motore nuovo;
- 20 proposte nuove o da rivedere;
- oltre 50 chiavi scartate perché artefatti, duplicati o interfaccia.

## 4. Backlog: nuovi parametri da sviluppare

Regole comuni a ogni proposta:
- il **default riproduce il comportamento attuale**, quindi non ci sono regressioni sui test esistenti;
- il campo è aggiunto al contratto JSON con versione;
- c'è un controllo in `verify_plan` o un KPI dedicato;
- l'effetto è spiegato nel piano;
- ci sono test sintetici, un aggiornamento di [coverage](coverage.md) e una issue con criteri di accettazione.

Le stime sono giornate di sviluppo del motore, test inclusi, interfaccia esclusa.

### P1: Zona congelata (time fence), 1–2 gg
- **Cosa:** `frozen_horizon` in ore lavorative. Le unità del piano pubblicato che iniziano entro la finestra restano ferme (inizio e bundle).
- **Perché:** il reparto non deve vedere cambiare il lavoro delle prossime ore. Sostituisce le tre chiavi `AcceptTheoreticalStart*`.
- **Come:**
  - `freeze_window(scenario, previous_plan, until)` in `engine/simulation.py`, riusando `freeze()`;
  - i nesting si congelano interi e le unità completate sono escluse;
  - l'audit verifica che nessuna unità nella finestra sia stata spostata;
  - KPI: unità congelate.

### P2: Politiche di dispatch basate sul margine, 2–3 gg
- **Cosa:** nuove politiche in `optimize`, con `priority` sempre baseline:
  - `min_slack`: consegna − (inizio possibile + lavoro residuo a valle);
  - `critical_ratio`: tempo alla consegna ÷ lavoro residuo;
  - `atc` (Apparent Tardiness Cost), con parametro di sensibilità `k`.
- **Perché:** la formula attuale ignora il lavoro che resta (vedi 3.8).
- **Come:**
  - calcolare una volta il lavoro residuo a valle per task, come percorso più lungo nel DAG già collassato (nesting inclusi);
  - aggiungere le politiche a `DISPATCH_POLICIES`;
  - test su scenari in cui `priority` produce un ritardo evitabile.

### P3: Margine di consegna nel motore, 1–2 gg
- **Cosa:** `delivery_buffer` in ore lavorative, con override per cliente o classe d'ordine.
- **Perché:** oggi l'adapter sottrae giorni civili alla consegna e la data promessa al cliente si perde.
- **Come:**
  - consegna interna derivata (`internal_due`) senza sovrascrivere `due`;
  - i KPI riportano il servizio su entrambe le date;
  - l'adapter smette di modificare la consegna.

### P4: Modalità automatica per margine, 2 gg
- **Cosa:** `auto_rule: due_distance | slack` e `auto_slack_hours`. Si pianifica a ritroso solo se il margine stimato (consegna − fine al più presto) supera la soglia.
- **Perché:** una commessa con consegna lontana ma ciclo lungo oggi va a ritroso e rischia il ritardo.
- **Come:**
  - passata preliminare in avanti per stimare la fine al più presto;
  - decisione per task;
  - `explanation` riporta il margine usato;
  - `due_distance` resta il default.

### P5: Ritardi a calendario e trasferimenti tra sedi, 3–4 gg
- **Cosa:**
  - `lag_calendar_id` sulla dipendenza: un ritardo misurato in tempo lavorativo di un calendario;
  - trasferimenti come attività su una risorsa "trasporto" con calendario (es. solo martedì e giovedì) e capacità (viaggi).
- **Perché:** le code esterne e i trasferimenti non sono ore trascorse. Un trasferimento del venerdì arriva il martedì.
- **Come:**
  - campo opzionale in `Dependency`, calcolato con `engine/calendars.py`;
  - l'adapter genera l'attività di trasferimento quando due fasi consecutive sono su sedi diverse;
  - l'audit ricontrolla il lag sul calendario indicato.

### P6: Attesa massima tra fasi, 2 gg (soft) + 4–6 gg (vincolo duro)
- **Cosa:** `max_lag` su dipendenze FS. Il successore deve iniziare entro X dalla fine del predecessore.
- **Perché:** limita il materiale fermo a terra e il WIP (es. tra taglio e piega). Rimpiazza `MaxDaysBetweenPhases*`.
- **Come:**
  - prima versione: avviso e KPI "attese oltre soglia";
  - versione dura: riparazione locale che posticipa il predecessore entro la sua consegna, oppure unità non collocata con motivo;
  - l'euristica seriale non garantisce la fattibilità, quindi va documentato.

### P7: Lotto di trasferimento, 3 gg
- **Cosa:** `transfer_batch_qty` sulla dipendenza. Il successore parte dopo k pezzi del predecessore.
- **Perché:** sovrapposizione realistica tra fasi (`Overlay`, `OverlayBatches`).
- **Come:**
  - servono quantità e tempo unitario nel contratto, oggi assenti (solo durate);
  - si genera un vincolo SS con lag = k × tempo unitario del predecessore;
  - si aggiunge un vincolo FF: fine successore ≥ fine predecessore + k × tempo unitario del successore;
  - l'audit va esteso a FF.

### P8: Politica del materiale, 1 gg
- **Cosa:** `material_policy: hard | warn | ignore`.
- **Perché:** sostituisce i toggle storici. Disattivare il materiale ha senso solo in prova ("se il materiale arrivasse domani…").
- **Come:**
  - `hard` di default;
  - `warn` e `ignore` marcano il piano come **non pubblicabile**;
  - la diagnostica elenca le unità che violano la data materiale.

### P9: Efficienza delle risorse, 2–3 gg + adapter
- **Cosa:** `efficiency_source: none | fixed | history` ed `efficiency_pct` per risorsa o per (risorsa, famiglia). Durata pianificata = durata standard ÷ efficienza.
- **Perché:** i tempi standard sono spesso ottimistici, e il piano lo diventa di conseguenza (`Inefficiency*`).
- **Come:**
  - `history` = rapporto consuntivo/standard su una finestra mobile (es. 90 giorni), calcolato dall'adapter con un numero minimo di campioni;
  - con dati insufficienti vale 100% e scatta una diagnostica (nessun valore inventato);
  - percentuali in basis point, come in ADR 003.

### P10: Guadagno minimo per cambiare macchina, 1–2 gg
- **Cosa:** `preferred_alternative` per operazione e `alternative_switch_min_gain`. Un'alternativa non preferita si usa solo se fa finire prima di almeno N minuti.
- **Perché:** evita spostamenti su macchine equivalenti per guadagni irrilevanti (`ResourcesChangeThreshold`).
- **Come:** il confronto avviene in fase di booking; la spiegazione riporta il guadagno ottenuto.

### P11: Raggruppamento attrezzaggi con limiti, 4–6 gg (dopo #53)
- **Cosa:** `setup_grouping_window` e `setup_grouping_max_delay`. L'ottimizzazione può avvicinare lavori con lo stesso stato macchina (ADR 003) entro la finestra, purché nessuna commessa peggiori oltre il limite.
- **Perché:** riformula `OttClasseAttr`, `RangeOttClasseAttr`, `CheckDueDateClasseAttr` e `PercMaxRitAttr` su uno stato macchina esplicito, senza matrici inventate.
- **Come:**
  - estensione di `setup_sequence_search` con budget dichiarato;
  - KPI: tempo di setup risparmiato e ritardo aggiunto.

### P12: Bonus ai lavori avviati, 1 gg
- **Cosa:** `wip_bonus`, un peso che riduce il punteggio delle commesse già avviate.
- **Perché:** chiudere quello che è iniziato riduce il WIP. Un solo peso al posto di `PesoAperte`, `PesoLottiAperti` e `PesoFattibili`.
- **Come:** un termine aggiunto alla formula; default 0.

### P13: Diagnosi dell'anticipo forzato da nesting, 1 gg
- **Cosa:** `nesting_max_advance`, che segnala i membri di un nesting lavorati molto prima del necessario (consegna − ciclo residuo) perché trainati dalla commessa più urgente.
- **Perché:** il nesting è esterno al motore (ADR 002), ma l'effetto sul magazzino va reso visibile.
- **Come:** solo KPI e avviso, nessuna modifica automatica.

### P14: Stima del taglio senza nesting (adapter), 2 gg
- **Cosa:** per le fasi di taglio non ancora nestate, durata stimata con `forecast_source: routing | quote | history` e flag `estimated`.
- **Perché:** pianificare a medio termine prima che il CAM abbia prodotto il nesting.
- **Come:** il motore non cambia, salvo il flag propagato al piano; KPI "% ore stimate".

### P15: Filtri di perimetro tipizzati (adapter), 1 gg
- **Cosa:** sede, reparto, cliente, tipo commessa, intervallo di consegne.
- **Perché:** sostituisce `WhereCond` (SQL libero).
- **Come:** parametri tipizzati nelle query di `integrations/factory-export/sql`.

### P16: Limite di calcolo, 1 gg
- **Cosa:** `time_limit_seconds` per `optimize`, `local-search` e `setup-search`. Restituisce il miglior piano verificato fino a quel momento.
- **Come:**
  - il risultato dichiara se il limite è stato raggiunto;
  - il budget a numero di candidati resta il criterio preferito, perché deterministico;
  - con il limite di tempo la ripetibilità non è garantita, e va dichiarato.

### P17: Profilo obiettivi, 2–3 gg
- **Cosa:** ordine lessicografico configurabile tra queste metriche:
  - non collocate;
  - commesse in ritardo;
  - ritardo totale;
  - ritardo massimo;
  - numero e tempo di attrezzaggi;
  - stabilità (P18);
  - makespan;
  - bilanciamento dei carichi.
- **Perché:** ADR 003 §8 lascia gli obiettivi configurabili ma senza un ordine imposto.
- **Come:**
  - default = ordine attuale;
  - pesi numerici solo se fissati dal committente;
  - la frontiera di Pareto resta disponibile.

### P18: Stabilità rispetto al piano pubblicato, 2 gg
- **Cosa:** metrica "unità spostate oltre X ore" o "spostamento medio", usabile come obiettivo (P17) e come avviso.
- **Perché:** un ricalcolo che migliora di poco i KPI ma stravolge il reparto non è un miglioramento.
- **Come:** si appoggia a `compare()`; finestra e soglia configurabili.

### P19: Straordinari candidati (what-if), 3 gg
- **Cosa:** `overtime_candidates`, finestre extra per risorsa (es. sabato mattina) che il motore **non** usa nel piano ma valuta in simulazione.
- **Perché:** rispondere a "dove serve aprire un turno extra e cosa si recupera".
- **Come:**
  - piani multipli con calendario modificato (`simulation`);
  - output: elenco di suggerimenti con guadagno KPI verificato;
  - nessuna modifica al piano pubblicato.

### P20: Profili e regole con ambito ("memoria aziendale"), 5–7 gg
- **Cosa:**
  - `PlanningProfile` versionato: nome, revisione e parametri di questo documento;
  - `ScopedRule` tipizzate da un catalogo chiuso:
    - priorità cliente con data di scadenza;
    - setup extra per (materiale, spessore, macchina);
    - giorni ammessi per i trasferimenti;
    - macchina preferita o esclusa per famiglia;
    - margine di consegna per cliente.
- **Perché:** sostituisce funzioni SQL e "chiavi segrete" con regole leggibili, datate e verificabili. Regole in testo libero restano note e non entrano nel calcolo.
- **Come:**
  - trasformazioni pure dello scenario in ordine deterministico, come `apply_technical_limits` e `apply_material_releases`;
  - i conflitti tra regole sono errori di validazione o seguono una precedenza dichiarata;
  - il piano elenca regola → unità modificate → valore prima e dopo.

### Ordine suggerito

| Gruppo | Proposte | Totale indicativo |
|---|---|---|
| 1. Basso costo, alto valore | P1, P2, P3, P8, P12, P15, P16 | 8–11 gg |
| 2. Dopo un primo collaudo con dati reali | P4, P5, P6 (avviso), P9, P10, P13, P14, P17, P18, P20 | 22–28 gg |
| 3. Dopo il completamento di #53 | P6 (vincolo duro), P7, P11, P19 | 14–18 gg |

Totale circa 44–57 giornate di sviluppo del motore. Le stime sono indicative e
vanno ricalcolate per ogni issue.

## 5. Finestra parametri per una futura interfaccia

L'interfaccia è rinviata (decisione del 20/10/2026). Quando verrà ripresa, la
finestra dovrebbe mostrare **solo** parametri del profilo e regole, nelle
sezioni di questo catalogo:

1. orizzonte e modalità;
2. avanzamento e zona congelata;
3. nesting;
4. materiali e trasferimenti;
5. perimetro;
6. risorse ed efficienza;
7. sovrapposizioni e attrezzaggi;
8. priorità e obiettivi;
9. budget di ricerca.

Le chiavi storiche scartate nella sezione 3 non vanno esposte.
