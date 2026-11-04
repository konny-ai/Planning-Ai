# Changelog

## Non rilasciato — ripianificazione incrementale esatta

`plan(..., trace=, resume=)`: se uno scenario differisce dal precedente solo per il `release`
di alcune attivita', quelle schedulate **prima** della prima modificata nello stesso ordine di
dispatch sono identiche (con la politica `priority` l'ordine dipende solo da chiavi statiche,
non dal rilascio) e si riusano invece di ricercarle di nuovo. Ogni precondizione e' ricontrollata
(`_reusable`: stesse risorse, calendari, impostazioni, orizzonte, archi e attivita' tranne il solo
rilascio dichiarato; solo attivita' in avanti; piano precedente senza lavoro non collocato); se una
fallisce si ripianifica per intero, quindi il risultato non dipende mai da `resume`. Lo scheduler a
stadi la usa nei cicli del backward (`resume_from`, `changed_stage_tasks`). Provata l'equivalenza
esatta con piano completo su 250 scenari casuali, su 150 scenari a stadi e sui casi con nesting,
completate e inizi fissi; stesso hash delle prenotazioni sulle misure: 80 fasi 11,4 s -> 8,9 s,
160 fasi 32,3 s -> 22,6 s. Il resto del tempo e' nel calcolo degli intervalli liberi.

## Non rilasciato — correzioni dalla revisione del codice

(1) Finestra di ricerca: con piu' alternative poteva scegliere una macchina diversa dalla ricerca
completa (vedi sopra). (2) `plan(trace=)` scrive la traccia dopo il ciclo di riparazione, quindi
descrive sempre il piano restituito quando c'e' una traccia. (3) Le cache per oggetto di
`compile_tasks` e `scenario_digest` sono protette da un lock (il servizio pianifica in thread) e
l'eviction non puo' piu' fallire con una chiave gia' rimossa. Non applicati: dividere
`setup_sequence_search.py` (819 righe) e l'handler `setup-stages` della CLI (~215 righe): sono
refactoring ampi senza cambiamento di comportamento.

## Non rilasciato — correzione dalla quarta revisione (piano parziale)

`--no-candidate` / `extra_without_candidates` rifiutano ora le operazioni gia' completate e i
traguardi fornitore (non servono risorse): prima venivano rimosse in silenzio dal piano e
trascinavano via i successori pianificabili (`PREDECESSOR_UNPLANNED`). Provata anche la modalita'
`holds` di `staged-plan` (normale, backward, con ricerca): audit pulito. Test di regressione.

## Non rilasciato — correzioni dalla terza revisione (ricerca di sequenza)

(1) `default_machine_sequence` andava in `KeyError` con due traguardi fornitore concatenati elencati
dal successore: il grafo e' ora costruito solo sulle attivita' pianificate. (2) `sequence_metrics`
dava un makespan negativo se tutto era completato prima dell'inizio dell'orizzonte (e il report
violava il proprio schema): ora e' almeno 0. (3) Il ritardo delle lavorazioni non tenta piu' di
spostare quelle con inizio fisso, che comparivano tra le "rifiutate". Ognuna con un test.

## Non rilasciato — lag da predecessori completati (correzione)

Nel piano a stadi l'arco da un predecessore gia' completato veniva scartato **insieme al suo lag**: il
successore partiva troppo presto (con lag di 20 ore, il 19 ottobre invece del 20, come invece fa il
pianificatore standard) e l'audit indipendente non lo vedeva. Ora il vincolo minimo di avvio
(`external_release_floors`) comprende `completed_at + lag` per FS e SS, ed e' verificato anche
dall'audit. Test di regressione che fallisce senza la correzione (3 errori).

## Non rilasciato — schema del report di `setup-stages`

`contracts/setup-stages-report-v1.schema.json`: il formato del report per le integrazioni. Un
validatore minimo in sola libreria standard (`tests/schema_check.py`) applica lo schema ai report di
11 modalita' (base, ricerca, partenze multiple, just-in-time, backward con e senza ricerca, per task,
parziale, replay) e verifica che rifiuti report alterati.

## Non rilasciato — partenze multiple anche per il backward, errori piu' chiari

