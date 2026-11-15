# Decision Center — collaudo locale Windows (15 novembre 2026)

## Scopo e confine

Verifica della branch `Planning_Ai_DecisionModel` di `konny-ai/Planning-Ai`
su una postazione di sviluppo Windows, in un checkout dedicato.

Tutte le prove usano `examples/workshop.json`, dati sintetici e archivi
isolati in `.local-qa/` (esclusi da Git). Non sono stati usati dati aziendali
reali, connessioni SQL, chiavi AI di fornitori esterni o scritture ERP.
La versione `main` e il sistema in esercizio non sono stati toccati.

## Ambiente verificato

- Windows, `py -3.13` = Python 3.13.14, Git 2.52.0.windows.1,
  Node.js v26.13.0.
- Ambiente principale isolato: `.venv`, installazione editabile
  `pip install -e .` e database `tzdata`.
- Secondo ambiente isolato: `.local-qa/package-venv`, installazione da
  wheel costruita in locale.
- Chrome installato sul PC, avviato in modalità headless per QA di
  interazione DOM e rete con protocollo DevTools.
- Esempio di server autenticato avviato su porta loopback dedicata 18872,
  poi 18873 per verifica dopo modifiche, con utente di prova locale e
  file di configurazione dedicati.

## Risultati della prima tornata

| Verifica | Risultato |
|---|---|
| Test mirati Decision Model / Decision Center / HTTP | 28/28 OK |
| Suite completa iniziale | 783 OK, 1 skipped, 0 errori |
| Schema `tools/generate_schema.py --check` | OK |
| Node `--check` della UI | OK |
| CLI `schedule` su officina sintetica | 7 assegnate, 0 senza piano |
| CLI `verify` | `valid=true`, 0 errori |
| CLI `optimize` | 4 politiche valutate, nessun rifiuto |
| Build del pacchetto wheel | OK |
| Installazione wheel in nuovo virtualenv | OK |
| Inclusione `decision-model.html`, JS e moduli Python nella wheel | OK |

## Browser end-to-end

La prova automatizzata `.local-qa/browser-qa.mjs` usa Chrome headless
e il server `secure-web` con utente locale sintetico.

- Pagina Decision Center caricata correttamente, 4 tab.
- Configurazione modello locale `baseline` abilitata e salvata tramite UI.
- Profilo `stability` e massimo 2 candidati salvati tramite UI.
- Esecuzione reale API e rendering della tabella con 2 candidati verificati.
- Approvazione consultiva, visualizzazione del registro revisioni.
- Nessuna eccezione JavaScript nel browser; output `BROWSER_QA_PASS`.
- Screenshot locale in `.local-qa/decision-center-qa.png` (non commesso).

Le chiamate non hanno trasmesso dati a provider esterni né pubblicato piani.

## What-if verificato

Con chiusura simulata di `PIEGA-1` tra
`2026-10-20T08:00:00Z` e `2026-10-20T10:00:00Z`:

- Piano derivato con scenario digest diverso dall'originale.
- Tre operazioni spostate; 7 ore complessive di differenza degli inizi.
- Nessuna commessa aggiuntiva in ritardo e nessuna risorsa riassegnata.
- `verified=true`, `simulation_only=true`,
  `source_modified=false`, `authorizes_execution=false`,
  `can_commit_to_erp=false`.

Una chiusura irrilevante di `LASER-1` non ha provocato variazioni
inventate: zero cambiamenti, pur generando uno scenario derivato.

## Robustezza, ruolo e protocollo HTTP

Durante il collaudo sono state individuate e corrette queste criticità:

1. **Visibilità delle revisioni in secure-web:** inizialmente la
   consultazione del registro era disponibile anche al ruolo `reparto`.
   Ora è richiesta almeno l'autorizzazione `planner`.
2. **UTF-8 BOM di Windows/PowerShell:** file storici JSON creati con
   `Set-Content -Encoding utf8` non venivano letti. Configurazioni,
   regole, revisioni e storico leggono ora `utf-8-sig` e i test
   verificano esplicitamente il caso.
3. **Trasporto del provider locale:** nuovi test HTTP effettivi
   verificano il body JSON, il percorso `/v1/chat/completions`,
   consenso per richiesta, divieto di redirect e astensione sicura
   in errore; non servono chiavi né rete esterna.

Il test dei ruoli verifica anche accessi anonimi respinti, permessi
admin/planner/reparto, Origin ostile, review duplicata, assenza di
revisioni di piano pubblicate e `store.list_revisions()==[]`.

## Verifica storica sintetica

Dataset locale di 100 osservazioni **artificiali**, cronologicamente
ordinate, con distribuzione controllata: 80 train / 20 validation.
L'API ha restituito `status=provisional`, `sample_count=100`,
`validation_count=20`, `brier_score=0.1601`,
`bucket_support=80` e `probability=0.2073`.

Questi valori dimostrano il percorso tecnico ma non stimano il rischio
reale dell'azienda: il dataset è sintetico ed è inadatto a qualsiasi
decisione industriale. In assenza di dati, oppure se supporto o Brier
sono insufficienti, il modulo deve astenersi.

## Test finali

I test da ripetere dopo le correzioni sono:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -t . -v
.\.venv\Scripts\python.exe tools\generate_schema.py --check
node --check src\planning_factory\web\decision-model.js
git diff --check
```

Esito della prima suite completa: **783 test eseguiti, zero errori, 1 skipped**.
Esito **finale dopo le correzioni**: **790 test eseguiti, zero failure o errori,
1 skipped**, comando concluso con codice 0 in **120,211 secondi**.
I dettagli restano nel log locale `.local-qa/final-tests.log` e
nel workflow GitHub collegato alla PR.

## Limiti prima di produzione

- Nessuna prova live a pagamento con OpenAI Decisions, OpenRouter,
  ScaleDown, Anthropic o altro provider cloud; manca una credenziale
  di test autorizzata e un controllo di privacy/costi.
- Nessun collaudo su un dataset storico cliente anonimizzato;
  performance e calibrazione dei modelli sono da misurare.
- Nessun collaudo SQL su clone aggiornato né test di pubblicazione ERP
  (l'applicazione resta consultiva).
- Nessuna prova manuale completa del browser su tutti i form factor,
  lettori di schermo, browser e periferiche; eseguito QA Chrome headless.
- Nessuna prova TLS/reverse proxy esposto in rete; i test utilizzano
  esclusivamente il loopback locale con archivi separati.
- `avvia.bat` non è stato eseguito perché potrebbe interrompere
  un'istanza attiva sulla porta standard; lo stesso backend UI
  `secure-web` è stato collaudato esplicitamente su porte dedicate.

**Conclusione:** idoneità tecnica per un pilot locale con dati sintetici;
il rilascio aziendale e la pubblicazione su `main` richiedono ancora
l'accettazione dei punti sopra, secondo ADR 002 e AGENTS.md.
