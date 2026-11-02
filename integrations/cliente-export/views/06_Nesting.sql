/*
   dbo.X_CLIENTE_Planning_Nesting - contratto Factory -> Excel, versione 2.
   AREA: Tutti i programmi aperti, membri, quantita, lamiere e collegamenti con le fasi; nessuna perdita dei programmi esclusi.
   FOGLI ALIMENTATI: Nesting, Membri nesting, Provenienza record. Tutte le righe sono anche conservate in Provenienza record.

   COME LEGGERLA E MODIFICARLA
   - Una riga conserva UN tipo di record, indicato da TipoRecord.
   - I blocchi CTE iniziali contengono le SELECT di lavoro da modificare.
   - L UNION ALL finale allinea colonne e tipi: NULL significa non pertinente.
   - Non unire righe di grana diversa con JOIN, per non moltiplicare quantita o tempi.
   - Non rinominare TipoRecord o colonne senza aggiornare anche il contratto del connettore.
   - Esempio: SELECT * FROM dbo.X_CLIENTE_Planning_Nesting WHERE TipoRecord=N'PianiLavoro'.

   PERIMETRO: X_CLIENTE_Planning_Perimetro conserva le fasi aperte delle commesse non chiuse.
   MANCANTI: NULL rimane mancante, zero rimane un valore; nessun arrivo o tempo viene inventato.
   DATE: valori locali Factory; il connettore assegna Europe/Rome, conserva l unita e applica l orizzonte.
   UNITA: tempi in secondi, percentuali 0..100, quantita nella unita esplicita dell articolo.
   La vista non scrive su Factory. Il pianificatore legge l Excel, mai direttamente questa vista.
*/
CREATE OR ALTER VIEW dbo.X_CLIENTE_Planning_Nesting
AS
WITH
/* PianiLavoro: Una riga del tipo PianiLavoro. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
PianiLavoro AS (
SELECT N.IDNES AS workplanId, N.NSCOD AS code, N.NSDSC AS description, N.TPNES AS kind, N.STNES AS statusCode,
       N.TFCOD AS phaseTypeId, N.MACOD AS machineCode, N.DTEXP AS plannedDate, N.OLCOD AS workOrderCode,
       N.ATCOD AS toolCode, N.ATTYP AS toolTypeId
FROM dbo.A_NES N
WHERE ISNULL(N.STNES, 0) < 5
  AND N.IDNES IN (SELECT L.IDPTC FROM dbo.A_LAV L JOIN dbo.X_CLIENTE_Planning_Perimetro J ON J.IDLAV = L.IDLAV WHERE ISNULL(L.IDPTC, 0) <> 0
                  UNION SELECT P.IDPTC FROM dbo.L_PCPA P JOIN dbo.X_CLIENTE_Planning_Perimetro J ON J.CONUM = P.CONUM AND J.LOCOD = P.LOCOD AND J.IDFAS = P.IDFAS)
),

/* QuantitaPiani: Una riga del tipo QuantitaPiani. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
QuantitaPiani AS (
SELECT J.IDLAV AS operationId, V.IDPTC AS workplanId, V.RETYP AS rowType, V.OLQTP AS qty,
       V.TMATT AS setupSec, V.TMLAV AS unitSec
FROM dbo.V_ODLA_PLANNING V
JOIN dbo.X_CLIENTE_Planning_Perimetro J ON J.CONUM = V.CONUM AND J.LOCOD = V.LOCOD AND J.IDFAS = V.IDFAS
),

/* Programmi: Una riga per programma aperto STNES<5, inclusi i programmi senza taglio. cutSec e setupSec sono tempi totali del programma: non moltiplicarli per il numero dei membri. La scelta dei nesting pianificabili e la riconciliazione avvengono dopo la lettura e sono conservate nell Excel. */
Programmi AS (
SELECT N.IDNES AS workplanId, N.NSCOD AS code, N.NSDSC AS description, N.TPNES AS kind, N.STNES AS statusCode,
       N.TFCOD AS phaseTypeId, N.MACOD AS machineCode, N.DTEXP AS plannedDate, N.OLCOD AS workOrderCode, N.DTCRE AS createdOn,
       N.X_LAM_AFFIANCATE AS sideBySide, N.X_TEMPO_TOTALE_SEC AS forcedCutSec,
       dbo.X_CalculateWorkplanJobTime(N.IDNES) AS cutSec, dbo.X_CalculateWorkplanSetupTime(N.IDNES) AS setupSec,
       (SELECT ISNULL(SUM(E.NMRIP),0) FROM dbo.L_NELM E WHERE E.IDNES = N.IDNES) AS sheetRepetitions,
       (SELECT COUNT(DISTINCT D.IDLAV) FROM dbo.L_ODLA D WHERE D.IDNES = N.IDNES) AS memberOperations,
       dbo.X_MinDataConsegnaPdl(N.IDNES) AS minMemberDueDate, N.ATCOD AS toolCode, N.ExpLastUpdate AS sourceLastUpdate
FROM dbo.A_NES N WHERE ISNULL(N.STNES,0) < 5
),

