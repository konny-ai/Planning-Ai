/* CLIENTE - lamiere (formati) dei nesting aperti, ripetizioni e tempo di taglio per ripetizione. */
SELECT E.IDNES AS workplanId, E.IDFOR AS sheetSlot, F.FOCOD AS sheetPartCode, F.FODSC AS sheetDescription, F.MTCOD AS material,
       F.PFDX1 AS widthMm, F.PFDY1 AS lengthMm, F.PFDZ1 AS thicknessMm, F.Weight AS sheetWeightKg, F.MGCOD AS warehouse, F.LCCOD AS location,
       F.NMNES AS sheetsRequired, F.NMIMP AS sheetsReserved, E.NMRIP AS repetitions, E.LTIME AS cutSecPerRepetition,
       E.LAREA AS cutAreaMm2, E.UsedPercentage AS usedPct, E.ScrapPercentage AS scrapPct
FROM dbo.L_NELM E JOIN dbo.A_NES N ON N.IDNES = E.IDNES
LEFT JOIN dbo.L_PCFO F ON F.IDPTC = E.IDNES AND F.IDFOR = E.IDFOR
WHERE ISNULL(N.STNES,0) < 5;
