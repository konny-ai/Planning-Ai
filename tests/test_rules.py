"""#78 P20 / #93: confirmed scoped rules, applied deterministically and traced."""
import unittest

from planning_factory.canonical.builder import build_scenario
from planning_factory.canonical.decompose import scenario_to_bundle
from tests.test_canonical import ALL, NOW, WORKSHOP, batches_from, binding, codes

CONFIRMED = {'confirmed_by': 'planner', 'confirmed_at': '2026-10-18T09:00:00Z'}


class RuleTests(unittest.TestCase):
    def setUp(self):
        self.bundle = scenario_to_bundle(WORKSHOP)
        records = self.bundle['records']
        self.ops = {o['id']: o for o in records['operation']}
        self.ops['ord-a/saldatura'].update(material='S355', thickness_mm='50')
        self.ops['ord-a/piega'].update(material='S355', thickness_mm='30')
        for order in records['order']:
            order['customer_id'] = {'A': 'CUS-1', 'B': 'CUS-2', 'C': 'CUS-2'}[order['id']]
        records['rule'] = []

    def rule(self, **fields):
        self.bundle['records']['rule'].append(fields)

    def build(self, profile=None):
        b = binding(['erp'], {d: {'sources': ['erp']} for d in ALL}, **({'profile': profile} if profile else {}))
        return build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)

    def op(self, result, op_id):
        return next(o for o in result.scenario.operations if o.id == op_id)

    def test_extra_setup_above_a_thickness_on_one_machine(self):
        self.rule(id='R-12', action='add_setup_seconds', value=2700, resource_id='SALDATURA-1', min_thickness_mm='40',
                  note='spessori oltre 40 mm', **CONFIRMED)
        result = self.build()
        self.assertEqual([], codes(result))
        self.assertEqual(1800 + 2700, self.op(result, 'ord-a/saldatura').setup_seconds)
        self.assertEqual([{'rule': 'R-12', 'action': 'add_setup_seconds', 'value': 2700,
                           'operations': ['ord-a/saldatura'], 'confirmed_by': 'planner'}], result.manifest['rules']['applied'])

    def test_unconfirmed_rules_are_listed_and_ignored(self):
        self.rule(id='R-1', action='add_setup_seconds', value=600, resource_id='SALDATURA-1')
        result = self.build()
        self.assertEqual(1800, self.op(result, 'ord-a/saldatura').setup_seconds)
        self.assertEqual([('warn', 'rule_not_confirmed')], codes(result))
        self.assertEqual([{'rule': 'R-1', 'reason': 'not confirmed by a planner'}], result.manifest['rules']['skipped'])

    def test_customer_priority_until_a_date(self):
        self.rule(id='R-2', action='set_priority', value=-10, customer_id='CUS-2', valid_to='2026-10-31T00:00:00Z',
                  **CONFIRMED)
        result = self.build()
        self.assertEqual([-10, -10], [self.op(result, i).priority for i in ('ord-b/piega', 'ord-c/urgente')])
        self.assertEqual(0, self.op(result, 'ord-a/piega').priority)
        self.bundle['records']['rule'][0]['valid_to'] = '2026-10-19T00:00:00Z'
        expired = self.build()
        self.assertEqual(0, self.op(expired, 'ord-b/piega').priority)
        self.assertEqual('outside its validity window', expired.manifest['rules']['skipped'][0]['reason'])

    def test_conflicting_priorities_are_an_error(self):
        self.rule(id='R-3', action='set_priority', value=1, order_id='B', **CONFIRMED)
        self.rule(id='R-4', action='set_priority', value=2, customer_id='CUS-2', **CONFIRMED)
        result = self.build()
        self.assertIsNone(result.scenario)
        self.assertIn(('error', 'rule_conflict'), codes(result))

    def test_partial_scope_nests_and_empty_scope_are_reported(self):
        self.rule(id='R-5', action='set_priority', value=-1, resource_id='LASER-1', **CONFIRMED)  # taglio also runs on LASER-2
        self.rule(id='R-6', action='add_setup_seconds', value=60, resource_id='LASER-2', **CONFIRMED)  # only nest members
        self.rule(id='R-7', action='add_setup_seconds', value=60, material='HARDOX', **CONFIRMED)
        result = self.build()
        found = {(i.code, i.key) for i in result.issues}
        self.assertIn(('rule_not_applicable', 'ord-a/taglio'), found)
        self.assertIn(('rule_matches_nothing', 'R-7'), found)
        self.assertEqual(-1, self.op(result, 'ord-c/urgente').priority)  # runs on LASER-1 only
        self.assertEqual(0, self.op(result, 'ord-a/taglio').priority)

    def test_profile_scoped_rules(self):
        records = self.bundle['records']
        records['profile'].append(dict(records['profile'][0], id='urgenze'))
        self.rule(id='R-8', action='set_customer_priority', value=-5, customer_id='CUS-1', profile='urgenze', **CONFIRMED)
        self.assertEqual(0, self.op(self.build('default'), 'ord-a/piega').customer_priority)
        self.assertEqual(-5, self.op(self.build('urgenze'), 'ord-a/piega').customer_priority)


if __name__ == '__main__':
    unittest.main()
