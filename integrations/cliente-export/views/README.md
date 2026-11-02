# Otto viste complete per Factory e il workbook

Il connettore Excel versione 2 esegue **una SELECT per ciascuna delle otto
viste configurate**. Non esegue i vecchi batch 00–06, non crea tabelle temporanee
e non legge direttamente tabelle Factory. La logica SQL e i riferimenti alle
tabelle stanno nei file di questa cartella. Il piano legge soltanto l'Excel
prodotto e verificato, compresi i nesting e tutti i loro membri.

| File | Vista | Contenuto |
|---|---|---|
| 00_Perimetro.sql | X_CLIENTE_Planning_Perimetro | Supporto interno: fasi potenzialmente esportabili. Non è un nono ingresso del connettore. |
| 01_Calendari.sql | X_CLIENTE_Planning_Calendari | 8 tipi: modelli, giorni, intervalli, festività, assegnazioni, periodi, eccezioni, assenze. |
| 02_Risorse.sql | X_CLIENTE_Planning_Risorse | 11 tipi: macchine, operatori, fornitori, attrezzature, pallet e abbinamenti, reparti, appartenenze, tecnologie, abilitazioni, sedi magazzini. |
| 03_Commesse.sql | X_CLIENTE_Planning_Commesse | 6 tipi: clienti, commesse, righe ordine, lotti, sedi e stati commesse. |
| 04_Fasi.sql | X_CLIENTE_Planning_Fasi | Una riga per fase: quantità, tempi, requisiti setup/run, date, stato e consuntivi Factory. |
| 05_Cicli.sql | X_CLIENTE_Planning_Cicli | 6 tipi: risorse candidate, blocchi salvati, attrezzature per fase, legami, classi e parametri setup. |
| 06_Nesting.sql | X_CLIENTE_Planning_Nesting | 6 tipi: piani di lavoro e quantità, programmi, membri, quantità nestate e lamiere. |
| 07_Materiali.sql | X_CLIENTE_Planning_Materiali | 8 tipi: componenti, arrivi per lotto, conto lavoro, stock, ordini fornitore, riserve nesting, articoli/unità, residui ordini. |
| 08_Avanzamento.sql | X_CLIENTE_Planning_Avanzamento | Una riga per fase con attività: quantità, tempi attribuiti e ultima attività. |

## Come leggere e modificare una vista

Ogni file contiene prima le SELECT in CTE con nomi italiani e commenti sul
significato dei record. La parte finale allinea le colonne con UNION ALL.
Le colonne non pertinenti a un tipo sono NULL. Questo preserva granularità
diverse senza JOIN che moltiplicherebbero quantità, tempi, lamiere o membri.
Esempio: `SELECT * FROM dbo.X_CLIENTE_Planning_Nesting WHERE TipoRecord=N'Membri'`.

Per cambiare una regola di estrazione si modifica la SELECT del CTE iniziale.
Mantenere TipoRecord, nomi e significato delle colonne. Un nuovo campo o tipo
richiede anche aggiornare `schemas/factory-views-v2.json` e il lettore: valori
fuori contratto producono un errore, non vengono ignorati. I tipi originari
sono conservati per ogni record: un codice numerico e un codice testuale con
zeri iniziali non vengono confusi.

`00_Perimetro.sql` centralizza le esclusioni di commesse chiuse/annullate e
fasi chiuse/annullate. Anagrafiche e programmi nesting conservano anche il
contesto fuori dal perimetro pianificabile; tutti i programmi sono riconciliati.
I filtri operativi CLIENTE e la scelta del programma per fasi condivise mantengono
le decisioni già esplicite del progetto, senza trasformare il vecchio planner
in un oracolo per il nuovo motore.

Le viste calendario conservano i periodi completi. Il trasporto prepara la
finestra richiesta senza cancellare le righe originali dalla provenienza Excel.
Le date locali assumono Europe/Rome. Le percentuali dei requisiti setup/run
sono 0–100 nell'export e diventano percentuali Excel 0–1. Non sono velocità.

## Creazione automatica delle viste mancanti

Nella schermata Fonti dati, il pulsante **Crea viste mancanti** (solo amministratore,
con conferma e password SQL) crea nel database configurato le viste che non
esistono, partendo dai file di questa cartella: prima il perimetro, poi le
otto viste, con i nomi scritti nel pannello. Le viste gia' presenti, anche se
modificate, non vengono toccate; un oggetto non-vista con lo stesso nome viene
segnalato e lasciato com'e'. Serve il permesso CREATE VIEW. Se manca un oggetto
Factory da cui dipendono (tabelle o funzioni non presenti in quella versione di
Factory), la vista viene segnalata con il motivo e le altre vengono create
comunque. Dopo la creazione il pannello verifica le colonne rispetto al contratto.

## Installazione manuale

Applicare i nove file in ordine numerico sul database di destinazione
autorizzato. Creano o aggiornano soltanto oggetti `dbo.X_CLIENTE_Planning_*`.
Prima di aggiornare oggetti già esistenti conservare le definizioni attuali
e confrontarle: le modifiche locali del cliente non vanno sovrascritte.
Non serve modificare tabelle Factory o installare stored procedure.
Concedere al login del connettore SELECT sulle otto viste; le funzioni e le
catene di proprietà devono essere accessibili secondo le regole SQL Server.
Le vecchie `X_CLIENTE_EXPORT_*` rimangono per gli eventuali utilizzatori precedenti,
ma non sono dipendenze delle nuove otto viste.

La configurazione `factory-source.json` contiene solo otto associazioni:
calendari, risorse, commesse, fasi, cicli, nesting, materiali, avanzamento.
La schermata Fonti espone descrizione, dati alimentati, tipi e conteggi.
Il dettaglio di ogni area resta aperto durante gli aggiornamenti automatici.
Il SQL commentato è disponibile in questa cartella come modello di
riferimento: se si cambia la definizione direttamente nel DB, va riportata
anche nel file del progetto. Il server non modifica le viste dalla pagina.

## Copertura e controlli

Il contratto conserva i 34 insiemi dell'export strutturato e i 13 insiemi
prima estratti dalle undici viste e dalle due query integrative: **47 in tutto**.
Le precedenze derivate sono ricostruite dai record, non da query nascoste.
Il foglio Provenienza record conserva anche le righe escluse dal calcolo,
con area e tipo, mentre le tabelle operative del workbook sono gli input
effettivi del piano. Compilare un campo di contesto non attiva automaticamente
un vincolo non supportato: i vincoli operativi incompatibili vengono segnalati.

Le funzioni Factory di consuntivazione e di quantità nestate restano dipendenze
interne documentate delle SELECT. Raggruppare le letture in viste non elimina
automaticamente il costo di quelle funzioni. Il nuovo algoritmo di pianificazione
rimane indipendente dal pianificatore storico.

Nel collaudo locale CLIENTE i 35 insiemi del precedente scenario, comprese le
precedenze, devono essere confrontati record per record con il nuovo export.
Separare questa verifica dal controllo del piano e dall'accettazione industriale.
Le evidenze con dati cliente restano fuori dal repository.
