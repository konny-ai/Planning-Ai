# Simulazioni di reparto dalla Control Room

Incremento UI+backend **opt-in** sopra il dashboard file-only. Il motore
rimane l'autorità sulle prenotazioni; la UI non modifica direttamente
i segmenti del Gantt e non salva sul database.

## Due azioni sul piano macchine

1. **Blocca qui / Sposta** una lavorazione già collocata, purché sia
   un task `job:` non storico, non esterno e non di nesting condiviso.
   Il browser passa `task_id`, `start` con offset UTC e indice di
   una **alternativa già dichiarata**; l'API rifiuta indici inesistenti,
   task sconosciuti, input malformati e inizi fuori orizzonte.
2. **Simula fermo risorsa** dalla vista Calendari: la UI richiede
   risorsa, inizio/fine UTC e motivo non vuoto. Il backend crea un
   calendario **dedicato a quella risorsa**, copiando i turni e
   aggiungendo una `closure`. Eventuali altre risorse con lo stesso
   calendario originale **non** vengono bloccate.

Le simulazioni si possono accumulare fino a 40 task fissati e
40 intervalli di fermo, ma ogni ricalcolo ricomincia dallo
Scenario sorgente immutabile e rigioca tutti gli edit espliciti.
Un errore di input o di planner viene mostrato all'utente e la UI
ripristina gli edit precedenti: non lascia una simulazione
parzialmente applicata. **Annulla simulazioni** ripristina la
pianificazione derivata soltanto dal file iniziale.

Il backend `POST /api/simulate` riceve:

```json
{
  "settings": {"mode": "auto"},
  "edits": {
    "fixed_tasks": [
      {"task_id": "job:O123", "start": "2026-10-22T08:00:00Z", "alternative": 0}
    ],
    "closures": [
      {
        "resource_id": "LASER_1",
        "start": "2026-10-22T12:00:00Z",
        "end": "2026-10-22T16:00:00Z",
        "reason": "Manutenzione simulata"
      }
    ]
  }
}
```

Questi identificativi sono solo esempi; **non** implicano che
il dataset banco A contenga un laser o uno scenario approvato.

## Gate e comportamenti non supportati

- Tutti gli edit devono essere entro l'orizzonte e coerenti col contratto
  di dominio. Gli inizi fissi conflittuali fanno fallire il
  calcolo/validazione; un fermo può rendere il piano **parziale**,
  segnalato da `plan.unscheduled`.
- `verify_plan(scenario, plan)` è sempre eseguito sul nuovo scenario,
  prima di restituire il piano al browser.
- Non sono supportati drag/drop che sfuggono ai vincoli, frazioni WIP
  storiche, fisici setup già eseguiti, editing di nesting condivisi,
  o pubblicazione ERP. I booking già esistenti nella fonte non vengono
  arbitrariamente sbloccati.
- Gli orari nelle forme UI sono **UTC dichiarati**, non "ora locale"
  dedotta; la UX multi-fuso è un lavoro successivo.
- L'app resta browser preview su **127.0.0.1** in Codespaces Private,
  senza autenticazione di produzione. Nessun SQL/ODBC/RPC esterno.
- I test sono predisposti in `tests/test_dashboard_simulation.py`,
  **non eseguiti** per indicazione precedente dell'utente.

Il percorso di servizio aziendale resta quello con autenticazione,
revisione scenari e permessi distinti; questa UI di simulazione
non concede alcuna autorizzazione a salvare o pubblicare su Factory.
