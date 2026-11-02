/* =====================================================================
   00_universo.sql - Planning Factory, adapter Factory
   Definisce l'universo della pianificazione nelle tabelle temporanee
   #U_JOB (lavorazioni), #U_LOT (lotti delle commesse), #U_COM (commesse).
   Va eseguito per primo, sulla stessa connessione degli altri script.

   Parametri (impostati dall'esportatore con DECLARE in testa al batch):
     @IncludiBloccate  bit   1 = carica anche le commesse bloccate (COSTO 22),
                              come CaricaCommesseBloccate=1 del legacy (default)
     @UsaVistaCliente  bit   1 = filtra con dbo.Xv_ElementiInPlanning se esiste
                              (equivalente al WhereCond di FSM.INI del cliente)
     @SoloDaPianificare bit  1 = solo fasi con FAPNF='Y'; 0 = tutte (come il legacy,
                              che carica anche FAPNF='N' e non le pianifica)

   Regole riprese dalla SELECT principale di FSMo_SCH (docs/21 §21.3):
     A_COM.COSTO NOT IN (24,25) [e 22 se @IncludiBloccate=0]
     A_LAV.LASTO NOT IN (44,45)
   ===================================================================== */
IF OBJECT_ID('tempdb..#U_JOB') IS NOT NULL DROP TABLE #U_JOB;
IF OBJECT_ID('tempdb..#U_LOT') IS NOT NULL DROP TABLE #U_LOT;
IF OBJECT_ID('tempdb..#U_COM') IS NOT NULL DROP TABLE #U_COM;

CREATE TABLE #U_JOB (IDLAV int NOT NULL PRIMARY KEY, CONUM int NOT NULL, LOCOD int NOT NULL, IDFAS int NOT NULL);

INSERT INTO #U_JOB (IDLAV, CONUM, LOCOD, IDFAS)
SELECT L.IDLAV, L.CONUM, L.LOCOD, L.IDFAS
FROM dbo.A_LAV L
JOIN dbo.A_LOT T ON T.CONUM = L.CONUM AND T.LOCOD = L.LOCOD
JOIN dbo.A_COM C ON C.CONUM = L.CONUM
WHERE C.COSTO NOT IN (24, 25)
  AND (@IncludiBloccate = 1 OR C.COSTO <> 22)
  AND L.LASTO NOT IN (44, 45)
  AND (@SoloDaPianificare = 0 OR L.FAPNF = 'Y');

IF @UsaVistaCliente = 1 AND OBJECT_ID('dbo.Xv_ElementiInPlanning') IS NOT NULL
    EXEC (N'DELETE U FROM #U_JOB U WHERE U.IDLAV NOT IN (SELECT IDLAV FROM dbo.Xv_ElementiInPlanning)');

SELECT DISTINCT CONUM INTO #U_COM FROM #U_JOB;

/* Tutti i lotti delle commesse in piano, anche senza lavorazioni aperte:
   servono per ricostruire l'albero LOCLP e il lotto finale LOCLM. */
SELECT T.CONUM, T.LOCOD INTO #U_LOT
FROM dbo.A_LOT T JOIN #U_COM C ON C.CONUM = T.CONUM;

SELECT (SELECT COUNT(*) FROM #U_JOB) AS lavorazioni,
       (SELECT COUNT(*) FROM #U_LOT) AS lotti,
       (SELECT COUNT(*) FROM #U_COM) AS commesse;
