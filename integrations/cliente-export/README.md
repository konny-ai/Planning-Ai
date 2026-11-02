# Estrazioni CLIENTE per il test del planning

Sola lettura su `Factory_Cliente` (SQL Server locale `SERVER\ISTANZA`, autenticazione Windows). Dati fermi al **02/05/2026**: il test va ancorato a quella data (`--start 2026-05-04`), non a ottobre 2026.

```powershell
python run_extract.py      # scrive out/<script>/<n>.csv  (out/ e _defs/ sono ignorate da git: dati cliente)
```

| Script | Contenuto | Righe |
|---|---|---|
| `10_nesting_programmi.sql` | A_NES aperti: macchina, stato, durata taglio (`X_CalculateWorkplanJobTime`), setup (`X_CalculateWorkplanSetupTime`) | 970 |
| `11_nesting_membri.sql` | fasi membro (`L_ODLA`) e pezzi nestati per commessa/lotto (`L_PCPA`) | 3.294 / 3.306 |
| `12_nesting_lamiere.sql` | lamiere per nesting: formato, spessore, ripetizioni, secondi per ripetizione (`L_NELM`/`L_PCFO`) | 439 |
| `14_sedi.sql` | sede per commessa (`A_COM.Division`: 3 Sede A, 4 Sede B) e magazzini per sede | 354 / 7 |
| `13_materiali_giacenze_arrivi.sql` | giacenze e impegnato per formato; righe ordine fornitore aperte (`L_OFPA`); lamiere assegnate | 354 / 421 / 242 |

Il resto del dominio (calendari, risorse, ordini, fasi, conto lavoro) viene da `integrations/factory-export` del motore, già provato su CLIENTE: 7.174 fasi, 3.903 lotti, 80 macchine, 211 operatori, 110 ordini di conto lavoro.

## Esiti dell'analisi dei dati

- **Nesting.** Dei 970 programmi aperti, 525 sono di tipo fase 66 ("COSTO PULIZIA", `TPNES=S`) senza macchina né tempo: non sono tagli. I tagli veri sono le fasi 42/68/69 (443 con macchina, 438 con durata > 0, 439 con lamiere, 291 formati distinti). Durata e setup non stanno in colonne di A_NES ma nelle funzioni cliente; il setup oggi è una costante di 300 s.
- **Materiale.** L'estrazione standard dà 0 righe: `A_RFO` è vuota e il linked server `AS400` (`Xv_LinkedServerAs400`) non esiste in questo database. Gli arrivi vanno quindi letti da `L_OFPA` (421 righe, date da 2017 a 06/2026, da ripulire) e dalle giacenze `L_MLPA`/`L_PAQT`; 63 dei 354 formati dei nesting aperti non hanno giacenza. La data di arrivo AS400 resta una fonte esterna da concordare.
- **Risorse mancanti.** La conversione con `--missing-resource-policy fail` si ferma: molte fasi hanno `runOperators`/`runMachines` > candidati in `L_LARE`. La quota maggiore è su fasi non da pianificare (`FAPNF` ≠ Y); i candidati per tipo fase in `L_OPTF`/`L_MATF` esisterebbero, ma assegnarli è una regola da decidere con CLIENTE, non un default.
- **Da chiarire con CLIENTE:** sedi e trasferimenti: la sede esiste solo per commessa (`A_COM.Division`, `14_sedi.sql`); le macchine non hanno sede e molte lavorano commesse di entrambe (es. Panto.6, Panto.9), quindi la mappa macchina→sede e i giorni/durate di trasferimento vanno forniti da CLIENTE, significato di `STNES` 4 e 0/1/3, durata setup nesting.

## Scenario con le regole del cliente (23/10/2026)

`build_scenario_cliente.py` applica le risposte di CLIENTE al questionario: fasi da pianificare = stato 41/42/43 (`A_STA`), `FAPNF='N'` non si pianifica salvo fase dentro un nesting; tipo fase 66 (Pulizia) escluso; fasi senza risorse in `L_LARE` = tutte le risorse abilitate al tipo di fase (`L_OPTF`/`L_MATF`); sede ignorata; materiale **simulato** (`materiali_simulati.csv`: giacenza = subito, ordine fornitore 2026 = data promessa, altrimenti inizio + 7 giorni) in attesa della fonte AS400.

```powershell
python run_extract.py
python build_scenario_cliente.py prepara ..\..\data\private\cliente\scenario.json ..\..\data\private\cliente\regole
python ..\factory-export\tools\to_engine_v02.py ..\..\data\private\cliente\regole\scenario.json --out ..\..\data\private\cliente\regole\engine --subcontract-policy anchored
python build_scenario_cliente.py nesting ...\engine\engine-scenario.json ...\engine\engine-scenario-nesting.json ...\engine\materiali_simulati.csv
python -m planning_factory schedule ...\engine\engine-scenario-nesting.json -o ...\engine\plan.json
```

Esito sul dump del 02/05/2026 (start 2026-05-04): conversione con policy `fail` riuscita; 1.475 fasi da pianificare, 331 nesting con 1.085 fasi membro, 569 task, 200 commesse, `verify` valido, ripetibilita' identica, servizio a consegna 68,3 %. Esclusi 20 nesting senza bundle di risorse comune e 87 senza membri nel piano.

### Materiali simulati in formato canonico

