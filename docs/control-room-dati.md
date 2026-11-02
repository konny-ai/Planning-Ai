# Control Room: dati in ingresso, correzioni e fonti

Implementazione del 24/10/2026 (`secure-web`, con token o `--local-user`).
Richiede un binding canonico con sorgenti file (`file`, `canonical-json`):
con uno Scenario JSON semplice le maschere dati non compaiono.

## Maschere dei dati in ingresso

Menu **Dati in ingresso**, una voce per dominio collegato
nell'installazione, con una scheda per ogni tabella canonica:

| Voce | Tabelle |
| --- | --- |
| Commesse | `order` |
| Fasi e cicli | `operation`, `operation_demand`, `operation_dependency` |
| Macchine e risorse | `resource` |
| Calendari | turni, festività, giorni speciali, turni speciali, chiusure |
| Programmi di nesting | `nest`, `nest_member` |
| Materiali | `material_supply`, `material_need`, `allocation_rank` |
| Consuntivi | `operation_progress`, `operation_state` |
| Conto lavoro | `operation_external_end` |
| Profili e regole | `profile`, `rule` |

Le righe sono quelle fuse dal builder dopo le correzioni: ricerca,
filtro per stato (fonte, modificato, aggiunto, escluso), ordinamento e
paginazione avvengono sul server (CLIENTE: 156.728 risorse ammesse per fase).
Le colonne che la fonte non valorizza mai sono nascoste e si mostrano con un
interruttore. Durate in ore e minuti, istanti in ora locale (UTC nel tooltip),
enumerati con etichette italiane. Selezionando una riga, il pannello di destra
mostra tutti i campi, la provenienza e lo storico.

## Correzioni

Una modifica non tocca la sorgente: diventa una **correzione** nello store
dell'istanza (`data_corrections`), con autore, istante, motivo obbligatorio e
valori precedenti. Azioni: modifica di campi (non della chiave), aggiunta di
una riga, esclusione dal calcolo. Le richieste singole e i lotti seguono la
stessa validazione: un lotto viene provato come insieme, registrato in una
transazione e ricostruisce il canonico una sola volta. L'assistente espone
anche `exclude_orders_before` per escludere insieme le commesse prima di una
data (usa la data della commessa o, se assente, la prima data delle fasi) e le
righe canoniche collegate tramite riferimenti a commessa/fase; i
nidi condivisi restano se contengono altre fasi. Il piano viene ricalcolato
una sola volta per l'azione e l'intero lotto si annulla con un solo comando.
Ogni correzione:

1. è validata campo per campo con le regole del modello canonico;
2. viene provata ricostruendo lo scenario (CLIENTE: circa 15 secondi per richiesta;
   un lotto esegue una sola ricostruzione); se i dati non sarebbero più
   calcolabili è rifiutata e non salvata;
3. si applica dopo la fusione delle sorgenti e la traduzione dei codici, a
   ogni lettura successiva: resta valida dopo una nuova estrazione e dopo un
   riavvio;
4. si annulla (**Ripristina originale**, oppure dal **Registro correzioni**)
   senza cancellare nulla: la riga resta nel registro come annullata.

Una correzione il cui record non esiste più nella fonte genera l'avviso
`correction_orphan`; un'aggiunta poi arrivata dalla fonte genera
`correction_insert_exists` e i valori corretti prevalgono. Il manifest
riporta numero e impronta delle correzioni attive, quindi il digest dello
scenario cambia e le revisioni salvate restano tracciabili. Dopo una
correzione la UI avvisa che il piano mostrato va ricalcolato.

## Fonti dati

**Amministrazione → Fonti dati** mostra installazione, orizzonte, ultima
lettura dei file e stato di pubblicabilità; per ogni sorgente i file (percorso,
dimensione, data di modifica, esistenza) e i record letti per dominio; per ogni
dominio sorgenti, politiche (residuo, ordine materiali, stati nesting),
impronta; la diagnostica raggruppata per codice con esempi. **Rileggi i file**
ricarica i dati dopo una nuova estrazione (per CLIENTE
`integrations\cliente-export\run_cliente_locale.ps1`) senza riavviare, riapplicando
le correzioni. Le sorgenti restano in sola lettura.

## API

Tutte same-origin, JSON, con sessione (o accesso locale):

- `GET /api/data/catalog`, `GET /api/data/rows?entity=&q=&status=&sort=&dir=&offset=&limit=&f.<campo>=`
- `GET /api/data/ids?ref=resource&q=`, `GET /api/data/history?entity=&key=[...]`
- `POST /api/data/corrections` `{entity, action: update|insert|exclude, key, values, reason}` (planner)
- `POST /api/data/exclude-orders-before` `{before, reason}` (planner; lotto annullabile, ricostruzione e ricalcolo singoli)
- `POST /api/data/corrections/revoke` `{ids, reason}` (planner)
- `GET /api/sources`, `POST /api/sources/reload` (planner)
- `GET /api/settings/ai`, `GET /api/settings/ai/models?provider=`, `POST /api/settings/ai`,
  `POST /api/settings/ai/test`, `POST /api/settings/ai/reload` (admin per le scritture)

Gli errori di validazione tornano con `errors` in italiano.
