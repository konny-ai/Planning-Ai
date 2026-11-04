# ADR 006 — Fasi senza risorse candidate e setup dinamici (PROPOSTA)

**Stato:** proposta, **non approvata**. Nessun codice la implementa. Serve a
ridurre la scelta del committente a una risposta, come ADR 005. Chiude il punto
aperto della roadmap "strategia esplicita per le fasi senza risorse candidate".

## Comportamento attuale (verificato nel codice)

- Il controllo qualità dei dati canonici segnala `no_resource` come **errore**:
  fase aperta senza nessuna risorsa candidata (`docs/canonical-data.md`).
- Lo scheduler a due stadi **fallisce in modo esplicito** (`ValidationError`)
  se una fase con durata positiva non ha requisiti di risorsa dichiarati.
- Quindi oggi un solo dato mancante blocca il piano intero: nessuna fase
  "salta" e nessuna risorsa viene mai inventata (ADR 003, punto 7).

## Il problema

Nei dati reali (ADR 003: il clone banco A ha 188 fasi esterne con durate che
non sono tempo macchina) capitano fasi senza risorsa candidata. Bloccare tutto è
sicuro ma poco utile; ogni alternativa però introduce una regola di prodotto.

## Opzioni

**A. Blocco (comportamento attuale).** Il piano non si calcola finché la fase
non è completata nei dati. Pro: nessuna regola inventata. Contro: un buco di
anagrafica ferma tutta la pianificazione.

**B. Non pianificata con motivo esplicito.** Il resto del piano si calcola; la
fase compare come non collocata con codice dedicato (es. `NO_CANDIDATE`) e i
suoi successori restano non collocati (`PREDECESSOR_UNSCHEDULED`, come oggi per
le catene bloccate). Pro: piano parziale utile e onesto. Contro: le consegne a
valle risultano non pianificabili finché il dato non arriva.

**C. Tratta come traguardo esterno con data dichiarata.** Solo se la sorgente
fornisce una data (come `external_end` dei fornitori): nessuna prenotazione,
vincolo FS sui successori. Pro: coerente con il modello fornitori. Contro:
richiede una data affidabile; senza data ricade su A o B.

**D. Tempo senza risorsa (capacità infinita).** La fase occupa solo tempo, non
risorse. Pro: il piano è completo. Contro: **nasconde colli di bottiglia** e può
far sembrare fattibile ciò che non lo è; da usare solo se il committente
conferma che quelle fasi non impegnano risorse finite.

## Setup dinamici

Oggi lo stato macchina è dichiarato (contratto setup v1) e il setup si salta
solo se coincide. Domande aperte (ADR 003): come si rileva lo stato, quanto
permane dopo guasti o modifiche manuali, e se esiste preattrezzaggio esterno
mentre la macchina lavora. Finché non sono risposte, uno stato sconosciuto
**non** vale "già pronto".

## Domande per il committente

1. Quale opzione (A, B, C, D) per le fasi senza risorsa candidata? Si può
   combinare: es. C se c'è una data dichiarata, altrimenti B.
2. Le fasi senza risorsa impegnano risorse finite (macchina/operatore)? In caso
   negativo, D diventa ammissibile per quelle fasi.
3. Come si conosce lo stato macchina reale (sorgente, frequenza) e per quanto
   resta valido dopo un fermo?
4. Esiste preattrezzaggio esterno (setup mentre la macchina lavora un altro
   pezzo)? Se sì, con quali risorse?

## Vincoli che restano validi in ogni caso

Audit indipendente di ogni piano, nessuna risorsa o tempo inventato, nessuna
matrice di transizioni inventata, nessun peso numerico non approvato, nessuna
scrittura su Factory.
