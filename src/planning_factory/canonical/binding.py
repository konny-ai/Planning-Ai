"""Per-installation binding: which source feeds which canonical domain (ADR 004).

The file is versioned with the installation and must never contain credentials:
connectors receive references (e.g. the name of an environment variable) instead.
"""
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from planning_factory.domain.validation import ValidationError
from .perimeter import KEYS as PERIMETER_KEYS
from .entities import DOMAINS, ENTITIES, NEST_STATUSES, REF_KINDS, REQUIRED_DOMAINS, RESERVED_DOMAINS

BINDING_VERSION = 1
_SECRET_KEY = re.compile(r'pass(word|wd)?|pwd|secret|token|api[_-]?key|credential', re.IGNORECASE)
_SECRET_VALUE = re.compile(r'(^|;)\s*(pwd|password)\s*=', re.IGNORECASE)


@dataclass(frozen=True)
class SourceBinding:
    name: str
    connector: str
    options: dict
    identity: tuple[tuple[str, str], ...] = ()  # (reference kind, identity map) applied to this source's keys


@dataclass(frozen=True)
class DomainBinding:
    domain: str
    sources: tuple[str, ...]
    max_age_minutes: int | None = None
    precedence: tuple[tuple[str, tuple[str, ...]], ...] = ()  # ('entity.field', (source, ...))
    allocation_order: str | None = None  # materials only: 'due' or 'rank', an explicit business choice
    include_statuses: tuple[str, ...] | None = None  # nesting only: program statuses to plan, None = all
    residual_policy: str | None = None  # progress only: none | time | quantity (domain/durations.py)

    def precedence_for(self, entity: str, field: str) -> tuple[str, ...] | None:
        return dict(self.precedence).get(f'{entity}.{field}')


@dataclass(frozen=True)
class Binding:
    installation: str
    scenario_id: str
    horizon_start: datetime
    horizon_end: datetime
    profile: str | None
    sources: tuple[SourceBinding, ...]
    domains: tuple[DomainBinding, ...]
    base_dir: Path
    digest: str
    nesting_required_resources: tuple[str, ...] = ()
    perimeter: dict | None = None  # typed order filters (P15), see canonical/perimeter.py

    def source(self, name: str) -> SourceBinding:
        return next(s for s in self.sources if s.name == name)

    def domain(self, name: str) -> DomainBinding | None:
        return next((d for d in self.domains if d.domain == name), None)


def _keys(value, allowed, required, label, errors):
    if not isinstance(value, dict):
        errors.append(f'{label}: object required')
        return False
    unknown, missing = set(value) - set(allowed), set(required) - set(value)
    if unknown or missing:
        errors.append(f'{label}: unknown fields {sorted(unknown)}, missing fields {sorted(missing)}')
        return False
    return True


def _secrets(value, path, errors):
    if isinstance(value, dict):
        for k, v in value.items():
            if _SECRET_KEY.search(str(k)) and not str(k).endswith('_env'):
                errors.append(f'{path}.{k}: credentials are not allowed in the binding, reference an installation secret (e.g. {k}_env)')
            _secrets(v, f'{path}.{k}', errors)
    elif isinstance(value, list):
        for i, v in enumerate(value):
            _secrets(v, f'{path}[{i}]', errors)
    elif isinstance(value, str) and _SECRET_VALUE.search(value):
        errors.append(f'{path}: connection strings with passwords are not allowed in the binding')


def _instant(value, label, errors):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            raise ValueError
        return parsed.astimezone(UTC)
    except ValueError:
        errors.append(f'{label}: ISO 8601 instant with offset required')
        return None


