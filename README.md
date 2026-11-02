# Planning Factory

Motore di pianificazione della produzione **a capacità finita**, v0.2.0.
Pianifica commesse, fasi e nesting condivisi su macchine, operatori e altre
risorse, e restituisce un piano **verificato in modo indipendente**, con
carichi, ritardi e motivazione delle attività non collocate.

- Funziona con **file locali**, senza database né servizi esterni.
- Nessuna dipendenza oltre alla libreria standard di Python (su Windows solo `tzdata`).
- Risultato deterministico e ripetibile: stesso scenario, stesso piano.
- Interfacce: riga di comando, libreria Python, API HTTP locale, interfaccia web (Control Room), workbench desktop.
- Pensato per integrarsi con Factory ERP in sola lettura; non lo richiede.

È un prodotto indipendente: il planner storico di Factory è servito solo come
analisi del dominio e **non** definisce la correttezza dei nuovi piani.

## Avvio in 2 minuti

### Windows: un doppio clic

Scarica il repository (o `git clone`) e fai doppio clic su **`avvia.bat`**.
Controlla Python (lo installa con winget se manca), crea l'ambiente, installa
Planning Factory, chiude un'eventuale copia già aperta sulla stessa porta,
avvia la Control Room e apre il browser su <http://127.0.0.1:8770>.
Dal secondo avvio parte in pochi secondi. Opzioni: `avvia.bat mio.xlsx`,
`avvia.bat semplice`, `avvia.bat test`, `avvia.bat ferma`
(tutte in [docs/guida-installazione.md](docs/guida-installazione.md)).

### Linea di comando (Windows, Linux, macOS)

Serve **Python 3.12 o successivo**.

```sh
git clone https://github.com/konny-ai/planning-factory.git
cd planning-factory
python -m venv .venv
. .venv/bin/activate            # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e .

mkdir out
planning-factory schedule examples/workshop.json -o out/plan.json --html out/report.html
planning-factory verify examples/workshop.json out/plan.json
```

Atteso: `{"assigned": 7, "unscheduled": 0, ...}` e `{"valid": true, "errors": []}`.
Apri `out/report.html` nel browser per il Gantt. Poi, per l'interfaccia web:

```sh
planning-factory web --fixture examples/workshop.json --port 8770
# apri http://127.0.0.1:8770
```

Su Windows con più Python installati: `py -3.13 -m venv .venv`.
**Guida passo passo, esiti attesi e risoluzione dei problemi:
[docs/guida-installazione.md](docs/guida-installazione.md).**

## Che cosa sa fare

- Calendari settimanali con fuso esplicito, turni notturni, festività, eccezioni e indisponibilità.
- Capacità per risorsa, domande multiple, prenotazione simultanea di macchina e operatore.
- Alternative di risorsa, precedenze fine→inizio e inizio→inizio, lag, disponibilità dei materiali.
- Pianificazione in avanti, a ritroso o automatica, con ripiego esplicito.
- Nesting atomici multicommessa: durata condivisa e collegamento a tutte le operazioni.
- Attrezzaggi, lavoro residuo, operazioni interrompibili, inizi e risorse fissati, storico completato.
- Priorità per lotto, cliente e fase; ordinamento deterministico.
- Ricerca **opzionale** tra politiche di dispatch e ricerca locale a budget limitato (nessuna garanzia di ottimalità globale).
- Verifica indipendente del piano, diagnostica, carichi, consegne, confronto tra piani, export CSV/HTML/MS Project.
- Snapshot SQLite con revisioni, what-if, zona congelata tra revisioni.
- Ingresso da un solo file **Excel**, da **CSV + binding** o da Scenario **JSON**.
- Assistente AI **facoltativo** nella Control Room (chiave propria, mai nel repository).

## Come portare i propri dati

| Via | Quando | Comando |
| --- | --- | --- |
| Excel unico | iniziare, dati compilati a mano | `planning-factory workbook-template -o modello.xlsx` → compilare → `workbook-plan` |
| CSV + binding | dati estratti da un gestionale | `planning-factory build-scenario binding.json …` |
| Scenario JSON | integrazione da altro software | `planning-factory schedule scenario.json …` |
| Factory ERP | installazioni Factory | connettori in sola lettura in `integrations/` |

Esempi sintetici pronti in `examples/`. Dettagli:
[Excel](docs/planning-workbook.md), [dati canonici](docs/canonical-data.md),
[contratti e API](docs/api.md), [adattatore Factory](docs/factory-adapter.md).

## Control Room (interfaccia web)

