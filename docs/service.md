# Servizio di rete con autenticazione — #90

Servizio multiutente, distinto dall'API locale di sviluppo (`serve`, solo `127.0.0.1` e senza login), che resta invariata.

```
planning-factory service-token --name m.rossi --role planner
planning-factory service --store dati/piani.db --credentials /etc/planning/users.json --audit /var/log/planning/audit.jsonl \
    --host 0.0.0.0 --port 8443 --cert server.pem --key server.key --workers 2 --job-timeout 120
```

## Utenti e ruoli

- `service-token` stampa **una sola volta** il token e la voce da aggiungere al file credenziali. Il file contiene solo `name`, `role` e `token_sha256`, e un token in chiaro viene rifiutato. Il file va tenuto **fuori dal repository**.
- Il client manda `Authorization: Bearer <token>`. Una richiesta senza token o con un token sconosciuto riceve 401; un ruolo insufficiente riceve 403. Le richieste con `Origin` di un browser vengono rifiutate.

| Ruolo | Può |
|---|---|
| `reparto` | Leggere scenario e piano, verificare un piano |
| `planner` | Inoltre salvare scenari e calcolare piani |
| `admin` | Inoltre leggere il registro delle operazioni |

**Nessun endpoint pubblica nell'ERP**: la pubblicazione richiede l'autorizzazione esplicita di #3.

## Endpoint

| Metodo e percorso | Ruolo | Note |
|---|---|---|
| `GET /health` | nessuno | |
| `POST /v1/scenarios` `{scenario, expected_revision}` | planner | 409 se la revisione attesa non è quella corrente |
| `GET /v1/scenarios/{id}` | reparto | Revisione e scenario |
| `POST /v1/scenarios/{id}/plan` `{expected_revision}` | planner | Calcolo isolato, verifica, salvataggio legato al digest |
| `GET /v1/scenarios/{id}/plan` | reparto | Piano della revisione corrente |
| `POST /v1/verify` `{scenario, plan}` | reparto | |
| `GET /v1/audit` | admin | Ultime 200 voci |

## Calcolo e concorrenza

- **Calcolo isolato**: ogni piano si calcola in un **processo separato** (`spawn`) con limite di tempo (`--job-timeout`). Oltre il limite il processo viene terminato e la risposta è 504.
- **Coda**: lavorano al massimo `--workers` calcoli insieme. Gli altri aspettano fino a 30 secondi, poi ricevono 503.
- **Memoria**: `--memory-mb` limita la memoria del worker solo su sistemi POSIX; su Windows non è applicabile.
- **Concorrenza**: due scritture concorrenti non si sovrascrivono, grazie al controllo di revisione di `SnapshotStore`.

## Registro delle operazioni

Il registro è in JSON lines: istante, utente, ruolo, metodo, percorso, esito e, per salvataggi e calcoli, scenario, revisione e digest. Non contiene mai token né contenuti di produzione.

## Rete

- Fuori da loopback il servizio richiede **TLS** (`--cert`, `--key`, minimo TLS 1.2).
- In alternativa si può dichiarare `--behind-proxy`, se un reverse proxy TLS termina la connessione.

## Limiti

- **Account**: niente SSO o LDAP; i token si revocano togliendo la voce dal file e riavviando.
- **Coda**: non è persistente (resta in memoria), quindi un riavvio perde le richieste in attesa.
- **Endpoint esposti**: solo calcolo base (`plan`). Ottimizzazione, ricerca attrezzaggi e costruzione da sorgenti canoniche (ADR 004) vanno esposti dopo i rispettivi merge.
