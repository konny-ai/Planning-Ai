# Planning Factory — Decisioni produttive per gli attrezzaggi (issue #53)

**Data:** 21 ottobre 2026
**Stato:** risposte del committente ricevute il 21/10/2026. Interpretazione normativa in [ADR 003](003-phase-setup-capacity-state.md); le sole ambiguità residue sono marcate come aperte.
**Perimetro:** solo motore, nessuna interfaccia. Nessuna equivalenza con FSMo_SCH.
**Nota:** le risposte approvate sono qui trascritte senza inventare pesi, formule o stati macchina.

## Q1 — Cosa impegna un attrezzaggio?
L'attrezzaggio occupa **solo la macchina**, oppure anche **uno o più operatori**, o può occupare **una risorsa diversa dalla macchina** (es. attrezzista, carrello)?
- **Risposta:** macchina, operatori e attrezzi, con quantità e percentuale di occupazione variabili e distinti fra attrezzaggio e lavorazione (vedi screenshot Factory fornito).
- **Esempio:** su una macchina laser, chi cambia l'utensile e quali risorse rimangono indisponibili?

## Q2 — L'attrezzaggio può essere interrotto?
Può iniziare a fine turno e riprendere al turno successivo, oppure deve essere eseguito senza interruzioni una volta iniziato? Vale la stessa regola per macchine diverse?
- **Risposta:** può iniziare anche a fine turno; il cambio turno non è un vincolo di atomicità, quindi la preparazione può proseguire quando le risorse tornano disponibili.

## Q3 — Quando può iniziare?
Un attrezzaggio per il prossimo lavoro può iniziare **prima che arrivi il materiale**? Può essere preparato **mentre la macchina lavora sul pezzo precedente** (attrezzaggio esterno), oppure solo quando la macchina è libera?
- **Risposta:** sì, può essere eseguito senza materiale. Non è stata ancora confermata la contemporaneità con una precedente lavorazione che occupi la stessa macchina.

## Q4 — Quali fattori cambiano la durata?
Dipende da sequenza **famiglia precedente → famiglia successiva**, macchina, spessore, materiale, utensile, operatore o una combinazione? Avete **tempi reali o tabelle** da cui partire?
- **Risposta:** il tempo di attrezzaggio è unico e definito sulla fase. Se la macchina risulta già attrezzata per il lavoro, il setup non va ricalcolato. Non si introduce una matrice obbligatoria A→B.
- **Esempio di due cambi con durate differenti:**

## Q5 — Più lamiere/commesse nello stesso nesting
Un nesting multiplo rimane **un'unica attività indivisibile**; al suo interno possono esistere cambi di attrezzaggio da pianificare separatamente (p.es. fra lamiere), oppure tali tempi sono già compresi nel programma di taglio/nesting?
- **Risposta:** il nesting dispone del tempo di taglio complessivo, resta unico e indivisibile.

## Q6 — Attività avviate, bloccate e ripianificazione
Se una lavorazione è già iniziata o fissata, è consentito **spostare il suo attrezzaggio**? Dobbiamo fissare anche attrezzaggi già effettuati e lo **stato corrente della macchina**?
- **Risposta:** in linea di massima no: le attività già fissate o avviate non vanno spostate. Da precisare eventuali eccezioni.

## Q7 — Dove si reperiscono i dati?
Gli attrezzaggi e i consuntivi sono già memorizzati in **Factory ERP**? Se sì, quali schermate/tabelle/campi li distinguono dai tempi ciclo e dai tempi preparazione generici? In assenza di dati, siete d'accordo a **non inventare durate** e a lasciare l'operazione diagnostica fino a compilazione?
- **Risposta:** i dati sono già in Factory; occorre definire prima il contratto canonico del nostro motore, che potrà poi essere popolato da Factory o altri ERP.

## Q8 — Priorità industriali
Quali obiettivi devono pesare di più: **consegne puntuali, minimizzare ritardo totale, ridurre il numero di attrezzaggi, stabilità del piano**, occupazione delle risorse? Se cambia per cliente/reparto, indicarlo; i pesi numerici saranno configurabili.
- **Risposta:** approvata la proposta multi-obiettivo configurabile. I pesi numerici e l'ordine delle priorità non sono ancora stabiliti.

## Ulteriori chiarimenti per completare il motore (non ancora approvati)

### Q9 — Come si riconosce una macchina già attrezzata?
Quale campo o combinazione di parametri (classe/gruppo/valore, programma,
utensile, ecc.) identifica **esattamente la configurazione** da mantenere?
Da quale dato di Factory o della rilevazione operatore ricaviamo lo
**stato attuale** della macchina? Uno stato non noto non autorizza uno skip.
- **Risposta:** da definire.

### Q10 — Preattrezzaggio a macchina occupata
Hai confermato che il setup può precedere la disponibilità materiale.
Può anche essere eseguito su un banco esterno **mentre la macchina sta
ancora lavorando sulla fase precedente**, usando soltanto un attrezzista/
attrezzo, oppure il setup del lavoro successivo deve attendere la
liberazione della macchina?
- **Risposta:** da definire.

## Sviluppo conseguente e aspetti da precisare

1. Consolidare i test su calendario macchina, attrezzaggio e vincoli simultanei.
2. Produrre un contratto **proposto** per prenotazioni setup, separato dalle durate statiche già presenti, senza dichiararlo regola approvata.
3. Introdurre diagnostica verificabile su tempi di cambio e dati mancanti.
4. Misurare banco A sul **clone** e su scenari sintetici ricchi di attrezzaggi. Dato che banco A può averne pochi, non usarlo da solo per certificare la funzione.
5. Non modificare la produzione, non modificare i dati Factory ERP, non cambiare il piano standard finché le regole non sono confermate.

## Decisioni già confermate

- Il nuovo motore è indipendente da FSMo_SCH (ADR 002).
- Il nesting multicommesse predefinito è una singola attività atomica di planning.
- I vincoli vanno verificati indipendentemente dall'algoritmo.
- L'interfaccia è rinviata.
