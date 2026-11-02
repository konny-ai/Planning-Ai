# KPI di qualita' del piano (indipendenti dal planner storico)

`assess_quality(scenario, plan)` genera indicatori deterministici **solo dopo**
`verify_plan` indipendente (tramite `measure`). Non usa il Gantt FSMo_SCH
e non impone pesi o soglie che la fabbrica non ha ancora concordato.

```sh
planning-factory schedule scenario.json -o plan.json
python tools/plan_quality.py scenario.json plan.json --out quality.json
# Sul piano della schedulazione standard: ripete il motore e confronta il JSON
python tools/plan_quality.py scenario.json plan.json --out quality.json --check-reproduction
```

Il report JSON contiene:
- impronta SHA-256 del piano (solo come fingerprint tecnico);
- task e operazioni pianificate/non pianificate, con cause aggregate;
- commesse complete/incomplete, complete con data promessa, puntuali,
  ritardatarie, secondi totali di ritardo e livello di servizio;
- makespan in secondi dal principio dell'orizzonte all'ultima lavorazione
  con segmenti, **non** una durata dei lavori esterni pre-orizzonte;
- carico e disponibilita' per tipo di risorsa, utilizzo pesato e le prime
  cinque risorse finite piu' occupate.

**Coorti di consegna:** vengono riportate separatamente le commesse
complete con data di consegna **precedente all'inizio dell'orizzonte**,
**all'interno dell'orizzonte** e **successiva alla fine**. Una commessa
con consegna gia' trascorsa all'inizio del planning non puo' diventare
puntuale per effetto della nuova schedulazione. Per questo, accanto al
livello di servizio globale, il report fornisce
`service_level_due_from_start_percent`, calcolato solo sulle commesse
complete con data di consegna uguale o successiva all'inizio dell'orizzonte.
Non e' una prova di fattibilita' della singola consegna e non esclude la
responsabilita' operativa delle commesse pregresse.

**Denominatori:** il livello di servizio conta solo commesse **complete con
una consegna definita**; le commesse incomplete non sono erroneamente
considerate puntuali. Le capacita' infinite sono escluse dal rapporto di
utilizzo; quando una categoria contiene risorse infinite il suo utilizzo
aggregato viene lasciato `null`. Il tempo disponibile nullo non produce
divisioni per zero.

`--check-reproduction` ripete `plan(scenario)` senza opzioni e richiede
identita' esatta dei byte canonici del piano: e' un gate di **determinismo**
del comando `schedule`, non si applica a piani generati con politiche o
ottimizzazioni diverse. Se il piano non supera l'audit il comando fallisce,
senza emettere un report di successo.

Questi KPI costituiscono una **linea di misura**, non certificano un piano
ottimo ne' una soglia industriale concordata. La priorita' relativa tra
ritardi, utilizzo, setup e stabilita' del piano richiede criteri espliciti
del committente (ADR 002). Ogni modifica dell'algoritmo puo' riportare
prima/dopo, sempre sullo stesso scenario, senza introdurre un oracolo legacy.

## Copertura degli attrezzaggi statici

`static_setup_coverage` registra quanti task **effettivamente prenotati**
contengono `setup_seconds > 0` e i relativi secondi nominali. I
nesting multicommesse sono contati come **un singolo programma** e
`Nesting.setup_seconds` sostituisce i setup dei membri: non vengono
duplicati i tempi. Le operazioni non pianificate o solo storiche non sono
conteggiate come lavoro prenotato.

Questa misura descrive esclusivamente il **setup statico incluso nella
durata v0.2**, non verifica la matrice direzionale di attrezzaggio
`famiglia precedente -> successiva` e non ne misura i tempi.
`sequence_dependent_setup_assessed` e' quindi **false** per un piano
senza matrice esplicita. La distinzione e' essenziale per leggere i
benchmark banco A, che potrebbero avere pochi setup.
Non sommare questi secondi alle prenotazioni dinamiche senza
una regola di deduplicazione approvata dal committente (issue #53).

## Stabilita' fra due pianificazioni

La funzione gia' presente `planning_factory.engine.simulation.compare(before, after)`
restituisce ora anche il numero di operazioni assegnate confrontate, quante
cambiano o restano uguali, quante cambiano bundle di risorsa o struttura dei
frammenti, la somma degli spostamenti assoluti di inizio e il massimo
spostamento assoluto. Una lavorazione puo' mantenere **lo stesso inizio e la
stessa fine** ma cambiare gli intervalli intermedi: questo conta come modifica,
perche' altera la disponibilita' macchina/operatori dentro il piano.

Le statistiche sono calcolate sugli ID di operazione con assegnazioni presenti
in almeno uno dei due piani; le operazioni ancora non pianificate vengono
riportate separatamente nei conteggi `before_unscheduled` e
`after_unscheduled`. Per un nesting che comprende due commesse, entrambi gli
ID di operazione sono rappresentati, anche se i segmenti condivisi occupano
la capacita' **una sola volta**. Gli spostamenti senza inizio confrontabile
non vengono artificialmente valorizzati a zero nei singoli record.

E' una metrica descrittiva, non un nuovo obiettivo o una soglia industriale
imposta al motore. La scelta del peso da assegnare alla stabilita' richiede
una decisione della fabbrica (ADR 002).
