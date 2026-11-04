# Contratto canonico degli attrezzaggi di fase (incremento 1, ADR 003)

Questo modello e' **indipendente da Factory ERP**. Lo [schema JSON pubblico e versionato](../contracts/setup-stage-v1.schema.json) definisce la struttura per qualsiasi adapter; e' validato ulteriormente da
`planning_factory.domain.setup_contract.load_setup_contract` e descrive
la configurazione per il futuro scheduling delle prenotazioni separate.

```json
{
  "schema_version": 1,
  "resources": [
    {"id": "LASER_1", "kind": "machine"},
    {"id": "OPERATORE_A", "kind": "operator"},
    {"id": "ATTREZZO_X", "kind": "tool"}
  ],
  "initial_machine_states": {"LASER_1": "SETUP_OLD"},
  "phases": [
    {
      "id": "fase-10",
      "machine_id": "LASER_1",
      "target_state": "SETUP_NEW",
      "setup_seconds": 1800,
      "setup_locked": false,
      "setup_requirements": [
        {"kind": "machine", "count": 1, "occupancy_bps": 10000, "candidate_ids": ["LASER_1"]},
        {"kind": "operator", "count": 1, "occupancy_bps": 5000, "candidate_ids": ["OPERATORE_A"]},
        {"kind": "tool", "count": 1, "occupancy_bps": 10000, "candidate_ids": ["ATTREZZO_X"]}
      ],
      "run_requirements": [
        {"kind": "machine", "count": 1, "occupancy_bps": 10000, "candidate_ids": ["LASER_1"]},
        {"kind": "operator", "count": 1, "occupancy_bps": 2500, "candidate_ids": ["OPERATORE_A"]}
      ]
    }
  ]
}
```

**Semantica dei campi.** `count` indica il numero di risorse distinte richieste
tra le `candidate_ids`, `occupancy_bps` la quota di impegno **di ciascuna**
risorsa selezionata, in unità intere fra 1 e 10000 (10000=100%, 5000=50%).
`setup_requirements` e `run_requirements` sono volutamente diversi.
La percentuale zero con `count>0` e' rifiutata, perché un fabbisogno
positivo con impegno nullo sarebbe ambiguo. Se una fase non richiede una
tipologia, non la elenca. Le candidate non vengono inventate e il catalogo
dichiara esplicitamente macchine/operatori/attrezzi.

`setup_seconds` e' un **tempo nominale unico della fase**. Il contratto
non assume una matrice di durata A→B. `target_state` e'
un identificatore *opaco* e esplicito di predisposizione della macchina:
`resolve_setup_states(contract, ordered_phase_ids)` indica il bisogno
di setup nella sequenza fornita e salta il tempo solo quando lo stato
precedente noto e' identico. Stato `null` significa **sconosciuto**,
quindi non autorizza il salto. La stessa fase puo' ricomparire nella
sequenza se la lavorazione e' intercalata: se nel frattempo la macchina
ha cambiato stato, il nuovo setup e' richiesto.

`setup_locked` segnala una preparazione da preservare, **non** significa
che sia gia' stata eseguita o vada saltata. Lo scheduler di attrezzaggi
dovrà rispettare prenotazioni fissate e consuntivate.

**Confermati dal committente:** il setup puo' proseguire oltre il turno,
non necessita di materiale gia' disponibile, un nesting resta una sola
attivita' di taglio indivisibile. L'avvio di preparazioni esterne mentre la
macchina lavora resta da precisare; nessuna sovrapposizione viene
inventata.

**Limiti di questo incremento:** validazione del contratto e calcolo
dello stato su una sequenza gia' nota **non** equivalgono allo scheduling
di macchina/operatori/attrezzi con percentuali né al congelamento
delle prenotazioni. Il planner v0.2 continua a incorporare
`Operation.setup_seconds` nella durata del task; non gli si deve
aggiungere un secondo booking che faccia doppio conteggio. Il profilo
sperimentale di matrice setup esistente resta opt-in, ma non e' la
regola di business approvata dall'ADR 003.

Il passo successivo e' l'audit indipendente delle occupazioni e la
sostituzione esplicita della componente statica v0.2 con due stage
prenotati separatamente, mantenendo correttezza di nesting, calendari,
consuntivi e convalida SQL clone. I criteri pesati sono configurabili e
non assumono pesi numerici impliciti.

## Audit indipendente dei segmenti di attrezzaggio/lavorazione (incremento 2)

`planning_factory.engine.setup_capacity.audit_stage_capacity` riceve:

- `SetupContract` canonico, `Scenario` con le risorse e i calendari
  già identificati, sequenza esplicita delle attività su ogni macchina;
- una collezione di `StageBooking(phase_id, stage, segments, allocations)`,
  dove `stage` è `setup` o `run` e
  `StageAllocation(resource_id, occupancy_bps)` indica la quota prenotata.

