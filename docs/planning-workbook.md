# Excel completo come ingresso del planning

Il formato `planning-workbook-v1` contiene in un solo `.xlsx` gli input di uno
snapshot: dati produttivi, calendari, profilo, politiche, perimetro, provenienza
e correzioni. L'importazione non legge SQL, altri Excel, `descrizioni.json` o
un export precedente. Il file viene letto senza modificarlo. Non pubblica su ERP.

Il contratto comprende **59 tabelle dati, 382 campi**, più un indice navigabile.
Copre tutti i **119 campi delle 22 entità canoniche attuali**. Questo misura la
copertura del contratto comune, non la copertura di ogni funzione di Factory.
Il catalogo è `src/planning_factory/schemas/planning-workbook-v1.json`.
La mappa verificabile dei campi è [workbook-field-coverage.md](workbook-field-coverage.md).

Nell'indice `00 Indice`, il nome di ogni foglio è un collegamento diretto.
Ogni foglio dati ha **Torna all’indice** in A5, all'interno delle righe e colonne
già bloccate: il ritorno rimane visibile anche scorrendo i dati. Sono collegamenti
interni al file, senza macro o file esterni.

## Differenze rispetto agli ingressi esistenti

| Aspetto | CSV/binding o Scenario JSON | Workbook completo |
| --- | --- | --- |
| Dati del modello comune | Disponibili nelle entità canoniche; il binding compone più fonti | Tutti presenti in tabelle nominate nello stesso file |
| Configurazione | Binding, profili, fonti e talvolta memoria separati | Metadati, profili, politiche e correzioni inclusi |
| Lettura cliente | Contratto tecnico, più file | Etichette italiane, indice, filtri, celle modificabili, tempi in minuti |
| Quantità e tempi | Nel modello operativo spesso è già presente il totale | Quantità, tempo per pezzo/ogni N/totale, totale ricalcolato |
| Setup/run | Lo Scenario standard usa lo stesso bundle; esiste anche il percorso tecnico a stadi | Otto valori indipendenti, candidate e presidio nello stesso file |
| Contesto | Dipende dall'export e dai file descrittivi | Clienti, righe, lotti, articoli, unità e provenienza conservati |
| Funzioni del motore | Percorso standard più ampio per storico, fissi e ritroso | Stesse possibilità standard; il percorso setup/run ha limiti specifici sotto |

L'export SQL Factory ricco contiene già conteggi e percentuali separati, tempo
unitario, avanzamento e altri dettagli. Il vecchio adattatore semplifica parte
di queste informazioni prima dello Scenario. Il workbook rende esplicita la
raccolta di entrambe le categorie; non ricostruisce informazioni perse a valle.
Quindi il solo suffisso CSV/Excel non indica quale sorgente sia più completa.

## Uso

```sh
planning-factory workbook-template -o modello.xlsx
planning-factory workbook-export examples/workshop.json -o snapshot.xlsx --now 2026-10-26T07:00:00Z --data-nature simulati
planning-factory workbook-export examples/canonical-files/binding.json -o da-fonti.xlsx --now 2026-10-26T07:00:00Z --data-nature simulati
planning-factory workbook-validate snapshot.xlsx --report controlli.json
planning-factory workbook-plan snapshot.xlsx -o piano.json --scenario-out scenario-effettivo.json --report evidenze.json --html gantt.html
planning-factory web --fixture snapshot.xlsx --port 8766
python tools/build_workbook_demo.py --out out/workbook-demo
```

Il modello vuoto è intenzionalmente **non calcolabile**: date, dati fisici e
copertura dei domini devono essere dichiarati. Un dominio vuoto va distinto da
uno non applicabile motivato e da uno incompleto. Le politiche materiali e di
residuo non vengono inventate durante l'export.

L'export di un binding usa i connettori già configurati e le loro autorizzazioni;
può quindi leggere le fonti quando richiesto dall'operatore. Il risultato è
autonomo. L'export non include automaticamente correzioni conservate in un
database UI esterno. Quelle da utilizzare devono essere nel workbook.

`--factory-source export-ricco.json` allega i record originali come provenienza.
Non genera abilitazioni setup, stato macchina o contratti di presidio. Valori
non nulli di `splitMinDurationSec`, `splitMaxFractions`, `overlapPct` producono
marcatori di vincolo non applicato che bloccano l'importazione.

Gli esiti CLI sono 0 per successo, 1 per input/contratto non valido, 2 per
blocchi di pubblicabilità o piano con attività non collocate. `workbook-validate`
controlla l'input; solo `workbook-plan` esegue il calcolo e l'audit completo.
Piano, scenario effettivo ed evidenze vanno conservati insieme: nel percorso
a stadi lo scenario effettivo contiene setup separati e capacità scalate.

## Come compilare e modificare

