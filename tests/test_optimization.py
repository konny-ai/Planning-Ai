"""Regression tests for opt-in planning policies and immutable candidate ranking."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from planning_factory.adapters.json_io import dumps, loads, plan_loads, scenario_digest
from planning_factory.cli import main
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.optimization import (optimize, measure, DISPATCH_POLICIES,
                                                  CandidateMetrics, pareto_policies)
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from .helpers import dt, op, scenario


def competing_orders():
    # Priority alone books A before B, missing B's 11:00 promise. An
    # earliest-due choice places B first and avoids *all* order lateness.
    return scenario([
        op('a', hours=3, priority=-2, due=dt(22, 17), order_id='A'),
        op('b', hours=2, priority=0, due=dt(19, 11), order_id='B'),
    ])


class OptimizationTests(unittest.TestCase):
    def test_default_policy_remains_byte_identical(self):
        s = competing_orders()
        self.assertEqual(dumps(plan(s)), dumps(plan(s, dispatch_policy='priority')))

    def test_improves_due_date_without_changing_scenario(self):
        s = competing_orders()
        original = dumps(s)
        baseline = plan(s)
        tuned = optimize(s)
        self.assertEqual(tuned.winner_policy, 'earliest_due')
        self.assertEqual(tuned.candidates[0].policy, 'priority')
        self.assertGreater(measure(s, baseline, 'priority').total_tardiness_seconds, 0)
        self.assertEqual(measure(s, tuned.plan, tuned.winner_policy).total_tardiness_seconds, 0)
        self.assertEqual(verify_plan(s, tuned.plan), ())
        self.assertEqual(dumps(s), original)
        self.assertEqual(tuned.plan.scenario_digest, scenario_digest(s))

    def test_no_regression_and_tie_prefers_baseline(self):
        for s in (scenario([op()]), competing_orders()):
            tuned = optimize(s)
            self.assertLessEqual(measure(s, tuned.plan, tuned.winner_policy).ranking,
                                 measure(s, plan(s), 'priority').ranking)
        s = scenario([op()])
        self.assertEqual(optimize(s).winner_policy, 'priority')

    def test_deterministic_serialization(self):
        s = competing_orders()
        a, b = optimize(s), optimize(s)
        self.assertEqual(dumps(a), dumps(b))
        decoded = json.loads(dumps(a))
        self.assertEqual(verify_plan(s, plan_loads(json.dumps(decoded['plan']))), ())
        self.assertEqual(len(decoded['candidates']), len(DISPATCH_POLICIES))

    def test_pareto_frontier_exposes_tradeoff_not_hidden_by_lexicographic_winner(self):
        # Fewer late orders but far more cumulative delay: neither dominates.
        metrics = (
            CandidateMetrics('baseline', 0, 54, 103398644, 5509800),
            CandidateMetrics('more_punctual', 0, 51, 141316639, 5509800),
            CandidateMetrics('strictly_worse', 0, 54, 150000000, 5509800),
            CandidateMetrics('same_as_baseline', 0, 54, 103398644, 5509800),
        )
        self.assertEqual(pareto_policies(metrics),
                         ('baseline', 'more_punctual', 'same_as_baseline'))
        self.assertEqual(pareto_policies(metrics), pareto_policies(metrics))

    def test_pareto_diagnostics_preserve_existing_winner_and_default_schedule(self):
        s = competing_orders()
        before = dumps(plan(s))
        result = optimize(s)
        self.assertIn(result.winner_policy, result.pareto_frontier)
        self.assertEqual(result.winner_policy, 'earliest_due')
        self.assertEqual(dumps(plan(s)), before)
        self.assertEqual(verify_plan(s, result.plan), ())

    def test_invalid_policy_rejected(self):
        s = scenario([op()])
        with self.assertRaises(ValueError):
            plan(s, dispatch_policy='unknown')
        for invalid in ((), ('earliest_due',), ('priority', 'priority'),
                        ('priority', 'unknown')):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                optimize(s, invalid)

    def test_cli_optimize_with_metrics(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            s = competing_orders()
            source = root / 'scenario.json'
            output = root / 'best.json'
            metrics = root / 'metrics.json'
            source.write_text(dumps(s), encoding='utf-8')
            stdout = StringIO()
            with redirect_stdout(stdout), redirect_stderr(StringIO()):
                code = main(['optimize', str(source), '-o', str(output), '--metrics', str(metrics)])
            self.assertEqual(code, 0)
            announced = json.loads(stdout.getvalue())
            self.assertEqual(announced['winner_policy'], 'earliest_due')
            self.assertEqual(announced['pareto_frontier'],
                             json.loads(metrics.read_text(encoding='utf-8'))['pareto_frontier'])
            self.assertIn('earliest_due', announced['pareto_frontier'])
            self.assertEqual(verify_plan(s, plan_loads(output.read_text(encoding='utf-8'))), ())
            parsed = json.loads(metrics.read_text(encoding='utf-8'))
            self.assertEqual(parsed['winner_policy'], 'earliest_due')
            self.assertIn('earliest_due', parsed['pareto_frontier'])
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                rejected = main(['optimize', str(source), '-o', str(output), '--metrics', str(output)])
            self.assertEqual(rejected, 1)

    def test_structured_result_cannot_claim_unverified_candidate(self):
        s = competing_orders()
        p = plan(s)
        forged = replace(p, scenario_digest='wrong')
        with self.assertRaises(ValueError):
            measure(s, forged, 'priority')
