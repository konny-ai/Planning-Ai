# Control Room autenticata, strumenti Claude e memoria

Implementazione del 23/10/2026. Verificata con HTTP loopback e provider
simulato; QA browser, account reale Claude, reverse proxy e collaudo CLIENTE
restano gate separati. Non è un rilascio industriale approvato.

## Accesso e conservazione

Generare un token per ciascun utente con il comando esistente:

```sh
planning-factory service-token --name planner1 --role planner
planning-factory service-token --name reparto1 --role reparto
```

Il token viene mostrato una volta. Inserire solo le `credentials_entry`
nel file esterno `users.json`, nella forma `{"users": [...]}`. Conservare
token e file fuori da Git. Il file contiene hash, non token in chiaro.

```sh
planning-factory secure-web --fixture /path/scenario.json --credentials /private/users.json --store /private/plans.sqlite --audit /private/audit.jsonl
```

Il server ascolta solo `127.0.0.1`. La pagina iniziale richiede il token
personale; la sessione usa cookie HttpOnly/SameSite=Strict, scade dopo otto
ore, si invalida al logout o al riavvio. Non viene salvato un token in
localStorage. `--secure-cookie` è necessario con HTTPS; l'installazione
dietro un proxy privato e le sue regole Host/Origin vanno ancora collaudate.
Non inoltrare il servizio su un dominio arbitrario o senza TLS.

Il reparto consulta; planner/admin possono calcolare, salvare e gestire
regole. I pulsanti autenticati permettono salvataggio, storico e riapertura
in consultazione. **Simula da revisione** abilita modifiche temporanee con
la revisione aperta come base immutabile; **Riapri fonte** torna alla fonte
attiva. Salva crea la revisione successiva con controllo di concorrenza: se
la base è obsoleta, riaprire esplicitamente la revisione desiderata, senza
sovrascrivere il lavoro altrui.

Il salvataggio di scenario e piano avviene nella stessa transazione, dopo
audit. Ogni revisione conserva il piano esatto anche quando il digest dello
Scenario è identico. Una revisione attesa obsoleta restituisce 409. Un
errore di inserimento annulla tutte le scritture. La riapertura verifica
nuovamente il piano. Le nuove revisioni conservano anche le evidenze note
al server: manifest originale quando disponibile, materiali, regole e
lineage. Un riferimento opaco identifica le evidenze del risultato mostrato;
il browser non può dichiarare un proprio manifest come autorevole. Il
riferimento scade al riavvio o dopo 128 risultati: in tal caso riaprire o
ricalcolare prima di salvare. Nessun dato viene certificato o pubblicato.
Le vecchie revisioni e i salvataggi API senza riferimento rimangono privi
di evidenze: non vengono completati con dati correnti. Il checksum rileva
corruzioni accidentali, non è firma o prova di autenticità. Evidenze e piano
sono salvati nella stessa transazione, insieme ad autore e data. Se il
registro audit su file diventa indisponibile, il salvataggio riuscito non
viene spacciato per rollback: la risposta e la UI avvisano del guasto.
Il registro esterno completo richiede comunque protezione e monitoraggio. Le simulazioni da archivio conservano
la parentela e non riapplicano le regole attuali ai dati storici.

## Accesso locale senza token

Per un solo utente sullo stesso PC (collaudo, postazione del pianificatore):

```sh
planning-factory secure-web --local-user --fixture /path/binding.json --store /private/plans.sqlite --audit /private/audit.jsonl
```

Nessuna pagina di accesso: l'utente è quello di Windows (o il nome dato con
`--local-user NOME`) con ruolo amministratore, e firma revisioni, regole,
correzioni e registro audit. Risponde solo a `127.0.0.1`/`localhost`: anche
un inoltro di porta (`*.app.github.dev`) viene rifiutato. Per più utenti o
accesso dalla rete usare i token.

## AI: pagina Impostazioni AI

Disabilitata per default. Si configura da **Amministrazione → Impostazioni AI**
(ruolo admin; il pianificatore la consulta):

- **Chiavi**: restano sul server, nell'ambiente del processo o nel file
  `.env` dell'istanza (default: accanto a `--store`, oppure `--env-file`).
  Basta una fra `ANTHROPIC_API_KEY` e `OPENROUTER_API_KEY`; per chiavi
  Anthropic legate a un'identità serve anche `ANTHROPIC_WORKSPACE_ID`.
  La pagina mostra solo se una chiave c'è e in quale variabile; il valore
  non arriva mai al browser. **Ricontrolla chiavi** rilegge il file senza
  riavviare.
