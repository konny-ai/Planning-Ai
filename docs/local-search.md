# Ricerca locale a budget limitato

Nuovo incremento **opt-in** sul motore indipendente: la ricerca locale parte
sempre dal piano standard `plan(scenario)`, già verificato, senza cambiarlo
quando viene eseguito il comando `schedule`.

## Strategia e limiti

Il candidato di partenza è il piano standard. I task sono ordinati per il loro
inizio pianificato, i non collocati seguono in ordine ID. Per una prova si
scambiano due posizioni adiacenti di task flessibili e si rigenera *l'intero*
piano, non si manipolano semplicemente le date della soluzione. Le
dipendenze rimangono soggette al grafo originale e il nesting è indivisibile.

Non vengono scambiate posizioni di task con inizio fissato, completamento
storico o milestone esterna. In ogni iterazione si accetta la **prima**
soluzione strettamente migliore e si riparte; a parità di metrica resta la
soluzione precedente. Ogni candidato è sottoposto al verificatore indipendente
`verify_plan`.

Il ranking è identico a `optimize`: minimizzare in ordine
(1) operazioni non collocate, (2) commesse in ritardo, (3) secondi di ritardo
cumulati, (4) makespan. Non vengono ottimizzati setup di sequenza e costi
energetici o economici.

Il parametro `budget` indica un massimo di **piani alternativi
effettivamente ricalcolati**, da 1 a 64 compresi, escluso il baseline.
Il motore termina anche prima se non trova miglioramenti in un giro.
Il risultato include quanti vicini sono stati valutati, quanti miglioramenti
sono stati accettati, la sequenza di dispatch finale e gli eventuali
rifiuti di candidati. Non viene promesso un ottimo globale né un
miglioramento per ogni scenario.

## Utilizzo

```sh
planning-factory local-search scenario.json -o improved.json --budget 16 --metrics search.json --html gantt.html
planning-factory verify scenario.json improved.json
```

Python:

```python
from planning_factory.engine.local_search import local_search
result = local_search(scenario, budget=16)
assert result.winner.ranking <= result.baseline.ranking
```

Il file `improved.json` contiene il piano verificato; `search.json`
registra baseline, vincitore, numero di valutazioni e ordini testati.
Nessuna lettura o scrittura sul database Factory reale. L'equivalenza
al pianificatore legacy non viene dichiarata.

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
