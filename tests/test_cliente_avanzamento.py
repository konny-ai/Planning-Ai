"""CLIENTE TRD progress: plan perimeter, time/quantity policies and diagnostics (synthetic data, no database)."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'integrations' / 'cliente-export' / 'build_avanzamento_trd.py'
spec = importlib.util.spec_from_file_location('build_avanzamento_trd', PATH)
trd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trd)

FIELDS = (*trd.CANONICAL, 'planned_quantity', 'last_activity_local')


def operation(key, **extra):
    order, lot, phase = key.split('/')
    return {'orderId': int(order), 'lotId': int(lot), 'phaseId': int(phase), 'phaseTypeId': 10, 'toPlan': 'Y',
            'theoreticalTotalSec': 3600, 'executionCode': 'I', 'qty': 10.0, **extra}


def progress(key, produced, work='100', planned='10', end='2026-04-30 12:00:00.000000'):
    return {'operation_id': key, 'status': 'started', 'completed_at': '', 'produced_quantity': produced,
            'recorded_work_seconds': work, 'recorded_setup_seconds': '0', 'resource_id': '',
            'planned_quantity': planned, 'last_activity_local': end}


class ClienteProgressTests(unittest.TestCase):
    def run_policy(self, policy):
        folder = Path(self.tmp.name)
        bundle = {'records': {'operation': [{'id': k, 'duration_seconds': 3600} for k in ('1/1/1', '1/1/2', '1/1/3')],
                              'nest_member': [{'nest_id': 'NEST-1', 'operation_id': '1/1/3'}]}}
        (folder / 'bundle.json').write_text(json.dumps(bundle), encoding='utf-8')
        scenario = {'operations': [operation('1/1/1'), operation('1/1/2'), operation('1/1/3'),
                                   operation('1/1/9', phaseTypeId=66)]}
        (folder / 'scenario.json').write_text(json.dumps(scenario), encoding='utf-8')
        with (folder / 'trd.csv').open('w', encoding='utf-8', newline='') as f:
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerows([progress('1/1/1', '4'), progress('1/1/2', '10'), progress('1/1/3', '10'),
                         progress('1/1/9', '1')])
        with patch('builtins.print'):
            trd.main(str(folder / 'bundle.json'), str(folder / 'scenario.json'), str(folder / 'trd.csv'),
                     str(folder / 'out'), policy)
        def read(name):
            with (folder / 'out' / name).open(encoding='utf-8', newline='') as stream:
                return list(csv.DictReader(stream))
        return read('operation_progress.csv'), read('avanzamento_diagnostica.csv'), \
            json.loads((folder / 'bundle.json').read_text(encoding='utf-8'))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_time_policy_keeps_plan_perimeter_and_reports_the_rest(self):
        kept, notes, bundle = self.run_policy('time')
        self.assertEqual([r['operation_id'] for r in kept], ['1/1/1', '1/1/2', '1/1/3'])
        self.assertTrue(all(r['status'] == 'started' for r in kept))
        self.assertEqual([(n['operation_id'], n['motivo']) for n in notes],
                         [('1/1/9', 'fuori perimetro: tipo fase 66 (Pulizia)')])
        self.assertNotIn('quantity', bundle['records']['operation'][0])

    def test_quantity_policy_closes_finished_phases_but_not_open_nest_members(self):
        kept, notes, bundle = self.run_policy('quantity')
        state = {r['operation_id']: (r['status'], r['completed_at']) for r in kept}
        self.assertEqual(state['1/1/1'], ('started', ''))
        self.assertEqual(state['1/1/2'], ('completed', '2026-04-30T12:00:00+02:00'))   # last TRD activity, Rome
        self.assertNotIn('1/1/3', state)                                               # stays with its nesting
        self.assertEqual({r['quantity'] for r in bundle['records']['operation']}, {'10'})
        reasons = {n['operation_id']: n['motivo'] for n in notes}
        self.assertIn('da chiudere in Factory', reasons['1/1/2'])
        self.assertIn('nesting aperto', reasons['1/1/3'])