/* Membri: Una riga per legame programma/fase/bolla. Lo stesso IDLAV puo comparire in programmi diversi: non eliminarlo con DISTINCT su sola fase. */
Membri AS (
SELECT D.IDNES AS workplanId, D.IDLAV AS operationId, D.CONUM AS orderId, D.LOCOD AS lotId, D.IDFAS AS phaseId,
       D.OLQTP AS memberQty, D.OLCOD AS workOrderCode
FROM dbo.L_ODLA D JOIN dbo.A_NES N ON N.IDNES = D.IDNES WHERE ISNULL(N.STNES,0) < 5
),

/* Quantita: Una riga per programma/commessa/lotto/fase/articolo, con somme delle quantita nestate e richieste. */
Quantita AS (
SELECT P.IDPTC AS workplanId, P.CONUM AS orderId, P.LOCOD AS lotId, P.IDFAS AS phaseId, P.PACOD AS partCode,
       SUM(P.PANES) AS nestedQty, SUM(P.PANUM) AS requiredQty, MAX(P.WeightNested) AS weightNested
FROM dbo.L_PCPA P JOIN dbo.A_NES N ON N.IDNES = P.IDPTC WHERE ISNULL(N.STNES,0) < 5
GROUP BY P.IDPTC, P.CONUM, P.LOCOD, P.IDFAS, P.PACOD
),

/* Lamiere: Una riga per posizione lamiera del programma. Conserva numero lamiere, ripetizioni, formato e peso dichiarato; il peso consente il fabbisogno in kg senza inventare conversioni. */
Lamiere AS (
SELECT E.IDNES AS workplanId, E.IDFOR AS sheetSlot, F.FOCOD AS sheetPartCode, F.FODSC AS sheetDescription, F.MTCOD AS material,
       F.PFDX1 AS widthMm, F.PFDY1 AS lengthMm, F.PFDZ1 AS thicknessMm, F.Weight AS sheetWeightKg, F.MGCOD AS warehouse, F.LCCOD AS location,
       F.NMNES AS sheetsRequired, F.NMIMP AS sheetsReserved, E.NMRIP AS repetitions, E.LTIME AS cutSecPerRepetition,
       E.LAREA AS cutAreaMm2, E.UsedPercentage AS usedPct, E.ScrapPercentage AS scrapPct
FROM dbo.L_NELM E JOIN dbo.A_NES N ON N.IDNES = E.IDNES
LEFT JOIN dbo.L_PCFO F ON F.IDPTC = E.IDNES AND F.IDFOR = E.IDFOR
WHERE ISNULL(N.STNES,0) < 5
)