Le tabelle Excel native si chiamano `pf_<codice>`: non rinominarle. Si possono
riordinare fogli e colonne usando i codici o le etichette italiane previste.
Inserire nuove righe **dentro la tabella**, così che Excel ne estenda l'intervallo.
Il lettore non interpreta righe sparse fuori dalle tabelle. L'indice è una guida.

Zero, falso, cella vuota e testo `null` sono diversi. Gli ID sono testo. Le
percentuali si digitano come `25%`, non `25`; precisione massima 0,01%.
I tempi sono minuti e devono corrispondere a secondi interi. Date/ore istantanee
in testo richiedono un fuso esplicito; date numeriche Excel usano il fuso dello
scenario. Le date locali e gli orari dei calendari hanno i rispettivi tipi.

Nelle Fasi il tempo nominale totale deriva da quantità e modalità del tempo;
il totale di setup deriva dalla preparazione nominale e dal numero dichiarato.
Il motore ricalcola questi due valori dagli input anche senza cache delle
formule Excel. Altre formule richiedono una cache valida salvata dal foglio di
calcolo. Nessuna formula, macro o collegamento esterno viene eseguito dal lettore.

`input_basis=base_con_correzioni` applica una volta le correzioni attive,
scoped al dataset; `effettivi` conserva lo storico senza riapplicarlo. Anche i
nuovi valori di campi temporali nelle correzioni sono espressi in minuti.
L'età dei dati deriva dalle estrazioni originali, non dalla data di esportazione.

Quando una fase indica un lotto, quantità, unità, rilascio e scadenza mancanti
sono ereditati dal lotto; per la scadenza si considera anche la promessa della
riga commerciale. Un valore esplicito sulla fase prevale. Le precedenze sono
sempre esplicite: la sequenza visualizzata non crea dipendenze.

Materiali: articolo e unità base espliciti, quantità nette utilizzabili e
conversioni dirette dichiarate per articolo. Non si assume che kg, pezzi e
lastre siano intercambiabili. Consuntivi e membri nesting devono usare l'unità
della fase. L'esclusione dei materiali richiede sia una politica coerente sia
un'indicazione `ignore_material` confermata e valida; le carenze restano nelle
evidenze originali.

## Esempio piega e significato delle percentuali

| Parametro | Attrezzaggio | Lavorazione |
| --- | ---: | ---: |
| Numero macchine | 1 | 1 |
| Utilizzo di ciascuna macchina | 100% | 100% |
| Numero operatori | 1, modificabile | 2 |
| Utilizzo di ciascun operatore | 100% | 100% |
| Tempo | 120 min per lotto | 5 min × 200 pezzi = 1.000 min |

Il lavoro dura **16 h 40 min**, il setup **2 h**: totale macchina **18 h 40 min**.
Due persone non raddoppiano né dimezzano automaticamente la durata. Con i
valori sopra il fabbisogno è 2 ore uomo di setup e 33 h 20 min uomo di lavoro.
Le interruzioni e le date effettive dipendono dai calendari.

Una quota operatore 25% occupa un quarto della capacità di **ogni persona**
selezionata e consente condivisione; non cambia il ciclo. Richiede comunque
disponibilità nel turno umano. Per ciclo completamente autonomo: numero 0,
utilizzo 0%, presidio `assente`; il calendario macchina può proseguire di notte.
La percentuale media non descrive interventi puntuali di carico o scarico.

Le candidate di setup e run sono distinte. I conteggi e le quote macchina/uomo
dei requisiti normalizzati derivano dagli otto valori della fase: non esiste
un secondo proprietario da aggiornare. Per un nesting condiviso tutti i membri
concordano sugli otto valori; stadi e preparazione indicano `nest:<id>` e il
tempo comune è contato una sola volta. Senza frequenza dichiarata, preparazione
e totale setup condiviso devono concordare.

È ammesso anche il percorso standard con gli otto valori compilati quando
setup e lavoro richiedono esattamente gli stessi conteggi, quota 100% per ogni
risorsa presente e quota 0% quando il conteggio è zero. Il lettore verifica
ogni alternativa contro il bundle dichiarato. Non servono ordine macchina o
stato di attrezzaggio: viene sempre eseguito il setup nominale. Se un valore
non coincide, occorre il contratto separato setup/run; non si ignora la modifica.

Lo stato iniziale di ogni macchina principale è esplicito e riferito all'inizio
dello scenario; `null` significa sconosciuto. Un setup può saltare soltanto con
stato noto coincidente. `execution_rank` dichiara l'ordine dei task sulle macchine;
non è dedotto dall'ordine delle righe. Non è un ottimizzatore di sequenza.

## Limiti di calcolo e dati conservati

Il percorso standard mantiene le funzioni canoniche esistenti. Il percorso con
risorse distinte setup/run richiede attività attive, modalità avanti, capacità
finite ed efficienza 100%; rifiuta storico, attività fissate, rientri esterni e
altre modalità. Si verificano indipendentemente piano, materiali e capacità a
stadi; ordini e fasi commerciali rimangono riconoscibili anche nei nesting.

