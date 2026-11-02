/*
   dbo.X_CLIENTE_Planning_Commesse - contratto Factory -> Excel, versione 2.
   AREA: Clienti, commesse, righe commerciali, albero dei lotti, sedi e descrizioni degli stati.
   FOGLI ALIMENTATI: Commesse, Clienti, Righe ordine, Lotti produttivi. Tutte le righe sono anche conservate in Provenienza record.

   COME LEGGERLA E MODIFICARLA
   - Una riga conserva UN tipo di record, indicato da TipoRecord.
   - I blocchi CTE iniziali contengono le SELECT di lavoro da modificare.
   - L UNION ALL finale allinea colonne e tipi: NULL significa non pertinente.
   - Non unire righe di grana diversa con JOIN, per non moltiplicare quantita o tempi.
   - Non rinominare TipoRecord o colonne senza aggiornare anche il contratto del connettore.
   - Esempio: SELECT * FROM dbo.X_CLIENTE_Planning_Commesse WHERE TipoRecord=N'Clienti'.

   PERIMETRO: X_CLIENTE_Planning_Perimetro conserva le fasi aperte delle commesse non chiuse.
   MANCANTI: NULL rimane mancante, zero rimane un valore; nessun arrivo o tempo viene inventato.
   DATE: valori locali Factory; il connettore assegna Europe/Rome, conserva l unita e applica l orizzonte.
   UNITA: tempi in secondi, percentuali 0..100, quantita nella unita esplicita dell articolo.
   La vista non scrive su Factory. Il pianificatore legge l Excel, mai direttamente questa vista.
*/
CREATE OR ALTER VIEW dbo.X_CLIENTE_Planning_Commesse
AS
WITH
/* Clienti: Una riga del tipo Clienti. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
Clienti AS (
SELECT CL.IDCLI AS customerId, CL.CTCOD AS code, CL.CTRAG AS name,
       ISNULL(CL.Priority, 0) AS priority, CL.IDCLS AS classId, CF.CLDSC AS className,
       ISNULL(CF.CLPRI, 0) AS classPriority
FROM dbo.A_CLI CL
LEFT JOIN dbo.A_CLCF CF ON CF.IDCLS = CL.IDCLS
WHERE CL.CTCOD IN (SELECT C.CTCOD FROM dbo.A_COM C JOIN (SELECT DISTINCT CONUM FROM dbo.X_CLIENTE_Planning_Perimetro) U ON U.CONUM = C.CONUM)
),

/* Commesse: Una riga del tipo Commesse. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
Commesse AS (
SELECT C.CONUM AS orderId, C.COCOD AS code, C.CODSC AS description, C.CTCOD AS customerCode,
       C.COSTO AS statusCode, C.CODTE AS issueDate, C.CODCC AS dueDate, C.COHCC AS dueTime,
       C.CORIF AS reference, C.COPNF AS planningModeSaved,
       C.COSCH AS fixedStartFlag, C.CODTS AS fixedStartAt,
       C.CODIP AS plannedStartDate, C.COHIP AS plannedStartTime,
       C.CODFP AS plannedEndDate, C.COHFP AS plannedEndTime,
       C.IDTPC AS orderTypeId, C.COTYP AS orderKind
FROM dbo.A_COM C JOIN (SELECT DISTINCT CONUM FROM dbo.X_CLIENTE_Planning_Perimetro) U ON U.CONUM = C.CONUM
),

/* RigheOrdine: Una riga del tipo RigheOrdine. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
RigheOrdine AS (
SELECT P.CONUM AS orderId, P.FINUM AS lineNumber, P.LOCOD AS finalLotId, P.PACOD AS partCode,
       P.PADSC AS partDescription, P.PAUDM AS uom, P.LQNTA AS qtyOrdered, P.LQCON AS qtyDelivered,
       P.CMDTS AS dueDate, P.PADCR AS requestedDate, P.STCON AS deliveryStatusCode,
       P.PASCH AS fixedStartFlag, P.PADTS AS fixedStartAt, P.Priority AS priority
FROM dbo.L_CMPA P JOIN (SELECT DISTINCT CONUM FROM dbo.X_CLIENTE_Planning_Perimetro) U ON U.CONUM = P.CONUM
WHERE P.LOCOD <> 0
),

/* Lotti: Una riga del tipo Lotti. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
Lotti AS (
SELECT T.CONUM AS orderId, T.LOCOD AS lotId, NULLIF(NULLIF(T.LOCLP, 0), T.LOCOD) AS parentLotId,   
       T.LOCLM AS finalLotId, T.LOCLC AS linkedLotId,
       T.PACOD AS partCode, T.PADSC AS partDescription, T.PAUDM AS uom, T.LOQTP AS qty,
       T.LOSTO AS statusCode, ISNULL(T.LOPRI, 0) AS priority, T.LODSC AS dueDate, T.LOHSC AS dueTime,
       T.LOSCH AS fixedStartFlag, T.LODTS AS fixedStartAt,
       T.FMCOD AS familyCode, T.MTCOD AS materialCode,
       T.PADX1 AS dimX, T.PADY1 AS dimY, T.PADZ1 AS dimZ, T.PATPA AS partTypeId,
       T.LODIP AS plannedStartDate, T.LOHIP AS plannedStartTime, T.LODFP AS plannedEndDate, T.LOHFP AS plannedEndTime,
       T.MaterialMissing AS materialMissing, T.ProductionCritical AS productionCritical
FROM dbo.A_LOT T JOIN (SELECT T.CONUM,T.LOCOD FROM dbo.A_LOT T WHERE EXISTS (SELECT 1 FROM dbo.X_CLIENTE_Planning_Perimetro P WHERE P.CONUM=T.CONUM)) U ON U.CONUM = T.CONUM AND U.LOCOD = T.LOCOD
),

/* SediCommesse: Una riga del tipo SediCommesse. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
SediCommesse AS (
SELECT C.CONUM AS orderId, C.COCOD AS orderCode, C.Division AS divisionId,
       CASE C.Division WHEN 3 THEN 'Sede A' WHEN 4 THEN 'Sede B' END AS siteName
FROM dbo.A_COM C WHERE C.COSTO NOT IN (24, 25)
),

/* StatiCommesse: Una riga del tipo StatiCommesse. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
StatiCommesse AS (
SELECT STCOD AS statusCode, STDSC AS description FROM dbo.A_STA WHERE STTYP = 'P'
)

/* Allineamento del contratto; la logica di business e nei CTE sopra. */
SELECT
    CAST(N'Clienti' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[customerId] AS int) AS [customerId],
    CAST(D.[code] AS varchar(15)) AS [code],
    CAST(D.[name] AS varchar(80)) AS [name],
    CAST(D.[priority] AS smallint) AS [priority],
    CAST(D.[classId] AS int) AS [classId],
    CAST(D.[className] AS varchar(40)) AS [className],
    CAST(D.[classPriority] AS int) AS [classPriority],
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS nvarchar(max)) AS [description],
    CAST(NULL AS varchar(15)) AS [customerCode],
    CAST(NULL AS smallint) AS [statusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(NULL AS date) AS [dueDate],
    CAST(NULL AS time(6)) AS [dueTime],
    CAST(NULL AS varchar(40)) AS [reference],
    CAST(NULL AS int) AS [planningModeSaved],
    CAST(NULL AS bigint) AS [fixedStartFlag],
    CAST(NULL AS datetime2(6)) AS [fixedStartAt],
    CAST(NULL AS date) AS [plannedStartDate],
    CAST(NULL AS time(6)) AS [plannedStartTime],
    CAST(NULL AS date) AS [plannedEndDate],
    CAST(NULL AS time(6)) AS [plannedEndTime],
    CAST(NULL AS int) AS [orderTypeId],
    CAST(NULL AS varchar(1)) AS [orderKind],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [finalLotId],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [partDescription],
    CAST(NULL AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyOrdered],
    CAST(NULL AS numeric(18,6)) AS [qtyDelivered],
    CAST(NULL AS date) AS [requestedDate],
    CAST(NULL AS smallint) AS [deliveryStatusCode],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [parentLotId],
    CAST(NULL AS int) AS [linkedLotId],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS varchar(15)) AS [familyCode],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [dimX],
    CAST(NULL AS numeric(18,6)) AS [dimY],
    CAST(NULL AS numeric(18,6)) AS [dimZ],
    CAST(NULL AS int) AS [partTypeId],
    CAST(NULL AS smallint) AS [materialMissing],
    CAST(NULL AS smallint) AS [productionCritical],
    CAST(NULL AS varchar(15)) AS [orderCode],
    CAST(NULL AS int) AS [divisionId],
    CAST(NULL AS varchar(15)) AS [siteName]