/* Allineamento del contratto; la logica di business e nei CTE sopra. */
SELECT
    CAST(N'PianiLavoro' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[workplanId] AS int) AS [workplanId],
    CAST(D.[code] AS varchar(100)) AS [code],
    CAST(D.[description] AS varchar(100)) AS [description],
    CAST(D.[kind] AS varchar(1)) AS [kind],
    CAST(D.[statusCode] AS smallint) AS [statusCode],
    CAST(D.[phaseTypeId] AS smallint) AS [phaseTypeId],
    CAST(D.[machineCode] AS varchar(15)) AS [machineCode],
    CAST(D.[plannedDate] AS date) AS [plannedDate],
    CAST(D.[workOrderCode] AS varchar(10)) AS [workOrderCode],
    CAST(D.[toolCode] AS smallint) AS [toolCode],
    CAST(D.[toolTypeId] AS smallint) AS [toolTypeId],
    CAST(NULL AS int) AS [operationId],
    CAST(NULL AS int) AS [rowType],
    CAST(NULL AS numeric(38,6)) AS [qty],
    CAST(NULL AS decimal(38,1)) AS [setupSec],
    CAST(NULL AS numeric(19,6)) AS [unitSec],
    CAST(NULL AS date) AS [createdOn],
    CAST(NULL AS bit) AS [sideBySide],
    CAST(NULL AS int) AS [forcedCutSec],
    CAST(NULL AS int) AS [cutSec],
    CAST(NULL AS int) AS [sheetRepetitions],
    CAST(NULL AS int) AS [memberOperations],
    CAST(NULL AS date) AS [minMemberDueDate],
    CAST(NULL AS datetime2(6)) AS [sourceLastUpdate],
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [phaseId],
    CAST(NULL AS numeric(18,6)) AS [memberQty],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS int) AS [nestedQty],
    CAST(NULL AS int) AS [requiredQty],
    CAST(NULL AS numeric(18,6)) AS [weightNested],
    CAST(NULL AS smallint) AS [sheetSlot],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS varchar(160)) AS [sheetDescription],
    CAST(NULL AS varchar(15)) AS [material],
    CAST(NULL AS numeric(18,6)) AS [widthMm],
    CAST(NULL AS numeric(18,6)) AS [lengthMm],
    CAST(NULL AS numeric(18,6)) AS [thicknessMm],
    CAST(NULL AS numeric(18,6)) AS [sheetWeightKg],
    CAST(NULL AS varchar(15)) AS [warehouse],
    CAST(NULL AS varchar(15)) AS [location],
    CAST(NULL AS smallint) AS [sheetsRequired],
    CAST(NULL AS numeric(18,6)) AS [sheetsReserved],
    CAST(NULL AS int) AS [repetitions],
    CAST(NULL AS int) AS [cutSecPerRepetition],
    CAST(NULL AS float) AS [cutAreaMm2],
    CAST(NULL AS decimal(18,6)) AS [usedPct],
    CAST(NULL AS decimal(18,6)) AS [scrapPct]
FROM PianiLavoro AS D

UNION ALL

SELECT
    CAST(N'QuantitaPiani' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[workplanId] AS int) AS [workplanId],
    CAST(NULL AS varchar(100)) AS [code],
    CAST(NULL AS varchar(100)) AS [description],
    CAST(NULL AS varchar(1)) AS [kind],
    CAST(NULL AS smallint) AS [statusCode],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(15)) AS [machineCode],
    CAST(NULL AS date) AS [plannedDate],
    CAST(NULL AS varchar(10)) AS [workOrderCode],
    CAST(NULL AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(D.[operationId] AS int) AS [operationId],
    CAST(D.[rowType] AS int) AS [rowType],
    CAST(D.[qty] AS numeric(38,6)) AS [qty],
    CAST(D.[setupSec] AS decimal(38,1)) AS [setupSec],
    CAST(D.[unitSec] AS numeric(19,6)) AS [unitSec],
    CAST(NULL AS date) AS [createdOn],
    CAST(NULL AS bit) AS [sideBySide],
    CAST(NULL AS int) AS [forcedCutSec],
    CAST(NULL AS int) AS [cutSec],
    CAST(NULL AS int) AS [sheetRepetitions],
    CAST(NULL AS int) AS [memberOperations],
    CAST(NULL AS date) AS [minMemberDueDate],
    CAST(NULL AS datetime2(6)) AS [sourceLastUpdate],
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [phaseId],
    CAST(NULL AS numeric(18,6)) AS [memberQty],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS int) AS [nestedQty],
    CAST(NULL AS int) AS [requiredQty],
    CAST(NULL AS numeric(18,6)) AS [weightNested],
    CAST(NULL AS smallint) AS [sheetSlot],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS varchar(160)) AS [sheetDescription],
    CAST(NULL AS varchar(15)) AS [material],
    CAST(NULL AS numeric(18,6)) AS [widthMm],
    CAST(NULL AS numeric(18,6)) AS [lengthMm],
    CAST(NULL AS numeric(18,6)) AS [thicknessMm],
    CAST(NULL AS numeric(18,6)) AS [sheetWeightKg],
    CAST(NULL AS varchar(15)) AS [warehouse],
    CAST(NULL AS varchar(15)) AS [location],
    CAST(NULL AS smallint) AS [sheetsRequired],
    CAST(NULL AS numeric(18,6)) AS [sheetsReserved],
    CAST(NULL AS int) AS [repetitions],
    CAST(NULL AS int) AS [cutSecPerRepetition],
    CAST(NULL AS float) AS [cutAreaMm2],
    CAST(NULL AS decimal(18,6)) AS [usedPct],
    CAST(NULL AS decimal(18,6)) AS [scrapPct]
