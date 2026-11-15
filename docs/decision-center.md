# Planning AI Decision Center

**Branch di sviluppo:** `Planning_Ai_DecisionModel` nel repository
`konny-ai/Planning-Ai`. È un'estensione di Planning Factory; **TDE non è
una dipendenza**. Non sovrascrive il piano corrente né scrive sul gestionale.

## Funzionalità end-to-end

1. Il server legge lo Scenario dalla fonte attendibile e calcola il piano
   baseline con il planner esistente.
2. `decision_center.risks` misura attività non assegnate, ordini in ritardo,
   consegne incomplete, ritardo massimo e saturazione delle risorse. Questi
   sono segnali **misurati**, non probabilità inventate.
3. La tabella decisionale sceglie un profilo fra `delivery`, `balanced`
   e `stability`, con regole ordinate e una prima corrispondenza vincente.
   Un Decision Model configurato può suggerire un profilo solo se nessuna
   regola aziendale è applicabile; l'uscita rimane consultiva.
4. Le politiche di dispatch già disponibili nel motore vengono testate con
   un limite di candidati (1–7) e di tempo (1–120 secondi). La baseline
   `priority` viene sempre inclusa. È possibile attivare una ricerca
   locale limitata a 64 tentativi, senza garantie di ottimo globale.
5. Ogni candidato passa per `measure` e `verify_plan`, esistenti. Le
   alternative non verificate non vengono classificate come valide.
6. Un ordinamento **lessicografico deterministico** sceglie il piano
   preferito: niente scoring segreto affidato al modello. Il profilo
   stabilità usa gli spostamenti rispetto a un piano corrente verificato.
7. Per i what-if il server accetta solo `fixed_tasks` e `closures`
   e applica la validazione esistente. Calcola l'impatto sul piano iniziale.
8. Il Decision Center presenta i KPI e la raccomandazione e chiede la
   revisione. `secure-web` registra approvazione/rifiuto, attore e impronta
   dello scenario. **Approvare non applica il piano**: non è stato aggiunto
   un bypass di pubblicazione o un comando Factory.

## Interfaccia

Aprire nella Control Room **Strumenti → Decision Model** (URL
`/decision-model`), con quattro sezioni:

- **Modelli AI**: provider, endpoint locale, soglia, consenso, benchmark.
- **Regole e politiche**: profilo, obiettivi, politiche candidate, budget,
  tabella di decisione ordinata e modello distinto per strategia, rischio
  e spiegazione.
- **Confronto piani**: simulazione, indicatori, rischi, alternative
  verificate, delta what-if; si possono inserire fermi macchina, start
  fissi di task, o un JSON strutturato per più modifiche.
- **Revisioni**: registro persistente per l'installazione autenticata,
  con approvazione/rifiuto manuale. In `web` demo non viene abilitato
  l'endpoint di approvazione persistente.

I form di configurazione del provider e delle regole richiedono il ruolo
amministratore in `secure-web`. Per la generazione e revisione delle
proposte sono necessari i ruoli previsti dal backend `secure-web`.

## Provider e assegnazioni

La configurazione del provider resta nel file preesistente
`~/.planning-factory/decision-model.json` (o
`PLANNING_DECISION_CONFIG`); le nuove regole vanno in
`~/.planning-factory/decision-rules.json` (o
`PLANNING_DECISION_RULES`). Le chiavi API **non sono salvate nei JSON**.

Sono disponibili la baseline deterministica, Ollama/OpenAI-compatible
locale, System One locale, OpenRouter Chat, OpenAI Chat, Anthropic Chat,
OpenRouter Decisions e OpenAI Decisions. Non usare endpoint Decisions
sulla API Chat. I protocolli nativi richiedono collaudi effettivi con
credenziali e documentazione della versione attiva.

Per `strategy`, `risk`, `explanation` si configura `model_routes` con
`provider` (`inherit`, `disabled`, o uno dei provider supportati) e
`model` (identificativo specifico, se diverso). Gli output dei modelli
sono etichette strutturate e relative spiegazioni: **il ruolo explanation
non è ancora un generatore di relazioni libere**; gli indicatori e i
confronti verificati costituiscono la spiegazione deterministica.

Tutti i ruoli sono disattivi finché `use_ai_advice=false` (impostazione
iniziale). L'analisi richiede `ai_approved=true` per quella richiesta.
Inoltre, i provider remoti richiedono il flag globale
`external_approved`, le rispettive credenziali d'ambiente e la policy
privacy aziendale. Le domande inserite manualmente nella UI precedente
non sono anonimizzate automaticamente. Non usare dati sensibili durante
il pilot. Gli abbonamenti consumer ChatGPT/Claude non includono
automaticamente le rispettive API: l'integrazione CLI non è implementata.

## Tabella decisionale

Schema `decision-rules.json` versionato 1, esemplificato dalle
impostazioni di default. Le regole hanno forma:

