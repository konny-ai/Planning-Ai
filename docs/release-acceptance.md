# Pianificazione Factory — Gate di accettazione (issue #3)

**Stato al 21/10/2026: NON APPROVATO PER IL DATABASE PRODUTTIVO.**
L'esistenza di una CI verde o di un clone SQL verificato non equivale
a un'accettazione del piano da parte del responsabile di produzione.

## Verifiche obbligatorie

Il comando **di sola lettura** `python tools/release_readiness.py
release/acceptance-evidence.template.json` restituisce per impostazione
predefinita `blocked` con exit code 2. Usare una **copia** del template
per registrare evidenze verificabili con commit SHA e URL di prova.
Il risultato massimo consentito è `ready_for_manual_review`:
il comando **non** stabilisce l'autenticità delle evidenze e
**non** permette, invoca o autorizza alcuna pubblicazione SQL.

Ogni elemento della lista `evidence` deve avere
`name`, `result: "passed"`, `url: https://...`,
`engine_sha` coerente e `recorded_at` con fuso esplicito.
Criteri di accettazione (evidenza per ciascuno):

1. CI Python 3.12 e 3.13, Windows e SQL Server, sulla **stessa revisione**.
2. Ripristino clone banco A, pianificazione, transazione SQL su clone,
   rilettura identica, verifica dei trigger di dominio.
3. Test di **rollback** su transazione fallita e di **conflitto** fra
   estrazione e scrittura; non limitarsi al caso di successo.
4. Scenari rappresentativi **oltre banco A**, inclusi setup multipli
   macchine/operatori/attrezzi, nesting multicommesse, materiali in
   ritardo, precedenze, conto lavoro e fasi già iniziate/fissate.
5. Pegging materiali, compatibilità tecnologica con unità/revisioni
   e verifica indipendente delle assegnazioni.
6. KPI firmati dalla fabbrica: consegne, ritardo ponderato, saturazione,
   numero/durata setup, stabilità piano, tempi calcolo; soglie concrete
   da definire con un referente autorizzato. Nessuna replica del legacy.
7. Accettazione flusso produttivo dagli operatori/resp. pianificazione.
8. Security review, backup e rollback, privilegio minimo, monitoraggio,
   riconciliazione delle risorse e autorizzazione formale go-live.
   Nessun accesso di scrittura al database reale viene introdotto qui.

I nomi obbligatori si trovano in `REQUIRED_EVIDENCE` del verificatore.
Il risultato non autentica URL, firme o commit: un referente deve
confrontarli con GitHub Actions e con il sistema del cliente.

## Prove già ottenute

- Collaudo banco A SQL Server isolato:
  346 attività schedulate, 0 non schedulate, 721 blocchi di pianificazione
  riletti senza differenze. Verifica della **baseline**, non di tutte le
  novità v0.3.
- [CI precedente su main](https://github.com/konny-ai/planning-factory/actions/runs/39564693566):
  test automatici del motore, Python Linux/Windows e SQL di prova.
- Prove di setup e distinta/pegging e tecnologia su dati sintetici
  nel repository; non esiste ancora accettazione di tutte le condizioni
  industriali v0.3 e quindi le evidenze restano incomplete.

## Confini inviolabili

- Nessuna esecuzione su un DB di produzione in assenza di autorizzazione
  formale ulteriore e di un operatore responsabile.
- Non usare un piano di stage tecnico `setup_stage_scheduler` come
  payload per `publish_factory_plan.py`: il mapping di pubblicazione
  dei due stage è **da definire**.
- Mai attivare un fallback a risorse virtuali per mascherare fabbisogni
  obbligatori senza decisione industriale.
- Nessun commit con credenziali, backup sensibili, ID cliente o dati
  non anonimizzati nel repository del motore.

## Decisioni residue per l'accettazione finale

Le regole Q9 e Q10 di [ADR 003](decisions/003-phase-setup-capacity-state.md)
restano aperte; inoltre servono soglie KPI, responsabilità della
firma, finestra per il cambio sistema, prova su snapshot cliente aggiornato
e scelta di rollback. Queste decisioni richiedono intervento umano;
l'automazione non le approva al suo posto.
