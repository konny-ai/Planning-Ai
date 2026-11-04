"""Incremental free time (cache updated from new reservations) must equal the from-scratch result."""
import random
import unittest
from datetime import timedelta

from planning_factory.domain.models import Alternative, Assignment, Demand, Interval, Resource
from planning_factory.engine.booking import bundle_free, free_intervals, reserve
from planning_factory.engine.calendars import expand, intersect
from .helpers import dt, scenario


class IncrementalFreeTimeTests(unittest.TestCase):
    def test_cached_incremental_equals_scratch_over_random_reservation_sequences(self):
        s = scenario([], end=dt(month=11, day=30, hour=0))
        calendar = s.calendars[0]
        availability = expand(calendar, s.start, s.end)
        steps = 0
        for seed in range(400):
            rng = random.Random(seed)
            capacity = rng.choice([1, 2, 3, 10000])
            resources = {'a': Resource('a', 'work', capacity=capacity),
                         'b': Resource('b', 'work', capacity=rng.choice([1, 4, 10000])),
                         'inf': Resource('inf', 'work', capacity=None)}
            calendars = {'work': availability}
            reservations, cache = {}, {}
            demand_sets = [(Demand('a', rng.randint(1, max(1, capacity))),),
                           (Demand('a', 1), Demand('b', 1)),
                           (Demand('b', rng.randint(1, 4)), Demand('inf', 1))]
            for step in range(rng.randint(5, 30)):
                start = dt(day=19) + timedelta(minutes=rng.randint(0, 20 * 24 * 60))
                segment = Interval(start, start + timedelta(minutes=rng.choice([15, 60, 240, 1200, 3000])))
                chosen = rng.choice(demand_sets)
                used = [Demand(d.resource_id, min(d.units, resources[d.resource_id].capacity or d.units))
                        for d in chosen]
                reserve(Assignment(f't{step}', (), (segment,), tuple(used), 'forward', 'forward'), reservations)
                for demands in demand_sets:                  # query after every append, shared cache
                    lower = dt(day=19) + timedelta(minutes=rng.randint(0, 5 * 24 * 60))
                    upper = lower + timedelta(days=rng.choice([1, 14, 60]))
                    alternative = Alternative(demands)
                    cached = bundle_free(alternative, resources, calendars, reservations, lower, upper, cache)
                    scratch = bundle_free(alternative, resources, calendars, reservations, lower, upper)
                    with self.subTest(seed=seed, step=step):
                        self.assertEqual(cached, scratch)
                    steps += 1
                for (rid, units), (version, free) in [(k, v) for k, v in cache.items() if k[0] != 'load']:
                    if version == len(reservations.get(rid, ())):   # the whole cached free time, not a window
                        self.assertEqual(free, free_intervals(
                            resources[rid], availability, reservations.get(rid, ()), units))
        self.assertGreater(steps, 3000)

    def test_cache_stays_usable_when_a_resource_gets_a_first_reservation_later(self):
        s = scenario([], end=dt(month=11, day=30, hour=0))
        availability = expand(s.calendars[0], s.start, s.end)
        resources = {'a': Resource('a', 'work', capacity=1)}
        reservations, cache = {}, {}
        alternative = Alternative((Demand('a', 1),))
        lower, upper = s.start, s.end
        before = bundle_free(alternative, resources, {'work': availability}, reservations, lower, upper, cache)
        self.assertEqual(before, availability)
        segment = Interval(dt(day=20, hour=9), dt(day=20, hour=11))
        reserve(Assignment('t', (), (segment,), (Demand('a', 1),), 'forward', 'forward'), reservations)
        after = bundle_free(alternative, resources, {'work': availability}, reservations, lower, upper, cache)
        self.assertEqual(after, bundle_free(alternative, resources, {'work': availability}, reservations, lower, upper))
        self.assertNotIn(segment.start, [i.start for i in intersect(after, (segment,))])


if __name__ == '__main__':
    unittest.main()
