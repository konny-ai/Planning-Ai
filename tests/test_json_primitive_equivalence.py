"""The faster primitive()/dumps() must produce exactly the old bytes (digests depend on it)."""
import importlib.util
import json
import random
import unittest
from datetime import datetime, UTC
from pathlib import Path

from planning_factory.adapters import json_io
from planning_factory.engine.scheduler import plan
from planning_factory.domain.models import Operation, Settings
from .helpers import alt, dt
from .test_incremental_plan import random_scenario
from .test_setup_stage_fuzz import build

REFERENCE = Path(__file__).with_name('reference_json_io.py')


def reference_module():
    """The original implementation, frozen in tests/reference_json_io.py."""
    spec = importlib.util.spec_from_file_location('planning_factory.adapters.reference_json_io', REFERENCE)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = 'planning_factory.adapters'
    spec.loader.exec_module(module)
    return module


class PrimitiveEquivalenceTests(unittest.TestCase):
    def test_same_bytes_on_scenarios_plans_and_edge_values(self):
        ref = reference_module()
        objects = []
        for seed in range(150):
            objects.append(random_scenario(random.Random(seed)))
            objects.append(build(random.Random(1000 + seed))[0])
        for seed in range(40):
            objects.append(plan(random_scenario(random.Random(seed))))
        objects += [Settings(), Settings(auto_rule='slack', auto_slack_seconds=5),
                    Operation('x', 'o', 3600, (alt('m'),), external_end=None, preferred_alternative=0),
                    Operation('y', 'o', 0, (), external_end=dt(day=20)), dt(day=19), {'b': (1, 2.5, None, True), 'a': [dt()]}]
        for i, value in enumerate(objects):
            with self.subTest(i=i):
                self.assertEqual(json_io.dumps(value), ref.dumps(value))
        self.assertEqual(json_io.scenario_digest(objects[0]), ref.scenario_digest(objects[0]))

    def test_scalar_fast_path_does_not_change_subclass_or_naive_handling(self):
        ref = reference_module()
        class Text(str):
            pass
        for value in (Text('abc'), 3, 3.0, True, None, 'é'):
            self.assertEqual(json_io.primitive(value), ref.primitive(value))
        naive = datetime(2026, 1, 1)
        with self.assertRaises(Exception) as new:
            json_io.primitive(naive)
        with self.assertRaises(Exception) as old:
            ref.primitive(naive)
        self.assertEqual(type(new.exception), type(old.exception))


if __name__ == '__main__':
    unittest.main()
