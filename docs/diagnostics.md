# Diagnosi del piano — #78 P13, P18, P19

Tre analisi che **non modificano mai il piano**.

## P13 — Anticipo forzato dal nesting

```
planning-factory nesting-advance scenario.json plan.json [--max-advance-hours 72]
```

Un programma di taglio porta avanti tutti i suoi pezzi insieme, quindi i pezzi di commesse lontane vengono tagliati con quella più urgente e restano in magazzino. Per ogni membro di un nesting con consegna:
- **inizio necessario** = consegna − somma del lavoro delle fasi successive della stessa commessa;
- **anticipo** = inizio necessario − inizio del programma.

Vengono segnalati i membri con anticipo oltre il limite, dal più grande. È solo un avviso: il nesting è deciso fuori dal motore (ADR 002).

## P18 — Stabilità rispetto al piano pubblicato

```
planning-factory stability piano-pubblicato.json piano-ricalcolato.json [--threshold-minutes 60] [--window-end ISO]
```

Si basa su `compare` e conta le fasi del piano pubblicato spostate oltre la soglia o passate a risorse diverse. Con `--window-end` considera solo quelle che dovevano iniziare prima di quell'istante.
- **Uscita**: numero di fasi spostate, spostamento medio e massimo.
- **Fasi nuove e rimosse**: elencate a parte, perché non sono spostamenti.
- **Uso**: serve come avviso ("il ricalcolo stravolge il reparto") e, in futuro, come obiettivo (P17).

## P19 — Straordinari candidati (what-if)

```
planning-factory overtime scenario.json candidati.json
```

`candidati.json` è un elenco di finestre `{"resource_id", "start", "end"}`, ciascuna entro un giorno locale. Per ogni finestra:
- la risorsa riceve una copia del proprio calendario con la finestra in più;
- si ricalcola il piano e lo si verifica;
- si confronta con il piano senza straordinari.

L'uscita elenca, dal più utile, le ore aggiunte e il guadagno in commesse in ritardo, ritardo totale, fasi non pianificate e durata complessiva. Il piano pubblicato non cambia.
