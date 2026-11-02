"""Canonical entities per domain (v1) and strict normalization of their values.

Physical data (durations, setup, capacity, calendar of a resource) must be explicit.
Only neutral fields documented below have defaults. Normalized values are JSON
primitives: instants in UTC ``...Z``, dates ``YYYY-MM-DD``, local times ``HH:MM:SS``.
"""
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class Field:
    name: str
    type: str  # id, text, int, nonneg, positive, weekday, bool, instant, date, time, mode, dependency, timezone
    required: bool = True
    nullable: bool = False
    default: object = None  # applied by the builder only for neutral fields, never for physical data
    ref: str | None = None  # kind of identifier this field holds or points to (translated by identity maps)


@dataclass(frozen=True)
class Entity:
    name: str
    domain: str
    key: tuple[str, ...]
    fields: tuple[Field, ...]
    description: str

    def field(self, name):
        return next((f for f in self.fields if f.name == name), None)


REF_KINDS = ('calendar', 'resource', 'order', 'operation', 'nest')
NEST_STATUSES = ('draft', 'released', 'cut')


def _f(name, type_, required=True, nullable=False, default=None, ref=None):
    return Field(name, type_, required, nullable, default, ref)


def _r(name, ref):
    return Field(name, 'id', ref=ref)


ENTITIES = {e.name: e for e in (
    Entity('calendar', 'calendars', ('id',), (_r('id', 'calendar'), _f('timezone', 'timezone')),
           'Calendario con fuso IANA'),
    Entity('calendar_shift', 'calendars', ('calendar_id', 'weekday', 'start', 'end'),
           (_r('calendar_id', 'calendar'), _f('weekday', 'weekday'), _f('start', 'time'), _f('end', 'time')),
           'Turno settimanale; end <= start significa giorno successivo'),
    Entity('calendar_holiday', 'calendars', ('calendar_id', 'day'), (_r('calendar_id', 'calendar'), _f('day', 'date')),
           'Giorno di chiusura'),
    Entity('calendar_exception', 'calendars', ('calendar_id', 'day'), (_r('calendar_id', 'calendar'), _f('day', 'date')),
           'Giorno con turni sostitutivi (anche nessuno)'),
    Entity('calendar_exception_shift', 'calendars', ('calendar_id', 'day', 'start', 'end'),
           (_r('calendar_id', 'calendar'), _f('day', 'date'), _f('start', 'time'), _f('end', 'time')),
           'Turno di un giorno di eccezione'),
    Entity('calendar_closure', 'calendars', ('calendar_id', 'start', 'end'),
           (_r('calendar_id', 'calendar'), _f('start', 'instant'), _f('end', 'instant')),
           'Chiusura assoluta (fermo, manutenzione)'),
    Entity('resource', 'resources', ('id',),
           (_r('id', 'resource'), _r('calendar_id', 'calendar'), _f('capacity', 'positive', nullable=True),
            _f('kind', 'text', required=False, default='machine'), _f('efficiency_bp', 'positive', False)),
           'Risorsa; capacity null = infinita ma soggetta al calendario; efficiency_bp in punti base (P9)'),
    Entity('order', 'orders', ('id',),
           (_r('id', 'order'), _f('due', 'instant', False, True), _f('release', 'instant', False, True),
            _f('priority', 'int', False), _f('customer_priority', 'int', False), _f('mode', 'mode', False, True),
            _f('customer_id', 'text', False)),
           'Commessa; i campi valgono per le fasi che non li dichiarano'),
    Entity('operation', 'routings', ('id',),
           (_r('id', 'operation'), _r('order_id', 'order'), _f('duration_seconds', 'nonneg'), _f('setup_seconds', 'nonneg'),
            _f('due', 'instant', False, True), _f('release', 'instant', False, True),
            _f('priority', 'int', False), _f('customer_priority', 'int', False),
            _f('phase_priority', 'int', False, default=0), _f('mode', 'mode', False, True),
            _f('preferred_alternative', 'nonneg', False),
            _f('splittable', 'bool', False, default=False), _f('quantity', 'decimal', False),
            _f('material', 'text', False), _f('thickness_mm', 'decimal', False)),
           'Fase: durata residua di lavorazione e setup in secondi'),
    Entity('operation_demand', 'routings', ('operation_id', 'alternative', 'resource_id'),
           (_r('operation_id', 'operation'), _f('alternative', 'nonneg'), _r('resource_id', 'resource'),
            _f('units', 'positive', False, default=1)),
           'Risorsa richiesta da una alternativa (indice 0..n-1) di una fase'),
    Entity('operation_dependency', 'routings', ('operation_id', 'predecessor_id'),
           (_r('operation_id', 'operation'), _r('predecessor_id', 'operation'), _f('kind', 'dependency', False, default='FS'),
            _f('lag_seconds', 'int', False, default=0), _f('max_lag_seconds', 'nonneg', False)),
           'Precedenza FS/SS con ritardo in secondi trascorsi; max_lag_seconds = attesa massima segnalata (P6)'),
    Entity('nest', 'nesting', ('id',),
           (_r('id', 'nest'), _f('duration_seconds', 'nonneg'), _f('setup_seconds', 'nonneg'),
            _f('status', 'nest_status', False), _f('version', 'text', False),
            Field('resource_id', 'id', False, ref='resource'), _f('material', 'text', False),
            _f('thickness_mm', 'decimal', False), _f('sheets', 'positive', False), _f('preview', 'text', False)),
           'Programma di taglio: una sola attività con durata condivisa; resource_id = macchina assegnata dal CAM'),
    Entity('nest_member', 'nesting', ('nest_id', 'operation_id'),
           (_r('nest_id', 'nest'), _r('operation_id', 'operation'), _f('quantity', 'decimal', False)),
           'Fase contenuta in un programma di taglio (quantità informativa)'),
    Entity('operation_external_end', 'subcontract', ('operation_id',),
           (_r('operation_id', 'operation'), _f('external_end', 'instant')),
           'Fine dichiarata dal fornitore di conto lavoro (non è un consuntivo)'),
    Entity('operation_state', 'progress', ('operation_id',),
           (_r('operation_id', 'operation'), _f('completed_at', 'instant', False, True),
            _f('fixed_start', 'instant', False, True), _f('fixed_alternative', 'nonneg', False, True)),
           'Stato di avanzamento: fase chiusa o avviata/bloccata su inizio e alternativa'),
    Entity('operation_progress', 'progress', ('operation_id',),
           (_r('operation_id', 'operation'), _f('status', 'progress_status'), _f('completed_at', 'instant', False, True),
            _f('produced_quantity', 'decimal0', False), _f('recorded_work_seconds', 'nonneg', False),
            _f('recorded_setup_seconds', 'nonneg', False), Field('resource_id', 'id', False, ref='resource')),
           'Consuntivo: fase avviata (residuo secondo residual_policy, macchina in uso) o chiusa'),
    Entity('profile', 'rules', ('id',),
           (_f('id', 'id'), _f('mode', 'mode'), _f('auto_backward_days', 'nonneg'), _f('priority_weight', 'nonneg'),
            _f('due_weight', 'nonneg'), _f('customer_weight', 'nonneg'),
            _f('alternative_switch_min_gain_seconds', 'nonneg', False),
            _f('auto_rule', 'auto_rule', False), _f('auto_slack_seconds', 'nonneg', False),
            _f('delivery_buffer_seconds', 'nonneg', False), _f('wip_priority_bonus', 'nonneg', False)),
           'Profilo di calcolo (Settings del motore) con margine di consegna P3 e bonus lavori avviati P12'),
    Entity('rule', 'rules', ('id',),
           (_f('id', 'id'), _f('action', 'rule_action'), _f('value', 'int'), _f('note', 'text', False),
            Field('resource_id', 'id', False, ref='resource'), Field('order_id', 'id', False, ref='order'),
            _f('customer_id', 'text', False), _f('material', 'text', False),
            _f('min_thickness_mm', 'decimal0', False), _f('max_thickness_mm', 'decimal', False),
            _f('profile', 'text', False), _f('valid_from', 'instant', False), _f('valid_to', 'instant', False),
            _f('confirmed_by', 'text', False), _f('confirmed_at', 'instant', False)),
           'Regola con ambito confermata dal planner: add_setup_seconds, set_priority, set_customer_priority'),
    Entity('material_supply', 'materials', ('id',),
           (_f('id', 'id'), _f('item_id', 'id'), _f('quantity', 'decimal'), _f('available_at', 'instant', nullable=True),
            _f('kind', 'supply', False), _f('reference', 'text', False)),
           'Disponibilità: giacenza, ordine fornitore, trasferimento o produzione; available_at null = data ignota'),
    Entity('material_need', 'materials', ('operation_id', 'item_id'),
           (_r('operation_id', 'operation'), _f('item_id', 'id'), _f('quantity', 'decimal')),
           'Fabbisogno di materiale di una fase'),
    Entity('allocation_rank', 'materials', ('operation_id',), (_r('operation_id', 'operation'), _f('rank', 'int')),
           'Ordine esplicito di assegnazione del materiale (allocation_order = rank), minore prima'),
    Entity('identity_map', 'identity', ('map', 'external_id'),
           (_f('map', 'id'), _f('external_id', 'id'), _f('canonical_id', 'id')),
           'Corrispondenza esplicita fra la chiave di un sistema e la chiave canonica'),
)}

