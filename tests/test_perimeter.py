"""#78 P15: typed perimeter filters on canonical orders, for any source."""
import unittest

from planning_factory.canonical.builder import build_scenario
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from tests.test_canonical import ALL, NOW, WORKSHOP, batches_from, binding, codes


class PerimeterTests(unittest.TestCase):
    def setUp(self):
        self.bundle = scenario_to_bundle(WORKSHOP)
        records = self.bundle['records']
        for order in records['order']:
            order['customer_id'] = {'A': 'CUS-1', 'B': 'CUS-2', 'C': 'CUS-3'}[order['id']]
            order['due'] = {'A': '2026-10-22T15:00:00Z', 'B': '2026-11-05T15:00:00Z', 'C': None}[order['id']]

    def build(self, perimeter):
        b = binding(['erp'], {d: {'sources': ['erp']} for d in ALL}, perimeter=perimeter)
        return build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)

    def orders(self, result):
        return sorted({o.order_id for o in result.scenario.operations})

    def test_customer_filter_removes_every_record_of_excluded_orders(self):
        result = self.build({'customer_ids': ['CUS-1', 'CUS-3']})
        self.assertEqual(['A', 'C'], self.orders(result))
        self.assertEqual([('warn', 'perimeter_partial_nest')], codes(result))  # NEST-001 also cuts a part of B
        self.assertEqual((('ord-a/taglio',),), tuple(n.operation_ids for n in result.scenario.nestings))
        self.assertEqual(['B'], result.manifest['perimeter']['orders_excluded'])
        self.assertFalse(verify_plan(result.scenario, plan(result.scenario)))

    def test_due_window_and_orders_without_due(self):
        window = {'due_from': '2026-10-01T00:00:00+02:00', 'due_to': '2026-11-01T00:00:00+01:00'}
        self.assertEqual(['A', 'C'], self.orders(self.build(window)))
        self.assertEqual(['A'], self.orders(self.build(dict(window, include_without_due=False))))

    def test_order_lists_and_exclusions(self):
        self.assertEqual(['B', 'C'], self.orders(self.build({'exclude_order_ids': ['A']})))
        self.assertEqual(['C'], self.orders(self.build({'order_ids': ['B', 'C'], 'exclude_customer_ids': ['CUS-2']})))

    def test_cut_dependency_blocks_publication(self):
        self.bundle['records']['operation_dependency'].append(
            {'operation_id': 'ord-c/urgente', 'predecessor_id': 'ord-a/saldatura', 'kind': 'FS', 'lag_seconds': 0})
        self.bundle['records']['operation_state'] = [s for s in self.bundle['records']['operation_state']
                                                     if s['operation_id'] != 'ord-c/urgente']
        result = self.build({'exclude_order_ids': ['A']})
        self.assertIsNotNone(result.scenario)
        self.assertFalse(result.publishable)
        issue = next(i for i in result.issues if i.code == 'perimeter_cut_dependency')
        self.assertEqual(('block', 'ord-c/urgente'), (issue.severity, issue.key))

    def test_no_perimeter_changes_nothing(self):
        self.assertNotIn('perimeter', self.build(None).manifest)

    def test_binding_validation(self):
        for perimeter, text in (({'sites': ['VR']}, 'unknown fields'), ({'customer_ids': 'CUS-1'}, 'list of ids'),
                                ({'due_from': 'domani'}, 'ISO 8601'), ({'include_without_due': 'no'}, 'boolean')):
            with self.assertRaises(ValidationError) as ctx:
                binding(['erp'], {d: {'sources': ['erp']} for d in ALL}, perimeter=perimeter)
            self.assertIn(text, str(ctx.exception))


if __name__ == '__main__':
    unittest.main()
