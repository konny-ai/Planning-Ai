/* =====================================================================
   04_lavorazioni.sql - lavorazioni (fasi) da pianificare con tempi,
   fabbisogni di risorsa, vincoli e avanzamento.
   Richiede #U_JOB (00_universo.sql).

   Parametri:
     @CalcolaConsuntivi  bit  1 = quantità prodotta e tempi consuntivati
                               (funzioni dbo.ComputeJob*, solo fasi aperte LASTO=43)
     @UsaRisorsaCorrente bit  1 = macchina/operatore della sessione aperta
                               (dbo.GetJobCurrentMachine/Operator)

   Unità: secondi; percentuali già divise per 100 (Factory 10000 = 100%).
   ===================================================================== */

-- RS1 operations
SELECT L.IDLAV AS operationId, L.CONUM AS orderId, L.LOCOD AS lotId, L.IDFAS AS phaseId,
       L.FASEQ AS sequence, L.FACOD AS phaseCode, L.FADSC AS description,
       L.FATYP AS phaseTypeId, L.FAEXE AS executionCode,          -- I interna, E esterna
       L.FAPNF AS toPlan, L.LASTO AS statusCode, L.FAATT AS activationCode,  -- S sequenziale, P parallela
       L.LAQTP AS qty,
       L.FAHAT * 3600 + L.FASAT AS setupSec,
       L.FAHLV * 3600.0 + L.FASLV AS unitSec,
       CASE WHEN L.LAQTP = 0 THEN 0 ELSE (L.FAHLV * 3600.0 + L.FASLV) * L.LAQTP + L.FAHAT * 3600 + L.FASAT END AS theoreticalTotalSec,
       L.LAHLP * 3600 + L.LASLP AS savedTotalSec,
       -- fabbisogno di risorse: attrezzaggio (A) e lavorazione (L)
       ISNULL(L.FANMA, 0) AS setupMachines,  ISNULL(L.FAPAM, 0) / 100.0 AS setupMachinePct,
       ISNULL(L.FANPA, 0) AS setupOperators, ISNULL(L.FAPAO, 0) / 100.0 AS setupOperatorPct,
       ISNULL(L.FANML, 0) AS runMachines,    ISNULL(L.FAPLM, 0) / 100.0 AS runMachinePct,
       ISNULL(L.FANPL, 0) AS runOperators,   ISNULL(L.FAPLO, 0) / 100.0 AS runOperatorPct,
       -- sovrapposizione e code
       ISNULL(L.FAPSP, 0) / 100.0 AS overlapPct, ISNULL(L.FATMC, 0) AS queueTimeSec,
       -- priorità e ordinamento
       ISNULL(L.LAPRI, 0) AS priority, ISNULL(L.FACPG, 0) AS priorityDurationDivisor,
       ISNULL(L.FATGR, 0) AS keepResourcesCode,
       -- attrezzaggio
       L.IDCAT AS setupClassId, L.CATGA AS setupClassGroup, L.CAVAT AS setupClassValue,
       -- frazionamento
       L.LADMH * 3600 + ISNULL(L.LADMS, 0) AS splitMinDurationSec, L.LAMXF AS splitMaxFractions,
       -- vincolo di inizio fisso della fase
       ISNULL(L.LASCH, 0) AS fixedStartFlag, L.LADTS AS fixedStartAt,
       -- bolla e piano di lavoro (nesting)
       L.OLCOD AS workOrderCode, S.OLTYP AS workOrderType, NULLIF(L.IDPTC, 0) AS workplanId,
       L.FAABV AS depositEnabled, L.LASTUPDATE AS sourceLastUpdate,
       -- piano corrente salvato
       L.LADIP AS plannedStartDate, L.LAHIP AS plannedStartTime, L.LADFP AS plannedEndDate, L.LAHFP AS plannedEndTime,
       L.FiniteCapacityStartTime AS finiteStart, L.FiniteCapacityEndTime AS finiteEnd,
       L.InfiniteCapacityStartTime AS infiniteStart, L.InfiniteCapacityEndTime AS infiniteEnd,
       -- consegna richiesta come la calcola il legacy: MIN(CMDTS) delle righe del lotto finale
       (SELECT MIN(P.CMDTS) FROM dbo.L_CMPA P JOIN dbo.A_LOT LC ON P.CONUM = LC.CONUM AND P.LOCOD = LC.LOCLM
         WHERE LC.CONUM = T.CONUM AND (LC.LOCOD = T.LOCOD OR LC.LOCLC = T.LOCOD) AND P.LOCOD <> 0) AS legacyDueDate,
       -- avanzamento
       CASE WHEN @CalcolaConsuntivi = 1 AND L.LASTO = 43 THEN dbo.ComputeJobProducedQuantity(L.CONUM, L.LOCOD, L.IDFAS) END AS producedQty,
       CASE WHEN @CalcolaConsuntivi = 1 AND L.LASTO = 43 THEN dbo.ComputeJobSetupTimeMac(L.CONUM, L.LOCOD, L.IDFAS) END AS actualSetupMachineSec,
       CASE WHEN @CalcolaConsuntivi = 1 AND L.LASTO = 43 THEN dbo.ComputeJobSetupTimeOpr(L.CONUM, L.LOCOD, L.IDFAS) END AS actualSetupOperatorSec,
       CASE WHEN @CalcolaConsuntivi = 1 AND L.LASTO = 43 THEN dbo.ComputeJobWorkTimeMac(L.CONUM, L.LOCOD, L.IDFAS) END AS actualWorkMachineSec,
       CASE WHEN @CalcolaConsuntivi = 1 AND L.LASTO = 43 THEN dbo.ComputeJobWorkTimeOpr(L.CONUM, L.LOCOD, L.IDFAS) END AS actualWorkOperatorSec,
       -- risorsa in uso dalla sessione aperta
       CASE WHEN @UsaRisorsaCorrente = 1 THEN dbo.GetJobCurrentMachine(L.CONUM, L.LOCOD, L.IDFAS) END AS currentMachineCode,
       CASE WHEN @UsaRisorsaCorrente = 1 THEN dbo.GetJobCurrentOperator(L.CONUM, L.LOCOD, L.IDFAS) END AS currentOperatorCode
FROM #U_JOB U
JOIN dbo.A_LAV L ON L.IDLAV = U.IDLAV
JOIN dbo.A_LOT T ON T.CONUM = L.CONUM AND T.LOCOD = L.LOCOD
LEFT JOIN dbo.S_ODL S ON S.OLCOD = L.OLCOD
ORDER BY L.CONUM, T.LOCLM DESC, L.LOCOD, L.FASEQ;   -- ordine di caricamento del legacy