DOMAINS = ('resources', 'calendars', 'orders', 'routings', 'nesting', 'materials', 'subcontract', 'progress',
           'transport', 'rules', 'identity')
REQUIRED_DOMAINS = ('resources', 'calendars', 'orders', 'routings')
# Declared by ADR 004 but not yet composed into the scenario: binding them is refused, never ignored.
RESERVED_DOMAINS = {'transport': '#78 P5'}


def domain_entities(domain: str) -> tuple[str, ...]:
    return tuple(name for name, e in ENTITIES.items() if e.domain == domain)


def _instant(value):
    if not isinstance(value, str):
        raise ValueError('ISO 8601 string with offset required')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timezone offset required')
    if parsed.microsecond:
        raise ValueError('precision must be whole seconds')
    return parsed.astimezone(UTC).isoformat().replace('+00:00', 'Z')


def _int(value, minimum=None):
    if type(value) is not int:
        raise ValueError('integer required')
    if minimum is not None and value < minimum:
        raise ValueError(f'integer >= {minimum} required')
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('non-empty string required')
    return value


def _timezone(value):
    _text(value)
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError(f'unknown IANA timezone {value}') from None
    return value


def _choice(options):
    def check(value):
        if value not in options:
            raise ValueError(f'one of {list(options)} required')
        return value
    return check


