"""subtract() must equal the plain definition on any input (the fast path is exact)."""
import importlib.util
import random
import unittest
from datetime import datetime, timedelta, UTC

from planning_factory.domain.models import Interval
from planning_factory.engine.calendars import subtract

T0 = datetime(2026, 10, 19, tzinfo=UTC)


def reference(intervals, closures):
    """The definition: remove every closure from every interval, by repeated cutting."""
    result = list(intervals)
    for cut in sorted(closures):
        if cut.start >= cut.end:
            continue
        nxt = []
        for item in result:
            if cut.end <= item.start or cut.start >= item.end:
                nxt.append(item)
                continue
            if item.start < cut.start:
                nxt.append(Interval(item.start, cut.start))
            if cut.end < item.end:
                nxt.append(Interval(cut.end, item.end))
        result = nxt
    return tuple(result)


def at(minutes):
    return T0 + timedelta(minutes=minutes)


class SubtractTests(unittest.TestCase):
    def test_matches_the_definition_on_random_inputs(self):
        for seed in range(3000):
            rng = random.Random(seed)
            cursor, intervals = 0, []
            for _ in range(rng.randint(0, 25)):
                cursor += rng.choice([0, 0, 1, 5, 60])
                length = rng.randint(1, 120)
                intervals.append(Interval(at(cursor), at(cursor + length)))
                cursor += length
            closures = [Interval(at(a), at(a + rng.randint(0, 200)))
                        for a in (rng.randint(-20, cursor + 20) for _ in range(rng.randint(0, 20)))]
            with self.subTest(seed=seed):
                self.assertEqual(subtract(tuple(intervals), closures), reference(intervals, closures))

    def test_edge_cases(self):
        whole = (Interval(at(0), at(100)),)
        self.assertEqual(subtract(whole, []), whole)
        self.assertEqual(subtract(whole, [Interval(at(0), at(100))]), ())
        self.assertEqual(subtract(whole, [Interval(at(-5), at(500))]), ())
        self.assertEqual(subtract(whole, [Interval(at(40), at(60))]),
                         (Interval(at(0), at(40)), Interval(at(60), at(100))))
        self.assertEqual(subtract(whole, [Interval(at(10), at(20)), Interval(at(20), at(30))]),
                         (Interval(at(0), at(10)), Interval(at(30), at(100))))   # touching cuts merge
        two = (Interval(at(0), at(50)), Interval(at(50), at(100)))
        self.assertEqual(subtract(two, [Interval(at(40), at(60))]),
                         (Interval(at(0), at(40)), Interval(at(60), at(100))))
        self.assertEqual(subtract((), [Interval(at(0), at(5))]), ())
        unsorted = (Interval(at(50), at(100)), Interval(at(0), at(60)))    # overlapping, unsorted
        self.assertEqual(subtract(unsorted, [Interval(at(55), at(58))]),
                         reference(unsorted, [Interval(at(55), at(58))]))


if __name__ == '__main__':
    unittest.main()
