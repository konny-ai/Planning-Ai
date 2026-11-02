/*
   dbo.X_CLIENTE_Planning_Fasi - contratto Factory -> Excel, versione 2.
   AREA: Tempi, quantita, precedenze di sequenza, otto valori macchina/operatore, setup e lavorazione.
   FOGLI ALIMENTATI: Fasi, Requisiti di stadio. Tutte le righe sono anche conservate in Provenienza record.

   COME LEGGERLA E MODIFICARLA
   - Una riga conserva UN tipo di record, indicato da TipoRecord.
   - I blocchi CTE iniziali contengono le SELECT di lavoro da modificare.
   - L UNION ALL finale allinea colonne e tipi: NULL significa non pertinente.
   - Non unire righe di grana diversa con JOIN, per non moltiplicare quantita o tempi.
   - Non rinominare TipoRecord o colonne senza aggiornare anche il contratto del connettore.
   - Esempio: SELECT * FROM dbo.X_CLIENTE_Planning_Fasi WHERE TipoRecord=N'Fasi'.

   PERIMETRO: X_CLIENTE_Planning_Perimetro conserva le fasi aperte delle commesse non chiuse.
   MANCANTI: NULL rimane mancante, zero rimane un valore; nessun arrivo o tempo viene inventato.
   DATE: valori locali Factory; il connettore assegna Europe/Rome, conserva l unita e applica l orizzonte.
   UNITA: tempi in secondi, percentuali 0..100, quantita nella unita esplicita dell articolo.
   La vista non scrive su Factory. Il pianificatore legge l Excel, mai direttamente questa vista.
*/
CREATE OR ALTER VIEW dbo.X_CLIENTE_Planning_Fasi
AS
WITH
/* Fasi: Una riga per fase IDLAV. Tempi in secondi; percentuali 0..100 dopo conversione dai centesimi Factory. Attrezzaggio e lavorazione conservano numeri e percentuali indipendenti. I consuntivi sono calcolati sulle sole fasi in stato 43; risorsa corrente non richiesta (CASE costante 0). */
Fasi AS (
SELECT L.IDLAV AS operationId, L.CONUM AS orderId, L.LOCOD AS lotId, L.IDFAS AS phaseId,
       L.FASEQ AS sequence, L.FACOD AS phaseCode, L.FADSC AS description,
       L.FATYP AS phaseTypeId, L.FAEXE AS executionCode,          
       L.FAPNF AS toPlan, L.LASTO AS statusCode, L.FAATT AS activationCode,  
       L.LAQTP AS qty,
       L.FAHAT * 3600 + L.FASAT AS setupSec,
       L.FAHLV * 3600.0 + L.FASLV AS unitSec,
       CASE WHEN L.LAQTP = 0 THEN 0 ELSE (L.FAHLV * 3600.0 + L.FASLV) * L.LAQTP + L.FAHAT * 3600 + L.FASAT END AS theoreticalTotalSec,
       L.LAHLP * 3600 + L.LASLP AS savedTotalSec,
       
       ISNULL(L.FANMA, 0) AS setupMachines,  ISNULL(L.FAPAM, 0) / 100.0 AS setupMachinePct,
       ISNULL(L.FANPA, 0) AS setupOperators, ISNULL(L.FAPAO, 0) / 100.0 AS setupOperatorPct,
       ISNULL(L.FANML, 0) AS runMachines,    ISNULL(L.FAPLM, 0) / 100.0 AS runMachinePct,
       ISNULL(L.FANPL, 0) AS runOperators,   ISNULL(L.FAPLO, 0) / 100.0 AS runOperatorPct,
       
       ISNULL(L.FAPSP, 0) / 100.0 AS overlapPct, ISNULL(L.FATMC, 0) AS queueTimeSec,
       
       ISNULL(L.LAPRI, 0) AS priority, ISNULL(L.FACPG, 0) AS priorityDurationDivisor,
       ISNULL(L.FATGR, 0) AS keepResourcesCode,
       
       L.IDCAT AS setupClassId, L.CATGA AS setupClassGroup, L.CAVAT AS setupClassValue,
       
       L.LADMH * 3600 + ISNULL(L.LADMS, 0) AS splitMinDurationSec, L.LAMXF AS splitMaxFractions,
       
       ISNULL(L.LASCH, 0) AS fixedStartFlag, L.LADTS AS fixedStartAt,
       
       L.OLCOD AS workOrderCode, S.OLTYP AS workOrderType, NULLIF(L.IDPTC, 0) AS workplanId,
       L.FAABV AS depositEnabled, L.LASTUPDATE AS sourceLastUpdate,
       
       L.LADIP AS plannedStartDate, L.LAHIP AS plannedStartTime, L.LADFP AS plannedEndDate, L.LAHFP AS plannedEndTime,
       L.FiniteCapacityStartTime AS finiteStart, L.FiniteCapacityEndTime AS finiteEnd,
       L.InfiniteCapacityStartTime AS infiniteStart, L.InfiniteCapacityEndTime AS infiniteEnd,
       
       (SELECT MIN(P.CMDTS) FROM dbo.L_CMPA P JOIN dbo.A_LOT LC ON P.CONUM = LC.CONUM AND P.LOCOD = LC.LOCLM
         WHERE LC.CONUM = T.CONUM AND (LC.LOCOD = T.LOCOD OR LC.LOCLC = T.LOCOD) AND P.LOCOD <> 0) AS legacyDueDate,
       
       CASE WHEN 1 = 1 AND L.LASTO = 43 THEN dbo.ComputeJobProducedQuantity(L.CONUM, L.LOCOD, L.IDFAS) END AS producedQty,
       CASE WHEN 1 = 1 AND L.LASTO = 43 THEN dbo.ComputeJobSetupTimeMac(L.CONUM, L.LOCOD, L.IDFAS) END AS actualSetupMachineSec,
       CASE WHEN 1 = 1 AND L.LASTO = 43 THEN dbo.ComputeJobSetupTimeOpr(L.CONUM, L.LOCOD, L.IDFAS) END AS actualSetupOperatorSec,
       CASE WHEN 1 = 1 AND L.LASTO = 43 THEN dbo.ComputeJobWorkTimeMac(L.CONUM, L.LOCOD, L.IDFAS) END AS actualWorkMachineSec,
       CASE WHEN 1 = 1 AND L.LASTO = 43 THEN dbo.ComputeJobWorkTimeOpr(L.CONUM, L.LOCOD, L.IDFAS) END AS actualWorkOperatorSec,
       
       CASE WHEN 0 = 1 THEN dbo.GetJobCurrentMachine(L.CONUM, L.LOCOD, L.IDFAS) END AS currentMachineCode,
       CASE WHEN 0 = 1 THEN dbo.GetJobCurrentOperator(L.CONUM, L.LOCOD, L.IDFAS) END AS currentOperatorCode
FROM dbo.X_CLIENTE_Planning_Perimetro U
JOIN dbo.A_LAV L ON L.IDLAV = U.IDLAV
JOIN dbo.A_LOT T ON T.CONUM = L.CONUM AND T.LOCOD = L.LOCOD
LEFT JOIN dbo.S_ODL S ON S.OLCOD = L.OLCOD
)

