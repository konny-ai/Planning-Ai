# Ricerca di politiche di dispatch (profilo nuovo, sperimentale)

Questo incremento e' **opzionale**: `schedule` e `plan(scenario)` continuano
a usare la politica `priority` della v0.2 senza variazioni. Non modifica gli input,
non accede a SQL Server e non dichiara l'ottimo globale.

## Politiche provate in sequenza stabile

1. `priority`: comportamento baseline, punteggio configurato nel dominio;
2. `earliest_due`: consegna piu' vicina, poi punteggio baseline;
3. `shortest_processing`: minore durata residua (nesting gia' collassato);
4. `longest_processing`: maggiore durata residua.

Ogni politica cambia **solo l'ordine dei task gia' pronti**. Restano identici
calendari, precedenze, ricerca backward, risorse, nesting, prenotazioni fisse,
capacita' e audit. Le scelte di politica non sostituiscono l'ottimizzazione dei
setup di sequenza o la ricerca combinatoria.

## Criterio di scelta documentato

Un candidato viene ammesso soltanto dopo `verify_plan(scenario, plan) == ()`.
Il criterio lessicografico, minimizzato in quest'ordine, e':

1. numero di operazioni originali non collocate (nesting conta i membri);
2. numero di commesse complete consegnate in ritardo;
3. somma dei secondi di ritardo delle commesse complete;
4. tempo trascorso dall'inizio orizzonte al termine dell'ultima prenotazione interna.

Il confronto e' deterministico: un pareggio mantiene il primo piano. Il piano
baseline viene provato sempre per primo; quindi il selettore non puo' peggiorare
questa metrica rispetto al baseline valido. Cio' **non** significa che non possa
peggiorare metriche non incluse, per esempio il numero di cambi attrezzaggio.
Se un candidato alternativo fallisce, resta fuori dalla selezione e viene
segnalato in `rejected`; un fallimento baseline viene invece propagato.

## Trade-off osservabili: frontiera di Pareto

Il risultato dell'ottimizzazione include ora `pareto_frontier`, l'elenco
deterministico delle politiche **non dominate** fra le candidate valide e
indipendentemente auditate. Una politica domina un'altra solo quando non
peggiora **nessuno** dei quattro indicatori pubblicati e ne migliora almeno
uno. Il caso reale banco A del 20/10/2026 evidenzia il motivo:
con meno commesse in ritardo si possono accumulare molti piu' secondi di
ritardo totale. In tal caso entrambe le politiche restano alternative
non dominate, anziche' nascondere il costo del compromesso.

**Importante:** `winner_policy` continua ad applicare il ranking
lessicografico preesistente, per compatibilita' e senza inventare pesi
industriali. `pareto_frontier` e' una diagnostica; non modifica il piano
standard, non scrive nel database e non prova un ottimo globale.
Le politiche con punteggi identici possono entrambe comparire nella frontiera;
la selezione finale continua a preferire la prima valutata.

## Comandi

```sh
planning-factory schedule examples/workshop.json -o baseline.json
planning-factory optimize examples/workshop.json -o optimized.json --metrics metrics.json
# stdout: winner_policy e pareto_frontier (nessuna selezione automatica del trade-off)
planning-factory verify examples/workshop.json optimized.json
```

La chiave `--metrics` registra politica scelta, piano, metriche di ciascun
candidato valido ed eventuali rifiuti. In Python:
```python
from planning_factory.engine.optimization import optimize
result = optimize(scenario)
plan = result.plan
```

L'API di sviluppo locale espone `POST /v1/optimize` con lo stesso scenario JSON
usato da `/v1/plan`. Il risultato contiene `plan`, `winner_policy`,
`candidates` e `rejected`. Non e' un endpoint ERP ne' pubblico.

## Fuori ambito e passi successivi

L'ottimizzazione dei setup di sequenza richiede una matrice esplicita
risorsa/stato-prodotto/tempi di cambio con relative fixture, per non
inventare semantiche del pianificatore legacy. La pubblicazione sul
database Factory reale e' esclusa. Il confronto golden storico resta una
misura di differenze, non una certificazione di equivalenza.

## Limite di tempo (P16 di #78)

`optimize`, `local-search` e `setup-search` accettano `--time-limit SECONDI` (`time_limit_seconds` nelle funzioni).
- Il **budget di candidati** resta il criterio preferito, perché è deterministico.
- Con il limite di tempo il primo candidato, cioè la politica di base, si valuta sempre. Una volta scaduto il limite non se ne valutano altri, e si restituisce il miglior risultato verificato fino a quel momento.
- Il risultato e il riepilogo CLI riportano `time_limit_reached`.
- **Con il limite di tempo la ripetibilità non è garantita**: lo stesso scenario può fermarsi a candidati diversi su macchine diverse.

## Profilo obiettivi (P17 di #78)

`optimize` e `local-search` accettano `--objectives` (`objectives` nelle funzioni): un ordine lessicografico tra le metriche `unscheduled_operations`, `late_orders`, `total_tardiness_seconds`, `max_tardiness_seconds`, `makespan_seconds` e `moved_operations`.
- **Default**: è l'ordine attuale, quindi non cambia il vincitore.
- **`moved_operations`**: conta le fasi del piano pubblicato che il candidato sposta (P18). Richiede `--published`.
- **Frontiera di Pareto**: resta calcolata sulle quattro metriche storiche.
- **Pesi numerici**: non ci sono, perché vanno fissati dal committente.
- **Non incluse**: numero e tempo di attrezzaggi e bilanciamento dei carichi, che dipendono dallo scheduler a fasi di setup (#53).

## Politiche basate sul margine (P2 di #78)

`optimize --policies all` (in codice `optimize(s, ALL_POLICIES)`) aggiunge tre politiche alle quattro di base. La politica di base `priority` resta sempre la prima valutata, e senza `--policies` la ricerca è invariata.

Tutte usano il **lavoro residuo a valle**: il percorso più lungo di durate e ritardi FS nel grafo già collassato, nesting compresi.
- **`min_slack`**: prima l'unità con margine minore, cioè consegna − (rilascio + lavoro residuo).
- **`critical_ratio`**: prima l'unità con rapporto minore tra tempo alla consegna e lavoro residuo.
- **`atc`**: Apparent Tardiness Cost con sensibilità `k = 2` rispetto alla durata media.

Le unità senza consegna vengono dopo quelle con consegna. Le chiavi sono statiche, calcolate rispetto al rilascio e non all'istante di dispacciamento.
