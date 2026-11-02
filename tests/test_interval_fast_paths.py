"""Fast interval paths (linear subtract, clipped/trimmed bundle free time, free-time cache)
must return exactly what the reference algorithms return."""
import random
import unittest
from datetime import timedelta

from planning_factory.domain.models import Alternative, Demand, Interval, Resource
from planning_factory.engine.booking import bundle_free, free_intervals
from planning_factory.engine.calendars import intersect, merge, subtract
from .helpers import dt


def reference_subtract(intervals, closures):
    result = list(intervals)
    for cut in merge(closures):
        nxt = []
        for item in result:
            if cut.end <= item.start or cut.start >= item.end:
                nxt.append(item)
            else:
                if item.start < cut.start:
                    nxt.append(Interval(item.start, cut.start))
                if cut.end < item.end:
                    nxt.append(Interval(cut.end, item.end))
        result = nxt
    return tuple(result)


def intervals(rng, n, base=dt()):
    points = sorted(rng.sample(range(0, 5000), n * 2))
    return [Interval(base + timedelta(minutes=a), base + timedelta(minutes=b)) for a, b in zip(points[::2], points[1::2])]


class IntervalFastPathTests(unittest.TestCase):
    def test_linear_subtract_matches_reference(self):
        rng = random.Random(7)
        for _ in range(500):
            items = merge(intervals(rng, rng.randint(0, 12)))
            cuts = intervals(rng, rng.randint(0, 6)) + intervals(rng, rng.randint(0, 6))
            self.assertEqual(subtract(items, cuts), reference_subtract(items, cuts))

    def test_bundle_free_matches_full_intersection_with_and_without_cache(self):
        rng = random.Random(11)
        base = dt()
        resources = {'a': Resource('a', 'c1', 1), 'b': Resource('b', 'c2', 2), 'x': Resource('x', 'c1', None)}
        for _ in range(300):
            calendars = {'c1': merge(intervals(rng, 15)), 'c2': merge(intervals(rng, 15))}
            reservations = {r: [(i, 1) for i in intervals(rng, rng.randint(0, 5))] for r in ('a', 'b')}
            cache = {}
            for _ in range(4):
                demands = tuple(Demand(r) for r in rng.sample(['a', 'b', 'x'], rng.randint(1, 3)))
                lower = base + timedelta(minutes=rng.randint(0, 4000))
                upper = lower + timedelta(minutes=rng.randint(-10, 3000))
                expected = (Interval(lower, upper),) if lower < upper else ()
                for d in demands:
                    r = resources[d.resource_id]
                    expected = intersect(expected, free_intervals(r, calendars[r.calendar_id],
                                                                  reservations.get(r.id, ()), d.units))
                    if not expected:
                        break
                alternative = Alternative(demands)
                self.assertEqual(bundle_free(alternative, resources, calendars, reservations, lower, upper), expected)
                self.assertEqual(bundle_free(alternative, resources, calendars, reservations, lower, upper, cache), expected)
                reservations['a'].append((intervals(rng, 1)[0], 1))   # append-only, as reserve() does


if __name__ == '__main__':
    unittest.main()