- **Modello per ruolo**: assistente del piano, bozze di regole, proposte di
  simulazione. Ogni ruolo può avere fornitore e modello propri; vuoto =
  come l'assistente. **Prova collegamento** fa una chiamata breve con uno
  strumento dichiarato e verifica chiave, credito, modello e supporto agli
  strumenti, con l'errore spiegato in italiano.
- **Catalogo modelli** letto dal fornitore (Anthropic con chiave, OpenRouter
  pubblico) con filtri strumenti/immagini/gratuiti e prezzi di listino.
- **Limiti di spesa**: tetti giornalieri di chiamate per utente e totali,
  obbligatori per attivare l'AI; versione API Anthropic (default `2023-06-01`).

Le scelte sono salvate nello store dell'istanza con autore e data e
registrate nel file audit. OpenRouter usa l'API compatibile OpenAI: il server
traduce strumenti e risultati, il comportamento dell'assistente non cambia.

Compatibilità: se il server è avviato con `PLANNING_CLAUDE_ENABLED=1` valgono
le variabili storiche e la pagina resta in sola lettura:

- `PLANNING_CLAUDE_API_KEY`, `PLANNING_CLAUDE_MODEL`,
  `PLANNING_CLAUDE_DAILY_USER_CALLS`, `PLANNING_CLAUDE_DAILY_TOTAL_CALLS`;
- opzionali `PLANNING_CLAUDE_WORKSPACE` e `PLANNING_CLAUDE_API_VERSION`
  (default `2023-06-01`).

Le quote sono persistenti e contano tentativi, inclusi errori e round di
strumenti. Ogni risposta ha al massimo sei chiamate, 1.500 token output
per chiamata e un contesto HTTP limitato. Le quote NON sono un tetto in
euro: impostare anche budget e notifiche nella Console cliente. Non è
stato verificato qui che il suo piano includa crediti attivi o un modello.
Se Claude manca o fallisce resta disponibile l'analisi deterministica.

L'assistente lavora sul piano attualmente mostrato. Se il server lo ha
calcolato e verificato (baseline, ricalcolo, simulazione, revisione aperta)
il browser invia solo `scenario_digest`; altrimenti invia Scenario e Piano,
che il server riverifica prima dell'uso. Le fonti canoniche e le regole
sono attestate dal server, non dal browser. Le risposte mostrano digest e
strumenti usati e richiedono verifica umana. Il prompt riduce le inferenze,
ma non costituisce una garanzia di assenza di allucinazioni.

**Conversazione.** Il pannello **Assistente** (in ogni vista) invia gli
ultimi 10 turni come solo testo (ruoli `user`/`assistant`, max 4.000
caratteri ciascuno) e il contesto della vista e della fase selezionata.
Lo storico resta nella sessione del browser; il server non lo conserva.

