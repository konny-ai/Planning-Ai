from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from planning_factory.adapters.rule_memory import RuleMemory
from planning_factory.adapters.store import ConflictError
from planning_factory.domain.validation import ValidationError
from .helpers import op, scenario


class RuleMemoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.memory = RuleMemory(Path(self.tmp.name) / 'memory.db')
        self.source = scenario((op('a'),))
        self.values = {'action': 'add_setup_seconds', 'value': 600, 'order_id': self.source.operations[0].order_id}

    def test_draft_preview_confirmation_idempotence_and_revocation(self):
        draft = self.memory.create_draft(self.values, 'planner')
        self.assertEqual(self.source, self.memory.apply(self.source)[0])
        preview, _ = self.memory.apply(self.source, preview_rule=draft['id'])
        self.assertEqual(600, preview.operations[0].setup_seconds)
        self.assertEqual('draft', self.memory.list()[0]['status'])
        confirmed = self.memory.decide(draft['id'], 1, 'confirm', 'planner')
        first, evidence = self.memory.apply(self.source)
        self.assertEqual(first, self.memory.apply(self.source)[0])
        self.assertEqual(600, first.operations[0].setup_seconds)
        self.assertEqual('planner', evidence['applied'][0]['confirmed_by'])
        with self.assertRaises(ConflictError):
            self.memory.decide(draft['id'], 1, 'confirm', 'other')
        self.memory.decide(draft['id'], confirmed['version'], 'revoke', 'planner')
        self.assertEqual(self.source, self.memory.apply(self.source)[0])
        self.assertEqual(3, self.memory.list()[0]['version'])

    def test_unrecognized_and_preconfirmed_rules_rejected(self):
        for edits in ({'action': 'execute_sql'}, {'value': True}, {'confirmed_by': 'forged'}, {'action': []}):
            with self.assertRaises(ValidationError):
                self.memory.create_draft({**self.values, **edits}, 'planner')

    def test_conflicting_confirmed_priorities_fail_closed(self):
        for value in (1, 2):
            draft = self.memory.create_draft({**self.values, 'action': 'set_priority', 'value': value}, 'planner')
            self.memory.decide(draft['id'], 1, 'confirm', 'planner')
        with self.assertRaises(ValidationError):
            self.memory.apply(self.source)

    def test_rule_cache_key_changes_at_validity_boundaries(self):
        draft = self.memory.create_draft({**self.values, 'valid_from': '2026-10-23T12:00:00Z',
                                         'valid_to': '2026-10-23T13:00:00Z'}, 'planner')
        self.memory.decide(draft['id'], 1, 'confirm', 'planner')
        before = self.memory.cache_key('2026-10-23T11:59:59Z')
        active = self.memory.cache_key('2026-10-23T12:00:00Z')
        after = self.memory.cache_key('2026-10-23T13:00:00Z')
        self.assertNotEqual(before, active)
        self.assertNotEqual(active, after)

    def test_selected_canonical_scope_is_preserved_and_combined_without_guessing(self):
        from planning_factory.canonical.decompose import scenario_to_bundle
        from planning_factory.canonical.builder import build_scenario
        from tests.test_canonical import ALL, NOW, binding, batches_from
        source = scenario((op('a', order_id='ORDER-A'), op('b', order_id='ORDER-B')))
        bundle = scenario_to_bundle(source)
        for row in bundle['records']['order']:
            row['customer_id'] = 'CUS-A' if row['id'] == 'ORDER-A' else 'CUS-B'
        for row in bundle['records']['operation']:
            row.update(material='S275', thickness_mm='40' if row['id'] == 'a' else '20')
        selected = binding(['erp'], {d: {'sources': ['erp']} for d in ALL})
        built = build_scenario(selected, batches_from({'erp': bundle}, selected), NOW)
        self.assertIsNotNone(built.scenario)
        memory = RuleMemory(Path(self.tmp.name) / 'scoped.db', source=built.scenario,
                            scope_context=built.manifest['rule_scope'])
        draft = memory.create_draft({'action': 'add_setup_seconds', 'value': 600,
                                     'customer_id': 'CUS-A', 'resource_id': 'm', 'material': 'S275',
                                     'min_thickness_mm': '39.9', 'max_thickness_mm': '40'}, 'planner')
        preview, evidence = memory.apply(built.scenario, preview_rule=draft['id'])
        self.assertEqual([600, 0], [o.setup_seconds for o in preview.operations])
        self.assertEqual(['a'], evidence['applied'][0]['operations'])
        self.assertEqual(built.scenario, memory.apply(built.scenario)[0])
        memory.decide(draft['id'], 1, 'confirm', 'planner')
        self.assertEqual(preview, memory.apply(built.scenario)[0])
        changed = replace(built.scenario, settings=replace(built.scenario.settings, mode='backward'))
        with self.assertRaisesRegex(ValidationError, 'another scenario revision'):
            memory.apply(changed)
        for values in ({'material': 'INVENTED'}, {'customer_id': 'INVENTED'},
                       {'order_id': 'INVENTED'}, {'resource_id': 'INVENTED'},
                       {'min_thickness_mm': '40', 'max_thickness_mm': '40'}):
            with self.assertRaises(ValidationError):
                memory.create_draft({'action': 'set_priority', 'value': 1, **values}, 'planner')

    def test_missing_attributes_and_unscoped_global_rules_are_rejected(self):
        for scope in ({}, {'material': 'S275'}, {'customer_id': 'CUS-X'}, {'min_thickness_mm': '40'}):
            with self.assertRaises(ValidationError):
                self.memory.create_draft({'action': 'set_priority', 'value': 1, **scope}, 'planner')

    def test_textual_note_is_versioned_context_that_never_changes_the_plan(self):
        draft = self.memory.create_draft({'action': 'note', 'note': '  Per questo ordine si salda prima di piegare ',
                                          'order_id': self.values['order_id']}, 'planner')
        self.assertEqual('Per questo ordine si salda prima di piegare', draft['rule']['note'])
        self.assertEqual([], self.memory.guidance())
        self.memory.decide(draft['id'], 1, 'confirm', 'planner')
        adjusted, evidence = self.memory.apply(self.source)
        self.assertEqual(self.source, adjusted)
        self.assertEqual([draft['id']], [n['id'] for n in evidence['notes']])
        self.assertFalse(evidence['material_constraint']['ignored'])
        guidance = self.memory.guidance()
        self.assertEqual(('note', 'planner', self.values['order_id']),
                         (guidance[0]['action'], guidance[0]['confirmed_by'], guidance[0]['order_id']))
        self.memory.decide(draft['id'], 2, 'revoke', 'planner')
        self.assertEqual([], self.memory.guidance())

    def test_textual_note_and_switch_reject_values_scopes_and_empty_text(self):
        for values in ({'action': 'note'}, {'action': 'note', 'note': '   '}, {'action': 'note', 'note': 'x', 'value': 1},
                       {'action': 'note', 'note': 'x', 'customer_id': 'INVENTED'}, {'action': 'note', 'note': 'x' * 2001},
                       {'action': 'note', 'note': 'x', 'confirmed_by': 'forged'},
                       {'action': 'note', 'note': 'x', 'valid_from': '2026-10-24T12:00:00Z', 'valid_to': '2026-10-24T11:00:00Z'},
                       {'action': 'ignore_material'}):  # this source has no materials domain to switch off
            with self.subTest(values=values), self.assertRaises(ValidationError):
                self.memory.create_draft(values, 'planner')


    def test_guidance_lists_every_confirmed_rule_in_its_window_with_the_sentence(self):
        draft = self.memory.create_draft({**self.values, 'action': 'set_priority', 'value': -1000,
                                          'valid_to': '2999-01-01T00:00:00Z', 'note': 'fino al 2999 priorità massima'}, 'planner')
        expired = self.memory.create_draft({**self.values, 'valid_to': '2000-01-01T00:00:00Z'}, 'planner')
        for rule in (draft, expired):
            self.memory.decide(rule['id'], 1, 'confirm', 'planner')
        self.assertEqual([(draft['id'], -1000, 'fino al 2999 priorità massima')],
                         [(g['id'], g['value'], g['note']) for g in self.memory.guidance()])
        self.assertEqual(-1000, self.memory.scope_catalog()['priority_levels']['massima'])