FROM QuantitaPiani AS D

UNION ALL

SELECT
    CAST(N'Programmi' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[workplanId] AS int) AS [workplanId],
    CAST(D.[code] AS varchar(100)) AS [code],
    CAST(D.[description] AS varchar(100)) AS [description],
    CAST(D.[kind] AS varchar(1)) AS [kind],
    CAST(D.[statusCode] AS smallint) AS [statusCode],
    CAST(D.[phaseTypeId] AS smallint) AS [phaseTypeId],
    CAST(D.[machineCode] AS varchar(15)) AS [machineCode],
    CAST(D.[plannedDate] AS date) AS [plannedDate],
    CAST(D.[workOrderCode] AS varchar(10)) AS [workOrderCode],
    CAST(D.[toolCode] AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(NULL AS int) AS [operationId],
    CAST(NULL AS int) AS [rowType],
    CAST(NULL AS numeric(38,6)) AS [qty],
    CAST(D.[setupSec] AS decimal(38,1)) AS [setupSec],
    CAST(NULL AS numeric(19,6)) AS [unitSec],
    CAST(D.[createdOn] AS date) AS [createdOn],
    CAST(D.[sideBySide] AS bit) AS [sideBySide],
    CAST(D.[forcedCutSec] AS int) AS [forcedCutSec],
    CAST(D.[cutSec] AS int) AS [cutSec],
    CAST(D.[sheetRepetitions] AS int) AS [sheetRepetitions],
    CAST(D.[memberOperations] AS int) AS [memberOperations],
    CAST(D.[minMemberDueDate] AS date) AS [minMemberDueDate],
    CAST(D.[sourceLastUpdate] AS datetime2(6)) AS [sourceLastUpdate],
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [phaseId],
    CAST(NULL AS numeric(18,6)) AS [memberQty],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS int) AS [nestedQty],
    CAST(NULL AS int) AS [requiredQty],
    CAST(NULL AS numeric(18,6)) AS [weightNested],
    CAST(NULL AS smallint) AS [sheetSlot],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS varchar(160)) AS [sheetDescription],
    CAST(NULL AS varchar(15)) AS [material],
    CAST(NULL AS numeric(18,6)) AS [widthMm],
    CAST(NULL AS numeric(18,6)) AS [lengthMm],
    CAST(NULL AS numeric(18,6)) AS [thicknessMm],
    CAST(NULL AS numeric(18,6)) AS [sheetWeightKg],
    CAST(NULL AS varchar(15)) AS [warehouse],
    CAST(NULL AS varchar(15)) AS [location],
    CAST(NULL AS smallint) AS [sheetsRequired],
    CAST(NULL AS numeric(18,6)) AS [sheetsReserved],
    CAST(NULL AS int) AS [repetitions],
    CAST(NULL AS int) AS [cutSecPerRepetition],
    CAST(NULL AS float) AS [cutAreaMm2],
    CAST(NULL AS decimal(18,6)) AS [usedPct],
    CAST(NULL AS decimal(18,6)) AS [scrapPct]
FROM Programmi AS D

UNION ALL

