# Adattatore Factory in lettura

`adapters.factory.import_snapshot(template, snapshot, progress_policy="none", zero_phase_policy="reject")` importa un'esportazione NORMALIZZATA. Il template fornisce orizzonte, calendari, risorse, settings e nesting; le operazioni sono sostituite. Il componente non apre una connessione al database e non esegue SQL.

Il caller deve predisporre:

- `phases`: righe con `CONUM`, `LOCOD`, `IDFAS`, `FASEQ`, `FAHLV`, `FASLV`, `FAHAT`, `FASAT`, `LAQTP`, `resource_bundles`.
- `lots`, opzionale: righe con esattamente `CONUM`, `LOCOD`, `LOCLP`.
- `links`, opzionale: `CONUM`, `LOCOD`, `IDFAS`, `Prev_CONUM`, `Prev_LOCOD`, `Prev_IDFAS`, `LinkType`, piu `lag_seconds` intero opzionale (default 0) per preservare i ponti attraverso fasi non attive.

`resource_bundles` è una lista di liste di codici risorsa, già risolta dal sistema sorgente. Ogni bundle richiede una unità per risorsa; domande con altre cardinalità vanno costruite direttamente nel contratto Scenario. Non vengono indovinate associazioni compatibili da `L_LARE`, flag RITPU, corrente sessione o MACOD.

Tempo unitario = `FAHLV * 3600 + FASLV`; setup = `FAHAT * 3600 + FASAT`; il lavoro teorico è unitario × `LAQTP`. I decimali vengono conservati e arrotondati per eccesso sul residuo finale. Le funzioni `ComputeJobWorkTime*` NON sono usate come durata teorica.

Campi opzionali: `LOPRI`, `LAPRI`, `CustomerPriority`, `CMDTS`, `release`, `mode`, `splittable`, `fixed_start`, `fixed_alternative`, `produced_quantity`, `recorded_work_seconds`, `recorded_setup_seconds`.

`CMDTS` deve essere già risolta alla consegna del lotto finale; `release` può includere disponibilità materiali o forniture. `LOPRI` alimenta la priorità lotto, `LAPRI` la correzione fase additiva. La raffinazione ricorsiva legacy non viene emulata.

`progress_policy`: none = teorico; quantity = pezzi ancora da produrre; time = teorico meno consuntivo del lavoro. Il setup residuo sottrae il consuntivo setup in quantity/time. Un tempo zero non certifica il completamento: normalizzare esplicitamente lo storico nel contratto principale. Il parametro **opt-in** `zero_phase_policy="skip"` consente a uno scenario di sperimentazione di omettere le fasi a lavoro residuo nullo (inclusi placeholder e prelievi) conservando le altre, senza inventare una data di completamento. I link espliciti che attraversano fasi omesse sono ricollegati alle fasi attive conservando il tipo FS/SS a monte e sommando i lag. Le precedenze implicite per gruppi FASEQ e lotti vengono ricostruite dalle sole fasi attive. **Non equivale al comportamento storico:** se una fase a durata nulla rappresenta una milestone vincolante, va modellata con un evento/storico esplicito prima del rilascio operativo. Il default rimane il rifiuto sicuro.

Le fasi sono collegate per gruppi di `FASEQ` crescenti; fasi con la stessa sequenza restano parallele, e tutte precedono il gruppo seguente. Il figlio deve terminare prima della prima fase del più vicino antenato attivo. `LOCLP` zero o uguale a `LOCOD` identifica una radice. Cicli e padri mancanti nell'export sono errori. `LOCLM` non viene usato come padre.

`LinkType` 0/2 → FS; 1 → SS. Riferimenti a fasi fuori snapshot sono errori espliciti anche con lo skip; i cicli espliciti raggiunti da fasi attive vengono respinti. Il connettore cliente dovrà includere lo storico o normalizzare queste dipendenze prima dell'importazione.

## Blocco dell'integrazione live

Servono uno snapshot autorizzato delle viste e delle tabelle, la configurazione effettiva del cliente e fixture di banco per:

1. risolvere calendari `A_CAL/A_TCL/L_TCTG/A_INT/A_ECC/A_GDF` e variazioni;
2. tradurre risorse e associazioni, cardinalità, occupazioni, forzature ed esclusioni;
3. leggere piani `A_NES`, gruppi e tempi dalle funzioni cliente;
4. normalizzare stati e avanzamento, sessioni aperte e consegne fornitori;
5. ~~definire la pubblicazione transazionale e il controllo di concorrenza~~: `publish_factory_plan.py` (README dell'export) pubblica in un'unica transazione con controllo `LASTO`/`LASTUPDATE`/`L_LARE`. È provato su SQL Server con schema sintetico (CI) e sul clone banco A con backup del 20/10/2026: run 39440561563, 769 fasi e 721 blocchi inseriti, `--verify` senza differenze. Resta da autorizzare nel collaudo (#3) l'uso sul database del cliente e da verificarne i trigger effettivi.

Il motore attuale lavora già su input normalizzati; nessuna credenziale aziendale è richiesta per provarlo con l'esempio sintetico.

## Date promesse del conto lavoro (profilo sperimentale)

Il contratto `Operation.external_end` rappresenta una **data promessa di fine** comunicata dal fornitore: non e' una consuntivazione `completed_at`, non richiede capacita' macchina/operatore e puo' essere anche precedente all'orizzonte. Si usa esclusivamente per un'operazione a durata residua 0, senza alternative, release, inizio fisso o nesting; l'audit indipendente verifica che non siano registrati segmenti o prenotazioni e che la data coincida. I successori rispettano la data promessa nei vincoli FS/SS; una contraddizione con predecessori gia' pianificati e' un errore esplicito.

Il convertitore SQL→engine mantiene per default `--subcontract-policy resources`. Solo con `--subcontract-policy anchored` raccoglie le date `promisedDate` dalle righe aperte di `subcontractOrders`, prende il massimo per lavorazione e preserva l'istante locale `Europe/Rome` di `PADCR`; aggiunge `external_end` sia all'engine JSON sia allo snapshot Factory. Come FSMo_SCH con `CheckJobDeliveryDate=1`, ancora solo la prima fase esterna del lotto, in ordine FASEQ/IDFAS, che abbia un fornitore tra le candidate; lo fa solo se la lavorazione è pianificata in avanti, con la stessa soglia `auto_backward_days` del motore. Le altre fasi esterne con promessa restano a risorse e il report le conta per motivo. La modalità automatica segue `SogliaGGTipoPian` come il legacy: a ritroso se CMDTS ≥ inizio + `--auto-backward-days`, confrontando le date. Con `--delivery-margin-days N` (`GiorniMargineConsegna`) la consegna del motore anticipa CMDTS di N giorni, come la fine delle lavorazioni a ritroso nel legacy. La soglia scritta nello scenario tiene conto del margine. Con `--progress-policy time` lavoro e setup sono ridotti dell'avanzamento a tempi del legacy: il massimo dei rapporti consuntivo/teorico per macchine e operatori. Le fasi completamente consuntivate escono come quelle a durata zero, ricollegando le precedenze. Il default `none` non legge i consuntivi. Senza data promessa conserva il percorso a risorse e lo segnala nel report. Date promesse mancanti, inaccurate o obsolete non vengono inventate o considerate prove di lavorazione eseguita. **La comparazione del golden e' una misura di scarto, non equivalenza certificata.**

`import_snapshot` accetta una fase `external_end` con `resource_bundles=[]`; mantiene le fasi normali e l'opzione esplicita per zero-work. Il comportamento del profilo di default e le fixture del benchmark esistente non cambiano.
