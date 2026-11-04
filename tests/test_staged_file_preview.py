"""File-backed staged v0.3 pipeline contract (NOT executed at user request)."""
import json
from pathlib import Path
import tempfile
import unittest

from planning_factory.adapters.json_io import dumps, scenario_digest
from planning_factory.adapters.dashboard_api import DashboardState
from planning_factory.adapters.staged_file import load_staged_file, calculate_staged_preview
from planning_factory.domain.validation import ValidationError
from .helpers import op
from .test_setup_stage_scheduler import base, spec


class StagedFilePreviewTests(unittest.TestCase):
    def setUp(self):
        self.scenario=base([op('a'),op('b')])
        self.raw={
            "schema_version":1,
            "source_scenario_digest":scenario_digest(self.scenario),
            "mode":"normal",
            "setup_contract":spec(),
            "machine_sequence":["job:a","job:b"],
            "task_priority":["job:a","job:b"],
            "stock":{"accounting":"net","supplies":[]},
            "needs":[],
        }

    def from_file(self,value):
        with tempfile.TemporaryDirectory() as folder:
            filename=Path(folder)/"stage.json"
            filename.write_text(json.dumps(value),encoding="utf-8")
            return load_staged_file(filename,self.scenario)

    def test_net_stock_contract_and_independent_technical_pipeline(self):
        contract=self.from_file(self.raw)
        self.assertTrue(contract.inventory_net)
        self.assertIsNone(contract.external_commitments)
        result=calculate_staged_preview(self.scenario,contract)
        self.assertEqual(result.allocation.shortages,())
        self.assertTrue(any(x.stage=="setup" for x in result.staged.bookings))
        self.assertTrue(any(x.stage=="run" for x in result.staged.bookings))

    def test_gross_stock_requires_explicit_commitment_snapshot(self):
        raw={**self.raw,"stock":{"accounting":"gross","supplies":[]}}
        with self.assertRaisesRegex(ValidationError,"gross stock requires"):
            self.from_file(raw)
        raw["stock"]["external_commitments"]=[]
        parsed=self.from_file(raw)
        self.assertEqual(parsed.external_commitments,())

    def test_scenario_digest_guards_against_stale_stock_and_machine_state(self):
        raw={**self.raw,"source_scenario_digest":"0"*64}
        with self.assertRaisesRegex(ValidationError,"does not match"):
            self.from_file(raw)

    def test_rejects_float_quantities_and_unconfirmed_wip(self):
        raw={**self.raw,"needs":[{"task_id":"job:a","item_id":"PART","quantity":1.5}]}
        with self.assertRaisesRegex(ValidationError,"exact decimal string"):
            self.from_file(raw)
        raw={**self.raw,"mode":"paused"}
        with self.assertRaisesRegex(ValidationError,"paused mode requires"):
            self.from_file(raw)

    def test_backward_and_objective_in_the_spec(self):
        from .helpers import dt
        scenario=base([op('a',due=dt(day=23,hour=17)),op('b',due=dt(day=23,hour=17))])
        raw={**self.raw,"source_scenario_digest":scenario_digest(scenario)}
        with tempfile.TemporaryDirectory() as folder:
            def load(value):
                filename=Path(folder)/"stage.json"
                filename.write_text(json.dumps(value),encoding="utf-8")
                return load_staged_file(filename,scenario)
            forward=calculate_staged_preview(scenario,load(raw))
            spec_back=load({**raw,"backward":True})
            self.assertTrue(spec_back.backward)
            back=calculate_staged_preview(scenario,spec_back)
            self.assertIsNotNone(back.backward)
            f={(x.phase_id,x.stage):x for x in forward.staged.bookings}
            b={(x.phase_id,x.stage):x for x in back.staged.bookings}
            self.assertGreater(b[("job:b","run")].segments[0].start,f[("job:b","run")].segments[0].start)
            searched=calculate_staged_preview(
                scenario,load({**raw,"backward":True,"sequence_objective":"setup"}))
            self.assertEqual(sorted(searched.machine_sequence),["job:a","job:b"])
            for bad,message in (({**raw,"backward":"yes"},"explicit boolean"),
                                ({**raw,"sequence_objective":"setup"},"requires backward"),
                                ({**raw,"backward":True,"sequence_objective":"flow"},"delivery, setup or gap")):
                with self.assertRaisesRegex(ValidationError,message):
                    load(bad)

    def test_hostile_quantities_and_dates_are_rejected_not_overflowed(self):
        # Found by fuzzing every field with hostile values: these escaped as raw Python exceptions.
        supply={"id":"sheet","item_id":"SHEET","quantity":"2","available_at":"2026-10-20T09:00:00Z"}
        def spec_with(**changes):
            return {**self.raw,"stock":{"accounting":"net","supplies":[{**supply,**changes}]}}
        for changes,message in (({"quantity":"1e999999999"},"out of range"),
                                ({"quantity":"1e-999999999"},"out of range"),
                                ({"quantity":"1"+"0"*40},"out of range"),
                                ({"quantity":"NaN"},"finite positive"),
                                ({"available_at":"9999-12-31T23:59:59Z"},"year must be between"),
                                ({"available_at":"0001-01-01T00:00:00Z"},"year must be between")):
            with self.subTest(changes=changes),self.assertRaisesRegex(ValidationError,message):
                self.from_file(spec_with(**changes))
        self.assertTrue(self.from_file(spec_with(quantity="0.001")).supplies)       # ordinary values still pass
        self.assertTrue(self.from_file(spec_with(quantity="123456789012345")).supplies)

    def test_synthetic_generator_binds_scenario_and_stage_spec(self):
        from tools.generate_v03_stage_demo import make_example
        scenario, request = make_example()
        self.assertEqual(request["source_scenario_digest"], scenario_digest(scenario))
        with tempfile.TemporaryDirectory() as folder:
            filename = Path(folder) / "stage.json"
            filename.write_text(json.dumps(request), encoding="utf-8")
            spec = load_staged_file(filename, scenario)
            self.assertEqual(spec.mode, "normal")
            self.assertEqual(len(spec.supplies), 1)
            self.assertEqual(len(spec.needs), 2)
            self.assertEqual(spec.task_priority, ("job:CUT-A", "job:CUT-B"))

    def test_browser_preview_uses_exact_source_and_never_publishes(self):
        from tools.generate_v03_stage_demo import make_example
        source, payload = make_example()
        with tempfile.TemporaryDirectory() as folder:
            scenario_path = Path(folder) / "scenario.json"
            stage_path = Path(folder) / "stage.json"
            scenario_path.write_text(dumps(source), encoding="utf-8")
            stage_path.write_text(json.dumps(payload), encoding="utf-8")
            dashboard = DashboardState(scenario_path, staged_spec=stage_path)
            self.assertTrue(dashboard.bootstrap()["meta"]["v03_available"])
            preview = dashboard.staged_preview()
            self.assertTrue(preview["audited"])
            self.assertFalse(preview["publishable"])
            self.assertFalse(preview["erp_connected"])
            self.assertTrue(preview["technical_only"])
            self.assertFalse(preview["partial_plan"])
            self.assertEqual(preview["source_scenario_digest"],
                             payload["source_scenario_digest"])
            self.assertEqual(preview, dashboard.staged_preview())  # no recompute

    def test_preview_is_not_available_without_opt_in(self):
        state=DashboardState.__new__(DashboardState)
        state._stage_spec=None
        with self.assertRaisesRegex(ValidationError,"no opt-in"):
            state.staged_preview()


if __name__=="__main__":
    unittest.main()