```sh
planning-factory web --fixture examples/workshop.json              # 127.0.0.1, senza salvataggio
planning-factory secure-web --fixture examples/workshop.json --local-user \
    --store out/istanza/store.sqlite --audit out/istanza/audit.jsonl   # revisioni, regole, audit
```

Piano macchine, Gantt commesse, carichi, consegne, calendari, programma
giornaliero, parametri, spiegazioni, what-if, correzioni tracciate ai dati
in ingresso. I server ascoltano **solo su loopback**. Guide:
[Control Room](docs/web-dashboard.md), [versione autenticata](docs/secure-control-room.md),
[dati in ingresso](docs/control-room-dati.md).

## API HTTP e libreria Python

```sh
planning-factory serve --port 8772
curl -H "Content-Type: application/json" --data-binary @examples/workshop.json http://127.0.0.1:8772/v1/plan
```

```python
from pathlib import Path
from planning_factory import plan
from planning_factory.adapters.json_io import loads
from planning_factory.engine.verification import verify_plan

scenario = loads(Path("examples/workshop.json").read_text(encoding="utf-8"))
result = plan(scenario)
assert not verify_plan(scenario, result)
```

## Struttura del repository

| Cartella | Contenuto |
| --- | --- |
| `src/planning_factory/` | motore (`engine/`), modello (`domain/`), adattatori (`adapters/`), CLI, server, interfaccia web (`web/`) |
| `examples/` | scenario, CSV canonici e workbook Excel **sintetici** |
| `tests/` | suite di test (764) |
| `tools/` | banchi di prova, kit di stress, KPI di qualità, imballaggio |
| `integrations/` | estrazione/pubblicazione verso Factory ERP (sola lettura per default) |
| `contracts/` | schemi JSON dei contratti |
| `docs/` | documentazione (indice sotto) |

## Verifica

```sh
python -m unittest discover -s tests -t .      # 764 test, circa 2 minuti
python tools/generate_schema.py --check
python tools/fullstack_smoke.py --out out/fullstack-01
python tools/benchmark.py
```

Altri strumenti: `tools/stress_kit.py`, `tools/plan_quality.py`,
`tools/run_free_ci.py` (catena di controlli locale, senza runner ospitati).
Usare una cartella `out/` nuova a ogni esecuzione. I trigger GitHub Actions
sono temporaneamente solo manuali.

## Documentazione

Per iniziare:

- [Guida all'installazione e al primo avvio](docs/guida-installazione.md)
- [Excel come ingresso unico](docs/planning-workbook.md)
- [Dati canonici e connettori di sorgente](docs/canonical-data.md)
- [Contratti JSON, API e CLI](docs/api.md)

Come ragiona il motore:

- [Semantica e requisiti](docs/requirements.md) · [Architettura](docs/architecture.md)
- [Ottimizzazione opzionale](docs/optimization.md) · [Ricerca locale](docs/local-search.md)
- [Attrezzaggi: audit](docs/setup-audit.md) · [pianificazione con attrezzaggi](docs/setup-planning.md)
- [KPI di piano e ripetibilità](docs/plan-quality.md) · [Kit stress sintetico](docs/stress-kit.md)
- [Parametri del motore: catalogo e backlog](docs/parametri-motore.md)

Prodotto e decisioni:

- [Copertura delle funzioni](docs/coverage.md) · [Roadmap](docs/roadmap.md)
- [Decisioni di progetto](docs/decisions/) · [Fonti e limiti delle evidenze](docs/sources.md)
- [Installazione di un pacchetto offline](docs/local-installation.md)
- [Contribuire](CONTRIBUTING.md) · [Registro delle modifiche](CHANGELOG.md)

## Dati e riservatezza

Il repository deve contenere **solo dati sintetici o anonimizzati**. Dati
aziendali, credenziali, backup di database e chiavi (`.env`) non vanno mai
committati: il `.gitignore` esclude `data/private/`, `*.bak`, `*.sqlite`,
`.env`. Ogni installazione tiene i propri dati e la propria istanza
(revisioni, regole, chiavi AI) fuori dal repository.

## Confini della versione

Euristica seriale con verifica dei vincoli, non un ottimizzatore globale. Non
emula le opzioni del planner storico. La pubblicazione verso Factory
(`integrations/factory-export/tools/publish_factory_plan.py`) esiste ma va
eseguita solo su cloni di collaudo. Prima di un uso produttivo vanno concordati
criteri di accettazione e controlli di sicurezza dell'integrazione ERP.
Stato e voci in sviluppo: [roadmap](docs/roadmap.md).