class MaterialSwitchTests(unittest.TestCase):
    """«Non considerare il materiale»: a confirmed company-memory switch plans the source without material releases."""

    def setUp(self):
        from planning_factory.adapters.json_io import scenario_digest
        from planning_factory.canonical.builder import build_scenario
        from planning_factory.canonical.decompose import scenario_to_bundle
        from tests.test_canonical import ALL, NOW, WORKSHOP, batches_from, binding
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        bundle = scenario_to_bundle(WORKSHOP)
        bundle['records'].update(
            material_supply=[{'id': 'MAG-1', 'item_id': 'SHEET', 'quantity': '2', 'available_at': '2026-10-19T00:00:00Z',
                              'kind': 'stock'}],
            material_need=[{'operation_id': 'ord-a/piega', 'item_id': 'SHEET', 'quantity': '2'},
                           {'operation_id': 'ord-b/piega', 'item_id': 'SHEET', 'quantity': '30'}])
        domains = {d: {'sources': ['erp']} for d in ALL}
        domains['materials'] = {'sources': ['erp'], 'allocation_order': 'due'}
        selected = binding(['erp'], domains)
        self.built = build_scenario(selected, batches_from({'erp': bundle}, selected), NOW)
        self.original = {o.id: o.release for o in WORKSHOP.operations}
        self.memory = RuleMemory(Path(self.tmp.name) / 'memory.db', source=self.built.scenario,
                                 scope_context=self.built.manifest['rule_scope'],
                                 material_free=(scenario_digest(self.built.scenario), self.built.material_free))

    def test_confirmed_switch_plans_without_material_and_revocation_restores_it(self):
        from planning_factory.engine.scheduler import plan
        from planning_factory.engine.verification import verify_plan
        source = self.built.scenario
        short = next(o for o in source.operations if o.id == 'ord-b/piega')
        self.assertEqual(source.end, short.release)  # the shortage pushes the unit out of the horizon
        self.assertTrue(any(u.task_id == 'job:ord-b/piega' for u in plan(source).unscheduled))
        draft = self.memory.create_draft({'action': 'ignore_material', 'note': 'ho tutto a magazzino'}, 'planner')
        self.assertEqual(source, self.memory.apply(source)[0])  # a draft never changes the plan
        self.memory.decide(draft['id'], 1, 'confirm', 'planner')
        adjusted, evidence = self.memory.apply(source)
        freed = next(o for o in adjusted.operations if o.id == 'ord-b/piega')
        self.assertEqual(self.original['ord-b/piega'], freed.release)
        self.assertEqual({'ignored': True, 'rules': [draft['id']], 'operations_released': 1}, evidence['material_constraint'])
        self.assertEqual(['ord-b/piega'], next(a for a in evidence['applied'] if a['rule'] == draft['id'])['operations'])
        result = plan(adjusted)
        self.assertFalse(verify_plan(adjusted, result))
        self.assertFalse(any(u.task_id == 'job:ord-b/piega' for u in result.unscheduled))
        self.assertEqual('ignore_material', self.memory.guidance()[0]['action'])
        self.memory.decide(draft['id'], 2, 'revoke', 'planner')
        self.assertEqual(source, self.memory.apply(source)[0])

    def test_switch_combines_with_numeric_rules_and_refuses_another_revision(self):
        from dataclasses import replace as change
        switch = self.memory.create_draft({'action': 'ignore_material'}, 'planner')
        self.memory.decide(switch['id'], 1, 'confirm', 'planner')
        setup = self.memory.create_draft({'action': 'add_setup_seconds', 'value': 60, 'order_id': 'B'}, 'planner')
        self.memory.decide(setup['id'], 1, 'confirm', 'planner')
        adjusted, _ = self.memory.apply(self.built.scenario)
        before = {o.id: o.setup_seconds for o in self.built.scenario.operations}
        self.assertEqual(before['ord-b/piega'] + 60, next(o for o in adjusted.operations if o.id == 'ord-b/piega').setup_seconds)
        self.assertEqual(self.original['ord-b/piega'], next(o for o in adjusted.operations if o.id == 'ord-b/piega').release)
        for scope in ({'order_id': 'B'}, {'value': 1}):
            with self.subTest(scope=scope), self.assertRaises(ValidationError):
                self.memory.create_draft({'action': 'ignore_material', **scope}, 'planner')
        with self.assertRaises(ValidationError):
            self.memory.apply(change(self.built.scenario, id='another-revision'))
