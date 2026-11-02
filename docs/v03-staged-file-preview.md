# Anteprima verificata file-only della pipeline v0.3

La Control Room può caricare, **solo su richiesta esplicita
dell'operatore**, un file di attestazioni per l'engine v0.3.
Non legge SQL Server, non acquisisce osservazioni MES, non deduce
scorte, prenotazioni o stati setup.

Il comando è:

```bash
planning-factory web --fixture /workspace/scenario-run-only.json \
  --v03-spec /workspace/stage-spec.json --port 8766
```

L'istanza browser deve restare su loopback/Codespace **Private**
(questa demo non ha autenticazione integrata).
Lo `scenario-run-only.json` contiene SOLO run v0.2,
senza `operation.setup_seconds`, durate setup legacy duplicate,
storici non trasformati o vincoli incompatibili.
Un `binding.json` canonico che potrebbe aver già applicato release MRP
**non è accettato insieme a `--v03-spec`** per evitare di
nettarle/applicarle due volte. Per costruire lo Scenario preparare un
percorso di estrazione e normalizzazione tracciato **separato**.

## Esempio sintetico pronto da generare, senza dati di un cliente

Nel Codespace Python-only è possibile creare uno Scenario e un
contratto v0.3 con **digest esatto** e stock sintetico dichiarato:

```bash
python tools/generate_v03_stage_demo.py --out /tmp/stage-demo
planning-factory web --fixture /tmp/stage-demo/scenario.json \
  --v03-spec /tmp/stage-demo/stage-spec.json
```

Aprire la porta 8766 **Private**, entrare nella vista
**Setup / run v0.3** e scegliere "Calcola e verifica".
Il generatore NON consulta banco A, non scarica database e
non dichiara credibili gli stati di setup di alcun cliente.
Serve esclusivamente per un collaudo riproducibile di sviluppo.

## Contratto stage-spec.json

La seguente è la **forma** del documento, non dati banco A reali.
`source_scenario_digest` deve essere la SHA-256 completa ottenuta
da `planning_factory.adapters.json_io.scenario_digest(scenario)` e
coincidere *esattamente* con lo Scenario selezionato:

```json
{
  "schema_version": 1,
  "source_scenario_digest": "SHA256_FULL_64_HEX_FROM_INPUT_SCENARIO",
  "mode": "normal",
  "setup_contract": {
    "schema_version": 1,
    "resources": [
      {"id": "MACHINE_1", "kind": "machine"},
      {"id": "OPERATOR_1", "kind": "operator"}
    ],
    "initial_machine_states": {"MACHINE_1": null},
    "phases": [
      {
        "id": "job:PHASE_1",
        "machine_id": "MACHINE_1",
        "target_state": "SETUP-EXAMPLE",
        "setup_seconds": 1800,
        "setup_requirements": [
          {"kind": "machine", "count": 1, "occupancy_bps": 10000,
           "candidate_ids": ["MACHINE_1"]},
          {"kind": "operator", "count": 1, "occupancy_bps": 5000,
           "candidate_ids": ["OPERATOR_1"]}
        ],
        "run_requirements": [
          {"kind": "machine", "count": 1, "occupancy_bps": 10000,
           "candidate_ids": ["MACHINE_1"]}
        ]
      }
    ]
  },
  "machine_sequence": ["job:PHASE_1"],
  "task_priority": ["job:PHASE_1"],
  "stock": {"accounting": "net", "supplies": []},
  "needs": [],
  "frozen_bookings": []
}
```

Gli ID sono placeholder e **non** costituiscono attestazioni:
risorse e fasi devono coincidere con lo Scenario realmente importato.
Non usare questo esempio senza sostituire tutti gli ID, la sequenza,
gli stati, le durate, le richieste e il digest.

Scorte e convenzioni devono provenire dallo **stesso istante di
inventario** con quantità e unità coerenti:

- `"accounting":"net"`: quantità già al netto di impegni esterni,
  senza chiave `external_commitments`;
- `"accounting":"gross"`: quantità lorde più
  `external_commitments`, **array esplicito anche se vuoto**.
  La pipeline sottrae i commitment una sola volta e verifica
  indipendentemente il pegging.

Ogni `supply` contiene `id`, `item_id`,
`quantity` **decimale stringa positivo**, `available_at`
ISO8601 con offset. Ogni `need` contiene
`task_id`, `item_id`, `quantity` decimale stringa.
Una carenza nella pipeline v0.3 ferma il calcolo tecnico
con errore: non viene aggirata dall'interfaccia.
Il `task_priority` e la `machine_sequence` contengono ID
compilati come `job:ID` o `nest:ID`; devono essere completi.
Il massimo combinazioni è configurabile `max_alternatives` (1..64).

