"""Data screens of the Control Room: browse the canonical input records and correct them.

Reads come from the merged canonical records of the live build (after corrections);
records excluded by a correction stay visible so that they can be restored. A change
is a correction in the instance store, validated by rebuilding the whole Scenario
before it is stored: a correction that would make the data unbuildable is refused.
"""
from datetime import UTC, datetime
from pathlib import Path
import re
import threading

from planning_factory.canonical.corrections import SOURCE, CorrectionStore
from planning_factory.canonical.entities import DOMAINS, ENTITIES, domain_entities
from planning_factory.domain.validation import ValidationError

MAX_PAGE = 200
# Display-only columns of a screen: read from the presentation texts (descrizioni.json) and from the
# operation records, never stored, corrected or sent to the engine. Order: name, type.
EXTRAS = {
    'order': (('issue_date', 'date'), ('due_min', 'date'), ('due_max', 'date'), ('requested_min', 'date'),
              ('lines', 'nonneg'), ('phases', 'nonneg'), ('remaining_seconds', 'nonneg'),
              ('reference', 'text'), ('description', 'text')),
    'operation': (('line', 'nonneg'), ('lot', 'nonneg'), ('part', 'text'), ('part_description', 'text'),
                  ('phase_code', 'text'), ('phase', 'text'), ('sequence', 'nonneg'), ('uom', 'text'),
                  ('size_mm', 'text'), ('order_code', 'text'), ('customer', 'text'),
                  ('row_quantity', 'text'), ('lot_quantity', 'text'), ('row_due', 'date'), ('row_status', 'text')),
}
REF_ENTITY = {'calendar': 'calendar', 'resource': 'resource', 'order': 'order', 'operation': 'operation', 'nest': 'nest'}
_ITALIAN = (
    (re.compile(r'file-only canonical binding cannot produce a valid Scenario'),
     'Con questa modifica i dati non sarebbero più calcolabili: correzione rifiutata'),
    (re.compile(r'missing field (\w+)'), r'campo obbligatorio mancante: \1'),
    (re.compile(r'unknown field (\w+)'), r'campo sconosciuto: \1'),
    (re.compile(r'(\w+): null not allowed'), r'\1: non può essere vuoto'),
    (re.compile(r'(\w+): required, cannot be emptied'), r'\1: obbligatorio, non si può svuotare'),
    (re.compile(r'(\w+) is part of the key: exclude the record and insert a new one'),
     r'\1 fa parte della chiave: escludi il record e inseriscine uno nuovo'),
    (re.compile(r'integer >= 1 required'), 'serve un numero intero maggiore di zero'),
    (re.compile(r'integer >= 0 required'), 'serve un numero intero non negativo'),
    (re.compile(r'integer required'), 'serve un numero intero'),
    (re.compile(r'ISO 8601 string with offset required|timezone offset required'), 'serve data e ora complete'),
    (re.compile(r'precision must be whole seconds'), 'niente frazioni di secondo'),
    (re.compile(r'one of \[([^\]]*)\] required'), r'valori ammessi: \1'),
    (re.compile(r'nonnegative finite decimal \(string or integer\) required'), 'serve un numero maggiore o uguale a zero'),
    (re.compile(r'positive finite decimal \(string or integer\) required'), 'serve un numero maggiore di zero'),
    (re.compile(r'non-empty string required'), 'testo obbligatorio'),
    (re.compile(r'YYYY-MM-DD string required'), 'serve una data'),
    (re.compile(r'HH:MM\[:SS\] string required'), 'serve un orario'),
    (re.compile(r'boolean required'), 'serve sì o no'),
    (re.compile(r'weekday 0 \(Monday\) \.\. 6 required'), 'giorno della settimana da 0 (lunedì) a 6'),
    (re.compile(r'record not found'), 'record non trovato'),
    (re.compile(r'no field changes value'), 'nessun campo cambia valore'),
    (re.compile(r'record already excluded'), 'record già escluso'),
    (re.compile(r'a reason of 3\.\.500 characters is required'), 'scrivi il motivo della modifica (almeno 3 caratteri)'),
    (re.compile(r'an update needs at least one field'), 'modifica almeno un campo'),
    (re.compile(r'only active corrections can be revoked'), 'si possono annullare solo correzioni attive'),
)


def italian(errors) -> list[str]:
    result = []
    for message in errors:
        for pattern, replacement in _ITALIAN:
            message = pattern.sub(replacement, message)
        result.append(message)
    return result


