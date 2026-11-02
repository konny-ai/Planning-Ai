"""Regression contracts for file-only what-if actions (NOT executed)."""
from datetime import UTC
import unittest

from planning_factory.adapters.dashboard_simulation import apply_whatif_edits
from planning_factory.domain.models import Nesting, Resource
from planning_factory.domain.validation import ValidationError, compile_tasks
from planning_factory.engine.scheduler import plan
from .helpers import dt, op, scenario


class DashboardWhatIfTests(unittest.TestCase):
    def setUp(self):
        self.source = scenario((op('a'), op('b')))

    def test_pinned_start_is_immutable_and_preserves_original(self):
        edits = {'fixed_tasks':[{
            'task_id':'job:a','start':dt().isoformat(),'alternative':0,
        }], 'closures':[]}
        result=apply_whatif_edits(self.source,edits)
        self.assertEqual(result.operations[0].fixed_start,dt())
        self.assertEqual(result.operations[0].fixed_alternative,0)
        self.assertIsNone(self.source.operations[0].fixed_start)
        self.assertEqual(compile_tasks(result)[0]['job:a'].fixed_start,dt())

    def test_nesting_start_is_fixed_for_every_member_and_planned_there(self):
        src = scenario((op('a'), op('b')), nestings=(Nesting('N', ('a', 'b'), 3600),))
        at = dt(hour=11)
        result = apply_whatif_edits(src, {'fixed_tasks': [{'task_id': 'nest:N', 'start': at.isoformat(), 'alternative': 0}]})
        self.assertTrue(all(o.fixed_start == at and o.fixed_alternative == 0 for o in result.operations))
        self.assertEqual(at, plan(result).assignments[0].start)
        self.assertTrue(all(o.fixed_start is None for o in src.operations))
        with self.assertRaises(ValidationError):
            apply_whatif_edits(src, {'fixed_tasks': [{'task_id': 'nest:N', 'start': at.isoformat(), 'alternative': 3}]})

    def test_downtime_closes_only_named_resource_with_shared_calendar(self):
        src=scenario((op('a'),),resources=(Resource('m','work'),Resource('m2','work')))
        result=apply_whatif_edits(src,{
            'fixed_tasks':[],
            'closures':[{
                'resource_id':'m','start':dt(hour=8).isoformat(),
                'end':dt(hour=12).isoformat(),'reason':'maintenance',
            }],
        })
        r={x.id:x for x in result.resources}
        self.assertNotEqual(r['m'].calendar_id,r['m2'].calendar_id)
        self.assertEqual(r['m2'].calendar_id,'work')
        self.assertEqual(len(result.calendars),2)
        self.assertEqual(len(src.calendars),1)
        self.assertEqual(result.calendars[1].closures[0].seconds,4*3600)

    def test_invalid_or_historical_edits_rejected_without_changes(self):
        for edits in (
            {'fixed_tasks':[{'task_id':'job:unknown','start':dt().isoformat(),'alternative':0}]},
            {'fixed_tasks':[{'task_id':'job:a','start':dt().isoformat(),'alternative':12}]},
            {'closures':[{'resource_id':'m','start':dt(hour=12).isoformat(),
                          'end':dt(hour=8).isoformat(),'reason':'maintenance'}]},
            {'closures':[{'resource_id':'m','start':dt(hour=8).isoformat(),
                          'end':dt(hour=12).isoformat(),'reason':''}]},
        ):
            with self.subTest(edits=edits),self.assertRaises(ValidationError):
                apply_whatif_edits(self.source,edits)
        self.assertIsNone(self.source.operations[0].fixed_start)
        self.assertEqual(self.source.calendars[0].closures,())


if __name__=='__main__':
    unittest.main()
