"""Declared machine changeover times are auditable without modifying plans."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from planning_factory.adapters.json_io import dumps
from planning_factory.cli import main
from planning_factory.domain.models import Nesting, Resource
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.setup_audit import audit_setups, load_setup_rules
from .helpers import dt, op, scenario


def rules(**changes):
    seconds = changes.get('seconds', 3600)
    return dict(schema_version=1, families=changes.get('families', {'a': 'A', 'b': 'B'}),
                machines=[dict(resource_id='m', initial_family='A',
                               transitions=[dict(from_family='A', to_family='B',
                                                 seconds=seconds)])])


class SetupAuditTests(unittest.TestCase):
    def test_shortfall_report_when_jobs_run_back_to_back(self):
        s = scenario([op('a'), op('b')])
        p = plan(s)
        report = audit_setups(s, p, rules())
        self.assertFalse(report.valid)
        self.assertEqual(len(report.transitions), 2)
        self.assertEqual(len(report.violations), 1)
        self.assertEqual(report.violations[0].shortfall_seconds, 3600)
        self.assertEqual(report.violations[0].predecessor_task, 'job:a')
        self.assertEqual(report.to_dict()['violations'][0]['resource_id'], 'm')
        self.assertEqual(dumps(s), dumps(scenario([op('a'), op('b')])))

    def test_enough_idle_work_time_satisfies_changeover(self):
        s = scenario([op('a'), op('b', release=dt(hour=10))])
        report = audit_setups(s, plan(s), rules())
        self.assertTrue(report.valid)
        self.assertEqual(report.transitions[1].available_work_seconds, 3600)
        self.assertEqual(report.transitions[1].required_seconds, 3600)

    def test_lunch_not_counted_as_machine_setup_time(self):
        s = scenario([op('a', hours=3), op('b', release=dt(hour=13))])
        report = audit_setups(s, plan(s), rules(seconds=7200))
        self.assertEqual(report.transitions[1].available_work_seconds, 3600)
        self.assertEqual(report.violations[0].shortfall_seconds, 3600)

    def test_initial_setup_requires_available_work(self):
        data = rules(families={'a': 'A'})
        data['machines'][0]['initial_family'] = 'B'
        data['machines'][0]['transitions'].append(dict(from_family='B', to_family='A', seconds=3600))
        s = scenario([op('a')])
        report = audit_setups(s, plan(s), data)
        self.assertEqual(report.violations[0].predecessor_task, None)
        self.assertEqual(report.violations[0].shortfall_seconds, 3600)

    def test_nesting_is_one_atomic_machine_task(self):
        s = scenario([op('a'), op('b')],
                     nestings=(Nesting('ab', ('a', 'b'), 3600),))
        data = rules()
        data['families']['b'] = 'A'
        report = audit_setups(s, plan(s), data)
        self.assertTrue(report.valid)
        self.assertEqual(len(report.transitions), 1)
        self.assertEqual(report.transitions[0].successor_task, 'nest:ab')

    def test_nested_mixed_families_rejected(self):
        s = scenario([op('a'), op('b')], nestings=(Nesting('ab', ('a', 'b'), 3600),))
        with self.assertRaisesRegex(ValidationError, 'conflicting setup families'):
            audit_setups(s, plan(s), rules())

    def test_missing_transition_not_assumed_zero(self):
        s = scenario([op('a'), op('b')])
        data = rules()
        data['machines'][0]['transitions'] = []
        with self.assertRaisesRegex(ValidationError, 'missing explicit setup transition'):
            audit_setups(s, plan(s), data)

    def test_missing_family_rejected(self):
        s = scenario([op('a'), op('b')])
        data = rules()
        del data['families']['b']
        with self.assertRaisesRegex(ValidationError, 'missing or conflicting setup families'):
            audit_setups(s, plan(s), data)

    def test_capacity_and_partial_scheduling(self):
        s = scenario([op('a')], resources=(Resource('m', 'work', 2),))
        with self.assertRaisesRegex(ValidationError, 'unit-capacity machine'):
            audit_setups(s, plan(s), rules(families={'a': 'A'}))
        s = scenario([op('a', hours=100)])
        report = audit_setups(s, plan(s), rules(families={'a': 'A'}))
        self.assertTrue(report.valid)
        self.assertTrue(report.partial_plan)
        self.assertEqual(len(report.transitions), 0)

    def test_interrupted_task_across_shifts_retains_machine_family(self):
        s = scenario([op('a', hours=7, splittable=True)])
        p = plan(s)
        self.assertGreater(len(p.assignments[0].segments), 1)
        report = audit_setups(s, p, rules(families={'a': 'A'}))
        self.assertTrue(report.valid)
        self.assertEqual(len(report.transitions), 1)
        self.assertEqual(report.transitions[0].successor_task, 'job:a')

    def test_setup_after_split_task_counts_workable_gap_once(self):
        s = scenario([op('a', hours=7, splittable=True),
                      op('b', release=dt(20, 10))])
        p = plan(s)
        a = next(x for x in p.assignments if x.task_id == 'job:a')
        self.assertGreater(len(a.segments), 1)
        report = audit_setups(s, p, rules(seconds=7200))
        self.assertTrue(report.valid)
        self.assertEqual(len(report.transitions), 2)
        self.assertEqual(report.transitions[1].available_work_seconds, 10800)
        self.assertEqual(report.transitions[1].required_seconds, 7200)

    def test_interleaved_fragments_require_second_directional_changeover(self):
        # A uses the machine, fixed B interrupts it, and A resumes. The
        # machine performs A -> B -> A, not a single A -> B changeover.
        s = scenario([op('a', hours=7, splittable=True),
                      op('b', fixed_start=dt(hour=13))])
        p = plan(s)
        self.assertGreater(len(next(a for a in p.assignments
                                    if a.task_id == 'job:a').segments), 1)
        with self.assertRaisesRegex(ValidationError, 'missing explicit setup transition B -> A'):
            audit_setups(s, p, rules())
        spec = rules()
        spec['machines'][0]['transitions'].append(
            {'from_family': 'B', 'to_family': 'A', 'seconds': 3600})
        report = audit_setups(s, p, spec)
        self.assertEqual([(x.predecessor_task, x.successor_task) for x in report.transitions],
                         [(None, 'job:a'), ('job:a', 'job:b'), ('job:b', 'job:a')])
        self.assertEqual(len(report.violations), 2)
        self.assertEqual(report.transitions[2].shortfall_seconds, 3600)
        self.assertEqual(report.transitions[2].predecessor_end, dt(hour=14))
        self.assertEqual(report.to_dict()['transitions'][2]['predecessor_end'],
                         dt(hour=14).isoformat())

    def test_interleaved_fragments_with_zero_explicit_setups_can_be_valid(self):
        s = scenario([op('a', hours=7, splittable=True),
                      op('b', fixed_start=dt(hour=13))])
        spec = rules(seconds=0)
        spec['machines'][0]['transitions'].append(
            {'from_family': 'B', 'to_family': 'A', 'seconds': 0})
        report = audit_setups(s, plan(s), spec)
        self.assertTrue(report.valid)
        self.assertEqual(len(report.transitions), 3)

    def test_reject_forged_plan(self):
        s = scenario([op('a')])
        p = plan(s)
        with self.assertRaisesRegex(ValidationError, 'invalid plan'):
            audit_setups(s, replace(p, scenario_digest='forged'), rules())

    def test_strict_json_validation(self):
        s = scenario([op('a')])
        with self.assertRaisesRegex(ValidationError, 'duplicate setup JSON key'):
            load_setup_rules('{"schema_version":1,"schema_version":1}', s)
        data = rules(families={'a': 'A'})
        data['machines'][0]['transitions'][0]['seconds'] = True
        with self.assertRaisesRegex(ValidationError, 'integer'):
            load_setup_rules(json.dumps(data), s)
        data = rules(families={'a': 'A'})
        data['machines'][0]['transitions'].append(data['machines'][0]['transitions'][0])
        with self.assertRaisesRegex(ValidationError, 'duplicate transition'):
            load_setup_rules(json.dumps(data), s)

    def test_cli_returns_two_for_deficit_and_refuses_overwrite(self):
        s = scenario([op('a'), op('b')])
        p = plan(s)
        with TemporaryDirectory() as root:
            files = {k: Path(root) / f'{k}.json' for k in ('scenario', 'plan', 'rules', 'report')}
            files['scenario'].write_text(dumps(s), encoding='utf-8')
            files['plan'].write_text(dumps(p), encoding='utf-8')
            files['rules'].write_text(json.dumps(rules()), encoding='utf-8')
            out = StringIO()
            with redirect_stdout(out):
                code = main(['audit-setups', str(files['scenario']), str(files['plan']),
                             str(files['rules']), '-o', str(files['report'])])
            self.assertEqual(code, 2)
            self.assertFalse(json.loads(out.getvalue())['valid'])
            self.assertEqual(json.loads(files['report'].read_text())['violations'][0]['shortfall_seconds'], 3600)
            original = files['rules'].read_text()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                code = main(['audit-setups', str(files['scenario']), str(files['plan']),
                             str(files['rules']), '-o', str(files['rules'])])
            self.assertEqual(code, 1)
            self.assertEqual(files['rules'].read_text(), original)