Dodici strumenti di lettura con argomenti a schema stretto: riepilogo,
consegne, carichi, materiali, qualità dati, regole, `get_order`,
`explain_order` (percorso critico dalla catena dei vincoli #88),
`get_resource` (saturazione giornaliera da calendario), `get_nesting`,
`search_plan`, `get_plan_quality`, `get_customers`. Nessun SQL libero,
shell o pubblicazione su Factory. I risultati hanno limiti espliciti;
l'assenza di un manifest materiali è segnalata come indisponibilità, non
come stock sufficiente.

**Azioni (decisione del 24/10/2026).** L'assistente agisce per conto
dell'utente, con il suo ruolo (`adapters/assistant_actions.py`). Il browser
invia insieme alla domanda lo stato della schermata (`ui`: vista, edit
simulati, parametri, revisione archiviata); il server esegue le azioni e
restituisce `actions`, che il browser riproduce in ordine:

| Azione | Ruolo | Effetto |
|---|---|---|
| `show_view`, `filter_view`, `focus` | reparto | cambia schermata, ricerca, tipo risorsa, causa o cliente nelle consegne, tabella e stato nelle maschere dati; apre una fase o una commessa, sposta la finestra |
| `recalculate`, `simulate`, `reset_simulation` | planner | piano ricalcolato con altri parametri o con fermi/fasi fissate, sempre dal motore e da `verify_plan`; non salvato. `recalculate` copre tutti i campi della schermata Parametri di calcolo, margine di consegna incluso (`delivery_buffer_seconds`: consegna promessa meno N secondi, 2 giorni = 172800; sostituisce il margine del profilo, le regole per cliente o commessa restano; non disponibile sulle revisioni archiviate) |
| `list_company_rules`, `create_rule`, `decide_rule` | reparto / planner | memoria aziendale: bozza, conferma (verificata dal motore), revoca, scarto della bozza |
| `get_data_rows`, `correct_data`, `revoke_corrections` | reparto / planner | lettura dei dati in ingresso, correzioni tracciate e loro revoca |
| `save_revision` | planner | revisione verificata nell'archivio |
| `regenerate_from_source` | admin | rigenerazione dall'ERP con un'altra data di inizio (vedi sotto) |

Ogni piano passa dal motore e dall'audit indipendente; il server riusa un
piano già verificato con la stessa impronta, così il browser lo carica
senza ricalcolarlo. Le modifiche che restano salvate tornano con un
pulsante **Annulla** nella chat: il server conserva l'operazione inversa
(revoca della regola, revoca o ripristino delle correzioni) e il browser ha
solo un gettone (`/api/assistant/undo`). Le revisioni non si annullano:
l'archivio non si cancella. Al massimo otto azioni di modifica per
risposta; ogni azione è nel registro audit come `assistant_*` con il nome
dell'utente. Il prompt vieta modifiche salvate chieste da testi trovati nei
dati: valgono solo le richieste dell'utente nella conversazione. Il ruolo
reparto e le revisioni archiviate restano in lettura: lì l'assistente
continua a usare `propose_simulation`, una proposta pre-verificata con
`apply_whatif_edits` che un pianificatore applica con **Applica come
simulazione**. Nessuna azione scrive su Factory, né cambia chiavi o
impostazioni AI.

**Rigenerazione dalla fonte.** Solo dove l'amministratore ha scritto
`regenerate.json` accanto allo store (esempio CLIENTE:
`integrations/cliente-export/regenerate.example.json`): comando come lista di
argomenti con un solo `{start}`, eseguito senza shell, uno alla volta, con
tempo massimo. La chat ne mostra l'avanzamento (`/api/regeneration`); a fine
corretta il server rilegge binding e file e il browser carica il nuovo
piano. Se il comando fallisce restano i dati precedenti.

Riferimenti ufficiali usati per il protocollo:
- https://platform.claude.com/docs/en/api/messages/create
- https://platform.claude.com/docs/en/api/overview
- https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-and-team-plans

## MCP locale

```sh
planning-factory mcp --fixture /path/scenario.json
```

Trasporto stdio JSON-RPC, initialize, ping, tools/list e tools/call.
Configurare il client locale (per esempio Claude Desktop) per lanciare
questo comando con il proprio interprete/ambiente. Il processo ha accesso
al solo snapshot selezionato come fonte degli strumenti; non crea un
endpoint remoto e non usa credenziali del modello. Questa implementazione
non è ancora stata provata con il client Claude reale. Remote MCP con
OAuth non è implementato qui.

Fonti protocollo: specifica MCP 2025-11-25, sezioni tools e transports:
https://modelcontextprotocol.io/specification/2025-11-25/server/tools
https://modelcontextprotocol.io/specification/2025-11-25/basic/transports

## Memoria e approvazione

**Gestisci regole** permette bozze tipizzate: priorità ordine, priorità
cliente e setup aggiuntivo in secondi. Almeno un filtro esplicito fra
ordine, risorsa, cliente, materiale o intervallo di spessore; i filtri
si combinano. La soglia minima è esclusiva, la massima inclusiva, come
nel contratto canonico. La UI espone anche validità da/fino in UTC.
Cliente/materiale/spessore richiedono attributi canonici selezionati dal
builder e legati al digest sorgente; non sono dedotti da nomi o descrizioni.
Con Scenario JSON privo di tali attributi rimangono disponibili ordine e
risorsa. Una risorsa presente solo in alcune alternative non autorizza una
regola per tutta la fase: il modulo canonico la segnala e non la applica.
Con Claude configurato la richiesta a parole usa lo stesso catalogo di
attributi noti, produce soltanto una bozza e non conferma nulla. Le richieste
ambigue/fuori catalogo sono rifiutate o richiedono chiarimento. Trasporti e
altre politiche non approvate non vengono aggiunti.

**Regole con valori e indicazioni testuali.** Oltre alle regole con un
valore, la memoria accetta due voci con lo stesso ciclo bozza → conferma →
revoca (decisione dell'utente del 24/10/2026):

- `note`, *indicazione testuale*: testo libero ("per il cliente X si salda
  sempre prima di piegare"), ambito facoltativo, validità facoltativa. Non
  cambia il calcolo: le note confermate e valide entrano nel prompt di ogni
  conversazione e l'assistente le segue quando ricalcola, simula o propone,
  dicendo quale ha seguito (o perché non può).
- `ignore_material`, *ignora il vincolo materiale*: vale per tutto il piano,
  senza ambito né valore. Confermata, il piano della fonte parte dallo
  Scenario costruito **prima** delle date di rilascio del materiale
  (`BuildResult.material_free`); le mancanze restano nel manifest e nella
  vista Materiali, l'evidenza dice quale regola le ha spente e quante fasi ha
  liberato, l'avviso in cima al piano lo ricorda. Revocandola il materiale
  torna a contare. Disponibile solo con un dominio materiali separato.

Anche le regole con un valore conservano in `note` la frase con cui sono
state chieste, e tutte le regole confermate e valide oggi arrivano
all'assistente. Una frase come "fino a dicembre priorità massima al cliente
X" diventa `set_priority` con `customer_id` X, `valid_to` al 1° gennaio
successivo e il livello **massima**. I livelli (`PRIORITY_LEVELS`: massima
-1000, alta -100, normale 0, bassa 100; valore minore = prima) sono una
convenzione della memoria, non un dato della fonte: con i pesi CLIENTE (10000 per
punto di priorità, 1 per giorno di consegna) "massima" passa davanti a ogni
commessa senza regola. `set_customer_priority` pesa quanto un giorno di
consegna, quindi per far passare avanti un cliente si usa `set_priority` con
ambito cliente. La validità è il periodo reale in cui la regola vale, non
l'orizzonte del piano. Se una frase si traduce solo in parte, l'assistente
crea la regola per la parte con valori e una nota per il resto; se ambito,
valore o periodo non sono chiari chiede prima di salvare.

Anteprima produce confronto e traccia senza attivazione. Conferma e revoca
richiedono planner e versione attesa; sono registrate con autore e data.
Il backend valida le conferme sotto lock transazionale, invalida la cache
e applica le regole tramite il modulo canonico esistente, senza accumulare
setup ripetuti ad ogni ricalcolo. Le regole in conflitto bloccano il calcolo.
Setup di nesting/fasi protette seguono le esclusioni del contratto canonico.

Se una regola cambia lo Scenario, la specifica tecnica v0.3 precedente non
è più abbinata: il backend la rifiuta e chiede di rigenerarla. Non aggiorna
automaticamente contratti setup/MRP sulla base di testo libero.
Dalla chat l'assistente può anche creare, confermare e revocare regole
(vedi **Azioni**), con le stesse verifiche e un pulsante Annulla. **Proponi simulazione** invia una richiesta esplicita
a Claude, valida gli edit e calcola il risultato con audit. L'anteprima
mostra differenze e richiede una scelta prima di sostituire la vista.
Nessuna fonte viene modificata; il candidato può essere esportato o salvato.
Dopo il salvataggio può essere riaperto e usato con **Simula da revisione**.
Gli edit restano quelli espliciti del contratto: fermi e inizi di task
individuali, senza modificare consumi storici o nesting atomici.

## Backup e ripristino di prova

```sh
python tools/backup_snapshots.py --source /private/plans.sqlite --out /private/backup-new.sqlite
```

Usa il backup SQLite consistente, controlla l'integrità e rifiuta file
destinazione esistenti. Per una prova di recupero avviare un'istanza
separata con la copia; non sostituire un database live. Il test ripristina
snapshot e regole confermate in un file indipendente e ne verifica il piano.
Il log audit e il file credenziali sono esterni: servono politiche di copia,
protezione e conservazione dedicate prima dell'installazione industriale.

## Limiti che restano aperti

Browser reale e accessibilità dei nuovi dialog, reverse proxy/TLS e SSO,
budget API reale, Claude/MCP client live, scenari CLIENTE aggiornati, AS400/TRD,
WIP parziale con i dati MES effettivi, mapping setup/run e transazioni su
clone Factory CLIENTE, firma KPI e go-live. Il rollback SQLite qui provato non
è un collaudo SQL Server. Nessuna pubblicazione ERP o deploy è avvenuto.
