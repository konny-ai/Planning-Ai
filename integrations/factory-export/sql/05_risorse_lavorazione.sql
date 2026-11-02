/* =====================================================================
   05_risorse_lavorazione.sql - risorse candidate per lavorazione (L_LARE)
   e piano salvato (L_LAPN) come stato di partenza.
   Richiede #U_JOB (00_universo.sql).
   ===================================================================== */

-- RS1 operationResources: una riga per risorsa candidata della fase
--   RITPU: 0 ammessa, 1 forzata, 2 esclusa. RIUSE: Y usata nell'ultimo piano salvato.
--   RECOD valorizzato = fornitore (A_FOR.FOCOD) per le fasi esterne.
SELECT L.IDLARE AS assignmentId, J.IDLAV AS operationId,
       CASE WHEN L.MACOD IS NOT NULL THEN 'MACHINE'
            WHEN L.OPCOD IS NOT NULL THEN 'OPERATOR'
            WHEN L.RECOD IS NOT NULL THEN 'SUPPLIER' END AS resourceKind,
       COALESCE(L.MACOD, L.OPCOD, L.RECOD) AS resourceCode,
       CASE ISNULL(L.RITPU, 0) WHEN 1 THEN 'FORCED' WHEN 2 THEN 'EXCLUDED' ELSE 'ALLOWED' END AS assignmentRule,
       ISNULL(L.Preferential, 0) AS preferential,
       CASE WHEN L.RIUSE = 'Y' THEN 1 ELSE 0 END AS usedInSavedPlan,
       ISNULL(L.RIPOC, 100) AS usagePct,
       ISNULL(L.RIPAS, 10000) / 100.0 AS allocationPct,
       ISNULL(L.RPRIO, 0) AS priority, ISNULL(L.INEFF, 0) AS inefficiencyPct,
       ISNULL(L.TMBNS, 0) AS bonusTimeSec, ISNULL(L.OPATR, 0) AS setupCapable,
       NULLIF(L.IDNUM, 0) AS calendarId, L.TCCOD AS calendarModelId,
       L.IDBLK AS savedBlockId
FROM #U_JOB J
JOIN dbo.L_LARE L ON L.CONUM = J.CONUM AND L.LOCOD = J.LOCOD AND L.IDFAS = J.IDFAS;

-- RS2 savedPlanBlocks: intervalli del piano salvato per blocco (IDBLK)
SELECT J.IDLAV AS operationId, B.IDBLK AS blockId, B.IDINT AS intervalId, B.IDFRZ AS fractionId,
       DATEADD(second, DATEDIFF(second, CAST('00:00' AS time), ISNULL(B.BLHIP, '00:00')), CAST(B.BLDIP AS datetime2(0))) AS startAt,
       DATEADD(second, DATEDIFF(second, CAST('00:00' AS time), ISNULL(B.BLHFP, '00:00')), CAST(B.BLDFP AS datetime2(0))) AS endAt,
       B.RIPOC AS usagePct, NULLIF(B.IDPTC, 0) AS workplanId
FROM #U_JOB J
JOIN dbo.L_LAPN B ON B.CONUM = J.CONUM AND B.LOCOD = J.LOCOD AND B.IDFAS = J.IDFAS;

-- RS3 operationTools: attrezzature/stampi richiesti dalla lavorazione (L_ATLA)
--   TMCAR/TMSCA carico e scarico in secondi; IDBLK collega l'attrezzatura al blocco salvato.
SELECT J.IDLAV AS operationId, A.IDALA AS toolAssignmentId, A.ATCOD AS toolCode, A.ATTYP AS toolTypeId,
       A.TMCAR AS loadTimeSec, A.TMSCA AS unloadTimeSec, A.TMLAV AS workTimeSec,
       A.OPNUM AS operatorsRequired, A.OPPRC AS operatorUsagePct, A.PAQTA AS qty,
       A.MachineCode AS machineCode, A.ProgramNumber AS programNumber, A.IDBLK AS savedBlockId
FROM #U_JOB J
JOIN dbo.L_ATLA A ON A.CONUM = J.CONUM AND A.LOCOD = J.LOCOD AND A.IDFAS = J.IDFAS;
