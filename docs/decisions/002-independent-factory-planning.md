# ADR 002 — Planning Factory indipendente dal planner storico

**Data:** 20 ottobre 2026. **Stato:** decisione di prodotto approvata dal committente.

## Contesto

FSMo_SCH e' stato studiato per riconoscere le funzioni e i dati della pianificazione
industriale. E' un software incompleto e potenzialmente impreciso: i suoi risultati
non sono un oracolo, una specifica normativa o un obiettivo di compatibilita'.

## Decisione

Planning Factory e' un **prodotto nuovo**, non una riscrittura fedele di FSMo_SCH.
Le decisioni di pianificazione si basano sui requisiti della fabbrica, sui contratti
espliciti del nuovo motore e su esperimenti verificabili. In caso di conflitto,
una regola confermata di produzione e la correttezza dei vincoli hanno precedenza
su quanto osservato nel vecchio programma.

- **Vincoli duri:** unita' di tempo corrette, calendari e indisponibilita',
  disponibilita' simultanea di risorse, precedenze, nesting come attivita'
  indivisibili, lavori in corso/fissi, materiali e tempi esterni. Ogni piano
  deve passare un audit indipendente.
- **Obiettivi di qualita':** rispetto delle consegne, occupazione utile,
  tempi di cambio attrezzaggio, stabilita' del piano e carico equilibrato.
  Definire priorita', pesi e KPI con il committente; non importare punteggi
  opachi dal planner storico e non inventare obiettivi mancanti.
- **Strategia:** ottenere prima un piano ammissibile, poi valutare in modo
  riproducibile alternative e miglioramenti, entro budget dichiarati.
  L'euristica corrente non promette ottimalita' globale.
- **Dati:** Factory ERP rimane una fonte di dati e, dopo approvazione esplicita,
  una destinazione per pubblicare i piani; **non** e' la fonte della politica
  decisionale del motore.
- **Nesting:** un programma di taglio/nesting predefinito, anche contenente
  piu' lamiere e piu' commesse, e' una sola attivita' atomica ai fini del
  planning; il calcolo geometrico del nesting e' esterno al motore.
- **Regressioni:** test sintetici, scenari reali autorizzati, audit indipendente,
  coerenza dei dati, determinismo e KPI prima/dopo. Gli snapshot legacy
  possono restare come materiale storico, ma **non** come gate di accettazione.

## Impatto sul backlog

Non eseguire lavoro soltanto per imitare: priorita' ricorsive nascoste, fallback
buggati, ordinamenti accidentali, workaround dei difetti, o uguaglianza dei
Gantt con FSMo_SCH. Rivalutare qualsiasi "profilo legacy" o opzione derivata
dal reverse engineering alla luce di un requisito operativo autonomo.

I comparatori e gli archivi precedenti non vanno rimossi: sono materiale di
analisi storica. I nuovi miglioramenti non si giudicano dall'aumento di
percentuali di corrispondenza con il vecchio Gantt.

## Sicurezza e rilascio

La rimozione del vincolo di parita' legacy **non** autorizza la pubblicazione
di dati in produzione. Prima del rilascio restano indispensabili test su
clone reale, verifica delle transazioni/trigger, accettazione del comportamento
con criteri nuovi, controlli di accesso e consenso specifico alla scrittura ERP.

## Gerarchia delle fonti

1. Decisioni correnti del committente e requisiti operativi verificati.
2. Questa ADR e contratti di dominio del prodotto nuovo.
3. Risultati di test, benchmark tecnici e collaudi su dati reali autorizzati.
4. Documentazione e osservazioni del vecchio programma, **solo descrittive**.

L'ADR 001 definiva la semantica v0.2 iniziale; questa decisione ne aggiorna il
principio di prodotto e rende la parita' legacy definitivamente fuori obiettivo.
