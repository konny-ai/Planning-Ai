# Audit indipendente degli attrezzaggi di sequenza

Questo controllo **non modifica il piano**: verifica che un piano gia' valido
abbia, tra due lavori consecutivi sulla stessa macchina, abbastanza tempo
di calendario macchina per il cambio attrezzaggio dichiarato. Non chiama
Factory e non afferma di riprodurre la logica Giostra o i setup legacy.

## Contratto esplicito

Formato JSON della specifica di audit, versione 1:

```json
{
  "schema_version": 1,
  "families": {"a": "ACCIAIO", "b": "ALLUMINIO"},
  "machines": [{
    "resource_id": "m",
    "initial_family": "ACCIAIO",
    "transitions": [{
      "from_family": "ACCIAIO",
      "to_family": "ALLUMINIO",
      "seconds": 3600
    }]
  }]
}
```

`families` collega gli ID operazione originali alla famiglia setup.
Il nesting rimane un task indivisibile: tutti i suoi membri che lavorano
su una macchina controllata devono appartenere alla stessa famiglia.
`machines` definisce l'insieme di macchine da verificare; ogni macchina
deve avere capacita' esattamente 1 e tipo `machine`. L'`initial_family`
e' obbligatoria: descrive lo stato macchina dichiarato all'inizio
dell'orizzonte, NON inferito dallo storico.

Ogni coppia direzionale di famiglie distinte incontrata nel piano deve
essere esplicitamente nella matrice, anche se il cambio dura zero secondi.
Per scelta documentata, famiglia invariata non necessita attrezzaggio.
La specifica rifiuta duplicati, ID inesistenti, secondi negativi o frazionari,
campi imprevisti e transizioni duplicate. Le macchine non nominate
restano fuori dall'ambito del controllo.

Un cambio viene soddisfatto solo se il **tempo lavorabile del calendario
macchina** tra fine lavoro precedente e inizio del successivo e'
almeno pari a `seconds`. Notte, pause, festivi e chiusure non
contribuiscono. Se la macchina parte dalla famiglia iniziale dichiarata,
il primo setup deve essere contenuto nell'intervallo lavorabile prima
dell'inizio del primo lavoro. Il report indica deficit e provenienza.
Le operazioni **interrompibili** sono ammesse. I frammenti consecutivi
dello stesso task mantengono la famiglia di attrezzaggio anche tra turni.
Se invece un'altra lavorazione si interpone, il ritorno al task originale
richiede un **nuovo cambio di famiglia**: un ordine A-B-A produce tre
sequenze macchina, comprese entrambe le transizioni A->B e B->A. Ogni
cambio richiede una matrice esplicita, anche quando dura zero secondi.
Il tempo disponibile per quel cambio parte dalla **fine del frammento
precedente**, non dalla fine complessiva dell'operazione interrompibile.
Il report include `predecessor_end` per permettere una verifica puntuale
e la riparazione della reale sequenza. Le prenotazioni vengono prima
verificate dal normale audit della capacità.

I secondi di setup dinamico sono un **vincolo di intervallo libero
aggiuntivo** e non vanno confusi con il campo preesistente
`Operation.setup_seconds`, che e' gia' parte della durata prenotata.
Non usare simultaneamente le due rappresentazioni per lo stesso cambio
senza una decisione esplicita sul dato.

## CLI ed esiti

```sh
planning-factory schedule scenario.json -o plan.json
planning-factory audit-setups scenario.json plan.json setup-rules.json -o setup-report.json
```

Codici: **0** controllo passato; **2** un piano valido presenta uno o piu'
intervalli insufficienti; **1** scenario, piano o matrice non validi.
Il report JSON contiene `valid`, `partial_plan`, le macchine coperte,
tutte le transizioni verificate e quelle insufficienti. Il rapporto
controlla solo le fasi effettivamente pianificate se il piano e' parziale.
Non scrive mai su database o sulle sorgenti di input.

## Ambito e limiti

Per ora e' un **audit di sicurezza e fattibilita' degli attrezzaggi**,
non un algoritmo di prenotazione: la schedulazione standard e la
ricerca opt-in delle politiche continuano a produrre piani secondo il
contratto storico v0.2 e potrebbero non soddisfare questo vincolo.
La ricerca con cambi attrezzaggio e alternative dichiarate e' opt-in.
I cambi tra frammenti intercalati vengono controllati rispetto ai gap
lavorabili: non vengono ancora prenotati come attivita' autonoma e la
ricerca a rilasci monotoni resta euristica e limitata dal budget.
Non sono supportati cambi macchina nel corso dello stesso task,
capacita' macchina multiple o attrezzaggi che richiedano risorse
aggiuntive e un ciclo operativo proprio.

## Proiezione di prenotazioni macchina (contratto provvisorio, issue #53)

L'utility **opt-in** `tools/project_setup_bookings.py` puo' ora
materializzare intervalli di attrezzaggio che occuperebbero **una sola macchina
a capacita' 1** gia' controllata dall'audit. La durata viene inserita nei
gap lavorabili liberi del **piano gia' valido**: nessuna durata e' inventata,
nessun intervallo collide con lavorazioni, altri setup o chiusure calendario.
La proiezione e' verificata indipendentemente con
`verify_setup_projection` e rappresentata in un JSON separato.

Sono parametri **obbligatori**: `--placement earliest|latest` e
`--split-policy atomic|splittable`. Nessuna scelta di officina viene
data per scontata. La modalita' `atomic` richiede un intervallo contiguo:
un gap di 3 ore distribuito su 2 turni non e' una prenotazione atomica di 3
ore. La modalita' `splittable` puo' occupare piu' segmenti del calendario,
sempre per la stessa macchina.

```sh
python tools/project_setup_bookings.py scenario.json plan.json setup-rules.json \
  --placement latest --split-policy atomic --output projected-setups.json
```

**Attenzione:** il report e' una *proiezione verificabile*, **non** il piano
riprogrammato con setup prenotati durante la ricerca, **non** un booking ERP
e **non** una conferma che in fabbrica basti la sola macchina.
Operatori/attrezzisti, preattrezzaggio, esecuzione del setup, stato corrente
della macchina e regole nesting richiedono risposte alle Q1–Q8 del
[questionario issue #53](decisions/QUESTIONARIO-ATTREZZAGGI-ISSUE-53.md).
Se il piano non contiene un gap valido, il comando fallisce senza indicare
una falsa prenotazione. La pianificazione standard non cambia.
