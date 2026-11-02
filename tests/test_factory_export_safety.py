"""Safety regression tests for the Factory extraction/conversion boundary.

All fixtures are synthetic: no customer database, credentials, or raw IDs.
"""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "integrations" / "factory-export" / "tools"


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def scenario():
    """Minimum complete synthetic Factory-export payload for conversion."""
    return {
        "scenarioId": "synthetic", "source": {"database": "SYNTHETIC", "capturedAt": "2026-10-19T00:00:00+02:00"},
        "planning": {"timeZone": "Europe/Rome", "start": "2026-10-19T00:00:00", "extraction": {"database": "SYNTHETIC"}},
        "calendarModels": [{"modelId": 1, "cycleDays": 7, "cycleStartDate": "2026-10-19"}],
        "calendarModelDays": [{"modelId": 1, "dayIndex": x, "dayTypeId": 1} for x in range(7)],
        "dayTypeIntervals": [{"dayTypeId": 1, "startSec": 0, "endSec": 0, "crossesMidnight": True}],
        "calendarAssignments": [{"calendarId": 0, "modelId": 1, "validFrom": "2020-01-01"}],
        "holidays": [], "calendarExceptions": [], "calendarPeriods": [], "operatorAbsences": [],
        "operators": [{"code": "OP-A", "calendarId": 0, "name": "Worker A"}],
        "machines": [{"code": "MAC-A", "calendarId": 0, "name": "Machine A"}],
        "suppliers": [], "customers": [], "departments": [], "orders": [], "orderLines": [],
        "departmentMembers": [], "materialArrivals": [], "lotComponents": [],
        "subcontractOrders": [], "explicitLinks": [],
        "lots": [{"orderId": 1, "lotId": 1, "parentLotId": None, "priority": 0}],
        "operations": [{
            "operationId": 101, "orderId": 1, "lotId": 1, "phaseId": 11,
            "sequence": 10, "executionCode": "I", "toPlan": "Y",
            "theoreticalTotalSec": 3600, "unitSec": 3600, "qty": 1,
            "setupSec": 0, "priority": 0, "runMachines": 1, "runOperators": 1,
            "legacyDueDate": "2026-10-30", "fixedStartFlag": 0, "fixedStartAt": None,
        }],
        "operationResources": [
            {"operationId": 101, "resourceKind": "MACHINE", "resourceCode": "MAC-A",
             "assignmentRule": "ALLOWED", "calendarId": None, "calendarModelId": None},
            {"operationId": 101, "resourceKind": "OPERATOR", "resourceCode": "OP-A",
             "assignmentRule": "ALLOWED", "calendarId": None, "calendarModelId": None},
        ],
        "precedences": [],
    }


