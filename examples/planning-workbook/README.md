# Input Excel autonomo

Questi file contengono solo dati sintetici. Il modello è da completare e viene
rifiutato finché date, dati produttivi e politiche non sono pronti.

| File | Caso |
| --- | --- |
| `template.xlsx` | 59 tabelle nominate, indice e configurazioni da completare |
| `bending.xlsx` | 200 pezzi × 5 min, setup 120 min, 1 macchina e 2 operatori nel ciclo |
| `unattended.xlsx` | Stessi tempi; 1 operatore nel setup e nessuno nel ciclo, macchina aperta di notte |
| `fractional.xlsx` | Stessi tempi; ogni operatore occupato al 25% nel ciclo, dentro i turni umani |

```sh
planning-factory workbook-plan examples/planning-workbook/bending.xlsx -o out/plan.json --scenario-out out/scenario.json --report out/evidence.json
planning-factory web --fixture examples/planning-workbook/unattended.xlsx
```

`python tools/build_workbook_demo.py --out out/workbook-demo` rigenera gli input
dei tre casi in JSON e XLSX con l'esportatore del prodotto. L'aspetto dei file
distribuiti è stato rifinito e verificato con il renderer di spreadsheet.

Contratto, unità, compilazione e limiti: [documentazione](../../docs/planning-workbook.md).
