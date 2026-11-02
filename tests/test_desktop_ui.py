"""Headless tests for Tk workbench controller: no display dependency."""
from dataclasses import replace
import unittest
from planning_factory.adapters.desktop_ui import evaluate, evaluate_setup, evaluate_setup_search, require_current_plan, export
from planning_factory.adapters.json_io import dumps, plan_loads
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op, scenario


class DesktopControllerTests(unittest.TestCase):
    def setUp(self):
        self.scenario = scenario([op('normal', hours=3, due=dt(22, 17),
                                     priority=-5, order_id='normal'),
                                  op('urgent', hours=2, due=dt(19, 11),
                                     order_id='express')])
        self.source = dumps(self.scenario)

    def test_validate_without_planning(self):
        s, p, info = evaluate(self.source, 'validate')
        self.assertIsNone(p)
        self.assertTrue(info['valid'])
        self.assertEqual(s, self.scenario)

    def test_plan_and_export_offline(self):
        s, p, info = evaluate(self.source, 'schedule')
        self.assertEqual(verify_plan(s, p), ())
        self.assertEqual(info['assigned'], 2)
        self.assertEqual(plan_loads(export(self.source, s, p, 'json')), p)
        self.assertIn('id="task-filter"', export(self.source, s, p, 'html'))
        self.assertIn('resource_id', export(self.source, s, p, 'csv'))
        with self.assertRaisesRegex(ValidationError, 'format'):
            export(self.source, s, p, 'wrong')

    def test_optimize_and_local_search(self):
        _, baseline, _ = evaluate(self.source, 'schedule')
        s, optimized, opt = evaluate(self.source, 'optimize')
        self.assertEqual(opt['winner_policy'], 'earliest_due')
        self.assertEqual(verify_plan(s, optimized), ())
        s, local, details = evaluate(self.source, 'local-search', budget=2)
        self.assertTrue(details['improved'])
        self.assertEqual(verify_plan(s, local), ())
        self.assertNotEqual(dumps(baseline), dumps(optimized))

    def test_prevent_export_after_edit(self):
        s, p, _ = evaluate(self.source, 'schedule')
        changed = dumps(replace(s, operations=(
            replace(s.operations[0], duration_seconds=3600), s.operations[1])))
        with self.assertRaisesRegex(ValidationError, 'scenario changed'):
            export(changed, s, p, 'json')
        with self.assertRaisesRegex(ValidationError, 'calculate'):
            export(self.source, s, None, 'json')

    def test_invalid_json_budget_and_size(self):
        with self.assertRaises(ValidationError):
            evaluate('{}', 'schedule')
        with self.assertRaisesRegex(ValidationError, 'unknown desktop action'):
            evaluate(self.source, 'delete')
        with self.assertRaisesRegex(ValueError, 'budget'):
            evaluate(self.source, 'local-search', budget=1000)
        with self.assertRaisesRegex(ValidationError, '8 MiB'):
            evaluate(' ' * (8 * 1024 * 1024 + 1), 'validate')

    def test_setup_aware_desktop_controller(self):
        rules = {'schema_version': 1, 'families': {'normal': 'A', 'urgent': 'B'},
                 'machines': [{'resource_id': 'm', 'initial_family': 'A',
                               'transitions': [{'from_family': 'A', 'to_family': 'B', 'seconds': 1800},
                                             {'from_family': 'B', 'to_family': 'A', 'seconds': 1800}]}]}
        adjusted, result, details = evaluate_setup(self.source, __import__('json').dumps(rules), budget=8)
        self.assertTrue(details['setup_valid'])
        self.assertEqual(verify_plan(adjusted, result), ())
        self.assertEqual(result.scenario_digest, __import__('planning_factory.adapters.json_io', fromlist=['scenario_digest']).scenario_digest(adjusted))
        self.assertEqual(details['scenario_digest'], __import__('planning_factory.adapters.json_io', fromlist=['scenario_digest']).scenario_digest(adjusted))

    def test_setup_alternative_search_desktop_controller(self):
        rules = {'schema_version':1,'families':{'normal':'A','urgent':'B'},
                 'machines':[{'resource_id':'m','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':1800}]}]}
        adjusted, result, details = evaluate_setup_search(self.source, __import__('json').dumps(rules), budget=8, repair_budget=8)
        self.assertTrue(details['setup_valid'])
        self.assertEqual(verify_plan(adjusted, result), ())
        self.assertLessEqual(details['candidates_evaluated'], 8)
