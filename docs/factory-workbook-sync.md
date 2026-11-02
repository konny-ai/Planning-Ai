# Sincronizzazione Factory, Excel e piano

La Control Room avviata da un workbook usa quel solo file come ingresso
produttivo. La sincronizzazione è un connettore che aggiorna il workbook e
installa il piano verificato della stessa fotografia. Non cambia la sorgente
del servizio in CSV e non applica nuovamente le correzioni del vecchio SQLite.

## Uso dalla schermata Fonti

Un amministratore configura server, database, autenticazione Windows o SQL,
driver ODBC 17/18 e le otto viste complete CLIENTE. **Verifica SQL e viste** controlla
collegamento, permessi SELECT e colonne del contratto. **Sincronizza Factory → Excel → Piano** legge
le fonti, confronta le modifiche, genera un candidato privato, valida il
workbook, calcola e verifica il piano. Soltanto dopo sostituisce atomicamente
l'Excel attivo e aggiorna le viste. La password SQL vale per la singola richiesta
e non viene salvata in configurazione, richiesta, audit o log.

Le modifiche al file durante l'estrazione impediscono la pubblicazione. Anche
errori SQL, workbook non valido o piano non verificato mantengono la fotografia
precedente. Prima della sostituzione viene conservata una copia dell'Excel.
Chi tiene aperto il file in Excel deve chiuderlo prima della pubblicazione.

## Modifiche manuali e conflitti

Il confronto usa tre versioni: ultima fotografia Factory, Excel attuale e nuova
fotografia Factory. Un cambiamento solo in Excel resta; uno solo in Factory
entra. Se entrambi cambiano lo stesso campo, o una cancellazione incontra una
modifica, l'aggiornamento si ferma. La schermata mostra i primi cento conflitti
e permette di scaricare il report completo. L'amministratore può scegliere i
valori Excel oppure Factory **per i campi in conflitto**. La scelta non rimuove
le altre modifiche manuali. La risoluzione riusa l'estrazione già fatta.

Profili, regole confermate, correzioni documentate, politiche, requisiti di
stadio e finestre di presidio sono gestiti dal workbook. Conteggi e percentuali
macchina/operatore di setup e lavorazione arrivano da Factory e restano
modificabili con lo stesso confronto. Zero, dato assente e null sono distinti.
Formule personalizzate negli input bloccano la sincronizzazione per evitare
di convertirle silenziosamente in valori; sono ammesse le formule standard dei
tempi ricostruibili dai rispettivi input dichiarati.

## Copertura delle fonti CLIENTE

Il contratto 2 legge soltanto otto viste: calendari, risorse, commesse, fasi,
cicli, nesting, materiali e avanzamento. Una vista di supporto interna definisce
il perimetro comune delle fasi. I 47 tipi di record coprono l'intero precedente
export e le vecchie undici viste, incluse unità e quantità residue degli acquisti.
Non vengono eseguiti batch SQL su tabelle di base o SELECT integrative.
Conserva nel foglio Provenienza tutte le righe delle otto viste, i valori
originali dei requisiti di risorsa e la riconciliazione di ogni programma
nesting, inclusi quelli esclusi dal perimetro. Dati di contesto, quali sedi e
riserve non attivate, non diventano automaticamente nuovi vincoli.

Anagrafica articoli A_PAR per l'unità interna e righe L_OFPA/A_ORF per ordinato
e consegnato sono ora contenute nella vista Materiali. Il connettore non crea
o modifica viste. Conserva anche queste righe nel workbook. Le giacenze
utilizzabili sono giacenza fisica meno impegni, senza riaccredito implicito
delle riserve. Gli ordini fornitore apportano soltanto il residuo positivo
ordinato meno consegnato, alla data dichiarata, nella stessa unità dell'articolo.
Lamiere NR/PZ usano il numero richiesto; lamiere KG usano numero per peso
lamiera dichiarato. Altre conversioni o unità mancanti bloccano l'importazione.
Non vengono creati arrivi simulati o ritardi materiali automatici di sette giorni.

Una regola confermata `ignore_material` continua a consentire il calcolo in
presenza delle sole carenze materiali. Il report espone sia le carenze sia
l'accettazione derivante dalla regola. Non viene dichiarata una disponibilità
inesistente. Il collegamento AS400 richiede un connettore e una mappatura
espliciti: non è implicito nel collegamento a Factory_Cliente.

## Installazione e persistenza

Accanto a plans.sqlite servono `factory-source.json` (server, database, user,
driver, views) e `factory-sync.json` (pipeline_root, bootstrap_baseline).
pipeline_root deve contenere il pacchetto installato e gli script tracciati di
integrations/cliente-export, incluse le viste SQL documentate, e
integrations/factory-export/tools per la conversione del formato di scambio.
Sono necessari pyodbc (installato da `avvia.bat`, oppure `pip install pyodbc`), il driver selezionato e i permessi di lettura sulle
otto viste, con accesso alle rispettive dipendenze SQL. Il test rapido delle viste non sostituisce il collaudo
dell'export completo.

La base iniziale deve essere ricostruita dall'ultima fonte precedente alle
modifiche manuali. Non si assume arbitrariamente che l'Excel corrente sia la
base Factory. Dopo il primo aggiornamento, una riga Fonti registra l'impronta
della nuova base immutabile. Cambiare server/database richiede un'istanza e una
base coerenti, evitando di fondere clienti diversi.

`factory-sync/runs/<id>` conserva richieste senza password, estrazioni, confronto,
piano e report. `factory-sync/baselines` conserva le basi con SHA256;
`factory-sync/history` conserva i workbook precedenti. Sono dati privati cliente.
`plans.sqlite` conserva revisioni, piano, evidenze ed Excel originale quando si
salva una revisione. La configurazione del servizio, gli accessi e le impostazioni
AI restano separati dagli input produttivi. CSV e JSON nelle cartelle dei job
sono artefatti del connettore, non ingressi attivi alternativi.

## Verifica

I test `test_factory_workbook_sync`, `test_factory_sync_http` e
`test_factory_material_sources` coprono confronto, conflitti, autorizzazioni,
pubblicazione, rollback, modifiche concorrenti, quantità e unità materiali.
Il collaudo SQL e browser dell'istanza cliente deve essere registrato fuori
dal repository. La riuscita tecnica non approva automaticamente i filtri di
perimetro o i programmi nesting segnalati dalla riconciliazione.

Per elenco delle viste, tipi di record, installazione e modifica dei CTE,
vedere [le viste SQL del connettore](../integrations/cliente-export/views/README.md).
La schermata mostra solo i nomi effettivamente usati, con descrizione, conteggi
e dettaglio espandibile stabile. Il SQL di riferimento rimane nella cartella
delle viste. I percorsi CSV nei job restano interscambi privati;
non sono sorgenti alternative del sistema in esecuzione.

Riferimenti di schema consultati via Factory Knowledge, distinti da osservazioni
SQL live: `<docs-factory>/A_PAR.md` (PAUDM),
`<docs-factory>/L_OFPA.md` (PAUDM, PAQTA, PAQCO),
`<docs-factory>/L_MLPA.md` (QTLOC) e
`<docs-factory>/L_PAQT.md` (PAIMP).
