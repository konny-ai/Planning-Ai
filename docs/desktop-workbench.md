# Workbench desktop offline (prima interfaccia)

Avvio:

```sh
planning-factory desktop
# Oppure: python -m planning_factory desktop
```

Richiede Python 3.12+ e **Tkinter con un display grafico** disponibile.
Su Windows normalmente Tkinter e' incluso nell'installazione standard
Python; in ambienti Linux minimi potrebbe richiedere il pacchetto tkinter
fornito dalla distribuzione. In ambienti headless il comando restituisce
un errore chiaro, senza tentare connessioni di rete.

Questa interfaccia e' un **editor JSON di scenari**, non un gestionale completo
con anagrafiche, utenti, autenticazione o database Factory.

Operazioni:
- Aprire un file scenario JSON o compilarlo/modificarlo nel campo di testo;
- Salvare lo scenario dopo la verifica rigorosa dei dati;
- Validare dati e calendari prima di pianificare;
- Pianificare con la strategia standard, provare le quattro politiche
  con `Ottimizza`, o avviare la ricerca locale con budget predefinito 8;
- Esportare un piano JSON, un Gantt HTML offline filtrabile o un CSV;
- Pianificare con attrezzaggi espliciti selezionando il file JSON delle regole: il workbench sostituisce il testo con lo scenario aggiornato, che deve essere salvato insieme al piano.

La finestra riporta il numero di task assegnati e non collocati, nonche'
la strategia risultante quando pertinente. Tutte le funzioni di calcolo
usano la stessa libreria e lo stesso verificatore indipendente della CLI.

Per evitare risultati obsoleti, se il testo dello scenario cambia dopo
il calcolo, un report non puo' essere esportato finche' il piano non viene
ricalcolato. Prima di esportare si ricontrolla il digest SHA-256 e si
riesegue `verify_plan`. Il caricamento di testo e' limitato a 8 MiB.

Il workbench opera interamente sul computer, salva solamente i file
richiesti dall'utente, non avvia server, non usa rete e non scrive dati ERP.

## Limiti

La prima UI permette di modificare **il JSON**, non di aggiungere operazioni
attraverso form grafici dedicati. Non effettua drag-and-drop nel Gantt,
non implementa utenti/ruoli e non sostituisce un collaudo con dati cliente.
Le prove automatiche verificano il controller indipendentemente dal
display; la disposizione visiva della finestra richiede ancora collaudo
manuale su macchina desktop.
