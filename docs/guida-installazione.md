# Guida all'installazione e al primo avvio

Percorso completo per chi scarica il repository per la prima volta: dal clone
al primo piano verificato, fino alla Control Room nel browser. Ogni passo è
stato eseguito su una copia pulita del repository (Windows 11, Python 3.13);
gli esiti attesi sono riportati sotto i comandi.

Non servono database, server o account: tutto funziona con file locali.

## 0. Windows: il modo più semplice, `avvia.bat`

Nella radice del repository c'è `avvia.bat`. Doppio clic (oppure da
PowerShell/cmd nella cartella) e fa tutto da solo:

| Passo | Cosa fa |
| --- | --- |
| 1 | cerca Python 3.12+ (`py -3.13/-3.12`, poi `python`); se manca lo installa con `winget` |
| 2 | crea l'ambiente `.venv` se non esiste (lo ricrea se usa un Python troppo vecchio) |
| 3 | installa Planning Factory solo se serve (prima volta o se `pyproject.toml` è cambiato) |
| 4 | se una copia di Planning Factory è già aperta sulla stessa porta la chiude; se la porta è di un altro programma **non tocca nulla** e ti avvisa |
| 5 | avvia la Control Room e apre il browser |

Chiudi la finestra nera (o `CTRL+C`) per fermare il server.

```bat
avvia.bat                    esempio incluso, porta 8770
avvia.bat esempio 8800       esempio incluso, porta 8800
avvia.bat mio.xlsx           un tuo workbook Excel o uno scenario .json
avvia.bat mio.xlsx 8800      con un'altra porta
avvia.bat salva              con salvataggio: revisioni, regole e audit nella cartella "istanza"
avvia.bat test               esegue i 749 test (circa 2 minuti)
avvia.bat ferma              chiude il server in esecuzione
```

Note:

- La prima esecuzione scarica le dipendenze: serve internet solo allora.
- Se `winget` installa Python, la finestra potrebbe non vederlo subito: chiudila e rilancia `avvia.bat`.
- Variabili facoltative: `AVVIA_NOBROWSER=1` (non apre il browser) e `AVVIA_NOPAUSE=1`.
- Lo script usa `tools/libera_porta.ps1`: riconosce le copie dalla riga di comando
  (`planning-factory ... --port N`), quindi non chiude mai altri programmi.
- Windows SmartScreen può mostrare un avviso la prima volta: "Ulteriori informazioni" → "Esegui comunque".
- Su Linux e macOS usa i comandi dei paragrafi seguenti.

## 1. Requisiti

| Cosa | Versione | Note |
| --- | --- | --- |
| Python | **3.12 o successivo** | con `venv` e `pip`. Python 3.8–3.11 non è supportato |
| Git | qualsiasi | solo per scaricare il codice |
| Browser | recente | solo per Control Room e per i report HTML |
| Sistema | Windows, Linux, macOS | su Windows `tzdata` si installa da solo |

Opzionali: Tkinter (workbench desktop `planning-factory desktop`, incluso nelle
installazioni Python standard), ODBC Driver 17 per SQL Server (solo per
collegarsi a un Factory ERP reale, non serve per provare il prodotto).

Controllare la versione di Python:

```sh
python --version
```

Se risponde meno di 3.12 ma ne hai una più recente installata, su Windows usa
il launcher: `py -3.13 --version` (elenco: `py -0p`).

## 2. Scaricare il codice

```sh
git clone https://github.com/konny-ai/planning-factory.git
cd planning-factory
```

## 3. Creare l'ambiente e installare

Windows (PowerShell):

```powershell
py -3.13 -m venv .venv          # oppure: python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
```

