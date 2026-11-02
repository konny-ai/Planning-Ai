/*
   dbo.X_CLIENTE_Planning_Materiali - contratto Factory -> Excel, versione 2.
   AREA: Distinte, richieste/arrivi, conto lavoro, giacenze e impegni, ordini e residui, unita e riserve nesting.
   FOGLI ALIMENTATI: Articoli, Disponibilita materiali, Fabbisogni materiali, Esecuzioni esterne. Tutte le righe sono anche conservate in Provenienza record.

   COME LEGGERLA E MODIFICARLA
   - Una riga conserva UN tipo di record, indicato da TipoRecord.
   - I blocchi CTE iniziali contengono le SELECT di lavoro da modificare.
   - L UNION ALL finale allinea colonne e tipi: NULL significa non pertinente.
   - Non unire righe di grana diversa con JOIN, per non moltiplicare quantita o tempi.
   - Non rinominare TipoRecord o colonne senza aggiornare anche il contratto del connettore.
   - Esempio: SELECT * FROM dbo.X_CLIENTE_Planning_Materiali WHERE TipoRecord=N'Componenti'.

   PERIMETRO: X_CLIENTE_Planning_Perimetro conserva le fasi aperte delle commesse non chiuse.
   MANCANTI: NULL rimane mancante, zero rimane un valore; nessun arrivo o tempo viene inventato.
   DATE: valori locali Factory; il connettore assegna Europe/Rome, conserva l unita e applica l orizzonte.
   UNITA: tempi in secondi, percentuali 0..100, quantita nella unita esplicita dell articolo.
   La vista non scrive su Factory. Il pianificatore legge l Excel, mai direttamente questa vista.
*/
CREATE OR ALTER VIEW dbo.X_CLIENTE_Planning_Materiali
AS
WITH
/* Componenti: Una riga del tipo Componenti. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
Componenti AS (
SELECT C.CONUM AS orderId, C.LOCLP AS lotId, C.FINUM AS lineNumber, C.FENUM AS componentNo,
       C.PACOD AS partCode, C.PADSC AS description, C.PAUDM AS uom, C.LOQTP AS qtyRequired,
       C.PAQIM AS qtyCommitted, C.FATYP AS phaseTypeId, C.PAPRE AS supplyKind, C.MTCOD AS materialCode
FROM dbo.L_CMFE C
WHERE C.CONUM IN (SELECT CONUM FROM (SELECT DISTINCT CONUM FROM dbo.X_CLIENTE_Planning_Perimetro) PC)
),

/* ArriviLotto: Una riga del tipo ArriviLotto. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
ArriviLotto AS (
SELECT R.CONUM AS orderId,
       (SELECT COALESCE(T.LOCLC, T.LOCOD) FROM dbo.A_LOT T WHERE T.CONUM = R.CONUM AND T.LOCOD = R.LOCLP) AS lotId,
       R.PACOD AS partCode, R.PAQTA AS qty, R.PADAT AS availableDate,
       'REQUEST' AS source, NULL AS purchaseOrderCode, NULL AS supplierCode, R.RFTYP AS requestType
FROM dbo.A_RFO R
WHERE R.IDORF IS NULL AND R.CONUM IN (SELECT CONUM FROM (SELECT DISTINCT CONUM FROM dbo.X_CLIENTE_Planning_Perimetro) PC)
UNION ALL
SELECT R.CONUM,
       (SELECT COALESCE(T.LOCLC, T.LOCOD) FROM dbo.A_LOT T WHERE T.CONUM = R.CONUM AND T.LOCOD = R.LOCLP),
       O.PACOD, O.PAQTA, O.PADCR, 'PURCHASE_ORDER', A.ORCOD, F.FOCOD, R.RFTYP
FROM dbo.L_OFPA O
JOIN dbo.A_ORF A ON A.IDORF = O.IDORF
LEFT JOIN dbo.A_FOR F ON F.IDFOR = A.IDFOR
JOIN dbo.A_RFO R ON R.IDORF = O.IDORF AND R.NMRIG = O.NMRIG
WHERE R.CONUM IN (SELECT CONUM FROM (SELECT DISTINCT CONUM FROM dbo.X_CLIENTE_Planning_Perimetro) PC)
  AND O.RISTO NOT IN (63, 65)
),

/* ContoLavoro: Una riga del tipo ContoLavoro. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
ContoLavoro AS (
SELECT J.IDLAV AS operationId, A.IDORF AS purchaseOrderId, A.ORCOD AS purchaseOrderCode, O.NMRIG AS rowNo,
       F.FOCOD AS supplierCode, O.PADCR AS promisedDate, O.PAQTA AS qty, O.RISTO AS rowStatusCode,
       A.ORSTO AS orderStatusCode, A.ORDTO AS issueDate
FROM dbo.X_CLIENTE_Planning_Perimetro J
JOIN dbo.L_OFLA L ON L.CONUM = J.CONUM AND L.LOCOD = J.LOCOD AND L.IDFAS = J.IDFAS
JOIN dbo.L_OFPA O ON O.IDORF = L.IDORF AND O.NMRIG = L.NMRIG
JOIN dbo.A_ORF A ON A.IDORF = O.IDORF
LEFT JOIN dbo.A_FOR F ON F.IDFOR = A.IDFOR
WHERE O.RISTO NOT IN (63, 65)
),

/* Giacenze: Una riga per articolo formato. stockAvailable e giacenza fisica in ubicazioni abilitate; engaged e impegnato. Il connettore usa max(0,stockAvailable-engaged), senza riaccredito implicito delle riserve. */
Giacenze AS (
SELECT S.FOCOD AS sheetPartCode,
       (SELECT ISNULL(SUM(M.QTLOC),0) FROM dbo.L_MLPA M WHERE M.PACOD = S.FOCOD AND M.LCABI = 'Y') AS stockAvailable,
       (SELECT ISNULL(SUM(Q.PAIMP),0) FROM dbo.L_PAQT Q WHERE Q.PACOD = S.FOCOD) AS engaged,
       (SELECT ISNULL(SUM(Q.PAORD),0) FROM dbo.L_PAQT Q WHERE Q.PACOD = S.FOCOD) AS onOrder
FROM (SELECT DISTINCT F.FOCOD FROM dbo.L_PCFO F JOIN dbo.A_NES N ON N.IDNES = F.IDPTC WHERE ISNULL(N.STNES,0) < 5) S
),