FROM Clienti AS D

UNION ALL

SELECT
    CAST(N'Commesse' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [customerId],
    CAST(D.[code] AS varchar(15)) AS [code],
    CAST(NULL AS varchar(80)) AS [name],
    CAST(NULL AS smallint) AS [priority],
    CAST(NULL AS int) AS [classId],
    CAST(NULL AS varchar(40)) AS [className],
    CAST(NULL AS int) AS [classPriority],
    CAST(D.[orderId] AS int) AS [orderId],
    CAST(D.[description] AS nvarchar(max)) AS [description],
    CAST(D.[customerCode] AS varchar(15)) AS [customerCode],
    CAST(D.[statusCode] AS smallint) AS [statusCode],
    CAST(D.[issueDate] AS date) AS [issueDate],
    CAST(D.[dueDate] AS date) AS [dueDate],
    CAST(D.[dueTime] AS time(6)) AS [dueTime],
    CAST(D.[reference] AS varchar(40)) AS [reference],
    CAST(D.[planningModeSaved] AS int) AS [planningModeSaved],
    CAST(D.[fixedStartFlag] AS bigint) AS [fixedStartFlag],
    CAST(D.[fixedStartAt] AS datetime2(6)) AS [fixedStartAt],
    CAST(D.[plannedStartDate] AS date) AS [plannedStartDate],
    CAST(D.[plannedStartTime] AS time(6)) AS [plannedStartTime],
    CAST(D.[plannedEndDate] AS date) AS [plannedEndDate],
    CAST(D.[plannedEndTime] AS time(6)) AS [plannedEndTime],
    CAST(D.[orderTypeId] AS int) AS [orderTypeId],
    CAST(D.[orderKind] AS varchar(1)) AS [orderKind],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [finalLotId],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [partDescription],
    CAST(NULL AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyOrdered],
    CAST(NULL AS numeric(18,6)) AS [qtyDelivered],
    CAST(NULL AS date) AS [requestedDate],
    CAST(NULL AS smallint) AS [deliveryStatusCode],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [parentLotId],
    CAST(NULL AS int) AS [linkedLotId],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS varchar(15)) AS [familyCode],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [dimX],
    CAST(NULL AS numeric(18,6)) AS [dimY],
    CAST(NULL AS numeric(18,6)) AS [dimZ],
    CAST(NULL AS int) AS [partTypeId],
    CAST(NULL AS smallint) AS [materialMissing],
    CAST(NULL AS smallint) AS [productionCritical],
    CAST(NULL AS varchar(15)) AS [orderCode],
    CAST(NULL AS int) AS [divisionId],
    CAST(NULL AS varchar(15)) AS [siteName]