## Consuntivi, blocchi e congelamenti

`"mode":"paused"` richiede
`observations` per **ogni macchina** con
`machine_id`, `observed_at`, `state_id`,
`source_ref` e `idle_confirmed:true`, allo **stesso istante**
`Scenario.start`. Richiede `paused_progresses` con
`task_id`, `machine_id`, `original_seconds`,
`worked_seconds`, `remaining_seconds`, `paused_at`,
`source_ref` e
`"residual_material_requirements_confirmed":true`.
È riservata a fasi *ferme* e risorse libere, non a fasi
ancora in esecuzione. Lo stato machine setup deve essere
attestato. Non ricavarlo dai contatori Factory storici.

`"mode":"holds"` richiede `active_holds` con
`resource_id`, `observed_at`, `reserved_until`,
`source_ref`, `occupying_task_id` (lavoro esterno alle
fasi da ripianificare). Ogni hold blocca una risorsa di
capacità unitaria fino a un rilascio esplicito e rende
sconosciuto lo stato setup della macchina al termine.
Non rappresenta occupazioni percentuali di operatori
o durate residue di una fase tuttora attiva.

`"frozen_bookings"` contiene, per ciascuno stage
congelato, `phase_id`, `stage` (`setup` o `run`),
`segments` (istanti `start`/`end` assoluti) e
`allocations` (`resource_id`, `occupancy_bps`).
Lo scheduler per ora conserva soltanto stage interi
con segmento unico; non corregge o accorcia consuntivi
parziali. Questo dato deve essere attestato dalla fonte,
non ricavato arbitrariamente dalla UI.

Le tre modalità `normal`, `paused`, `holds`
sono **mutuamente esclusive**: non si mescolano occupazioni
ancora attive con consuntivi fermati senza una riconciliazione
esterna verificata.

## API e UI

`POST /api/v03-preview` con corpo JSON `{}` è disponibile **solo** quando il file
`--v03-spec` viene caricato all'avvio. La vista
**Setup / run v0.3** attiva il calcolo soltanto premendo
"Calcola e verifica". Il backend applica la pipeline
materiali → audit pegging → release → setup/run →
`verify_plan` + `audit_stage_capacity` e restituisce
booking in percentuali, coppia Scenario/Plan tecnici e
riferimento al digest della fonte.

L'output ha sempre `technical_only=true`,
`publishable=false`, `erp_connected=false`.
La richiesta usa i controlli same-origin del server; non è avviabile da link esterni, né accetta percorsi o dati macchina/materiali nel body. Un errore di contenuto ritorna 422, un calcolo già
in esecuzione 503. Nessun endpoint del browser scrive
un piano su ERP o lo registra come prenotazione reale.
Le simulazioni drag/fisso/fermo della UI classica non
vengono applicate automaticamente allo Scenario
avanzato; il digest del contratto appartiene **sempre
allo Scenario sorgente immutabile**. Ripristinare gli
edit what-if prima di consultare l'anteprima v0.3.

## Stato della consegna

Questo ramo prepara la prima connessione API/UI del
motore avanzato; **non è certificato**: test e CI
sono deliberatamente **non eseguiti** su richiesta
del committente. Le attestazioni reali macchina, MRP e
WIP devono essere fornite e approvate dal proprietario
del processo; non esistono automaticamente nei CSV HF.
Il codice viene mantenuto in PR draft e non esposto
su un server pubblico privo di login.

## Gantt stage ADR003 (setup/run)

La vista avanzata visualizza ora una **timeline per risorsa**
con blocchi distinti: attrezzaggi in ambra, run in verde acqua.
Gli intervalli sono i singoli `StageBooking.segments`
restituiti dal motore, non l'intera durata tra primo inizio e
ultima fine; le pause fra turni restano vuote. Ogni tooltip
mostra ID fase, tipo stage, occupazione percentuale
(`occupancy_bps/100`) e gli istanti esatti.

Per non saturare il browser la timeline mostra inizialmente
18 risorse classificate per tipo e numero di booking;
la tabella sotto elenca fino a 250 booking nel dettaglio.
Questo non altera la verifica del motore né autorizza
spostamenti, trascinamenti o pubblicazione degli stage.