Linux / macOS:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
```

Esito atteso: `Successfully installed planning-factory-0.2.0 …` (su Windows
anche `tzdata`). Il comando `planning-factory` è ora disponibile nell'ambiente.
L'installazione non ha altre dipendenze: il motore usa solo la libreria
standard di Python.

> **PowerShell blocca l'attivazione?** Esegui una volta
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, oppure salta
> l'attivazione e chiama direttamente `.\.venv\Scripts\planning-factory.exe`.

## 4. Primo piano: scenario di esempio

```sh
mkdir out
planning-factory validate examples/workshop.json
planning-factory schedule examples/workshop.json -o out/plan.json --html out/report.html --csv out/plan.csv
planning-factory verify examples/workshop.json out/plan.json
```

Esiti attesi:

```text
{"valid": true, "scenario_digest": "d904b098…"}
{"assigned": 7, "unscheduled": 0, "scenario_digest": "d904b098…"}
{"valid": true, "errors": []}
```

Apri `out/report.html` nel browser: Gantt filtrabile, carico delle risorse,
indicatori di ritardo. `verify` ricontrolla il piano con un verificatore
indipendente dal motore: `errors` deve essere vuoto.

Altri comandi sullo stesso esempio:

```sh
planning-factory optimize examples/workshop.json -o out/optimized.json --metrics out/optimization.json
planning-factory local-search examples/workshop.json -o out/local.json --budget 16 --metrics out/search.json
planning-factory compare out/plan.json out/optimized.json --html out/comparison.html
```

## 5. Le tre vie per portare i propri dati

Il motore legge un solo formato interno (il **modello canonico**). Si arriva
lì in tre modi, dal più semplice:

### a) Un unico file Excel (consigliato per iniziare)

```sh
planning-factory workbook-template -o modello.xlsx        # modello vuoto da compilare
planning-factory workbook-validate examples/planning-workbook/bending.xlsx --report out/controlli.json
planning-factory workbook-plan examples/planning-workbook/bending.xlsx -o out/piano-wb.json --scenario-out out/scenario-wb.json --report out/evidenze.json --html out/gantt-wb.html
```

Il modello vuoto è **volutamente non calcolabile**: va compilato. Gli esempi
`bending`, `unattended`, `fractional` sono già completi. Formato, unità e
limiti: [planning-workbook.md](planning-workbook.md).

### b) Una cartella di CSV con un file di collegamento (binding)

```sh
planning-factory build-scenario examples/canonical-files/binding.json -o out/scenario.json --manifest out/manifest.json --report out/report.json --now 2026-10-19T00:00:00Z
planning-factory schedule out/scenario.json -o out/plan.json
```

Esito atteso: `"buildable": true, "publishable": true` e 7 attività assegnate.
Il `--now` serve a fissare la data di riferimento: i dati di esempio sono
datati ottobre 2026. Vedi [canonical-data.md](canonical-data.md).

### c) Scenario JSON scritto o generato da un altro sistema

È il formato di `examples/workshop.json`; contratto in [api.md](api.md).

## 5-bis. Collegare un ERP

I connettori verso Factory ERP stanno in `integrations/`. Sono **in sola
lettura** e richiedono un'installazione Factory raggiungibile: non servono per
provare il prodotto. Vedi [factory-adapter.md](factory-adapter.md) e i README
nelle cartelle di `integrations/`. La pubblicazione verso l'ERP va eseguita solo
su cloni di collaudo.

## 6. Control Room (interfaccia web)

```sh
planning-factory web --fixture examples/workshop.json --port 8770
```

Apri <http://127.0.0.1:8770>. Compaiono piano macchine, Gantt commesse, carichi,
consegne, calendari e programma giornaliero. Il server ascolta **solo su
127.0.0.1**; interrompi con `Ctrl+C`. In console del browser puoi vedere due
risposte 404 su `/api/session` e `/api/regeneration`: sono normali in questa
modalità, esistono solo nella versione autenticata.

Si può caricare anche un workbook: `planning-factory web --fixture examples/planning-workbook/unattended.xlsx`.

### Versione con salvataggio (revisioni, regole, audit)

Per un singolo utente sullo stesso PC, senza token:

```sh
mkdir out/istanza
planning-factory secure-web --fixture examples/workshop.json --local-user --store out/istanza/store.sqlite --audit out/istanza/audit.jsonl --port 8771
```

Per più persone usare `--credentials` al posto di `--local-user`
(vedi [secure-control-room.md](secure-control-room.md)). Archivio, audit e
chiavi AI restano nella cartella dell'istanza, mai nel repository.

### Assistente AI (facoltativo)

L'AI non serve al motore. Per attivarla, dalla pagina **Impostazioni AI**:
incolla una chiave Anthropic, oppure usa OpenRouter con un clic, oppure (solo con
`--local-user`) il tuo abbonamento Claude tramite Claude Code. La chiave viene
verificata e scritta in un file `.env` nella cartella dell'istanza; non va
mai nel repository (`.env` è già ignorato da git).

## 7. API HTTP locale

```sh
planning-factory serve --port 8772
```

```sh
curl -H "Content-Type: application/json" --data-binary @examples/workshop.json http://127.0.0.1:8772/v1/plan
```

PowerShell: `Invoke-RestMethod -Uri http://127.0.0.1:8772/v1/plan -Method Post -ContentType application/json -InFile examples/workshop.json`.
La risposta contiene `assignments`, `orders`, `resources`, `unscheduled`.
È un'API di sviluppo su loopback, senza autenticazione. Contratti: [api.md](api.md).

