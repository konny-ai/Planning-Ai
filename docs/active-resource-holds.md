# Occupazioni di risorse ancora attive all'inizio dell'orizzonte

`ActiveResourceHold(resource_id, observed_at, reserved_until, source_ref, occupying_task_id)`
rappresenta una risorsa **interamente occupata** dallo snapshot iniziale
fino a un istante di rilascio esplicitamente dichiarato. Non si ricava la
fine occupazione dal tempo lavorato o dal vecchio planning.

`apply_full_capacity_holds` richiede risorse a capacità unitaria (1),
una sola prenotazione per ID risorsa, timestamp con offset al secondo,
`observed_at == Scenario.start` e `reserved_until` strettamente
successivo e dentro l'orizzonte. Rifiuta riferimenti mancanti, orari
inesistenti e dati duplicati. Richiede anche l'ID esplicito dell'attività
che occupa la risorsa e **rifiuta il doppio conteggio** se tale attività è
già presente fra i task/operazioni da pianificare nel nuovo Scenario.
L'adapter deve distinguere il lavoro in corso esterno al nuovo orizzonte
dai lavori futuri da programmare.

Per **ciascuna risorsa** occupata costruisce un calendario derivato,
indipendente dai calendari delle altre macchine/operatori/attrezzi anche
quando condividono lo stesso turno. Chiude il calendario derivato da
`Scenario.start` fino a `reserved_until`; il planner non può prenotare
la risorsa in tale intervallo. Le chiusure preesistenti restano valide.

Quando la risorsa è una macchina, il suo stato iniziale di attrezzaggio
viene reimpostato a **sconosciuto** (null): il fatto che sia occupata non
autorizza a supporre lo stato al termine del lavoro in corso. Se la
prossima lavorazione richiede setup, lo scheduler lo ripianifica in
modo conservativo dopo il rilascio, con i bundle dichiarati.

`plan_materials_with_active_holds` inserisce queste chiusure nella
pipeline materiali → setup/run → verifica, mantenendo la distinzione
tra stock netto e stock lordo con impegni espliciti.

## Perimetro non coperto

- La prenotazione occupante è **esterna al piano prodotto**: non vi
  sono consuntivi di quantità o produzione della sua attività.
- Non si riduce il residuo di una fase in esecuzione mentre la risorsa
  è ancora occupata: tale integrazione richiede una rilevazione atomica
  del residuo all'istante di rilascio e un nuovo snapshot del suo stato.
- Per ora non sono ammessi impegni frazionari (es. operatore 50%) o
  risorse con capacità >1. Occorre un contratto di booking percentuale
  storico con audit indipendente.
- Non è ammessa una fine prevista priva di origine attendibile; il
  chiamante deve attestare il timestamp e il riferimento sorgente.
- Non si certificano da qui trigger, dati Factory, contabilità materiali
  consumati, né autorizzazioni a pubblicare.

Il codice e i casi sintetici sono predisposti ma **non testati** per
istruzione dell'utente. Nessun dato ERP viene scritto.
