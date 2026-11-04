"""Calendar expansion and interval algebra, using UTC elapsed working seconds."""
from bisect import bisect_right
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


def _interval_end(item):
    return item.end


def _subtract_sorted(intervals, cuts) -> tuple[Interval, ...]:
    """``intervals`` sorted and disjoint, ``cuts`` merged and sorted.

    Walks the cuts, not the intervals: the stretches between two cuts are copied in
    bulk (bisect + slice), so the cost follows the number of cuts. Same result as a
    linear sweep over every interval.
    """
    n = len(intervals)
    result = []
    pos = 0
    start = None      # start of what is left of intervals[pos] after the cuts seen so far
    for cut in cuts:
        j = bisect_right(intervals, cut.start, lo=pos, key=_interval_end)
        if j > pos:   # intervals[pos:j] end before this cut: untouched (bar a pending remainder)
            if start is not None:
                result.append(Interval(start, intervals[pos].end))
                result.extend(intervals[pos + 1:j])
            else:
                result.extend(intervals[pos:j])
            pos, start = j, None
        while pos < n and intervals[pos].start < cut.end:
            item = intervals[pos]
            begin = item.start if start is None else start
            if begin < cut.start:
                result.append(Interval(begin, cut.start))
            if cut.end < item.end:
                start = cut.end   # a remainder is left: a later cut may still split it
                break
            pos += 1
            start = None
    if start is not None:
        result.append(Interval(start, intervals[pos].end))
        pos += 1
    result.extend(intervals[pos:])
    return tuple(result)


def subtract(intervals, closures) -> tuple[Interval, ...]:
    cuts = merge(closures)
    if all(a.end <= b.start for a, b in zip(intervals, intervals[1:])):
        # Sorted disjoint input (calendars, free time).
        return _subtract_sorted(intervals, cuts)
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
