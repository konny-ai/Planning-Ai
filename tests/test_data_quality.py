"""#87: one data quality report for all sources, with explicit cross-system identity maps."""
import copy
import json
import unittest
from pathlib import Path

from planning_factory.adapters.json_io import dumps
from planning_factory.canonical.binding import parse_binding
from planning_factory.canonical.builder import build_scenario
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.domain.validation import ValidationError
from tests.test_canonical import ALL, NOW, WORKSHOP, batches_from, binding, codes

CAM_NEST = {'schema_version': 1, 'records': {
    'nest': [{'id': 'PL-77', 'duration_seconds': 10800, 'setup_seconds': 1800}],
    'nest_member': [{'nest_id': 'PL-77', 'operation_id': 'ALMA-1'}, {'nest_id': 'PL-77', 'operation_id': 'ALMA-2'}]}}
MAP = [{'map': 'alma-parti', 'external_id': 'ALMA-1', 'canonical_id': 'ord-a/taglio'},
       {'map': 'alma-parti', 'external_id': 'ALMA-2', 'canonical_id': 'ord-b/taglio'}]


def with_identity(cam_identity=True, **extra):
    data = {'schema_version': 1, 'installation': 'test',
            'scenario': {'id': 'officina-demo', 'start': '2026-10-19T00:00:00Z', 'end': '2026-10-26T00:00:00Z'},
            'sources': {'erp': {'connector': 'canonical-json'}, 'cam': {'connector': 'canonical-json'}},
            'domains': {d: {'sources': ['cam' if d == 'nesting' else 'erp']} for d in ALL}}
    data['domains']['identity'] = {'sources': ['erp']}
    if cam_identity:
        data['sources']['cam']['identity'] = {'operation': 'alma-parti'}
    data.update(extra)
    return parse_binding(data, Path('.'))


class IdentityMapTests(unittest.TestCase):
    def setUp(self):
        self.erp = scenario_to_bundle(WORKSHOP)
        for key in ('nest', 'nest_member'):
            self.erp['records'].pop(key)
        self.erp['records']['identity_map'] = copy.deepcopy(MAP)
        self.cam = copy.deepcopy(CAM_NEST)

    def test_cam_members_are_translated_to_erp_operations(self):
        b = with_identity()
        result = build_scenario(b, batches_from({'erp': self.erp, 'cam': self.cam}, b), NOW)
        self.assertEqual([], codes(result))
        self.assertEqual([('PL-77', ('ord-a/taglio', 'ord-b/taglio'))],
                         [(n.id, n.operation_ids) for n in result.scenario.nestings])
        self.assertEqual(2, result.manifest['domains']['identity']['records'])

    def test_unmatched_keys_are_reported_and_never_guessed(self):
        self.cam['records']['nest_member'].append({'nest_id': 'PL-77', 'operation_id': 'ALMA-9'})
        self.cam['records']['nest_member'].append({'nest_id': 'PL-77', 'operation_id': 'alma-1'})  # case differs: no match
        b = with_identity()
        result = build_scenario(b, batches_from({'erp': self.erp, 'cam': self.cam}, b), NOW)
        self.assertIsNotNone(result.scenario)
        self.assertFalse(result.publishable)
        found = [(i.severity, i.code, i.source, i.original_key) for i in result.issues]
        self.assertEqual([('block', 'unmatched_identity', 'cam', 'cam#nest_member/2'),
                          ('block', 'unmatched_identity', 'cam', 'cam#nest_member/3')], found)
        self.assertEqual(('ord-a/taglio', 'ord-b/taglio'), result.scenario.nestings[0].operation_ids)

    def test_without_the_map_the_foreign_keys_are_unknown_references(self):
        b = with_identity(cam_identity=False)
        result = build_scenario(b, batches_from({'erp': self.erp, 'cam': self.cam}, b), NOW)
        self.assertIsNone(result.scenario)
        self.assertIn(('error', 'unknown_reference'), codes(result))

    def test_binding_rules_for_identity(self):
        data = {'schema_version': 1, 'installation': 'x',
                'scenario': {'id': 's', 'start': '2026-10-19T00:00:00Z', 'end': '2026-10-26T00:00:00Z'},
                'sources': {'erp': {'connector': 'canonical-json', 'identity': {'operation': 'm'}}},
                'domains': {d: {'sources': ['erp']} for d in ('resources', 'calendars', 'orders', 'routings')}}
        with self.assertRaises(ValidationError) as ctx:
            parse_binding(data, Path('.'))
        self.assertIn('binding.domains.identity: required', str(ctx.exception))
        data['domains']['identity'] = {'sources': ['erp']}
        data['sources']['erp']['identity'] = {'machine': 'm'}
        with self.assertRaises(ValidationError) as ctx:
            parse_binding(data, Path('.'))
        self.assertIn('identity: object', str(ctx.exception))