FROM Commesse AS D

UNION ALL

SELECT
    CAST(N'RigheOrdine' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [customerId],
    CAST(NULL AS varchar(15)) AS [code],
    CAST(NULL AS varchar(80)) AS [name],
    CAST(D.[priority] AS smallint) AS [priority],
    CAST(NULL AS int) AS [classId],
    CAST(NULL AS varchar(40)) AS [className],
    CAST(NULL AS int) AS [classPriority],
    CAST(D.[orderId] AS int) AS [orderId],
    CAST(NULL AS nvarchar(max)) AS [description],
    CAST(NULL AS varchar(15)) AS [customerCode],
    CAST(NULL AS smallint) AS [statusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(D.[dueDate] AS date) AS [dueDate],
    CAST(NULL AS time(6)) AS [dueTime],
    CAST(NULL AS varchar(40)) AS [reference],
    CAST(NULL AS int) AS [planningModeSaved],
    CAST(D.[fixedStartFlag] AS bigint) AS [fixedStartFlag],
    CAST(D.[fixedStartAt] AS datetime2(6)) AS [fixedStartAt],
    CAST(NULL AS date) AS [plannedStartDate],
    CAST(NULL AS time(6)) AS [plannedStartTime],
    CAST(NULL AS date) AS [plannedEndDate],
    CAST(NULL AS time(6)) AS [plannedEndTime],
    CAST(NULL AS int) AS [orderTypeId],
    CAST(NULL AS varchar(1)) AS [orderKind],
    CAST(D.[lineNumber] AS int) AS [lineNumber],
    CAST(D.[finalLotId] AS int) AS [finalLotId],
    CAST(D.[partCode] AS varchar(20)) AS [partCode],
    CAST(D.[partDescription] AS varchar(160)) AS [partDescription],
    CAST(D.[uom] AS varchar(10)) AS [uom],
    CAST(D.[qtyOrdered] AS numeric(18,6)) AS [qtyOrdered],
    CAST(D.[qtyDelivered] AS numeric(18,6)) AS [qtyDelivered],
    CAST(D.[requestedDate] AS date) AS [requestedDate],
    CAST(D.[deliveryStatusCode] AS smallint) AS [deliveryStatusCode],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [parentLotId],
    CAST(NULL AS int) AS [linkedLotId],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS varchar(15)) AS [familyCode],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [dimX],
    CAST(NULL AS numeric(18,6)) AS [dimY],
    CAST(NULL AS numeric(18,6)) AS [dimZ],
    CAST(NULL AS int) AS [partTypeId],
    CAST(NULL AS smallint) AS [materialMissing],
    CAST(NULL AS smallint) AS [productionCritical],
    CAST(NULL AS varchar(15)) AS [orderCode],
    CAST(NULL AS int) AS [divisionId],
    CAST(NULL AS varchar(15)) AS [siteName]
FROM RigheOrdine AS D

UNION ALL

SELECT
    CAST(N'Lotti' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [customerId],
    CAST(NULL AS varchar(15)) AS [code],
    CAST(NULL AS varchar(80)) AS [name],
    CAST(D.[priority] AS smallint) AS [priority],
    CAST(NULL AS int) AS [classId],
    CAST(NULL AS varchar(40)) AS [className],
    CAST(NULL AS int) AS [classPriority],
    CAST(D.[orderId] AS int) AS [orderId],
    CAST(NULL AS nvarchar(max)) AS [description],
    CAST(NULL AS varchar(15)) AS [customerCode],
    CAST(D.[statusCode] AS smallint) AS [statusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(D.[dueDate] AS date) AS [dueDate],
    CAST(D.[dueTime] AS time(6)) AS [dueTime],
    CAST(NULL AS varchar(40)) AS [reference],
    CAST(NULL AS int) AS [planningModeSaved],
    CAST(D.[fixedStartFlag] AS bigint) AS [fixedStartFlag],
    CAST(D.[fixedStartAt] AS datetime2(6)) AS [fixedStartAt],
    CAST(D.[plannedStartDate] AS date) AS [plannedStartDate],
    CAST(D.[plannedStartTime] AS time(6)) AS [plannedStartTime],
    CAST(D.[plannedEndDate] AS date) AS [plannedEndDate],
    CAST(D.[plannedEndTime] AS time(6)) AS [plannedEndTime],
    CAST(NULL AS int) AS [orderTypeId],
    CAST(NULL AS varchar(1)) AS [orderKind],
    CAST(NULL AS int) AS [lineNumber],
    CAST(D.[finalLotId] AS int) AS [finalLotId],
    CAST(D.[partCode] AS varchar(20)) AS [partCode],
    CAST(D.[partDescription] AS varchar(160)) AS [partDescription],
    CAST(D.[uom] AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyOrdered],
    CAST(NULL AS numeric(18,6)) AS [qtyDelivered],
    CAST(NULL AS date) AS [requestedDate],
    CAST(NULL AS smallint) AS [deliveryStatusCode],
    CAST(D.[lotId] AS int) AS [lotId],
    CAST(D.[parentLotId] AS int) AS [parentLotId],
    CAST(D.[linkedLotId] AS int) AS [linkedLotId],
    CAST(D.[qty] AS numeric(18,6)) AS [qty],
    CAST(D.[familyCode] AS varchar(15)) AS [familyCode],
    CAST(D.[materialCode] AS varchar(15)) AS [materialCode],
    CAST(D.[dimX] AS numeric(18,6)) AS [dimX],
    CAST(D.[dimY] AS numeric(18,6)) AS [dimY],
    CAST(D.[dimZ] AS numeric(18,6)) AS [dimZ],
    CAST(D.[partTypeId] AS int) AS [partTypeId],
    CAST(D.[materialMissing] AS smallint) AS [materialMissing],
    CAST(D.[productionCritical] AS smallint) AS [productionCritical],
    CAST(NULL AS varchar(15)) AS [orderCode],
    CAST(NULL AS int) AS [divisionId],
    CAST(NULL AS varchar(15)) AS [siteName]
FROM Lotti AS D

UNION ALL

SELECT
    CAST(N'SediCommesse' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [customerId],
    CAST(NULL AS varchar(15)) AS [code],
    CAST(NULL AS varchar(80)) AS [name],
    CAST(NULL AS smallint) AS [priority],
    CAST(NULL AS int) AS [classId],
    CAST(NULL AS varchar(40)) AS [className],
    CAST(NULL AS int) AS [classPriority],
    CAST(D.[orderId] AS int) AS [orderId],
    CAST(NULL AS nvarchar(max)) AS [description],
    CAST(NULL AS varchar(15)) AS [customerCode],
    CAST(NULL AS smallint) AS [statusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(NULL AS date) AS [dueDate],
    CAST(NULL AS time(6)) AS [dueTime],
    CAST(NULL AS varchar(40)) AS [reference],
    CAST(NULL AS int) AS [planningModeSaved],
    CAST(NULL AS bigint) AS [fixedStartFlag],
    CAST(NULL AS datetime2(6)) AS [fixedStartAt],
    CAST(NULL AS date) AS [plannedStartDate],
    CAST(NULL AS time(6)) AS [plannedStartTime],
    CAST(NULL AS date) AS [plannedEndDate],
    CAST(NULL AS time(6)) AS [plannedEndTime],
    CAST(NULL AS int) AS [orderTypeId],
    CAST(NULL AS varchar(1)) AS [orderKind],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [finalLotId],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [partDescription],
    CAST(NULL AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyOrdered],
    CAST(NULL AS numeric(18,6)) AS [qtyDelivered],
    CAST(NULL AS date) AS [requestedDate],
    CAST(NULL AS smallint) AS [deliveryStatusCode],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [parentLotId],
    CAST(NULL AS int) AS [linkedLotId],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS varchar(15)) AS [familyCode],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [dimX],
    CAST(NULL AS numeric(18,6)) AS [dimY],
    CAST(NULL AS numeric(18,6)) AS [dimZ],
    CAST(NULL AS int) AS [partTypeId],
    CAST(NULL AS smallint) AS [materialMissing],
    CAST(NULL AS smallint) AS [productionCritical],
    CAST(D.[orderCode] AS varchar(15)) AS [orderCode],
    CAST(D.[divisionId] AS int) AS [divisionId],
    CAST(D.[siteName] AS varchar(15)) AS [siteName]
FROM SediCommesse AS D

UNION ALL

SELECT
    CAST(N'StatiCommesse' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [customerId],
    CAST(NULL AS varchar(15)) AS [code],
    CAST(NULL AS varchar(80)) AS [name],
    CAST(NULL AS smallint) AS [priority],
    CAST(NULL AS int) AS [classId],
    CAST(NULL AS varchar(40)) AS [className],
    CAST(NULL AS int) AS [classPriority],
    CAST(NULL AS int) AS [orderId],
    CAST(D.[description] AS nvarchar(max)) AS [description],
    CAST(NULL AS varchar(15)) AS [customerCode],
    CAST(D.[statusCode] AS smallint) AS [statusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(NULL AS date) AS [dueDate],
    CAST(NULL AS time(6)) AS [dueTime],
    CAST(NULL AS varchar(40)) AS [reference],
    CAST(NULL AS int) AS [planningModeSaved],
    CAST(NULL AS bigint) AS [fixedStartFlag],
    CAST(NULL AS datetime2(6)) AS [fixedStartAt],
    CAST(NULL AS date) AS [plannedStartDate],
    CAST(NULL AS time(6)) AS [plannedStartTime],
    CAST(NULL AS date) AS [plannedEndDate],
    CAST(NULL AS time(6)) AS [plannedEndTime],
    CAST(NULL AS int) AS [orderTypeId],
    CAST(NULL AS varchar(1)) AS [orderKind],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [finalLotId],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [partDescription],
    CAST(NULL AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyOrdered],
    CAST(NULL AS numeric(18,6)) AS [qtyDelivered],
    CAST(NULL AS date) AS [requestedDate],
    CAST(NULL AS smallint) AS [deliveryStatusCode],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [parentLotId],
    CAST(NULL AS int) AS [linkedLotId],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS varchar(15)) AS [familyCode],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [dimX],
    CAST(NULL AS numeric(18,6)) AS [dimY],
    CAST(NULL AS numeric(18,6)) AS [dimZ],
    CAST(NULL AS int) AS [partTypeId],
    CAST(NULL AS smallint) AS [materialMissing],
    CAST(NULL AS smallint) AS [productionCritical],
    CAST(NULL AS varchar(15)) AS [orderCode],
    CAST(NULL AS int) AS [divisionId],
    CAST(NULL AS varchar(15)) AS [siteName]
FROM StatiCommesse AS D;
