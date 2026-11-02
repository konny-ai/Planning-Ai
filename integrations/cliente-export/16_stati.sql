/* Descrizioni degli stati di riga ordine (L_CMPA.STCON): A_STA, tipo P (70 non consegnata ... 75 annullato).
   Solo testo da mostrare nella Control Room: il motore non lo legge. */
SELECT STCOD AS statusCode, STDSC AS description FROM dbo.A_STA WHERE STTYP = 'P';
