# ADR 004 — Pianificazione backward con attrezzaggio di fase (PROPOSTA)

**Stato:** proposta, **non approvata**. Nessun codice la implementa: lo scheduler
a due stadi (`schedule_setup_stages`) continua a rifiutare i task in modalità
`backward`/`auto`. Questo documento esiste solo per ridurre la scelta del
committente a una risposta.

## Problema

ADR 003 definisce attrezzaggio e lavorazione come stadi separati, ma non dice
come si comportano quando la lavorazione e' pianificata **all'indietro** dalla
consegna. Servono regole esplicite su: dove va l'attrezzaggio rispetto alla
lavorazione, e cosa succede alla sequenza macchina (lo stato macchina dipende
dall'ordine, quindi un piano all'indietro cambia quali attrezzaggi servono).

## Opzioni

**A. Lavorazione backward, attrezzaggio "il più tardi possibile" prima di essa.**
Setup immediatamente prima della lavorazione, rispettando risorse e calendari.
Pro: minimizza il tempo di macchina ferma e di WIP. Contro: sequenza e stato
macchina vanno fissati *prima* (l'ordine non puo' emergere dal calcolo).

**B. Lavorazione backward, attrezzaggio "il più presto possibile" (anticipato).**
Il setup si porta avanti appena risorse e macchina sono libere (ADR 003 punto 4
lo consente: puo' iniziare prima del materiale). Pro: piu' robusto ai ritardi.
Contro: occupa operatori/attrezzi prima del necessario.

**C. Solo forward per gli stadi; backward limitato alla verifica della consegna.**
Si pianifica in avanti e il backward serve solo a calcolare lo slack. Pro:
nessuna nuova semantica. Contro: non soddisfa chi vuole piani just-in-time.

## Domande per il committente

1. Quale opzione (A, B, C) riflette il bisogno reale?
2. In backward, la sequenza macchina e' un dato fornito o va ottimizzata
   (oggi `setup-stages` accetta `--sequence` o parte da un ordine per scadenza)?
3. Lo slack di setup puo' essere "assorbito" spostando il setup a un turno
   precedente anche se un operatore e' occupato altrove?

## Vincoli che restano validi in ogni caso

Audit indipendente di ogni piano, nessuna matrice di transizioni inventata,
nessun peso di business numerico non approvato, nessuna scrittura su Factory.
