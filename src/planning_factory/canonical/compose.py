"""Compose the batches of several sources into one set of canonical records per domain.

One owner per field: when sources disagree, the binding must declare the precedence,
otherwise the conflict is an error. Records only known to one source are kept (union).
Records are never matched by similarity, only by their canonical key.
"""
import hashlib
import json
from datetime import datetime

from .binding import DomainBinding
from .entities import ENTITIES, key_of, normalize
from .model import Issue, MergedRecord, SourceBatch


def _key_text(key: tuple) -> str:
    return '/'.join(str(k) for k in key)


def compose_domain(binding: DomainBinding, batches: list[SourceBatch]) -> tuple[tuple[MergedRecord, ...], list[Issue]]:
    domain = binding.domain
    issues: list[Issue] = []
    rows: dict[tuple, list[tuple[str, str, dict]]] = {}
    for batch in batches:
        issues.extend(batch.issues)
        seen = set()
        for record in batch.records:
            spec = ENTITIES.get(record.entity)
            if spec is None or spec.domain != domain:
                issues.append(Issue('error', 'unknown_entity', domain, f'entity {record.entity!r} does not belong to {domain}',
                                    batch.source, record.entity, original_key=record.original_key))
                continue
            values, problems = normalize(spec, record.values)
            if problems:
                issues.extend(Issue('error', 'invalid_record', domain, p, batch.source, record.entity,
                                    original_key=record.original_key) for p in problems)
                continue
            key = (record.entity, key_of(spec, values))
            if key in seen:
                issues.append(Issue('error', 'duplicate_key', domain, 'same key twice in one source', batch.source,
                                    record.entity, _key_text(key[1]), record.original_key))
                continue
            seen.add(key)
            rows.setdefault(key, []).append((batch.source, record.original_key, values))
    merged = []
    for (entity, key), contributions in sorted(rows.items(), key=lambda item: (item[0][0], _sortable(item[0][1]))):
        values = {}
        for field in sorted({f for _, _, v in contributions for f in v}):
            offered = [(source, v[field]) for source, _, v in contributions if field in v]
            distinct = {json.dumps(v, sort_keys=True) for _, v in offered}
            if len(distinct) == 1:
                values[field] = offered[0][1]
                continue
            order = binding.precedence_for(entity, field)
            detail = ', '.join(f'{s}={v!r}' for s, v in offered)
            if not order:
                issues.append(Issue('error', 'source_conflict', domain,
                                    f'{field}: sources disagree ({detail}) and the binding declares no precedence',
                                    entity=entity, key=_key_text(key)))
                continue
            winner = next((s for s in order if any(s == src for src, _ in offered)), None)
            if winner is None:
                issues.append(Issue('error', 'source_conflict', domain,
                                    f'{field}: no source in the declared precedence offers a value ({detail})',
                                    entity=entity, key=_key_text(key)))
                continue
            values[field] = next(v for s, v in offered if s == winner)
            issues.append(Issue('warn', 'source_override', domain, f'{field}: {winner} wins by precedence ({detail})',
                                winner, entity, _key_text(key)))
        merged.append(MergedRecord(entity, key, values, tuple((s, k) for s, k, _ in contributions)))
    return tuple(merged), issues


def _sortable(key: tuple) -> tuple:
    # Keys mix ints (weekday, alternative) and strings; sort ints numerically, never by repr.
    return tuple((0, k, '') if isinstance(k, int) else (1, 0, str(k)) for k in key)


def domain_digest(records: tuple[MergedRecord, ...]) -> str:
    """Digest of the content only: the same data from different sources has the same digest."""
    payload = [[r.entity, r.values] for r in records]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def freshness(binding: DomainBinding, batches: list[SourceBatch], now: datetime, accept_stale: bool) -> tuple[list[dict], list[Issue]]:
    sources, issues = [], []
    for batch in batches:
        age = int((now - batch.extracted_at).total_seconds())
        stale = binding.max_age_minutes is not None and age > binding.max_age_minutes * 60
        if age < 0:
            issues.append(Issue('warn', 'extracted_in_future', binding.domain,
                                f'extracted_at is {-age}s after the build instant (clock skew?)', batch.source))
        if stale:
            issues.append(Issue('warn' if accept_stale else 'block', 'stale_data_accepted' if accept_stale else 'stale_data',
                                binding.domain, f'data is {age // 60} min old, limit {binding.max_age_minutes} min',
                                batch.source))
        sources.append({'source': batch.source, 'connector': batch.connector,
                        'extracted_at': batch.extracted_at.isoformat().replace('+00:00', 'Z'),
                        'age_seconds': age, 'stale': stale, 'records': len(batch.records),
                        **({'fingerprint': batch.fingerprint} if batch.fingerprint else {})})
    return sources, issues
