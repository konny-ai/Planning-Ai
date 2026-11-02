/* CLIENTE - disponibilita' dei formati dei nesting aperti. A_RFO e' vuota in CLIENTE e il linked server AS400 non e'
   presente nel database: arrivi da L_OFPA (righe ordine fornitore aperte), giacenze da L_MLPA/L_PAQT. */
WITH S AS (SELECT DISTINCT F.FOCOD FROM dbo.L_PCFO F JOIN dbo.A_NES N ON N.IDNES = F.IDPTC WHERE ISNULL(N.STNES,0) < 5)
SELECT S.FOCOD AS sheetPartCode,
       (SELECT ISNULL(SUM(M.QTLOC),0) FROM dbo.L_MLPA M WHERE M.PACOD = S.FOCOD AND M.LCABI = 'Y') AS stockAvailable,
       (SELECT ISNULL(SUM(Q.PAIMP),0) FROM dbo.L_PAQT Q WHERE Q.PACOD = S.FOCOD) AS engaged,
       (SELECT ISNULL(SUM(Q.PAORD),0) FROM dbo.L_PAQT Q WHERE Q.PACOD = S.FOCOD) AS onOrder
FROM S;
SELECT O.PACOD AS partCode, A.ORCOD AS purchaseOrderCode, F.FOCOD AS supplierCode, O.NMRIG AS rowNo, O.PAQTA AS qty,
       O.PADCR AS promisedDate, O.DeliveryDateConfirmed AS dateConfirmed, O.RISTO AS rowStatusCode
FROM dbo.L_OFPA O JOIN dbo.A_ORF A ON A.IDORF = O.IDORF LEFT JOIN dbo.A_FOR F ON F.IDFOR = A.IDFOR
WHERE O.RISTO NOT IN (63, 65);
SELECT W.WorkplanID AS workplanId, W.WorkplanJobID AS operationId, W.StoreLocationPartTrackingID AS trackingId,
       W.InitialQuantity AS initialQty, W.ResidualQuantity AS residualQty, W.Mandatory AS mandatory
FROM dbo.WorkplanJobsSheets W JOIN dbo.A_NES N ON N.IDNES = W.WorkplanID WHERE ISNULL(N.STNES,0) < 5;
