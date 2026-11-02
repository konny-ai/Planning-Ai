# ADR 004 — Dati canonici e connettori di sorgente

**Data:** 21 ottobre 2026. **Stato:** indirizzo indicato dal committente il
21/10/2026. Il contratto tecnico qui descritto è una **proposta**, da
dettagliare nelle issue collegate prima dello sviluppo.

## Contesto

Ogni fabbrica tiene gli stessi tipi di dato in sistemi diversi. Per esempio, la data di arrivo del materiale può venire:
- da un AS400;
- da un altro gestionale o MRP;
- da Factory stesso;
- da un foglio compilato a mano.

I programmi di taglio arrivano dal CAM di nesting (Alma o altri). L'avanzamento arriva da Factory, da un MES o da un terminale di reparto.

Oggi l'unico ingresso strutturato è l'export Factory (`integrations/factory-export`,
`adapters/factory.py`). Aggiungere un cliente con fonti diverse richiederebbe
logica dedicata dentro l'adapter Factory, con il rischio di legare il motore
all'origine dei dati.

## Decisione

1. **Gestione interna di tutti i dati di pianificazione.** Planning Factory mantiene un
   modello canonico per dominio di dato (sotto). Il motore legge **solo** il
   modello canonico, mai una sorgente, ed è quindi trasparente rispetto all'origine.
2. **Un connettore per sorgente e dominio.** Un connettore legge una sorgente
   (ERP, CAM, AS400, MES, file) e produce record canonici per uno o più domini.
   Lo stesso dominio può essere alimentato da connettori diversi in installazioni diverse.
3. **Associazione per installazione.** Ogni installazione dichiara in un file di
   configurazione versionato quale connettore alimenta ogni dominio. Collegare un
   nuovo cliente significa scrivere o riusare connettori e configurare
   l'associazione, non modificare motore o dominio.
4. **Il motore non cambia** quando cambia una sorgente. Il costruttore di scenario
   compone i domini canonici nello `Scenario` esistente in modo deterministico.

## Domini canonici

| Dominio | Contenuto | Sorgenti tipiche |
|---|---|---|
| `resources` | macchine, operatori, attrezzi, reparti, sedi, capacità tecniche (spessori, tonnellaggi) | ERP, file |
| `calendars` | turni, festività, eccezioni, fermi e manutenzioni pianificate | ERP, CMMS, file |
| `orders` | commesse, righe, consegne, priorità lotto e cliente, stato bloccato | ERP |
| `routings` | fasi, tempi, setup, precedenze, alternative di risorsa, conto lavoro | ERP |
| `nesting` | programmi di taglio: membri (fase e quantità), durata, setup, macchina, lamiere, stato, versione, anteprima facoltativa | CAM di nesting |
| `materials` | disponibilità e arrivi: giacenze, ordini fornitore, date previste e confermate, lotti, pegging | AS400, ERP, MRP, file |
| `subcontract` | date promesse e consuntivate dei fornitori di conto lavoro | ERP |
| `progress` | consuntivi, pezzi prodotti, fasi avviate e chiuse, stato macchina e stato di attrezzaggio (ADR 003) | ERP, MES, terminali |
| `transport` | collegamenti tra sedi, giorni ammessi, durate (vedi P5 in [parametri](../parametri-motore.md)) | file, ERP |
| `rules` | profili di calcolo e regole con ambito (P20) | archivio interno di Planning Factory |

Uscite, con connettori separati e scrittura autorizzata esplicitamente:
- pubblicazione del piano in ERP;
- export per MS Project, CSV e JSON;
- viste di reparto.

## Contratto del connettore (proposta)

- **Ingresso:** configurazione della sorgente e periodo o orizzonte richiesto.
- **Uscita:**
  - record canonici versionati (JSON Schema per dominio);
  - **provenienza** per ogni record: sorgente, chiave originale, istante di estrazione;
  - **rapporto di validazione**: righe scartate, campi mancanti, riferimenti non risolti.
- **Nessun valore inventato.** Un dato mancante è diagnostica, non un default nascosto (ADR 003 §7).
- **Sola lettura** per definizione. La scrittura è un connettore di uscita distinto.
- **Modalità:** snapshot completo; facoltativamente incrementale o a evento (es. "nesting modificato") come innesco di un ricalcolo.
- **Test:** fixture anonimizzata e test di contratto per ogni connettore. Il motore si testa solo con dati canonici.

## Composizione delle fonti

- **Un proprietario per campo.** Di norma ogni campo di un dominio ha una sola
  sorgente. Se più sorgenti forniscono lo stesso campo (es. data materiale da
  AS400 e da inserimento manuale), la precedenza è dichiarata nell'associazione e
  ogni conflitto finisce nel rapporto.
- **Identità tra sistemi.** Le chiavi di sistemi diversi si collegano con
  tabelle di corrispondenza esplicite: programma CAM ↔ fase ERP ↔ articolo e
  ordine fornitore. I record non abbinati sono riportati, mai abbinati per
  somiglianza.
- **Freschezza.** Ogni dominio ha un'età massima configurata. Dati scaduti
  generano un avviso e rendono il piano non pubblicabile, salvo conferma.
- **Riproducibilità.** Il piano registra:
  - il digest di ogni dominio canonico usato;
  - la provenienza aggregata per sorgente;
  - revisione di profilo e regole.

  Lo stesso insieme di input produce lo stesso piano.

## Connettori generici previsti

| Connettore | Domini | Note |
|---|---|---|
| File (CSV, Excel, JSON) | tutti | Per installazioni senza integrazione e come ripiego manuale |
| Factory | resources, calendars, orders, routings, subcontract, progress, materials | Evoluzione di `integrations/factory-export`: query già pronte, da dividere per dominio |
| CAM di nesting (primo: Alma CAM/Quote) | nesting | Struttura comune agli altri CAM |
| Disponibilità materiale da DB2/AS400 o da SQL generico | materials | Query configurabile e mappatura dichiarata |
| MES / avanzamento web | progress | Polling o evento |

## Conseguenze

- `adapters/factory.py` e `integrations/factory-export` diventano il connettore
  Factory del nuovo contratto. Il comportamento attuale resta finché la migrazione
  non è coperta da test.
- Una verticalizzazione cliente si riduce a quattro elementi:
  - configurazione dell'associazione;
  - eventuali connettori nuovi e riutilizzabili;
  - profilo e regole iniziali;
  - collaudo.
- La diagnostica dei dati mancanti (tempi, materiale, nesting, riferimenti)
  diventa un rapporto unico per tutte le sorgenti, consultabile prima del calcolo.
- Nessuna credenziale nei file di associazione versionati: vanno nei segreti
  dell'installazione.

## Punti aperti

- Formato e posizione del file di associazione per installazione.
- Granularità della freschezza (per dominio o per sorgente).
- Contratto di eventi per il ricalcolo automatico e frequenza massima.
- Se il dominio `nesting` debba trasportare la geometria della lamiera o solo
  un'anteprima prodotta dal CAM.
