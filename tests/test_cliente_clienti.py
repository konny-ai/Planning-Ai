"""CLIENTE customers: order.customer_id from the Factory extraction, nothing made up (synthetic data, no database)."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

PATH = Path(__file__).resolve().parents[1] / 'integrations' / 'cliente-export' / 'build_clienti.py'
spec = importlib.util.spec_from_file_location('build_clienti', PATH)
clienti = importlib.util.module_from_spec(spec)
spec.loader.exec_module(clienti)


class ClienteCustomerTests(unittest.TestCase):
    def test_customer_code_becomes_canonical_customer_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            bundle = {'records': {'order': [{'id': '0'}, {'id': '100'}, {'id': '700', 'customer_id': 'stale'}],
                                  'operation': [{'id': '0/1/1', 'order_id': '0'}]}}
            (folder / 'bundle.json').write_text(json.dumps(bundle), encoding='utf-8')
            scenario = {'orders': [{'orderId': 0, 'customerCode': 'K1'}, {'orderId': 100, 'customerCode': ' K2 '},
                                   {'orderId': 700, 'customerCode': '  '}]}
            (folder / 'scenario.json').write_text(json.dumps(scenario), encoding='utf-8')
            with patch('builtins.print'):
                missing = clienti.main(str(folder / 'bundle.json'), str(folder / 'scenario.json'))
            result = json.loads((folder / 'bundle.json').read_text(encoding='utf-8'))
        self.assertEqual([{'id': '0', 'customer_id': 'K1'}, {'id': '100', 'customer_id': 'K2'}, {'id': '700'}],
                         result['records']['order'])
        self.assertEqual(['700'], missing)
        self.assertEqual(bundle['records']['operation'], result['records']['operation'])


if __name__ == '__main__':
    unittest.main()
