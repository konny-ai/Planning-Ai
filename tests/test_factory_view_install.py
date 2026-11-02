"""Missing Factory views are created from the documented SQL; existing objects are never replaced."""
import unittest
from unittest.mock import patch

from planning_factory.adapters import factory_view_install as install
from planning_factory.adapters.factory_extraction import FactoryExtraction, _DEFAULT_VIEWS


class FakeCursor:
    def __init__(self, objects, broken):
        self.objects, self.broken, self.executed, self.row = objects, broken, [], None

    def execute(self, sql, *params):
        if sql.startswith('SELECT type FROM sys.objects'):
            kind = self.objects.get(params[0])
            self.row = None if kind is None else (kind + ' ',)
            return
        if any(word in sql for word in self.broken):
            raise RuntimeError("('42S02', \"[42S02] Invalid object name 'dbo.A_MANCANTE'. (208)\")")
        self.executed.append(sql)

    def fetchone(self):
        return self.row

    def close(self):
        pass


class FakeConnection:
    def __init__(self, objects=None, broken=()):
        self.cursor_ = FakeCursor(objects or {}, broken)

    def cursor(self):
        return self.cursor_


class ViewInstallTests(unittest.TestCase):
    def test_creates_the_perimeter_first_and_every_view_under_its_configured_name(self):
        views = dict(_DEFAULT_VIEWS, fasi='dbo.MIE_Fasi')
        connection = FakeConnection()
        result = install.install(connection, views)
        self.assertEqual(len(result['created']), 9)
        self.assertEqual(result['failed'], [])
        statements = connection.cursor_.executed
        self.assertIn('CREATE VIEW [dbo].[X_CLIENTE_Planning_Perimetro]', statements[0])
        self.assertTrue(any('CREATE VIEW [dbo].[MIE_Fasi]' in s for s in statements))
        self.assertFalse(any('CREATE OR ALTER' in s.upper() for s in statements))

    def test_existing_views_are_left_untouched_and_the_perimeter_is_not_added_for_nothing(self):
        objects = {'[' + v.replace('.', '].[') + ']': 'V' for v in _DEFAULT_VIEWS.values()}
        connection = FakeConnection(objects)
        result = install.install(connection, _DEFAULT_VIEWS)
        self.assertEqual(result['created'], [])
        self.assertEqual(len(result['present']), 8)
        self.assertEqual(connection.cursor_.executed, [])

    def test_a_table_with_the_same_name_is_reported_not_replaced(self):
        objects = {'[dbo].[X_CLIENTE_Planning_Fasi]': 'U'}
        connection = FakeConnection(objects)
        result = install.install(connection, _DEFAULT_VIEWS)
        self.assertEqual([f['view'] for f in result['failed']], ['dbo.X_CLIENTE_Planning_Fasi'])
        self.assertNotIn('dbo.X_CLIENTE_Planning_Fasi', result['created'])

    def test_one_failing_view_does_not_hide_the_others(self):
        connection = FakeConnection(broken=('X_CLIENTE_Planning_Nesting]',))
        result = install.install(connection, _DEFAULT_VIEWS)
        self.assertEqual([f['view'] for f in result['failed']], ['dbo.X_CLIENTE_Planning_Nesting'])
        self.assertEqual(len(result['created']), 8)
        self.assertIn('manca un oggetto Factory', result['failed'][0]['error'])


class MissingDriverTests(unittest.TestCase):
    def test_missing_pyodbc_gets_an_actionable_message(self):
        extraction = FactoryExtraction('.', '.', '.', lambda: None)
        data = dict(server='HOST\\SQL', database='Factory_Test', user='', password='', views=dict(_DEFAULT_VIEWS))
        with patch.dict('sys.modules', {'pyodbc': None}):
            result = extraction.test_connection(data)
            created = extraction.create_views(data)
        for outcome in (result, created):
            self.assertFalse(outcome['ok'])
            self.assertIn('pip install pyodbc', outcome['error'])
            self.assertIn('avvia.bat', outcome['error'])


class EmptyConfigTests(unittest.TestCase):
    def test_an_empty_link_file_falls_back_to_the_defaults(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / 'factory-source.json').write_text('', encoding='utf-8')
            config = FactoryExtraction(folder, folder, folder, lambda: None).config()
        self.assertEqual(config['views'], _DEFAULT_VIEWS)


if __name__ == '__main__':
    unittest.main()
