# Efficienza delle risorse — #78 P9

L'opzione è facoltativa. Quando non è usata, lo scenario e il piano non cambiano: il campo non compare nel JSON e il digest resta lo stesso.

## Nel motore

- `Resource.efficiency_bp`: efficienza in punti base, come le percentuali di ADR 003. `10000` vale 100 % ed è il default. `8000` vale 80 %, `12000` vale 120 %. È ammesso da 1 a 100000.
- Il **tempo pianificato** è il tempo standard diviso per l'efficienza, arrotondato per eccesso al secondo: 1 h all'80 % diventa 4500 s.
  - Vale per l'intera unità, preparazione compresa, perché il motore la riceve come un'unica durata.
- Un'alternativa con più risorse (macchina più operatore) lavora alla velocità della risorsa **meno efficiente**.
- La spiegazione del piano riporta `efficiency <bp>bp: <standard>s planned as <pianificato>s`.
- Quando le alternative di un'unità hanno tempi pianificati diversi, pianificando in avanti vince quella che **finisce prima**. Con tempi uguali resta la regola storica: vince quella che inizia prima.
  - Così una macchina libera ma lenta non viene preferita a una appena occupata che finisce prima.
- Il verificatore indipendente ricalcola il tempo pianificato dall'alternativa prenotata e lo confronta con i segmenti.
- La dispatch basata sul margine (P2) e la stima della modalità automatica (P4) usano i tempi standard.

## Dallo storico

`planning_factory.engine.efficiency` serve agli adapter che leggono i consuntivi:

- `history_efficiency_bp(resource_id, samples, min_samples=5)` riceve coppie (secondi standard, secondi effettivi). Restituisce il rapporto tra totale standard e totale effettivo, in punti base. Le coppie con un valore nullo non vengono contate.
- Con meno di `min_samples` campioni utili non inventa un valore: `efficiency_bp` è `None` e la diagnostica dice `INSUFFICIENT_HISTORY`.
- `apply_history(scenario, samples_by_resource, min_samples)` imposta l'efficienza misurata solo dove lo storico basta. Restituisce anche le stime per ogni risorsa, utili per il report.

La finestra temporale (per esempio 90 giorni) e la lettura dei consuntivi spettano all'adapter. Il motore non legge il database.

## Dati canonici

Disponibile come `resource.efficiency_bp`, facoltativo. Assente equivale a 100 %.
