# Estrazione da Factory (sola lettura)

Script che producono gli scenari da un database Factory (nel repository non sono distribuiti dati reali). L'estrazione non esegue scritture: solo `SELECT` e tabelle temporanee `#U_*` sulla connessione. L'unico strumento che scrive è `publish_factory_plan.py` (sotto), e solo con `--execute`.

| Percorso | Ruolo |
|---|---|
| `sql/00_universo.sql` … `06_vincoli_esterni.sql` | Query per area: universo, calendari, risorse, ordini/lotti, fasi, candidate L_LARE, vincoli esterni |
| `tools/export_factory_scenario.py` | Esegue le query, deriva le precedenze di ciclo e distinta e scrive `scenario.json` + `validation.md` (schema `planning-scenario.schema.json`) |
| `tools/to_engine_v02.py` | Converte `scenario.json` nel contratto `Scenario` del motore e nello snapshot per `import_snapshot`; `--anonymize` ricodifica gli identificativi |
| `tools/anonymize_scenario.py` | Anonimizza lo `scenario.json` completo con la stessa mappa |
| `tools/anonymize_bench.py` | Anonimizza il golden legacy (`jobs.tsv`) |
| `tools/compare_bench.py` | Confronta un piano del motore con il golden FSMo_SCH |
| `tools/publish_factory_plan.py` | Pubblica il piano in Factory con le scritture del Salva legacy: genera `publish.sql` e `publish-report.md`; con `--execute` esegue su un clone, con `--verify` rilegge |

Requisiti: Python 3.12, `pyodbc`, ODBC Driver 17/18 for SQL Server, utente con SELECT ed EXECUTE sulle funzioni lette. Per `publish_factory_plan.py --execute` l'utente deve avere anche:
- `SELECT`/`UPDATE` su `A_LAV`, `L_LARE`, `A_LOT` e `A_COM`;
- `SELECT`/`DELETE`/`INSERT` su `L_LAPN`;
- i permessi richiesti dai trigger Factory su quelle tabelle, per esempio il numeratore `GetProgressivo` per `L_LAPN.IDINT`.

Le sole letture di `--verify` richiedono `SELECT`. I dati non anonimizzati vanno in `data/private/` (ignorata da Git).

```sh
python tools/export_factory_scenario.py --database <DB> --start 2026-10-19 --horizon-days 365 --out ../../data/private/<cliente> --customer-view
python tools/to_engine_v02.py ../../data/private/<cliente>/scenario.json --out ../../data/private/<cliente>/engine
```

## Fasi senza candidate risorsa: controllo obbligatorio

Con `to_engine_v02.py` la politica predefinita
`--missing-resource-policy fail` **rifiuta** le fasi che dichiarano un
fabbisogno positivo di macchina/operatore (`runMachines`/`runOperators`)
ma non hanno sufficienti candidate, **anche quando mancano tutte le righe
`L_LARE`**. Non si puo' dichiarare un piano fattibile usando
`NO_RESOURCE` al posto di una macchina obbligatoria non identificata.

`--missing-resource-policy available` e' un'opzione **esplicita di
esplorazione**: in caso di assenza totale di candidate usa `NO_RESOURCE`
illimitata e segnala il numero di ripieghi nel report; con candidate parziali
abbassa il fabbisogno a quello disponibile. Non utilizzare la conversione
permissiva per accettare un piano operativo senza una decisione specifica sulle
risorse mancanti. Le fasi che **dichiarano zero risorse richieste** possono
legittimamente usare la risorsa virtuale, che resta un modello aperto da
convalidare per il singolo processo.

## Pubblicazione del piano (Salva)

`publish_factory_plan.py` riceve lo `scenario.json` **non anonimizzato** e il `plan.json` calcolato sullo scenario convertito, e riproduce le scritture del Salva di FSMo_SCH (RE `docs/21` §21.4-21.7):

