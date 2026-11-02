/*
   dbo.X_CLIENTE_Planning_Calendari - contratto Factory -> Excel, versione 2.
   AREA: Modelli, giorni, intervalli, festivita, assegnazioni, periodi, eccezioni e assenze.
   FOGLI ALIMENTATI: Calendari, Turni, Festivita, Eccezioni, Indisponibilita. Tutte le righe sono anche conservate in Provenienza record.

   COME LEGGERLA E MODIFICARLA
   - Una riga conserva UN tipo di record, indicato da TipoRecord.
   - I blocchi CTE iniziali contengono le SELECT di lavoro da modificare.
   - L UNION ALL finale allinea colonne e tipi: NULL significa non pertinente.
   - Non unire righe di grana diversa con JOIN, per non moltiplicare quantita o tempi.
   - Non rinominare TipoRecord o colonne senza aggiornare anche il contratto del connettore.
   - Esempio: SELECT * FROM dbo.X_CLIENTE_Planning_Calendari WHERE TipoRecord=N'Modelli'.

   PERIMETRO: X_CLIENTE_Planning_Perimetro conserva le fasi aperte delle commesse non chiuse.
   MANCANTI: NULL rimane mancante, zero rimane un valore; nessun arrivo o tempo viene inventato.
   DATE: valori locali Factory; il connettore assegna Europe/Rome, conserva l unita e applica l orizzonte.
   UNITA: tempi in secondi, percentuali 0..100, quantita nella unita esplicita dell articolo.
   La vista non scrive su Factory. Il pianificatore legge l Excel, mai direttamente questa vista.
*/
CREATE OR ALTER VIEW dbo.X_CLIENTE_Planning_Calendari
AS
WITH
/* Modelli: Una riga del tipo Modelli. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
Modelli AS (
SELECT TCCOD AS modelId, TCDSC AS name,
       CASE WHEN TCTYP = 0 THEN 7 ELSE TCTYP END AS cycleDays,
       CASE WHEN TCTYP = 0 THEN 'WEEKLY' ELSE 'CYCLIC' END AS cycleKind,
       TCSTD AS cycleStartDate
FROM dbo.A_TCL
),

/* GiorniModello: Una riga del tipo GiorniModello. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
GiorniModello AS (
SELECT G.TCCOD AS modelId, G.TGNUM AS dayIndex, G.TGCOD AS dayTypeId, D.TGDSC AS dayTypeName
FROM dbo.L_TCTG G LEFT JOIN dbo.A_TGG D ON D.TGCOD = G.TGCOD
),

/* Intervalli: Una riga del tipo Intervalli. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
Intervalli AS (
SELECT TGCOD AS dayTypeId, TGINI AS startSec, TGFIN AS endSec, ISNULL(NextDay, 0) AS crossesMidnight
FROM dbo.A_INT
),

/* Festivita: Una riga del tipo Festivita. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
Festivita AS (
SELECT GFDTE AS date, GFDSC AS name,
       CASE WHEN GFTYP = 0 THEN 'YEARLY' ELSE 'ONCE' END AS recurrence,
       TCCOD AS modelId
FROM dbo.A_GDF
WHERE GFOSS = 1
),

/* Assegnazioni: Una riga del tipo Assegnazioni. I valori sono mantenuti con il significato e le unita delle colonne Factory; le colonne non pertinenti agli altri tipi sono NULL. */
Assegnazioni AS (
SELECT IDNUM AS calendarId, TCCOD AS modelId, CLDTI AS validFrom
FROM dbo.A_CAL
),

/* Periodi: Una riga per cambio modello e periodo. La vista conserva tutti i periodi; il lettore seleziona quelli che intersecano l orizzonte richiesto (con margine di sette giorni). */
Periodi AS (
SELECT IDNUM AS calendarId, TCCOD AS modelId, TCSTD AS fromDate, TCEND AS toDate, TCSEQ AS seq
FROM dbo.L_CATC
),

/* Eccezioni: Una riga per intervallo sostitutivo del calendario/data; ECINI=ECFIN=0 indica giorno chiuso. Il lettore applica la finestra temporale richiesta. */
Eccezioni AS (
SELECT IDNUM AS calendarId, ECDTE AS date, ECINI AS startSec, ECFIN AS endSec, ECTYP AS kind
FROM dbo.A_ECC
),

/* Assenze: Una riga per giustificativo con inizio precedente alla fine; conserva anche lo stato approvazione. Il lettore applica la finestra temporale richiesta. */
Assenze AS (
SELECT OPCOD AS operatorCode, GADTE AS date, GAINI AS startSec, GAFIN AS endSec,
       GACOD AS reasonCode, APPROVED AS approved
FROM dbo.S_GSA
WHERE GAINI < GAFIN
)

