/* =====================================================================
   01_calendari.sql - modelli di calendario, assegnazioni, festività,
   eccezioni e indisponibilità. Ogni SELECT è un result set distinto,
   nell'ordine atteso dall'esportatore.

   Parametri: @DataInizio date, @DataFine date (finestra di eccezioni e
   indisponibilità; i modelli e le assegnazioni sono caricati per intero).

   Regola del giorno del ciclo (da dbo.ComputeCalendarIntervals):
     TGNUM = DATEDIFF(day, A_TCL.TCSTD, data) % (CASE TCTYP WHEN 0 THEN 7 ELSE TCTYP END)
   Precedenza nel giorno: festività osservata (A_GDF.GFOSS=1) -> nessun
   intervallo; eccezione A_ECC della risorsa -> sostituisce il modello;
   altrimenti intervalli A_INT del tipo giornata.
   ===================================================================== */

-- RS1 calendarModels: modello (turno settimanale o ciclo di N giorni)
SELECT TCCOD AS modelId, TCDSC AS name,
       CASE WHEN TCTYP = 0 THEN 7 ELSE TCTYP END AS cycleDays,
       CASE WHEN TCTYP = 0 THEN 'WEEKLY' ELSE 'CYCLIC' END AS cycleKind,
       TCSTD AS cycleStartDate
FROM dbo.A_TCL;

-- RS2 calendarModelDays: giorno del ciclo -> tipo giornata
SELECT G.TCCOD AS modelId, G.TGNUM AS dayIndex, G.TGCOD AS dayTypeId, D.TGDSC AS dayTypeName
FROM dbo.L_TCTG G LEFT JOIN dbo.A_TGG D ON D.TGCOD = G.TGCOD;

-- RS3 dayTypeIntervals: intervalli lavorativi in secondi dalla mezzanotte
--     NextDay=1: l'intervallo finisce il giorno dopo (turno notturno)
SELECT TGCOD AS dayTypeId, TGINI AS startSec, TGFIN AS endSec, ISNULL(NextDay, 0) AS crossesMidnight
FROM dbo.A_INT
ORDER BY TGCOD, TGINI;

-- RS4 holidays: solo osservate; GFTYP 0 = ricorrente ogni anno (giorno/mese), 1 = data puntuale
SELECT GFDTE AS date, GFDSC AS name,
       CASE WHEN GFTYP = 0 THEN 'YEARLY' ELSE 'ONCE' END AS recurrence,
       TCCOD AS modelId
FROM dbo.A_GDF
WHERE GFOSS = 1;

-- RS5 calendarAssignments: calendario (IDNUM) -> modello, con data di validità
SELECT IDNUM AS calendarId, TCCOD AS modelId, CLDTI AS validFrom
FROM dbo.A_CAL;

-- RS6 calendarPeriods: cambio di modello per periodo su un calendario (L_CATC)
SELECT IDNUM AS calendarId, TCCOD AS modelId, TCSTD AS fromDate, TCEND AS toDate, TCSEQ AS seq
FROM dbo.L_CATC
WHERE TCEND >= DATEADD(day, -7, @DataInizio) AND TCSTD <= @DataFine;

-- RS7 calendarExceptions: intervalli sostitutivi per calendario e data
--     ECINI=ECFIN=0 = giorno chiuso. ECTYP conservato (il legacy non lo legge).
SELECT IDNUM AS calendarId, ECDTE AS date, ECINI AS startSec, ECFIN AS endSec, ECTYP AS kind
FROM dbo.A_ECC
WHERE ECDTE BETWEEN DATEADD(day, -7, @DataInizio) AND @DataFine
ORDER BY IDNUM, ECDTE, ECINI;

-- RS8 operatorAbsences: giustificativi (assenze) degli operatori
SELECT OPCOD AS operatorCode, GADTE AS date, GAINI AS startSec, GAFIN AS endSec,
       GACOD AS reasonCode, APPROVED AS approved
FROM dbo.S_GSA
WHERE GADTE BETWEEN DATEADD(day, -7, @DataInizio) AND @DataFine
  AND GAINI < GAFIN;
