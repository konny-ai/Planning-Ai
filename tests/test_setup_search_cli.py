"""CLI contract for bounded setup-search outputs and input protection."""
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import unittest

from planning_factory.adapters.json_io import dumps, loads, plan_loads
from planning_factory.cli import main
from planning_factory.engine.setup_audit import audit_setups
from planning_factory.engine.verification import verify_plan
from planning_factory.domain.models import Alternative, Demand, Resource
from .helpers import op, scenario


class SetupSearchCliTests(unittest.TestCase):
    def test_writes_matching_scenario_and_verified_plan(self):
        s = scenario([op('a', alternatives=(Alternative((Demand('m1'),)), Alternative((Demand('m2'),)))),
                      op('b', alternatives=(Alternative((Demand('m1'),)), Alternative((Demand('m2'),))))],
                     resources=(Resource('m1','work'), Resource('m2','work')))
        rules = {'schema_version':1,'families':{'a':'A','b':'B'},
                 'machines':[{'resource_id':'m1','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':0},{'from_family':'B','to_family':'A','seconds':0}]},
                             {'resource_id':'m2','initial_family':'A','transitions':[{'from_family':'A','to_family':'B','seconds':0},{'from_family':'B','to_family':'A','seconds':0}]}]}
        with TemporaryDirectory() as tmp:
            root=Path(tmp); src=root/'scenario.json'; matrix=root/'rules.json'
            out=root/'plan.json'; adjusted=root/'adjusted.json'
            src.write_text(dumps(s),encoding='utf-8'); matrix.write_text(json.dumps(rules),encoding='utf-8')
            stdout=StringIO()
            with redirect_stdout(stdout):
                code=main(['setup-search',str(src),str(matrix),'-o',str(out),'--scenario-output',str(adjusted),'--budget','4','--repair-budget','4'])
            self.assertEqual(code,0)
            scenario_out=loads(adjusted.read_text(encoding='utf-8')); result=plan_loads(out.read_text(encoding='utf-8'))
            self.assertEqual(verify_plan(scenario_out,result),())
            self.assertTrue(audit_setups(scenario_out,result,rules).valid)
            report=json.loads(stdout.getvalue())
            self.assertTrue(report['setup_audit']['valid'])
            self.assertLessEqual(report['candidates_evaluated'],4)
            original=src.read_text(encoding='utf-8')
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                code=main(['setup-search',str(src),str(matrix),'-o',str(src),'--scenario-output',str(adjusted)])
            self.assertEqual(code,1)
            self.assertEqual(src.read_text(encoding='utf-8'),original)
