/* =====================================================================
   02_risorse.sql - risorse, reparti, tipi fase e abilitazioni.
   Richiede #U_JOB (00_universo.sql) per limitare i fornitori a quelli usati.
   Percentuali Factory: valori x100 (10000 = 100%). Qui sono già convertite.
   Dati personali: degli operatori si esportano solo codice e descrizione.
   ===================================================================== */

-- RS1 machines (tipo risorsa legacy 2)
SELECT MACOD AS code, MADSC AS name, IDNUM AS calendarId, MASTO AS statusCode,
       MACUN AS costPerHour, ISNULL(INEFF, 0) AS inefficiencyPct, ISNULL(RPRIO, 0) AS priority,
       ISNULL(TMBNS, 0) AS bonusTimeSec, IDCAT AS currentSetupClassId,
       ISNULL(IsCentre, 0) AS isWorkCenter, IDGroup AS groupId, MachineType AS machineTypeId,
       MAMXI / 100.0 AS maxUtilizationPct, MAXSX AS maxSizeX, MAXSY AS maxSizeY, MACDC AS costCenter
FROM dbo.A_MAC;

-- RS2 operators (tipo 1). OPMXI = massimo impiego (x100); OPSTO = stato (vedi PRD 04)
SELECT OPCOD AS code, OPDSC AS name, IDNUM AS calendarId, OPSTO AS statusCode,
       OPMXI / 100.0 AS maxUtilizationPct, OPCUN AS costPerHour,
       ISNULL(INEFF, 0) AS inefficiencyPct, ISNULL(RPRIO, 0) AS priority,
       ISNULL(OPATR, 0) AS setupCapable, ISNULL(TMBNS, 0) AS bonusTimeSec,
       GRCOD AS groupCode, PartTime AS partTimePct
FROM dbo.A_OPR;

-- RS3 suppliers (tipo 4): solo quelli assegnati a fasi in piano o con ordini di conto lavoro
SELECT F.IDFOR AS supplierId, F.FOCOD AS code, F.FORAG AS name, F.IDNUM AS calendarId,
       F.CSTYP AS deliveryRuleType, F.CSDAY AS deliveryRuleRefDate, F.CSDAT AS deliveryRuleData,
       F.LeadTime AS leadTime, F.LeadTimeType AS leadTimeType, ISNULL(F.IsObsolete, 0) AS obsolete
FROM dbo.A_FOR F
WHERE F.FOCOD IN (SELECT R.RECOD FROM dbo.L_LARE R JOIN #U_JOB U ON U.CONUM = R.CONUM AND U.LOCOD = R.LOCOD AND U.IDFAS = R.IDFAS WHERE R.RECOD IS NOT NULL)
   OR F.IDFOR IN (SELECT O.IDFOR FROM dbo.A_ORF O JOIN dbo.L_OFLA A ON A.IDORF = O.IDORF JOIN #U_JOB U ON U.CONUM = A.CONUM AND U.LOCOD = A.LOCOD AND U.IDFAS = A.IDFAS);

-- RS4 tools (attrezzature/stampi, tipo 8) con tipo attrezzatura
SELECT A.ATCOD AS code, A.ATTYP AS toolTypeId, T.TADSC AS toolTypeName, A.ATDSC AS name,
       A.IDNUM AS calendarId, A.ATDX1 AS sizeX, A.ATDY1 AS sizeY, A.ATDZ1 AS sizeZ,
       A.ATPMA AS maxLoadKg, A.TMCAR AS loadTimeSec, A.TMSCA AS unloadTimeSec,
       A.OPNUM AS operatorsRequired, A.OPPRC AS operatorUsagePct, A.ATDII AS activeFrom, A.ATDFI AS activeTo
FROM dbo.A_ATR A LEFT JOIN dbo.A_TAT T ON T.ATTYP = A.ATTYP;

-- RS5 pallets (tipo 7) e abbinamento pallet-macchina
SELECT P.PLCOD AS code, P.PLDSC AS name, P.IDNUM AS calendarId, P.PLSTA AS statusCode,
       P.CDGRP AS groupId, P.TMCAR AS loadTimeSec, P.TMSCA AS unloadTimeSec
FROM dbo.A_PLT P;
SELECT PLCOD AS palletCode, CDGRP AS groupId, MACOD AS machineCode,
       TMCAR AS loadTimeSec, TMSCA AS unloadTimeSec, OPNUM AS operatorsRequired, OPPRC AS operatorUsagePct
FROM dbo.L_MAPL;

-- RS7 departments e appartenenze (una risorsa può stare in più reparti)
SELECT RECOD AS code, REDSC AS name, REDSA AS shortName FROM dbo.A_REP;
SELECT RECOD AS departmentCode, 'MACHINE' AS resourceKind, MACOD AS resourceCode FROM dbo.L_REMA
UNION ALL
SELECT RECOD, 'OPERATOR', OPCOD FROM dbo.L_REOP;

-- RS9 phaseTypes (tipi fase / tecnologie)
SELECT TFCOD AS phaseTypeId, TFDSC AS name, TFORD AS sortOrder, TFVIS AS visible, TFACC AS groupedHandling
FROM dbo.A_TFA;

-- RS10 capabilities: quali tipi fase lavora una risorsa, con costo, priorità, inefficienza, calendario
SELECT 'MACHINE' AS resourceKind, MACOD AS resourceCode, TFCOD AS phaseTypeId,
       MACUN AS costPerHour, ISNULL(RPRIO, 0) AS priority, ISNULL(INEFF, 0) AS inefficiencyPct,
       ISNULL(TMBNS, 0) AS bonusTimeSec, TCCOD AS calendarModelId, IDNUM AS calendarId, FAAUT AS automatic
FROM dbo.L_MATF
UNION ALL
SELECT 'OPERATOR', OPCOD, TFCOD, OPCUN, ISNULL(RPRIO, 0), ISNULL(INEFF, 0),
       ISNULL(TMBNS, 0), TCCOD, IDNUM, NULL
FROM dbo.L_OPTF;
