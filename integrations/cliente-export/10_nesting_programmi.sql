/* CLIENTE - programmi di nesting aperti (A_NES.STNES < 5). Sola lettura.
   Durata taglio = X_CalculateWorkplanJobTime (tempo forzato X_TEMPO_TOTALE_SEC, altrimenti SUM(L_NELM.LTIME*NMRIP),
   con lamiere affiancate = meta' ripetizioni); setup = X_CalculateWorkplanSetupTime (oggi costante 300 s nel DB). */
SELECT N.IDNES AS workplanId, N.NSCOD AS code, N.NSDSC AS description, N.TPNES AS kind, N.STNES AS statusCode,
       N.TFCOD AS phaseTypeId, N.MACOD AS machineCode, N.DTEXP AS plannedDate, N.OLCOD AS workOrderCode, N.DTCRE AS createdOn,
       N.X_LAM_AFFIANCATE AS sideBySide, N.X_TEMPO_TOTALE_SEC AS forcedCutSec,
       dbo.X_CalculateWorkplanJobTime(N.IDNES) AS cutSec, dbo.X_CalculateWorkplanSetupTime(N.IDNES) AS setupSec,
       (SELECT ISNULL(SUM(E.NMRIP),0) FROM dbo.L_NELM E WHERE E.IDNES = N.IDNES) AS sheetRepetitions,
       (SELECT COUNT(DISTINCT D.IDLAV) FROM dbo.L_ODLA D WHERE D.IDNES = N.IDNES) AS memberOperations,
       dbo.X_MinDataConsegnaPdl(N.IDNES) AS minMemberDueDate, N.ATCOD AS toolCode, N.ExpLastUpdate AS sourceLastUpdate
FROM dbo.A_NES N WHERE ISNULL(N.STNES,0) < 5 ORDER BY N.IDNES;
