# Dati canonici e costruttore di scenario (ADR 004, v1)

Implementazione di [ADR 004](decisions/004-canonical-data-and-source-connectors.md), issue #81.

Il motore legge solo lo `Scenario` v1. Lo scenario viene composto dai **domini canonici**, a loro volta alimentati dai connettori indicati nel file di associazione di ogni installazione. Cambiare sorgente, per esempio la data del materiale presa da AS400 invece che da Factory o da un file, non cambia né il motore né il dominio.

```
sorgenti ──connettori (sola lettura)──▶ record canonici ──composizione──▶ Scenario v1 ──▶ motore
                                         + provenienza      precedenze       + manifest
                                         + rapporto         freschezza       + rapporto qualità
```

## Moduli

| Modulo | Responsabilità |
|---|---|
| `canonical/model.py` | `Record`, `Issue`, `SourceBatch`, `ReadRequest` e protocollo `Connector`, solo lettura e senza metodo di scrittura |
| `canonical/entities.py` | Entità per dominio, normalizzazione rigorosa dei valori e generazione del JSON Schema |
| `canonical/binding.py` | File di associazione per installazione, con rifiuto delle credenziali |
| `canonical/compose.py` | Unione delle fonti per chiave, precedenza per campo, conflitti, digest di dominio, freschezza |
| `canonical/quality.py` | Traduzione delle chiavi tramite le mappe di corrispondenza, controlli di qualità, unità di piano coinvolte (#87) |
| `canonical/materials.py` | Disponibilità materiale → rilasci, tramite il pegging esistente `allocate_materials` (#85) |
| `canonical/changes.py` | Confronto tra manifest: domini cambiati ed evento di ricalcolo (#84) |
| `canonical/progress.py` | Consuntivi → residui con le politiche esistenti `remaining_seconds` (#86) |
| `canonical/rules.py` | Regole con ambito confermate dal planner, applicate e tracciate per fase (P20, #93) |
| `canonical/perimeter.py` | Filtri di perimetro tipizzati sulle commesse (P15) |
| `canonical/delivery.py` | Margine di consegna con date promesse conservate (P3) e bonus lavori avviati (P12) |
| `canonical/builder.py` | Lettura dei connettori, assemblaggio deterministico dello `Scenario`, manifest e rapporto |
| `canonical/decompose.py` | `Scenario` → pacchetto canonico, per la migrazione e come oracolo dei test |
| `connectors/` | Registro dei connettori: `canonical-json` (riferimento), `file` (CSV, Excel), `sql` (query dichiarate, sqlite3/pyodbc), `factory-export` (export Factory normalizzato) |

Il confine è verificato da un test: `engine/` e `domain/` non importano mai `canonical/` né `connectors/`.

## Entità v1

Schema macchina: [`contracts/canonical-v1.schema.json`](../contracts/canonical-v1.schema.json), generato con `python tools/generate_canonical_schema.py`. Un test segnala lo schema non aggiornato.

| Dominio | Entità (chiave) |
|---|---|
| `calendars` | `calendar` (id), `calendar_shift` (calendar_id, weekday, start, end), `calendar_holiday` (calendar_id, day), `calendar_exception` (calendar_id, day), `calendar_exception_shift` (calendar_id, day, start, end), `calendar_closure` (calendar_id, start, end) |
| `resources` | `resource` (id): `calendar_id` e `capacity` obbligatori, `null` = infinita |
| `orders` | `order` (id): `due`, `release`, `priority`, `customer_priority`, `mode` facoltativi |
| `routings` | `operation` (id): `duration_seconds` e `setup_seconds` obbligatori; `operation_demand` (operation_id, alternative, resource_id); `operation_dependency` (operation_id, predecessor_id) |
| `nesting` | `nest` (id), con durata e setup condivisi una sola volta; facoltativi `status`, `version`, `resource_id` (macchina del CAM), `material`, `thickness_mm`, `sheets`, `preview`; `nest_member` (nest_id, operation_id, `quantity` informativa) |
| `subcontract` | `operation_external_end` (operation_id): fine dichiarata dal fornitore, non un consuntivo |
| `progress` | `operation_progress` (operation_id): `status` started/completed, `completed_at`, `produced_quantity`, `recorded_work_seconds`, `recorded_setup_seconds`, `resource_id` (#86); `operation_state` (operation_id): `completed_at`, `fixed_start`, `fixed_alternative` (forma diretta dello scenario) |
| `rules` | `profile` (id): i parametri `Settings` del motore, tutti obbligatori; `rule` (id): regole con ambito confermate (P20, #93) |
| `identity` | `identity_map` (map, external_id) → `canonical_id`: corrispondenze esplicite tra sistemi (#87) |
| `materials` | `material_supply` (id): articolo, quantità decimale, `available_at` (null = data ignota), tipo e riferimento; `material_need` (operation_id, item_id); `allocation_rank` (operation_id) (#85) |
| `transport` | **Riservato**: un'associazione che lo usa viene rifiutata finché non è implementato (P5 di #78) |

### Regole di normalizzazione

I valori sono primitive JSON. Questo garantisce che lo stesso dato letto da sorgenti diverse produca lo stesso digest.
- Gli istanti si convertono in UTC (`…Z`) e richiedono l'offset.
- I giorni sono `YYYY-MM-DD`, gli orari locali `HH:MM:SS`.
- Campi sconosciuti o mancanti, tipi sbagliati e `null` non ammessi sono errori, riportati con la riga di origine.

**I dati fisici sono obbligatori**: durata, setup, capacità e calendario della risorsa. Solo i campi neutri hanno un valore predefinito documentato:
- `kind` = `machine`;
- `phase_priority` = 0;
- `splittable` = false;
- `units` = 1;
- `kind` della precedenza = `FS`;
- `lag_seconds` = 0.

Una fase senza `due`, `release`, `priority`, `customer_priority` o `mode` prende il valore della propria commessa. Se manca anche lì:
- le priorità valgono 0;
- data di consegna, rilascio e modalità restano assenti.

Le alternative sono indicizzate 0..n-1, senza buchi, e mantengono l'ordine: `fixed_alternative` le indica per indice. Tutte le altre liste sono ordinate per chiave canonica, mai per ordine della sorgente. Lo scheduler spareggia già per id, quindi l'ordinamento cambia il digest ma non il piano.

## File di associazione

```json
{
  "schema_version": 1,
  "installation": "demo",
  "scenario": {"id": "settimana-43", "start": "2026-10-19T00:00:00Z", "end": "2026-11-16T00:00:00Z"},
  "profile": "standard",
  "sources": {
    "erp":    {"connector": "canonical-json", "options": {"path": "export/erp.json"}},
    "manual": {"connector": "canonical-json", "options": {"path": "manuale/correzioni.json"}}
  },
  "domains": {
    "resources": {"sources": ["erp"]},
    "calendars": {"sources": ["erp", "manual"]},
    "orders":    {"sources": ["erp"]},
    "routings":  {"sources": ["erp"], "max_age_minutes": 60},
    "progress":  {"sources": ["erp"], "max_age_minutes": 15},
    "rules":     {"sources": ["manual"]},
    "subcontract": {"sources": ["erp", "manual"],
                    "precedence": {"operation_external_end.external_end": ["manual", "erp"]}}
  }
}
```

- I percorsi relativi partono dalla cartella del file di associazione.
- I domini obbligatori sono `resources`, `calendars`, `orders` e `routings`.
- Senza `rules` si usano i `Settings` predefiniti del motore, con un avviso `default_profile`.
- **Nessuna credenziale**: le chiavi come `password`, `pwd`, `token`, `secret`, `api_key` e le stringhe di connessione con `PWD=` sono rifiutate. Si referenzia un segreto dell'installazione, per esempio `password_env`.

### Più sorgenti per lo stesso dominio

- I record si uniscono **per chiave canonica**, mai per somiglianza.
- Un record presente in una sola sorgente viene conservato (unione).
- Se le sorgenti forniscono valori diversi per lo stesso campo, l'associazione deve dichiarare `precedence` per `entità.campo`:
  - con la precedenza vince la prima sorgente dell'elenco che offre un valore, e un avviso `source_override` registra tutti i valori;
  - senza precedenza il conflitto è un errore `source_conflict`.

### Freschezza

- `max_age_minutes` vale per dominio e si confronta con l'`extracted_at` di ogni sorgente.
- Un dato scaduto produce `stale_data` di gravità `block`: lo scenario si costruisce, ma il piano **non è pubblicabile**.
- `--accept-stale` registra la conferma (`stale_data_accepted`, `warn`) e rende il piano pubblicabile.

## Connettore file (CSV, Excel) — #82

Una tabella per entità: un file `.csv` oppure un foglio `.xlsx#Foglio`. Le intestazioni sono i nomi dei campi canonici, oppure le intestazioni del cliente rinominate con `columns`. Esempio completo, generato da `examples/workshop.json`: [`examples/canonical-files/`](../examples/canonical-files/).

```json
"manuale": {"connector": "file", "options": {
  "path": "manuale",
  "tables": {"operation": "fasi.csv", "order": "commesse.xlsx#Aperte"},
  "delimiter": ";", "encoding": "utf-8-sig", "timezone": "Europe/Rome",
  "columns": {"operation": {"id": "CODICE", "order_id": "COMMESSA", "duration_seconds": "ORE"}},
  "units": {"operation.duration_seconds": "hours"},
  "extra_columns": "ignore"
}}
```

Regole:
- **Celle vuote e `null`**: una cella vuota vale "non fornito". Il testo `null` vale null, ed è ammesso solo nei campi che lo prevedono (per esempio `capacity` null = infinita).
- **Istanti**:
  - con offset vengono convertiti in UTC;
  - senza offset richiedono `timezone` della sorgente;
  - le ore ambigue o inesistenti per il cambio dell'ora legale sono rifiutate, mai indovinate.
- **Excel**:
  - date e orari numerici vengono convertiti, gli istanti numerici richiedono `timezone`;
  - gli identificativi numerici diventano testo (12345 → `"12345"`);
  - le celle in errore (`#N/D`) sono errori.
- **Unità**: con `units` i campi `*_seconds` si possono esprimere in `minutes` o `hours`, ma devono dare secondi interi.
- **Colonne sconosciute**: sono errori, salvo `extra_columns: "ignore"`.
- **Errori**: riportano file, riga e colonna, per esempio `fasi.csv:12:C`. La provenienza dei record è `file:riga`. L'impronta è lo SHA-256 dei file letti, l'`extracted_at` è la data di modifica più recente.
- **Tabelle mancanti**: una sorgente legata a un dominio senza nessuna tabella di quel dominio produce un errore `no_tables`.
- **Lettore xlsx**: usa solo la libreria standard e legge i valori ma non gli stili. Le formule contano per il valore salvato nel file.

Correzione manuale tipica: un file con le sole righe da correggere, nello stesso dominio della sorgente principale, con `precedence` sul campo. Per esempio `"operation_state.completed_at": ["manuale", "erp"]`.

`export-canonical scenario.json --csv-dir cartella` produce i modelli CSV con le intestazioni canoniche.

## Materiali — #85

Il dominio `materials` trasforma la disponibilità del materiale in **date di rilascio** delle unità di piano. Le fonti possono essere:
- giacenze;
- ordini fornitore con data prevista o confermata;
- trasferimenti;
- produzione.

Il pegging è quello esistente e deterministico (`domain/production_flow.allocate_materials`): tutto o niente per unità, senza doppie prenotazioni.

```json
"materials": {"sources": ["as400", "manuale"], "allocation_order": "due",
              "precedence": {"material_supply.available_at": ["manuale", "as400"]}}
```

- **`allocation_order` è obbligatorio**: è una scelta esplicita del cliente, non un'euristica implicita.
  - `due`: prima la consegna più vicina, poi le unità senza consegna, a parità per id.
  - `rank`: ordine dato dai record `allocation_rank`. Ogni unità con fabbisogno deve averne uno, altrimenti errore `missing_allocation_rank`.
- **Fabbisogni di un nesting**: si sommano sull'unità `nest:<id>`.
- **Fabbisogni su fasi chiuse o esterne**: non prenotano nulla (`need_on_closed_operation`, warn).
- **Rilasci**:
  - un'unità coperta riceve come rilascio la data di disponibilità più tarda dei lotti assegnati, mai prima del rilascio già dichiarato;
  - il manifest riporta i legami (`materials.pegs`: unità, articolo, lotto, quantità).
- **Carenze**:
  - un'unità non coperta produce `material_shortage` (`block`) con la quantità mancante;
  - **non viene pianificata**: riceve come rilascio la fine dell'orizzonte e il motore la riporta `NO_FEASIBLE_SLOT`, con le successive `PREDECESSOR_UNSCHEDULED`;
  - nel manifest compare in `materials.shortages`.
- **Date mancanti**: un lotto senza data (`available_at` null) non è utilizzabile (`supply_without_date`, warn). Se serve, la carenza conseguente viene riportata.
- **Quantità**: sono decimali esatti, scritti come testo (`"3.5"`) o interi; mai numeri in virgola mobile.

## Connettore SQL generico — #85

Le query e la mappatura stanno nella configurazione, non nel codice. Ogni query rinomina le colonne con i nomi dei campi canonici, senza distinzione tra maiuscole e minuscole.

```json
"as400": {"connector": "sql", "options": {
  "driver": "pyodbc", "connection_env": "CLIENTE_AS400_ODBC", "timezone": "Europe/Rome", "timeout_seconds": 60,
  "queries": {
    "material_supply": "SELECT NUMORD AS id, CODART AS item_id, QTARES AS quantity, DTCONF AS available_at, 'purchase_order' AS kind FROM ...",
    "material_need": "SELECT ... AS operation_id, ... AS item_id, ... AS quantity FROM ..."
  }}}
```

- **Driver**:
  - `sqlite3`, dalla libreria standard, aperto in sola lettura (`mode=ro`);
  - `pyodbc` per DB2/AS400, SQL Server e altri: dipendenza facoltativa, connessione `readonly=True`.
- **Credenziali**: la stringa di connessione si legge dalla variabile d'ambiente indicata in `connection_env`, mai dal file di associazione.
- **Sola lettura**:
  - si accetta una sola istruzione `SELECT` o `WITH`, senza parole chiave di scrittura;
  - l'utente del database deve comunque essere in sola lettura.
- **Valori dal database**:
  - date e istanti senza fuso richiedono `timezone`, e le ore ambigue del cambio dell'ora legale sono rifiutate;
  - i decimali restano esatti.
- **Provenienza**: `sql:<entità>:<riga>[chiave]`. L'`extracted_at` è l'istante della lettura.

Le stesse disponibilità lette da SQL o da CSV producono lo stesso scenario e lo stesso digest di dominio (test).

## Nesting e CAM — #84

Il collegamento al CAM serve a tutti i clienti con taglio lamiera. Il CAM, Alma o altri, si legge con il connettore `sql` o `file`, con query o tabelle dichiarate nella configurazione. I codici dei pezzi del CAM si collegano alle fasi dell'ERP con una mappa `identity` (vedi sotto). Lo schema del database di un CAM specifico **non** è nel codice.

```json
"cam": {"connector": "sql", "identity": {"operation": "cam-parti"}, "options": {
  "driver": "pyodbc", "connection_env": "CAM_ODBC",
  "units": {"nest.duration_seconds": "minutes", "nest.setup_seconds": "minutes"},
  "queries": {
    "nest": "SELECT ... AS id, ... AS status, ... AS resource_id, ... AS duration_seconds, ... AS setup_seconds FROM ...",
    "nest_member": "SELECT ... AS nest_id, ... AS operation_id, ... AS quantity FROM ..."}}},
"domains": {"nesting": {"sources": ["cam"], "include_statuses": ["released"]}}
```

- **Una sola attività**: il programma ha durata e setup condivisi una sola volta, mantiene i legami con tutte le fasi e le commesse e non duplica il tempo (ADR 002 e 003).
- **Stato**: `include_statuses` (`draft`, `released`, `cut`) sceglie quali programmi pianificare.
  - Un programma escluso dà `nest_excluded` (warn), e i suoi membri si pianificano uno per uno.
  - Senza `include_statuses` si pianificano tutti i programmi.
- **Macchina del CAM** (`resource_id`):
  - le fasi del programma tengono solo le alternative che usano quella macchina;
  - se una fase non ne ha, o è fissata su un'altra alternativa, l'errore è `nest_resource_conflict`.
- **Dati informativi**: materiale, spessore, lamiere, anteprima e quantità per membro sono conservati nel dominio, ma il motore non li usa.
- **Fasi di taglio non ancora nestate**: sono segnalate da `not_nested` (vedi `quality`). Il loro tempo resta quello della fase ed è una stima.

### Evento di modifica

`planning-factory changes manifest-prima.json manifest-dopo.json
planning-factory service-levels scenario.json plan.json manifest.json` elenca i domini il cui digest è cambiato, più eventuali cambi di associazione, profilo, orizzonte o materiali, e restituisce `recompute`.
- Codice di uscita `3` = da ricalcolare, `0` = nulla di cambiato.
- Rileggere gli stessi dati non è un evento, perché il digest dipende solo dal contenuto.
- Dopo il ricalcolo, `planning-factory compare` confronta i due piani.

## Avanzamento — #86

L'avanzamento può arrivare da Factory, da un MES, dall'avanzamento web di reparto o da terminali. In ogni caso diventa lavoro residuo con **una sola regola**, quella esistente: `domain/durations.remaining_seconds`.

```json
"progress": {"sources": ["mes"], "max_age_minutes": 15, "residual_policy": "quantity"}
```

- **`residual_policy`** va dichiarata (`none`, `time`, `quantity`). Senza di essa, i record `operation_progress` danno l'errore `missing_residual_policy`.
  - `none`: tempo teorico.
  - `time`: tolti lavoro e setup consuntivati.
  - `quantity`: tolti i pezzi prodotti, con `operation.quantity` obbligatoria; il setup consuntivato si toglie a parte.
- **Fase avviata**:
  - il residuo sostituisce durata e setup;
  - con `resource_id` la fase resta sulla macchina in uso (`progress_resource_conflict` se nessuna alternativa la usa);
  - residuo nullo: errore `no_residual` (chiudere la fase alla sorgente o correggere il consuntivo).
- **Fase chiusa**:
  - richiede `completed_at` non successivo all'inizio dell'orizzonte;
  - diventa storico: residuo zero, senza rilascio né inizio fisso.
- **Stesso dato due volte**: una fase descritta sia da `operation_state` che da `operation_progress` dà errore `progress_conflict`.
- **Dato mancante**: nessun consuntivo vale "niente registrato", mai "fatto". Lo stato di macchina e di attrezzaggio (ADR 003) non viene dedotto.
- **Freschezza**: si imposta con `max_age_minutes` del dominio. I dati scaduti bloccano la pubblicazione.

Non incluso: il **congelamento nel tempo** delle fasi avviate. È la zona congelata P1 di #78, perché richiede di decidere come trattare l'inizio fuori turno e i predecessori aperti.

## Connettore Factory — #83, primo passo

`factory-export` legge l'export Factory normalizzato esistente, cioè template e snapshot prodotti da `integrations/factory-export`. Lo passa **senza modifiche** a `adapters.factory.import_snapshot`, che si occupa di identità CONUM/LOCOD/IDFAS, lotti LOCLP, bundle espliciti e politica di residuo. Poi divide il risultato nei domini canonici.

```json
"factory": {"connector": "factory-export", "options": {
  "template": "export/factory-template.json", "snapshot": "export/factory-snapshot.json",
  "progress_policy": "none", "zero_phase_policy": "skip"}}
```

- **Domini serviti**: `resources`, `calendars`, `orders`, `routings`, `nesting`, `subcontract`, `progress` e `rules` (profilo `factory`). Il rilascio materiale già risolto dall'export resta nel campo `release` della fase.
- **Combinazione**: Factory si combina con altre sorgenti per dominio, con le precedenze dichiarate. Per esempio materiali da AS400, nesting dal CAM, festività aggiuntive da file.
- **Nessuna regressione sul banco**: lo scenario del banco di riferimento banco A costruito attraverso il percorso canonico ha **lo stesso digest** di `engine-scenario.json` (test).
- **Passo successivo (resta in #83)**: lettura diretta del database Factory per dominio, in sola lettura, dividendo le query `sql/00..06` e aggiungendo i filtri tipizzati P15.

## Regole con ambito (memoria aziendale) — P20 di #78, base di #93

Una regola dice **cosa** cambiare e **dove**. È la parte deterministica della memoria aziendale. La traduzione da linguaggio naturale a regola richiede un modello AI e non è inclusa.

```json
{"id": "R-12", "action": "add_setup_seconds", "value": 2700, "resource_id": "OSSITAGLIO-1",
 "min_thickness_mm": "40", "note": "spessori oltre 40 mm",
 "confirmed_by": "m.rossi", "confirmed_at": "2026-10-21T09:00:00+02:00"}
```

- **Azioni**: `add_setup_seconds` (si somma), `set_priority`, `set_customer_priority`.
- **Ambito**, tutti i criteri insieme:
  - `resource_id` (vale solo se **tutte** le alternative della fase usano quella risorsa);
  - `order_id`, `customer_id` (campo `customer_id` della commessa);
  - `material`, `min_thickness_mm` (escluso), `max_thickness_mm` (incluso), campi `material` e `thickness_mm` della fase;
  - `profile`;
  - finestra di validità `valid_from` / `valid_to` rispetto all'istante di costruzione.
- **Conferma**: solo le regole con `confirmed_by` e `confirmed_at` vengono applicate. Le altre sono elencate (`rule_not_confirmed`) e ignorate.
- **Non applicabile** (`rule_not_applicable`, warn): fase che rientra nell'ambito solo su alcune alternative; setup aggiuntivo su un membro di nesting (il setup è del programma) o su una fase avviata. Fasi chiuse ed esterne sono escluse.
- **Conflitti**: due regole che assegnano priorità diverse alla stessa fase danno errore `rule_conflict`.
- **Tracciabilità**: una regola confermata che non trova fasi dà `rule_matches_nothing`. Il manifest riporta in `rules.applied` regola, azione, valore, fasi e chi l'ha confermata, e in `rules.skipped` i motivi.

## Perimetro — P15 di #78

Il perimetro decide quali commesse entrano nello scenario, qualunque sia la sorgente. Sostituisce il `WhereCond` legacy, che era SQL libero, con criteri tipizzati.

```json
"perimeter": {"customer_ids": ["CUS-1"], "exclude_order_ids": ["C-99"],
              "due_from": "2026-10-01T00:00:00+02:00", "due_to": "2026-12-01T00:00:00+01:00",
              "include_without_due": true}
```

- **Criteri**: `order_ids` / `exclude_order_ids`, `customer_ids` / `exclude_customer_ids` (campo `customer_id` della commessa), consegna nell'intervallo `[due_from, due_to)`. Le commesse senza consegna sono incluse salvo `include_without_due: false`.
- **Pulizia**: i record delle commesse escluse si rimuovono in tutti i domini, seguendo i riferimenti annotati nel catalogo (fasi, risorse richieste, precedenze, nesting, materiali, avanzamento, regole). Il manifest riporta in `perimeter` le commesse escluse e i record rimossi.
- **Precedenza tagliata**: una fase inclusa che dipende da una fase esclusa dà `perimeter_cut_dependency` (`block`). La precedenza cade, quindi il piano non è pubblicabile.
- **Nesting parziale**: un programma che taglia anche pezzi di commesse escluse dà `perimeter_partial_nest` (warn). Il tempo condiviso resta intero.
- **Non coperti**: sede e reparto, che richiedono il dominio `transport` (P5).

## Margine di consegna e lavori avviati — P3 e P12 di #78

Sono opzioni facoltative del `profile`, spente per default.

- **`delivery_buffer_seconds`** (P3): il motore pianifica contro una **consegna interna**, cioè la consegna promessa meno il margine (in secondi trascorsi).
  - Il margine si può cambiare per cliente o commessa con regole confermate `set_delivery_buffer_seconds`.
  - Le date promesse restano nel manifest (`delivery.promised_due`). Storico e conto lavoro non vengono ridatati.
  - `planning-factory service-levels scenario.json plan.json manifest.json` riporta per ogni commessa la puntualità sulla data interna e su quella promessa.
- **`wip_priority_bonus`** (P12): le fasi aperte delle commesse che hanno già avanzamento o stato registrato ricevono una priorità più bassa di questo valore, cioè passano prima, per chiudere ciò che è iniziato. Il manifest le elenca in `delivery.wip_boosted_operations`.

Queste opzioni non entrano nei `Settings` del motore: modificano solo i dati delle fasi prima del calcolo.

## Rapporto qualità e corrispondenze tra sistemi — #87

Il rapporto (`--report`) è **unico per tutte le sorgenti** e viene prodotto prima del calcolo. Ogni voce riporta:
- gravità e codice;
- dominio, sorgente ed entità;
- chiave canonica e chiave di origine (riga del file, chiave del sistema);
- `units`: le unità del piano coinvolte, cioè `job:<fase>` o `nest:<programma>`.

L'ordinamento è stabile, quindi due esecuzioni sugli stessi dati producono lo stesso JSON.

### Corrispondenze tra sistemi

Le chiavi di sistemi diversi si collegano solo con tabelle esplicite (dominio `identity`). Esempio: i membri dei piani di lavoro del CAM nominano i pezzi con codici propri, non con le fasi dell'ERP.

```json
"sources": {
  "erp":  {"connector": "file", "options": {"tables": {"identity_map": "corrispondenze.csv", "...": "..."}}},
  "alma": {"connector": "...", "identity": {"operation": "alma-parti"}}
},
"domains": {"identity": {"sources": ["erp"]}, "nesting": {"sources": ["alma"]}}
```

- `identity` di una sorgente indica, per tipo di riferimento (`calendar`, `resource`, `order`, `operation`, `nest`), quale mappa traduce le chiavi di quella sorgente.
- Le mappe si applicano prima della composizione.
- Una chiave senza corrispondenza dà `unmatched_identity` (`block`): il record viene scartato e indicato con la sua riga di origine, mai abbinato per somiglianza, nemmeno ignorando maiuscole e minuscole. Lo scenario si costruisce, ma il piano non è pubblicabile finché la mappa non è completa.

### Controlli

| Codice | Gravità | Caso |
|---|---|---|
| `source_unavailable`, `invalid_record`, `invalid_value`, `invalid_header`, `no_tables`, `duplicate_key` | error | Sorgente, riga o cella non leggibile |
| `unknown_reference`, `alternative_gap`, `empty_nest`, `profile_selection` | error | Riferimenti non risolti o struttura incompleta |
| `source_conflict` | error | Sorgenti in disaccordo senza precedenza |
| `no_resource` | error | Fase aperta senza nessuna risorsa candidata |
| `zero_time` | error | Fase aperta con tempo di lavorazione e setup mancante o nullo |
| `nest_resource_conflict` | error | Macchina del CAM incompatibile con un membro |
| `missing_residual_policy`, `missing_quantity`, `no_residual`, `completed_without_date`, `completed_after_horizon_start`, `progress_conflict`, `progress_resource_conflict` | error | Avanzamento incompleto o contraddittorio |
| `rule_conflict` | error | Regole con priorità diverse sulla stessa fase |
| `missing_allocation_rank` | error | Ordine di assegnazione `rank` incompleto |
| `unmatched_identity` | block | Chiave di un sistema senza corrispondenza |
| `material_shortage` | block | Fabbisogno non coperto: unità non pianificata nell'orizzonte |
| `perimeter_cut_dependency` | block | Precedenza verso una fase fuori perimetro |
| `stale_data` | block | Dati oltre l'età massima del dominio |
| `not_nested` | warn | Fase su risorsa di taglio (`quality.nesting_required_resources`) senza programma di nesting: il suo tempo è una stima |
| `rule_not_confirmed`, `rule_not_applicable`, `rule_matches_nothing` | warn | Regole ignorate, parziali o senza effetto |
| `perimeter_partial_nest` | warn | Programma con pezzi di commesse fuori perimetro |
| `nest_excluded` | warn | Programma con stato non pianificato: membri singoli |
| `supply_without_date`, `need_on_closed_operation` | warn | Materiale senza data, fabbisogno su fase chiusa |
| `due_in_past`, `due_before_release`, `release_after_horizon` | warn | Date incoerenti con l'orizzonte o tra loro |
| `source_override`, `default_profile`, `stale_data_accepted`, `extracted_in_future`, `unknown_quality_resource` | warn | Tracce delle scelte applicate |

`quality.nesting_required_resources` è un elenco di risorse del cliente, a livello dell'associazione: non si deduce dal nome della macchina.

## Gravità

| Gravità | Effetto |
|---|---|
| `error` | Scenario non costruibile, per esempio sorgente non leggibile, record non valido, riferimento inesistente, conflitto senza precedenza o scenario non valido |
| `block` | Scenario costruito, piano non pubblicabile, per esempio per dati scaduti |
| `warn` | Informativo: precedenza applicata, profilo predefinito, conferma di dati scaduti |

## CLI

```
planning-factory build-scenario binding.json -o scenario.json --manifest manifest.json --report quality.json [--now ISO] [--accept-stale]
planning-factory export-canonical scenario.json [-o bundle.json] [--csv-dir cartella] [--extracted-at ISO] [--profile-id default]
planning-factory changes manifest-prima.json manifest-dopo.json
```

Codici di uscita di `build-scenario`:
- `0` = costruito e pubblicabile;
- `2` = costruito ma non pubblicabile;
- `1` = non costruibile. In questo caso si scrivono comunque manifest e rapporto, ma nessuno scenario.

Il **manifest** riporta:
- digest dell'associazione;
- istante di costruzione;
- digest dello scenario;
- profilo usato;
- per ogni dominio: digest del contenuto (indipendente dalla sorgente), numero di record ed età massima;
- per ogni sorgente: connettore, `extracted_at`, età, stato di scadenza, numero di record e impronta del file.

Il piano resta legato al manifest tramite `scenario_digest`. **Il contratto del piano non cambia.**

`export-canonical` converte uno scenario esistente, per esempio l'export Factory attuale, in un pacchetto canonico. È il ponte finché non esiste il connettore Factory di #83.

## Verifiche

`tests/test_canonical.py`:
- **Ricostruzione dei piani**: `examples/workshop.json` e la fixture Factory da 348 fasi, ricostruiti da dati canonici, danno lo stesso piano (digest a parte).
- **Associazioni equivalenti**: due associazioni diverse, una con una sola sorgente e l'altra con due sorgenti e record in ordine inverso, danno lo stesso scenario e gli stessi digest di dominio.
- **Composizione e freschezza**: conflitti, precedenze, unione, eredità dalla commessa, righe non valide con riferimento alla sorgente, riferimenti inesistenti, buchi nelle alternative, scadenza dei dati e conferma.
- **Associazione e CLI**: rifiuto delle credenziali e dei domini riservati, comandi CLI, sorgente mancante, allineamento dello schema e confine tra moduli.

## Limiti dichiarati

- Il dominio `transport` è riservato.
- Il connettore Factory legge l'export normalizzato, non ancora il database per dominio (#83). CAM, AS400 e MES passano dal connettore `sql`, con query dichiarate, verificato solo su SQLite. Non c'è ancora un collaudo su un database Alma reale (#94).
- La lettura è solo a snapshot completo. L'evento di ricalcolo si ottiene confrontando i manifest (`changes`); non esistono notifiche push.
- L'orizzonte è fisso nel file di associazione: non esiste un orizzonte relativo come "oggi + N giorni".