def _bool(value):
    if type(value) is not bool:
        raise ValueError('boolean required')
    return value


def _weekday(value):
    _int(value, 0)
    if value > 6:
        raise ValueError('weekday 0 (Monday) .. 6 required')
    return value


def _decimal(value, minimum=None):
    try:
        number = Decimal(value) if isinstance(value, (str, int)) and not isinstance(value, bool) else None
    except InvalidOperation:
        number = None
    if number is None or not number.is_finite() or (number < 0 if minimum == 0 else number <= 0):
        raise ValueError(f'{"nonnegative" if minimum == 0 else "positive"} finite decimal (string or integer) required')
    return format(number.normalize(), 'f') if number else '0'


def _date(value):
    if not isinstance(value, str):
        raise ValueError('YYYY-MM-DD string required')
    return date.fromisoformat(value).isoformat()


def _time(value):
    if not isinstance(value, str):
        raise ValueError('HH:MM[:SS] string required')
    return time.fromisoformat(value).isoformat()


NORMALIZERS = {
    'id': _text, 'text': _text, 'timezone': _timezone, 'int': _int, 'nonneg': lambda v: _int(v, 0),
    'positive': lambda v: _int(v, 1), 'weekday': _weekday, 'bool': _bool, 'instant': _instant,
    'date': _date, 'time': _time, 'mode': _choice(('forward', 'backward', 'auto')),
    'dependency': _choice(('FS', 'SS')), 'decimal': _decimal,
    'supply': _choice(('stock', 'purchase_order', 'transfer', 'production')),
    'nest_status': _choice(NEST_STATUSES), 'auto_rule': _choice(('due_distance', 'slack')), 'progress_status': _choice(('started', 'completed')),
    'decimal0': lambda v: _decimal(v, minimum=0),
    'rule_action': _choice(('add_setup_seconds', 'set_priority', 'set_customer_priority', 'set_delivery_buffer_seconds')),
}