## 8. Libreria Python

```python
from pathlib import Path
from planning_factory import plan
from planning_factory.adapters.json_io import loads
from planning_factory.engine.verification import verify_plan

scenario = loads(Path("examples/workshop.json").read_text(encoding="utf-8"))
result = plan(scenario)
assert not verify_plan(scenario, result)
```

## 9. Verificare l'installazione

```sh
python -m unittest discover -s tests -t . 
python tools/generate_schema.py --check
python tools/fullstack_smoke.py --out out/fullstack-01
```

- La suite conta **764 test** e dura circa 2 minuti: deve terminare con
  `OK (skipped=1)`. Compaiono alcuni `ResourceWarning` di un test di
  anonimizzazione: sono innocui.
- `fullstack_smoke.py` genera dati sintetici, avvia un server effimero e
  ripercorre piano, what-if ed export; deve riportare `"status": "passed"`.
- Strumenti di prova per i tecnici: `tools/stress_kit.py`, `tools/plan_quality.py`,
  `tools/benchmark.py`. Usa sempre una cartella `out/` nuova a ogni esecuzione:
  le evidenze non vengono sovrascritte.

Gli script in `tools/` importano il pacchetto dalla radice: lanciali dalla
radice del repository (se necessario `PYTHONPATH=src:.`, su PowerShell
`$env:PYTHONPATH="src;."`).

## 10. Problemi comuni

| Sintomo | Causa e rimedio |
| --- | --- |
| `requires a different Python: 3.8.x not in '>=3.12'` | Python troppo vecchio: usa `py -3.13 -m venv .venv` o installa Python 3.12+ |
| `planning-factory: command not found` | ambiente non attivo; attivalo o chiama `.venv/bin/planning-factory` (`.venv\Scripts\planning-factory.exe`) |
| `Address already in use` / porta occupata | cambia `--port` |
| Il browser non si apre su `localhost` | usa proprio `http://127.0.0.1:<porta>`: il server non ascolta su altre interfacce |
| `workbook-plan` esce con 1 o 2 | 1 = input non valido (leggi `issues` nel report), 2 = piano con attività non collocate |
| Modello Excel rifiutato | il modello vuoto non è calcolabile: compilare date, dati e copertura dei domini |
| Windows: `ModuleNotFoundError: zoneinfo/tzdata` | `python -m pip install tzdata` (di norma già installato) |
| Connessione SQL Server fallita | serve ODBC Driver 17; con il 18 impostare `TrustServerCertificate`. Non necessario per l'esempio |

## 11. Dove andare dopo

- Che cosa fa il motore e le sue regole: [requirements.md](requirements.md), [architecture.md](architecture.md)
- Tutti i comandi e i contratti: [api.md](api.md)
- Funzioni e limiti: [coverage.md](coverage.md), [roadmap.md](roadmap.md)
- Ottimizzazione, setup, nesting: [optimization.md](optimization.md), [setup-planning.md](setup-planning.md)
- Contribuire: [CONTRIBUTING.md](../CONTRIBUTING.md)
