# Catena dei vincoli e percorso critico a capacità finita — #88

Per ogni unità del piano risponde a "perché inizia qui?". Per ogni commessa risponde a "perché finisce qui?".

```
planning-factory explain scenario.json plan.json [--manifest manifest.json] [-o analisi.json]
```

L'analisi si **ricostruisce dal piano verificato e dal suo scenario**, indipendentemente dall'algoritmo che ha prodotto il piano. Il piano non cambia: un test controlla che resti identico byte per byte. Un piano alterato o di un altro scenario viene rifiutato.

## Causa dell'inizio di un'unità

Per ogni limite inferiore si calcola il primo inizio possibile sul **calendario** delle risorse dell'unità, ignorando la capacità. I limiti considerati sono:
- inizio dell'orizzonte;
- rilascio;
- fine o inizio del predecessore più il ritardo.

Un'unità non frazionabile ha bisogno di un'unica finestra lavorativa abbastanza lunga.

| Causa | Significato |
|---|---|
| `predecessor` | Inizia appena lo consente il predecessore (`ref`) |
| `release` / `material` | Inizia appena lo consente il rilascio. Con `--manifest`, le unità che hanno legami di materiale (#85) sono etichettate `material` |
| `horizon` | Inizia all'inizio dell'orizzonte |
| `resource` | Nessun limite spiega l'inizio: l'unità ha **aspettato capacità**. `ref` e `resource` indicano l'unità che ha liberato la risorsa condivisa durante l'attesa; `capacity_wait_seconds` è l'attesa |
| `fixed` | Inizio e risorse fissati |
| `backward` | Collocata il più tardi possibile rispetto alla consegna o ai successori: l'inizio non dipende da un limite inferiore |
| `completed` / `external` | Storico o fine dichiarata dal fornitore |

`calendar_wait_seconds` è il tempo tra il limite e l'inizio dovuto ai soli turni, alle chiusure o alla mancanza di una finestra abbastanza lunga.

## Percorso critico a capacità finita

Il percorso critico di una commessa parte dalla sua unità che finisce per ultima. Risale la catena seguendo predecessori e **unità che occupavano la risorsa**, e si ferma su rilascio, materiale, orizzonte, inizio fisso o unità all'indietro. A differenza del percorso critico classico di MS Project, include le attese di capacità.

## Limiti

- Su risorse con capacità maggiore di 1, o quando più unità liberano la risorsa nello stesso istante, viene indicata una sola di esse, in modo deterministico.

## Registrazione nello scheduler e confronto

`planning-factory schedule scenario.json -o plan.json --record-constraints` (in codice `plan(s, record_constraints=True)`) aggiunge a ogni assegnazione una voce `constraint=<tipo>[:<riferimento>][;calendar]`:
- `predecessor:<unità>`, `release` oppure `horizon`: il limite inferiore attivo. Il suffisso `;calendar` indica che l'inizio è slittato solo per il calendario;
- `capacity`: l'inizio è slittato per l'occupazione delle risorse;
- `backward` e `fixed`.

Per distinguere il calendario dalla capacità lo scheduler prenota di nuovo lo stesso bundle ignorando le altre unità. L'opzione è spenta per default, quindi i piani pubblicati e i golden non cambiano.

Quando il piano contiene queste voci, `explain` le confronta con la ricostruzione indipendente e riporta `scheduler_agreement` con le unità controllate, quelle concordi e le differenze. Sull'esempio officina e sul banco Factory (348 unità) le due analisi coincidono.