```json
{
  "name": "consegne_a_rischio",
  "field": "late_orders",
  "operator": "gte",
  "value": 1,
  "profile": "delivery"
}
```

I soli campi sono `late_orders`, `incomplete_orders`,
`unscheduled_units`, `max_delay_hours`,
`max_resource_utilization_pct`. Operatori `gte`, `lte`, `eq`.
Nessun `eval`, script o query è accettato. Se manca una misura il valore
non diventa zero per convenienza. Le regole hanno precedenza sul suggerimento
AI. La prima politica candidata deve sempre essere `priority`.

### Significato dei profili

- **delivery:** priorità lessicografica a lavori non schedulati,
  consegne in ritardo, ritardo totale e ritardo massimo.
- **balanced:** lavori non schedulati, ritardi e makespan.
- **stability:** lavori non schedulati e ritardi, quindi numero di
  operazioni spostate rispetto al piano corrente verificato, se
  disponibile, e criteri successivi. Non inventa un piano pubblicato.

Il motore fa *ranking* di piani fattibili, non valutazione costi diretti
di acquisto materiale o tariffa macchina senza una fonte economica
dedicata. La riduzione dei setup non è un obiettivo misurato in questi
tre profili: per setup espliciti usare le capacità già presenti
`setup-plan` e `setup-search` e introdurre un KPI verificato prima
di aggiungerlo al ranking del Decision Center.

## Valutazione storica del rischio

Opzionale: impostare `PLANNING_DECISION_HISTORY` a un file JSON
locale con questo formato (dati aggregati, non identificativi):

```json
{
  "schema_version": 1,
  "rows": [
    {
      "late_orders": 0,
      "unscheduled_units": 0,
      "max_resource_utilization_pct": 75,
      "late_actual": false
    }
  ]
}
```

Ogni riga rappresenta un piano storico con l'esito effettivamente
misurato; ordinare le righe cronologicamente e non utilizzare
informazioni future nella previsione. Il sistema richiede almeno 50
osservazioni totali, una soglia minima configurabile (>=20), almeno
10 esempi nella categoria, e non meno di 10 esempi utilizzabili in
validazione temporale sull'ultimo 20%. Calcola un Brier score su dati
tenuti fuori dall'apprendimento. In caso di supporto/validazione
insufficiente o Brier >0.25 non espone probabilità. Quando disponibile
la stima è etichettata `provisional`, *non* garantita né formalmente
calibrata. Occorrono verifica di drift, bias e calibrazione su dati
reali per uso decisionale produttivo.

Il modello non invia lo storico a provider esterni; dimensione max
1 MiB e 5000 righe. Se i dati storici non esistono la UI dichiara
esplicitamente che non può calcolare una probabilità.

## HTTP API

Route del dashboard loopback:

- `GET /api/decision-model/config` e `POST /api/decision-model/config`:
  provider e consenso.
- `GET /api/decision-center/rules` e `POST /api/decision-center/rules`:
  tabella decisionale.
- `POST /api/decision-center/recommend` con
  `{"ai_approved":false}`, oppure aggiungendo
  `"edits":{"fixed_tasks":[],"closures":[]}` e `"settings":{}`.
- Solo `secure-web`: `GET /api/decision-center/reviews` e
  `POST /api/decision-center/review` con
  `decision_id`, `expected_digest`, `action` (approve/reject) e
  `note`. Non è possibile inviare dal browser un piano arbitrario da
  approvare: il server conserva solo rapporti da lui generati.

Le richieste what-if sono validate con i vincoli esistenti, non
possono cambiare l'ERP o il workbook reale. Le revisioni sono
identificate da hash del rapporto, impronta dello Scenario e della
policy; le decisioni non possono essere riesaminate senza una nuova
simulazione. La cronologia revisione è salvata in
`decision-reviews.json` accanto allo store autenticato.

## Sicurezza e limite di rilascio

**Non è una funzionalità pronta per il rilascio in produzione.**
Il servizio demo è locale e non autenticato, quindi non deve essere
esposto al pubblico; il percorso `secure-web` impone sessioni, ruoli
e audit. Nessuna funzione Decision Center firma o pubblica in Factory.
Il vincolo di tempo controlla *l'avvio* dei candidati, non interrompe
il singolo scheduler in esecuzione.

Prima del merge sono richiesti: CI completa, verifica browser reale,
test di ruoli/CSRF/SSRF, contratti API live e privacy, benchmark sui
dati anonimizzati, limiti economici per l'uso dei provider e accettazione
operativa. Gli esiti dei test vanno allegati alla pull request; una
PR draft non costituisce collaudo.

## Test previsti

```sh
python -m pip install -e .
python -m unittest -v tests.test_decision_model tests.test_decision_center tests.test_decision_center_http
node --check src/planning_factory/web/decision-model.js
python -m unittest discover -s tests -t .
```

La CI GitHub del branch esegue queste verifiche se GitHub Actions è
disponibile, ma non sostituisce il pilot industriale con dati reali.
