/* =====================================================================
   06_vincoli_esterni.sql - legami espliciti, materiali, conto lavoro,
   piani di lavoro (nesting) e classi di attrezzaggio.
   Richiede #U_JOB e #U_COM (00_universo.sql).
   ===================================================================== */

-- RS1 explicitLinks (JobPrevious): LinkType 0/2 fine->inizio, 1 inizio->inizio
SELECT S.IDLAV AS successorOperationId, P.IDLAV AS predecessorOperationId,
       CASE JP.LinkType WHEN 1 THEN 'SS' ELSE 'FS' END AS linkType, JP.LinkType AS legacyLinkType
FROM dbo.JobPrevious JP
JOIN dbo.A_LAV S ON S.CONUM = JP.CONUM AND S.LOCOD = JP.LOCOD AND S.IDFAS = JP.IDFAS
LEFT JOIN dbo.A_LAV P ON P.CONUM = JP.Prev_CONUM AND P.LOCOD = JP.Prev_LOCOD AND P.IDFAS = JP.Prev_IDFAS
WHERE S.IDLAV IN (SELECT IDLAV FROM #U_JOB);

-- RS2 materialArrivals: date di arrivo di materiali e parti per commessa e lotto
--   (stessa UNION del legacy, FUN_0055E03C): richieste non ancora ordinate (A_RFO.IDORF NULL)
--   e righe di ordini fornitore non annullate/chiuse (RISTO <> 63, 65).
SELECT R.CONUM AS orderId,
       (SELECT COALESCE(T.LOCLC, T.LOCOD) FROM dbo.A_LOT T WHERE T.CONUM = R.CONUM AND T.LOCOD = R.LOCLP) AS lotId,
       R.PACOD AS partCode, R.PAQTA AS qty, R.PADAT AS availableDate,
       'REQUEST' AS source, NULL AS purchaseOrderCode, NULL AS supplierCode, R.RFTYP AS requestType
FROM dbo.A_RFO R
WHERE R.IDORF IS NULL AND R.CONUM IN (SELECT CONUM FROM #U_COM)
UNION ALL
SELECT R.CONUM,
       (SELECT COALESCE(T.LOCLC, T.LOCOD) FROM dbo.A_LOT T WHERE T.CONUM = R.CONUM AND T.LOCOD = R.LOCLP),
       O.PACOD, O.PAQTA, O.PADCR, 'PURCHASE_ORDER', A.ORCOD, F.FOCOD, R.RFTYP
FROM dbo.L_OFPA O
JOIN dbo.A_ORF A ON A.IDORF = O.IDORF
LEFT JOIN dbo.A_FOR F ON F.IDFOR = A.IDFOR
JOIN dbo.A_RFO R ON R.IDORF = O.IDORF AND R.NMRIG = O.NMRIG
WHERE R.CONUM IN (SELECT CONUM FROM #U_COM)
  AND O.RISTO NOT IN (63, 65);

-- RS3 subcontractOrders: ordini di conto lavoro collegati alle fasi esterne
--   La fase esterna è ancorata a MAX(PADCR) delle righe aperte (CheckJobDeliveryDate=1).
SELECT J.IDLAV AS operationId, A.IDORF AS purchaseOrderId, A.ORCOD AS purchaseOrderCode, O.NMRIG AS rowNo,
       F.FOCOD AS supplierCode, O.PADCR AS promisedDate, O.PAQTA AS qty, O.RISTO AS rowStatusCode,
       A.ORSTO AS orderStatusCode, A.ORDTO AS issueDate
FROM #U_JOB J
JOIN dbo.L_OFLA L ON L.CONUM = J.CONUM AND L.LOCOD = J.LOCOD AND L.IDFAS = J.IDFAS
JOIN dbo.L_OFPA O ON O.IDORF = L.IDORF AND O.NMRIG = L.NMRIG
JOIN dbo.A_ORF A ON A.IDORF = O.IDORF
LEFT JOIN dbo.A_FOR F ON F.IDFOR = A.IDFOR
WHERE O.RISTO NOT IN (63, 65);

-- RS4 lotComponents: componenti di distinta dei lotti (L_CMFE), per i vincoli di parti
SELECT C.CONUM AS orderId, C.LOCLP AS lotId, C.FINUM AS lineNumber, C.FENUM AS componentNo,
       C.PACOD AS partCode, C.PADSC AS description, C.PAUDM AS uom, C.LOQTP AS qtyRequired,
       C.PAQIM AS qtyCommitted, C.FATYP AS phaseTypeId, C.PAPRE AS supplyKind, C.MTCOD AS materialCode
FROM dbo.L_CMFE C
WHERE C.CONUM IN (SELECT CONUM FROM #U_COM);

-- RS5 workplans: piani di lavoro / programmi di nesting non chiusi (STNES < 5)
SELECT N.IDNES AS workplanId, N.NSCOD AS code, N.NSDSC AS description, N.TPNES AS kind, N.STNES AS statusCode,
       N.TFCOD AS phaseTypeId, N.MACOD AS machineCode, N.DTEXP AS plannedDate, N.OLCOD AS workOrderCode,
       N.ATCOD AS toolCode, N.ATTYP AS toolTypeId
FROM dbo.A_NES N
WHERE ISNULL(N.STNES, 0) < 5
  AND N.IDNES IN (SELECT L.IDPTC FROM dbo.A_LAV L JOIN #U_JOB J ON J.IDLAV = L.IDLAV WHERE ISNULL(L.IDPTC, 0) <> 0
                  UNION SELECT P.IDPTC FROM dbo.L_PCPA P JOIN #U_JOB J ON J.CONUM = P.CONUM AND J.LOCOD = P.LOCOD AND J.IDFAS = P.IDFAS);

-- RS6 workplanQuantities: quota di ogni lavorazione dentro ciascun piano (V_ODLA_PLANNING, RETYP 1)
SELECT J.IDLAV AS operationId, V.IDPTC AS workplanId, V.RETYP AS rowType, V.OLQTP AS qty,
       V.TMATT AS setupSec, V.TMLAV AS unitSec
FROM dbo.V_ODLA_PLANNING V
JOIN #U_JOB J ON J.CONUM = V.CONUM AND J.LOCOD = V.LOCOD AND J.IDFAS = V.IDFAS;

-- RS7 setupClasses (A_CAT) e loro parametri (L_CAGR)
SELECT IDCAT AS setupClassId, CATDE AS name, CATNT AS note, PERAC AS legacyPerac,
       CATGA AS groupCode, CAVAT AS value, TMSGL AS legacyTmsgl
FROM dbo.A_CAT;
SELECT IDCAT AS setupClassId, GRCOD AS measureId, GRVAL AS value, GRTOI AS toleranceLow, GRTOS AS toleranceHigh, UMCOD AS uom
FROM dbo.L_CAGR;
