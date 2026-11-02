/*
   X_CLIENTE_Planning_Perimetro - supporto interno alle otto viste di ingresso.
   Una riga = una fase Factory potenzialmente esportabile, identificata da IDLAV.

   Si escludono commesse chiuse/annullate (24,25) e fasi chiuse/annullate (44,45).
   Si mantengono commesse bloccate e fasi FAPNF=N: la decisione di pianificazione
   e il legame con i nesting aperti sono conservati nei dati, non cancellati qui.
   Tutti i lotti delle commesse incluse restano ricostruibili dalle altre viste.

   Per cambiare il perimetro comune, modificare SOLO il WHERE di questa vista.
   Nessun dato Factory viene aggiornato. Non e' una funzione di pianificazione.
*/
CREATE OR ALTER VIEW dbo.X_CLIENTE_Planning_Perimetro
AS
SELECT L.IDLAV, L.CONUM, L.LOCOD, L.IDFAS
FROM dbo.A_LAV AS L
INNER JOIN dbo.A_LOT AS T ON T.CONUM=L.CONUM AND T.LOCOD=L.LOCOD
INNER JOIN dbo.A_COM AS C ON C.CONUM=L.CONUM
WHERE C.COSTO NOT IN (24,25)
  AND L.LASTO NOT IN (44,45);
