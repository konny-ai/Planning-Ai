"""All Factory business reads go through the complete eight-view contract."""
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from planning_factory.adapters import factory_views as fv
from planning_factory.domain.validation import ValidationError


class CompleteViewTests(unittest.TestCase):
    def setUp(self):self.views=fv.contract()['views']

    def row(self,view,tipo,**values):
        fields=['TipoRecord',*view['columns']]
        return fields,[tipo if k=='TipoRecord' else values.get(k) for k in fields]

    def test_contract_covers_the_previous_47_datasets_in_eight_views(self):
        datasets=[d for v in self.views for d in v['datasets']]
        self.assertEqual(len(self.views),8)
        self.assertEqual(len(datasets),47)
        self.assertEqual(len({d['name'] for d in datasets}),47)
        self.assertEqual(sum(d['raw'] for d in datasets),34)
        self.assertEqual(sum(d['csv'] is not None for d in datasets),13)
        repo=Path(__file__).resolve().parents[1]
        for view in self.views:
            sql=(repo/'integrations/cliente-export/views'/view['sql']).read_text(encoding='utf-8')
            self.assertIn('CREATE OR ALTER VIEW '+view['default'],sql)
            self.assertIn('COME LEGGERLA E MODIFICARLA',sql)
            self.assertNotIn('X_CLIENTE_EXPORT_',sql)
            self.assertNotIn('#U_',sql)

    def test_typed_record_keeps_codes_zero_and_missing_distinct(self):
        view=next(v for v in self.views if v['key']=='risorse')
        cols,department=self.row(view,'Reparti',code='12',name='Reparto')
        _,machine=self.row(view,'Macchine',code='0012',name='Macchina',statusCode='0',priority=0,maxUtilizationPct=0)
        _,pallet=self.row(view,'Pallet',code='12',name='Pallet numerico')
        split=fv.split_records(view,cols,[department,machine,pallet])
        self.assertEqual(split['departments'][0]['code'],'12')
        self.assertEqual(split['pallets'][0]['code'],12)
        self.assertEqual(split['machines'][0]['code'],'0012')
        self.assertEqual(split['machines'][0]['statusCode'],'0')
        self.assertEqual(split['machines'][0]['priority'],0)
        self.assertEqual(split['machines'][0]['maxUtilizationPct'],0)
        self.assertIsNone(split['machines'][0]['calendarId'])

    def test_unknown_type_missing_columns_and_nonpertinent_values_fail(self):
        view=self.views[0]
        cols,row=self.row(view,'Modelli',modelId=1)
        with self.assertRaises(ValidationError):fv.split_records(view,cols[:-1],[row[:-1]])
        with self.assertRaises(ValidationError):fv.split_records(view,cols,[['Sconosciuto',*row[1:]]])
        cols,row=self.row(view,'Modelli',modelId=1,operatorCode='invalid grain')
        with self.assertRaises(ValidationError):fv.split_records(view,cols,[row])

    def test_only_the_eight_configured_views_are_queried(self):
        calls=[];views={fv.quoted_object(v['default']):v for v in self.views}
        class Cursor:
            def execute(self,query):
                calls.append(query);view=views[query.removeprefix('SELECT * FROM ')]
                self.description=[(c,) for c in ['TipoRecord',*view['columns']]]
            def fetchall(self):return []
            def close(self):pass
        connection=SimpleNamespace(cursor=lambda:Cursor(),close=lambda:None,timeout=0)
        config=dict(views=fv.defaults(),server='TEST',database='Factory_Test')
        exporter=SimpleNamespace(derive_precedences=lambda *args:[])
        with tempfile.TemporaryDirectory() as folder,patch.object(fv,'connect',return_value=connection):
            fetched,coverage=fv.extract(config,folder,'2026-10-26',exporter)
            self.assertEqual(len(fetched),8)
            self.assertEqual(set(coverage),set(fv.defaults()))
            scenario=json.loads((Path(folder)/'raw/scenario.json').read_text())
            self.assertIn('workplans',scenario)
            self.assertIn('workplanQuantities',scenario)
            self.assertTrue((Path(folder)/'extract/11_nesting_membri/1.csv').is_file())
        self.assertEqual(calls,['SELECT * FROM '+fv.quoted_object(v['default']) for v in self.views])