class QualityCheckTests(unittest.TestCase):
    def setUp(self):
        self.bundle = scenario_to_bundle(WORKSHOP)
        self.ops = {o['id']: o for o in self.bundle['records']['operation']}

    def build(self, **extra):
        b = binding(['erp'], {d: {'sources': ['erp']} for d in ALL}, **extra)
        return build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)

    def issue(self, result, code):
        return next(i for i in result.issues if i.code == code)

    def test_open_operation_without_resources_or_time_blocks_the_build(self):
        records = self.bundle['records']
        records['operation_demand'] = [d for d in records['operation_demand'] if d['operation_id'] != 'ord-a/piega']
        self.ops['ord-b/piega'].update(duration_seconds=0, setup_seconds=0)
        result = self.build()
        self.assertIsNone(result.scenario)
        self.assertEqual({('error', 'no_resource', 'ord-a/piega', ('job:ord-a/piega',)),
                          ('error', 'zero_time', 'ord-b/piega', ('job:ord-b/piega',))},
                         {(i.severity, i.code, i.key, i.units) for i in result.issues})

    def test_dates_and_horizon_are_warnings_with_plan_units(self):
        self.ops['ord-a/piega'].update(due='2026-10-18T10:00:00Z')
        self.ops['ord-c/urgente'].update(release='2026-10-27T00:00:00Z', due='2026-10-26T12:00:00Z')
        self.bundle['records']['operation_state'] = [s for s in self.bundle['records']['operation_state']
                                                     if s['operation_id'] != 'ord-c/urgente']
        result = self.build()
        self.assertIsNotNone(result.scenario)
        self.assertTrue(result.publishable)
        self.assertEqual({('warn', 'due_in_past', 'ord-a/piega'),
                          ('warn', 'release_after_horizon', 'ord-c/urgente'), ('warn', 'due_before_release', 'ord-c/urgente')},
                         {(i.severity, i.code, i.key) for i in result.issues})
        self.assertEqual(('job:ord-a/piega',), self.issue(result, 'due_in_past').units)

    def test_cutting_operations_without_nesting_are_reported(self):
        records = self.bundle['records']
        records['nest'], records['nest_member'] = [], []
        result = self.build(quality={'nesting_required_resources': ['LASER-1', 'LASER-2', 'PRESSA-9']})
        found = {(i.code, i.key) for i in result.issues}
        self.assertEqual({('not_nested', 'ord-a/taglio'), ('not_nested', 'ord-b/taglio'), ('not_nested', 'ord-c/urgente'),
                          ('unknown_quality_resource', 'PRESSA-9')}, found)
        self.assertTrue(result.publishable)

    def test_nested_members_are_not_reported_and_nest_issues_carry_the_nest_unit(self):
        result = self.build(quality={'nesting_required_resources': ['LASER-1', 'LASER-2']})
        self.assertEqual([('warn', 'not_nested', 'ord-c/urgente')], [(i.severity, i.code, i.key) for i in result.issues])
        self.bundle['records']['nest'].append({'id': 'VUOTO', 'duration_seconds': 60, 'setup_seconds': 0})
        result = self.build()
        issue = self.issue(result, 'empty_nest')
        self.assertEqual(('error', 'VUOTO'), (issue.severity, issue.key))

    def test_report_is_stable_json_with_units(self):
        self.ops['ord-a/piega'].update(due='2026-10-18T10:00:00Z')
        first, second = self.build().report(), self.build().report()
        self.assertEqual(dumps(first), dumps(second))
        self.assertEqual({'severity': 'warn', 'code': 'due_in_past', 'domain': 'routings', 'entity': 'operation',
                          'key': 'ord-a/piega', 'units': ['job:ord-a/piega'],
                          'detail': 'due 2026-10-18T10:00:00Z before horizon start 2026-10-19T00:00:00Z'},
                         first['issues'][0])
        self.assertEqual({'error': 0, 'block': 0, 'warn': 1}, first['counts'])
        json.dumps(first)


if __name__ == '__main__':
    unittest.main()
