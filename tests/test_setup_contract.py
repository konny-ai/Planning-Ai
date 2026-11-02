"""ADR 003: setup duration is per phase; resources and percentages are explicit."""
import json
import unittest

from planning_factory.domain.setup_contract import (
    load_setup_contract, resolve_setup_states,
)
from planning_factory.domain.validation import ValidationError


def payload():
    return {
        'schema_version': 1,
        'resources': [
            {'id': 'M1', 'kind': 'machine'},
            {'id': 'M2', 'kind': 'machine'},
            {'id': 'OP1', 'kind': 'operator'},
            {'id': 'OP2', 'kind': 'operator'},
            {'id': 'TOOL1', 'kind': 'tool'},
        ],
        'initial_machine_states': {'M1': 'RED', 'M2': None},
        'phases': [
            {'id': 'A', 'machine_id': 'M1', 'target_state': 'BLUE',
             'setup_seconds': 1800,
             'setup_requirements': [
                 {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
                  'candidate_ids': ['M1']},
                 {'kind': 'operator', 'count': 2, 'occupancy_bps': 5000,
                  'candidate_ids': ['OP1', 'OP2']},
                 {'kind': 'tool', 'count': 1, 'occupancy_bps': 10000,
                  'candidate_ids': ['TOOL1']},
             ],
             'run_requirements': [
                 {'kind': 'machine', 'count': 1, 'occupancy_bps': 7500,
                  'candidate_ids': ['M1']},
                 {'kind': 'operator', 'count': 1, 'occupancy_bps': 2500,
                  'candidate_ids': ['OP1']},
             ]},
            {'id': 'B', 'machine_id': 'M1', 'target_state': 'BLUE',
             'setup_seconds': 1200,
             'setup_requirements': [
                 {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
                  'candidate_ids': ['M1']},
             ],
             'run_requirements': [
                 {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
                  'candidate_ids': ['M1']},
             ]},
            {'id': 'C', 'machine_id': 'M1', 'target_state': 'RED',
             'setup_seconds': 600, 'setup_locked': True,
             'setup_requirements': [
                 {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
                  'candidate_ids': ['M1']},
             ],
             'run_requirements': [
                 {'kind': 'machine', 'count': 1, 'occupancy_bps': 10000,
                  'candidate_ids': ['M1']},
             ]},
        ],
    }


class SetupContractTests(unittest.TestCase):
    def test_bundles_distinct_and_integer_fraction(self):
        p = load_setup_contract(json.dumps(payload())).phases[0]
        self.assertEqual(len(p.setup_requirements), 3)
        self.assertEqual(len(p.run_requirements), 2)
        self.assertEqual(p.setup_requirements[1].count, 2)
        self.assertEqual(p.setup_requirements[1].occupancy_bps, 5000)
        self.assertEqual(p.run_requirements[0].occupancy_bps, 7500)

    def test_same_known_state_skips_phase_duration_but_new_state_requires_it(self):
        c = load_setup_contract(json.dumps(payload()))
        result = resolve_setup_states(c, ('A', 'B', 'C', 'A'))
        self.assertEqual([r.setup_seconds for r in result], [1800, 0, 600, 1800])
        self.assertFalse(result[1].requires_setup)
        self.assertTrue(result[2].setup_locked)
        self.assertEqual(result[2].state_before, 'BLUE')

    def test_unknown_state_never_assumed_ready(self):
        p = payload()
        p['phases'][0]['machine_id'] = 'M2'
        for req in p['phases'][0]['run_requirements']:
            if req['kind'] == 'machine':
                req['candidate_ids'] = ['M2']
        for req in p['phases'][0]['setup_requirements']:
            if req['kind'] == 'machine':
                req['candidate_ids'] = ['M2']
        contract = load_setup_contract(json.dumps(p))
        self.assertTrue(resolve_setup_states(contract, ('A',))[0].requires_setup)

    def test_requires_explicit_machine_run_candidate(self):
        p = payload()
        p['phases'][0]['run_requirements'][0]['candidate_ids'] = ['M2']
        with self.assertRaisesRegex(ValidationError, 'run demands'):
            load_setup_contract(json.dumps(p))

    def test_invalid_percent_count_and_resource_fail_closed(self):
        for key, invalid in [('occupancy_bps', 0), ('occupancy_bps', 10001),
                             ('occupancy_bps', True), ('occupancy_bps', 50.5),
                             ('count', 3), ('count', False)]:
            p = payload()
            p['phases'][0]['setup_requirements'][1][key] = invalid
            with self.subTest(key=key, invalid=invalid), self.assertRaises(ValidationError):
                load_setup_contract(json.dumps(p))
        p = payload()
        p['phases'][0]['setup_requirements'][1]['candidate_ids'] = ['OP1', 'M2']
        with self.assertRaises(ValidationError):
            load_setup_contract(json.dumps(p))

    def test_unknown_machine_state_is_not_inferred_from_phase_name(self):
        p = payload()
        p['phases'][1]['target_state'] = None
        c = load_setup_contract(json.dumps(p))
        report = resolve_setup_states(c, ('A', 'B', 'B'))
        self.assertEqual([r.setup_seconds for r in report], [1800, 1200, 1200])

    def test_missing_setup_resources_or_duplicate_json_keys_rejected(self):
        p = payload()
        p['phases'][0]['setup_requirements'] = []
        with self.assertRaisesRegex(ValidationError, 'positive setup'):
            load_setup_contract(json.dumps(p))
        with self.assertRaisesRegex(ValidationError, 'duplicate JSON key'):
            load_setup_contract('{"schema_version":1,"schema_version":1}')
        with self.assertRaisesRegex(ValidationError, 'schema_version'):
            p = payload()
            p['schema_version'] = True
            load_setup_contract(json.dumps(p))


if __name__ == '__main__':
    unittest.main()