SELECT
    CAST(N'Membri' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[workplanId] AS int) AS [workplanId],
    CAST(NULL AS varchar(100)) AS [code],
    CAST(NULL AS varchar(100)) AS [description],
    CAST(NULL AS varchar(1)) AS [kind],
    CAST(NULL AS smallint) AS [statusCode],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(15)) AS [machineCode],
    CAST(NULL AS date) AS [plannedDate],
    CAST(D.[workOrderCode] AS varchar(10)) AS [workOrderCode],
    CAST(NULL AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(D.[operationId] AS int) AS [operationId],
    CAST(NULL AS int) AS [rowType],
    CAST(NULL AS numeric(38,6)) AS [qty],
    CAST(NULL AS decimal(38,1)) AS [setupSec],
    CAST(NULL AS numeric(19,6)) AS [unitSec],
    CAST(NULL AS date) AS [createdOn],
    CAST(NULL AS bit) AS [sideBySide],
    CAST(NULL AS int) AS [forcedCutSec],
    CAST(NULL AS int) AS [cutSec],
    CAST(NULL AS int) AS [sheetRepetitions],
    CAST(NULL AS int) AS [memberOperations],
    CAST(NULL AS date) AS [minMemberDueDate],
    CAST(NULL AS datetime2(6)) AS [sourceLastUpdate],
    CAST(D.[orderId] AS int) AS [orderId],
    CAST(D.[lotId] AS int) AS [lotId],
    CAST(D.[phaseId] AS int) AS [phaseId],
    CAST(D.[memberQty] AS numeric(18,6)) AS [memberQty],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS int) AS [nestedQty],
    CAST(NULL AS int) AS [requiredQty],
    CAST(NULL AS numeric(18,6)) AS [weightNested],
    CAST(NULL AS smallint) AS [sheetSlot],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS varchar(160)) AS [sheetDescription],
    CAST(NULL AS varchar(15)) AS [material],
    CAST(NULL AS numeric(18,6)) AS [widthMm],
    CAST(NULL AS numeric(18,6)) AS [lengthMm],
    CAST(NULL AS numeric(18,6)) AS [thicknessMm],
    CAST(NULL AS numeric(18,6)) AS [sheetWeightKg],
    CAST(NULL AS varchar(15)) AS [warehouse],
    CAST(NULL AS varchar(15)) AS [location],
    CAST(NULL AS smallint) AS [sheetsRequired],
    CAST(NULL AS numeric(18,6)) AS [sheetsReserved],
    CAST(NULL AS int) AS [repetitions],
    CAST(NULL AS int) AS [cutSecPerRepetition],
    CAST(NULL AS float) AS [cutAreaMm2],
    CAST(NULL AS decimal(18,6)) AS [usedPct],
    CAST(NULL AS decimal(18,6)) AS [scrapPct]
FROM Membri AS D

UNION ALL

SELECT
    CAST(N'Quantita' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[workplanId] AS int) AS [workplanId],
    CAST(NULL AS varchar(100)) AS [code],
    CAST(NULL AS varchar(100)) AS [description],
    CAST(NULL AS varchar(1)) AS [kind],
    CAST(NULL AS smallint) AS [statusCode],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(15)) AS [machineCode],
    CAST(NULL AS date) AS [plannedDate],
    CAST(NULL AS varchar(10)) AS [workOrderCode],
    CAST(NULL AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(NULL AS int) AS [operationId],
    CAST(NULL AS int) AS [rowType],
    CAST(NULL AS numeric(38,6)) AS [qty],
    CAST(NULL AS decimal(38,1)) AS [setupSec],
    CAST(NULL AS numeric(19,6)) AS [unitSec],
    CAST(NULL AS date) AS [createdOn],
    CAST(NULL AS bit) AS [sideBySide],
    CAST(NULL AS int) AS [forcedCutSec],
    CAST(NULL AS int) AS [cutSec],
    CAST(NULL AS int) AS [sheetRepetitions],
    CAST(NULL AS int) AS [memberOperations],
    CAST(NULL AS date) AS [minMemberDueDate],
    CAST(NULL AS datetime2(6)) AS [sourceLastUpdate],
    CAST(D.[orderId] AS int) AS [orderId],
    CAST(D.[lotId] AS int) AS [lotId],
    CAST(D.[phaseId] AS int) AS [phaseId],
    CAST(NULL AS numeric(18,6)) AS [memberQty],
    CAST(D.[partCode] AS varchar(20)) AS [partCode],
    CAST(D.[nestedQty] AS int) AS [nestedQty],
    CAST(D.[requiredQty] AS int) AS [requiredQty],
    CAST(D.[weightNested] AS numeric(18,6)) AS [weightNested],
    CAST(NULL AS smallint) AS [sheetSlot],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS varchar(160)) AS [sheetDescription],
    CAST(NULL AS varchar(15)) AS [material],
    CAST(NULL AS numeric(18,6)) AS [widthMm],
    CAST(NULL AS numeric(18,6)) AS [lengthMm],
    CAST(NULL AS numeric(18,6)) AS [thicknessMm],
    CAST(NULL AS numeric(18,6)) AS [sheetWeightKg],
    CAST(NULL AS varchar(15)) AS [warehouse],
    CAST(NULL AS varchar(15)) AS [location],
    CAST(NULL AS smallint) AS [sheetsRequired],
    CAST(NULL AS numeric(18,6)) AS [sheetsReserved],
    CAST(NULL AS int) AS [repetitions],
    CAST(NULL AS int) AS [cutSecPerRepetition],
    CAST(NULL AS float) AS [cutAreaMm2],
    CAST(NULL AS decimal(18,6)) AS [usedPct],
    CAST(NULL AS decimal(18,6)) AS [scrapPct]
