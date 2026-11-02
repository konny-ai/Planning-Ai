"""Side-effect-free slot search; only accepted assignments reserve capacity."""
from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timedelta
from planning_factory.domain.models import Alternative, Assignment, Interval, Resource
from .calendars import intersect, merge


def free_intervals(resource: Resource, availability: tuple[Interval, ...], reservations,
                   units: int) -> tuple[Interval, ...]:
    if resource.capacity is None:
        return availability
    if units > resource.capacity:
        return ()
    events = defaultdict(int)
    for segment, used in reservations:
        events[segment.start] += used
        events[segment.end] -= used
    points = sorted(events)
    occupied = []
    load = 0
    for i, point in enumerate(points):
        load += events[point]
        if i + 1 < len(points) and load + units > resource.capacity:
            occupied.append(Interval(point, points[i + 1]))
    from .calendars import subtract
    return subtract(availability, occupied)


def bundle_free(alternative: Alternative, resources: dict, calendars: dict, reservations: dict,
                lower: datetime, upper: datetime, cache: dict | None = None) -> tuple[Interval, ...]:
    """Free intervals of a bundle in [lower, upper).

    ``cache`` may only be shared by calls on the same append-only ``reservations`` (as ``reserve``
    does): a resource's free time is reused until it receives a new reservation.
    """
    if not lower < upper:
        return ()
    result = None
    for d in alternative.demands:
        r = resources[d.resource_id]
        booked = reservations.get(r.id, ())
        key = (r.id, d.units)
        hit = cache.get(key) if cache is not None else None
        if hit is not None and hit[0] == len(booked):
            free = hit[1]
        else:
            free = free_intervals(r, calendars[r.calendar_id], booked, d.units)
            if cache is not None:
                cache[key] = (len(booked), free)
        part = clip(free, lower, upper)
        if result is None:
            # Free time is sorted, disjoint and never touching: trimming the slice equals
            # intersect((lower, upper), free) without rebuilding every interval.
            result = trim(part, lower, upper)
        else:
            result = intersect(result, part)
        if not result:
            break
    return (Interval(lower, upper),) if result is None else result


def clip(free: tuple[Interval, ...], lower: datetime, upper: datetime) -> tuple[Interval, ...]:
    """Part of sorted disjoint ``free`` overlapping [lower, upper): O(log n) instead of a full scan."""
    first = bisect_right(free, lower, key=lambda i: i.end)
    last = bisect_left(free, upper, key=lambda i: i.start)
    return free[first:last]


def trim(part: tuple[Interval, ...], lower: datetime, upper: datetime) -> tuple[Interval, ...]:
    if not part:
        return ()
    if part[0].start < lower or part[-1].end > upper:
        part = list(part)
        if part[0].start < lower:
            part[0] = Interval(lower, part[0].end)
        if part[-1].end > upper:
            part[-1] = Interval(part[-1].start, upper)
        part = tuple(part)
    return part


def select_segments(free, seconds: int, splittable: bool, backward: bool = False,
                    exact_start: datetime | None = None) -> tuple[Interval, ...] | None:
    if seconds <= 0:
        return None
    candidates = tuple(reversed(free)) if backward else free
    remaining = seconds
    selected = []
    for interval in candidates:
        if exact_start is not None and not selected and interval.start != exact_start:
            return None  # free intervals already clipped at the requested start
        if not splittable and interval.seconds < remaining:
            if exact_start is not None:
                return None
            continue
        take = min(remaining, interval.seconds)
        segment = (Interval(interval.end - timedelta(seconds=take), interval.end) if backward
                   else Interval(interval.start, interval.start + timedelta(seconds=take)))
        selected.append(segment)
        remaining -= take
        if remaining == 0:
            return tuple(reversed(selected)) if backward else tuple(selected)
    return None


def reserve(assignment: Assignment, reservations: dict) -> None:
    for d in assignment.demands:
        for segment in assignment.segments:
            reservations.setdefault(d.resource_id, []).append((segment, d.units))
