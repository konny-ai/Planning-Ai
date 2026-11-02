/*
   dbo.X_CLIENTE_Planning_Cicli - contratto Factory -> Excel, versione 2.
   AREA: Candidati macchina/operatore, vincoli di risorsa, blocchi salvati, attrezzature, legami e classi setup.
   FOGLI ALIMENTATI: Risorse per fase, Precedenze, Setup delle fasi. Tutte le righe sono anche conservate in Provenienza record.

   COME LEGGERLA E MODIFICARLA
   - Una riga conserva UN tipo di record, indicato da TipoRecord.
   - I blocchi CTE iniziali contengono le SELECT di lavoro da modificare.
   - L UNION ALL finale allinea colonne e tipi: NULL significa non pertinente.
   - Non unire righe di grana diversa con JOIN, per non moltiplicare quantita o tempi.
   - Non rinominare TipoRecord o colonne senza aggiornare anche il contratto del connettore.
   - Esempio: SELECT * FROM dbo.X_CLIENTE_Planning_Cicli WHERE TipoRecord=N'RisorseCandidate'.

   PERIMETRO: X_CLIENTE_Planning_Perimetro conserva le fasi aperte delle commesse non chiuse.
   MANCANTI: NULL rimane mancante, zero rimane un valore; nessun arrivo o tempo viene inventato.
   DATE: valori locali Factory; il connettore assegna Europe/Rome, conserva l unita e applica l orizzonte.
   UNITA: tempi in secondi, percentuali 0..100, quantita nella unita esplicita dell articolo.
   La vista non scrive su Factory. Il pianificatore legge l Excel, mai direttamente questa vista.
*/
CREATE OR ALTER VIEW dbo.X_CLIENTE_Planning_Cicli
AS
WITH
/* RisorseCandidate: Una riga del tipo RisorseCandidate. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
RisorseCandidate AS (
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
FROM dbo.X_CLIENTE_Planning_Perimetro J
JOIN dbo.L_LARE L ON L.CONUM = J.CONUM AND L.LOCOD = J.LOCOD AND L.IDFAS = J.IDFAS
),

/* BlocchiSalvati: Una riga del tipo BlocchiSalvati. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
BlocchiSalvati AS (
SELECT J.IDLAV AS operationId, B.IDBLK AS blockId, B.IDINT AS intervalId, B.IDFRZ AS fractionId,
       DATEADD(second, DATEDIFF(second, CAST('00:00' AS time), ISNULL(B.BLHIP, '00:00')), CAST(B.BLDIP AS datetime2(0))) AS startAt,
       DATEADD(second, DATEDIFF(second, CAST('00:00' AS time), ISNULL(B.BLHFP, '00:00')), CAST(B.BLDFP AS datetime2(0))) AS endAt,
       B.RIPOC AS usagePct, NULLIF(B.IDPTC, 0) AS workplanId
FROM dbo.X_CLIENTE_Planning_Perimetro J
JOIN dbo.L_LAPN B ON B.CONUM = J.CONUM AND B.LOCOD = J.LOCOD AND B.IDFAS = J.IDFAS
),

/* AttrezzatureFase: Una riga del tipo AttrezzatureFase. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
AttrezzatureFase AS (
SELECT J.IDLAV AS operationId, A.IDALA AS toolAssignmentId, A.ATCOD AS toolCode, A.ATTYP AS toolTypeId,
       A.TMCAR AS loadTimeSec, A.TMSCA AS unloadTimeSec, A.TMLAV AS workTimeSec,
       A.OPNUM AS operatorsRequired, A.OPPRC AS operatorUsagePct, A.PAQTA AS qty,
       A.MachineCode AS machineCode, A.ProgramNumber AS programNumber, A.IDBLK AS savedBlockId
FROM dbo.X_CLIENTE_Planning_Perimetro J
JOIN dbo.L_ATLA A ON A.CONUM = J.CONUM AND A.LOCOD = J.LOCOD AND A.IDFAS = J.IDFAS
),

/* LegamiEspliciti: Una riga del tipo LegamiEspliciti. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
LegamiEspliciti AS (
SELECT S.IDLAV AS successorOperationId, P.IDLAV AS predecessorOperationId,
       CASE JP.LinkType WHEN 1 THEN 'SS' ELSE 'FS' END AS linkType, JP.LinkType AS legacyLinkType
FROM dbo.JobPrevious JP
JOIN dbo.A_LAV S ON S.CONUM = JP.CONUM AND S.LOCOD = JP.LOCOD AND S.IDFAS = JP.IDFAS
LEFT JOIN dbo.A_LAV P ON P.CONUM = JP.Prev_CONUM AND P.LOCOD = JP.Prev_LOCOD AND P.IDFAS = JP.Prev_IDFAS
WHERE S.IDLAV IN (SELECT IDLAV FROM dbo.X_CLIENTE_Planning_Perimetro)
),

/* ClassiSetup: Una riga del tipo ClassiSetup. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
ClassiSetup AS (
SELECT IDCAT AS setupClassId, CATDE AS name, CATNT AS note, PERAC AS legacyPerac,
       CATGA AS groupCode, CAVAT AS value, TMSGL AS legacyTmsgl
FROM dbo.A_CAT
),

/* ParametriSetup: Una riga del tipo ParametriSetup. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
ParametriSetup AS (
SELECT IDCAT AS setupClassId, GRCOD AS measureId, GRVAL AS value, GRTOI AS toleranceLow, GRTOS AS toleranceHigh, UMCOD AS uom
FROM dbo.L_CAGR
)

/* Allineamento del contratto; la logica di business e nei CTE sopra. */
SELECT
    CAST(N'RisorseCandidate' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[assignmentId] AS int) AS [assignmentId],
    CAST(D.[operationId] AS int) AS [operationId],
    CAST(D.[resourceKind] AS varchar(8)) AS [resourceKind],
    CAST(D.[resourceCode] AS varchar(15)) AS [resourceCode],
    CAST(D.[assignmentRule] AS varchar(8)) AS [assignmentRule],
    CAST(D.[preferential] AS bit) AS [preferential],
    CAST(D.[usedInSavedPlan] AS int) AS [usedInSavedPlan],
    CAST(D.[usagePct] AS numeric(6,2)) AS [usagePct],
    CAST(D.[allocationPct] AS numeric(12,6)) AS [allocationPct],
    CAST(D.[priority] AS int) AS [priority],
    CAST(D.[inefficiencyPct] AS int) AS [inefficiencyPct],
    CAST(D.[bonusTimeSec] AS int) AS [bonusTimeSec],
    CAST(D.[setupCapable] AS smallint) AS [setupCapable],
    CAST(D.[calendarId] AS int) AS [calendarId],
    CAST(D.[calendarModelId] AS int) AS [calendarModelId],
    CAST(D.[savedBlockId] AS int) AS [savedBlockId],
    CAST(NULL AS int) AS [blockId],
    CAST(NULL AS int) AS [intervalId],
    CAST(NULL AS int) AS [fractionId],
    CAST(NULL AS datetime2(0)) AS [startAt],
    CAST(NULL AS datetime2(0)) AS [endAt],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [toolAssignmentId],
    CAST(NULL AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(NULL AS int) AS [loadTimeSec],
    CAST(NULL AS int) AS [unloadTimeSec],
    CAST(NULL AS int) AS [workTimeSec],
    CAST(NULL AS int) AS [operatorsRequired],
    CAST(NULL AS decimal(6,3)) AS [operatorUsagePct],
    CAST(NULL AS int) AS [qty],
    CAST(NULL AS varchar(15)) AS [machineCode],
    CAST(NULL AS varchar(128)) AS [programNumber],
    CAST(NULL AS int) AS [successorOperationId],
    CAST(NULL AS int) AS [predecessorOperationId],
    CAST(NULL AS varchar(2)) AS [linkType],
    CAST(NULL AS int) AS [legacyLinkType],
    CAST(NULL AS int) AS [setupClassId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS varchar(255)) AS [note],
    CAST(NULL AS numeric(8,3)) AS [legacyPerac],
    CAST(NULL AS smallint) AS [groupCode],
    CAST(NULL AS numeric(18,6)) AS [value],
    CAST(NULL AS int) AS [legacyTmsgl],
    CAST(NULL AS int) AS [measureId],
    CAST(NULL AS numeric(18,6)) AS [toleranceLow],
    CAST(NULL AS numeric(18,6)) AS [toleranceHigh],
    CAST(NULL AS varchar(10)) AS [uom]
FROM RisorseCandidate AS D

UNION ALL

SELECT
    CAST(N'BlocchiSalvati' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [assignmentId],
    CAST(D.[operationId] AS int) AS [operationId],
    CAST(NULL AS varchar(8)) AS [resourceKind],
    CAST(NULL AS varchar(15)) AS [resourceCode],
    CAST(NULL AS varchar(8)) AS [assignmentRule],
    CAST(NULL AS bit) AS [preferential],
    CAST(NULL AS int) AS [usedInSavedPlan],
    CAST(D.[usagePct] AS numeric(6,2)) AS [usagePct],
    CAST(NULL AS numeric(12,6)) AS [allocationPct],
    CAST(NULL AS int) AS [priority],
    CAST(NULL AS int) AS [inefficiencyPct],
    CAST(NULL AS int) AS [bonusTimeSec],
    CAST(NULL AS smallint) AS [setupCapable],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS int) AS [calendarModelId],
    CAST(NULL AS int) AS [savedBlockId],
    CAST(D.[blockId] AS int) AS [blockId],
    CAST(D.[intervalId] AS int) AS [intervalId],
    CAST(D.[fractionId] AS int) AS [fractionId],
    CAST(D.[startAt] AS datetime2(0)) AS [startAt],
    CAST(D.[endAt] AS datetime2(0)) AS [endAt],
    CAST(D.[workplanId] AS int) AS [workplanId],
    CAST(NULL AS int) AS [toolAssignmentId],
    CAST(NULL AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(NULL AS int) AS [loadTimeSec],
    CAST(NULL AS int) AS [unloadTimeSec],
    CAST(NULL AS int) AS [workTimeSec],
    CAST(NULL AS int) AS [operatorsRequired],
    CAST(NULL AS decimal(6,3)) AS [operatorUsagePct],
    CAST(NULL AS int) AS [qty],
    CAST(NULL AS varchar(15)) AS [machineCode],
    CAST(NULL AS varchar(128)) AS [programNumber],
    CAST(NULL AS int) AS [successorOperationId],
    CAST(NULL AS int) AS [predecessorOperationId],
    CAST(NULL AS varchar(2)) AS [linkType],
    CAST(NULL AS int) AS [legacyLinkType],
    CAST(NULL AS int) AS [setupClassId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS varchar(255)) AS [note],
    CAST(NULL AS numeric(8,3)) AS [legacyPerac],
    CAST(NULL AS smallint) AS [groupCode],
    CAST(NULL AS numeric(18,6)) AS [value],
    CAST(NULL AS int) AS [legacyTmsgl],
    CAST(NULL AS int) AS [measureId],
    CAST(NULL AS numeric(18,6)) AS [toleranceLow],
    CAST(NULL AS numeric(18,6)) AS [toleranceHigh],
    CAST(NULL AS varchar(10)) AS [uom]
FROM BlocchiSalvati AS D

UNION ALL

SELECT
    CAST(N'AttrezzatureFase' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [assignmentId],
    CAST(D.[operationId] AS int) AS [operationId],
    CAST(NULL AS varchar(8)) AS [resourceKind],
    CAST(NULL AS varchar(15)) AS [resourceCode],
    CAST(NULL AS varchar(8)) AS [assignmentRule],
    CAST(NULL AS bit) AS [preferential],
    CAST(NULL AS int) AS [usedInSavedPlan],
    CAST(NULL AS numeric(6,2)) AS [usagePct],
    CAST(NULL AS numeric(12,6)) AS [allocationPct],
    CAST(NULL AS int) AS [priority],
    CAST(NULL AS int) AS [inefficiencyPct],
    CAST(NULL AS int) AS [bonusTimeSec],
    CAST(NULL AS smallint) AS [setupCapable],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS int) AS [calendarModelId],
    CAST(D.[savedBlockId] AS int) AS [savedBlockId],
    CAST(NULL AS int) AS [blockId],
    CAST(NULL AS int) AS [intervalId],
    CAST(NULL AS int) AS [fractionId],
    CAST(NULL AS datetime2(0)) AS [startAt],
    CAST(NULL AS datetime2(0)) AS [endAt],
    CAST(NULL AS int) AS [workplanId],
    CAST(D.[toolAssignmentId] AS int) AS [toolAssignmentId],
    CAST(D.[toolCode] AS smallint) AS [toolCode],
    CAST(D.[toolTypeId] AS smallint) AS [toolTypeId],
    CAST(D.[loadTimeSec] AS int) AS [loadTimeSec],
    CAST(D.[unloadTimeSec] AS int) AS [unloadTimeSec],
    CAST(D.[workTimeSec] AS int) AS [workTimeSec],
    CAST(D.[operatorsRequired] AS int) AS [operatorsRequired],
    CAST(D.[operatorUsagePct] AS decimal(6,3)) AS [operatorUsagePct],
    CAST(D.[qty] AS int) AS [qty],
    CAST(D.[machineCode] AS varchar(15)) AS [machineCode],
    CAST(D.[programNumber] AS varchar(128)) AS [programNumber],
    CAST(NULL AS int) AS [successorOperationId],
    CAST(NULL AS int) AS [predecessorOperationId],
    CAST(NULL AS varchar(2)) AS [linkType],
    CAST(NULL AS int) AS [legacyLinkType],
    CAST(NULL AS int) AS [setupClassId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS varchar(255)) AS [note],
    CAST(NULL AS numeric(8,3)) AS [legacyPerac],
    CAST(NULL AS smallint) AS [groupCode],
    CAST(NULL AS numeric(18,6)) AS [value],
    CAST(NULL AS int) AS [legacyTmsgl],
    CAST(NULL AS int) AS [measureId],
    CAST(NULL AS numeric(18,6)) AS [toleranceLow],
    CAST(NULL AS numeric(18,6)) AS [toleranceHigh],
    CAST(NULL AS varchar(10)) AS [uom]
FROM AttrezzatureFase AS D

UNION ALL

SELECT
    CAST(N'LegamiEspliciti' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [assignmentId],
    CAST(NULL AS int) AS [operationId],
    CAST(NULL AS varchar(8)) AS [resourceKind],
    CAST(NULL AS varchar(15)) AS [resourceCode],
    CAST(NULL AS varchar(8)) AS [assignmentRule],
    CAST(NULL AS bit) AS [preferential],
    CAST(NULL AS int) AS [usedInSavedPlan],
    CAST(NULL AS numeric(6,2)) AS [usagePct],
    CAST(NULL AS numeric(12,6)) AS [allocationPct],
    CAST(NULL AS int) AS [priority],
    CAST(NULL AS int) AS [inefficiencyPct],
    CAST(NULL AS int) AS [bonusTimeSec],
    CAST(NULL AS smallint) AS [setupCapable],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS int) AS [calendarModelId],
    CAST(NULL AS int) AS [savedBlockId],
    CAST(NULL AS int) AS [blockId],
    CAST(NULL AS int) AS [intervalId],
    CAST(NULL AS int) AS [fractionId],
    CAST(NULL AS datetime2(0)) AS [startAt],
    CAST(NULL AS datetime2(0)) AS [endAt],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [toolAssignmentId],
    CAST(NULL AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(NULL AS int) AS [loadTimeSec],
    CAST(NULL AS int) AS [unloadTimeSec],
    CAST(NULL AS int) AS [workTimeSec],
    CAST(NULL AS int) AS [operatorsRequired],
    CAST(NULL AS decimal(6,3)) AS [operatorUsagePct],
    CAST(NULL AS int) AS [qty],
    CAST(NULL AS varchar(15)) AS [machineCode],
    CAST(NULL AS varchar(128)) AS [programNumber],
    CAST(D.[successorOperationId] AS int) AS [successorOperationId],
    CAST(D.[predecessorOperationId] AS int) AS [predecessorOperationId],
    CAST(D.[linkType] AS varchar(2)) AS [linkType],
    CAST(D.[legacyLinkType] AS int) AS [legacyLinkType],
    CAST(NULL AS int) AS [setupClassId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS varchar(255)) AS [note],
    CAST(NULL AS numeric(8,3)) AS [legacyPerac],
    CAST(NULL AS smallint) AS [groupCode],
    CAST(NULL AS numeric(18,6)) AS [value],
    CAST(NULL AS int) AS [legacyTmsgl],
    CAST(NULL AS int) AS [measureId],
    CAST(NULL AS numeric(18,6)) AS [toleranceLow],
    CAST(NULL AS numeric(18,6)) AS [toleranceHigh],
    CAST(NULL AS varchar(10)) AS [uom]
FROM LegamiEspliciti AS D

UNION ALL

SELECT
    CAST(N'ClassiSetup' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [assignmentId],
    CAST(NULL AS int) AS [operationId],
    CAST(NULL AS varchar(8)) AS [resourceKind],
    CAST(NULL AS varchar(15)) AS [resourceCode],
    CAST(NULL AS varchar(8)) AS [assignmentRule],
    CAST(NULL AS bit) AS [preferential],
    CAST(NULL AS int) AS [usedInSavedPlan],
    CAST(NULL AS numeric(6,2)) AS [usagePct],
    CAST(NULL AS numeric(12,6)) AS [allocationPct],
    CAST(NULL AS int) AS [priority],
    CAST(NULL AS int) AS [inefficiencyPct],
    CAST(NULL AS int) AS [bonusTimeSec],
    CAST(NULL AS smallint) AS [setupCapable],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS int) AS [calendarModelId],
    CAST(NULL AS int) AS [savedBlockId],
    CAST(NULL AS int) AS [blockId],
    CAST(NULL AS int) AS [intervalId],
    CAST(NULL AS int) AS [fractionId],
    CAST(NULL AS datetime2(0)) AS [startAt],
    CAST(NULL AS datetime2(0)) AS [endAt],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [toolAssignmentId],
    CAST(NULL AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(NULL AS int) AS [loadTimeSec],
    CAST(NULL AS int) AS [unloadTimeSec],
    CAST(NULL AS int) AS [workTimeSec],
    CAST(NULL AS int) AS [operatorsRequired],
    CAST(NULL AS decimal(6,3)) AS [operatorUsagePct],
    CAST(NULL AS int) AS [qty],
    CAST(NULL AS varchar(15)) AS [machineCode],
    CAST(NULL AS varchar(128)) AS [programNumber],
    CAST(NULL AS int) AS [successorOperationId],
    CAST(NULL AS int) AS [predecessorOperationId],
    CAST(NULL AS varchar(2)) AS [linkType],
    CAST(NULL AS int) AS [legacyLinkType],
    CAST(D.[setupClassId] AS int) AS [setupClassId],
    CAST(D.[name] AS varchar(30)) AS [name],
    CAST(D.[note] AS varchar(255)) AS [note],
    CAST(D.[legacyPerac] AS numeric(8,3)) AS [legacyPerac],
    CAST(D.[groupCode] AS smallint) AS [groupCode],
    CAST(D.[value] AS numeric(18,6)) AS [value],
    CAST(D.[legacyTmsgl] AS int) AS [legacyTmsgl],
    CAST(NULL AS int) AS [measureId],
    CAST(NULL AS numeric(18,6)) AS [toleranceLow],
    CAST(NULL AS numeric(18,6)) AS [toleranceHigh],
    CAST(NULL AS varchar(10)) AS [uom]
FROM ClassiSetup AS D

UNION ALL

SELECT
    CAST(N'ParametriSetup' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [assignmentId],
    CAST(NULL AS int) AS [operationId],
    CAST(NULL AS varchar(8)) AS [resourceKind],
    CAST(NULL AS varchar(15)) AS [resourceCode],
    CAST(NULL AS varchar(8)) AS [assignmentRule],
    CAST(NULL AS bit) AS [preferential],
    CAST(NULL AS int) AS [usedInSavedPlan],
    CAST(NULL AS numeric(6,2)) AS [usagePct],
    CAST(NULL AS numeric(12,6)) AS [allocationPct],
    CAST(NULL AS int) AS [priority],
    CAST(NULL AS int) AS [inefficiencyPct],
    CAST(NULL AS int) AS [bonusTimeSec],
    CAST(NULL AS smallint) AS [setupCapable],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS int) AS [calendarModelId],
    CAST(NULL AS int) AS [savedBlockId],
    CAST(NULL AS int) AS [blockId],
    CAST(NULL AS int) AS [intervalId],
    CAST(NULL AS int) AS [fractionId],
    CAST(NULL AS datetime2(0)) AS [startAt],
    CAST(NULL AS datetime2(0)) AS [endAt],
    CAST(NULL AS int) AS [workplanId],
    CAST(NULL AS int) AS [toolAssignmentId],
    CAST(NULL AS smallint) AS [toolCode],
    CAST(NULL AS smallint) AS [toolTypeId],
    CAST(NULL AS int) AS [loadTimeSec],
    CAST(NULL AS int) AS [unloadTimeSec],
    CAST(NULL AS int) AS [workTimeSec],
    CAST(NULL AS int) AS [operatorsRequired],
    CAST(NULL AS decimal(6,3)) AS [operatorUsagePct],
    CAST(NULL AS int) AS [qty],
    CAST(NULL AS varchar(15)) AS [machineCode],
    CAST(NULL AS varchar(128)) AS [programNumber],
    CAST(NULL AS int) AS [successorOperationId],
    CAST(NULL AS int) AS [predecessorOperationId],
    CAST(NULL AS varchar(2)) AS [linkType],
    CAST(NULL AS int) AS [legacyLinkType],
    CAST(D.[setupClassId] AS int) AS [setupClassId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS varchar(255)) AS [note],
    CAST(NULL AS numeric(8,3)) AS [legacyPerac],
    CAST(NULL AS smallint) AS [groupCode],
    CAST(D.[value] AS numeric(18,6)) AS [value],
    CAST(NULL AS int) AS [legacyTmsgl],
    CAST(D.[measureId] AS int) AS [measureId],
    CAST(D.[toleranceLow] AS numeric(18,6)) AS [toleranceLow],
    CAST(D.[toleranceHigh] AS numeric(18,6)) AS [toleranceHigh],
    CAST(D.[uom] AS varchar(10)) AS [uom]
FROM ParametriSetup AS D;
