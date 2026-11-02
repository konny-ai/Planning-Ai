"""Actual stock and source dates cannot be replaced with the former demo +7 days."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

from planning_factory.adapters.factory_workbook_pipeline import real_materials,load_tool


def write_csv(root,path,fields,rows):
    target=root/path;target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)


class ActualMaterialsTests(unittest.TestCase):
    def test_quantities_sum_slots_and_no_supply_is_invented_for_a_shortage(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);out=root/'out';out.mkdir()
            write_csv(root,'material-items.csv',('partCode','uom'),[
                dict(partCode='M',uom='NR'),dict(partCode='N',uom='KG')])
            write_csv(root,'purchase-residuals.csv',('partCode','purchaseOrderCode','rowNo','orderedQty','deliveredQty','uom'),[
                dict(partCode='M',purchaseOrderCode='PO1',rowNo=1,orderedQty=12,deliveredQty=5,uom='NR')])
            write_csv(root,'12_nesting_lamiere/1.csv',('workplanId','sheetSlot','sheetPartCode','sheetsRequired','sheetWeightKg'),[
                dict(workplanId=1,sheetSlot=1,sheetPartCode='M',sheetsRequired=2),
                dict(workplanId=1,sheetSlot=2,sheetPartCode='M',sheetsRequired=3),
                dict(workplanId=1,sheetSlot=3,sheetPartCode='N',sheetsRequired=2,sheetWeightKg=240)])
            write_csv(root,'13_materiali_giacenze_arrivi/1.csv',('sheetPartCode','stockAvailable','engaged'),[
                dict(sheetPartCode='M',stockAvailable=10,engaged=4),dict(sheetPartCode='N',stockAvailable=0,engaged=0)])
            write_csv(root,'13_materiali_giacenze_arrivi/2.csv',('partCode','promisedDate','qty','purchaseOrderCode','rowNo'),[
                dict(partCode='M',promisedDate='2026-10-28',qty=12,purchaseOrderCode='PO1',rowNo=1)])
            scenario=dict(start='2026-10-26T00:00:00+01:00',nestings=[dict(id='NEST-1',operation_ids=['a'])])
            with redirect_stdout(StringIO()):real_materials(scenario,root,out)
            with (out/'material_supply.csv').open() as f:supplies=list(csv.DictReader(f))
            with (out/'material_need.csv').open() as f:needs=list(csv.DictReader(f))
            self.assertEqual(len(supplies),2)
            self.assertEqual(supplies[0]['quantity'],'6')
            self.assertEqual(supplies[1]['quantity'],'7')
            self.assertEqual({r['item_id']:r['quantity'] for r in needs},{'M':'5','N':'480'})
            self.assertFalse(any('SIM' in r['id'] for r in supplies))

    def test_real_pipeline_can_disable_the_legacy_simulated_release(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            write_csv(root,'10_nesting_programmi/1.csv',('workplanId','minMemberDueDate','cutSec','setupSec'),[
                dict(workplanId=1,minMemberDueDate='2026-10-26',cutSec=600,setupSec=60)])
            write_csv(root,'11_nesting_membri/1.csv',('workplanId','orderId','lotId','phaseId'),[
                dict(workplanId=1,orderId=1,lotId=1,phaseId=1)])
            write_csv(root,'12_nesting_lamiere/1.csv',('workplanId','sheetPartCode'),[dict(workplanId=1,sheetPartCode='M')])
            write_csv(root,'13_materiali_giacenze_arrivi/1.csv',('sheetPartCode','stockAvailable'),[])
            write_csv(root,'13_materiali_giacenze_arrivi/2.csv',('partCode','promisedDate'),[])
            source=root/'source.json';target=root/'target.json'
            source.write_text(json.dumps(dict(id='demo',start='2026-10-26T00:00:00+01:00',operations=[
                dict(id='1/1/1',alternatives=[dict(demands=[dict(resource_id='m',units=1)])])])) )
            tool=load_tool(Path(__file__).resolve().parents[1]/'integrations/cliente-export/build_scenario_cliente.py','test_sync_builder')
            tool.OUT=str(root)
            with redirect_stdout(StringIO()):tool.nesting(source,target,apply_material_release=False)
            self.assertNotIn('release',json.loads(target.read_text())['operations'][0])