`optimize_backward_sequence_multistart` / `setup-stages --backward --objective ... --restarts N --seed S`:
stesse partenze deterministiche della ricerca in avanti (ordine per scadenza, per stato, N
perturbazioni), ma ogni candidato e' valutato sul proprio piano backward; mai peggiore della
partenza singola. `--restarts` resta rifiutato con `--task-modes` e `--partial`. Il codice di
generazione delle partenze e' condiviso (comportamento in avanti invariato). Un report `--replay`
malformato ora da' un errore esplicito invece di una chiave mancante. Controllati 25 ingressi malformati
(JSON rotto, file vuoti o mancanti, contratti non validi, opzioni fuori intervallo): sempre errore JSON e
codice 1, mai un traceback.

## Non rilasciato — serializzazione piu' veloce (digest identici)

`primitive()` (usata da `dumps` e da `scenario_digest`) ha un percorso rapido per i tipi base esatti e
calcola una volta per classe i nomi dei campi; tutto il resto passa dal percorso di prima. L'output e'
identico byte per byte (test contro l'implementazione originale congelata in
`tests/reference_json_io.py`, su 340 oggetti tra scenari, piani e valori limite), quindi i digest gia'
registrati restano validi. Backward: 80 fasi 5,7 s -> 4,8 s, 160 fasi 13,6 s -> 12,7 s.

## Non rilasciato — tempi liberi aggiornati in modo incrementale

`bundle_free` con cache non ricalcola piu' da zero i tempi liberi di una risorsa a ogni nuova
prenotazione: tiene il profilo di carico (aggiornato per prenotazione) e sottrae dai tempi liberi
memorizzati solo le zone che le nuove prenotazioni saturano (`free_intervals` resta la definizione
di riferimento). Provata l'uguaglianza esatta passo per passo su 400 sequenze casuali di
prenotazioni (capacita' 1, 2, 3, 10.000, risorse a capacita' infinita; oltre 20.000 confronti con il
calcolo da zero). Stesso hash delle prenotazioni su tutte le misure; backward: 80 fasi 6,8 s -> 5,7 s,
160 fasi 18,6 s -> 13,6 s, 320 fasi 97 s -> 50 s.

## Non rilasciato — fuzz esteso del percorso a stadi

`tests/test_setup_stage_fuzz_extended.py`: scenari casuali a seme fisso su prenotazioni congelate
(restano identiche o il calcolo fallisce in modo esplicito), fasi non pianificabili (chiusura sui
successori, nessuna prenotazione per le non pianificate, audit pulito) e modalita' per task
(piani sempre auditati). Nessun difetto trovato; nessuna modifica di comportamento.

## Non rilasciato — comando `staged-plan` (materiali + stadi da riga di comando)

`planning-factory staged-plan scenario.json spec.json -o piano.json [--html] [--scenario-output]`
esegue la pipeline materiali + setup/run (+ backward e ricerca di sequenza se la specifica v0.3
li chiede) da file, con report JSON (materiali, sequenza, prenotazioni, KPI, gap, audit) e
HTML offline. Fail-closed: carenza di materiale, digest non coerente, audit o sovrascrittura
degli ingressi fermano il calcolo senza scrivere il report; mai pubblicabile su ERP.

## Non rilasciato — ottimizzazioni esatte del motore

(1) `subtract` degli intervalli lavora sui tagli invece che su ogni intervallo (provato uguale alla
definizione su 3.000 casi casuali); (2) la ricerca in avanti guarda prima in una finestra di 14
giorni (`scheduler.SEARCH_WINDOW`) e poi sull'orizzonte intero; la finestra decide solo se **ogni**
alternativa del task vi trova posto (una che entra solo dopo potrebbe comunque vincere sull'orizzonte
intero, partendo prima: difetto trovato dalla revisione del codice e corretto, con test che fallisce
senza la correzione), quindi il piano non cambia (provato su 200 scenari con
attivita' lunghe, anche oltre la finestra; escluse ricerche all'indietro, a inizio esatto e con
alternativa preferita); (3) `compile_tasks` e' memorizzato per oggetto scenario, restituendo
sempre un dizionario nuovo. Stesso hash delle prenotazioni su tutte le misure; backward: 80 fasi
8,9 s -> 6,8 s, 160 fasi 22,6 s -> 18,6 s, 320 fasi 141 s -> 97 s.

## Non rilasciato — guida e esempio per la pianificazione a stadi

`docs/guida-pianificazione-a-stadi.md` raccoglie in un solo posto ingressi, sequenza,
ricerca, backward, replay e fasi senza candidate, con `examples/setup-stages/` (dati
sintetici). Un test esegue gli stessi comandi e controlla i numeri citati (attrezzaggio
5.400 s -> 1.800 s). Corretti i testi di aiuto della CLI per `--just-in-time-tail` e
`--scenario-output`, che dicevano ancora "non approvato".

