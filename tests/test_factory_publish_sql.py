"""Factory publication executed on a real SQL Server with a SYNTHETIC Factory schema.

Runs only when FACTORY_PUBLISH_SQL holds an ODBC connection string to a disposable server
(the CI job starts one in a container); the test creates and drops its own database.
"""
import os
import re
import unittest
from pathlib import Path

from .test_factory_export_safety import load_tool

publish = load_tool('publish_factory_plan')
CONNECTION = os.environ.get('FACTORY_PUBLISH_SQL')
SCHEMA = Path(__file__).resolve().parent / 'sql' / 'factory_publish_schema.sql'
DB = 'PublishTest'

SEED = """
INSERT dbo.X_PROGRESSIVO VALUES ('dbo.L_LAPN', 100);
INSERT dbo.A_COM (CONUM) VALUES (1), (2);
INSERT dbo.A_LOT (CONUM, LOCOD) VALUES (1, 1), (1, 2), (2, 1);
INSERT dbo.A_LAV (IDLAV, CONUM, LOCOD, IDFAS, LASTO, FPIAN, LADIP, LAHIP, LADTS, LASCH, LASTUPDATE) VALUES
 (101, 1, 1, 10, 41, 'N', NULL, NULL, '2026-10-20T08:00:00', 1, '2026-10-14T16:47:38.127'),
 (102, 1, 2, 10, 41, 'N', NULL, NULL, NULL, 0, NULL),
 (103, 1, 2, 20, 41, 'Y', '2026-09-01', '08:00', NULL, 0, '2026-10-01T10:00:00'),
 (201, 2, 1, 10, 41, 'N', NULL, NULL, NULL, 0, '2026-10-02T10:00:00');
INSERT dbo.L_LARE (IDLARE, CONUM, LOCOD, IDFAS, MACOD, OPCOD, RECOD, RIUSE, IDBLK, RIPOC) VALUES
 (1001, 1, 1, 10, 'M1', NULL, NULL, 'Y', 1, 100), (1002, 1, 1, 10, NULL, 'O1', NULL, 'N', 0, 50),
 (1003, 1, 2, 10, NULL, 'O1', NULL, 'Y', 3, 100), (1004, 1, 2, 10, NULL, 'O2', NULL, 'N', 0, 100),
 (1005, 1, 2, 20, NULL, 'O1', NULL, 'Y', 1, 100), (1006, 2, 1, 10, NULL, NULL, 'S1', 'N', 0, 100);
INSERT dbo.L_LAPN (IDINT, CONUM, LOCOD, IDFAS, IDBLK, BLDIP, BLHIP, BLDFP, BLHFP, IDFRZ, IDPTC, RIPOC) VALUES
 (5, 1, 1, 10, 1, '2026-09-01', '08:00', '2026-09-01', '09:00', 0, 0, 100),
 (6, 1, 2, 20, 1, '2026-09-01', '08:00', '2026-09-01', '09:00', 0, 0, 100);
"""
STATE = """SELECT (SELECT COUNT(*) FROM dbo.L_LAPN),
  (SELECT CHECKSUM_AGG(CHECKSUM(IDLAV, LADIP, LAHIP, LADFP, LAHFP, FPIAN, LASTUPDATE)) FROM dbo.A_LAV),
  (SELECT CHECKSUM_AGG(CHECKSUM(IDLARE, RIUSE, IDBLK)) FROM dbo.L_LARE),
  (SELECT CHECKSUM_AGG(CHECKSUM(CONUM, LOCOD, LODIP, LOHIP, LODFP, LOHFP)) FROM dbo.A_LOT),
  (SELECT CHECKSUM_AGG(CHECKSUM(CONUM, COPNF, CODIP, COHIP, CODFP, COHFP)) FROM dbo.A_COM)"""

RESOURCES = [(1001, 101, 'MACHINE', 'M1', 100), (1002, 101, 'OPERATOR', 'O1', 50), (1003, 102, 'OPERATOR', 'O1', 100),
             (1004, 102, 'OPERATOR', 'O2', 100), (1005, 103, 'OPERATOR', 'O1', 100), (1006, 201, 'SUPPLIER', 'S1', 100)]
PLAN = {'scenario_id': 'sqltest', 'scenario_digest': 'synthetic', 'assignments': [
    {'operation_ids': ['1/1/10'], 'effective_mode': 'forward',
     'segments': [{'start': '2026-10-19T08:30:00Z', 'end': '2026-10-19T10:00:00Z'},
                  {'start': '2026-10-19T11:00:00Z', 'end': '2026-10-19T12:00:00Z'}],
     'demands': [{'resource_id': 'MAC:M1', 'units': 1}, {'resource_id': 'OP:O1', 'units': 1}]},
    {'operation_ids': ['1/2/10'], 'effective_mode': 'backward',
     'segments': [{'start': '2026-10-21T06:00:00Z', 'end': '2026-10-21T07:00:00Z'}],
     'demands': [{'resource_id': 'OP:O2', 'units': 1}]},
    {'operation_ids': ['2/1/10'], 'effective_mode': 'external', 'segments': [], 'demands': [],
     'external_end': '2026-10-24T22:00:00Z'}],
    'unscheduled': [{'task_id': 'job:1/2/20', 'operation_ids': ['1/2/20'], 'code': 'NO_FEASIBLE_SLOT'}]}

