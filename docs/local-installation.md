# Pacchetto tecnico locale CLIENTE — 23 ottobre 2026

Questo pacchetto contiene un wheel Python e un esempio interamente sintetico.
Non contiene dati, token, chiavi Claude o database cliente. La versione del
pacchetto rimane 0.2.0: la preview setup/materiali/WIP v0.3 è sperimentale.
Non rappresenta un rilascio industriale approvato.

## Requisiti e installazione

Python 3.12 o successivo. Estrarre il pacchetto in una directory privata.
Verificare i file rispetto a `SHA256SUMS.json`, che rileva alterazioni ma non
costituisce firma del produttore. Creare un ambiente dedicato:

```sh
python -m venv .venv
# Linux/macOS
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install --no-index --find-links wheels planning-factory
python init_local_instance.py --out instance --fixture scenario.json --stage-spec stage-spec.json
```

Su Windows serve inoltre `tzdata`: il wheel dichiara questa dipendenza,
ma il pacchetto non la include. Installarla da una fonte Python autorizzata
prima del comando offline, oppure predisporre un wheelhouse completo per
Windows. L'esecuzione Windows non è attestata dal collaudo Linux.

L'inizializzatore rifiuta una directory esistente, verifica i dati e crea
credenziali con hash, database e audit. Mostra una sola volta il token del
planner: conservarlo privatamente. Gli esempi sono una fonte Scenario JSON;
un binding con sorgenti esterne va avviato direttamente dal checkout con
`secure-web`, mantenendo i suoi file e percorsi originali.

```sh
planning-factory secure-web --fixture instance/scenario.json --credentials instance/users.json --store instance/plans.sqlite --audit instance/audit.jsonl --v03-spec instance/stage-spec.json
```

Aprire http://127.0.0.1:8766, accedere con il token personale, calcolare,
consultare materiali e fasi v0.3, salvare e riaprire revisioni. Usare
**Simula da revisione** per proseguire da un archivio. Le regole correnti
si applicano alla fonte attiva, non retroattivamente alle revisioni.
Le evidenze conservate non certificano la correttezza dei dati CLIENTE.

Il server è loopback: questo pacchetto non configura TLS, proxy, SSO,
autostart o un accesso pubblico. Su Windows impostare ACL private per
la cartella `instance`: `chmod` non sostituisce le ACL Windows.

## File di esempio sostituibili

La cartella `tables` contiene otto CSV interamente sintetici: risorse,
fasi, fabbisogni setup/run, stati macchina, sequenza, priorità, forniture e
fabbisogni materiali. Quantità come stringhe decimali, durate in secondi,
occupazione in basis point (10000 = 100%), timestamp con offset esplicito.
Non dedurre stato macchina, residui o quantità dai soli contatori.

Dopo avere riconciliato uno Scenario reale e i CSV corrispondenti:

```sh
python build_v03_spec_from_csv.py --scenario scenario.json --tables tables --out imported-spec.json --stock-accounting net
```

Usare `net` solo per disponibilità già nette di impegni esterni. Per stock
lordo usare `gross` e fornire `commitments.csv`, anche se vuoto con intestazione,
soltanto quando è attestata l'assenza di impegni. Non cambiare convenzione
per aggirare una carenza. L'import crea anche `imported-spec.sources.json`
con hash di provenienza; conservarlo con i file originali. La specifica è
abbinata al digest dello Scenario: cambiare dati richiede rigenerazione.
Le regole che modificano il setup v0.2 non vengono migrate automaticamente
al contratto setup/run v0.3. L'anteprima rifiuta un abbinamento incoerente;
serve una riconciliazione tecnica esplicita, non un cambio del digest a mano.

## Claude e MCP

Claude è disabilitato per default. Per abilitarlo occorrono chiave e modello
API, quote giornaliere per utente e globali, budget nella Console cliente.
Variabili: `PLANNING_CLAUDE_ENABLED`, `PLANNING_CLAUDE_API_KEY`,
`PLANNING_CLAUDE_MODEL`, `PLANNING_CLAUDE_DAILY_USER_CALLS`,
`PLANNING_CLAUDE_DAILY_TOTAL_CALLS`; workspace/versione API sono opzionali.
Non inserire segreti nei file di questo pacchetto. Il collegamento API e
l'eventuale disponibilità di crediti del piano devono essere verificati
sull'account effettivo. Nessuna chiamata a pagamento nel collaudo incluso.

MCP locale: `planning-factory mcp --fixture instance/scenario.json`.
Il client può avviare il comando tramite il Python dell'ambiente dedicato;
non è un connettore remoto con OAuth e non è ancora attestato con Claude.

## Backup, aggiornamento e recupero

```sh
python backup_snapshots.py --source instance/plans.sqlite --out backup-new.sqlite
```

Il backup consistente copia snapshot, evidenze e memoria regole. Conservare
separatamente, con accesso ristretto, `users.json`, `audit.jsonl`, scenario,
specifica tecnica e configurazione del servizio. Per un backup completo
coerente arrestare prima l'istanza e copiare la directory. La chiave Claude
resta nell'ambiente privato e va gestita separatamente.

Per aggiornare: arrestare il servizio, conservare la cartella completa e il
vecchio wheel, installare il nuovo wheel in un NUOVO ambiente, provare su
una COPIA della cartella e verificare accesso, calcolo, storico e backup.
Non cambiare un database live. Per tornare alla versione precedente usare
l'ambiente precedente e la copia dei dati corrispondente: una migrazione
non garantisce compatibilità retroattiva del database. Collaudare il
recupero su copia prima dell'avviamento cliente.

## Evidenze e limiti

`verification.json` riporta la piattaforma realmente provata: installazione
wheel in venv pulito, CLI, HTTP autenticato, salvataggio/conflitto/storico,
preview setup e MCP come subprocess locale. Le prove HTTP non sono QA
browser. Servono ancora browser reale, account Claude, dati CLIENTE aggiornati,
SQL Server su clone, mapping setup/run, KPI e accettazione industriale.