/* OrdiniFornitore: Una riga per ordine fornitore/riga aperta. Quantita totale ordinata, data dichiarata e stato; la disponibilita usa invece il residuo di ResiduiOrdini. */
OrdiniFornitore AS (
SELECT O.PACOD AS partCode, A.ORCOD AS purchaseOrderCode, F.FOCOD AS supplierCode, O.NMRIG AS rowNo, O.PAQTA AS qty,
       O.PADCR AS promisedDate, O.DeliveryDateConfirmed AS dateConfirmed, O.RISTO AS rowStatusCode
FROM dbo.L_OFPA O JOIN dbo.A_ORF A ON A.IDORF = O.IDORF LEFT JOIN dbo.A_FOR F ON F.IDFOR = A.IDFOR
WHERE O.RISTO NOT IN (63, 65)
),

/* RiserveNesting: Una riga del tipo RiserveNesting. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
RiserveNesting AS (
SELECT W.WorkplanID AS workplanId, W.WorkplanJobID AS operationId, W.StoreLocationPartTrackingID AS trackingId,
       W.InitialQuantity AS initialQty, W.ResidualQuantity AS residualQty, W.Mandatory AS mandatory
FROM dbo.WorkplanJobsSheets W JOIN dbo.A_NES N ON N.IDNES = W.WorkplanID WHERE ISNULL(N.STNES,0) < 5
),

/* ArticoliMateriali: Una riga per articolo presente nei formati dei nesting aperti; unita reale A_PAR.PAUDM, nessun default NR o KG. */
ArticoliMateriali AS (
SELECT DISTINCT RTRIM(P.PACOD) AS partCode, RTRIM(P.PAUDM) AS uom, P.PADSC AS description
FROM dbo.A_PAR P JOIN (SELECT DISTINCT F.FOCOD FROM dbo.L_PCFO F JOIN dbo.A_NES N ON N.IDNES=F.IDPTC WHERE ISNULL(N.STNES,0)<5) S ON S.FOCOD=P.PACOD
),