## Non rilasciato — ricerca di sequenza con partenze multiple

`optimize_setup_sequence_multistart` / `setup-stages --objective ... --restarts N --seed S`:
la ricerca locale parte dall'ordine per scadenza, da quello per stato e da N perturbazioni
deterministiche (seme fisso) e tiene la migliore; le partenze infattibili sono saltate, ogni
candidato e' ripianificato e auditato, a parita' vince la prima partenza. Costo al massimo
(2 + N) ricerche da `--budget` valutazioni. Mai peggiore della ricerca da una sola partenza;
nessuna garanzia di ottimalita'. Solo per la ricerca in avanti: con `--backward`,
`--task-modes` o `--partial` l'opzione e' rifiutata (non ignorata). Il report ha `multistart`.

## Non rilasciato — report HTML del piano a stadi

`setup-stages --html report.html` (anche con `--partial`, `--backward`, `--replay`) scrive
un report offline senza script: barre di attrezzaggio e lavorazione distinte per risorsa,
prenotazioni, KPI, distanza attrezzaggio→lavorazione del backward, fasi non pianificate
con codice e violazioni dell'audit. Ogni valore e' escapato (anche gli attributi dei
suggerimenti) e il file non puo' sovrascrivere ingressi o altre uscite.

## Non rilasciato — prestazioni del backward

Il backward richiedeva una ripianificazione per ogni tentativo: ora (1) il rilascio piu'
tardo di un setup e il ritardo di una lavorazione sono limitati dai calendari delle loro
risorse (un limite superiore sicuro: la contesa puo' solo anticiparlo), (2) se gia' uno
spostamento minimo (1 s per il setup, 1 minuto per la lavorazione) disturba le altre
prenotazioni non si esplorano gli spostamenti intermedi (euristica prudente: al piu' si
perde uno stringimento, mai la validita'), (3) `scenario_digest` e' memorizzato per
oggetto (scenario immutabile), (4) verificatore e audit completo girano solo sui candidati che
stanno per essere accettati (`schedule_setup_stages(audit=False)` + `verify_staged`); il default
verifica sempre e i risultati sono identici (stesso hash delle prenotazioni). Misure: 80 fasi 26 s -> 11,7 s, 160 fasi 123 s -> 33 s,
320 fasi oltre 400 s -> 141 s. Il piano singolo resta sotto 0,35 s a 320 fasi. I risultati
possono differire leggermente dalla versione precedente (mai peggiori sui KPI, gap
totale uguale o minore sulle prove). Benchmark riproducibile: `tools/stage_bench.py`.

## Non rilasciato — fasi senza risorse candidate (ADR 006, opzione B)

`plan_setup_stages_partial` / `setup-stages --partial [--no-candidate ...]`: si pianifica
cio' che ha candidate e si riporta il resto come non pianificato con codice
(`NO_CANDIDATE`, `PREDECESSOR_UNPLANNED`, `NEST_MEMBER_UNPLANNED`). Compatibile con
`--backward`. Il comportamento di default resta bloccante; nessuna risorsa, durata o
data inventata; piano restante auditato. I setup dinamici restano aperti.

## Non rilasciato — backward nel workbook Excel

Il foglio `objective` (prima sempre rifiutato) accetta righe `kind = politica` con
`planning_direction` (`forward`/`backward`) e `sequence_objective`
(`delivery`/`setup`/`gap`, richiede backward) per il percorso setup/run; ogni altra
riga resta rifiutata con errore. L'evidenza riporta `planning_direction` e
`machine_sequence`. Documentato in `docs/planning-workbook.md`.

## Non rilasciato — backward nel file staged v0.3

Il file JSON dell'anteprima v0.3 accetta `backward` (booleano esplicito) e
`sequence_objective` (`delivery`/`setup`/`gap`, richiede `backward`), che attivano
l'opzione A dopo i gate dei materiali. Nessun cambiamento senza i campi. 

## Non rilasciato — riproducibilità: `setup-stages --replay`

I rilasci derivati dei setup non possono stare nello scenario esportato (i setup
esistono solo come stadi). `--replay REPORT` li ricarica dal report precedente
insieme alla sequenza, ripianifica sullo scenario esportato, confronta **esattamente**
le prenotazioni (`replay_matches`) e rilancia l'audit (codice 2 se differiscono o
se l'audit segnala violazioni). Non si combina con altre opzioni di pianificazione.

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
