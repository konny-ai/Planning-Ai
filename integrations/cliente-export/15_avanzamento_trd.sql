-- Avanzamento TRD delle fasi aperte, nel formato canonico operation_progress (dominio progress, ADR 004).
-- Stessa logica di V_OrderState_Job / V_OrderState_TicketTime di Factory:
--   tempi dai ticket S_ODL (setup OTLA*, lavoro OTLP*+OTF*), ripartiti sulle fasi di un ticket multiplo
--   (OLTYP='M', nesting) con i rapporti della vista; quantita' prodotta da S_PAV (COMPUTEJOBPRODUCEDQUANTITY);
--   macchina in uso solo se c'e' una sessione S_SES aperta (SESFE nullo): nulla si deduce dall'ultima sessione.
-- I secondi registrati sono riportati alla durata della fase dividendo per n. risorse x % d'impiego (FANML/FAPLM,
-- FANPL/FAPLO) e prendendo il massimo fra macchine e operatori, come la modalita' "Con tempi" del legacy.
-- Solo fasi aperte (stato 41/42/43) con tempi o quantita' registrati. planned_quantity (LAQTP) e
-- last_activity_local (GetJobEndTime, ora locale Factory) servono alla politica a pezzi.
WITH ticket AS (
    SELECT CONUM, LOCOD, IDFAS,
           SUM(ISNULL(MacWorkTime, 0) * WorkTimeRatio)  AS macWork,
           SUM(ISNULL(OprWorkTime, 0) * WorkTimeRatio)  AS oprWork,
           SUM(ISNULL(MacSetupTime, 0) * SetupTimeRatio) AS macSetup,
           SUM(ISNULL(OprSetupTime, 0) * SetupTimeRatio) AS oprSetup
    FROM dbo.V_OrderState_TicketTime
    GROUP BY CONUM, LOCOD, IDFAS
), running AS (
    SELECT O.CONUM, O.LOCOD, O.IDFAS, MIN(RTRIM(S.MACOD)) AS machine, COUNT(DISTINCT RTRIM(S.MACOD)) AS machines
    FROM dbo.S_SES S
         INNER JOIN dbo.V_ODLA_OLCOD O ON O.OLCOD = S.OLCOD
    WHERE S.SESFE IS NULL AND S.MACOD IS NOT NULL
    GROUP BY O.CONUM, O.LOCOD, O.IDFAS
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
)
SELECT CONCAT(CONUM, '/', LOCOD, '/', IDFAS) AS operation_id,
       'started' AS status,
       CAST(NULL AS varchar(1)) AS completed_at,
       CAST(ISNULL(produced, 0) AS decimal(18, 6)) AS produced_quantity,
       CAST(CEILING((SELECT MAX(v) FROM (VALUES
            (CASE WHEN macShare > 0 THEN ISNULL(macWork, 0) / macShare ELSE 0 END),
            (CASE WHEN oprShare > 0 THEN ISNULL(oprWork, 0) / oprShare ELSE 0 END)) x(v))) AS bigint) AS recorded_work_seconds,
       CAST(CEILING((SELECT MAX(v) FROM (VALUES
            (CASE WHEN macShare > 0 THEN ISNULL(macSetup, 0) / macShare ELSE 0 END),
            (CASE WHEN oprShare > 0 THEN ISNULL(oprSetup, 0) / oprShare ELSE 0 END)) x(v))) AS bigint) AS recorded_setup_seconds,
       CASE WHEN machines = 1 THEN CONCAT('MAC:', machine) END AS resource_id,
       -- non canonici: usati da build_avanzamento_trd.py per la politica a pezzi
       CAST(planned AS decimal(18, 6)) AS planned_quantity, lastActivity AS last_activity_local
FROM job
WHERE ISNULL(macWork, 0) + ISNULL(oprWork, 0) + ISNULL(macSetup, 0) + ISNULL(oprSetup, 0) > 0
   OR ISNULL(produced, 0) > 0 OR machine IS NOT NULL
ORDER BY CONUM, LOCOD, IDFAS;
