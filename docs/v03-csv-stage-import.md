# Preparare la pipeline v0.3 interamente da CSV

L'input normale della Control Room usa già JSON o binding CSV canonici
(ADR 004). La modalità avanzata di **setup/run, materiali e WIP** può ora
partire da un *secondo set di tabelle CSV esplicite*, senza installare
SQL Server o interrogare Factory.

`tools/build_v03_spec_from_csv.py` legge uno **Scenario run-only**
già normalizzato, una cartella di tabelle CSV con colonne rigorose
e produce un `stage-spec.json` con digest SHA-256 corrispondente allo
Scenario, più un file `stage-spec.sources.json` che documenta gli
hash SHA-256 dei CSV e le origini dichiarate degli stati macchina.
Questi hash attestano soltanto l'identità dei file, **non** la bontà
dei dati del cliente.

## Prova sintetica, nessuna tabella banco A richiesta

```bash
python tools/generate_v03_stage_demo.py --out /tmp/pf-csv-demo --csv

python tools/build_v03_spec_from_csv.py \
  --scenario /tmp/pf-csv-demo/scenario.json \
  --tables /tmp/pf-csv-demo/tables \
  --stock-accounting net \
  --out /tmp/pf-csv-demo/stage-from-csv.json

planning-factory web \
  --fixture /tmp/pf-csv-demo/scenario.json \
  --v03-spec /tmp/pf-csv-demo/stage-from-csv.json
```

La vista **Setup / run v0.3** esegue il calcolo soltanto premendo
il comando di anteprima e non dispone di azioni di pubblicazione.
Questo esempio usa *solo* codici inventati `SYNTH-...`:
NON deriva dal cliente Sede B né da banco A.

## Tabelle richieste (UTF-8, intestazioni esatte)

| File | Colonne |
|---|---|
| `resources.csv` | `id,kind` |
| `phases.csv` | `id,machine_id,target_state,setup_seconds,setup_locked` |
| `requirements.csv` | `phase_id,stage,kind,count,occupancy_bps,candidate_ids` |
| `machine_states.csv` | `machine_id,state_id,observed_at,source_ref` |
| `sequence.csv` | `position,task_id` |
| `priority.csv` | `position,task_id` |
| `supplies.csv` | `id,item_id,quantity,available_at` |
| `needs.csv` | `task_id,item_id,quantity` |

Gli ID di fase compilati usano `job:...` o `nest:...`.
La tabella `requirements.csv` contiene una riga per vincolo setup
o run (macchina/operatore/attrezzatura), con
`stage=setup` oppure `stage=run`;
`candidate_ids` è una lista delimitata da `|`, ad esempio
`WORKCELL_1|WORKCELL_2`.
`occupancy_bps=10000` significa 100% della risorsa,
`5000` significa 50%.
Le disponibilità sono dichiarate con timestamp ISO8601
e offset, le quantità con decimali puntati esatti, per esempio
`2.5` (non virgola, non float JSON).

`machine_states.csv` deve contenere **ogni macchina
una volta**, con `observed_at` **uguale a
`Scenario.start`** e `source_ref` non vuoto.
`state_id=null` significa stato setup sconosciuto:
non si salta il setup solo perché il vecchio piano indicava
una lavorazione precedente. Le righe sono dichiarazioni
del sorgente, non una certificazione autonoma del MES.

`sequence.csv` e `priority.csv` richiedono `position`
progressivo **da 1 a N** e copertura esatta di tutte le unità
compilate. Non si inventano priorità dai codici di fase.
`phases.csv.setup_locked` richiede `true` o `false`
esplicito, non lasciarlo vuoto.

Tutte le tabelle sono richieste anche quando alcune non hanno
dati: lasciare la sola intestazione in `supplies.csv` o
`needs.csv` non crea stock/fabbisogni. Ciò è distinto
dall'asserire che non vi sono bisogni reali: l'attendibilità
resta responsabilità della fonte.
Non inserire i CSV clienti non anonimizzati in Git.

## Tabelle opzionali

| File | Colonne | Quando |
|---|---|---|
| `commitments.csv` | `supply_id,quantity` | **Obbligatorio** con `--stock-accounting gross`, anche vuoto, e **vietato** con `net` |
| `frozen_bookings.csv` | `phase_id,stage,start,end,resource_id,occupancy_bps` | Prenotazioni setup/run congelate integrali |
| `observations.csv` | `machine_id,observed_at,state_id,source_ref,idle_confirmed` | Modalità `paused` |
| `paused_progresses.csv` | `task_id,machine_id,original_seconds,worked_seconds,remaining_seconds,paused_at,source_ref` | Modalità `paused` |
| `active_holds.csv` | `resource_id,observed_at,reserved_until,source_ref,occupying_task_id` | Modalità `holds` |

Le prenotazioni congelate con diverse risorse hanno una riga
per risorsa, con **identico segmento start/end**.
Attualmente lo stage congelato deve contenere un
segmento unico; una fase parzialmente consuntivata
non può essere falsamente registrata come congelata
integrale. L'audit finale verifica gli intervalli
e le occupazioni dichiarate.

La modalità WIP fermata richiede **tutte** le osservazioni macchina
alla partenza, tempi lavorati e residui esatti, e una
conferma esplicita che i fabbisogni di materiale siano
**residui** e non richieste totali già consumate:

```bash
python tools/build_v03_spec_from_csv.py \
  --scenario scenario.json --tables /workspace/csv \
  --mode paused --residual-materials-confirmed \
  --stock-accounting gross --out /workspace/stage-spec.json
```

Per risorse occupate, usare `--mode holds` e
`active_holds.csv`: risorse capacità 1, rilascio
esplicito e ID attività occupante **esclusa dal nuovo
Scenario**. Nessuna occupazione percentuale o fase
ancora eseguita viene improvvisata dal convertitore.

I file di altre modalità vengono **rifiutati** invece di
essere ignorati; un CSV con intestazioni non riconosciute,
colonne duplicate, orari ambigui, valori booleani non espliciti
o ID estranei viene rifiutato. Limite 8MiB/10.000 righe per CSV,
1MiB per JSON v0.3 risultante.

## Avvio e limiti di sicurezza

Il convertitore è un'operazione esplicita da terminale Python:
non espone un servizio di upload pubblico, non consulta SQL,
non apre il backup e non esegue prenotazioni ERP. La UI browser
riceve **solo** la spec risultante pre-selezionata all'avvio;
un visitatore non può cambiare path o struttura macchina tramite
il corpo HTTP. Il servizio demo resta su `127.0.0.1`
e la porta Codespaces deve rimanere **Private**.

Questa importazione **non** approva autonomamente i consuntivi
né lo stato macchina as-of. Il file `.sources.json` è
un promemoria di tracciabilità: per il rilascio industriale
servono attestazione umana e test end-to-end, tuttora
**non eseguiti** per il vincolo del committente.
