from dataclasses import replace
import unittest
from planning_factory.adapters.factory import identity, import_snapshot
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from .helpers import scenario, dt


def row(c=1, lot=1, phase=1, sequence=10, **kw):
    return dict(CONUM=c, LOCOD=lot, IDFAS=phase, FASEQ=sequence, FAHLV=0, FASLV='12.5', FAHAT=0,
                FASAT=60, LAQTP=10, resource_bundles=[['m']], **kw)


class FactoryTests(unittest.TestCase):
    def test_theoretical_seconds_preserve_decimal(self):
        s = import_snapshot(scenario(), {'phases': [row()]})
        self.assertEqual(s.operations[0].duration_seconds, 125)
        self.assertEqual(s.operations[0].setup_seconds, 60)
        self.assertEqual(plan(s).assignments[0].segments[0].seconds, 185)

    def test_same_phase_id_different_lots_is_unique(self):
        s = import_snapshot(scenario(), {'phases': [row(lot=1), row(lot=2)]})
        self.assertEqual(len({o.id for o in s.operations}), 2)

    def test_phase_sequence_and_explicit_ss(self):
        s = import_snapshot(scenario(), {'phases': [row(phase=1), row(phase=2, sequence=20), row(lot=2)],
             'links': [dict(CONUM=1, LOCOD=2, IDFAS=1, Prev_CONUM=1, Prev_LOCOD=1, Prev_IDFAS=1, LinkType=1)]})
        a = next(o for o in s.operations if o.id == '1/1/2')
        b = next(o for o in s.operations if o.id == '1/2/1')
        self.assertEqual(a.predecessors[0].kind, 'FS')
        self.assertEqual(b.predecessors[0].kind, 'SS')

    def test_parent_lot_and_self_parent_root(self):
        s = import_snapshot(scenario(), {'phases': [row(lot=1), row(lot=2)],
             'lots': [dict(CONUM=1, LOCOD=1, LOCLP=1), dict(CONUM=1, LOCOD=2, LOCLP=1)]})
        parent = next(o for o in s.operations if o.id == '1/1/1')
        self.assertEqual(parent.predecessors[0].predecessor_id, '1/2/1')
        self.assertEqual(len(plan(s).assignments), 2)

    def test_parent_cycle_rejected(self):
        with self.assertRaises(ValidationError):
            import_snapshot(scenario(), {'phases': [row()], 'lots': [dict(CONUM=1, LOCOD=1, LOCLP=2), dict(CONUM=1, LOCOD=2, LOCLP=1)]})

    def test_duplicate_identity_rejected(self):
        with self.assertRaises(ValidationError):
            import_snapshot(scenario(), {'phases': [row(), row()]})

    def test_unknown_external_predecessor_rejected(self):
        with self.assertRaises(ValidationError):
            import_snapshot(scenario(), {'phases': [row()], 'links': [dict(CONUM=1, LOCOD=1, IDFAS=1, Prev_CONUM=1, Prev_LOCOD=2, Prev_IDFAS=1, LinkType=0)]})

    def test_progress_quantity(self):
        s = import_snapshot(scenario(), {'phases': [row(produced_quantity=4)]}, progress_policy='quantity')
        self.assertEqual(s.operations[0].duration_seconds, 75)

    def test_priority_fields(self):
        s = import_snapshot(scenario(), {'phases': [row(LOPRI=2, LAPRI=-10, CustomerPriority=5)]})
        self.assertEqual((s.operations[0].priority, s.operations[0].phase_priority, s.operations[0].customer_priority), (2, -10, 5))

    def test_no_guess_on_resource_compatibility(self):
        data = row()
        data['resource_bundles'] = [['missing']]
        with self.assertRaises(ValidationError):
            import_snapshot(scenario(), {'phases': [data]})

    def test_zero_work_skipping_is_explicit_and_bridges_links(self):
        inactive = row(phase=2, sequence=20)
        inactive.update(FASLV=0, FASAT=0, LAQTP=0)
        links = [
            dict(CONUM=1, LOCOD=1, IDFAS=2,
                 Prev_CONUM=1, Prev_LOCOD=1, Prev_IDFAS=1,
                 LinkType=0, lag_seconds=7),
            dict(CONUM=1, LOCOD=2, IDFAS=3,
                 Prev_CONUM=1, Prev_LOCOD=1, Prev_IDFAS=2,
                 LinkType=1, lag_seconds=3),
        ]
        snap = {'phases': [row(), inactive, row(lot=2, phase=3)], 'links': links}
        with self.assertRaises(ValidationError):
            import_snapshot(scenario(), snap)
        converted = import_snapshot(scenario(), snap, zero_phase_policy='skip')
        self.assertEqual({o.id for o in converted.operations}, {'1/1/1', '1/2/3'})
        target = next(o for o in converted.operations if o.id == '1/2/3')
        self.assertEqual([(p.predecessor_id, p.kind, p.lag_seconds) for p in target.predecessors],
                         [('1/1/1', 'FS', 10)])
        self.assertEqual(len(plan(converted).assignments), 2)

    def test_zero_work_skipping_validates_cycles_and_duplicate_ids(self):
        inactive = row(phase=2, sequence=20)
        inactive.update(FASLV=0, FASAT=0, LAQTP=0)
        with self.assertRaises(ValidationError):
            import_snapshot(scenario(), {'phases': [inactive, inactive]}, zero_phase_policy='skip')
        links = [
            dict(CONUM=1, LOCOD=1, IDFAS=2,
                 Prev_CONUM=1, Prev_LOCOD=1, Prev_IDFAS=2, LinkType=0),
            dict(CONUM=1, LOCOD=2, IDFAS=3,
                 Prev_CONUM=1, Prev_LOCOD=1, Prev_IDFAS=2, LinkType=0),
        ]
        with self.assertRaises(ValidationError):
            import_snapshot(scenario(), {'phases': [inactive, row(lot=2, phase=3)],
                                         'links': links}, zero_phase_policy='skip')
        with self.assertRaises(ValidationError):
            import_snapshot(scenario(), {'phases': [row()]}, zero_phase_policy='invent')

    def test_identity_escaping(self):
        self.assertNotEqual(identity('a/b', 'c', 'd'), identity('a', 'b/c', 'd'))
