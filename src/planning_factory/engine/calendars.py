"""Calendar expansion and interval algebra, using UTC elapsed working seconds."""
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo
from planning_factory.domain.models import Calendar, Interval
from planning_factory.domain.validation import ValidationError, utc


def merge(intervals) -> tuple[Interval, ...]:
    result = []
    for item in sorted(intervals):
        if item.start >= item.end:
            continue
        if result and item.start <= result[-1].end:
            result[-1] = Interval(result[-1].start, max(result[-1].end, item.end))
        else:
            result.append(item)
    return tuple(result)


def intersect(a, b) -> tuple[Interval, ...]:
    result = []
    i = j = 0
    while i < len(a) and j < len(b):
        start, end = max(a[i].start, b[j].start), min(a[i].end, b[j].end)
        if start < end:
            result.append(Interval(start, end))
        if a[i].end <= b[j].end:
            i += 1
        else:
            j += 1
    return merge(result)


def subtract(intervals, closures) -> tuple[Interval, ...]:
    cuts = merge(closures)
    if all(a.end <= b.start for a, b in zip(intervals, intervals[1:])):
        # Sorted disjoint input (calendars, free time): one linear sweep, same result.
        result, j = [], 0
        for item in intervals:
            start = item.start
            while j < len(cuts) and cuts[j].end <= start:
                j += 1
            k = j
            while k < len(cuts) and cuts[k].start < item.end:
                if start < cuts[k].start:
                    result.append(Interval(start, cuts[k].start))
                start = max(start, cuts[k].end)
                k += 1
            if start < item.end:
                result.append(Interval(start, item.end) if start != item.start else item)
        return tuple(result)
    result = list(intervals)
    for cut in cuts:
        next_result = []
        for item in result:
            if cut.end <= item.start or cut.start >= item.end:
                next_result.append(item)
            else:
                if item.start < cut.start:
                    next_result.append(Interval(item.start, cut.start))
                if cut.end < item.end:
                    next_result.append(Interval(cut.end, item.end))
        result = next_result
    return tuple(result)


def local_instant(naive: datetime, zone: ZoneInfo) -> datetime:
    a = naive.replace(tzinfo=zone, fold=0)
    b = naive.replace(tzinfo=zone, fold=1)
    if a.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValidationError([f'nonexistent calendar endpoint {naive} in {zone.key}'])
    if a.utcoffset() != b.utcoffset():
        raise ValidationError([f'ambiguous calendar endpoint {naive} in {zone.key}; use an unambiguous shift endpoint'])
    return a.astimezone(UTC)


def expand(calendar: Calendar, start: datetime, end: datetime) -> tuple[Interval, ...]:
    zone = ZoneInfo(calendar.timezone)
    start, end = utc(start), utc(end)
    day = start.astimezone(zone).date() - timedelta(days=1)
    last = end.astimezone(zone).date()
    exceptions = {e.day: e.shifts for e in calendar.exceptions}
    holidays = set(calendar.holidays)
    intervals = []
    closures = [Interval(utc(c.start), utc(c.end)) for c in calendar.closures]
    while day <= last:
        if day in holidays:
            lo = local_instant(datetime.combine(day, datetime.min.time()), zone)
            hi = local_instant(datetime.combine(day + timedelta(days=1), datetime.min.time()), zone)
            closures.append(Interval(lo, hi))
            shifts = ()
        else:
            shifts = exceptions.get(day, tuple((s.start, s.end) for s in calendar.shifts if s.weekday == day.weekday()))
        for a, b in shifts:
            following = day + timedelta(days=1) if b <= a else day
            lo = local_instant(datetime.combine(day, a), zone)
            hi = local_instant(datetime.combine(following, b), zone)
            if lo < hi and lo < end and hi > start:
                intervals.append(Interval(max(lo, start), min(hi, end)))
        day += timedelta(days=1)
    return subtract(merge(intervals), closures)
