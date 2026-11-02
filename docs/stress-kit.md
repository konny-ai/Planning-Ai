# Stress test riproducibili, senza database Factory

`tools/stress_kit.py` genera solo scenari **sintetici** e controlla
che ripetizioni dello stesso scenario producano piani byte-identici.
Non legge dati cliente, non esegue binari legacy e non contatta SQL Server.

## Uso

```sh
python tools/stress_kit.py --seed 123 --operations 200 --machines 8 --horizon-days 40 --repeat 3 --json stress.json
python tools/stress_kit.py --seed 123 --operations 40 --machines 4 --policy earliest_due --repeat 2
python tools/stress_kit.py --seed 123 --operations 40 --machines 4 --policy local-search --budget 8 --repeat 2
```

La generazione è deterministica rispetto al seed ed include fasi di diversa
durata, priorità, macchine alternative, dipendenze FS/SS e lag, commesse
di 5 fasi e date di consegna. Il calendario è fisso in UTC con turni
lunedì-venerdì 08–12 e 13–17. La validazione del piano viene eseguita
a ogni ripetizione con `verify_plan`, indipendente dalla schedulazione.

Limiti espliciti: seed tra 0 e 2³¹−1, 1–1000 operazioni, 1–32 macchine,
1–120 giorni di orizzonte, 1–5 ripetizioni. La ricerca locale è ammessa
fino a 80 operazioni con budget da 1 a 16, per evitare esplosioni
involontarie del numero di valutazioni.

Il report registra digest dello scenario, SHA-256 del piano serializzato,
coerenza fra ripetizioni, tempi di esecuzione per ogni run, compiti
collocati e non collocati, commesse in ritardo, versione Python e
parametri espliciti. Non contiene dati identificativi aziendali.

## Interpretazione

Un piano verificato e identico su più esecuzioni fornisce evidenza di
ripetibilità, **non** di ottimalità, e non esclude bug che agiscano
identicamente in ogni run. Il tempo in secondi dipende da CPU, Python,
carico di macchina e dimensioni del problema; non sono SLA o promesse
su una installazione cliente.

La suite automatica include casi piccoli e ripetibili per Linux e Windows.
Benchmark ampi si eseguono manualmente; la CI evita prove troppo
lunghe o dipendenti dall'hardware del runner.