/* ResiduiOrdini: Una riga per ordine fornitore/riga aperta. orderedQty e deliveredQty sono nella unita interna uom: usare solo max(0,ordinato-consegnato), mai il totale gia consegnato. */
ResiduiOrdini AS (
SELECT RTRIM(O.PACOD) AS partCode, A.ORCOD AS purchaseOrderCode, O.NMRIG AS rowNo,
       O.PAQTA AS orderedQty, O.PAQCO AS deliveredQty, RTRIM(O.PAUDM) AS uom
FROM dbo.L_OFPA O JOIN dbo.A_ORF A ON A.IDORF=O.IDORF
WHERE O.RISTO NOT IN (63,65)
)

/* Allineamento del contratto; la logica di business e nei CTE sopra. */
SELECT
    CAST(N'Componenti' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[orderId] AS int) AS [orderId],
    CAST(D.[lotId] AS int) AS [lotId],
    CAST(D.[lineNumber] AS int) AS [lineNumber],
    CAST(D.[componentNo] AS int) AS [componentNo],
    CAST(D.[partCode] AS varchar(20)) AS [partCode],
    CAST(D.[description] AS varchar(160)) AS [description],
    CAST(D.[uom] AS varchar(10)) AS [uom],
    CAST(D.[qtyRequired] AS numeric(18,6)) AS [qtyRequired],
    CAST(D.[qtyCommitted] AS numeric(18,6)) AS [qtyCommitted],
    CAST(D.[phaseTypeId] AS smallint) AS [phaseTypeId],
    CAST(D.[supplyKind] AS varchar(1)) AS [supplyKind],
    CAST(D.[materialCode] AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS date) AS [availableDate],
    CAST(NULL AS varchar(14)) AS [source],
    CAST(NULL AS varchar(15)) AS [purchaseOrderCode],
    CAST(NULL AS varchar(15)) AS [supplierCode],
    CAST(NULL AS smallint) AS [requestType],
    CAST(NULL AS bigint) AS [operationId],
    CAST(NULL AS int) AS [purchaseOrderId],
    CAST(NULL AS smallint) AS [rowNo],
    CAST(NULL AS date) AS [promisedDate],
    CAST(NULL AS smallint) AS [rowStatusCode],
    CAST(NULL AS smallint) AS [orderStatusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS numeric(38,6)) AS [stockAvailable],
    CAST(NULL AS numeric(38,6)) AS [engaged],
    CAST(NULL AS numeric(38,6)) AS [onOrder],
    CAST(NULL AS smallint) AS [dateConfirmed],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [trackingId],
    CAST(NULL AS numeric(18,6)) AS [initialQty],
    CAST(NULL AS numeric(18,6)) AS [residualQty],
    CAST(NULL AS bit) AS [mandatory],
    CAST(NULL AS numeric(18,6)) AS [orderedQty],
    CAST(NULL AS numeric(18,6)) AS [deliveredQty]
FROM Componenti AS D

UNION ALL

