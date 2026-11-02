# Zona congelata (time fence) — #78 P1

Il reparto non deve vedere cambiare il lavoro delle prossime ore. Le unità del **piano pubblicato** che iniziano entro la finestra restano ferme nel nuovo calcolo: stesso inizio, stesse risorse.

```
planning-factory freeze-window nuovo-scenario.json piano-pubblicato.json -o scenario-congelato.json (--until ISO | --hours 8 --calendar turno-reparto)
planning-factory schedule scenario-congelato.json -o nuovo-piano.json
```

- **Revisioni diverse**: il piano pubblicato appartiene alla revisione precedente dello scenario. Le unità si abbinano per **fasi**, mai per posizione.
- **Finestra**: si indica come istante (`--until`) oppure in ore lavorative di un calendario (`--hours` con `--calendar`), contate dall'inizio dell'orizzonte.
- **Congelamento**: un'unità congelata riceve `fixed_start` e `fixed_alternative`, cioè il bundle che aveva nel piano pubblicato. Un nesting si congela intero.
- **Unità non congelate**: ognuna viene riportata con il motivo, mai spostata o eliminata in silenzio:
  - fasi non più presenti;
  - nesting modificato;
  - fase chiusa o esterna nella nuova revisione;
  - bundle non più tra le alternative;
  - unità iniziata prima del nuovo orizzonte, da trattare come avanzamento (#86).
- **Verifica**: `engine.time_fence.check_fence(nuovo_piano, risultato)` controlla che nessuna unità congelata sia stata spostata o lasciata fuori, per esempio se nella nuova revisione la capacità non basta più.

Sostituisce le tre chiavi legacy `AcceptTheoreticalStart*` con una sola finestra temporale.