1. `DELETE L_LAPN` **solo delle fasi ripubblicate**, preservando i blocchi delle altre fasi della commessa (incluse quelle filtrate dall'estrazione).
2. Per ogni fase:
   - `UPDATE A_LAV` con `FPIAN='Y'` e le date pianificate;
   - `UPDATE L_LARE` con `RIUSE` e `IDBLK` su ogni risorsa candidata;
   - `INSERT L_LARE` (`RIUSE='Y'`, `IDLARE=0` numerato dal trigger) per la risorsa usata quando è un candidato aggiunto da una regola cliente senza riga in `L_LARE` (`assignmentId` 0, es. CLIENTE: abilitati al tipo fase). I candidati da regola non usati non vengono scritti.
3. `UPDATE A_LOT` e `UPDATE A_COM`: date ricalcolate da **tutte** le fasi già presenti nel database, comprese quelle non estratte e non ripianificate. `COPNF` è aggiornato solo se sono ripubblicate tutte le fasi della commessa; altrimenti mantiene il valore precedente.
4. `INSERT L_LAPN`: un blocco per ogni segmento e ogni risorsa usata, con `IDINT=0` numerato dal trigger.

Casi particolari, come nel Salva catturato sul banco HF:
- **Esterne ancorate alla data promessa:** il fornitore dell'ordine di conto lavoro (o il primo ammesso in `L_LARE`) è `RIUSE='Y'`, con un blocco di durata zero alla data. Le esterne il cui fornitore non è in `L_LARE` ricevono solo le date.
- **Fasi dell'universo fuori piano** (durata zero, `FAPNF='N'`, consuntivate) diventano milestone con inizio uguale alla fine:
  - alla fine del predecessore più tardivo, anche attraverso catene di milestone;
  - altrimenti all'inizio del successore più precoce;
  - se isolate, all'inizio della pianificazione.
- **Nesting** (`--engine-scenario engine-scenario-nesting.json`, lo scenario con i programmi prodotto da `build_scenario_cliente.py nesting`, con le stesse fasi della conversione): ogni fase membro riceve le date e gli stessi blocchi del programma, con `IDPTC` = programma A_NES e `RIPOC` = impiego / numero di membri, come nei nesting salvati dal legacy (es. 4 membri → 25%).

Collaudo CLIENTE (23/10/2026) sul clone `Factory_Cliente_Collaudo`, ripristinato dal backup del 02/05/2026:
- piano senza nesting: 7.174 fasi, 7.878 righe `L_LARE` aggiornate e 683 inserite (+683 nel database), 3.352 blocchi, 3.401 lotti, 343 commesse; `--verify` senza differenze. Ripetere lo stesso script viene rifiutato (50001) senza scritture;
- ripubblicazione con i nesting, dopo una nuova estrazione dal clone: 297 programmi, 3.604 blocchi (3.110 con `IDPTC`), `--verify` senza differenze; i membri di un programma hanno gli stessi orari, macchina e operatore e `RIPOC` ripartito (es. 3 membri → 33,33%).
- Attenzione: la prima pubblicazione **senza** nesting aveva fissato in `L_LARE` un operatore diverso per membri dello stesso programma; alla riestrazione i nesting ricostruibili sono scesi da 330 a 297 (bundle comune perso). Con i nesting conviene pubblicare sempre il piano con i nesting.

Sul banco l'anteprima pubblica 769 fasi, 600 lotti e 126 commesse, gli stessi insiemi del Salva legacy. Con le date legacy delle fasi pianificate, la regola delle milestone riproduce esattamente 110 delle 149 milestone collegate.

Lo script è **un'unica transazione** (`XACT_ABORT`). Prima di scrivere, sotto lock, controlla che `LASTO`/`LASTUPDATE` delle fasi e le righe `L_LARE` siano ancora quelle dell'estrazione. Se qualcosa è cambiato annulla tutto (errori 50001/50002) e bisogna ripetere estrazione e pianificazione.

Differenze volute rispetto al legacy:
- **Commit:** una sola transazione, invece di un commit per statement.
- **Colonne caricate:** non le riscrive, quindi `LADTS`/`LASCH`, `L_CMPA.PASCH/PADTS`, tempi e `FAPNF` restano quelli dell'utente.
- **Legami e macchine:** non ricrea `JobPrevious` e non tocca `A_MAC.IDCAT`.
- **Milestone isolate:** il legacy ne colloca alcune su istanti di calendario non ricostruiti; qui vanno all'inizio della pianificazione.

```sh
python tools/publish_factory_plan.py scenario.json motore/plan.json --engine-dir motore --out pubblicazione   # solo script e report
python tools/publish_factory_plan.py scenario.json motore/plan.json --engine-dir motore --out pubblicazione --execute --verify   --database <CLONE> --confirm-database <CLONE> [--server ... --user ... --password ... --driver ...]
```

`--engine-dir` è la cartella di `to_engine_v02.py`: il piano deve avere il digest del suo `engine-scenario.json` e `conversion.json` deve riportare l'impronta (SHA-256) dello `scenario.json`, quindi un piano non si pubblica con un'estrazione diversa. Il controllo sotto lock confronta anche risorsa, regola e percentuale delle righe `L_LARE`. Un errore di comunicazione rende l'esito incerto: prima di ripetere, `--verify` da solo rilegge il database. `--execute` richiede che `--confirm-database` coincida con `--database` e che lo scenario sia stato estratto dallo stesso database. Fino alla decisione del collaudo (#3) va usato **solo su cloni**:
- il workflow *Snapshot Factory* del repository dei dati, opzione `publish_clone`;
- il Codespace.

La CI del motore esegue lo script su SQL Server 2022 con uno schema sintetico (`tests/sql/factory_publish_schema.sql`) e verifica pubblicazione, rifiuti per concorrenza senza scritture parziali e ripubblicazione.

## Diagnostica attrezzaggi Factory (issue #53)

Il database Factory espone **campi distinti** per i secondi statici di
attrezzaggio (`FAHAT*3600+FASAT`), le macchine e gli operatori richiesti
**per attrezzare** (`FANMA`, `FANPA`), e le risorse richieste **per lavorare**
(`FANML`, `FANPL`). Il semplice `Operation.setup_seconds` non prova
che queste risorse siano le stesse per entrambe le fasi: occorre verificare
i dati e una regola operativa.

```sh
python integrations/factory-export/tools/analyze_setup_coverage.py \
  out/estrazione/scenario.json out/motore/engine-scenario.json \
  --output out/motore/source-setup-coverage.json
```

Il report conta solo le operazioni **presenti nello scenario convertito**, per
evitarne la confusione con fasi storiche; registra presenza/durata di setup
statico, risorse per attrezzaggio e divergenze dai fabbisogni della
lavorazione. Riporta anche percentili e valore massimo delle durate, così
si possono verificare valori anomali **senza presumere che ogni durata sia
un reale cambio di famiglia macchina**.

Il report ora separa inoltre `by_execution_code.I` (fasi interne)
da `by_execution_code.E` (conto lavoro esterno): un tempo sorgente positivo
su una fase esterna puo' descrivere lead time o altra attesa, e NON e'
una prova di attrezzaggio macchina interno. La validazione del significato
rimane necessaria.

Nessuna matrice `famiglia A -> famiglia B` e' inferita dai campi ERP, e
il report lo esplicita. L'approvazione della semantica effettiva dei campi
resta una domanda nel questionario [#53](../../docs/decisions/QUESTIONARIO-ATTREZZAGGI-ISSUE-53.md).

## Preparazione dell'adapter ADR 003 (senza inventare occupazioni)

```sh
python tools/setup_adapter_readiness.py \
  out/estrazione/scenario.json out/motore/engine-scenario.json \
  --output out/motore/setup-adapter-readiness.json
```

Questo report **in sola lettura** identifica le fasi interne con setup
positivo e distingue ciò che Factory dichiara (`FAHAT/FASAT`,
`FANMA/FANPA`, `FAPAM/FAPAO`) da ciò che deve ancora essere
**risolto/approvato** per lo scheduler ADR 003:
la macchina effettivamente selezionata, i candidati specifici per
attrezzaggio, gli attrezzi, lo stato macchina precedente e il significato
delle percentuali mancanti o zero. Le alternative in `L_LARE` sono
attualmente lette per **lavorazione**: **non** sono riutilizzate come
identificativi di prenotazione setup senza prova.

Il risultato elenca motivazioni concrete e **non genera un SetupContract
automatico** (`canonical_contract_generated=false`). L'esistenza di una
classe/gruppo/valore di attrezzaggio nel database non prova da sola che
tale tripla sia l'identità completa di configurazione (Q9). Le fasi
esterne non vengono scambiate per setup macchina interno. Le fasi
fissate/rilevate richiedono snapshot di prenotazione da preservare.

Il report dettagliato può contenere identificativi reali: salvare solo
negli artefatti privati del clone e non pubblicare l'output non anonimizzato
in documentazione o repository.
