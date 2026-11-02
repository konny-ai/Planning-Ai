# Pianificazione con attrezzaggi espliciti (sperimentale)

`setup-plan` e' un comando **opt-in** che parte da uno scenario e da una matrice esplicita di attrezzaggio, genera un piano, misura i gap lavorabili sulle macchine configurate e sposta in avanti le disponibilita' (`release`) dei task che violano il tempo di cambio. Ricalcola il piano e ripete il controllo fino a quando e' fattibile o raggiunge il budget. Il planner standard `schedule` resta invariato.

```sh
planning-factory setup-plan scenario.json setup-rules.json -o setup-plan.json --scenario-output setup-scenario.json --budget 12 --html setup-gantt.html
planning-factory verify setup-scenario.json setup-plan.json
planning-factory audit-setups setup-scenario.json setup-plan.json setup-rules.json
```

Il file `setup-scenario.json` e' parte del risultato: contiene le nuove disponibilita' derivate dai vincoli espliciti, e il piano e' digestato rispetto a questo scenario modificato. Non verificare il piano contro lo scenario originale. Il JSON di audit stampato dal comando registra iterazioni, budget e risultato del controllo attrezzaggi.

Limiti: budget da 1 a 32 riparazioni; matrice obbligatoria; solo macchine a capacita' 1; anche per le attivita' interrompibili con
frammenti **intercalati** la verifica misura ogni transizione nella sequenza
effettiva. Il processo di riparazione ancora il rilascio del task alla fine
del frammento macchina immediatamente precedente, non alla fine globale
di un altro task interrompibile. La riparazione sposta l'**intero task**
(perche' il contratto v0.2 non definisce release diverse per frammento),
quindi puo' non trovare una soluzione disponibile; in quel caso fallisce
con diagnostica senza dichiarare valido un piano non auditato. Se non e' possibile rispettare i vincoli entro il budget, la funzione fallisce con diagnostica invece di restituire un piano che dichiara fattibile. Questo e' un algoritmo di riparazione euristica a rilasci monotoni, non un'ottimizzazione globale: potrebbe non trovare una soluzione fattibile anche se ne esiste una. Le famiglie iniziali devono essere fornite esplicitamente. Non legge ne' scrive il database Factory.

## Ricerca tra alternative di risorsa

`setup-search` mantiene lo scenario originale immutato e prova fino a `--budget` candidati con le risorse gia' dichiarate. La ricerca valuta prima lo scenario senza forzature, poi **i cambi di macchina su una singola lavorazione** (a rotazione tra le lavorazioni), poi i cambi combinati e infine altre selezioni esplicite. Questo evita che un budget piccolo venga sprecato soltanto sulla prima combinazione cartesiana di macchine. Nessuna combinazione di risorse viene inventata.

Ogni candidato parte dallo scenario originale, usa al massimo `--repair-budget` riparazioni di attrezzaggio e viene verificato con l'audit dei vincoli e degli attrezzaggi. I rifiuti sono identificati con le scelte forzate. I budget sono limiti rigidi ai tentativi, **non** certificati di ottimalita' globale. Un budget insufficiente puo' ancora non individuare una soluzione esistente.

```sh
planning-factory setup-search scenario.json setup-rules.json -o setup-plan.json --scenario-output setup-scenario.json --budget 8 --repair-budget 12
```

Questo miglioramento fa parte del nuovo Planning Factory indipendente (ADR 002), non di una ricerca di compatibilita' con i risultati del software storico.