`build_materiali_simulati.py` produce `material_supply.csv` e `material_need.csv` (entita' canoniche `materials`) al posto della fonte AS400 non ancora disponibile: giacenze reali da `L_MLPA`, ordini fornitore 2026 da `L_OFPA`, e per i formati senza ne' giacenza ne' ordini un arrivo **SIMULATO** a inizio piano + 7 giorni (id `SIM-`, riferimento `SIMULATO`). Fabbisogno = lamiere richieste dal nesting, sul primo membro. Sul dump: 246 formati (239 giacenza, 7 simulati), 330 righe di fabbisogno. Quando CLIENTE fornisce la vista AS400 (`FACTO00F`), basta sostituire questi due file.

```powershell
python build_materiali_simulati.py ...\engine\engine-scenario-nesting.json ...\engine\materiali
```

## Collaudo locale completo

`run_cliente_locale.ps1` esegue l'intera catena in sola lettura su `Factory_Cliente` e scrive tutto in `data\private\cliente` (ignorata da git): estrazioni, regole del cliente, conversione, nesting, materiali simulati, piano e verifica, riconciliazione dei programmi A_NES, poi lo scenario canonico secondo ADR 004 (`binding-cliente.json`: bundle `canonical-json` da Factory, materiali e avanzamento TRD come sorgenti `file`).

```powershell
.\run_cliente_locale.ps1                      # server SERVER\ISTANZA, database Factory_Cliente, start 2026-05-04
.\run_cliente_locale.ps1 -SkipExtract         # riusa CSV e scenario gia' estratti
.\run_cliente_locale.ps1 -Progress time       # avanzamento a tempi (default: quantity, a pezzi; none = fasi intere)
```

`-Start` sposta anche l'orizzonte del binding canonico (id, inizio e fine a 365 giorni, ora di Roma). Dalla Control Room un amministratore può far rilanciare la catena dall'assistente con un'altra data: copiare `regenerate.example.json` come `regenerate.json` nella cartella dell'istanza (accanto a `plans.sqlite`) e riavviare `secure-web`; vedi `docs/secure-control-room.md`, "Rigenerazione dalla fonte".

### Cliente delle commesse

`export-canonical` parte dallo scenario motore, che non porta il cliente. `build_clienti.py` lo riprende dall'estrazione Factory (`A_COM.CTCOD`) e lo scrive nel campo canonico `order.customer_id` del bundle: perimetro e regole per cliente, Control Room e assistente lo usano. La ragione sociale resta in `descrizioni.json` e si mostra accanto al codice (Gantt, consegne con riepilogo per cliente, programma giornaliero, nesting, maschere dati, regole, chat con lo strumento `get_customers`). Una commessa senza cliente in Factory resta senza. Nel dump del 02/05/2026: 200 commesse su 200 con cliente, 87 clienti. L'impronta dello scenario non cambia.

### Avanzamento TRD

`15_avanzamento_trd.sql` legge i consuntivi con la stessa logica di `V_OrderState_Job`/`V_OrderState_TicketTime`: tempi dai ticket `S_ODL` (ripartiti sulle fasi dei ticket multipli dei nesting), pezzi da `S_PAV`, macchina in uso solo da una sessione `S_SES` aperta (nel dump nessuna). I secondi sono riportati alla durata della fase (diviso n. risorse x % d'impiego, massimo fra macchine e operatori). `build_avanzamento_trd.py` produce `operation_progress.csv` (dominio `progress`) per le sole fasi del piano e `avanzamento_diagnostica.csv` con il motivo delle altre. La conversione Factory resta `--progress-policy none`: i consuntivi si applicano una volta sola, nel canonico.

Sul dump: 265 fasi aperte con consuntivi, 52 nel piano, 213 fuori perimetro (96 tipo 66, 61 durata zero, 56 non da pianificare).
- `-Progress time`: residuo = teorico - secondi registrati; in CLIENTE i tempi registrati sono pochi (2,8 h su 52 fasi): 132 commesse su 200 in tempo;
- `-Progress quantity`: residuo = pezzi mancanti x tempo unitario (quantita' da `LAQTP`). 6 fasi hanno tutti i pezzi ma sono ancora aperte in Factory: completate nel piano alla fine dell'ultima attivita' TRD ed elencate da chiudere; 1 e' membro di un nesting aperto e resta con il programma. 143 commesse su 200 in tempo.

Esito sul dump del 02/05/2026:
- fasi in piu' programmi (pezzi dello stesso lotto su lamiere diverse): vanno nel programma **piu' urgente** (consegna minima dei membri, `X_MinDataConsegnaPdl`; a parita' il `workplanId` minore), decisione del 23/10/2026;
- piano con nesting: 330 nesting, 568 task, nessuno non collocato, `verify` valido;
- riconciliazione: 285 programmi inclusi, 81 con tutte le fasi gia' in un nesting piu' urgente e 45 con una parte (`members_in_other_nesting`), 20 senza bundle di risorse comune, 7 senza membri, 532 non tagli, nessuna esclusione non spiegata;
- scenario canonico: il pegging dei materiali controlla le quantita' e trova 6 carenze, quindi lo scenario non e' pubblicabile; piano con 562 task e 6 nesting `NO_FEASIBLE_SLOT` per materiale; senza avanzamento 131 commesse su 200 in tempo (come con la regola precedente, primo programma in ordine di A_NES), 132 con avanzamento a tempi, 143 a pezzi.

La Control Room si apre sul binding (`--fixture ...\canonico\binding.json`) con le credenziali create da `tools\init_local_instance.py`; vedi `docs/EXECUTION-CLIENTE-2026-10-23.md`.
