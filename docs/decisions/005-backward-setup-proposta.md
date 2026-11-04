# ADR 005 — Pianificazione backward con attrezzaggio di fase (PROPOSTA)

**Stato:** il committente ha scelto l'**opzione A** (04/11/2026). Implementata
come politica esplicita `backward_setup_before_run` / `setup-stages --backward`
(vedi sezione finale). Lo scheduler a due stadi continua a rifiutare i task
in modalità `backward`/`auto` *nello scenario di ingresso*: il backward e' una
politica applicata al piano, con sequenza macchina fissata prima.

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

## Prototipo sperimentale (non approvato)

Per rendere concreta l'opzione B senza imporla, il repository contiene
`just_in_time_tail` e `setup-stages --just-in-time-tail`: ritarda ogni
lavorazione fino allo spazio che ha (consegna, successori, lavoro successivo
sulla macchina), ripianifica e audita, e rifiuta ogni candidato che sposti altre
fasi o peggiori i KPI. E' opt-in e puo' essere rimosso se il committente sceglie
A o C. Non sposta mai l'attrezzaggio ne' la sequenza macchina.

## Decisione e implementazione (opzione A)

Scelta del committente: **A** — lavorazione al più tardi possibile e
attrezzaggio subito prima di essa, sequenza macchina fissata in anticipo.

`planning-factory setup-stages ... --sequence ... --backward --scenario-output adj.json`:
1. ritarda le lavorazioni dello spazio che hanno (consegna, successori FS/SS con
   lag, lavoro successivo sulla macchina), come `--just-in-time-tail`;
2. porta ogni attrezzaggio contro la sua lavorazione alzando il rilascio del
   solo setup (ricerca per bisezione, secondi interi);
3. ogni passo e' ripianificato e auditato e tenuto solo se nient'altro si
   sposta e i KPI non peggiorano. Il report indica per fase il `gap` residuo
   fra fine setup e inizio lavorazione: e' > 0 quando calendari, pause o
   la lavorazione precedente sulla macchina impediscono di avvicinarlo.

Non cambia la durata ne' il numero degli attrezzaggi, non sposta mai nulla
prima del piano in avanti e non accede a Factory. La sequenza e' un dato fissato prima del backward: si puo' ottimizzarla
con `--objective` (con `--backward` ogni candidato e' valutato sul proprio
piano backward; obiettivi `delivery`, `setup`, `gap`) o fornirla con `--sequence`; i rilasci derivati dei setup sono
riportati nel report, non nello scenario esportato.

Aggiornamento: con `--task-modes` (`plan_setup_stages_by_mode`) i task con
`mode` backward/auto nello scenario di ingresso sono accettati e ricevono
l'opzione A (solo quelli che la richiedono; `auto` segue la regola `due_distance`).
