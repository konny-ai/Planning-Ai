# Ricerca limitata di alternative con attrezzaggi

Il comando `setup-search` prova un numero limitato di selezioni forzate delle alternative macchina dichiarate nello scenario: il primo candidato è lo scenario originale, i successivi fissano per ogni operazione flessibile una sola alternativa (combinazioni enumerate in ordine dichiarato). Le operazioni con `fixed_alternative` restano invariate. Ogni candidato parte dallo scenario originale, esegue `plan_with_setups` con un budget separato di riparazione, poi richiede sia `verify_plan` sia `audit_setups` senza errori. I candidati non validi sono scartati; se nessun candidato è valido il comando termina con errore.

```sh
planning-factory setup-search scenario.json setup-rules.json -o plan.json --scenario-output adjusted.json --budget 8 --repair-budget 12
planning-factory verify adjusted.json plan.json
planning-factory audit-setups adjusted.json plan.json setup-rules.json
```

`--budget` limita i candidati di alternativa (1..32); `--repair-budget` limita le iterazioni interne di ogni candidato (1..32). Il report riporta candidati esaminati, ordine scelto, rifiuti e audit. Lo scenario `adjusted.json` va conservato insieme al piano perché contiene le release derivate e il digest del piano si riferisce a quello scenario. Attenzione: se vince un candidato forzato, nelle operazioni flessibili `adjusted.json` contiene **solo l'alternativa scelta**; le altre sono rimosse. Per ripianificare con tutta la flessibilità ripartire dallo scenario originale.

La ricerca è deterministica e limitata; prova combinazioni delle alternative di più operazioni entro il budget dichiarato. Non esplora tutte le permutazioni possibili, non garantisce l'ottimo globale e può fallire anche se esiste una soluzione non esaminata. Le famiglie e i tempi di cambio devono essere espliciti. Nessun accesso al database Factory.

## Limite di tempo (P16 di #78)

`optimize`, `local-search` e `setup-search` accettano `--time-limit SECONDI` (`time_limit_seconds` nelle funzioni).
- Il **budget di candidati** resta il criterio preferito, perché è deterministico.
- Con il limite di tempo il primo candidato, cioè la politica di base, si valuta sempre. Una volta scaduto il limite non se ne valutano altri, e si restituisce il miglior risultato verificato fino a quel momento.
- Il risultato e il riepilogo CLI riportano `time_limit_reached`.
- **Con il limite di tempo la ripetibilità non è garantita**: lo stesso scenario può fermarsi a candidati diversi su macchine diverse.
