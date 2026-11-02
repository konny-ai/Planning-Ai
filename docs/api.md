# Contratti, comandi e API

## JSON v1

Contratti generati dalle dataclass: `src/planning_factory/schemas/scenario.schema.json` e `plan.schema.json`. Il parser Python è l'autorità per i vincoli semantici incrociati: riferimenti, DAG, calendari, durate, bundle e orizzonte. Campi sconosciuti, chiavi duplicate, bool al posto di interi, date invalide, NaN e timestamp privi di offset sono rifiutati.

Uno scenario richiede `schema_version: 1`, `id`, `start`, `end`, `calendars`, `resources`, `operations`; `nestings` e `settings` sono opzionali. Usare `examples/workshop.json` come contratto eseguibile. Le risorse devono riferire calendari esistenti; le operazioni devono elencare almeno un bundle simultaneo, salvo storico completato.

Un task non raggruppato ha ID `job:<operation_id>`; un nesting ha ID `nest:<nesting_id>`. Gli `operation_ids` dell'assegnazione mantengono tutti i riferimenti originali. `segments` è ordinato, senza sovrapposizioni interne, con istanti UTC. La durata è la somma dei segmenti, non l'ampiezza dal primo inizio all'ultima fine.

Un piano contiene digest, versione motore, assegnazioni, unità non collocate, carichi e consegne. `requested_mode`, `effective_mode` ed `explanation` spiegano le differenze fra richiesta e calcolo.

## CLI

```sh
planning-factory validate scenario.json
planning-factory schedule scenario.json -o plan.json --html report.html --csv plan.csv
planning-factory verify scenario.json plan.json
planning-factory optimize scenario.json -o optimized.json --metrics optimization.json
planning-factory local-search scenario.json -o local.json --budget 16 --metrics search.json
planning-factory compare plan-before.json plan-after.json --html comparison.html
planning-factory audit-setups scenario.json plan.json setup-rules.json -o setup-report.json
planning-factory serve --port 8765
planning-factory desktop
```

Exit code: 0 successo; 1 input/IO non valido o audit fallito; 2 calcolo valido ma parziale, con unità non collocate. L'output parziale viene comunque scritto. La CLI non sovrascrive lo scenario con un file di output e rifiuta percorsi di output duplicati. I report esistenti indicati esplicitamente possono essere sostituiti.

## HTTP locale

Il comando `serve` ascolta solo su `127.0.0.1`. Content-Type richiesto per POST: `application/json`. Nessun Origin browser è ammesso; usare un client locale. Corpo massimo 8 MiB, senza transfer encoding chunked.

| Endpoint | Corpo | Risposta |
|---|---|---|
| `GET /health` | — | stato e versione |
| `POST /v1/validate` | scenario v1 | validità, oppure 422 |
| `POST /v1/plan` | scenario v1 | piano v1, incluse eventuali unità non collocate |
| `POST /v1/optimize` | scenario v1 | politica vincente, piano verificato e metriche delle candidate |
| `POST /v1/setup-plan` | `scenario`, `rules`, opzionale `budget` (1..32) | scenario aggiornato, piano verificato, iterazioni e audit setup |
| `POST /v1/setup-search` | `scenario`, `rules`, opzionali `budget` e `repair_budget` (1..32) | migliore candidato valido nel campione limitato, scenario abbinato, piano e audit |
| `POST /v1/verify` | `{ "scenario": {...}, "plan": {...} }` | validità/errori; 422 per piano invalido |

HTTP 200 di `/v1/plan` non implica che tutte le unità siano collocate: leggere `unscheduled`. Rotta sconosciuta 404, Origin browser 403, Content-Type errato 415, lunghezza non accettata 413. Nessun endpoint scrive nell'ERP.

`compare --html comparison.html` genera un report offline che mostra spostamenti delle operazioni e variazioni complessive di ritardi. Il confronto richiede lo stesso identificatore di scenario per il report HTML; non certifica l'equivalenza fra due scenari con contenuti diversi.

`--html` in `schedule` e `optimize` produce un Gantt offline consultabile con filtro locale per codice fase, commessa, nesting e risorsa, oltre a carichi e milestone. Il report non esegue richieste esterne. Il file può essere aperto direttamente nel browser.

## Snapshot e simulazioni Python

```python
from planning_factory.adapters.store import SnapshotStore
from planning_factory.engine.simulation import compare, freeze

store = SnapshotStore("planning.sqlite")
revision = store.save(scenario, expected_revision=0)  # 0 = creazione
store.save_plan(scenario, result)
revision, restored = store.load(scenario.id)
frozen = freeze(scenario, result, ["ord-a/taglio"])
# I membri del suo nesting vengono congelati insieme.
```

Un successivo aggiornamento richiede la revisione corrente; `ConflictError` segnala dati cambiati. `load_plan(scenario_id, digest)` recupera un piano storico. `compare()` confronta per ID operazione; un nesting può quindi produrre più righe di confronto, una per ciascun membro, senza duplicare il carico.

## Ottimizzazione opzionale

Il comando `optimize` e l'endpoint locale `/v1/optimize` confrontano le politiche di scelta dei job pronti; `schedule` e `/v1/plan` restano invariati. Ordinamento delle metriche e limiti: [ottimizzazione](optimization.md). Nessun accesso al database Factory.

## Attrezzaggi di sequenza

`audit-setups` verifica i tempi liberi fra lavorazioni consecutive su macchine unitarie, usando una matrice dichiarata dal chiamante e gli effettivi turni macchina. Codice 0 per audit superato, 2 per deficit su un piano altrimenti valido, 1 per dati o matrice errati. Specifica [setup-audit](setup-audit.md). E' un audit senza ripianificazione o accesso al database Factory.

## Ricerca locale limitata

`local-search` valuta un massimo di 1–64 piani alternativi per scambi adiacenti nell'ordine di dispatch; ogni piano viene verificato. L'output conserva il contratto piano v1; le metriche dichiarano budget, valutazioni, miglioramenti e baseline. Contratto: [ricerca locale](local-search.md).

## Workbench desktop (Tk opzionale)

`desktop` apre l'editor locale JSON con validazione, pianificazione, ottimizzazione, ricerca locale ed esportazione Gantt/CSV/JSON. La UI non espone una porta di rete. Se il JSON e' stato cambiato rispetto al digest del piano, e' necessario ripianificare prima dell'export. Vedere [workbench desktop](desktop-workbench.md).


## Pianificazione con setup di sequenza

`setup-plan scenario.json setup-rules.json -o plan.json --scenario-output adjusted-scenario.json --budget 12` è un profilo opt-in che ritarda le release necessarie, ripianifica e ri-audita i cambi. Il piano è valido rispetto ad `adjusted-scenario.json`, che deve essere conservato insieme al piano perché contiene i vincoli derivati. Il comando standard `schedule` non cambia. Se non trova una soluzione entro il budget o i dati non permettono il controllo, restituisce errore invece di dichiarare fattibile un piano non verificato. Dettagli e limiti: [setup-planning](setup-planning.md). L'endpoint loopback `/v1/setup-plan` accetta un JSON con `scenario`, `rules` e `budget` opzionale; restituisce insieme lo scenario modificato, il piano con digest corrispondente e il report di audit.


`setup-search` prova alternative macchina dichiarate, entro due budget espliciti. L'endpoint locale e il workbench desktop restituiscono il piano insieme allo scenario aggiornato; conservare entrambi perché il digest del piano corrisponde allo scenario aggiornato. La ricerca è euristica e non garantisce l'ottimo globale.
