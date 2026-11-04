# Pipeline opt-in: materiali → attrezzaggi → audit

`planning_factory.engine.material_stage_pipeline.plan_materials_and_setup_stages`
compone **nella stessa chiamata**, senza accesso all'ERP:

1. Allocazione `allocate_materials` con priorità dei task esplicita, anche
   per i nesting atomici (un'unica allocazione per `nest:ID`).
2. Audit indipendente del pegging con `audit_material_pegging`.
3. Stop in presenza di carenze, anziché pianificare fasi prive di materiale.
4. Nuovo `Scenario` immutabile con `apply_material_releases`: la release
   dei task viene ritardata solo quanto necessario.
5. Scheduling setup/run con `schedule_setup_stages`: lo **setup non aspetta
   il materiale**; il run rispetta la release e i precedenti vincoli supportati.
6. Verifica aggiuntiva di `verify_plan` e `audit_stage_capacity`.
   Il risultato `MaterialStagedPlan` conserva sorgente, allocazione, scenario
   adattato e piano tecnico.

## Una sola convenzione contabile per le scorte

L'API accetta **due modalità alternative**:

- **Scorte già nette:** `supply_quantities_net_of_external_commitments=True`
  con `external_commitments=None` (default). Le `MaterialSupply.quantity`
  devono già escludere le riserve ERP; l'allocatore non le sottrae ancora.
- **Scorte lorde:** `external_commitments=(MaterialCommitment(...), ...)`
  con il flag net lasciato a `False`. L'allocatore sottrae per ID di lotto
  gli impegni noti, l'audit indipendente controlla la disuguaglianza
  `pegged + committed <= gross stock`. La tupla esplicitamente vuota è
  un'attestazione del chiamante di non avere impegni esterni nello snapshot,
  non una deduzione del motore.

Se entrambe o nessuna delle modalità sono dichiarate, la pipeline **rifiuta**
la richiesta. L'adapter deve garantire coerenza temporale, unità e provenienza
delle disponibilità e delle prenotazioni dallo stesso snapshot. Un flag o una
tupla vuota non prova da soli la bontà dei dati sorgente.

## Collegamento alle prenotazioni congelate

Il parametro opzionale `frozen_bookings` consente di fornire setup e run
già fissati **integralmente**, ciascuno con un solo segmento. La pipeline
applica prima le disponibilità materiali, poi fa rispettare gli istanti
e le risorse di questi booking allo scheduler tecnico; l'audit finale li
confronta nuovamente per identità esatta.

Se un booking `run` già fissato precede la nuova data di disponibilità
del materiale, il piano viene **rifiutato**: non viene anticipato il
materiale né spostata automaticamente la lavorazione. Per i consuntivi
parziali (durate residue e prenotazioni già consumate) questo contratto
resta intenzionalmente non rappresentativo: occorre uno snapshot di
avanzamento validato dal sistema sorgente.

## Vincoli consolidati da implementazioni precedenti

Il risultato `MaterialAvailability` conserva l'impronta SHA-256 dello
`Scenario` originale; `apply_material_releases` e
`audit_material_pegging` rifiutano allocation generate per una revisione
diversa, anche se gli ID delle attività coincidono (PR #76).

Il percorso setup/run conserva ora le precedenze **FS e SS** sui soli run
(PR #125), mentre lo scheduler e l'audit rifiutano macchine indicate nel
contratto canonico ma non ammesse dalle alternative tecniche originali
(PR #77). Questi vincoli valgono anche con `frozen_bookings`.

Un lotto di produzione non può riferirsi a una riga cliente di un altro
articolo (PR #76). Lo snapshot dei materiali, il profilo setup e le
prenotazioni congelate devono comunque provenire da fonti coerenti: il
motore non dichiara approvato alcun mapping ERP autonomamente.

## Confini non negoziabili

- Nessuna scrittura ERP, nessun download, nessuna deduzione di giacenze.
- Non esegue MRP completo: acquisti, revisioni BOM, WIP e consuntivi restano
  regolati dai rispettivi contratti.
- Utilizza il percorso stage conservativo (task forward); i booking
  congelati integrali sono ora accettati, mentre lavori parzialmente
  consuntivati e storico non trasformato restano esclusi.
- `MaterialStagedPlan.staged.plan` è un piano **tecnico**, non un output
  pubblicabile direttamente sulle tabelle Factory.
- Test sintetici aggiunti ma **non eseguiti** su richiesta del committente;
  ogni PR rimane bozza senza merge fino alle verifiche necessarie.


## Backward (ADR 005, opzione A)

Con `backward=True` la pipeline, dopo aver allocato e auditato i materiali,
porta le lavorazioni al piu' tardi e gli attrezzaggi subito prima di esse
(`backward_setup_before_run`). Si ritarda soltanto: nessun rilascio scende
sotto quello imposto dai materiali (controllo esplicito, altrimenti errore).
`sequence_objective` (`delivery`, `setup`, `gap`, richiede `backward=True`)
cerca la sequenza valutando ogni candidato sul proprio piano backward; la
sequenza usata e il risultato con i gap sono in `MaterialStagedPlan`.
Le prenotazioni congelate (`frozen_bookings`) sono supportate: restano
identiche, non vengono mai ritardate e limitano lo spazio dei vicini; l'audit le
ricontrolla esattamente. `just_in_time_tail`, `backward_setup_before_run` e
`optimize_backward_sequence` accettano lo stesso parametro.
