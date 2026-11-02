# ADR 003 — Attrezzaggio di fase, risorse granulari e stato macchina

**Data:** 21 ottobre 2026. **Stato:** decisione di dominio confermata nelle risposte Q1–Q8 del committente, con dettagli tecnici aperti esplicitati.

## Decisioni confermate

1. **Distinzione fra fasi di attrezzaggio e di lavorazione.** Ogni fase ha requisiti indipendenti per macchine, operatori e attrezzi. La quantità e la **percentuale di occupazione** per risorsa possono variare tra attrezzaggio e lavorazione. Lo screenshot di Factory mostra sezioni distinte per attrezzaggio/lavorazione con N° Macchine, N° Operatori e %; le attrezzature sono una sezione distinta. Il contratto canonico deve poter rappresentare anche attrezzi come risorse.
2. **Tempo unico per fase.** La durata nominale di attrezzaggio è una proprietà della fase, non una matrice obbligatoria di tempi famiglia precedente→successiva. Il tempo **non si ripete quando la macchina è già nello stato richiesto**. Le informazioni per determinare tale stato devono essere dichiarate dall'adapter o dal contesto: non dedurre automaticamente l'uguaglianza da un codice fase o macchina.
3. **Calendari.** L'attrezzaggio può cominciare vicino alla fine del turno e proseguire al turno successivo. Il passaggio di turno non crea un vincolo di atomicità artificiale.
4. **Materiali.** L'attrezzaggio può essere svolto prima della disponibilità del materiale, purché le risorse effettivamente richieste siano disponibili. Non si trasferisce automaticamente il vincolo di arrivo materiale dall'operazione di produzione al setup.
5. **Nesting.** Un programma di taglio/nesting, anche su più commesse e lamiere, ha il suo tempo di taglio complessivo e resta **una sola attività indivisibile** nel motore. Non duplicare durata o setup per commessa membro.
6. **Piani fissati.** Per lavorazioni/attrezzaggi già avviati o fissati la preferenza è **non spostarli**. Applicare il vincolo conservativo di non modificare prenotazioni già consuntivate o esplicitamente bloccate; eventuali eccezioni richiedono ulteriore decisione.
7. **Schema indipendente.** Il contratto canonico è di Planning Factory, non dipende dalla struttura dati Factory: Factory o altri gestionali lo alimentano mediante adapter. Eventuali campi mancanti devono essere diagnostici, non riempiti con valori inventati.
8. **Qualità del piano.** Consegne, ritardo cumulato, numero/tempo di setup, stabilità e saturazione sono obiettivi utilizzabili e configurabili. **Nessun ordine assoluto o peso numerico è stato stabilito**; il motore non deve inventarlo.

## Contratto tecnico del prossimo incremento

- Due bundle *distinti* di utilizzo delle risorse: `setup` e `run`, ciascuno con quantità, tipo (macchina, operatore, attrezzo, altro), identificativi candidate/assegnati e quota percentuale di occupazione. Quando un requisito è positivo ma le candidate sono insufficienti, validazione rigorosa.
- Un identificatore di **stato di attrezzaggio** della macchina esplicito e opaco (dichiarato da sorgente o definito tecnicamente, senza inferirlo dalle sole durate). Stato sconosciuto **non equivale** a stato già pronto; si può saltare il setup solo con corrispondenza verificata.
- Durata di setup statico non deve essere conteggiata due volte: la vecchia v0.2 `Operation.setup_seconds` fa parte del tempo prenotato col bundle di lavorazione; migrazione alla prenotazione distinta richiede un contratto/versionamento esplicito.
- Percentuale occupazione: rappresentazione intera in centesimi di percento o basis point (`10000` = 100%) per evitare arrotondamenti flottanti nella capacità. Il conteggio delle risorse è distinto dalla quota di ogni risorsa.
- Setup già eseguito/bloccato non può essere eliminato retroattivamente dal calcolo; è necessario lo stato reale osservato e storico.
- Nell'algoritmo canonico il setup occupa soltanto le risorse configurate, con disponibilità simultanea e calendari specifici; non va automaticamente considerato sempre impegno macchina al 100%.

## Punti ancora NON definiti dalle risposte

- Se sia possibile fare *preattrezzaggio esterno* mentre la macchina esegue un pezzo precedente; è confermato soltanto che può iniziare senza il materiale.
- Come definire il valore dello stato macchina, come venga rilevato e quanto permanga in presenza di guasti/fermi o modifiche manuali.
- Semantica precisa della `%` sulle risorse Factory (valori vuoti/zero, percentuali di fabbisogno o saturazione). La UI mostra la granularità ma non stabilisce da sola la formula.
- Priorità/pesi numerici, politiche in conflitto con ordini fissati, migrazione dall'attuale durate v0.2.
- Se e come siano conservate nel gestionale le prenotazioni setup separate dalle lavorazioni: la pubblicazione su ERP è fuori dall'incremento finché non è verificato il mapping.

## Evidenza Factory banco A e limiti della prova

Clone 21/10/2026: 158 fasi interne, di cui solo 18 con setup statico positivo (48.600 s sorgente); 188 fasi esterne con durate statiche largamente non imputabili a setup macchina. Le risorse setup dichiarate possono differire dai tempi e da quelle di lavorazione. Questo clone verifica l'integrazione dati, **non** tutte le combinazioni di setup industriale.

## Conseguenze

ADR 003 sostituisce il vecchio assunto sperimentale della matrice di cambio famiglia come regola primaria. L'audit sequenziale v1 resta disponibile **solo opt-in e come euristica esplicitamente diversa**, non è la fonte normativa per la durata dell'attrezzaggio di fase. Nessuna scrittura in produzione e nessun lavoro UI.