I seguenti vincoli, se compilati, **bloccano** il calcolo: trasporti, legami
produttivi per lotti di trasferimento, riserve materiali, impegni del piano
precedente, qualifiche, finestre di intervento, obiettivi aggiuntivi, nuova
composizione identità/precedenza fonti e mappature di stato. È richiesta una
fotografia già composta con identità canoniche. Sono rifiutati anche vincoli
fra sedi diverse, setup ripetuti per riprese/batch, lotti non aperti non risolti
sulle fasi, date materiali ignote e attraversamenti esterni da calcolare.

Clienti, descrizioni, tipo fase, attributi articolo, codici originali e
provenienza sono contesto leggibile, non nuove regole automatiche. Un rientro
esterno già dichiarato è supportato nel percorso standard; spedizione e stato
di conferma sono contesto, non un calcolo di trasporto. Le politiche di qualità
configurabili comprendono il controllo `nesting_required_resources`; accettazioni
di altri controlli non vengono ignorate. Il report espone i controlli canonici.

La Control Room locale legge direttamente il workbook. Per modificarne
parametri, regole o vincoli setup/run si salva l'Excel e si preme **Rileggi Excel**
nella schermata Fonti, poi si ricalcola. È possibile anche riavviare la Control
Room. Se il file cambia su disco, il vecchio snapshot viene rifiutato. Un file
non valido non sostituisce lo snapshot caricato. Le
simulazioni UI che aggirerebbero gli stadi sono bloccate. Non è implementata la
modifica del workbook dal browser. Un piano archiviato o caricato separatamente
non eredita automaticamente certificazione e provenienza del workbook.

Nel servizio autenticato le maschere dati restano consultabili. Le modifiche
agli input e alle regole si effettuano nell'Excel; correzioni e memoria del
vecchio database non vengono applicate in aggiunta. Le impostazioni AI e gli
accessi restano configurazione del servizio, separata dagli input produttivi.
Il salvataggio di una revisione conserva atomicamente scenario, piano,
evidenze e file Excel originale nel database SQLite indicato con `--store`.
La schermata Revisioni permette di scaricare quel file, anche dopo successive
modifiche alla sorgente. Le vecchie revisioni prive di Excel restano leggibili.

## Passaggio da un'istanza esistente

`adapters.workbook_migration.snapshot_tables` materializza i record canonici
effettivi, le politiche originali, la memoria confermata e le correzioni ancora
in vigore. Richiede le unità reali degli articoli materiali: non inventa
conversioni kg/pezzi. Mantiene l'età delle estrazioni e conserva descrizioni,
clienti e contesto delle fasi nel file. Le correzioni già applicate sono
evidenze, senza essere riapplicate al calcolo.

Prima del passaggio si conserva una copia coerente dell'archivio originale e
si confrontano gli scenari e i piani dei due percorsi. Lo storico revocato resta
nella copia dell'istanza; non è un ingresso del nuovo calcolo. Migrare uno
snapshot standard non ricostruisce gli otto requisiti setup/run persi da un
vecchio adattatore, né importa altre restrizioni da un export ERP più ricco.
Il perimetro operativo trasferito deve essere dichiarato nel collaudo.

`tools/start_excel_instance.ps1 -Config <istanza.json>` avvia un'istanza locale
con Excel obbligatorio. La configurazione specifica `python`, `runtime`, `input`,
`store`, `audit` e facoltativamente `port`. Prima verifica il workbook; sostituisce
soltanto un processo Planning Factory riconosciuto sulla porta scelta, avvia
il server nascosto e ne controlla la salute. I vecchi ingressi CSV/JSON e i loro
comandi rimangono disponibili per le altre installazioni.

## Prove riproducibili

Per il flusso operativo Factory → Excel → piano, il confronto con le modifiche
manuali, i conflitti e la configurazione dell'istanza, vedere
[Sincronizzazione Factory](factory-workbook-sync.md). L'Excel rimane l'unico
ingresso produttivo anche dopo l'aggiornamento dalle viste SQL.

`tests/test_planning_workbook.py` e `tests/test_workbook_entrypoints.py` verificano
roundtrip, copertura campi, formule, CLI e HTTP, setup/run, quote concorrenti,
notte non presidiata, nesting multicommesse, materiali, correzioni e rifiuto di
vincoli incompatibili. `tools/build_workbook_demo.py` produce solo dati sintetici.
Non è un collaudo su database Factory né un'accettazione industriale cliente.

Le informazioni sui campi Factory derivano da documentazione, non da letture
live: `<docs-factory>/A_FAS.md`, righe
22–60. Il comportamento concreto dell'export è verificabile nel sorgente
`integrations/factory-export/sql/04_lavorazioni.sql` e nell'adattatore
`integrations/factory-export/tools/to_engine_v02.py`. La scala delle percentuali
va controllata sul contratto dell'export, senza confonderla con il tipo DB.
