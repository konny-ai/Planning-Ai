# ADR 006 — Fasi senza risorse candidate e setup dinamici (PROPOSTA)

**Stato:** il committente ha scelto l'**opzione B** (04/11/2026), implementata
come percorso opt-in (vedi sezione finale). Il comportamento di default resta
bloccante (A). Chiude il punto della roadmap "strategia esplicita per le fasi senza
risorse candidate"; i setup dinamici restano aperti.

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

## Decisione e implementazione (opzione B)

Scelta del committente: **B** — il resto del piano si calcola e le fasi senza
risorse candidate risultano non pianificate con motivo esplicito.

- Libreria: `plan_setup_stages_partial` (`engine/setup_partial.py`). Una fase e'
  "senza candidate" se l'operazione non ha alternative (e non e' completata ne'
  traguardo fornitore) oppure se e' indicata in `extra_without_candidates`.
- Codici: `NO_CANDIDATE`; `PREDECESSOR_UNPLANNED` per i successori (transitivi);
  `NEST_MEMBER_UNPLANNED` per gli altri membri di un nesting che contiene una fase
  non pianificabile (il nesting e' una sola attivita' indivisibile).
- CLI: `setup-stages --partial [--no-candidate ID,ID] [--backward --scenario-output ...]`.
  Un Scenario JSON non puo' contenere operazioni senza alternative (il caricamento le
  rifiuta), quindi dalla CLI si dichiarano con `--no-candidate`, ad esempio con gli
  ID del controllo qualita' `no_resource`.
- Nessuna risorsa, durata o data viene inventata; il piano restante e' auditato; le
  prenotazioni congelate di fasi non pianificate sono rifiutate; con nulla di
  pianificabile il risultato non ha piano e solo l'elenco delle non pianificate.
- I KPI di consegna non sono definiti per le commesse senza alcuna fase pianificata
  (il report li omette); le commesse con fasi non pianificate vanno lette insieme
  all'elenco `unplanned`, non come consegne rispettate.

## Setup dinamici: stato macchina (confermato)

Risposta del committente (04/11/2026) alla domanda 3: lo stato macchina e'
**dichiarato nel file e valido a inizio orizzonte** (contratto setup v1,
`initial_machine_states`; un dato mancante o `null` significa stato sconosciuto e
quindi setup, mai "gia' pronto"). Nessuna scadenza dello stato e nessun reset
dopo fermi o guasti: e' il comportamento gia' implementato, quindi non cambia
nulla nel codice. Resta aperta solo la domanda 4 (preattrezzaggio esterno,
cioe' setup mentre la macchina lavora un altro pezzo): oggi il setup non puo'
sovrapporsi alla lavorazione precedente sulla stessa macchina e l'audit lo verifica.
