# ADR 001 — Profilo nuovo esplicito e deterministico

Data: 18 ottobre 2026. Stato: decisione tecnica iniziale nell'ambito dello sviluppo autorizzato; non certificazione di equivalenza al legacy.

## Evidenze

La guida 01, la specifica M01/M03/M04/M07/M08/M09 e le rettifiche del capitolo 44 mostrano che il planner legacy contiene comportamenti dipendenti dalla configurazione e difetti noti. Il brainstorming richiede nesting multipli pianificati insieme, conteggiando il tempo dell'intero nesting una volta.

## Decisione

Realizzare un motore autonomo con contratti nuovi e verificabili. Il termine `new profile` indica le convenzioni descritte in `requirements.md`; non viene esposto un selettore `legacy` che faccia intendere una compatibilità non implementata.

Usare: istanti UTC + calendari con fuso, interi per i secondi, bundle espliciti di risorse simultanee, DAG con FS/SS, nesting atomici, euristica seriale con target backward, priorità lineare esplicita, snapshot immutabili e audit indipendente.

## Differenze intenzionali

- Ordine totale per ID, senza introsort instabile o puntatori.
- Nessun wrap unsigned dei pesi, date nulle/errate rifiutate.
- Nessun limite 2037, dipendenza dal fuso del computer o epsilon non transitivo.
- Nessuna duplicazione delle durate dei nesting; nessun riuso delle forzature temporanee legacy.
- Setup residuo esplicito da consuntivo se richiesto, senza replicare i numeratori mai valorizzati.
- `LOCLP == LOCOD` è radice nell'adapter Factory, non ciclo; i veri cicli sono rifiutati.
- Nessun tentativo/simulazione modifica lo scenario o scrive dati operativi.

## Conseguenze

**Aggiornamento 20/10/2026:** l'[ADR 002](002-independent-factory-planning.md) stabilisce che la parita' con FSMo_SCH non e' richiesta ne' desiderata. Ogni accenno seguente a una possibile compatibilita' legacy e' storico e non rappresenta lavoro da pianificare.

Il motore è utilizzabile con scenari normalizzati e consente verifiche riproducibili. La parità con un cliente richiede un contratto separato, configurazione effettiva e casi di replay. Restano da decidere con evidenze del cliente: compatibilità dei difetti legacy, semantica MultiOdl=3, quantità frazionate su più macchine, regole fornitore e attrezzaggio dipendente dalla sequenza.
