"""Canonical planning data (ADR 004): records, provenance, issues and the read-only connector contract.

The engine never sees these types. Connectors produce them, the scenario builder
turns them into the existing v1 ``Scenario``. Values are JSON primitives so that the
same data read from different sources normalizes to the same digest.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

SEVERITIES = ('error', 'block', 'warn')  # error: cannot build; block: cannot publish; warn: informative


@dataclass(frozen=True)
class Record:
    entity: str
    values: dict
    original_key: str  # where the row lives in its source (table/key, file#row)


@dataclass(frozen=True)
class Issue:
    severity: str
    code: str
    domain: str
    detail: str
    source: str = ''
    entity: str = ''
    key: str = ''
    original_key: str = ''
    units: tuple[str, ...] = ()  # plan units involved: job:<operation> or nest:<nest>

    def to_dict(self) -> dict:
        return {k: list(v) if isinstance(v, tuple) else v for k, v in self.__dict__.items() if v not in ('', ())}


@dataclass(frozen=True)
class ReadRequest:
    domain: str
    entities: tuple[str, ...]
    horizon_start: datetime
    horizon_end: datetime
    now: datetime


@dataclass(frozen=True)
class SourceBatch:
    """What one source returned for one domain, with the instant it was extracted."""
    source: str
    connector: str
    domain: str
    extracted_at: datetime
    records: tuple[Record, ...] = ()
    issues: tuple[Issue, ...] = ()
    fingerprint: str = ''  # e.g. sha256 of the file read, empty for live sources


class Connector(Protocol):
    """Read-only by contract: there is deliberately no write method. Publishing is a separate output connector."""

    def read(self, request: ReadRequest) -> SourceBatch: ...


@dataclass(frozen=True)
class MergedRecord:
    entity: str
    key: tuple
    values: dict
    provenance: tuple[tuple[str, str], ...] = field(default=())  # (source, original_key)