SELECT
    CAST(N'ArriviLotto' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[orderId] AS int) AS [orderId],
    CAST(D.[lotId] AS int) AS [lotId],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [componentNo],
    CAST(D.[partCode] AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [description],
    CAST(NULL AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyRequired],
    CAST(NULL AS numeric(18,6)) AS [qtyCommitted],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(1)) AS [supplyKind],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(D.[qty] AS numeric(18,6)) AS [qty],
    CAST(D.[availableDate] AS date) AS [availableDate],
    CAST(D.[source] AS varchar(14)) AS [source],
    CAST(D.[purchaseOrderCode] AS varchar(15)) AS [purchaseOrderCode],
    CAST(D.[supplierCode] AS varchar(15)) AS [supplierCode],
    CAST(D.[requestType] AS smallint) AS [requestType],
    CAST(NULL AS bigint) AS [operationId],
    CAST(NULL AS int) AS [purchaseOrderId],
    CAST(NULL AS smallint) AS [rowNo],
    CAST(NULL AS date) AS [promisedDate],
    CAST(NULL AS smallint) AS [rowStatusCode],
    CAST(NULL AS smallint) AS [orderStatusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS numeric(38,6)) AS [stockAvailable],
    CAST(NULL AS numeric(38,6)) AS [engaged],
    CAST(NULL AS numeric(38,6)) AS [onOrder],
    CAST(NULL AS smallint) AS [dateConfirmed],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [trackingId],
    CAST(NULL AS numeric(18,6)) AS [initialQty],
    CAST(NULL AS numeric(18,6)) AS [residualQty],
    CAST(NULL AS bit) AS [mandatory],
    CAST(NULL AS numeric(18,6)) AS [orderedQty],
    CAST(NULL AS numeric(18,6)) AS [deliveredQty]
FROM ArriviLotto AS D

UNION ALL

SELECT
    CAST(N'ContoLavoro' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [componentNo],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [description],
    CAST(NULL AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyRequired],
    CAST(NULL AS numeric(18,6)) AS [qtyCommitted],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(1)) AS [supplyKind],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(D.[qty] AS numeric(18,6)) AS [qty],
    CAST(NULL AS date) AS [availableDate],
    CAST(NULL AS varchar(14)) AS [source],
    CAST(D.[purchaseOrderCode] AS varchar(15)) AS [purchaseOrderCode],
    CAST(D.[supplierCode] AS varchar(15)) AS [supplierCode],
    CAST(NULL AS smallint) AS [requestType],
    CAST(D.[operationId] AS bigint) AS [operationId],
    CAST(D.[purchaseOrderId] AS int) AS [purchaseOrderId],
    CAST(D.[rowNo] AS smallint) AS [rowNo],
    CAST(D.[promisedDate] AS date) AS [promisedDate],
    CAST(D.[rowStatusCode] AS smallint) AS [rowStatusCode],
    CAST(D.[orderStatusCode] AS smallint) AS [orderStatusCode],
    CAST(D.[issueDate] AS date) AS [issueDate],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS numeric(38,6)) AS [stockAvailable],
    CAST(NULL AS numeric(38,6)) AS [engaged],
    CAST(NULL AS numeric(38,6)) AS [onOrder],
    CAST(NULL AS smallint) AS [dateConfirmed],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [trackingId],
    CAST(NULL AS numeric(18,6)) AS [initialQty],
    CAST(NULL AS numeric(18,6)) AS [residualQty],
    CAST(NULL AS bit) AS [mandatory],
    CAST(NULL AS numeric(18,6)) AS [orderedQty],
    CAST(NULL AS numeric(18,6)) AS [deliveredQty]
FROM ContoLavoro AS D

UNION ALL

