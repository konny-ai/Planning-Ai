/* CLIENTE - sede per commessa (A_COM.Division: 3 = Sede A, 4 = Sede B) e magazzini per sede.
   Le macchine (A_MAC) NON hanno una sede nel database e molte lavorano commesse di entrambe: la mappa
   macchina -> sede e i trasferimenti tra sedi vanno forniti da CLIENTE. */
SELECT C.CONUM AS orderId, C.COCOD AS orderCode, C.Division AS divisionId,
       CASE C.Division WHEN 3 THEN 'Sede A' WHEN 4 THEN 'Sede B' END AS siteName
FROM dbo.A_COM C WHERE C.COSTO NOT IN (24, 25);
SELECT W.Divisione AS divisionId, W.CodiceMagazzino AS warehouseCode FROM dbo.X_WarehouseDivision W;
