/* CLIENTE - membri dei nesting aperti: fase (A_LAV) e pezzi nestati per commessa/lotto. */
SELECT D.IDNES AS workplanId, D.IDLAV AS operationId, D.CONUM AS orderId, D.LOCOD AS lotId, D.IDFAS AS phaseId,
       D.OLQTP AS memberQty, D.OLCOD AS workOrderCode
FROM dbo.L_ODLA D JOIN dbo.A_NES N ON N.IDNES = D.IDNES WHERE ISNULL(N.STNES,0) < 5;
SELECT P.IDPTC AS workplanId, P.CONUM AS orderId, P.LOCOD AS lotId, P.IDFAS AS phaseId, P.PACOD AS partCode,
       SUM(P.PANES) AS nestedQty, SUM(P.PANUM) AS requiredQty, MAX(P.WeightNested) AS weightNested
FROM dbo.L_PCPA P JOIN dbo.A_NES N ON N.IDNES = P.IDPTC WHERE ISNULL(N.STNES,0) < 5
GROUP BY P.IDPTC, P.CONUM, P.LOCOD, P.IDFAS, P.PACOD;
