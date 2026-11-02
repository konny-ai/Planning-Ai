# Indicazioni per gli agenti

Leggere README, docs/requirements.md, docs/architecture.md e docs/roadmap.md prima di modificare il motore.

Rispettare la decisione di prodotto `docs/decisions/002-independent-factory-planning.md`: FSMo_SCH e' esclusivamente una fonte storica e non un obiettivo di parita' o un test-oracolo. Non introdurre imitazioni dei suoi difetti, ordinamenti o punteggi per migliorare una metrica golden. Le metriche di prodotto devono riflettere bisogni industriali espliciti.

Aggiornamento esplicito 22/10/2026: il committente ha **autorizzato lo sviluppo della UI/UX web** dopo il lavoro sul motore. Usare il PDF di riferimento v4 come riferimento di interazione e linguaggio visuale, ma distinguere il cliente di produzione lamiera da banco A (filtri): nessun nesting CAM o geometria va dedotto dagli scenari CSV banco A. UI e backend devono usare Scenario/domini canonici, con simulazioni separate da pubblicazione ERP. Evitare backend esposti senza autenticazione e non presentare come verificato codice ancora in PR draft.

Rispettare anche `docs/decisions/003-phase-setup-capacity-state.md`: setup per fase con richieste di macchina/operatore/attrezzo indipendenti dalla lavorazione, percentuali configurabili e salto del setup solo su stato macchina esplicitamente coincidente. Non sostituire questa regola con una matrice di transizioni inventata.

Non trattare le decisioni aperte come requisiti approvati. Distinguere ipotesi e comportamenti verificati. Non eseguire i binari legacy né caricare dati privati nel repository.

Preservare il confine fra dominio, engine e adapter. Ogni piano deve essere verificabile indipendentemente dal suo algoritmo. I nesting multipli devono mantenere i legami con tutte le operazioni coinvolte senza duplicare la durata condivisa.

Verificare il codice con i controlli applicabili e riportare le limitazioni. Non dichiarare implementate funzionalità presenti soltanto nella roadmap.

Aggiornamento 23/10/2026: il committente ha chiesto integrazione, avvio e prove riproducibili dello stack CLIENTE. I test locali sono autorizzati. Mantenere separati dati sintetici, prove HTTP, QA browser, collaudo SQL su clone e accettazione industriale. Non presentare trasporti/provider simulati come collegamenti Claude live.