SELECT
    CAST(N'Giacenze' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [componentNo],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [description],
    CAST(NULL AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyRequired],
    CAST(NULL AS numeric(18,6)) AS [qtyCommitted],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(1)) AS [supplyKind],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS date) AS [availableDate],
    CAST(NULL AS varchar(14)) AS [source],
    CAST(NULL AS varchar(15)) AS [purchaseOrderCode],
    CAST(NULL AS varchar(15)) AS [supplierCode],
    CAST(NULL AS smallint) AS [requestType],
    CAST(NULL AS bigint) AS [operationId],
    CAST(NULL AS int) AS [purchaseOrderId],
    CAST(NULL AS smallint) AS [rowNo],
    CAST(NULL AS date) AS [promisedDate],
    CAST(NULL AS smallint) AS [rowStatusCode],
    CAST(NULL AS smallint) AS [orderStatusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(D.[sheetPartCode] AS varchar(20)) AS [sheetPartCode],
    CAST(D.[stockAvailable] AS numeric(38,6)) AS [stockAvailable],
    CAST(D.[engaged] AS numeric(38,6)) AS [engaged],
    CAST(D.[onOrder] AS numeric(38,6)) AS [onOrder],
    CAST(NULL AS smallint) AS [dateConfirmed],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [trackingId],
    CAST(NULL AS numeric(18,6)) AS [initialQty],
    CAST(NULL AS numeric(18,6)) AS [residualQty],
    CAST(NULL AS bit) AS [mandatory],
    CAST(NULL AS numeric(18,6)) AS [orderedQty],
    CAST(NULL AS numeric(18,6)) AS [deliveredQty]
FROM Giacenze AS D

UNION ALL

SELECT
    CAST(N'OrdiniFornitore' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [componentNo],
    CAST(D.[partCode] AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [description],
    CAST(NULL AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyRequired],
    CAST(NULL AS numeric(18,6)) AS [qtyCommitted],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(1)) AS [supplyKind],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(D.[qty] AS numeric(18,6)) AS [qty],
    CAST(NULL AS date) AS [availableDate],
    CAST(NULL AS varchar(14)) AS [source],
    CAST(D.[purchaseOrderCode] AS varchar(15)) AS [purchaseOrderCode],
    CAST(D.[supplierCode] AS varchar(15)) AS [supplierCode],
    CAST(NULL AS smallint) AS [requestType],
    CAST(NULL AS bigint) AS [operationId],
    CAST(NULL AS int) AS [purchaseOrderId],
    CAST(D.[rowNo] AS smallint) AS [rowNo],
    CAST(D.[promisedDate] AS date) AS [promisedDate],
    CAST(D.[rowStatusCode] AS smallint) AS [rowStatusCode],
    CAST(NULL AS smallint) AS [orderStatusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS numeric(38,6)) AS [stockAvailable],
    CAST(NULL AS numeric(38,6)) AS [engaged],
    CAST(NULL AS numeric(38,6)) AS [onOrder],
    CAST(D.[dateConfirmed] AS smallint) AS [dateConfirmed],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [trackingId],
    CAST(NULL AS numeric(18,6)) AS [initialQty],
    CAST(NULL AS numeric(18,6)) AS [residualQty],
    CAST(NULL AS bit) AS [mandatory],
    CAST(NULL AS numeric(18,6)) AS [orderedQty],
    CAST(NULL AS numeric(18,6)) AS [deliveredQty]
FROM OrdiniFornitore AS D

UNION ALL

SELECT
    CAST(N'RiserveNesting' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [componentNo],
    CAST(NULL AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [description],
    CAST(NULL AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyRequired],
    CAST(NULL AS numeric(18,6)) AS [qtyCommitted],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(1)) AS [supplyKind],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS date) AS [availableDate],
    CAST(NULL AS varchar(14)) AS [source],
    CAST(NULL AS varchar(15)) AS [purchaseOrderCode],
    CAST(NULL AS varchar(15)) AS [supplierCode],
    CAST(NULL AS smallint) AS [requestType],
    CAST(D.[operationId] AS bigint) AS [operationId],
    CAST(NULL AS int) AS [purchaseOrderId],
    CAST(NULL AS smallint) AS [rowNo],
    CAST(NULL AS date) AS [promisedDate],
    CAST(NULL AS smallint) AS [rowStatusCode],
    CAST(NULL AS smallint) AS [orderStatusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS numeric(38,6)) AS [stockAvailable],
    CAST(NULL AS numeric(38,6)) AS [engaged],
    CAST(NULL AS numeric(38,6)) AS [onOrder],
    CAST(NULL AS smallint) AS [dateConfirmed],
    CAST(D.[workplanId] AS int) AS [workplanId],
    CAST(D.[trackingId] AS int) AS [trackingId],
    CAST(D.[initialQty] AS numeric(18,6)) AS [initialQty],
    CAST(D.[residualQty] AS numeric(18,6)) AS [residualQty],
    CAST(D.[mandatory] AS bit) AS [mandatory],
    CAST(NULL AS numeric(18,6)) AS [orderedQty],
    CAST(NULL AS numeric(18,6)) AS [deliveredQty]
FROM RiserveNesting AS D

UNION ALL

SELECT
    CAST(N'ArticoliMateriali' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [componentNo],
    CAST(D.[partCode] AS varchar(20)) AS [partCode],
    CAST(D.[description] AS varchar(160)) AS [description],
    CAST(D.[uom] AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyRequired],
    CAST(NULL AS numeric(18,6)) AS [qtyCommitted],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(1)) AS [supplyKind],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS date) AS [availableDate],
    CAST(NULL AS varchar(14)) AS [source],
    CAST(NULL AS varchar(15)) AS [purchaseOrderCode],
    CAST(NULL AS varchar(15)) AS [supplierCode],
    CAST(NULL AS smallint) AS [requestType],
    CAST(NULL AS bigint) AS [operationId],
    CAST(NULL AS int) AS [purchaseOrderId],
    CAST(NULL AS smallint) AS [rowNo],
    CAST(NULL AS date) AS [promisedDate],
    CAST(NULL AS smallint) AS [rowStatusCode],
    CAST(NULL AS smallint) AS [orderStatusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS numeric(38,6)) AS [stockAvailable],
    CAST(NULL AS numeric(38,6)) AS [engaged],
    CAST(NULL AS numeric(38,6)) AS [onOrder],
    CAST(NULL AS smallint) AS [dateConfirmed],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [trackingId],
    CAST(NULL AS numeric(18,6)) AS [initialQty],
    CAST(NULL AS numeric(18,6)) AS [residualQty],
    CAST(NULL AS bit) AS [mandatory],
    CAST(NULL AS numeric(18,6)) AS [orderedQty],
    CAST(NULL AS numeric(18,6)) AS [deliveredQty]
FROM ArticoliMateriali AS D

UNION ALL

SELECT
    CAST(N'ResiduiOrdini' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [orderId],
    CAST(NULL AS int) AS [lotId],
    CAST(NULL AS int) AS [lineNumber],
    CAST(NULL AS int) AS [componentNo],
    CAST(D.[partCode] AS varchar(20)) AS [partCode],
    CAST(NULL AS varchar(160)) AS [description],
    CAST(D.[uom] AS varchar(10)) AS [uom],
    CAST(NULL AS numeric(18,6)) AS [qtyRequired],
    CAST(NULL AS numeric(18,6)) AS [qtyCommitted],
    CAST(NULL AS smallint) AS [phaseTypeId],
    CAST(NULL AS varchar(1)) AS [supplyKind],
    CAST(NULL AS varchar(15)) AS [materialCode],
    CAST(NULL AS numeric(18,6)) AS [qty],
    CAST(NULL AS date) AS [availableDate],
    CAST(NULL AS varchar(14)) AS [source],
    CAST(D.[purchaseOrderCode] AS varchar(15)) AS [purchaseOrderCode],
    CAST(NULL AS varchar(15)) AS [supplierCode],
    CAST(NULL AS smallint) AS [requestType],
    CAST(NULL AS bigint) AS [operationId],
    CAST(NULL AS int) AS [purchaseOrderId],
    CAST(D.[rowNo] AS smallint) AS [rowNo],
    CAST(NULL AS date) AS [promisedDate],
    CAST(NULL AS smallint) AS [rowStatusCode],
    CAST(NULL AS smallint) AS [orderStatusCode],
    CAST(NULL AS date) AS [issueDate],
    CAST(NULL AS varchar(20)) AS [sheetPartCode],
    CAST(NULL AS numeric(38,6)) AS [stockAvailable],
    CAST(NULL AS numeric(38,6)) AS [engaged],
    CAST(NULL AS numeric(38,6)) AS [onOrder],
    CAST(NULL AS smallint) AS [dateConfirmed],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [trackingId],
    CAST(NULL AS numeric(18,6)) AS [initialQty],
    CAST(NULL AS numeric(18,6)) AS [residualQty],
    CAST(NULL AS bit) AS [mandatory],
    CAST(D.[orderedQty] AS numeric(18,6)) AS [orderedQty],
    CAST(D.[deliveredQty] AS numeric(18,6)) AS [deliveredQty]
FROM ResiduiOrdini AS D;