def normalize(entity: Entity, values) -> tuple[dict | None, list[str]]:
    """Return normalized values or the list of problems. Unknown and missing fields are errors."""
    if not isinstance(values, dict):
        return None, ['object required']
    problems, result = [], {}
    names = {f.name for f in entity.fields}
    for name in sorted(set(values) - names):
        problems.append(f'unknown field {name}')
    for f in entity.fields:
        if f.name not in values:
            if f.required:
                problems.append(f'missing field {f.name}')
            continue
        value = values[f.name]
        if value is None:
            if f.nullable:
                result[f.name] = None
            else:
                problems.append(f'{f.name}: null not allowed')
            continue
        try:
            result[f.name] = NORMALIZERS[f.type](value)
        except ValueError as exc:
            problems.append(f'{f.name}: {exc}')
    return (None, problems) if problems else (result, [])


def key_of(entity: Entity, values: dict) -> tuple:
    return tuple(values[k] for k in entity.key)


def json_schema() -> dict:
    """JSON Schema of the canonical-json bundle, generated from the entity table."""
    kinds = {
        'id': {'type': 'string', 'minLength': 1}, 'text': {'type': 'string', 'minLength': 1},
        'timezone': {'type': 'string', 'minLength': 1}, 'int': {'type': 'integer'},
        'nonneg': {'type': 'integer', 'minimum': 0}, 'positive': {'type': 'integer', 'minimum': 1},
        'weekday': {'type': 'integer', 'minimum': 0, 'maximum': 6}, 'bool': {'type': 'boolean'},
        'instant': {'type': 'string', 'format': 'date-time'}, 'date': {'type': 'string', 'format': 'date'},
        'time': {'type': 'string', 'pattern': '^[0-2][0-9]:[0-5][0-9](:[0-5][0-9])?$'},
        'mode': {'enum': ['forward', 'backward', 'auto']}, 'dependency': {'enum': ['FS', 'SS']},
        'decimal': {'anyOf': [{'type': 'string', 'pattern': r'^[0-9]+(\.[0-9]+)?$'}, {'type': 'integer', 'minimum': 1}]},
        'supply': {'enum': ['stock', 'purchase_order', 'transfer', 'production']},
        'rule_action': {'enum': ['add_setup_seconds', 'set_priority', 'set_customer_priority', 'set_delivery_buffer_seconds']},
        'nest_status': {'enum': list(NEST_STATUSES)}, 'auto_rule': {'enum': ['due_distance', 'slack']}, 'progress_status': {'enum': ['started', 'completed']},
        'decimal0': {'anyOf': [{'type': 'string', 'pattern': r'^[0-9]+(\.[0-9]+)?$'}, {'type': 'integer', 'minimum': 0}]},
    }
    records = {}
    for e in ENTITIES.values():
        props = {}
        for f in e.fields:
            spec = dict(kinds[f.type])
            if f.nullable:
                spec = {'anyOf': [spec, {'type': 'null'}]}
            if f.default is not None:
                spec['default'] = f.default
            props[f.name] = spec
        records[e.name] = {'type': 'array', 'description': f'{e.domain}: {e.description}', 'items': {
            'type': 'object', 'additionalProperties': False, 'properties': props,
            'required': [f.name for f in e.fields if f.required]}}
    return {
        '$schema': 'https://json-schema.org/draft/2020-12/schema',
        '$id': 'planning-factory/canonical-v1.schema.json',
        'title': 'Planning Factory canonical data bundle v1 (ADR 004)',
        'type': 'object', 'additionalProperties': False, 'required': ['schema_version', 'records'],
        'properties': {
            'schema_version': {'const': SCHEMA_VERSION},
            'extracted_at': {'type': 'string', 'format': 'date-time'},
            'records': {'type': 'object', 'additionalProperties': False, 'properties': records},
        },
    }