def parse_binding(data, base_dir: Path, connectors: frozenset[str] | None = None) -> Binding:
    errors = []
    if not _keys(data, ('schema_version', 'installation', 'scenario', 'profile', 'sources', 'domains', 'quality', 'perimeter'),
                 ('schema_version', 'installation', 'scenario', 'sources', 'domains'), 'binding', errors):
        raise ValidationError(errors)
    if data['schema_version'] != BINDING_VERSION:
        errors.append(f'binding.schema_version must be {BINDING_VERSION}')
    _secrets(data, 'binding', errors)
    scenario = data['scenario']
    start = end = None
    if _keys(scenario, ('id', 'start', 'end'), ('id', 'start', 'end'), 'binding.scenario', errors):
        start = _instant(scenario['start'], 'binding.scenario.start', errors)
        end = _instant(scenario['end'], 'binding.scenario.end', errors)
        if start and end and start >= end:
            errors.append('binding.scenario: end must be after start')
    sources = []
    if not isinstance(data['sources'], dict) or not data['sources']:
        errors.append('binding.sources: non-empty object required')
    else:
        for name, raw in sorted(data['sources'].items()):
            if _keys(raw, ('connector', 'options', 'identity'), ('connector',), f'binding.sources.{name}', errors):
                if connectors is not None and raw['connector'] not in connectors:
                    errors.append(f'binding.sources.{name}: unknown connector {raw["connector"]!r}, available {sorted(connectors)}')
                options = raw.get('options', {})
                if not isinstance(options, dict):
                    errors.append(f'binding.sources.{name}.options: object required')
                identity = raw.get('identity', {})
                if not isinstance(identity, dict) or not all(k in REF_KINDS and isinstance(v, str) and v.strip()
                                                               for k, v in identity.items()):
                    errors.append(f'binding.sources.{name}.identity: object {list(REF_KINDS)} -> map name required')
                    identity = {}
                sources.append(SourceBinding(name, raw['connector'], options if isinstance(options, dict) else {},
                                             tuple(sorted(identity.items()))))
    names = {s.name for s in sources}
    domains = []
    raw_domains = data['domains'] if isinstance(data['domains'], dict) else {}
    if not isinstance(data['domains'], dict):
        errors.append('binding.domains: object required')
    for domain, raw in sorted(raw_domains.items()):
        label = f'binding.domains.{domain}'
        if domain not in DOMAINS:
            errors.append(f'{label}: unknown domain, expected one of {list(DOMAINS)}')
            continue
        if domain in RESERVED_DOMAINS:
            errors.append(f'{label}: domain declared by ADR 004 but not composed yet (see {RESERVED_DOMAINS[domain]})')
            continue
        allowed = ('sources', 'max_age_minutes', 'precedence') + (('allocation_order',) if domain == 'materials' else ()) + \
            (('include_statuses',) if domain == 'nesting' else ()) + (('residual_policy',) if domain == 'progress' else ())
        if not _keys(raw, allowed, ('sources',) + (('allocation_order',) if domain == 'materials' else ()), label, errors):
            continue
        statuses = raw.get('include_statuses')
        if statuses is not None and (not isinstance(statuses, list) or not statuses or len(set(statuses)) != len(statuses)
                                     or not set(statuses) <= set(NEST_STATUSES)):
            errors.append(f'{label}.include_statuses: non-empty list of distinct {list(NEST_STATUSES)} required')
            statuses = None
        residual = raw.get('residual_policy')
        if residual is not None and residual not in ('none', 'time', 'quantity'):
            errors.append(f'{label}.residual_policy: "none", "time" or "quantity" required')
        allocation = raw.get('allocation_order')
        if domain == 'materials' and allocation not in ('due', 'rank'):
            errors.append(f'{label}.allocation_order: "due" (earliest due first) or "rank" (allocation_rank records) required')
        listed = raw['sources']
        if not isinstance(listed, list) or not listed or len(set(listed)) != len(listed) or not set(listed) <= names:
            errors.append(f'{label}.sources: non-empty list of distinct declared sources required')
            continue
        max_age = raw.get('max_age_minutes')
        if max_age is not None and (type(max_age) is not int or max_age < 1):
            errors.append(f'{label}.max_age_minutes: positive integer required')
        precedence = []
        raw_prec = raw.get('precedence', {})
        if not isinstance(raw_prec, dict):
            errors.append(f'{label}.precedence: object required')
            raw_prec = {}
        for target, order in sorted(raw_prec.items()):
            entity, _, field = target.partition('.')
            spec = ENTITIES.get(entity)
            if spec is None or spec.domain != domain or spec.field(field) is None or field in spec.key:
                errors.append(f'{label}.precedence.{target}: non-key field of an entity of this domain required')
            elif not isinstance(order, list) or not order or len(set(order)) != len(order) or not set(order) <= set(listed):
                errors.append(f'{label}.precedence.{target}: list of distinct sources of this domain required')
            else:
                precedence.append((target, tuple(order)))
        domains.append(DomainBinding(domain, tuple(listed), max_age, tuple(precedence), allocation,
                                     tuple(statuses) if statuses is not None else None, residual))
    for domain in REQUIRED_DOMAINS:
        if domain not in raw_domains:
            errors.append(f'binding.domains.{domain}: required domain is not bound')
    if any(s.identity for s in sources) and 'identity' not in raw_domains:
        errors.append('binding.domains.identity: required when a source declares identity maps')
    quality = data.get('quality', {})
    nest_resources = ()
    if _keys(quality, ('nesting_required_resources',), (), 'binding.quality', errors):
        nest_resources = quality.get('nesting_required_resources', [])
        if not isinstance(nest_resources, list) or not all(isinstance(r, str) and r.strip() for r in nest_resources):
            errors.append('binding.quality.nesting_required_resources: list of resource ids required')
            nest_resources = []
    perimeter = data.get('perimeter')
    if perimeter is not None and _keys(perimeter, PERIMETER_KEYS, (), 'binding.perimeter', errors):
        for key in ('order_ids', 'exclude_order_ids', 'customer_ids', 'exclude_customer_ids'):
            value = perimeter.get(key)
            if value is not None and (not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value)):
                errors.append(f'binding.perimeter.{key}: list of ids required')
        for key in ('due_from', 'due_to'):
            if perimeter.get(key) is not None:
                parsed = _instant(perimeter[key], f'binding.perimeter.{key}', errors)
                if parsed is not None:
                    perimeter = dict(perimeter, **{key: parsed.isoformat().replace('+00:00', 'Z')})
        if 'include_without_due' in perimeter and type(perimeter['include_without_due']) is not bool:
            errors.append('binding.perimeter.include_without_due: boolean required')
    profile = data.get('profile')
    if profile is not None and not (isinstance(profile, str) and profile.strip()):
        errors.append('binding.profile: non-empty string required')
    if not isinstance(data['installation'], str) or not data['installation'].strip():
        errors.append('binding.installation: non-empty string required')
    if errors:
        raise ValidationError(errors)
    digest = hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()
    return Binding(data['installation'], scenario['id'], start, end, profile, tuple(sources), tuple(domains),
                   base_dir, digest, tuple(sorted(set(nest_resources))), perimeter)


def load_binding(path: Path, connectors: frozenset[str] | None = None) -> Binding:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except ValueError as exc:
        raise ValidationError([f'invalid binding JSON: {exc}']) from None
    return parse_binding(data, Path(path).resolve().parent, connectors)
