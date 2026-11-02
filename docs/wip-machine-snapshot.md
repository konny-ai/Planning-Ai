# Consuntivi parziali e fotografia degli stati macchina (ADR 003)

Incremento **opt-in, conservativo** del dominio. La funzione
`prepare_paused_wip` non legge Factory e non calcola implicitamente
tempi mancanti: riceve osservazioni e progressi con riferimenti di
provenienza espliciti. `plan_residual_wip_materials_and_setup` collega
poi la fotografia validata a allocazione materiali, scheduler e audit.

## Contratto esplicito

- `MachineObservation(machine_id, observed_at, state_id, source_ref,
  idle_confirmed)`: stato macchina **osservato** all'istante iniziale
  dello scenario. `state_id=None` significa sconosciuto, non pronto.
  Ogni macchina canonica richiede un'osservazione allo stesso istante.
- `PausedRunProgress(task_id, machine_id, original_seconds,
  worked_seconds, remaining_seconds, paused_at, source_ref)`: attività
  già avviata, **fermata e rilasciata** prima dell'inizio della nuova
  pianificazione. Il totale deve coincidere con la durata originale e
  `worked + remaining == original` esattamente.
- `WipPreparation` conserva il nuovo Scenario immutabile, il contratto
  setup con stati iniziali ricavati dalle osservazioni, e gli ID
  dei task con residuo esplicito.

Il task in corso viene ricondotto al suo residuo e deve essere il
**primo** sulla macchina indicata nel nuovo ordine per tale macchina.
L'osservazione deve confermare sia macchina libera sia stato di setup
corrispondente al target. Non si deduce lo stato da durata, ID fase,
vecchio Gantt o stato di un lavoro non attestato.

Per `nest:ID` il residuo è **un solo tempo condiviso**: si modifica la
durata del nesting, non si distribuisce o replica il residuo tra i
membri/commesse. Non si altera in-place lo Scenario sorgente.

## Integrazione con materiali e verifica

`plan_residual_wip_materials_and_setup(...)` applica la normalizzazione
conservativa e poi riutilizza la pipeline con audit:
materiali → release → attrezzaggio/lavorazione → audit del piano.

Per ogni scenario con progressi WIP, il chiamante deve dichiarare
`residual_material_requirements_confirmed=True` e passare fabbisogni
**residui reali**. Senza questa conferma la pipeline rifiuta la richiesta,
per evitare di prenotare di nuovo quanto già consumato. Continuano a
valere le due modalità esclusive delle scorte (nette oppure lorde più
impegni esterni), le prenotazioni congelate integrali e il digest dello
Scenario per il pegging.

## Casi non ancora automatizzabili

- **Macchina ancora occupata** all'inizio orizzonte: `idle_confirmed=False`
  fallisce. Occorrono booking/frazioni di occupazione storici e
  prospettici espliciti per bloccare davvero tutte le risorse.
- **Setup parzialmente completato**: non è lecito scalare il tempo
  nominale senza consuntivi specifici di setup e stato configurazione.
- **Stato iniziale sconosciuto o vecchio**: non si assume mai che il setup
  sia già stato eseguito o preservato.
- **Cambio risorsa/macchina durante l'esecuzione**: non si inventano
  permessi di trasferimento o nuove combinazioni tecniche.
- **Progressi di sequenze già intercalate**: servono evidenze che
  attestino lo stato della macchina dopo le lavorazioni intermedie.

La conferma `idle_confirmed` e i `source_ref` sono **affermazioni del
sistema sorgente**, non prove verificate automaticamente con ERP.
L'adapter deve garantirne autenticità e coerenza di snapshot. Le
osservazioni e i progressi non costituiscono autorizzazione alla
pubblicazione su database di produzione.

Casi sintetici nel repository **predisposti, non eseguiti** per
indicazione dell'utente. La PR resta in bozza fino alla verifica.

## Verifica file-only banco A: backup SQL rimosso

Il backup SQL Server banco A `Factory_BANCOA_2026-10-20.bak`
è stato rimosso dalla release privata per richiesta esplicita del
committente. Il vecchio workflow clone SQL (PR dati-di-test #11)
è stato chiuso senza merge ed **è superato**: non cercare di
scaricare o ripristinare il file eliminato.

Il banco ordinario è `tools/file_only_bench.py`, con dataset
JSON/CSV canonici banco A anonimizzati già nel repository.
Per la pipeline avanzata opt-in, `planning-factory web --fixture
scenario.json --v03-spec stage-spec.json` richiede **attestazioni
per singolo scenario** sullo stock e sugli stati macchina.
Guida: [v03-staged-file-preview.md](v03-staged-file-preview.md).

`integrations/factory-export/tools/wip_adapter_readiness.py`
resta un *inventario diagnostico* read-only dei campi di un'eventuale
vecchia estrazione, **non** crea automaticamente `MachineObservation`,
`PausedRunProgress`, stock o impegni. Un report di readiness
privo delle evidenze as-of non prova che macchine e risorse siano
libere allo snapshot. Per banco A senza quelle fonti,
la pagina setup/run avanzata resta intenzionalmente disabilitata.

I test sintetici v0.3 sono predisposti ma NON eseguiti su richiesta
del committente; nessun workflow GitHub Actions o accesso
desktop/ERP/SQL è stato avviato in questa attività.
