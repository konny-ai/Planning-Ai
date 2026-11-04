# Guida: pianificazione con attrezzaggio e lavorazione separati

`planning-factory setup-stages` pianifica **attrezzaggio** e **lavorazione** come stadi
distinti, ciascuno con le proprie risorse e percentuali di occupazione (ADR 003), e
verifica sempre il piano con un audit indipendente. Non scrive mai sugli ingressi e non
accede a Factory. Dati di esempio sintetici: `examples/setup-stages/`.

## Ingressi

- **scenario** (`scenario.json`): solo lavorazioni, senza `setup_seconds` v0.2.
- **contratto** (`contract.json`, [formato](setup-contract-v1.md)): per ogni fase macchina,
  stato richiesto, durata dell'attrezzaggio e risorse di attrezzaggio e lavorazione. Lo stato
  iniziale di ogni macchina e' **dichiarato** (ADR 006); se manca, l'attrezzaggio si fa.

## Percorso base

```sh
planning-factory setup-stages examples/setup-stages/scenario.json \
  examples/setup-stages/contract.json -o stadi.json --html stadi.html
```

Senza `--sequence` la sequenza parte dall'ordine per scadenza che rispetta le precedenze.
`stadi.json` contiene sequenza, prenotazioni, KPI e violazioni dell'audit; il codice di uscita
e' 2 se l'audit ne trova. Nell'esempio l'attrezzaggio totale e' 5.400 s.

## Scegliere la sequenza

| Opzione | Effetto |
| --- | --- |
| `--sequence a,b,c` | ordine esplicito delle fasi |
| `--start state-grouped` / `best` | partenza che raggruppa gli stati obiettivo; `best` richiede `--objective` |
| `--objective delivery\|setup\|flow` | ricerca locale a budget (`--budget`, `--neighbourhood swap\|insertion`) |
| `--restarts N --seed S` | ripete la ricerca da piu' partenze deterministiche e tiene la migliore |

```sh
planning-factory setup-stages examples/setup-stages/scenario.json \
  examples/setup-stages/contract.json -o ricerca.json --objective setup --restarts 2 --budget 8
```

Nell'esempio l'attrezzaggio scende a 1.800 s. Nessuna garanzia di ottimo globale.

## Backward (ADR 005, opzione A)

```sh
planning-factory setup-stages examples/setup-stages/scenario.json \
  examples/setup-stages/contract.json -o backward.json --scenario-output adj.json \
  --sequence job:taglio-b,job:taglio-a,job:taglio-c --backward --html backward.html
```

Lavorazioni al piu' tardi possibile e attrezzaggio subito prima, con la **sequenza fissata
prima**; si ritarda soltanto, mai nulla prima del piano in avanti. Il report indica per ogni
fase il `gap` residuo fra fine attrezzaggio e inizio lavorazione (maggiore di zero quando
calendari, pause o la lavorazione precedente lo impediscono). `--objective delivery|setup|gap`
con `--backward` cerca la sequenza valutando ogni candidato sul proprio piano backward.
`--task-modes` rispetta invece `mode` per task (`auto` e' backward se la scadenza e' a
`auto_backward_days` o piu' dall'inizio).

Per **riprodurre** il piano da soli file:

```sh
planning-factory setup-stages adj.json examples/setup-stages/contract.json -o replay.json --replay backward.json
```

Esce con 0 solo se le prenotazioni coincidono esattamente e l'audit e' pulito.

## Fasi senza risorse candidate (ADR 006, opzione B)

`--partial [--no-candidate ID,ID]` pianifica cio' che ha candidate e riporta il resto come non
pianificato (`NO_CANDIDATE`, `PREDECESSOR_UNPLANNED`, `NEST_MEMBER_UNPLANNED`); si puo'
combinare con `--backward`. Senza `--partial` il comportamento resta bloccante.

## Con i materiali

`planning-factory staged-plan scenario.json stage-spec.json -o piano.json` aggiunge ai passi
sopra il controllo dei materiali (stock netto o lordo con impegni, mai mescolati) partendo da
una specifica v0.3 vincolata allo scenario da un digest esatto; vedi
[anteprima v0.3](v03-staged-file-preview.md).

## Cosa questo percorso non fa

- Non pubblica su Factory ne' su alcun ERP.
- Non ottimizza la sequenza in modo globale e non inventa risorse, durate o date.
- Il backward non gestisce un ingresso con `mode` backward/auto senza `--task-modes`.
- I risultati sono stati provati solo su scenari sintetici.
