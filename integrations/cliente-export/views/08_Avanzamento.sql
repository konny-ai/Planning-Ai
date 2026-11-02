/*
   dbo.X_CLIENTE_Planning_Avanzamento - contratto Factory -> Excel, versione 2.
   AREA: Quantita prodotte, tempi macchina/operatore e ultima attivita effettivamente registrata.
   FOGLI ALIMENTATI: Consuntivi, Provenienza record. Tutte le righe sono anche conservate in Provenienza record.

   COME LEGGERLA E MODIFICARLA
   - Una riga conserva UN tipo di record, indicato da TipoRecord.
   - I blocchi CTE iniziali contengono le SELECT di lavoro da modificare.
   - L UNION ALL finale allinea colonne e tipi: NULL significa non pertinente.
   - Non unire righe di grana diversa con JOIN, per non moltiplicare quantita o tempi.
   - Non rinominare TipoRecord o colonne senza aggiornare anche il contratto del connettore.
   - Esempio: SELECT * FROM dbo.X_CLIENTE_Planning_Avanzamento WHERE TipoRecord=N'Consuntivi'.

   PERIMETRO: X_CLIENTE_Planning_Perimetro conserva le fasi aperte delle commesse non chiuse.
   MANCANTI: NULL rimane mancante, zero rimane un valore; nessun arrivo o tempo viene inventato.
   DATE: valori locali Factory; il connettore assegna Europe/Rome, conserva l unita e applica l orizzonte.
   UNITA: tempi in secondi, percentuali 0..100, quantita nella unita esplicita dell articolo.
   La vista non scrive su Factory. Il pianificatore legge l Excel, mai direttamente questa vista.
*/
CREATE OR ALTER VIEW dbo.X_CLIENTE_Planning_Avanzamento
AS
WITH
ticket AS (
    SELECT CONUM, LOCOD, IDFAS,
           SUM(ISNULL(MacWorkTime, 0) * WorkTimeRatio) AS macWork,
           SUM(ISNULL(OprWorkTime, 0) * WorkTimeRatio) AS oprWork,
           SUM(ISNULL(MacSetupTime, 0) * SetupTimeRatio) AS macSetup,
           SUM(ISNULL(OprSetupTime, 0) * SetupTimeRatio) AS oprSetup
    FROM dbo.V_OrderState_TicketTime GROUP BY CONUM, LOCOD, IDFAS
), running AS (
    SELECT O.CONUM, O.LOCOD, O.IDFAS, MIN(RTRIM(S.MACOD)) AS machine, COUNT(DISTINCT RTRIM(S.MACOD)) AS machines
    FROM dbo.S_SES S INNER JOIN dbo.V_ODLA_OLCOD O ON O.OLCOD = S.OLCOD
    WHERE S.SESFE IS NULL AND S.MACOD IS NOT NULL GROUP BY O.CONUM, O.LOCOD, O.IDFAS
), job AS (
    SELECT L.CONUM, L.LOCOD, L.IDFAS,
           dbo.COMPUTEJOBPRODUCEDQUANTITY(L.CONUM, L.LOCOD, L.IDFAS) AS produced,
           L.LAQTP AS planned, dbo.GetJobEndTime(L.CONUM, L.LOCOD, L.IDFAS, L.OLCOD) AS lastActivity,
           T.macWork, T.oprWork, T.macSetup, T.oprSetup,
           ISNULL(L.FANML, 0) * ISNULL(L.FAPLM, 0) / 100.0 AS macShare,
           ISNULL(L.FANPL, 0) * ISNULL(L.FAPLO, 0) / 100.0 AS oprShare,
           R.machine, R.machines
    FROM dbo.A_LAV L
    LEFT JOIN ticket T ON T.CONUM = L.CONUM AND T.LOCOD = L.LOCOD AND T.IDFAS = L.IDFAS
    LEFT JOIN running R ON R.CONUM = L.CONUM AND R.LOCOD = L.LOCOD AND R.IDFAS = L.IDFAS
    WHERE L.LASTO IN (41, 42, 43)
),

/* Consuntivi: Una riga per fase con attivita registrata. La quantita prodotta e ricostruita dalle funzioni Factory, i tempi dai ticket macchina/operatore con i rapporti di attribuzione. Non confondere durata trascorsa con ore persona. Le funzioni di consuntivazione possono incidere sul tempo di estrazione. */
Consuntivi AS (
SELECT CONCAT(CONUM, '/', LOCOD, '/', IDFAS) AS operation_id, 'started' AS status,
       CAST(NULL AS varchar(1)) AS completed_at, CAST(ISNULL(produced, 0) AS decimal(18, 6)) AS produced_quantity,
       CAST(CEILING((SELECT MAX(v) FROM (VALUES
            (CASE WHEN macShare > 0 THEN ISNULL(macWork, 0) / macShare ELSE 0 END),
            (CASE WHEN oprShare > 0 THEN ISNULL(oprWork, 0) / oprShare ELSE 0 END)) x(v))) AS bigint) AS recorded_work_seconds,
       CAST(CEILING((SELECT MAX(v) FROM (VALUES
            (CASE WHEN macShare > 0 THEN ISNULL(macSetup, 0) / macShare ELSE 0 END),
            (CASE WHEN oprShare > 0 THEN ISNULL(oprSetup, 0) / oprShare ELSE 0 END)) x(v))) AS bigint) AS recorded_setup_seconds,
       CASE WHEN machines = 1 THEN CONCAT('MAC:', machine) END AS resource_id,
       CAST(planned AS decimal(18, 6)) AS planned_quantity, lastActivity AS last_activity_local
FROM job
WHERE ISNULL(macWork, 0) + ISNULL(oprWork, 0) + ISNULL(macSetup, 0) + ISNULL(oprSetup, 0) > 0
   OR ISNULL(produced, 0) > 0 OR machine IS NOT NULL
)

/* Allineamento del contratto; la logica di business e nei CTE sopra. */
SELECT
    CAST(N'Consuntivi' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[operation_id] AS varchar(38)) AS [operation_id],
    CAST(D.[status] AS varchar(7)) AS [status],
    CAST(D.[completed_at] AS varchar(1)) AS [completed_at],
    CAST(D.[produced_quantity] AS decimal(18,6)) AS [produced_quantity],
    CAST(D.[recorded_work_seconds] AS bigint) AS [recorded_work_seconds],
    CAST(D.[recorded_setup_seconds] AS bigint) AS [recorded_setup_seconds],
    CAST(D.[resource_id] AS varchar(19)) AS [resource_id],
    CAST(D.[planned_quantity] AS decimal(18,6)) AS [planned_quantity],
    CAST(D.[last_activity_local] AS datetime2(6)) AS [last_activity_local]
FROM Consuntivi AS D;
