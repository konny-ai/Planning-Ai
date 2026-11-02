"""#89: verified plan -> MS Project XML (MSPDI), manually scheduled tasks with the plan's dates."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree

from planning_factory.adapters.json_io import dumps, loads
from planning_factory.adapters.msproject import mspdi
from planning_factory.cli import main
from planning_factory.domain.models import Dependency
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan

ROOT = Path(__file__).resolve().parents[1]
WORKSHOP = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
NS = {'p': 'http://schemas.microsoft.com/project'}


def parse(xml):
    root = ElementTree.fromstring(xml)
    tasks = {t.find('p:UID', NS).text: t for t in root.find('p:Tasks', NS)}
    return root, tasks


def by_name(tasks, name):
    return next(t for t in tasks.values() if t.find('p:Name', NS).text == name)


def text(task, tag):
    node = task.find(f'p:{tag}', NS)
    return node.text if node is not None else None


class MsProjectTests(unittest.TestCase):
    def setUp(self):
        self.plan = plan(WORKSHOP)
        self.xml = mspdi(WORKSHOP, self.plan)
        self.root, self.tasks = parse(self.xml)

    def test_one_summary_per_order_and_one_task_per_plan_unit(self):
        summaries = [text(t, 'Name') for t in self.tasks.values() if text(t, 'Summary') == '1']
        self.assertEqual(['Commessa A', 'Commessa B', 'Commessa C', 'Nesting multicommessa'], summaries)
        units = [t for t in self.tasks.values() if text(t, 'Summary') == '0']
        self.assertEqual(len(self.plan.assignments) + len(self.plan.unscheduled), len(units))
        self.assertTrue(all(text(t, 'Manual') == '1' for t in self.tasks.values()))

    def test_dates_are_the_plan_dates_in_local_time(self):
        piega = by_name(self.tasks, 'ord-a/piega')
        a = next(x for x in self.plan.assignments if x.task_id == 'job:ord-a/piega')
        self.assertEqual(datetime(2026, 10, 20, 8, tzinfo=UTC), a.start)
        self.assertEqual(('2026-10-20T10:00:00', '2026-10-20T12:00:00', 'PT2H0M0S'),
                         (text(piega, 'Start'), text(piega, 'Finish'), text(piega, 'Duration')))
        winter = mspdi(WORKSHOP, self.plan, timezone='UTC')
        self.assertEqual('2026-10-20T08:00:00', text(by_name(parse(winter)[1], 'ord-a/piega'), 'Start'))

    def test_multi_order_nest_links_history_and_interruptions(self):
        nest = by_name(self.tasks, 'ord-a/taglio + ord-b/taglio')
        self.assertIn('Commesse: A, B', text(nest, 'Notes'))
        piega = by_name(self.tasks, 'ord-a/piega')
        link = piega.find('p:PredecessorLink', NS)
        self.assertEqual((text(nest, 'UID'), '1', '4'), (link.find('p:PredecessorUID', NS).text,
                                                        link.find('p:Type', NS).text, link.find('p:LagFormat', NS).text))
        history = by_name(self.tasks, 'ord-a/materiale')
        self.assertEqual(('1', '100'), (text(history, 'Milestone'), text(history, 'PercentComplete')))
        welding = by_name(self.tasks, 'ord-a/saldatura')
        self.assertIn('Interrotta:', text(welding, 'Notes'))
        self.assertEqual('PT5H30M0S', text(welding, 'Duration'))  # booked work, not the calendar span

    def test_resources_and_assignments(self):
        resources = {r.find('p:UID', NS).text: r.find('p:Name', NS).text for r in self.root.find('p:Resources', NS)}
        self.assertIn('LASER-1', resources.values())
        assignments = list(self.root.find('p:Assignments', NS))
        self.assertEqual(sum(len(a.demands) for a in self.plan.assignments), len(assignments))
        task_uids = set(self.tasks)
        self.assertTrue(all(x.find('p:TaskUID', NS).text in task_uids and x.find('p:ResourceUID', NS).text in resources
                            for x in assignments))

    def test_unscheduled_units_are_flagged_not_dated_as_planned(self):
        ops = [replace(o, release=datetime(2026, 10, 25, 23, tzinfo=UTC)) if o.id == 'ord-c/urgente' else o
               for o in WORKSHOP.operations]
        ops = [replace(o, fixed_start=None, fixed_alternative=None) if o.id == 'ord-c/urgente' else o for o in ops]
        scenario = replace(WORKSHOP, operations=tuple(ops))
        p = plan(scenario)
        self.assertTrue(p.unscheduled)
        _, tasks = parse(mspdi(scenario, p))
        flagged = by_name(tasks, '[NON PIANIFICATA] ord-c/urgente')
        self.assertIn('Non pianificata: NO_FEASIBLE_SLOT', text(flagged, 'Notes'))
        self.assertTrue(any(text(t, 'Name') == 'Non pianificate' for t in tasks.values()))

    def test_lag_is_exported_in_elapsed_tenths_of_minutes(self):
        ops = [replace(o, predecessors=(Dependency('ord-a/piega', 'FS', 3610),)) if o.id == 'ord-b/piega' else o
               for o in WORKSHOP.operations]
        scenario = replace(WORKSHOP, operations=tuple(ops))
        _, tasks = parse(mspdi(scenario, plan(scenario)))
        task = by_name(tasks, 'ord-b/piega')
        link = next(l for l in task.findall('p:PredecessorLink', NS)
                    if l.find('p:PredecessorUID', NS).text == text(by_name(tasks, 'ord-a/piega'), 'UID'))
        self.assertEqual('601', link.find('p:LinkLag', NS).text)
        self.assertIn('arrotondato per difetto: 3610s → 3606s', text(task, 'Notes'))

    def test_deterministic_and_bound_to_its_scenario(self):
        self.assertEqual(self.xml, mspdi(WORKSHOP, self.plan))
        other = replace(WORKSHOP, id='altro')
        with self.assertRaises(ValidationError):
            mspdi(other, self.plan)
        with self.assertRaises(ValidationError):
            mspdi(WORKSHOP, self.plan, timezone='Mars/Base')

    def test_cli_verifies_the_plan_before_exporting(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan_path, out = Path(tmp, 'plan.json'), Path(tmp, 'plan.xml')
            plan_path.write_text(dumps(self.plan), encoding='utf-8')
            stdout = io.StringIO()
            with redirect_stdout(stdout):
                self.assertEqual(0, main(['export-msproject', str(ROOT / 'examples' / 'workshop.json'), str(plan_path),
                                          '-o', str(out), '--title', 'Officina']))
            self.assertEqual('Officina', parse(out.read_text(encoding='utf-8'))[0].find('p:Title', NS).text)
            self.assertEqual(self.plan.scenario_digest, json.loads(stdout.getvalue())['scenario_digest'])
            tampered = json.loads(plan_path.read_text(encoding='utf-8'))
            tampered['assignments'][1]['segments'][0]['start'] = '2026-10-19T00:00:00Z'
            plan_path.write_text(json.dumps(tampered), encoding='utf-8')
            with redirect_stderr(io.StringIO()):
                self.assertEqual(1, main(['export-msproject', str(ROOT / 'examples' / 'workshop.json'), str(plan_path),
                                          '-o', str(out)]))


if __name__ == '__main__':
    unittest.main()
