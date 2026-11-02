# Modello produttivo canonico v0.3 — primo nucleo eseguibile

Il modulo `planning_factory.domain.production_flow` definisce **anagrafiche
articolo**, righe ordine cliente, lotti, distinte multilivello, fabbisogni e
forniture disponibili. E' indipendente da Factory ERP, dalla UI e da
FSMo_SCH. Si usa assieme allo `Scenario` esistente; non e' ancora una
migrazione di tutto il contratto scenario in produzione.

## Distinte e commesse

- `Item(id, unit, supply_mode=make|buy)` rappresenta il catalogo.
- `BomLine(parent_id, component_id, quantity_per_parent)` descrive una
  relazione padre-componente e un coefficiente `Decimal` positivo.
- `CustomerLine` conserva consegna e quantità di ciascuna riga.
- `ProductionLot` collega più righe cliente a un lotto e alle sue
  operazioni, senza imporre che un ordine sia un lotto.
- `validate_production` rifiuta cicli, duplicate, riferimenti mancanti,
  quantità non finite/negative o lotti senza fasi.
- `explode_bom` espande **fabbisogni lordi** sui componenti, inclusi
  intermedi da produrre. Non detrae stock, non calcola rendimenti o
  arrotondamenti di confezionamento e non crea ordini di acquisto.
  L'unità dell'articolo è esplicita e va mantenuta coerente dagli adapter.

## Pegging deterministico dei materiali

`allocate_materials(scenario, supplies, needs, task_priority)` usa lotti
identificati di magazzino/acquisto (`MaterialSupply`) e quantità richieste
dai task compilati (`MaterialNeed`, ID `job:...` o `nest:...`).
`task_priority` è **fornito** dal caller: non viene inventato un criterio
commerciale predefinito.

Per ogni attività assegna le quantità dai lotti della stessa materia in
ordine `available_at, id`, fino a coprire integralmente il fabbisogno.
Un'attività insufficiente è riportata nelle `shortages` e non consuma
nemmeno parzialmente stock che può servire alla successiva. Il risultato
contiene date `releases` e pegs tracciabili per fonte e quantità. Più
attività **non possono spendere due volte lo stesso lotto**.

`apply_material_releases` restituisce **un nuovo Scenario** con release
non antecedenti alla disponibilità dei materiali e non cancella le
release preesistenti. In presenza di insufficienza fallisce invece di
pianificare una fase impossibile. Un nesting usa una sola allocazione di
materiale a livello di task e la stessa data di rilascio viene applicata
ai membri senza duplicare la quantità.

### Allocazioni legate allo Scenario e impegni esterni

Ogni `MaterialAvailability` porta l'impronta SHA-256 dello **Scenario
originale** usato nell'allocazione: applicare pegs e date a un altro
Scenario, anche con gli stessi ID attività, viene rifiutato. Se cambiano
quantità, durate, release, ordini, risorse o calendari il pegging va
**ricalcolato**, non riutilizzato. Un `ProductionLot` non può coprire righe
cliente di un articolo diverso da quello che produce; una quantità prodotta
superiore alla richiesta resta una decisione di processo, non una quantità
consumabile inventata.

`allocate_materials(..., commitments=(MaterialCommitment(supply_id, quantity), ...))`
accetta le quantità **già impegnate altrove** secondo uno snapshot sorgente
verificato. Ogni impegno fa riferimento a un `MaterialSupply.id`, si somma
per lotto e viene **sottratto prima** dell'allocazione; impegni sconosciuti,
non positivi o superiori al lotto sono rifiutati invece di produrre stock
negativo. `commitments=()` non certifica che il lotto sia libero: significa
solo che non sono stati comunicati impegni. L'adapter deve prendere
disponibilità e impegni dallo stesso snapshot e non sottrarre due volte
quantità già nette (vedi [materials-staged-pipeline.md](materials-staged-pipeline.md)).
Nessuna riserva ERP viene creata o modificata. Test di regressione in
`tests/test_production_flow.py` (recuperati dalle PR #76 e #124).

### Confini prima del rilascio

Questa è una base per i fabbisogni, **non** ancora un sistema completo di
MRP: manca il netting temporale su ordini aperti e stock già impegnato
nell'ERP, gli sfridi, i rendimenti, le unità convertibili, i sostituti,
la tracciabilità del lotto materiale nelle prenotazioni ERP e il
scheduling di acquisto/produzione degli intermedi. Non usare i pegs per
promettere materiale che Factory ha già riservato altrove senza
riconciliazione. Le date sono stime da disponibilità dichiarate, non
consuntivi. La readiness del setup resta separata da quella del run:
il setup può precedere l'arrivo materiale (ADR 003).

## Prove

`python -m unittest tests.test_production_flow -v` comprende cicli di
distinta, multipli ordini/lotto, quantità decimali, approvvigionamenti in
date diverse, carenza e rollback, nesting atomici, task fantasma e
preservazione di release pregresse. CI Linux e Windows.
