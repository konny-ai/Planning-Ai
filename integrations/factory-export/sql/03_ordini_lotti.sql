/* =====================================================================
   03_ordini_lotti.sql - clienti, commesse, righe d'ordine e lotti.
   Richiede #U_COM e #U_LOT (00_universo.sql).
   ===================================================================== */

-- RS1 customers (solo quelli delle commesse in piano) con priorità e classe
SELECT CL.IDCLI AS customerId, CL.CTCOD AS code, CL.CTRAG AS name,
       ISNULL(CL.Priority, 0) AS priority, CL.IDCLS AS classId, CF.CLDSC AS className,
       ISNULL(CF.CLPRI, 0) AS classPriority
FROM dbo.A_CLI CL
LEFT JOIN dbo.A_CLCF CF ON CF.IDCLS = CL.IDCLS
WHERE CL.CTCOD IN (SELECT C.CTCOD FROM dbo.A_COM C JOIN #U_COM U ON U.CONUM = C.CONUM);

-- RS2 orders (commesse A_COM)
SELECT C.CONUM AS orderId, C.COCOD AS code, C.CODSC AS description, C.CTCOD AS customerCode,
       C.COSTO AS statusCode, C.CODTE AS issueDate, C.CODCC AS dueDate, C.COHCC AS dueTime,
       C.CORIF AS reference, C.COPNF AS planningModeSaved,
       C.COSCH AS fixedStartFlag, C.CODTS AS fixedStartAt,
       C.CODIP AS plannedStartDate, C.COHIP AS plannedStartTime,
       C.CODFP AS plannedEndDate, C.COHFP AS plannedEndTime,
       C.IDTPC AS orderTypeId, C.COTYP AS orderKind
FROM dbo.A_COM C JOIN #U_COM U ON U.CONUM = C.CONUM;

-- RS3 orderLines (righe d'ordine L_CMPA, collegate al lotto finale LOCOD)
SELECT P.CONUM AS orderId, P.FINUM AS lineNumber, P.LOCOD AS finalLotId, P.PACOD AS partCode,
       P.PADSC AS partDescription, P.PAUDM AS uom, P.LQNTA AS qtyOrdered, P.LQCON AS qtyDelivered,
       P.CMDTS AS dueDate, P.PADCR AS requestedDate, P.STCON AS deliveryStatusCode,
       P.PASCH AS fixedStartFlag, P.PADTS AS fixedStartAt, P.Priority AS priority
FROM dbo.L_CMPA P JOIN #U_COM U ON U.CONUM = P.CONUM
WHERE P.LOCOD <> 0;

-- RS4 lots (A_LOT): albero LOCLP (padre), LOCLM (lotto finale), LOCLC (collegamento)
SELECT T.CONUM AS orderId, T.LOCOD AS lotId, NULLIF(NULLIF(T.LOCLP, 0), T.LOCOD) AS parentLotId,   -- 0 o se stesso = radice
       T.LOCLM AS finalLotId, T.LOCLC AS linkedLotId,
       T.PACOD AS partCode, T.PADSC AS partDescription, T.PAUDM AS uom, T.LOQTP AS qty,
       T.LOSTO AS statusCode, ISNULL(T.LOPRI, 0) AS priority, T.LODSC AS dueDate, T.LOHSC AS dueTime,
       T.LOSCH AS fixedStartFlag, T.LODTS AS fixedStartAt,
       T.FMCOD AS familyCode, T.MTCOD AS materialCode,
       T.PADX1 AS dimX, T.PADY1 AS dimY, T.PADZ1 AS dimZ, T.PATPA AS partTypeId,
       T.LODIP AS plannedStartDate, T.LOHIP AS plannedStartTime, T.LODFP AS plannedEndDate, T.LOHFP AS plannedEndTime,
       T.MaterialMissing AS materialMissing, T.ProductionCritical AS productionCritical
FROM dbo.A_LOT T JOIN #U_LOT U ON U.CONUM = T.CONUM AND U.LOCOD = T.LOCOD;
