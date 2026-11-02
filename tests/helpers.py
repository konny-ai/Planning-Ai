from datetime import UTC, datetime, time
from planning_factory.domain.models import Alternative, Calendar, Demand, Operation, Resource, Scenario, Shift


def dt(day=19, hour=8, minute=0, month=10, year=2026):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def alt(*ids):
    return Alternative(tuple(Demand(i) for i in ids))


def op(id='a', hours=1, resource='m', **kw):
    return Operation(id, kw.pop('order_id', 'order'), int(hours * 3600), kw.pop('alternatives', (alt(resource),)), **kw)


def scenario(operations=(), **kw):
    c = Calendar('work', 'UTC', tuple(Shift(d, time(8), time(12)) for d in range(5)) +
                 tuple(Shift(d, time(13), time(17)) for d in range(5)))
    return Scenario(kw.pop('id', 'test'), kw.pop('start', dt(hour=0)), kw.pop('end', dt(24, 0)),
                    kw.pop('calendars', (c,)), kw.pop('resources', (Resource('m', 'work'),)),
                    tuple(operations), **kw)
