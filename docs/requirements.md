# Semantica del motore v0.2

## Orizzonte e tempo

Ogni scenario specifica `start` e `end` ISO 8601 con offset. L'intervallo è [start, end), espresso internamente in UTC. Unità: secondi interi; le frazioni sono rifiutate al confine del motore. L'adattatore delle durate conserva i decimali e arrotonda il residuo per eccesso al secondo. L'orizzonte esplicito è limitato a 3660 giorni; il chiamante può calcolare `end` da 40/100 giorni o da una data scelta.

I turni sono definiti nel fuso IANA del calendario. Una fine minore o uguale all'inizio significa giorno successivo, compreso un turno di 24 ore. Il tempo consumato è tempo realmente trascorso: il turno 22–06 dura 7 o 9 ore durante i cambi d'ora. Un endpoint locale ambiguo o inesistente è rifiutato: il chiamante deve scegliere estremi non ambigui. Non ci sono sentinelle OLE o limite al 2037.

Festività: chiudono l'intera data civile e tagliano anche la coda della notte precedente. Eccezioni: sostituiscono i turni che INIZIANO nella data; una lista vuota non annulla automaticamente la coda della notte precedente. Per chiusure che tagliano qualsiasi turno usare `closures`, intervalli assoluti. Turni sovrapposti vengono uniti; la disponibilità non viene duplicata.

## Risorse

Una risorsa ha capacità intera positiva, oppure `null` per capacità infinita. Anche la capacità infinita rispetta il calendario. Un'alternativa contiene un bundle di domande simultanee: per esempio macchina L1 + operatore O1, tutte necessarie per l'intera durata. Le alternative sono selezioni complete; non vengono inventate combinazioni o compatibilità.

Tutti i frammenti di un'operazione interrompibile mantengono lo stesso bundle. La capacità è consumata solo nei frammenti lavorati, non durante le pause. Il setup viene sommato una sola volta al tempo residuo; se `splittable=true`, l'intera durata comprensiva di setup può essere interrotta. Setup dipendenti dalla sequenza e cambio risorsa durante l'operazione richiedono un diverso contratto.

## Precedenze e nesting

`FS`: start_successore >= end_predecessore + lag. `SS`: start_successore >= start_predecessore + lag. Il lag è non negativo e usa secondi trascorsi, anche fuori turno. I cicli sono rifiutati prima del booking.

Ogni nesting raggruppa membri distinti, anche di commesse diverse. `duration_seconds + setup_seconds` del nesting SOSTITUISCE la somma delle durate dei membri. La prenotazione è unica, riferita a tutti i membri. I membri devono condividere almeno un identico bundle ammissibile, stessa modalità e stessa interrompibilità; gli inizi fissi presenti devono concordare. La disponibilità è la massima dei membri, la consegna la minima. Una dipendenza interna al nesting è rifiutata; il collasso dei gruppi non può introdurre cicli.

## Modalità e priorità

`forward`: primo slot fattibile. `backward`: cerca l'ultimo posizionamento entro la consegna e i target propagati dai successori, a partire dalle precedenze già soddisfatte. `auto`: backward se consegna >= start + `auto_backward_days`, altrimenti forward. La soglia nuova usa istanti UTC e non replica il confronto legacy sulla sola data.

Con `settings.auto_rule = "slack"` (#78 P4, spento per default) `auto` guarda il lavoro che resta invece della distanza della consegna. Il motore stima, senza capacità ma con calendari e precedenze, la fine più presto di ogni unità e la confronta con la fine più tardi ammessa dalla consegna e dai successori. Se il margine supera `settings.auto_slack_seconds` l'unità va a ritroso, altrimenti in avanti. La spiegazione riporta `auto by slack: margin <s>s vs <soglia>s -> <modo>`. Con il valore di default `due_distance` piani, digest e golden non cambiano. I due campi sono anche nel profilo dei dati canonici.

Il pianificatore è una euristica seriale topologica. I target backward sono calcolati ignorando le altre prenotazioni e poi verificati con la capacità reale. Se un target non è fattibile, si cerca in avanti nell'orizzonte e si registra il motivo. Le modalità miste sono ammesse, senza ricerca combinatoria o backtracking globale. Un risultato non collocato descrive l'insuccesso della ricerca rispetto alle scelte già confermate, non una prova matematica di impossibilità globale.

Fra le unità pronte vince il punteggio minore:

`priority * priority_weight + floor((due - start)/86400) * due_weight + customer_priority * customer_weight + phase_priority`

Consegna assente: il termine usa fine orizzonte. Pesi predefiniti: 10000, 1, 1. Pesi negativi rifiutati; priorità firmate consentite. Spareggio: consegna, ID task. Per un nesting ogni priorità è il minimo dei rispettivi valori dei membri. Nessuna raffinazione ricorsiva dell'albero, bonus occulto o wrap unsigned.

## Inizi fissi, avanzamento e ripianificazione

Le prenotazioni con `fixed_start` vengono riservate prima di quelle flessibili. `fixed_alternative`, se presente, fissa anche il bundle indicato. Conflitti fra prenotazioni fisse, calendari o precedenze generano un errore di scenario. Un predecessore flessibile deve terminare prima del successore fissato.

`freeze()` congela inizi e bundle da un piano verificato e include tutti i membri del nesting. Per operazioni interrotte non congela i timestamp di ogni frammento successivo: questi vengono ricalcolati. Per attività già avviate il chiamante deve fornire il lavoro residuo con un nuovo inizio non precedente all'orizzonte. La ricostruzione delle sessioni aperte ERP non è implicita.

Le operazioni completate hanno zero residuo e `completed_at <= start`; conservano l'istante per le precedenze senza occupare la capacità futura. Residuo zero senza evidenza di completamento è rifiutato.

## Esiti

Ogni unità ha un'assegnazione oppure una diagnostica non vuota. Il fallimento di un predecessore blocca i successori. Una commessa incompleta non riceve una data di fine promessa. La consegna riepilogativa della commessa è la minima consegna non nulla delle sue fasi: è una convenzione conservativa; un'integrazione con righe ordine distinte dovrà esporre esplicitamente gli impegni di consegna.

Carico: somma secondi lavorati × unità richieste. Disponibilità: secondi calendario × capacità. Utilizzo nullo per capacità infinita o calendario senza disponibilità. Il nesting pesa una sola volta per risorsa, pur contribuendo alle consegne di tutti i membri.
