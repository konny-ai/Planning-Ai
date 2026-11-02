# Contribuire

1. Descrivere problema e criteri di accettazione in una issue.
2. Sviluppare su un branch dedicato e aprire una pull request.
3. Mantenere dominio e motore indipendenti da UI, database ed ERP.
4. Aggiungere test per nuove regole di pianificazione, casi limite e correzioni.
5. Aggiornare documentazione e contratti quando cambia il comportamento.

Non inserire credenziali, dati aziendali reali o eseguibili legacy. Usare dati sintetici negli esempi e nei test.

Usare `python -m unittest discover -s tests -t . -v`, `python tools/generate_schema.py --check` e il flusso CLI dell'esempio. I test sono un gate obbligatorio; non accettare una suite vuota. Se cambiano i contratti, rigenerare gli schemi e gli esempi in un commit esplicito. Verificare che le funzionalità siano descritte correttamente nella matrice di copertura.