Il verificatore ricostruisce la necessità di setup in base allo stato macchina
esplicito, verifica il numero di candidate selezionate, le percentuali esatte,
la durata di ciascuno stage, calendari e occupazione complessiva (somma delle
quote simultanee fino alla capacità dichiarata). Non permette un nuovo setup
quando lo stato è già coincidente, non presume che il materiale serva al
setup e permette di proseguire un setup al turno successivo. Il nesting si
presenta **una sola volta** con ID `nest:<id>`, non con una riga per
commessa. Le lavorazioni devono seguire le release effettive e non vengono
ammesse attività a macchina diversa da quella dichiarata.

L'audit richiede uno scenario di **sola lavorazione**, con ogni
`Operation.setup_seconds` e `Nesting.setup_seconds` uguale a zero:
questo impedisce di validare erroneamente **due** prenotazioni per il
medesimo attrezzaggio statico v0.2. Gli setup dichiarati come già bloccati
richiedono riferimento a uno snapshot congelato: finché non viene fornito,
l'audit rifiuta la loro ripianificazione anziché spostarli.

### Prenotazioni congelate, precedenze e macchine idonee nell'audit

`audit_stage_capacity(..., frozen_bookings=(...))` accetta un sottoinsieme
esplicito di `StageBooking` già approvati o consuntivati, da conservare
**identici** nei segmenti e nelle allocazioni. Per ogni fase con
`setup_locked=true` che richiede davvero setup, la prenotazione congelata
dello stage `setup` è obbligatoria: il solo flag non prova che
l'attrezzaggio sia stato eseguito. Riferimenti duplicati, sconosciuti,
assenti dal piano corrente e booking alterati producono violazioni; ai
booking congelati si applicano comunque calendari, quantità di risorse,
durata e occupazione. Una prenotazione congelata è input fidato
dell'adapter, non viene ricostruita dal database.

L'audit controlla anche le precedenze **FS/SS** dello Scenario originale,
con i lag, confrontando i soli stage `run` (il setup può iniziare prima):
un booking con capacità valida ma precedenza violata viene rifiutato.
Infine il contratto canonico non può introdurre una macchina che il task
originale non ammette fra le alternative di lavorazione: il controllo è
ripetuto **indipendentemente** nello scheduler a due fasi e nell'audit,
così un catalogo tecnologico v0.3 che abbia escluso una macchina non può
essere aggirato scrivendola in `PhaseSetup.machine_id`. Operatori e
attrezzi possono invece arrivare da bundle canonici dichiarati, perché il
profilo v0.2 può non contenerli tutti. Test di regressione in
`tests/test_setup_capacity.py` (recuperati dalle PR #77, #123 e #127).

Questo verificatore controlla **capacità e tempi degli stage**, non genera
ancora le assegnazioni né sostituisce l'audit completo del piano (precedenze,
consegne, consuntivi). L'esistenza di stage validi non prova che il nuovo
scheduler completo sia stato integrato. Finché non è confermata la
preparazione su banco esterno mentre la macchina lavora, lo schema rifiuta
lo svolgimento del setup di una fase prima della fine della fase precedente
sulla stessa macchina. Per due attività di produzione sulla stessa macchina
la sequenza non può essere intercalata in questo incremento; l'audit di
cambi tra frammenti del vecchio profilo resta separato.

Ogni risorsa è finita e possiede un calendario proprio: macchine,
operatori e attrezzi possono essere usati contemporaneamente fino alla quota
configurata. Una quota del 50% non coincide con mezza persona: richiede un
operatore specifico e ne occupa metà capacità nel periodo, se la politica
di fabbrica consente questa allocazione.

## Schedulazione completa, opt-in e conservativa (incremento 3)

`planning_factory.engine.setup_stage_scheduler.schedule_setup_stages` crea **due
operazioni tecniche distinte** (attrezzaggio e lavorazione) per ogni fase che
richiede setup, oppure la sola lavorazione se la macchina è già configurata.
Usa il motore di capacità finita esistente; converte in unità intere
basis point i fabbisogni percentuali, mantenendo separati macchina, operatori
e attrezzi. Il risultato contiene uno `Scenario` tecnico, un `Plan`
verificato e le `StageBooking` controllate nuovamente dall'audit
indipendente delle percentuali/calendari.

La sequenza per ciascuna macchina viene fornita esplicitamente. La
pianificazione della fase `setup` non attende il materiale, mentre quella
`run` rispetta la release e le dipendenze FS/SS del task originale, con i
lag calcolati sul run e mai sull'inizio del setup. È ammesso
l'attrezzaggio fra più turni; non è ammesso iniziare il setup della fase
successiva mentre la precedente occupa la stessa macchina (preparazione
esterna non ancora autorizzata). Le combinazioni di risorse candidate sono
bounded e rifiutate se superano `max_alternatives` invece di tagliarle
silenziosamente. Ogni programma di nesting è un unico task `nest:<id>`.

`schedule_setup_stages(..., frozen_bookings=(...))` vincola prenotazioni
**integrali a segmento unico** di setup o run, mantenendo istante, durata e
bundle percentuale: le traduce in `fixed_start` e `fixed_alternative` delle
attività tecniche e verifica il risultato con `verify_plan` e
`audit_stage_capacity`. Snapshot con segmenti multipli, fasi sconosciute,
configurazioni macchina incompatibili o attività non rappresentabili come
fisse vengono **rifiutati**. Le precedenze SS sono supportate.

