# Changelog

## Non rilasciato — modalità per task nello scheduler a due stadi

`plan_setup_stages_by_mode` / `setup-stages --task-modes --scenario-output ...`
rispettano `mode` per task: `forward` resta il piu' presto possibile,
`backward` riceve l'opzione A (lavorazione al piu' tardi, setup subito prima),
`auto` diventa backward se la scadenza e' ad almeno `auto_backward_days` dall'inizio
(regola `due_distance` del motore standard; la regola `slack` e' rifiutata). Un task
backward senza scadenza fallisce in modo esplicito. Lo scheduler resta forward-only
e lo scenario di ingresso non viene modificato. Non si combina con
`--backward`, `--just-in-time-tail` o `--objective`.

## Non rilasciato — backward nella pipeline materiali

`plan_materials_and_setup_stages` (e le varianti WIP residuo e blocchi attivi)
accettano `backward=True` e `sequence_objective='delivery'|'setup'|'gap'`: dopo
allocazione e audit dei materiali applicano l'opzione A (lavorazioni al più
tardi, setup subito prima), con ricerca di sequenza opzionale sul piano
backward. Si ritarda soltanto, quindi la disponibilita' dei materiali resta
garantita (verificato esplicitamente); le prenotazioni congelate sono supportate:
restano identiche (setup e lavorazione), non vengono mai ritardate e limitano lo
spazio di ritardo dei vicini (successori, lavoro seguente sulla macchina). `MaterialStagedPlan` espone la
sequenza usata (`machine_sequence`) e il risultato backward con i gap.

## Non rilasciato — ricerca di sequenza sul piano backward

`optimize_backward_sequence` e `setup-stages --backward --objective delivery|setup|gap`
cercano la sequenza macchina valutando ogni candidato direttamente sul proprio
piano backward (ADR 005, opzione A): lavorazioni al più tardi e attrezzaggi
subito prima. Obiettivi `delivery`, `setup` e `gap` (somma dei gap
setup→lavorazione); `flow` non è ammesso perché il makespan non è significativo
per piani just-in-time. Ogni candidato è ripianificato e auditato, con budget
rigido (1..64) e nessuna garanzia di ottimalità globale.

## Non rilasciato — modalità backward (ADR 005, opzione A, scelta dal committente)

`backward_setup_before_run` / `setup-stages --backward --scenario-output ...`:
lavorazioni al più tardi possibile e attrezzaggio subito prima di esse, con
sequenza macchina fissata prima. Ogni passo è ripianificato e auditato; il report
dà il gap residuo setup→lavorazione per fase. `schedule_setup_stages` accetta
`setup_releases` (solo ritardi di setup). Non cambia durate/numero di
attrezzaggi e non sposta nulla prima del piano in avanti.

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
