# Changelog

## Non rilasciato — SPERIMENTALE: lavorazioni just-in-time

`just_in_time_tail` / `setup-stages --just-in-time-tail --scenario-output ...`
ritarda la lavorazione di ciascun task fino allo spazio che ha prima che
qualcos'altro debba spostarsi (slack di consegna, distanza dai successori FS/SS
con lag, distanza dal lavoro successivo sulla macchina; dalle ultime alle prime
fasi), lasciando l'attrezzaggio anticipato (simile all'opzione B di [ADR 005](docs/decisions/005-backward-setup-proposta.md),
che resta **una proposta non approvata**). Il ritardo diventa un rilascio
derivato nello scenario esportato; il piano e' ripianificato e auditato e un
candidato e' tenuto solo se nessun'altra fase cambia e i KPI non peggiorano
(altrimenti e' riportato come rifiutato). Non e' la modalita' backward
completa, che resta rifiutata.

## Non rilasciato — comando `setup-stages`

`planning-factory setup-stages scenario.json contratto.json -o stadi.json`
pianifica attrezzaggio e lavorazione come stadi separati (ADR 003), con
sequenza di default per scadenza o `--sequence`, ricerca opzionale
`--objective delivery|setup|flow` (`--neighbourhood`, `--budget`) e report con
prenotazioni, KPI e violazioni dell'audit indipendente (codice 2 se presenti).
Non scrive mai sugli ingressi e non tocca Factory. `--start state-grouped`
(opzionale, default `edd`) parte da un ordine che raggruppa per macchina le
fasi con lo stesso stato obiettivo, partendo dallo stato attuale della
macchina (`state_grouped_sequence`); se viola le precedenze si ricade sull'ordine
per scadenza. `--start best` (richiede `--objective`) confronta i due ordini
(`best_start_sequence`) e parte dal migliore sull'obiettivo scelto; a parità
resta l'ordine per scadenza.

## Non rilasciato — sequenza macchina automatica per lo scheduler setup/run

`optimize_setup_sequence` accetta ora `initial_sequence=None`: la sequenza di
partenza è `default_machine_sequence`, un ordine deterministico per scadenza
(EDD) che rispetta le precedenze, senza pesi di business inventati. Nuovo
parametro `neighbourhood='insertion'` (default `'swap'`, invariato) che aggiunge
il reinserimento di una fase in posizioni non adiacenti della stessa macchina.
Ogni candidato resta verificato dallo scheduler a due stadi e dall'audit
indipendente; nessuna garanzia di ottimalità globale. Test in
`tests/test_setup_sequence_search.py`.

Lo scheduler a due stadi accetta ora le **fasi già completate** come storico:
nessuna prenotazione, precedenze dei successori soddisfatte, stato macchina
osservato aggiornato (devono precedere le fasi attive sulla stessa macchina),
audit che rifiuta nuove prenotazioni per esse e KPI che ne usano il
completamento. Le **scadenze promesse dai fornitori** (`external_end`) non hanno fase né
prenotazione: un successore FS non può avviare la lavorazione prima della fine
dichiarata più il lag (l'attrezzaggio può anticiparla); l'audit verifica lo
stesso vincolo e SS da un fornitore è rifiutato. Gli **inizi fissi** (`fixed_start`) ancorano la lavorazione a
quell'istante (l'attrezzaggio deve concludersi prima, altrimenti il piano
fallisce in modo esplicito) e l'audit verifica l'ancoraggio; `fixed_alternative`
restringe le macchine ammesse. Resta rifiutata la modalità backward.

## Non rilasciato — assistente conversazionale (ramo feat/cliente-assistente-chat-v2)

Pannello **Assistente** in ogni vista della Control Room, con conversazione,
suggerimenti per vista e proposte applicabili come simulazioni verificate.
Nuove analisi per digest: percorso critico per commessa (`/api/explain`),
saturazione giornaliera da calendario (`/api/loads`), KPI di qualità
(`/api/quality`). L'assistente AI ha 12 strumenti di lettura con argomenti,
storico di 10 turni e lo strumento `propose_simulation`; senza AI le
risposte restano deterministiche. Piano e verifiche:
[docs/piano-assistente-chat-v2.md](docs/piano-assistente-chat-v2.md).

## 0.2.0 — 2026-10-18

Motore nuovo di pianificazione a capacità finita, calendari UTC/locali, bundle simultanei, nesting multipli, precedenze FS/SS, modalità per operazione, inizi fissi, storico e diagnosi. Aggiunti audit indipendente, contratti JSON e schemi, CLI, API locale, SQLite, simulazioni, adapter Factory in lettura, report e suite di test.

Il profilo nuovo è documentato esplicitamente e non certifica parità con il legacy.

## 0.1.0 — 2026-10-18

Struttura iniziale del progetto e configurazione CI.
