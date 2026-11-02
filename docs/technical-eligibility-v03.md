# Vincoli tecnologici su risorse alternative — v0.3

`planning_factory.domain.technology` introduce un catalogo tipizzato,
revisionato e indipendente dall'ERP. Per ogni task compilato `job:...`
o `nest:...` una `TechnicalNeed` descrive il valore misurato e la sua
unita'. Le `MachineCapability` descrivono intervalli chiusi, per
parametro/unità e macchina. Il confronto ha effetto **prima** della
ricerca degli slot disponibili.

```python
from decimal import Decimal
from planning_factory.domain.technology import (
    TechnologyCatalog, TechnicalNeed, MachineCapability,
    assess_technology, apply_technical_limits,
)
catalog = TechnologyCatalog(
    revision="2026-10-r1",
    needs=(TechnicalNeed("job:taglio", "SPESSORE", Decimal("8"), "mm"),),
    capabilities=(
        MachineCapability("LASER1", "SPESSORE", Decimal("1"), Decimal("6"), "mm"),
        MachineCapability("LASER2", "SPESSORE", Decimal("1"), Decimal("12"), "mm"),
    ),
)
report = assess_technology(scenario, catalog)
restricted_scenario = apply_technical_limits(scenario, catalog)
```

L'esempio elimina `LASER1` dai bundle del task, se la lavorazione richiede
8 mm: una macchina tecnologicamente incapace non è candidata nemmeno se
ha tutto il turno disponibile. Il caso di **dato macchina mancante**,
revisione mancante, valori decimali non finiti, limiti invertiti o unità
diverse è respinto; non sono applicate conversioni automatiche di unità,
ricette implicite o limiti inventati.

Ogni alternativa (bundle) che contiene più macchine deve soddisfare le
esigenze tecniche su ciascuna macchina. Le percentuali/capacità e la
compatibilità degli utensili restano modelli separati in evoluzione.
`assess_technology` produce un elenco degli indici delle alternative
accettate e delle cause precise del rifiuto. `apply_technical_limits`
restituisce **uno scenario nuovo**; non modifica l'originale.
I membri di un nesting condividono la selezione del task indivisibile.
Le fasi già fissate richiedono riconciliazione dedicata, non vengono
silenziosamente riallocate.

Questa regola si attiva **solo quando sono dichiarate le esigenze
tecniche**: l'assenza di dichiarazione non certifica la macchina, e
l'adapter dovrà segnalare gli attributi attesi ma mancanti. I cataloghi
Factory di grandezze e parametri macchina possono alimentare il modello,
ma il mapping e la gestione delle versioni richiedono prove con dati
attendibili. Non è incluso un linguaggio arbitrario di formule: prima
occorrono regole documentate e una sandbox sicura.

**Non cambia il planner di default né scrive sull'ERP.**