/* Allineamento del contratto; la logica di business e nei CTE sopra. */
SELECT
    CAST(N'Fasi' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[operationId] AS int) AS [operationId],
    CAST(D.[orderId] AS int) AS [orderId],
    CAST(D.[lotId] AS int) AS [lotId],
    CAST(D.[phaseId] AS int) AS [phaseId],
    CAST(D.[sequence] AS int) AS [sequence],
    CAST(D.[phaseCode] AS varchar(15)) AS [phaseCode],
    CAST(D.[description] AS varchar(40)) AS [description],
    CAST(D.[phaseTypeId] AS smallint) AS [phaseTypeId],
    CAST(D.[executionCode] AS varchar(1)) AS [executionCode],
    CAST(D.[toPlan] AS varchar(1)) AS [toPlan],
    CAST(D.[statusCode] AS smallint) AS [statusCode],
    CAST(D.[activationCode] AS varchar(1)) AS [activationCode],
    CAST(D.[qty] AS numeric(18,6)) AS [qty],
    CAST(D.[setupSec] AS int) AS [setupSec],
    CAST(D.[unitSec] AS numeric(19,6)) AS [unitSec],
    CAST(D.[theoreticalTotalSec] AS numeric(38,12)) AS [theoreticalTotalSec],
    CAST(D.[savedTotalSec] AS int) AS [savedTotalSec],
    CAST(D.[setupMachines] AS smallint) AS [setupMachines],
    CAST(D.[setupMachinePct] AS numeric(12,6)) AS [setupMachinePct],
    CAST(D.[setupOperators] AS smallint) AS [setupOperators],
    CAST(D.[setupOperatorPct] AS numeric(12,6)) AS [setupOperatorPct],
    CAST(D.[runMachines] AS smallint) AS [runMachines],
    CAST(D.[runMachinePct] AS numeric(12,6)) AS [runMachinePct],
    CAST(D.[runOperators] AS smallint) AS [runOperators],
    CAST(D.[runOperatorPct] AS numeric(12,6)) AS [runOperatorPct],
    CAST(D.[overlapPct] AS numeric(12,6)) AS [overlapPct],
    CAST(D.[queueTimeSec] AS numeric(18,6)) AS [queueTimeSec],
    CAST(D.[priority] AS smallint) AS [priority],
    CAST(D.[priorityDurationDivisor] AS numeric(18,6)) AS [priorityDurationDivisor],
    CAST(D.[keepResourcesCode] AS smallint) AS [keepResourcesCode],
    CAST(D.[setupClassId] AS int) AS [setupClassId],
    CAST(D.[setupClassGroup] AS smallint) AS [setupClassGroup],
    CAST(D.[setupClassValue] AS numeric(18,6)) AS [setupClassValue],
    CAST(D.[splitMinDurationSec] AS int) AS [splitMinDurationSec],
    CAST(D.[splitMaxFractions] AS smallint) AS [splitMaxFractions],
    CAST(D.[fixedStartFlag] AS int) AS [fixedStartFlag],
    CAST(D.[fixedStartAt] AS datetime2(6)) AS [fixedStartAt],
    CAST(D.[workOrderCode] AS varchar(10)) AS [workOrderCode],
    CAST(D.[workOrderType] AS varchar(1)) AS [workOrderType],
    CAST(D.[workplanId] AS int) AS [workplanId],
    CAST(D.[depositEnabled] AS varchar(1)) AS [depositEnabled],
    CAST(D.[sourceLastUpdate] AS datetime) AS [sourceLastUpdate],
    CAST(D.[plannedStartDate] AS date) AS [plannedStartDate],
    CAST(D.[plannedStartTime] AS time(6)) AS [plannedStartTime],
    CAST(D.[plannedEndDate] AS date) AS [plannedEndDate],
    CAST(D.[plannedEndTime] AS time(6)) AS [plannedEndTime],
    CAST(D.[finiteStart] AS datetime2(6)) AS [finiteStart],
    CAST(D.[finiteEnd] AS datetime2(6)) AS [finiteEnd],
    CAST(D.[infiniteStart] AS datetime2(6)) AS [infiniteStart],
    CAST(D.[infiniteEnd] AS datetime2(6)) AS [infiniteEnd],
    CAST(D.[legacyDueDate] AS date) AS [legacyDueDate],
    CAST(D.[producedQty] AS float) AS [producedQty],
    CAST(D.[actualSetupMachineSec] AS float) AS [actualSetupMachineSec],
    CAST(D.[actualSetupOperatorSec] AS float) AS [actualSetupOperatorSec],
    CAST(D.[actualWorkMachineSec] AS float) AS [actualWorkMachineSec],
    CAST(D.[actualWorkOperatorSec] AS float) AS [actualWorkOperatorSec],
    CAST(D.[currentMachineCode] AS varchar(20)) AS [currentMachineCode],
    CAST(D.[currentOperatorCode] AS varchar(20)) AS [currentOperatorCode]
FROM Fasi AS D;
