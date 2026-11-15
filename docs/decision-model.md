# Decision Model consultivi in Planning Factory (prima integrazione)

Questo modulo è indipendente da Technical Data Engine. Il planner resta l'unica
autorità per la fattibilità; il verificatore indipendente resta obbligatorio.
Le decisioni AI sono **shadow**, non cambiano le priorità, il piano, le regole
di dispatch o dati ERP.

## Uso

1. Avviare `planning-factory web --fixture examples/workshop.json`.
2. Aprire **Strumenti → Decision Model** (oppure `/decision-model`).
3. Selezionare Baseline locale (deterministica), Ollama / endpoint
   OpenAI-compatible, OpenRouter Chat, OpenAI API o Anthropic API.
4. Per provider esterni configurare sul processo server la variabile
   `OPENROUTER_API_KEY`, `OPENAI_API_KEY` o `ANTHROPIC_API_KEY`.
   Per locale usare `PLANNING_DECISION_LOCAL_KEY` solo se richiesto dal server.
5. Abilitare il modulo; per provider esterni confermare il consenso globale.
   Ogni esecuzione richiede inoltre conferma esplicita nel pannello.
6. Analizzare il piano o lanciare il benchmark sintetico offline.

Configurazione persistita in
`~/.planning-factory/decision-model.json` (o `PLANNING_DECISION_CONFIG`),
fuori dalla cartella Git. Non vengono salvate chiavi. La UI HTTP qui descritta
è il *workbench demo loopback*, non un servizio multiutente autenticato.
Non esporre la porta pubblicamente. La UI non è ancora integrata in
`secure-web`: servono policy ruoli e audit specifici prima dell'uso cliente.

## Contratto decisionale

L'ingresso al modello contiene soltanto il numero di attività schedulate,
non schedulate e consegne, insieme a una domanda opzionale digitata
dall'utente (non immettere informazioni riservate). Nessuna identificazione
di ordine, cliente o macchina viene inviata automaticamente.

L'uscita, se valida, contiene `label`, `confidence`, `reason`, e sempre
`status=shadow`, `requires_review=true`, `authorizes_execution=false`.
Le etichette sono `DELIVERY_RISK`, `CAPACITY_REVIEW`, `PLAN_REVIEW`,
`NO_ALERT` e `UNCERTAIN`. Una confidenza sotto soglia produce
`UNCERTAIN`. Input mancanti, credenziali assenti, output non valido o
errore di rete non consentono azioni.

La baseline è deterministica e utile per il test del flusso, **non** un
decision model addestrato. I provider OpenRouter/OpenAI/Anthropic/Ollama
usano modelli chat JSON, non un endpoint specializzato nativo Decisions.
ScaleDown Classify e altri protocolli specializzati richiedono adapter
dedicati e test di contratto prima di essere abilitati.

Gli abbonamenti ChatGPT e Claude non equivalgono ad accesso API a consumo.
Codex CLI e Claude Code richiedono integrazioni specifiche, autorizzazioni
e verifica dei termini: non sono provider implementati in questa versione.

## Sicurezza

- Porta del workbench vincolata a loopback e protezioni same-origin esistenti.
- Nessuna navigazione HTTP per gli endpoint locali: solo loopback e percorso /v1.
- Nessun redirect HTTP seguito nelle chiamate ai provider.
- Timeout di 12 secondi e risposta limitata a 32 KiB.
- Per provider esterni: consenso globale più approvazione per analisi.
- La domanda manuale è trasmessa al provider; non è anonimizzata automaticamente.
- Non esistono scritture ERP né comando di modifica del piano.
- Le chiamate esterne potrebbero comportare costi; limiti, data residency e
  condizioni contrattuali vanno verificati prima dell'attivazione.
- L'interfaccia locale non offre ancora una revisione privacy persistente per
  singola pratica: non utilizzare dati personali, ordini reali o segreti.

## Verifica

```sh
python -m unittest tests.test_decision_model
python -m unittest discover -s tests -t .
node --check src/planning_factory/web/decision-model.js
```

Il benchmark sintetico offline include quattro casi e non compie chiamate
ai provider; non è un'accuratezza statistica su dati produttivi.

## Roadmap successiva

- Dataset sintetici e anonimizzati realistici, confusion matrix, p50/p95,
  astensioni, costo provider e confronto modelli in shadow.
- Adapter dedicati e versionati per endpoint Decisions specializzati
  (ScaleDown e/o altri, solo dopo verifica API ufficiale).
- Integrazione autenticata in secure-web con approvazioni, audit e policy
  multiutente, prima di esporre dati reali.
- Decision model su scenari what-if comparabili per KPI, senza bypassare il
  verificatore del piano.
- Solo dopo collaudi su clone e approvazione operativa: suggerimenti di
  configurazione sottoposti a revisione umana, mai scritture automatiche.