def _status(record, excluded=False):
    tags = [k for s, k in record.provenance if s == SOURCE]
    sources = sorted({s for s, _ in record.provenance if s != SOURCE})
    if excluded:
        status = 'excluded'
    elif tags and not sources:
        status = 'inserted'
    elif tags:
        status = 'updated'
    else:
        status = 'source'
    return status, sources, [int(t.lstrip('#')) for t in tags if t.lstrip('#').isdigit()]


class DataWorkspace:
    def __init__(self, state, database):
        if state.binding is None:
            raise ValidationError(['data screens need a canonical binding, not a plain Scenario JSON'])
        self.state = state
        self.store = CorrectionStore(database)
        self.lock = threading.RLock()
        self._rows = {}
        active = self.store.active()
        if active:
            # Stored corrections apply from the start; one that no longer builds is reported, not hidden.
            try:
                state.commit_build(state.try_build(active), active)
            except ValidationError as exc:
                self.startup_error = exc.errors
            else:
                self.startup_error = None
        else:
            self.startup_error = None

    # ------------------------------------------------------------------ reading
    def catalog(self) -> dict:
        state = self.state
        bound = self.bound_domains()
        counts = {}
        for entity, items in (state.records or {}).items():
            counts[entity] = len(items)
        excluded = {entity: len(items) for entity, items in (state.excluded or {}).items()}
        corrected = {}
        for c in state.corrections:
            corrected[c.entity] = corrected.get(c.entity, 0) + 1
        if getattr(self, '_filled', (None,))[0] != state.data_version:
            filled = {}
            for entity, items in (state.records or {}).items():
                counter = filled.setdefault(entity, {})
                for record in items:
                    for name, value in record.values.items():
                        if value is not None:
                            counter[name] = counter.get(name, 0) + 1
            self._filled = (state.data_version, filled)
        filled = self._filled[1]
        extras = {}
        for entity, fields in EXTRAS.items():
            if entity in counts:
                found = extras[entity] = {name: 0 for name, _ in fields}
                for row in self._table(entity):
                    if row['status'] != 'excluded':
                        for name in row.get('extra', {}):
                            found[name] += 1
        entities = []
        for domain in DOMAINS:
            if domain not in bound or domain == 'identity':
                continue
            for name in domain_entities(domain):
                spec = ENTITIES[name]
                entities.append({
                    'name': name, 'domain': domain, 'key': list(spec.key), 'description': spec.description,
                    'fields': [{'name': f.name, 'type': f.type, 'required': f.required, 'nullable': f.nullable,
                                'default': f.default, 'ref': f.ref, 'filled': filled.get(name, {}).get(f.name, 0)}
                               for f in spec.fields],
                    'extras': [{'name': n, 'type': t, 'filled': extras.get(name, {}).get(n, 0)} for n, t in EXTRAS.get(name, ())],
                    'records': counts.get(name, 0), 'excluded': excluded.get(name, 0),
                    'corrections': corrected.get(name, 0)})
        return {'entities': entities, 'domains': sorted(bound - {'identity'}), 'data_version': state.data_version,
                'corrections_active': len(state.corrections), 'startup_error': self.startup_error}

    def bound_domains(self):
        return {d.domain for d in self.state.binding.domains}

    def materials(self) -> dict:
        """Needs and supplies of the active canonical data (corrections included), for the material detail of the plan.

        The plan views show them next to the pegging recorded in the source manifest: which part a phase needs,
        what is in stock and which purchase orders feed it. Nothing is derived here: a field the source does not give stays absent.
        """
        records = self.state.records or {}
        return {'available': True, 'data_version': self.state.data_version,
                'needs': [dict(r.values) for r in records.get('material_need', ())],
                'supplies': [dict(r.values) for r in records.get('material_supply', ())]}

    def _table(self, entity):
        cache_key = (entity, self.state.data_version)
        cached = self._rows.get(entity)
        if cached and cached[0] == cache_key:
            return cached[1]
        rows = []
        extra = self._extras(entity)
        for record in (self.state.records or {}).get(entity, ()):
            status, sources, ids = _status(record)
            rows.append({'key': list(record.key), 'values': record.values, 'status': status,
                         'sources': sources, 'corrections': ids, **extra(record)})
        for record in (self.state.excluded or {}).get(entity, ()):
            status, sources, ids = _status(record, excluded=True)
            rows.append({'key': list(record.key), 'values': record.values, 'status': status,
                         'sources': sources, 'corrections': ids, **extra(record)})
        self._rows[entity] = (cache_key, rows)
        return rows

    def _extras(self, entity):
        """Function giving the display-only values of a record as ``{'extra': {...}}`` (empty when the screen has none)."""
        if entity not in ('order', 'operation'):
            return lambda record: {}
        described = {}
        texts = {}
        if hasattr(self.state, 'descriptions'):
            descriptions = self.state.descriptions() or {}
            described = descriptions.get('orders') or {}
            texts = descriptions.get('operations') or {}
        if entity == 'operation':
            def operation_extra(record):
                text = texts.get(str(record.key[0]))
                text = text if isinstance(text, dict) else {}
                order = described.get(str(record.values.get('order_id')))
                order = order if isinstance(order, dict) else {}
                values = {name: text.get(name) for name, _ in EXTRAS['operation']}
                values['order_code'], values['customer'] = order.get('code'), order.get('customer')
                return {'extra': {k: v for k, v in values.items() if v is not None},
                        'search': [v for v in (order.get('customer'), order.get('code'), order.get('reference')) if isinstance(v, str)]}
            return operation_extra
        phases = {}
        for record in (self.state.records or {}).get('operation', ()):
            seconds = record.values.get('duration_seconds')
            count = phases.setdefault(str(record.values.get('order_id')), [0, 0])
            count[0] += 1
            count[1] += seconds if isinstance(seconds, int) and not isinstance(seconds, bool) else 0

        def extra(record):
            oid = str(record.key[0])
            text = described.get(oid) if isinstance(described.get(oid), dict) else {}
            found = phases.get(oid)
            values = {name: text.get(name) for name in ('issue_date', 'due_min', 'due_max', 'requested_min',
                                                       'lines', 'reference', 'description')}
            values['phases'] = found[0] if found else None
            values['remaining_seconds'] = found[1] if found and found[1] else None
            return {'extra': {k: v for k, v in values.items() if v is not None},
                    'search': [v for v in (text.get('customer'), text.get('code')) if isinstance(v, str)]}
        return extra

    def rows(self, entity, *, q='', status='', filters=None, sort=None, descending=False, offset=0, limit=50) -> dict:
        spec = ENTITIES.get(entity)
        if spec is None or entity not in {e['name'] for e in self.catalog()['entities']}:
            raise ValidationError([f'unknown or unbound entity {entity!r}'])
        if not 1 <= limit <= MAX_PAGE or offset < 0:
            raise ValidationError([f'limit 1..{MAX_PAGE} and offset >= 0 required'])
        rows = self._table(entity)
        if status:
            wanted = set(status.split(','))
            rows = [r for r in rows if r['status'] in wanted or (r['status'] != 'source' and 'corrected' in wanted)]
        extra_types = dict(EXTRAS.get(entity, ()))

        def get(row, field):
            return row['values'][field] if field in row['values'] else row.get('extra', {}).get(field)
        for field, value in (filters or {}).items():
            if spec.field(field) is None and field not in extra_types:
                raise ValidationError([f'unknown field {field}'])
            needle = str(value).casefold()
            rows = [r for r in rows if needle in str(get(r, field) if get(r, field) is not None else '').casefold()]
        if q:
            needle = q.casefold()
            rows = [r for r in rows if any(needle in str(v).casefold()
                                           for v in (*r['values'].values(), *r.get('extra', {}).values(), *r.get('search', ())) if v is not None)]
        if sort:
            if spec.field(sort) is None and sort not in extra_types:
                raise ValidationError([f'unknown sort field {sort}'])
            # The screen shows the company name next to the customer code: sort by what is read,
            # customers without a name after the named ones, by code.
            names = self.state.customer_names() if sort == 'customer_id' and hasattr(self.state, 'customer_names') else {}

            def order(row):
                value = get(row, sort)
                if value is None:
                    return (2, 0, '')
                if names:
                    name = names.get(str(value))
                    return (1, 0, name.casefold()) if name else (1, 1, str(value))
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    return (0, value, '')
                try:
                    return (0, float(value), '') if spec.field(sort) and spec.field(sort).type in ('decimal', 'decimal0') else (1, 0, str(value))
                except ValueError:
                    return (1, 0, str(value))
            rows = sorted(rows, key=order, reverse=descending)
        elif entity == 'operation':
            # Without an explicit sort the phases follow the structure of the work: order, lot, sequence.
            def structure(row):
                extra = row.get('extra', {})
                lot, sequence = (row['key'][0].split('/') + ['0', '0'])[1:3]
                order = str(row['values'].get('order_id'))
                return (0, int(order), '') if order.isdigit() else (1, 0, order), \
                    int(lot) if lot.isdigit() else 0, extra.get('sequence', int(sequence) if sequence.isdigit() else 0), row['key']
            rows = sorted(rows, key=structure)
        total = len(rows)
        return {'entity': entity, 'total': total, 'offset': offset, 'limit': limit,
                'rows': rows[offset:offset + limit], 'data_version': self.state.data_version}

    def ids(self, ref, q='', limit=50) -> list[str]:
        entity = REF_ENTITY.get(ref)
        if entity is None:
            raise ValidationError(['unknown reference kind'])
        needle = (q or '').casefold()
        result = []
        for record in (self.state.records or {}).get(entity, ()):
            value = str(record.key[0])
            if needle in value.casefold():
                result.append(value)
                if len(result) >= limit:
                    break
        return result

    def history(self, entity=None, key=None) -> list[dict]:
        if entity is not None and entity not in ENTITIES:
            raise ValidationError(['unknown entity'])
        return self.store.history(entity, tuple(key) if key is not None else None)

    # ------------------------------------------------------------------ writing
    def _previous(self, entity, key):
        for record in (self.state.records or {}).get(entity, ()):
            if record.key == key:
                return record.values
        for record in (self.state.excluded or {}).get(entity, ()):
            if record.key == key:
                return record.values
        return None

    def correct(self, entity, action, key, values, reason, actor) -> dict:
        """Validate, rebuild with the new correction, then store it. Nothing is stored if the rebuild fails."""
        result = self.correct_many([{'entity': entity, 'action': action, 'key': key,
                                     'values': values, 'reason': reason}], actor, include_records=True)
        return {**result, 'correction': result['corrections'][0]}

    def correct_many(self, items, actor, *, include_records=False) -> dict:
        """Validate a set together and build once; the correction history is committed atomically."""
        with self.lock:
            if not isinstance(items, (list, tuple)) or not items:
                raise ValidationError(['corrections: a non-empty list is required'])
            raw_candidates = []
            for item in items:
                if not isinstance(item, dict) or set(item) - {'entity', 'action', 'key', 'values', 'reason'} \
                        or not {'entity', 'action', 'reason'} <= set(item):
                    raise ValidationError(['each correction requires entity, action and reason'])
                raw_candidates.append(item)
            candidates = self.store.candidates(raw_candidates, actor)
            # Build indexes once: a bulk exclusion can contain many demand rows, so rescanning the full
            # canonical tables for every candidate would make validation quadratic.
            previous_by_key = {}
            for entity, records in (self.state.records or {}).items():
                for record in records:
                    previous_by_key[(entity, record.key)] = record.values
            excluded_keys = {}
            for entity, records in (self.state.excluded or {}).items():
                entity_keys = excluded_keys.setdefault(entity, set())
                for record in records:
                    previous_by_key.setdefault((entity, record.key), record.values)
                    entity_keys.add(record.key)
            previous_values, seen = [], set()
            for candidate in candidates:
                identity = (candidate.entity, candidate.key)
                if identity in seen:
                    raise ValidationError([f'duplicate correction for {candidate.entity} {list(candidate.key)}'])
                seen.add(identity)
                previous = previous_by_key.get(identity)
                if candidate.action in ('update', 'exclude') and previous is None:
                    raise ValidationError([f'{candidate.entity} {list(candidate.key)}: record not found'])
                if candidate.action == 'exclude' and candidate.key in excluded_keys.get(candidate.entity, ()):
                    raise ValidationError([f'{candidate.entity} {list(candidate.key)}: record already excluded'])
                if candidate.action == 'update' and all(previous.get(k) == v for k, v in candidate.values.items()):
                    raise ValidationError([f'{candidate.entity} {list(candidate.key)}: no field changes value'])
                previous_values.append({k: previous.get(k) for k in candidate.values}
                                        if candidate.action == 'update' else previous
                                        if candidate.action == 'exclude' else None)
            corrections = self.state.corrections + tuple(candidates)
            built = self.state.try_build(corrections)
            stored = self.store.add_many(zip(candidates, previous_values))
            corrections = self.state.corrections + stored
            self.state.commit_build(built, corrections)
            self._rows.clear()
            results = [item.to_dict() for item in stored] if include_records else [item.id for item in stored]
            return {'corrections': results, 'summary': self.summary()}

    def exclude_orders_before(self, before, reason, actor) -> dict:
        """Exclude orders due before an ISO date and every canonical row referencing them or their operations."""
        with self.lock:
            return self._exclude_orders_before_locked(before, reason, actor)

    def _exclude_orders_before_locked(self, before, reason, actor) -> dict:
        if not isinstance(before, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', before):
            raise ValidationError(['before must be an ISO date (YYYY-MM-DD)'])
        try:
            datetime.fromisoformat(before)
        except ValueError:
            raise ValidationError(['before must be a valid date (YYYY-MM-DD)']) from None
        operations_by_order = {}
        for operation in (self.state.records or {}).get('operation', ()):
            order_id = str(operation.values.get('order_id'))
            due = operation.values.get('due')
            if isinstance(due, str):
                operations_by_order.setdefault(order_id, []).append(due)
        orders = []
        for record in (self.state.records or {}).get('order', ()):
            order_id = str(record.key[0])
            due = record.values.get('due')
            if not isinstance(due, str):
                phase_dates = operations_by_order.get(order_id, ())
                due = min(phase_dates) if phase_dates else None
            # Delivery is a calendar date for this filter; a due time on today is not overdue.
            if isinstance(due, str) and due[:10] < before:
                orders.append(record)
        if not orders:
            raise ValidationError([f'no active orders are due before {before}'])
        order_ids = {r.key[0] for r in orders}
        operations = [r for r in (self.state.records or {}).get('operation', ())
                      if r.values.get('order_id') in order_ids]
        operation_ids = {r.key[0] for r in operations}
        # Remove every row with a foreign key into a selected order/operation. For shared nests, retain the nest
        # while other orders still have members; discard the whole nest only when all its members are removed.
        removed_nests = set()
        members = (self.state.records or {}).get('nest_member', ())
        affected_members = [r for r in members if r.values.get('operation_id') in operation_ids]
        nest_ids = {r.values.get('nest_id') for r in affected_members}
        for nest_id in nest_ids:
            remaining = [r for r in members if r.values.get('nest_id') == nest_id
                         and r.values.get('operation_id') not in operation_ids]
            if not remaining:
                removed_nests.add(nest_id)
        ids_by_ref = {'order': order_ids, 'operation': operation_ids, 'nest': removed_nests}
        selected = []
        for entity, records in (self.state.records or {}).items():
            for record in records:
                if entity in ('order', 'operation') and record.key[0] in ids_by_ref[entity]:
                    selected.append((entity, record))
                    continue
                spec = ENTITIES[entity]
                if entity == 'nest' and record.key[0] in removed_nests:
                    selected.append((entity, record))
                    continue
                if entity == 'nest_member' and (record.values.get('operation_id') in operation_ids
                                                or record.values.get('nest_id') in removed_nests):
                    selected.append((entity, record))
                    continue
                if any(field.ref in ids_by_ref and record.values.get(field.name) in ids_by_ref[field.ref]
                       for field in spec.fields if field.ref):
                    selected.append((entity, record))
        items = [{'entity': entity, 'action': 'exclude', 'key': list(record.key), 'values': {}, 'reason': reason}
                 for entity, record in selected]
        result = self.correct_many(items, actor)
        result['affected'] = {'orders': len(orders), 'operations': len(operations),
                              'records': len(items) - len(orders) - len(operations)}
        result['order_ids'] = sorted(str(item.key[0]) for item in orders)
        return result

    def revoke(self, ids, reason, actor) -> dict:
        with self.lock:
            ids = {int(i) for i in ids}
            if not ids or not ids <= {c.id for c in self.state.corrections}:
                raise ValidationError(['only active corrections can be revoked'])
            remaining = tuple(c for c in self.state.corrections if c.id not in ids)
            built = self.state.try_build(remaining)
            count = self.store.revoke(ids, actor, reason)
            self.state.commit_build(built, remaining)
            self._rows.clear()
            return {'revoked': count, 'summary': self.summary()}

    def reload(self) -> dict:
        """Re-read the files (e.g. after a new extraction) and rebuild with the active corrections."""
        with self.lock:
            batches = self.state.read_files(commit=False)
            built = self.state.try_build(self.state.corrections, batches)
            self.state.commit_build(built, self.state.corrections, batches)
            self._rows.clear()
            return self.sources()

    def summary(self) -> dict:
        issues = self.state.build_issues
        return {'data_version': self.state.data_version, 'corrections_active': len(self.state.corrections),
                'issues': {s: sum(1 for i in issues if i.severity == s) for s in ('error', 'block', 'warn')},
                'publishable': bool((self.state.manifest or {}).get('publishable'))}

    # ------------------------------------------------------------------ sources
    def sources(self) -> dict:
        state, binding = self.state, self.state.binding
        manifest = state.manifest or {}
        per_source = {}
        for domain, batches in (state.batches or {}).items():
            for batch in batches:
                item = per_source.setdefault(batch.source, {'domains': {}, 'extracted_at': None, 'fingerprints': set(),
                                                            'errors': []})
                counts = {}
                for record in batch.records:
                    counts[record.entity] = counts.get(record.entity, 0) + 1
                item['domains'][domain] = counts
                item['extracted_at'] = batch.extracted_at.isoformat().replace('+00:00', 'Z')
                if batch.fingerprint:
                    item['fingerprints'].add(batch.fingerprint)
                item['errors'] += [i.detail for i in batch.issues if i.severity == 'error']
        sources = []
        for source in binding.sources:
            files = []
            options = source.options
            if source.connector == 'canonical-json':
                files.append(self._file(binding.base_dir, options.get('path'), 'bundle'))
            elif source.connector == 'file':
                folder = binding.base_dir / options.get('path', '.')
                for entity, name in sorted((options.get('tables') or {}).items()):
                    files.append(self._file(folder, name, entity))
            seen = per_source.get(source.name, {})
            sources.append({'name': source.name, 'connector': source.connector, 'files': files,
                            'domains': seen.get('domains', {}), 'extracted_at': seen.get('extracted_at'),
                            'errors': seen.get('errors', []),
                            'options': {k: v for k, v in options.items() if k in ('delimiter', 'encoding', 'timezone', 'extra_columns')}})
        domains = []
        for d in binding.domains:
            info = manifest.get('domains', {}).get(d.domain, {})
            domains.append({'domain': d.domain, 'sources': list(d.sources), 'records': info.get('records'),
                            'digest': info.get('digest'), 'max_age_minutes': d.max_age_minutes,
                            'policies': {k: v for k, v in (('residual_policy', d.residual_policy),
                                                           ('allocation_order', d.allocation_order),
                                                           ('include_statuses', list(d.include_statuses) if d.include_statuses else None))
                                         if v is not None},
                            'precedence': {k: list(v) for k, v in d.precedence}})
        groups = {}
        for issue in state.build_issues:
            group = groups.setdefault((issue.severity, issue.domain, issue.code),
                                      {'severity': issue.severity, 'domain': issue.domain, 'code': issue.code,
                                       'count': 0, 'examples': []})
            group['count'] += 1
            if len(group['examples']) < 5:
                group['examples'].append({k: v for k, v in issue.to_dict().items() if k in ('detail', 'entity', 'key', 'source', 'units')})
        rank = {'error': 0, 'block': 1, 'warn': 2}
        return {'installation': binding.installation, 'scenario_id': binding.scenario_id,
                'binding_file': str(state.path), 'binding_digest': binding.digest,
                'horizon': manifest.get('horizon'), 'built_at': manifest.get('built_at'),
                'read_at': getattr(state, 'read_at', None) and state.read_at.isoformat().replace('+00:00', 'Z'),
                'scenario_digest': manifest.get('scenario_digest'), 'publishable': manifest.get('publishable'),
                'issue_counts': manifest.get('issues'), 'sources': sources, 'domains': domains,
                'issues': sorted(groups.values(), key=lambda g: (rank[g['severity']], -g['count'], g['code'])),
                'corrections': {'active': len(state.corrections), **(manifest.get('corrections') or {})},
                'perimeter': manifest.get('perimeter'), 'data_version': state.data_version}

    @staticmethod
    def _file(folder: Path, name, label):
        if not name:
            return {'label': label, 'path': None, 'exists': False}
        path = (folder / name).resolve()
        try:
            stat = path.stat()
        except OSError:
            return {'label': label, 'path': str(path), 'exists': False}
        return {'label': label, 'path': str(path), 'name': path.name, 'exists': True, 'bytes': stat.st_size,
                'modified': datetime.fromtimestamp(stat.st_mtime, UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')}