/* Allineamento del contratto; la logica di business e nei CTE sopra. */
SELECT
    CAST(N'Modelli' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[modelId] AS int) AS [modelId],
    CAST(D.[name] AS varchar(30)) AS [name],
    CAST(D.[cycleDays] AS int) AS [cycleDays],
    CAST(D.[cycleKind] AS varchar(6)) AS [cycleKind],
    CAST(D.[cycleStartDate] AS date) AS [cycleStartDate],
    CAST(NULL AS smallint) AS [dayIndex],
    CAST(NULL AS int) AS [dayTypeId],
    CAST(NULL AS varchar(30)) AS [dayTypeName],
    CAST(NULL AS int) AS [startSec],
    CAST(NULL AS int) AS [endSec],
    CAST(NULL AS int) AS [crossesMidnight],
    CAST(NULL AS date) AS [date],
    CAST(NULL AS varchar(6)) AS [recurrence],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS date) AS [validFrom],
    CAST(NULL AS date) AS [fromDate],
    CAST(NULL AS date) AS [toDate],
    CAST(NULL AS smallint) AS [seq],
    CAST(NULL AS smallint) AS [kind],
    CAST(NULL AS varchar(15)) AS [operatorCode],
    CAST(NULL AS smallint) AS [reasonCode],
    CAST(NULL AS smallint) AS [approved]
FROM Modelli AS D

UNION ALL

SELECT
    CAST(N'GiorniModello' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[modelId] AS int) AS [modelId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS int) AS [cycleDays],
    CAST(NULL AS varchar(6)) AS [cycleKind],
    CAST(NULL AS date) AS [cycleStartDate],
    CAST(D.[dayIndex] AS smallint) AS [dayIndex],
    CAST(D.[dayTypeId] AS int) AS [dayTypeId],
    CAST(D.[dayTypeName] AS varchar(30)) AS [dayTypeName],
    CAST(NULL AS int) AS [startSec],
    CAST(NULL AS int) AS [endSec],
    CAST(NULL AS int) AS [crossesMidnight],
    CAST(NULL AS date) AS [date],
    CAST(NULL AS varchar(6)) AS [recurrence],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS date) AS [validFrom],
    CAST(NULL AS date) AS [fromDate],
    CAST(NULL AS date) AS [toDate],
    CAST(NULL AS smallint) AS [seq],
    CAST(NULL AS smallint) AS [kind],
    CAST(NULL AS varchar(15)) AS [operatorCode],
    CAST(NULL AS smallint) AS [reasonCode],
    CAST(NULL AS smallint) AS [approved]
FROM GiorniModello AS D

UNION ALL

SELECT
    CAST(N'Intervalli' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [modelId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS int) AS [cycleDays],
    CAST(NULL AS varchar(6)) AS [cycleKind],
    CAST(NULL AS date) AS [cycleStartDate],
    CAST(NULL AS smallint) AS [dayIndex],
    CAST(D.[dayTypeId] AS int) AS [dayTypeId],
    CAST(NULL AS varchar(30)) AS [dayTypeName],
    CAST(D.[startSec] AS int) AS [startSec],
    CAST(D.[endSec] AS int) AS [endSec],
    CAST(D.[crossesMidnight] AS int) AS [crossesMidnight],
    CAST(NULL AS date) AS [date],
    CAST(NULL AS varchar(6)) AS [recurrence],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS date) AS [validFrom],
    CAST(NULL AS date) AS [fromDate],
    CAST(NULL AS date) AS [toDate],
    CAST(NULL AS smallint) AS [seq],
    CAST(NULL AS smallint) AS [kind],
    CAST(NULL AS varchar(15)) AS [operatorCode],
    CAST(NULL AS smallint) AS [reasonCode],
    CAST(NULL AS smallint) AS [approved]
FROM Intervalli AS D

UNION ALL

SELECT
    CAST(N'Festivita' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[modelId] AS int) AS [modelId],
    CAST(D.[name] AS varchar(30)) AS [name],
    CAST(NULL AS int) AS [cycleDays],
    CAST(NULL AS varchar(6)) AS [cycleKind],
    CAST(NULL AS date) AS [cycleStartDate],
    CAST(NULL AS smallint) AS [dayIndex],
    CAST(NULL AS int) AS [dayTypeId],
    CAST(NULL AS varchar(30)) AS [dayTypeName],
    CAST(NULL AS int) AS [startSec],
    CAST(NULL AS int) AS [endSec],
    CAST(NULL AS int) AS [crossesMidnight],
    CAST(D.[date] AS date) AS [date],
    CAST(D.[recurrence] AS varchar(6)) AS [recurrence],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS date) AS [validFrom],
    CAST(NULL AS date) AS [fromDate],
    CAST(NULL AS date) AS [toDate],
    CAST(NULL AS smallint) AS [seq],
    CAST(NULL AS smallint) AS [kind],
    CAST(NULL AS varchar(15)) AS [operatorCode],
    CAST(NULL AS smallint) AS [reasonCode],
    CAST(NULL AS smallint) AS [approved]
FROM Festivita AS D

UNION ALL