class FactoryExportSafetyTests(unittest.TestCase):
    def convert(self, payload, *args, expect_success=True):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "in.json"
            out = root / "out"
            src.write_text(json.dumps(payload), encoding="utf-8")
            cmd = [sys.executable, str(TOOLS / "to_engine_v02.py"), str(src), "--out", str(out), *args]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
            if not expect_success:
                self.assertNotEqual(result.returncode, 0, result.stdout)
                return result.stderr + result.stdout
            self.assertEqual(result.returncode, 0, result.stderr)
            return {p.stem: json.loads(p.read_text(encoding="utf-8"))
                    for p in out.glob("*.json")}

    def test_exclusions_forced_resource_and_full_day_shift(self):
        s = scenario()
        s["machines"].append({"code": "MAC-B", "calendarId": 0, "name": "Machine B"})
        s["operationResources"].append({
            "operationId": 101, "resourceKind": "MACHINE", "resourceCode": "MAC-B",
            "assignmentRule": "EXCLUDED", "calendarId": None, "calendarModelId": None})
        out = self.convert(s)
        operation = out["engine-scenario"]["operations"][0]
        self.assertEqual(operation["alternatives"][0]["demands"], [
            {"resource_id": "MAC:MAC-A", "units": 1},
            {"resource_id": "OP:OP-A", "units": 1}])
        self.assertTrue(out["engine-scenario"]["calendars"][0]["shifts"])
        shift = out["engine-scenario"]["calendars"][0]["shifts"][0]
        self.assertEqual((shift["start"], shift["end"]), ("00:00", "00:00"))

        s["operationResources"][2]["assignmentRule"] = "FORCED"
        out = self.convert(s)
        resources = {d["resource_id"] for d in out["engine-scenario"]["operations"][0]["alternatives"][0]["demands"]}
        self.assertEqual(resources, {"MAC:MAC-B", "OP:OP-A"})

    def test_automatic_threshold_and_delivery_margin_follow_legacy(self):
        # docs/22 §22.2.1: backward when CMDTS >= start + SogliaGGTipoPian (date comparison);
        # GiorniMargineConsegna moves the engine due date, never the mode decision.
        from planning_factory.adapters.json_io import loads
        from planning_factory.engine.scheduler import plan
        s = scenario()
        first = s["operations"][0]
        s["lots"].append({"orderId": 1, "lotId": 2, "parentLotId": None, "priority": 0})
        s["operations"] = [dict(first, legacyDueDate="2026-11-18"),  # start + 30 days
                           dict(first, operationId=102, lotId=2, legacyDueDate="2026-11-17")]
        s["operationResources"] += [dict(r, operationId=102) for r in s["operationResources"]]
        for margin, due in ((0, "2026-11-19T00:00:00+01:00"), (1, "2026-11-18T00:00:00+01:00")):
            with self.subTest(margin=margin):
                out = self.convert(s, "--delivery-margin-days", str(margin))
                engine = out["engine-scenario"]
                self.assertEqual(engine["operations"][0]["due"], due)
                result = plan(loads(json.dumps(engine)))
                modes = {a.operation_ids[0]: a.effective_mode for a in result.assignments}
                self.assertEqual(modes, {"1/1/11": "backward", "1/2/11": "forward"})

    def test_time_progress_policy_books_the_remaining_share(self):
        # Legacy "Con tempi" progress: p = max(recorded / theoretical) over machines and
        # operators; 6800/1/1 on the bench books 14400 - 7247 = 7153 s, 5798/3/2 nothing.
        s = scenario()
        first = s["operations"][0]
        s["operations"] = [
            dict(first, actualWorkOperatorSec=1800),                      # 50% operator
            dict(first, operationId=102, phaseId=12, sequence=20,
                 actualWorkMachineSec=900, actualSetupOperatorSec=2700),  # max(25%, 75%)
            dict(first, operationId=103, phaseId=13, sequence=30, actualWorkOperatorSec=5000),
        ]
        s["operationResources"] += [dict(r, operationId=oid) for oid in (102, 103)
                                    for r in s["operationResources"][:2]]
        untouched = self.convert(s)["engine-scenario"]["operations"]
        self.assertEqual([o["duration_seconds"] for o in untouched], [3600, 3600, 3600])
        out = self.convert(s, "--progress-policy", "time")
        operations = out["engine-scenario"]["operations"]
        self.assertEqual([(o["id"], o["duration_seconds"]) for o in operations],
                         [("1/1/11", 1800), ("1/1/12", 900)])
        phases = {p["IDFAS"]: p for p in out["factory-snapshot"]["phases"]}
        self.assertEqual((phases[11]["FASLV"], phases[13]["FASLV"]), (1800, 0))

    def test_mandatory_machine_and_operator_not_silently_dropped(self):
        s = scenario()
        s["operationResources"] = s["operationResources"][:1]
        message = self.convert(s, expect_success=False)
        self.assertIn("risorse obbligatorie mancanti", message)
        self.assertNotIn("RecursionError", message)
        out = self.convert(s, "--missing-resource-policy", "available")
        self.assertEqual(out["engine-scenario"]["operations"][0]["alternatives"],
                         [{"demands": [{"resource_id": "MAC:MAC-A", "units": 1}]}])

    def test_no_candidates_must_not_bypass_mandatory_resource_demand(self):
        s = scenario()
        s['operationResources'] = []
        error = self.convert(s, expect_success=False)
        self.assertIn('risorse obbligatorie mancanti', error)
        self.assertIn('macchine 0/1, operatori 0/1', error)
        # A permissive fallback must always be deliberate, never silent.
        out = self.convert(s, '--missing-resource-policy', 'available')
        self.assertEqual(
            out['engine-scenario']['operations'][0]['alternatives'],
            [{'demands': [{'resource_id': 'NO_RESOURCE', 'units': 1}]}])

    def test_explicit_zero_resource_demand_can_use_virtual_resource(self):
        s = scenario()
        s['operationResources'] = []
        s['operations'][0]['runMachines'] = 0
        s['operations'][0]['runOperators'] = 0
        out = self.convert(s)
        self.assertEqual(
            out['engine-scenario']['operations'][0]['alternatives'],
            [{'demands': [{'resource_id': 'NO_RESOURCE', 'units': 1}]}])

    def test_factory_calendar_model_cycles_periods_exceptions_and_absences(self):
        # RE M08 §5.2: day of cycle = (date - TCSTD) mod N; L_CATC overrides by period; A_ECC replaces
        # the day ([0,0] = closed); S_GSA closes only the absent operator.
        s = scenario()
        s["calendarModels"] = [
            {"modelId": 1, "cycleDays": 7, "cycleStartDate": "2026-10-19"},
            {"modelId": 2, "cycleDays": 14, "cycleStartDate": "2026-10-19"},   # one week on, one week off
            {"modelId": 3, "cycleDays": 7, "cycleStartDate": "2026-10-19"}]
        s["calendarModelDays"] = ([{"modelId": 1, "dayIndex": x, "dayTypeId": 1} for x in range(7)]
                                  + [{"modelId": 2, "dayIndex": x, "dayTypeId": 2 if x < 7 else 3} for x in range(14)]
                                  + [{"modelId": 3, "dayIndex": x, "dayTypeId": 4} for x in range(7)])
        s["dayTypeIntervals"] = [{"dayTypeId": 1, "startSec": 0, "endSec": 0, "crossesMidnight": True},
                                 {"dayTypeId": 2, "startSec": 6 * 3600, "endSec": 14 * 3600, "crossesMidnight": False},
                                 {"dayTypeId": 3, "startSec": 0, "endSec": 0, "crossesMidnight": False},
                                 {"dayTypeId": 4, "startSec": 22 * 3600, "endSec": 6 * 3600, "crossesMidnight": True}]
        s["calendarAssignments"] += [{"calendarId": 7, "modelId": 2, "validFrom": "2020-01-01"}]
        s["calendarPeriods"] = [{"calendarId": 7, "modelId": 3, "fromDate": "2026-11-02", "toDate": "2026-11-03", "seq": 1}]
        s["calendarExceptions"] = [{"calendarId": 7, "date": "2026-10-20", "startSec": 8 * 3600, "endSec": 10 * 3600, "kind": 0},
                                   {"calendarId": 7, "date": "2026-10-21", "startSec": 0, "endSec": 0, "kind": 0}]
        s["operators"] = [{"code": "OP-A", "calendarId": 7, "name": "A"}, {"code": "OP-B", "calendarId": 7, "name": "B"}]
        s["operatorAbsences"] = [{"operatorCode": "OP-B", "date": "2026-10-22", "startSec": 7 * 3600,
                                  "endSec": 9 * 3600, "reasonCode": 1, "approved": 1}]
        out = self.convert(s, "--horizon-days", "30")
        calendars = {c["id"]: c for c in out["engine-scenario"]["calendars"]}
        days = {e["day"]: e["shifts"] for e in calendars["CAL7"]["exceptions"]}
        self.assertEqual(calendars["CAL7"]["shifts"], [])
        self.assertEqual(days["2026-10-19"], [["06:00", "14:00"]])        # cycle day 0
        self.assertEqual(days["2026-10-20"], [["08:00", "10:00"]])        # A_ECC replaces the day
        self.assertEqual(days["2026-10-21"], [])                          # A_ECC [0,0]: closed
        self.assertEqual(days["2026-10-26"], [])                          # cycle day 7: week off
        self.assertEqual(days["2026-11-02"], [["22:00", "06:00"]])        # L_CATC period, night shift
        self.assertEqual(days["2026-11-04"], [["06:00", "14:00"]])        # back to the cycle (day 16 -> 2)
        resources = {r["id"]: r for r in out["engine-scenario"]["resources"]}
        self.assertEqual(resources["OP:OP-A"]["calendar_id"], "CAL7")
        own = calendars[resources["OP:OP-B"]["calendar_id"]]
        self.assertEqual(own["closures"], [{"start": "2026-10-22T07:00:00+02:00", "end": "2026-10-22T09:00:00+02:00"}])
        self.assertEqual(own["exceptions"], calendars["CAL7"]["exceptions"])
        self.assertEqual(calendars["CAL0"]["exceptions"], [])            # weekly calendars stay compact

    def test_calendar_edge_cases_overnight_exception_late_assignment_and_bad_model(self):
        s = scenario()
        s["calendarModels"].append({"modelId": 2, "cycleDays": 7, "cycleStartDate": "2026-10-19"})
        s["calendarModelDays"] += [{"modelId": 2, "dayIndex": x, "dayTypeId": 2} for x in range(7)]
        s["dayTypeIntervals"].append({"dayTypeId": 2, "startSec": 8 * 3600, "endSec": 16 * 3600, "crossesMidnight": False})
        s["calendarAssignments"].append({"calendarId": 7, "modelId": 2, "validFrom": "2026-10-26"})  # starts in the horizon
        s["calendarExceptions"] = [{"calendarId": 7, "date": "2026-10-27", "startSec": 22 * 3600, "endSec": 6 * 3600, "kind": 0}]
        s["operators"][0]["calendarId"] = 7
        out = self.convert(s, "--horizon-days", "14")
        days = {e["day"]: e["shifts"] for e in out["engine-scenario"]["calendars"][1]["exceptions"]}
        self.assertEqual(out["engine-scenario"]["calendars"][1]["id"], "CAL7")
        self.assertEqual(days["2026-10-20"], [])                        # before the assignment: unavailable
        self.assertEqual(days["2026-10-26"], [["08:00", "16:00"]])
        self.assertEqual(days["2026-10-27"], [["22:00", "06:00"]])      # overnight A_ECC interval kept
        s["calendarAssignments"].append({"calendarId": 8, "modelId": 99, "validFrom": "2020-01-01"})
        self.assertIn("modello 99 inesistente", self.convert(s, expect_success=False))

    def test_future_only_calendar_assignment_stays_closed(self):
        s = scenario()
        s["calendarAssignments"].append(
            {"calendarId": 7, "modelId": 1, "validFrom": "2027-01-01"})
        s["operators"][0]["calendarId"] = 7
        out = self.convert(s, "--horizon-days", "14")
        calendars = {c["id"]: c for c in out["engine-scenario"]["calendars"]}
        self.assertIn("CAL7", calendars)
        self.assertEqual(calendars["CAL7"]["shifts"], [])
        self.assertTrue(calendars["CAL7"]["exceptions"])
        self.assertTrue(all(not x["shifts"] for x in calendars["CAL7"]["exceptions"]))
        resources = {r["id"]: r for r in out["engine-scenario"]["resources"]}
        self.assertEqual(resources["OP:OP-A"]["calendar_id"], "CAL7")

    def test_ambiguous_and_nonexistent_dst_absences_fail_closed(self):
        for day, begin, finish in [
            ("2026-03-29", 2 * 3600 + 30 * 60, 3 * 3600 + 30 * 60),
            ("2026-10-25", 2 * 3600 + 30 * 60, 3 * 3600 + 30 * 60),
        ]:
            with self.subTest(day=day):
                s = scenario()
                s["operatorAbsences"] = [{
                    "operatorCode": "OP-A", "date": day,
                    "startSec": begin, "endSec": finish,
                    "reasonCode": 1, "approved": 1,
                }]
                self.assertIn("ambigua o inesistente", self.convert(s, expect_success=False))

    def test_missing_calendar_zero_fails_directly(self):
        s = scenario()
        s["calendarAssignments"] = []
        message = self.convert(s, expect_success=False)
        self.assertIn("calendario 0 senza assegnazione", message)
        self.assertNotIn("RecursionError", message)

    def test_multiple_machines_and_operators_are_required(self):
        s = scenario()
        s["operations"][0]["runMachines"] = 2
        s["operations"][0]["runOperators"] = 2
        s["machines"].append({"code": "MAC-B", "calendarId": 0})
        s["operators"].append({"code": "OP-B", "calendarId": 0})
        s["operationResources"].extend([
            {"operationId": 101, "resourceKind": kind, "resourceCode": code,
             "assignmentRule": "ALLOWED", "calendarId": None, "calendarModelId": None}
            for kind, code in (("MACHINE", "MAC-B"), ("OPERATOR", "OP-B"))])
        out = self.convert(s)
        alternatives = out["engine-scenario"]["operations"][0]["alternatives"]
        self.assertEqual(len(alternatives), 1)
        self.assertEqual(len(alternatives[0]["demands"]), 4)

    def test_fixed_start_is_preserved_in_both_contracts(self):
        s = scenario()
        s["operations"][0].update(fixedStartFlag=1, fixedStartAt="2026-10-20T08:30:00")
        out = self.convert(s)
        actual = out["engine-scenario"]["operations"][0]["fixed_start"]
        self.assertEqual(actual, "2026-10-20T08:30:00+02:00")
        self.assertEqual(out["factory-snapshot"]["phases"][0]["fixed_start"], actual)

    def test_fs_bridge_keeps_upstream_type_lag_and_explicit_link(self):
        s = scenario()
        s["operations"].append(dict(s["operations"][0], operationId=102, phaseId=12,
                                    sequence=20, theoreticalTotalSec=0, unitSec=0, qty=0))
        s["operations"].append(dict(s["operations"][0], operationId=103, phaseId=13, sequence=30))
        # This successor actually runs and needs its own mandatory
        # machine/operator candidates. The zero-duration bridge does not.
        s["operationResources"] += [dict(r, operationId=103) for r in s["operationResources"]]
        s["precedences"] = [
            {"predecessorOperationId": 101, "successorOperationId": 102, "linkType": "FS",
             "lagSec": 20, "overlapPct": 0, "source": "EXPLICIT"},
            {"predecessorOperationId": 102, "successorOperationId": 103, "linkType": "SS",
             "lagSec": 10, "overlapPct": 100, "source": "EXPLICIT"}]
        out = self.convert(s)
        target = next(o for o in out["engine-scenario"]["operations"] if o["id"].endswith("/13"))
        self.assertEqual(target["predecessors"],
                         [{"predecessor_id": "1/1/11", "kind": "FS", "lag_seconds": 30}])
        self.assertEqual(out["factory-snapshot"]["links"], [{
            "CONUM": 1, "LOCOD": 1, "IDFAS": 13, "Prev_CONUM": 1, "Prev_LOCOD": 1,
            "Prev_IDFAS": 11, "LinkType": 0, "lag_seconds": 30}])

    def test_equal_sequence_parallel_and_inactive_lot_ancestors(self):
        exported = load_tool("export_factory_scenario")
        ops = [
            {"operationId": 101, "orderId": 1, "lotId": 1, "phaseId": 1, "sequence": 10},
            {"operationId": 102, "orderId": 1, "lotId": 1, "phaseId": 2, "sequence": 10},
            {"operationId": 103, "orderId": 1, "lotId": 1, "phaseId": 3, "sequence": 20},
            {"operationId": 201, "orderId": 1, "lotId": 3, "phaseId": 4, "sequence": 10},
        ]
        lots = [
            {"orderId": 1, "lotId": 1, "parentLotId": 2},
            {"orderId": 1, "lotId": 2, "parentLotId": 3},
            {"orderId": 1, "lotId": 3, "parentLotId": None},
        ]
        links = exported.derive_precedences(ops, lots)
        pairs = {(l["predecessorOperationId"], l["successorOperationId"]) for l in links}
        self.assertEqual(pairs, {(101, 103), (102, 103), (103, 201)})
        self.assertNotIn((101, 102), pairs)

    def test_absence_operator_is_anonymized_with_master_reference(self):
        anon = load_tool("anonymize_scenario")
        s = scenario()
        s["operatorAbsences"] = [{"operatorCode": "OP-A", "startAt": "2026-10-20"}]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, mapping, output = (root / n for n in ("source.json", "mapping.json", "output.json"))
            source.write_text(json.dumps(s), encoding="utf-8")
            mapping.write_text(json.dumps({
                "OPERATOR": {"OP-A": "OPE001"},
                "MACHINE": {"MAC-A": "MAC001"}, "ORDER": {"1": "ORD001"},
                "SUPPLIER": {}}), encoding="utf-8")
            anon.main(str(source), str(mapping), str(output))
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(result["operatorAbsences"][0]["operatorCode"], "OPE001")
            self.assertEqual(result["operators"][0]["code"], "OPE001")


if __name__ == "__main__":
    unittest.main()
