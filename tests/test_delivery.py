"""#78 P3 delivery buffer with promised dates kept, and P12 bonus for orders already started."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from planning_factory.adapters.json_io import dumps
from planning_factory.canonical.builder import build_scenario
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.canonical.delivery import service_levels, with_buffer
from planning_factory.cli import main
from planning_factory.engine.scheduler import plan
from tests.test_canonical import ALL, NOW, WORKSHOP, batches_from, binding, codes

CONFIRMED = {'confirmed_by': 'planner', 'confirmed_at': '2026-10-18T09:00:00Z'}


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.bundle = scenario_to_bundle(WORKSHOP)
        records = self.bundle['records']
        self.profile = records['profile'][0]
        for order in records['order']:
            order['customer_id'] = {'A': 'CUS-1', 'B': 'CUS-2', 'C': 'CUS-3'}[order['id']]

    def build(self):
        b = binding(['erp'], {d: {'sources': ['erp']} for d in ALL})
        return build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)

    def op(self, result, op_id):
        return next(o for o in result.scenario.operations if o.id == op_id)

    def test_options_off_change_nothing(self):
        result = self.build()
        self.assertEqual([], codes(result))
        self.assertNotIn('delivery', result.manifest)

    def test_buffer_plans_against_an_internal_due_and_keeps_the_promise(self):
        self.profile['delivery_buffer_seconds'] = 86400
        result = self.build()
        self.assertEqual(datetime(2026, 10, 21, 15, tzinfo=UTC), self.op(result, 'ord-b/piega').due)
        delivery = result.manifest['delivery']
        self.assertEqual('2026-10-22T15:00:00Z', delivery['promised_due']['ord-b/piega'])
        self.assertNotIn('ord-a/materiale', delivery['promised_due'])  # history is not re-dated
        self.assertEqual(WORKSHOP.settings, result.scenario.settings)
        levels = service_levels(plan(result.scenario), {o.id: o.order_id for o in result.scenario.operations},
                                delivery['promised_due'])
        row = next(r for r in levels['orders'] if r['order_id'] == 'B')
        self.assertEqual(('2026-10-21T15:00:00Z', '2026-10-22T15:00:00Z'), (row['internal_due'], row['promised_due']))
        self.assertTrue(row['on_time_promised'])

    def test_rule_overrides_the_buffer_for_one_customer(self):
        self.profile['delivery_buffer_seconds'] = 86400
        self.bundle['records']['rule'] = [{'id': 'R-B', 'action': 'set_delivery_buffer_seconds', 'value': 0,
                                           'customer_id': 'CUS-2', **CONFIRMED}]
        result = self.build()
        self.assertEqual(datetime(2026, 10, 22, 15, tzinfo=UTC), self.op(result, 'ord-b/piega').due)
        self.assertEqual(datetime(2026, 10, 21, 15, tzinfo=UTC), self.op(result, 'ord-a/piega').due)
        self.assertEqual(0, result.manifest['delivery']['buffer_overrides']['ord-b/piega'])

    def test_what_if_buffer_replaces_the_default_and_keeps_rule_overrides(self):
        self.profile['delivery_buffer_seconds'] = 86400
        self.bundle['records']['rule'] = [{'id': 'R-B', 'action': 'set_delivery_buffer_seconds', 'value': 0,
                                           'customer_id': 'CUS-2', **CONFIRMED}]
        result = self.build()
        delivery = result.manifest['delivery']
        three = SimpleNamespace(scenario=with_buffer(result.scenario, delivery, 3 * 86400))
        none = SimpleNamespace(scenario=with_buffer(result.scenario, delivery, 0))
        self.assertEqual(datetime(2026, 10, 19, 15, tzinfo=UTC), self.op(three, 'ord-a/piega').due)  # promised - 3 days
        self.assertEqual(datetime(2026, 10, 22, 15, tzinfo=UTC), self.op(three, 'ord-b/piega').due)  # its own rule: 0
        self.assertEqual(self.op(result, 'ord-a/materiale'), self.op(three, 'ord-a/materiale'))  # history stays
        self.assertEqual(datetime(2026, 10, 22, 15, tzinfo=UTC), self.op(none, 'ord-a/piega').due)

    def test_orders_already_started_get_the_bonus(self):
        self.profile['wip_priority_bonus'] = 5
        result = self.build()
        boosted = result.manifest['delivery']['wip_boosted_operations']
        self.assertIn('ord-a/piega', boosted)  # order A has a completed phase
        self.assertNotIn('ord-b/piega', boosted)
        self.assertEqual((-5, 0), (self.op(result, 'ord-a/piega').priority, self.op(result, 'ord-b/piega').priority))

    def test_cli_service_levels(self):
        self.profile['delivery_buffer_seconds'] = 86400
        result = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            paths = [Path(tmp, n) for n in ('s.json', 'p.json', 'm.json')]
            paths[0].write_text(dumps(result.scenario), encoding='utf-8')
            paths[1].write_text(dumps(plan(result.scenario)), encoding='utf-8')
            paths[2].write_text(dumps(result.manifest), encoding='utf-8')
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(0, main(['service-levels', *map(str, paths)]))
            self.assertEqual(3, json.loads(out.getvalue())['orders_with_due'])


if __name__ == '__main__':
    unittest.main()