SELECT
    CAST(N'Assegnazioni' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[modelId] AS int) AS [modelId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS int) AS [cycleDays],
    CAST(NULL AS varchar(6)) AS [cycleKind],
    CAST(NULL AS date) AS [cycleStartDate],
    CAST(NULL AS smallint) AS [dayIndex],
    CAST(NULL AS int) AS [dayTypeId],
    CAST(NULL AS varchar(30)) AS [dayTypeName],
    CAST(NULL AS int) AS [startSec],
    CAST(NULL AS int) AS [endSec],
    CAST(NULL AS int) AS [crossesMidnight],
    CAST(NULL AS date) AS [date],
    CAST(NULL AS varchar(6)) AS [recurrence],
    CAST(D.[calendarId] AS int) AS [calendarId],
    CAST(D.[validFrom] AS date) AS [validFrom],
    CAST(NULL AS date) AS [fromDate],
    CAST(NULL AS date) AS [toDate],
    CAST(NULL AS smallint) AS [seq],
    CAST(NULL AS smallint) AS [kind],
    CAST(NULL AS varchar(15)) AS [operatorCode],
    CAST(NULL AS smallint) AS [reasonCode],
    CAST(NULL AS smallint) AS [approved]
FROM Assegnazioni AS D

UNION ALL

SELECT
    CAST(N'Periodi' AS nvarchar(40)) AS TipoRecord,
    CAST(D.[modelId] AS int) AS [modelId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS int) AS [cycleDays],
    CAST(NULL AS varchar(6)) AS [cycleKind],
    CAST(NULL AS date) AS [cycleStartDate],
    CAST(NULL AS smallint) AS [dayIndex],
    CAST(NULL AS int) AS [dayTypeId],
    CAST(NULL AS varchar(30)) AS [dayTypeName],
    CAST(NULL AS int) AS [startSec],
    CAST(NULL AS int) AS [endSec],
    CAST(NULL AS int) AS [crossesMidnight],
    CAST(NULL AS date) AS [date],
    CAST(NULL AS varchar(6)) AS [recurrence],
    CAST(D.[calendarId] AS int) AS [calendarId],
    CAST(NULL AS date) AS [validFrom],
    CAST(D.[fromDate] AS date) AS [fromDate],
    CAST(D.[toDate] AS date) AS [toDate],
    CAST(D.[seq] AS smallint) AS [seq],
    CAST(NULL AS smallint) AS [kind],
    CAST(NULL AS varchar(15)) AS [operatorCode],
    CAST(NULL AS smallint) AS [reasonCode],
    CAST(NULL AS smallint) AS [approved]
FROM Periodi AS D

UNION ALL

SELECT
    CAST(N'Eccezioni' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [modelId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS int) AS [cycleDays],
    CAST(NULL AS varchar(6)) AS [cycleKind],
    CAST(NULL AS date) AS [cycleStartDate],
    CAST(NULL AS smallint) AS [dayIndex],
    CAST(NULL AS int) AS [dayTypeId],
    CAST(NULL AS varchar(30)) AS [dayTypeName],
    CAST(D.[startSec] AS int) AS [startSec],
    CAST(D.[endSec] AS int) AS [endSec],
    CAST(NULL AS int) AS [crossesMidnight],
    CAST(D.[date] AS date) AS [date],
    CAST(NULL AS varchar(6)) AS [recurrence],
    CAST(D.[calendarId] AS int) AS [calendarId],
    CAST(NULL AS date) AS [validFrom],
    CAST(NULL AS date) AS [fromDate],
    CAST(NULL AS date) AS [toDate],
    CAST(NULL AS smallint) AS [seq],
    CAST(D.[kind] AS smallint) AS [kind],
    CAST(NULL AS varchar(15)) AS [operatorCode],
    CAST(NULL AS smallint) AS [reasonCode],
    CAST(NULL AS smallint) AS [approved]
FROM Eccezioni AS D

UNION ALL

SELECT
    CAST(N'Assenze' AS nvarchar(40)) AS TipoRecord,
    CAST(NULL AS int) AS [modelId],
    CAST(NULL AS varchar(30)) AS [name],
    CAST(NULL AS int) AS [cycleDays],
    CAST(NULL AS varchar(6)) AS [cycleKind],
    CAST(NULL AS date) AS [cycleStartDate],
    CAST(NULL AS smallint) AS [dayIndex],
    CAST(NULL AS int) AS [dayTypeId],
    CAST(NULL AS varchar(30)) AS [dayTypeName],
    CAST(D.[startSec] AS int) AS [startSec],
    CAST(D.[endSec] AS int) AS [endSec],
    CAST(NULL AS int) AS [crossesMidnight],
    CAST(D.[date] AS date) AS [date],
    CAST(NULL AS varchar(6)) AS [recurrence],
    CAST(NULL AS int) AS [calendarId],
    CAST(NULL AS date) AS [validFrom],
    CAST(NULL AS date) AS [fromDate],
    CAST(NULL AS date) AS [toDate],
    CAST(NULL AS smallint) AS [seq],
    CAST(NULL AS smallint) AS [kind],
    CAST(D.[operatorCode] AS varchar(15)) AS [operatorCode],
    CAST(D.[reasonCode] AS smallint) AS [reasonCode],
    CAST(D.[approved] AS smallint) AS [approved]
FROM Assenze AS D;