@unittest.skipUnless(CONNECTION, 'FACTORY_PUBLISH_SQL not set: no disposable SQL Server')
class FactoryPublishSqlServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import pyodbc
        cls.pyodbc = pyodbc
        master = pyodbc.connect(CONNECTION, autocommit=True)
        master.execute(f"IF DB_ID('{DB}') IS NOT NULL BEGIN ALTER DATABASE {DB} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE {DB}; END")
        master.execute(f'CREATE DATABASE {DB}')
        master.close()
        cls.cn = pyodbc.connect(f'{CONNECTION};DATABASE={DB}', autocommit=True)
        for batch in re.split(r'^\s*GO\s*$', SCHEMA.read_text(encoding='utf-8'), flags=re.M):
            if batch.strip():
                cls.cn.execute(batch)
        cls.cn.execute(SEED)

    @classmethod
    def tearDownClass(cls):
        cls.cn.close()
        master = cls.pyodbc.connect(CONNECTION, autocommit=True)
        master.execute(f'ALTER DATABASE {DB} SET SINGLE_USER WITH ROLLBACK IMMEDIATE; DROP DATABASE {DB}')
        master.close()

    def scenario(self):
        """Read phases like export_factory_scenario.py does (status and LASTUPDATE at extraction)."""
        ops = [{'operationId': i, 'orderId': c, 'lotId': l, 'phaseId': f, 'statusCode': s,
                'sourceLastUpdate': u.isoformat() if u else None}
               for i, c, l, f, s, u in self.cn.execute('SELECT IDLAV, CONUM, LOCOD, IDFAS, LASTO, LASTUPDATE FROM dbo.A_LAV').fetchall()]
        return {'scenarioId': 'sqltest', 'source': {'database': DB}, 'planning': {'timeZone': 'Europe/Rome'},
                'operations': ops,
                'operationResources': [{'assignmentId': a, 'operationId': o, 'resourceKind': k, 'resourceCode': c,
                                        'usagePct': u, 'assignmentRule': 'ALLOWED'} for a, o, k, c, u in RESOURCES]}

    def state(self):
        return tuple(self.cn.execute(STATE).fetchone())

    def refused(self, changes, message):
        before = self.state()
        with self.assertRaisesRegex(self.pyodbc.Error, message):
            publish.execute(self.cn, publish.render_sql(changes), changes)
        self.assertEqual(self.state(), before)              # rolled back: nothing written

    def test_publish_guards_and_republish(self):
        changes = publish.build_changeset(self.scenario(), PLAN)
        summary = publish.execute(self.cn, publish.render_sql(changes), changes)
        self.assertEqual(summary, {'fasi': 3, 'righe_L_LARE': 5, 'nuove_righe_L_LARE': 0, 'blocchi_L_LAPN': 6,
                                   'lotti': 3, 'commesse': 2})
        self.assertEqual(publish.verify(self.cn, changes), ({}, 6))
        q = lambda sql: [tuple(r) for r in self.cn.execute(sql).fetchall()]
        # Blocks numbered by the trigger; only republished phases lose old blocks, except
        # those of the phase the engine could not schedule (IDINT 6).
        self.assertEqual(q('SELECT IDINT, IDFAS, IDBLK, CONVERT(varchar(5), BLHIP), CONVERT(varchar(5), BLHFP) FROM dbo.L_LAPN ORDER BY IDINT'),
                         [(6, 20, 1, '08:00', '09:00'),
                          (101, 10, 1, '10:30', '12:00'), (102, 10, 1, '13:00', '14:00'), (103, 10, 2, '10:30', '12:00'),
                          (104, 10, 2, '13:00', '14:00'), (105, 10, 2, '08:00', '09:00'),
                          (106, 10, 1, '00:00', '00:00')])   # anchored external: zero block on the supplier
        self.assertEqual(q('SELECT IDLARE, RIUSE, IDBLK FROM dbo.L_LARE ORDER BY IDLARE'),
                         [(1001, 'Y', 1), (1002, 'Y', 2), (1003, 'N', 1), (1004, 'Y', 2), (1005, 'Y', 1), (1006, 'Y', 1)])
        # The unscheduled phase keeps its dates; user constraints are not cleared.
        self.assertEqual(q("SELECT IDLAV, CONVERT(varchar(10), LADIP), FPIAN, LASCH, CONVERT(varchar(16), LADTS, 126) FROM dbo.A_LAV WHERE IDLAV IN (101, 103) ORDER BY IDLAV"),
                         [(101, '2026-10-19', 'Y', 1, '2026-10-20T08:00'), (103, '2026-09-01', 'Y', 0, None)])
        self.assertEqual(q('SELECT CONUM, COPNF, CONVERT(varchar(10), CODIP), CONVERT(varchar(5), COHIP), CONVERT(varchar(10), CODFP), CONVERT(varchar(5), COHFP) FROM dbo.A_COM ORDER BY CONUM'),
                         [(1, None, '2026-09-01', '08:00', '2026-10-21', '09:00'), (2, 0, '2026-10-25', '00:00', '2026-10-25', '00:00')])

        # The same extraction is now stale (LASTUPDATE renewed by the trigger).
        self.refused(changes, "modificate dopo l'estrazione")
        # LASTUPDATE going from a value to NULL, and from NULL to a value, are both changes.
        fresh = publish.build_changeset(self.scenario(), PLAN)
        self.cn.execute('UPDATE dbo.A_LAV SET LASTUPDATE = NULL WHERE IDLAV = 102')
        self.refused(fresh, "1 fasi modificate dopo l'estrazione")
        fresh = publish.build_changeset(self.scenario(), PLAN)
        self.cn.execute("UPDATE dbo.A_LAV SET LASTUPDATE = '2026-10-30T10:00:00' WHERE IDLAV = 102")
        self.refused(fresh, "1 fasi modificate dopo l'estrazione")
        # Months apart (DATEDIFF in milliseconds used to overflow here, seen on a Factory copy).
        fresh = publish.build_changeset(self.scenario(), PLAN)
        self.cn.execute("UPDATE dbo.A_LAV SET LASTUPDATE = '2027-06-30T10:00:00' WHERE IDLAV = 102")
        self.refused(fresh, "1 fasi modificate dopo l'estrazione")
        # A resource row that disappeared or changed (usage) meanwhile.
        fresh = publish.build_changeset(self.scenario(), PLAN)
        self.cn.execute('DELETE FROM dbo.L_LARE WHERE IDLARE = 1004')
        self.refused(fresh, 'L_LARE non pi')
        self.cn.execute("INSERT dbo.L_LARE (IDLARE, CONUM, LOCOD, IDFAS, OPCOD, RIUSE, IDBLK, RIPOC) VALUES (1004, 1, 2, 10, 'O2', 'Y', 2, 100)")
        self.cn.execute('UPDATE dbo.L_LARE SET RIPOC = 80 WHERE IDLARE = 1002')
        self.refused(fresh, 'L_LARE non pi')
        self.cn.execute('UPDATE dbo.L_LARE SET RIPOC = 50 WHERE IDLARE = 1002')

        # Republishing from a fresh extraction succeeds and reproduces the same plan.
        fresh = publish.build_changeset(self.scenario(), PLAN)
        publish.execute(self.cn, publish.render_sql(fresh), fresh)
        self.assertEqual(publish.verify(self.cn, fresh), ({}, 6))
        self.assertEqual(q('SELECT COUNT(*), MIN(IDINT), MAX(IDINT) FROM dbo.L_LAPN WHERE IDINT > 6'), [(6, 107, 112)])
        # Parent dates and type must match the complete persisted phase envelope.
        self.cn.execute("UPDATE dbo.A_LOT SET LODIP = '2026-08-01' WHERE CONUM=1 AND LOCOD=2")
        parent_problems, _ = publish.verify(self.cn, fresh)
        self.assertIn('A_LOT date diverse dalle fasi', parent_problems)
        self.cn.execute("UPDATE dbo.A_LOT SET LODIP = '2026-09-01' WHERE CONUM=1 AND LOCOD=2")
        self.cn.execute("UPDATE dbo.A_COM SET CODIP = '2026-08-01' WHERE CONUM=1")
        parent_problems, _ = publish.verify(self.cn, fresh)
        self.assertIn('A_COM date diverse dalle fasi', parent_problems)
        self.cn.execute("UPDATE dbo.A_COM SET CODIP = '2026-09-01' WHERE CONUM=1")
        self.cn.execute("UPDATE dbo.A_COM SET COPNF = 3 WHERE CONUM=2")
        parent_problems, _ = publish.verify(self.cn, fresh)
        self.assertIn('A_COM COPNF diverso dal piano', parent_problems)
        self.cn.execute("UPDATE dbo.A_COM SET COPNF = 0 WHERE CONUM=2")
        # A trigger or user changing a block afterwards is caught by --verify.
        self.cn.execute("UPDATE dbo.L_LAPN SET BLHFP = '12:30' WHERE IDINT = 107")
        problems, _ = publish.verify(self.cn, fresh)
        self.assertIn('L_LAPN blocchi diversi dal piano (fase, IDBLK, date, RIPOC, IDPTC)', problems)

if __name__ == '__main__':
    unittest.main()
