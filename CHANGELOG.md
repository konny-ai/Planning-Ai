# Changelog

## Non rilasciato — sequenza macchina automatica per lo scheduler setup/run

`optimize_setup_sequence` accetta ora `initial_sequence=None`: la sequenza di
partenza è `default_machine_sequence`, un ordine deterministico per scadenza
(EDD) che rispetta le precedenze, senza pesi di business inventati. Nuovo
parametro `neighbourhood='insertion'` (default `'swap'`, invariato) che aggiunge
il reinserimento di una fase in posizioni non adiacenti della stessa macchina.
Ogni candidato resta verificato dallo scheduler a due stadi e dall'audit
indipendente; nessuna garanzia di ottimalità globale. Test in
`tests/test_setup_sequence_search.py`.

Lo scheduler a due stadi accetta ora le **fasi già completate** come storico:
nessuna prenotazione, precedenze dei successori soddisfatte, stato macchina
osservato aggiornato (devono precedere le fasi attive sulla stessa macchina),
audit che rifiuta nuove prenotazioni per esse e KPI che ne usano il
completamento. Restano rifiutati inizi fissi, modalità backward e fornitori
esterni.

## Non rilasciato — assistente conversazionale (ramo feat/cliente-assistente-chat-v2)

Pannello **Assistente** in ogni vista della Control Room, con conversazione,
suggerimenti per vista e proposte applicabili come simulazioni verificate.
Nuove analisi per digest: percorso critico per commessa (`/api/explain`),
saturazione giornaliera da calendario (`/api/loads`), KPI di qualità
(`/api/quality`). L'assistente AI ha 12 strumenti di lettura con argomenti,
storico di 10 turni e lo strumento `propose_simulation`; senza AI le
risposte restano deterministiche. Piano e verifiche:
[docs/piano-assistente-chat-v2.md](docs/piano-assistente-chat-v2.md).

## 0.2.0 — 2026-10-18

Motore nuovo di pianificazione a capacità finita, calendari UTC/locali, bundle simultanei, nesting multipli, precedenze FS/SS, modalità per operazione, inizi fissi, storico e diagnosi. Aggiunti audit indipendente, contratti JSON e schemi, CLI, API locale, SQLite, simulazioni, adapter Factory in lettura, report e suite di test.

Il profilo nuovo è documentato esplicitamente e non certifica parità con il legacy.

## 0.1.0 — 2026-10-18

Struttura iniziale del progetto e configurazione CI.
