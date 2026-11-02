# Export per MS Project (MSPDI) — #89

Chi oggi pianifica con MS Project può aprire in Project il piano calcolato e verificato qui, senza perdere il lavoro fatto dal motore.

```
planning-factory export-msproject scenario.json plan.json -o piano.xml [--timezone Europe/Rome] [--title "Settimana 43"]
```

Il comando **verifica il piano** contro lo scenario (`verify_plan`) prima di esportarlo. Un piano alterato o di un altro scenario viene rifiutato con codice di uscita 1. In Project il file si apre con *File → Apri → XML*.

## Struttura

| Elemento Project | Origine |
|---|---|
| Attività di riepilogo | Una per commessa (`Commessa <id>`). Due riepiloghi a parte: `Nesting multicommessa` per i nesting che legano più commesse, `Non pianificate` |
| Attività | Una per unità di piano: fase singola (`job:`) o nesting atomico (`nest:`), con le fasi nel nome |
| Inizio, Fine | Date del piano convertite nell'ora locale di `--timezone` |
| Durata | Lavoro prenotato (lavorazione + setup), non l'intervallo di calendario |
| Collegamenti | Precedenze FS/SS tra unità; ritardo in minuti trascorsi (`LagFormat` = em) |
| Risorse e assegnazioni | Risorse usate dal piano, unità richieste per ogni assegnazione |
| Note | Unità di piano, commesse, storico, inizio fisso, frammenti delle interruzioni e spiegazione del motore |

Tutte le attività sono **pianificate manualmente**. Project conserva le date calcolate qui e non le ricalcola con il proprio motore, che non conosce capacità finita, nesting, setup e calendari per risorsa.

## Casi particolari

- **Storico** (fasi chiuse): attività cardine (milestone) al 100% alla data di chiusura.
- **Conto lavoro con fine dichiarata**: attività cardine alla data promessa dal fornitore, con nota.
- **Unità non pianificate**: nome `[NON PIANIFICATA] …` sotto il riepilogo dedicato, con data fittizia all'inizio dell'orizzonte. La nota riporta il codice del motore (`NO_FEASIBLE_SLOT`, `PREDECESSOR_UNSCHEDULED`).
- **Interruzioni**: l'attività va dal primo inizio all'ultima fine. I frammenti stanno nella nota, perché lo split nativo di Project (dati con fasatura temporale) non viene scritto.
- **Ritardi**: Project li registra in decimi di minuto. Un ritardo non multiplo di 6 secondi viene arrotondato per difetto e la nota lo indica.

## Limiti

- Il calendario del file è quello Standard di Project, senza turni né festività: le date restano quelle del piano perché le attività sono manuali.
- Lo schema MSPDI è rispettato nell'ordine degli elementi usati, ma **l'apertura in una versione reale di MS Project va collaudata con il cliente**: in questo ambiente non è disponibile.
- L'export va da Planning Factory a Project. L'import di modifiche fatte in Project non c'è: il piano di riferimento resta quello verificato qui.
