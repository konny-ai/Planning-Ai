# Alternativa preferita e attese tra fasi — #78 P10 e P6

Entrambe le opzioni sono facoltative. Quando non sono usate, lo scenario non cambia, perché i campi non compaiono nel JSON e il digest resta lo stesso, e il piano è identico a prima.

## P10 — Guadagno minimo per cambiare macchina

- `Operation.preferred_alternative`: indice dell'alternativa preferita, per esempio la macchina abituale per quel lavoro.
- `Settings.alternative_switch_min_gain_seconds`: soglia. Con 0 (default) si sceglie sempre l'alternativa che fa iniziare prima, come oggi.

Durante la prenotazione, se la soglia è positiva e la migliore alternativa non è quella preferita:
- il **guadagno** si misura sulla fine. Pianificando in avanti conta quanto finisce prima l'alternativa migliore; pianificando all'indietro, quanto finisce più vicino all'obiettivo;
- con un guadagno **inferiore alla soglia** resta l'alternativa preferita, e la spiegazione del piano riporta `preferred alternative kept: another saves only …`;
- altrimenti si cambia, e la spiegazione riporta `left preferred alternative: saves …`.

Casi particolari:
- gli inizi fissi non sono toccati;
- i membri di un nesting devono indicare la stessa preferenza;
- se l'alternativa preferita non è disponibile per tutto il nesting, vale la scelta normale.

Nei dati canonici sono disponibili come `operation.preferred_alternative` e `profile.alternative_switch_min_gain_seconds`. Quando il CAM o l'avanzamento fissano già la macchina, la preferenza viene ignorata.

## P6 — Attesa massima tra fasi (prima versione)

- `Dependency.max_lag_seconds`, solo per precedenze FS e non minore di `lag_seconds`, indica l'attesa massima accettabile tra la fine del predecessore e l'inizio del successore. Serve per esempio a limitare il materiale fermo tra taglio e piega.
- `planning-factory waits scenario.json plan.json` elenca le attese oltre il limite, dalla più grande, e riporta il KPI `over_limit`.
- Nei dati canonici il campo si chiama `operation_dependency.max_lag_seconds`.

Il piano normale segnala il limite ma non lo impone. Per imporlo c'è la versione dura, descritta sotto.

## P6 — Versione dura: `enforce-waits`

`planning-factory enforce-waits scenario.json -o piano.json --scenario-out scenario-riparato.json [--max-rounds 10]`, oppure `engine.max_lag.enforce_max_lag(scenario)`, è un passo separato e facoltativo. Il comando `schedule` non cambia.

Funziona così:

1. Pianifica normalmente e cerca le attese oltre il limite.
2. Per ognuna posticipa il predecessore. Gli assegna una data di rilascio pari all'inizio più tardi che, sul calendario delle risorse già prenotate, lo fa finire:
   - non prima di `inizio successore − max_lag_seconds`;
   - non dopo `inizio successore − lag_seconds`;
   - non oltre la sua consegna.
3. Ripianifica tutto e ricomincia. I rilasci possono solo crescere, quindi il ciclo termina.
4. Si ferma quando non resta niente da spostare, o dopo `--max-rounds` giri.

Spostare un predecessore può creare un'attesa più a monte, che viene riparata al giro successivo.

Quello che non si può riparare resta nel piano, con il motivo:

| Codice | Motivo |
| --- | --- |
| `PREDECESSOR_PINNED` | Predecessore con inizio fisso, completato o con data promessa del fornitore |
| `PREDECESSOR_DUE` | Finire abbastanza tardi farebbe mancare al predecessore la sua consegna |
| `NO_CALENDAR_WINDOW` | Nessun orario di lavoro delle risorse prenotate permette di finire nella finestra (per esempio la pausa pranzo con limite 0) |
| `CAPACITY` | Il predecessore inizia già il più tardi che il calendario consente: l'attesa viene da capacità occupata da altri |
| `ROUNDS_EXHAUSTED` | Ancora oltre il limite dopo l'ultimo giro |

Cosa restituisce:
- il piano riparato, che si riferisce allo scenario riparato, cioè l'originale più i nuovi rilasci. Per questo vengono scritti entrambi, ed entrambi passano il verificatore indipendente;
- il riepilogo, che riporta i rilasci aggiunti, le attese oltre il limite prima e dopo e il ritardo totale prima e dopo.

**Limite dichiarato**: l'euristica seriale non garantisce la fattibilità. Una riparazione può spostare altro lavoro, quindi va letto il ritardo prima/dopo. Le unità non riparabili non vengono tolte dal piano: sono segnalate con il codice, così chi pianifica decide.