**Limitazioni dichiarate:** non sono implementati il lavoro parzialmente
consuntivato che attraversa il nuovo orizzonte (frammenti già consumati,
durate residue), il conto lavoro, il mapping di pubblicazione ERP e la
migrazione automatica da `Operation.setup_seconds`: non si creano durate
residue o date di completamento inventate. Per questo la funzione si attiva
solo per input di sola lavorazione (`setup_seconds=0` in tutte le
operazioni/nesting), con task attivi e modalità forward; l'ordine macchina
si sceglie con l'incremento 4. Le percentuali sono verificate, non dedotte da
Factory. Lo `StagedSetupResult.plan` è un piano **tecnico di stage**, non
un `Plan` da pubblicare direttamente sulle tabelle Factory.

In fase di integrazione l'adapter deve costruire il nuovo contratto
canonico con dati reali, migrare univocamente i setup statici e
collaudare il ricalcolo su un clone autorizzato **prima** di inserire
questa funzione nei percorsi produttivi.

## Scelta automatica e controllata dell'ordine macchina (incremento 4)

`optimize_setup_sequence(scenario, contract, initial_sequence,
objective='delivery'|'setup'|'flow', max_evaluations=24)` e' una ricerca
locale **opt-in** e a budget limitato. Valuta scambi fra lavori consecutivi
sulla stessa macchina; ogni sequenza candidata viene **ricalcolata da zero**
con `schedule_setup_stages`, `verify_plan` e l'audit indipendente
`audit_stage_capacity`. Vincoli impossibili sono rifiutati.

`objective` e' **obbligatorio** perché non sono stati approvati pesi
universali: `delivery` minimizza lessicograficamente commesse tardive,
secondi di ritardo, setup e makespan; `setup` inizia dal tempo di setup;
`flow` dal makespan. Le metriche misurano le **commesse originali**,
non gli ID tecnici delle due fasi; un nesting contribuisce alle sue
commesse originarie, senza replicare le prenotazioni.

I risultati riportano il piano iniziale e quello selezionato, numero
di alternative valutate e rifiutate e KPI confrontabili. `max_evaluations=1`
conserva esattamente il baseline. La procedura **non** promette l'ottimo
globale, non amplia da sola i profili v0.2 o i casi non supportati,
non esegue attrezzaggio esterno mentre la macchina e' occupata e non
scrive sul gestionale. La ricerca puo' arrestarsi in un minimo locale:
per scenari industriali grandi occorrono ulteriori mosse/euristiche
e benchmark misurati.


## Comando `setup-stages`

```sh
planning-factory setup-stages scenario.json contratto.json -o stadi.json
planning-factory setup-stages scenario.json contratto.json -o stadi.json \
  --sequence fase-10,fase-20 --objective setup --neighbourhood insertion --budget 24
```

Lo scenario deve essere *run-only* (nessun `setup_seconds` v0.2). Senza
`--sequence` si parte da un ordine per scadenza che rispetta le precedenze; le
fasi gia' completate, le scadenze fornitore e gli inizi fissi sono gestiti come
descritto nel CHANGELOG. `stadi.json` contiene sequenza, prenotazioni, KPI e le
eventuali violazioni dell'audit indipendente (codice di uscita 2 se non vuote).
Non scrive mai sugli ingressi e non accede a Factory. Con `--backward --scenario-output adj.json` (ADR 005, opzione A) le
lavorazioni vanno al piu' tardi e ogni attrezzaggio subito prima della sua
lavorazione; il report include i gap residui.

Con `--task-modes --scenario-output adj.json` si rispetta `mode` per task:
`backward` e `auto` (regola `due_distance`, backward se la scadenza e' ad almeno
`auto_backward_days` dall'inizio) ricevono l'opzione A; i task `forward` restano
il piu' presto possibile. Un task backward senza scadenza e la regola `slack`
sono rifiutati. Il report riporta `task_modes` (modo risolto per fase).

Per riprodurre un piano backward da soli file: `planning-factory setup-stages
adj.json contratto.json -o replay.json --replay stadi.json` usa lo scenario
esportato (`adj.json`) e il report precedente (`stadi.json`, che contiene sequenza
e rilasci dei setup), confronta esattamente le prenotazioni e rilancia l'audit.

`--html report.html` aggiunge un report offline (nessuno script, valori escapati) con
le barre di attrezzaggio e lavorazione per risorsa, le prenotazioni, i KPI, la
distanza attrezzaggio→lavorazione del backward, le fasi non pianificate e le
violazioni dell'audit.

`--objective delivery|setup|flow --restarts N --seed S` (N da 0 a 8, solo ricerca in
avanti) ripete la ricerca da piu' partenze deterministiche (ordine per scadenza, per
stato, N perturbazioni con seme fisso) e tiene la migliore; il report riporta
`multistart`. Con `--backward`, `--task-modes` o `--partial` l'opzione e' rifiutata.
