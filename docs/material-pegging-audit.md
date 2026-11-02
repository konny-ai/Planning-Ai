# Audit indipendente del pegging materiali (v0.3)

`planning_factory.domain.material_audit.audit_material_pegging` riceve
`Scenario`, `MaterialSupply`, `MaterialNeed` e `MaterialAvailability`
provenienti dallo **stesso snapshot logico**. Non richiama l'allocatore e non
legge dati ERP: ricalcola separatamente somme e controlli di coerenza e
restituisce una tupla di violazioni (vuota = nessuna violazione rilevata).

Controlli coperti:

- rifiuto di ID sorgente duplicati, supply inesistenti, pegs su articolo errato;
- quantità `Decimal` finite e positive; somma dei consumi non superiore alla
  quantità disponibile dichiarata per ogni lotto;
- per ogni task compilato, esattamente un rilascio oppure una carenza;
- niente pegs parziali registrati per un task dichiarato in carenza;
- pegs completi, senza eccesso, per tutti i fabbisogni di un task rilasciato;
- confronto della data di rilascio con l'ultima disponibilità dei lotti
  effettivamente allocati (senza anticipare l'inizio dello scenario).

**Impegni esterni**: il parametro opt-in `committed_by_supply` riceve
una mappa di quantità `Decimal` per ID lotto già riservate altrove.
L'audit controlla che `pegs + impegni <= quantità sorgente`. La PR #124
introduce gli stessi impegni nell'allocatore: l'adapter che integra
entrambi deve aggregare i `MaterialCommitment` per `supply_id` e usare lo
**stesso snapshot**. Se la disponibilità in input è già netta di impegni,
non detrarli una seconda volta.

**Limiti di sicurezza**: senza `committed_by_supply` il verificatore non
può scoprire riserve esterne nascoste. Non misura rendimenti, non controlla
revisioni di distinte e non certifica gli stock fisici. Un esito vuoto
non autorizza mai prenotazioni o pubblicazioni produttive.

La funzione è un controllo **opt-in**. Non sostituisce
`verify_plan`, che verifica le risorse e il tempo del piano, né trasforma
automaticamente le date materiali in prenotazioni. Test sintetici sono
aggiunti come codice ma **non eseguiti** su richiesta del committente.
