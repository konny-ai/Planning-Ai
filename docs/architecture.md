# Architettura

Python 3.12+, layout `src`, libreria standard per il motore; su Windows `tzdata` fornisce il database IANA richiesto da `zoneinfo`.

## Moduli

| Modulo | Responsabilità |
|---|---|
| `domain/models.py` | Contratti immutabili di scenario, task e piano |
| `domain/validation.py` | Validazione, collasso nesting, controllo del DAG |
| `domain/durations.py` | Residui con Decimal e policy esplicite |
| `engine/calendars.py` | Espansione turni locali in intervalli UTC, algebra intervalli |
| `engine/booking.py` | Sweep di capacità, intersezione risorse, ricerca senza side effect |
| `engine/scheduler.py` | Target backward, prenotazioni fisse, selezione topologica deterministica |
| `engine/verification.py` | Audit del risultato senza riusare lo stato delle prenotazioni |
| `engine/constraint_chain.py` | Catena dei vincoli per unità e percorso critico a capacità finita, ricostruiti dal piano ([constraint-chain](constraint-chain.md)) |
| `engine/time_fence.py` | Zona congelata tra revisioni dello scenario e verifica ([time-fence](time-fence.md)) |
| `engine/diagnostics.py` | Anticipo da nesting, stabilità, straordinari what-if ([diagnostics](diagnostics.md)) |
| `engine/simulation.py` | Confronto piani, congelamento atomico di inizi/risorse |
| `adapters/json_io.py` | Parsing rigoroso, formati versionati, SHA-256 |
| `adapters/factory.py` | Importazione in lettura da export Factory normalizzato |
| `adapters/store.py` | Snapshot SQLite, revisioni e controllo ottimistico |
| `adapters/reports.py` | CSV e Gantt HTML offline |
| `adapters/http_api.py` | API JSON di sviluppo su loopback |
| `canonical/` | Dati canonici per dominio, associazione delle sorgenti e costruttore di scenario (ADR 004, [canonical-data](canonical-data.md)) |
| `connectors/` | Connettori di sorgente in sola lettura verso i record canonici |
| `adapters/msproject.py` | Export del piano verificato in XML MS Project (MSPDI), attività manuali ([msproject-export](msproject-export.md)) |
| `adapters/service.py` | Servizio di rete autenticato: ruoli, worker isolati, revisioni, registro ([service](service.md)) |

## Pipeline

Lo scenario viene validato e normalizzato in task atomici. I calendari vengono espansi soltanto nell'orizzonte; il DAG produce un ordinamento senza ricorsione. Le stime latest-start vengono propagate all'indietro. Storico e prenotazioni fisse sono riservati prima del lavoro flessibile. Si sceglie poi il task pronto più urgente, si prova ogni bundle e si conferma soltanto la soluzione selezionata.

Il verificatore ricostruisce da zero gli eventi di occupazione del risultato e controlla: identità/digest dello scenario, copertura, membri nesting, bundle, durate, frammenti, calendari, orizzonte, inizi fissi, precedenze e metriche. Un errore interno impedisce la restituzione del piano.

La verifica è indipendente dalla ricerca e dal suo registro delle prenotazioni; condivide il compilatore del dominio e l'espansione dei calendari. Non è quindi un oracolo esterno di equivalenza al legacy.

## Ripetibilità e concorrenza

Oggetti frozen e tuple per gli input e output. Nessuno stato globale del motore. Ogni run ha prenotazioni proprie e non modifica lo scenario. Gli ID stabilizzano gli spareggi; il digest SHA-256 comprende l'intero snapshot serializzato in forma canonica, compreso l'ordine delle liste (riordinare l'input cambia il digest anche quando il piano rimane equivalente).

SQLite usa transazioni `BEGIN IMMEDIATE`, revisioni attese e query parametrizzate. Il piano è salvato solo contro il digest dello scenario attuale. I piani precedenti restano leggibili tramite digest. Il componente non è il salvataggio SQL Server del legacy.

## Distribuzione

L'API HTTP è soltanto locale, senza CORS, con payload massimo 8 MiB. Non è un server di produzione autenticato. Processi di calcolo pesanti vanno isolati in worker con limiti e autenticazione prima di qualsiasi esposizione in rete; nessun deploy pubblico viene eseguito da questo repository.
