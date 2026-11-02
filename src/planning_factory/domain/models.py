"""Immutable, timezone-aware planning contracts. Durations use integer seconds."""
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Literal

Mode = Literal['forward', 'backward', 'auto']


@dataclass(frozen=True, order=True)
class Interval:
    start: datetime
    end: datetime

    @property
    def seconds(self) -> int:
        return int((self.end.astimezone(UTC) - self.start.astimezone(UTC)).total_seconds())


@dataclass(frozen=True)
class Shift:
    weekday: int  # Monday=0
    start: time
    end: time  # end <= start means next day, including a 24h shift


@dataclass(frozen=True)
class CalendarException:
    day: date
    shifts: tuple[tuple[time, time], ...] = ()  # replaces shifts STARTING this day


@dataclass(frozen=True)
class Calendar:
    id: str
    timezone: str
    shifts: tuple[Shift, ...]
    holidays: tuple[date, ...] = ()
    exceptions: tuple[CalendarException, ...] = ()
    # Absolute closures also cut the carry-over from a previous night's shift.
    closures: tuple[Interval, ...] = ()


@dataclass(frozen=True)
class Resource:
    id: str
    calendar_id: str
    capacity: int | None = 1  # None = infinite, still respects its calendar
    kind: str = 'machine'
    efficiency_bp: int = 10000  # basis points; planned time = standard time / efficiency (#78 P9)


@dataclass(frozen=True, order=True)
class Demand:
    resource_id: str
    units: int = 1


@dataclass(frozen=True)
class Alternative:
    demands: tuple[Demand, ...]


@dataclass(frozen=True)
class Dependency:
    predecessor_id: str
    kind: Literal['FS', 'SS'] = 'FS'
    lag_seconds: int = 0  # elapsed seconds, not calendar working time
    max_lag_seconds: int | None = None  # FS only: longest acceptable wait, reported not enforced (#78 P6)


@dataclass(frozen=True)
class Operation:
    id: str
    order_id: str
    duration_seconds: int  # remaining processing time, excludes setup
    alternatives: tuple[Alternative, ...]
    setup_seconds: int = 0
    predecessors: tuple[Dependency, ...] = ()
    release: datetime | None = None
    due: datetime | None = None
    priority: int = 0  # smaller values first
    customer_priority: int = 0
    phase_priority: int = 0
    mode: Mode | None = None
    splittable: bool = False  # same resource bundle across all fragments
    fixed_start: datetime | None = None
    fixed_alternative: int | None = None
    completed_at: datetime | None = None
    external_end: datetime | None = None  # stated supplier finish, NOT actual completion
    preferred_alternative: int | None = None  # kept unless another finishes earlier by the switch gain (#78 P10)

    @property
    def total_seconds(self) -> int:
        return self.duration_seconds + self.setup_seconds


@dataclass(frozen=True)
class Nesting:
    id: str
    operation_ids: tuple[str, ...]
    duration_seconds: int
    setup_seconds: int = 0
    # The shared workplan replaces per-member durations, once for the whole nest.


@dataclass(frozen=True)
class Settings:
    mode: Mode = 'forward'
    auto_backward_days: int = 10
    priority_weight: int = 10000
    due_weight: int = 1
    customer_weight: int = 1
    alternative_switch_min_gain_seconds: int = 0  # 0 = always take the earliest alternative (#78 P10)
    auto_rule: Literal['due_distance', 'slack'] = 'due_distance'  # how 'auto' picks backward (#78 P4)
    auto_slack_seconds: int = 0  # slack rule: backward only above this estimated margin


@dataclass(frozen=True)
class Scenario:
    id: str
    start: datetime
    end: datetime
    calendars: tuple[Calendar, ...]
    resources: tuple[Resource, ...]
    operations: tuple[Operation, ...]
    nestings: tuple[Nesting, ...] = ()
    settings: Settings = Settings()
    schema_version: int = 1


@dataclass(frozen=True)
class Task:
    id: str
    operation_ids: tuple[str, ...]
    duration_seconds: int
    alternatives: tuple[Alternative, ...]
    release: datetime
    due: datetime | None
    priority: int
    customer_priority: int
    phase_priority: int
    mode: Mode
    splittable: bool
    fixed_start: datetime | None
    fixed_alternative: int | None
    completed_at: datetime | None
    external_end: datetime | None = None
    preferred_bundle: tuple[Demand, ...] | None = None


@dataclass(frozen=True)
class Edge:
    predecessor: str
    successor: str
    kind: str
    lag_seconds: int


@dataclass(frozen=True)
class Assignment:
    task_id: str
    operation_ids: tuple[str, ...]
    segments: tuple[Interval, ...]
    demands: tuple[Demand, ...]
    requested_mode: str
    effective_mode: str
    fixed: bool = False
    completed_at: datetime | None = None
    explanation: tuple[str, ...] = ()
    external_end: datetime | None = None

    @property
    def start(self) -> datetime:
        return self.segments[0].start if self.segments else (self.completed_at or self.external_end)

    @property
    def end(self) -> datetime:
        return self.segments[-1].end if self.segments else (self.completed_at or self.external_end)


@dataclass(frozen=True)
class Unscheduled:
    task_id: str
    operation_ids: tuple[str, ...]
    code: str
    detail: str


@dataclass(frozen=True)
class ResourceLoad:
    resource_id: str
    available_unit_seconds: int | None
    booked_unit_seconds: int
    utilization: float | None


@dataclass(frozen=True)
class OrderResult:
    order_id: str
    end: datetime | None
    due: datetime | None
    tardiness_seconds: int | None
    complete: bool


@dataclass(frozen=True)
class Plan:
    scenario_id: str
    scenario_digest: str
    assignments: tuple[Assignment, ...]
    unscheduled: tuple[Unscheduled, ...]
    resources: tuple[ResourceLoad, ...]
    orders: tuple[OrderResult, ...]
    engine_version: str = '0.2.0'
    schema_version: int = 1