FROM Quantita AS D

UNION ALL

SELECT
    CAST(N'Lamiere' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[workplanId] AS int) AS [workplanId],
    CAST(NULL AS varchar(100)) AS [code],
    CAST(NULL AS varchar(100)) AS [description],
    CAST(NULL AS varchar(1)) AS [kind],
    CAST(NULL AS smallint) AS [statusCode],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(15)) AS [machineCode],
    CAST(NULL AS date) AS [plannedDate],
    CAST(NULL AS varchar(10)) AS [workOrderCode],
    CAST(NULL AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(NULL AS int) AS [operationId],
    CAST(NULL AS int) AS [rowType],
    CAST(NULL AS numeric(38,6)) AS [qty],
    CAST(NULL AS decimal(38,1)) AS [setupSec],
    CAST(NULL AS numeric(19,6)) AS [unitSec],
    CAST(NULL AS date) AS [createdOn],
    CAST(NULL AS bit) AS [sideBySide],
    CAST(NULL AS int) AS [forcedCutSec],
    CAST(NULL AS int) AS [cutSec],
    CAST(NULL AS int) AS [sheetRepetitions],
    CAST(NULL AS int) AS [memberOperations],
    CAST(NULL AS date) AS [minMemberDueDate],
    CAST(NULL AS datetime2(6)) AS [sourceLastUpdate],
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [phaseId],
    CAST(NULL AS numeric(18,6)) AS [memberQty],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS int) AS [nestedQty],
    CAST(NULL AS int) AS [requiredQty],
    CAST(NULL AS numeric(18,6)) AS [weightNested],
    CAST(D.[sheetSlot] AS smallint) AS [sheetSlot],
    CAST(D.[sheetPartCode] AS varchar(20)) AS [sheetPartCode],
    CAST(D.[sheetDescription] AS varchar(160)) AS [sheetDescription],
    CAST(D.[material] AS varchar(15)) AS [material],
    CAST(D.[widthMm] AS numeric(18,6)) AS [widthMm],
    CAST(D.[lengthMm] AS numeric(18,6)) AS [lengthMm],
    CAST(D.[thicknessMm] AS numeric(18,6)) AS [thicknessMm],
    CAST(D.[sheetWeightKg] AS numeric(18,6)) AS [sheetWeightKg],
    CAST(D.[warehouse] AS varchar(15)) AS [warehouse],
    CAST(D.[location] AS varchar(15)) AS [location],
    CAST(D.[sheetsRequired] AS smallint) AS [sheetsRequired],
    CAST(D.[sheetsReserved] AS numeric(18,6)) AS [sheetsReserved],
    CAST(D.[repetitions] AS int) AS [repetitions],
    CAST(D.[cutSecPerRepetition] AS int) AS [cutSecPerRepetition],
    CAST(D.[cutAreaMm2] AS float) AS [cutAreaMm2],
    CAST(D.[usedPct] AS decimal(18,6)) AS [usedPct],
    CAST(D.[scrapPct] AS decimal(18,6)) AS [scrapPct]
FROM Lamiere AS D;
