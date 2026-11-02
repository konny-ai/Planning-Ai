"""Versioned complete workbook -> canonical records -> independently audited plan.

All operational inputs are contained in the selected workbook. The source is
immutable during a calculation. Extra context is preserved; an unsupported
operational constraint is a diagnostic, never a silently ignored column.
"""
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
import hashlib
import json

from planning_factory.adapters.json_io import _no_duplicates, dumps, primitive, scenario_digest, timestamp
from planning_factory.adapters.workbook_io import catalogue, read_tables, write_tables
from planning_factory.canonical.binding import parse_binding
from planning_factory.canonical.builder import build_scenario
from planning_factory.canonical.corrections import Correction, check as check_correction
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.canonical.entities import ENTITIES, REQUIRED_DOMAINS, NORMALIZERS, domain_entities, normalize
from planning_factory.canonical.model import Record, SourceBatch
from planning_factory.domain.models import Scenario, Dependency, Nesting
from planning_factory.domain.setup_contract import load_setup_contract
from planning_factory.domain.validation import ValidationError, compile_tasks
from planning_factory.domain.production_flow import MaterialSupply, MaterialNeed
from planning_factory.engine.material_stage_pipeline import plan_materials_and_setup_stages
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from planning_factory.engine.setup_capacity import StageAllocation, StageBooking, audit_stage_capacity

VERSION = 'planning-workbook-v1'
COUNTS = ('setup_machine_count', 'setup_operator_count', 'run_machine_count', 'run_operator_count')
QUOTAS = ('setup_machine_utilization', 'setup_operator_utilization', 'run_machine_utilization', 'run_operator_utilization')
RESOURCE_FIELDS = frozenset((*COUNTS, *QUOTAS))
UNSUPPORTED = frozenset(('transport', 'production_link', 'material_reservation', 'previous_segment',
                         'previous_member', 'previous_demand', 'qualification', 'attendance_window', 'objective',
                         'status_mapping', 'identity_binding', 'field_precedence'))
NUMERIC_TOLERANCE = Decimal('0.000000001')


def _error(label, detail):
    raise ValidationError([f'{label}: {detail}'])


def decimal(value, label, minimum=Decimal(0)):
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        _error(label, 'numero decimale richiesto')
    try:
        result = Decimal(str(value))
    except InvalidOperation:
        _error(label, 'numero decimale non valido')
    if not result.is_finite() or result < minimum:
        _error(label, f'numero finito >= {minimum} richiesto')
    return result


def integer(value, label, minimum=0):
    number = decimal(value, label, Decimal(minimum))
    if number != number.to_integral_value():
        _error(label, 'numero intero richiesto')
    return int(number)


def seconds(minutes, label):
    """Preserve integer seconds, tolerating only numerical XLSX representation noise.

    1 second displayed in minutes cannot have a finite decimal expansion. This
    tolerance is a billionth of a second, not permission to round real durations.
    """
    value = decimal(minutes, label, Decimal('-1000000000000')) * 60
    rounded = value.to_integral_value()
    if abs(value - rounded) > NUMERIC_TOLERANCE:
        _error(label, 'i minuti devono rappresentare secondi interi')
    return int(rounded)


def bps(value, label):
    number = decimal(value, label)
    if number > 1 or number * 10000 != (number * 10000).to_integral_value():
        _error(label, 'percentuale Excel 0%..100%, precisione massima 0,01%')
    return int(number * 10000)


def _plain(value):
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else format(value, 'f')
    return value


def _bool(value, label):
    if type(value) is bool:
        return value
    if isinstance(value, str) and value.lower() in ('true', 'false', 'sì', 'si', 'no'):
        return value.lower() in ('true', 'sì', 'si')
    _error(label, 'TRUE/FALSE o Sì/No richiesto')


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        _error(label, 'testo non vuoto richiesto; gli identificativi devono restare testo')
    return value


def _instant(value, zone, label):
    if isinstance(value, (int, Decimal)) and not isinstance(value, bool):
        from zoneinfo import ZoneInfo
        from planning_factory.connectors.files import _local_instant
        total=Decimal(value)*86400
        nearest=total.to_integral_value()
        if abs(total-nearest)>Decimal('0.00001'):
            _error(label,'data Excel con frazioni di secondo non ammesse')
        local = datetime(1899, 12, 30) + timedelta(seconds=int(nearest))
        value = _local_instant(local, ZoneInfo(zone))
    try:
        result = timestamp(value)
    except (ValueError, TypeError, ValidationError) as exc:
        _error(label, str(exc))
    if result is None or result.microsecond:
        _error(label, 'data e ora al secondo con fuso esplicito richieste')
    return primitive(result)


def _normalize_tables(tables, locations):
    cat = catalogue()
    defs = {t['code']: t for t in cat['tables']}
    if set(tables) - defs.keys():
        _error('workbook', f'tabelle sconosciute {sorted(set(tables) - defs.keys())}')
    normalized = {}
    for code, rows in tables.items():
        if not isinstance(rows, (list, tuple)):
            _error(code, 'elenco righe richiesto')
        fields = {f['code']: f for f in cat['fields'] if f['table'] == code}
        seen = set()
        normalized[code] = []
        for index, original in enumerate(rows):
            label = locations.get(code, [])[index] if index < len(locations.get(code, [])) else f'{code}[{index + 1}]'
            if not isinstance(original, dict) or set(original) - fields.keys():
                _error(label, 'colonne sconosciute o riga non valida')
            row = {}
            for name, value in original.items():
                f = fields[name]
                if value == 'null':
                    value = None
                if value is None:
                    if not f['nullable']:
                        _error(f'{label}.{name}', 'null non ammesso; vuoto e null sono distinti')
                    row[name] = None
                    continue
                ty = f['type']
                if ty.startswith('Intero'):
                    value = integer(value, f'{label}.{name}', 1 if '> 0' in ty else
                                    -1000000000000 if ty == 'Intero' else 0)
                elif ty.startswith('Decimale') or ty.startswith('Percentuale'):
                    value = decimal(value, f'{label}.{name}', Decimal('-1000000000000') if 'segno' in ty else Decimal(0))
                    if '> 0' in ty and value <= 0:
                        _error(f'{label}.{name}', 'valore positivo richiesto')
                    if ty.startswith('Percentuale'):
                        bps(value, f'{label}.{name}')
                elif ty == 'Booleano':
                    value = _bool(value, f'{label}.{name}')
                elif ty == 'JSON chiave' or (ty == 'Valore tipizzato' and isinstance(value, str) and value[:1] in ('{', '[')):
                    try:
                        value = json.loads(value, object_pairs_hook=_no_duplicates) if isinstance(value, str) else value
                    except ValueError:
                        _error(f'{label}.{name}', 'JSON non valido')
                    if ty == 'JSON chiave' and not isinstance(value, dict):
                        _error(f'{label}.{name}', 'oggetto con chiave composta richiesto')
                elif ty in ('Testo ID', 'Testo', 'Elenco', 'Fuso IANA'):
                    # CSV/imported JSON may contain numeric external identifiers.
                    # XLSX identifier columns are always written as strings.
                    if ty == 'Testo ID' and isinstance(value, (Decimal, int)) and not isinstance(value, bool):
                        value = str(_plain(value))
                    value = _text(value, f'{label}.{name}')
                    if ty == 'Elenco' and f['allowed'] and value not in f['allowed'].split('; '):
                        _error(f'{label}.{name}', f'valore ammesso: {f["allowed"]}')
                row[name] = value
            keys = [k.strip() for k in defs[code]['key'].split(',')]
            if any(k not in row or row[k] is None for k in keys):
                _error(label, f'chiave completa richiesta: {keys}')
            key = tuple(str(row[k]) for k in keys)
            if key in seen:
                _error(label, f'chiave duplicata {key}')
            seen.add(key)
            normalized[code].append(row)
    return normalized


def _one(tables, code):
    rows = tables.get(code, [])
    if len(rows) != 1:
        _error(code, 'esattamente una riga richiesta')
    return rows[0]


def _expand_demands(tables):
    groups = {r['id'] for r in tables.get('resource_group', [])}
    definitions = defaultdict(list)
    for row in tables.get('group_demand', []):
        if row['group_id'] not in groups:
            _error('group_demand', 'gruppo risorse inesistente')
        definitions[row['group_id']].append(row)
    output = []
    operations = {r['id'] for r in tables.get('operation', [])}
    for row in tables.get('phase_group', []):
        if row['operation_id'] not in operations or row['group_id'] not in definitions:
            _error('phase_group', 'fase o configurazione di risorse inesistente')
        for demand in definitions[row['group_id']]:
            output.append({'operation_id': row['operation_id'], **{
                k: demand[k] for k in ('alternative', 'resource_id', 'units')}})
    return output


def _time_inputs(tables):
    for op in tables.get('operation', []):
        mode = op.get('work_time_mode')
        if mode is not None:
            declared = decimal(op.get('declared_work_minutes'), f'{op["id"]}.tempo')
            if mode == 'totale':
                if 'reference_quantity' in op:
                    _error(op['id'], 'quantità riferimento utilizzabile solo nella modalità ogni_N_pezzi')
                total = declared
            else:
                quantity = decimal(op.get('quantity'), f'{op["id"]}.quantità')
                if quantity <= 0:
                    _error(op['id'], 'quantità positiva richiesta per il tempo unitario')
                divisor = decimal(op.get('reference_quantity'), op['id']) if mode == 'ogni_N_pezzi' else Decimal(1)
                if mode == 'per_pezzo' and op.get('reference_quantity',1) != 1:
                    _error(op['id'], 'tempo per pezzo richiede quantità riferimento 1 o vuota')
                if divisor <= 0:
                    _error(op['id'], 'quantità riferimento positiva richiesta')
                total = quantity * declared / divisor
            op['duration_seconds'] = total  # workbook units are minutes; canonical conversion follows
        elif any(k in op for k in ('declared_work_minutes', 'reference_quantity')):
            _error(op['id'], 'modalità del tempo obbligatoria quando si dichiara un tempo unitario')
        frequency = op.get('setup_frequency')
        if frequency is not None:
            occurrences = integer(op.get('setup_occurrences'), op['id'], 1)
            if frequency in ('per_ripresa', 'per_batch') or occurrences != 1:
                _error(op['id'], 'attrezzaggi ripetuti fra segmenti o batch non ancora supportati')
            phases = [r for r in tables.get('setup_phase', []) if r['operation_id'] == op['id']]
            if len(phases) != 1 or 'setup_minutes' not in phases[0]:
                _error(op['id'], 'preparazione nominale richiesta in Setup delle fasi')
            op['setup_seconds'] = decimal(phases[0]['setup_minutes'], op['id']) * occurrences


def _canonical_records(tables, zone):
    records = {name: [] for name in ENTITIES}
    cat = catalogue()
    mappings = defaultdict(list)
    for f in cat['fields']:
        if f['mapping'] and f['mapping'].split('.')[0] in ENTITIES:
            mappings[f['table']].append(f)
    for code, fs in mappings.items():
        if code in ('phase_group', 'group_demand'):
            continue
        for row in tables.get(code, []):
            values = {}
            entity = fs[0]['mapping'].split('.')[0]
            for f in fs:
                if f['code'] not in row:
                    continue
                target = f['mapping'].split('.')[1]
                value = row[f['code']]
                field = ENTITIES[entity].field(target)
                if value is not None:
                    if target.endswith('_seconds'):
                        value = seconds(value, f'{code}.{f["code"]}')
                    elif field.type == 'instant':
                        value = _instant(value, zone, f'{code}.{f["code"]}')
                    elif field.type in ('int', 'nonneg', 'positive', 'weekday'):
                        value = integer(value, f'{code}.{f["code"]}', -1000000000000 if field.type == 'int' else 0)
                    elif field.type in ('decimal', 'decimal0'):
                        value = format(decimal(value, code), 'f')
                    elif field.type == 'date' and isinstance(value, (int, Decimal)):
                        if Decimal(value) != Decimal(value).to_integral_value():
                            _error(code, 'data Excel senza componente oraria richiesta')
                        value = (datetime(1899,12,30) + timedelta(days=float(value))).date().isoformat()
                    elif field.type == 'time' and isinstance(value, (int, Decimal)):
                        part = Decimal(value) * 86400
                        if part < 0 or part >= 86400 or abs(part-part.to_integral_value()) > NUMERIC_TOLERANCE:
                            _error(code, 'orario Excel locale valido al secondo richiesto')
                        n = int(part.to_integral_value())
                        value = f'{n//3600:02d}:{n//60%60:02d}:{n%60:02d}'
                    else:
                        value = _plain(value)
                values[target] = value
            normalized, errors = normalize(ENTITIES[entity], values)
            if errors:
                _error(code, '; '.join(errors))
            records[entity].append(normalized)
    records['operation_demand'] = _expand_demands(tables)
    return records


def _check_business_constraints(tables):
    for code in UNSUPPORTED:
        if tables.get(code):
            _error(code, 'vincolo raccolto ma non ancora supportato: il piano non può ignorarlo')
    for row in tables.get('attendance_policy', []):
        if row.get('mode') == 'interventi':
            _error('attendance_policy', 'interventi localizzati richiedono il relativo supporto temporale')
        if row.get('outside_staff_shifts') and row.get('mode') != 'assente':
            _error('attendance_policy', 'la quota media non autorizza presenza umana fuori turno')
    for row in tables.get('material_supply', []):
        if row.get('quantity_basis') not in (None, 'netta_utilizzabile'):
            _error('material_supply', 'quantità nette utilizzabili richieste')
    for row in tables.get('subcontract_detail', []):
        if row.get('timing_mode') != 'rientro_dichiarato' or any(
                k in row for k in ('lead_time_minutes','time_basis','calendar_id')):
            _error('subcontract_detail', 'attraversamento esterno su calendario non ancora supportato; serve rientro dichiarato')
        ends = {r['operation_id'] for r in tables.get('operation_external_end', [])}
        if row['operation_id'] not in ends:
            _error('subcontract_detail', 'rientro dichiarato assente')
    for row in tables.get('quality_policy', []):
        if row.get('rule') != 'nesting_required_resources' or not row.get('resource_id'):
            _error('quality_policy', 'unico controllo configurabile: nesting_required_resources con risorsa esplicita')
        if row.get('accepted'):
            _error('quality_policy', 'accettazioni di controlli devono essere applicate esplicitamente; non vengono ignorate')
    for row in tables.get('resource', []):
        mode = row.get('capacity_mode')
        if mode and (mode == 'illimitata') != (row.get('capacity', 1) is None):
            _error('resource.capacity_mode', 'modalità e capacità canonica non concordano; illimitata richiede null esplicito')
    for row in tables.get('production_lot', []):
        if row.get('planning_status', 'aperto') != 'aperto':
            _error('production_lot.planning_status', 'lotto non aperto: materializzare stato/residuo sulle fasi prima del calcolo')
    locations = {r['site_id'] for name in ('resource', 'production_lot', 'material_supply')
                 for r in tables.get(name, []) if r.get('site_id')}
    if len(locations) > 1:
        _error('site_id', 'vincoli fra sedi diverse non ancora supportati')
    for row in tables.get('material_supply', []):
        if row.get('date_status') == 'ignota':
            _error('material_supply.date_status', 'disponibilità ignota: dichiarare la data o lasciare il fabbisogno scoperto')


def _business_links(tables, zone):
    """Resolve explicit commercial inputs once, keeping any phase override."""
    orders = {r['id']: r for r in tables.get('order', [])}
    lines = {r['id']: r for r in tables.get('order_line', [])}
    lots = {r['id']: r for r in tables.get('production_lot', [])}
    for line in lines.values():
        if line.get('order_id') not in orders:
            _error('order_line', 'commessa inesistente')
    for lot in lots.values():
        if lot.get('order_id') not in orders:
            _error('production_lot', 'commessa inesistente')
        if lot.get('order_line_id') is not None:
            line = lines.get(lot['order_line_id'])
            if line is None or line['order_id'] != lot['order_id']:
                _error('production_lot', 'riga ordine inesistente o appartenente ad altra commessa')
    for op in tables.get('operation', []):
        if op.get('execution') == 'esterna' and op['id'] not in {
                r['operation_id'] for r in tables.get('operation_external_end', [])}:
            _error(op['id'], 'la fase esterna richiede una fine dichiarata; nessuna conversione implicita in fase interna')
        if op.get('execution') == 'interna' and op['id'] in {
                r['operation_id'] for r in tables.get('operation_external_end', [])}:
            _error(op['id'], 'esecuzione interna incoerente con rientro esterno')
        if op.get('lot_id') is None:
            continue
        lot = lots.get(op['lot_id'])
        if lot is None or lot['order_id'] != op['order_id']:
            _error(op['id'], 'lotto inesistente o appartenente ad altra commessa')
        line = lines.get(lot.get('order_line_id'), {})
        for target, value in (('quantity',lot.get('quantity')),('uom_id',lot.get('uom_id')),
                              ('due',lot.get('due',line.get('promised_due'))),('release',lot.get('release'))):
            if target not in op and value is not None:
                op[target] = value
    operations = {r['id']:r for r in tables.get('operation', [])}
    for code in ('operation_progress', 'nest_member'):
        for row in tables.get(code, []):
            if row.get('uom_id') and row.get('uom_id') != operations.get(row['operation_id'],{}).get('uom_id'):
                _error(code, 'unità diversa o assente sulla fase; normalizzare esplicitamente le quantità prima del calcolo')


def _material_units(tables):
    """Normalize material quantities to the declared item base unit exactly."""
    items = {r['id']:r for r in tables.get('item',[])}
    units = {r['id'] for r in tables.get('uom',[])}
    conversions = {(r['item_id'],r['from_uom'],r['to_uom']):r
                   for r in tables.get('uom_conversion',[])}
    for table in ('material_need','material_supply'):
        for row in tables.get(table,[]):
            item = items.get(row['item_id'])
            if item is None or item.get('base_uom') not in units:
                _error(table, f'{row["item_id"]}: articolo e unità base espliciti richiesti')
            source = row.get('uom_id',item['base_uom'])
            if source not in units:
                _error(table,'unità della quantità sconosciuta')
            quantity = decimal(row['quantity'],table)
            if source != item['base_uom']:
                conv = conversions.get((row['item_id'],source,item['base_uom']))
                if conv is None or conv.get('valid_from') is not None:
                    _error(table,'conversione esatta e non ambigua per articolo richiesta')
                denominator = decimal(conv.get('denominator'),table)
                if denominator <= 0:
                    _error(table,'denominatore conversione positivo richiesto')
                quantity *= decimal(conv.get('numerator'),table) / denominator
            row['quantity'] = quantity
            row['uom_id'] = item['base_uom']


def _project_stages(source, staged_result, contract, sequence):
    """Keep actual order/operation/nesting identities in the user-facing plan.

    The technical staged scheduler uses one synthetic order per task. Its
    bookings are useful, but cannot replace commercial order membership. This
    projection retains one atomic nesting for every shared setup/run, then
    recalculates and independently checks both the plan and stage capacities.
    """
    tasks,_,_=compile_tasks(source)
    original={o.id:o for o in source.operations}
    stage_info={stage_id.removeprefix('job:'):(task_id,stage)
                for stage_id,task_id,stage in staged_result.stage_map}
    members={}
    projected_task={}
    for op in staged_result.scenario.operations:
        task_id,stage=stage_info[op.id]
        ids=tasks[task_id].operation_ids
        members[op.id]=tuple(oid if stage=='run' else 'setup:'+oid for oid in ids)
        projected_task[op.id]=(('nest:' if task_id.startswith('nest:') else 'job:') +
            (task_id.removeprefix('nest:') if stage=='run' else 'setup:'+task_id.removeprefix('nest:'))
            if task_id.startswith('nest:') else 'job:'+members[op.id][0])
    operations=[]; nests=[]; mapping=[]
    for op in staged_result.scenario.operations:
        task_id,stage=stage_info[op.id]
        ids=tasks[task_id].operation_ids
        dependencies=tuple(Dependency(oid,d.kind,d.lag_seconds,d.max_lag_seconds)
                           for d in op.predecessors for oid in members[d.predecessor_id])
        for old,new_id in zip(ids,members[op.id]):
            origin=original[old]
            operations.append(replace(op,id=new_id,order_id=origin.order_id,
                predecessors=dependencies,due=origin.due if stage=='run' else None,
                customer_priority=origin.customer_priority,phase_priority=origin.phase_priority))
        if task_id.startswith('nest:'):
            nests.append(Nesting(projected_task[op.id].removeprefix('nest:'),members[op.id],op.duration_seconds))
        mapping.append((projected_task[op.id],task_id,stage))
    if len({o.id for o in operations})!=len(operations):
        _error('stage_projection','identità setup generata in conflitto con una fase esistente')
    scenario=replace(staged_result.scenario,operations=tuple(sorted(operations,key=lambda o:o.id)),
                     nestings=tuple(sorted(nests,key=lambda n:n.id)),
                     settings=replace(source.settings,mode='forward'))
    result=plan(scenario)
    lookup={new:(old,stage) for new,old,stage in mapping}
    bookings=tuple(StageBooking(lookup[a.task_id][0],lookup[a.task_id][1],a.segments,
        tuple(StageAllocation(d.resource_id,d.units) for d in a.demands)) for a in result.assignments)
    errors=(*verify_plan(scenario,result),*audit_stage_capacity(contract,source,sequence,bookings))
    if errors or result.unscheduled:
        raise ValidationError(['projected workbook stage plan failed independent audit',*errors,
                               *(f'{u.task_id}: {u.code}' for u in result.unscheduled)])
    return scenario,result,bookings,mapping


@dataclass(frozen=True)
class WorkbookInput:
    tables: dict
    records: dict
    built: object
    source: Scenario
    stage_contract: object | None
    machine_sequence: tuple[str, ...]
    fingerprint: str
    materials_ignored: bool = False

    @property
    def validation_accepted(self):
        return self.built.publishable or (self.materials_ignored and all(
            i.code=='material_shortage' for i in self.built.issues if i.severity in ('error','block')))

    def calculate(self):
        if not self.built.publishable:
            blocking = [f'{i.code}: {i.detail}' for i in self.built.issues if i.severity in ('error','block')]
            # The ordinary engine already represents material shortages as
            # unscheduled work. Other blockers must not be bypassed here.
            if (self.stage_contract is not None and not self.materials_ignored) or any(i.code != 'material_shortage' for i in self.built.issues if i.severity in ('error','block')):
                raise ValidationError(['input workbook non pronto al calcolo', *blocking])
        if self.stage_contract is None:
            result = plan(self.source)
            errors = verify_plan(self.source, result)
            if errors:
                raise ValidationError(['workbook plan audit failed', *errors])
            return self.source, result, {'path': 'standard', 'audited': True,
                                        'workbook_sha256': self.fingerprint}
        active = {op.id: 'job:' + op.id for op in self.source.operations}
        for nest in self.source.nestings:
            for oid in nest.operation_ids:
                active[oid] = 'nest:' + nest.id
        supplies = tuple(MaterialSupply(r['id'], r['item_id'], Decimal(r['quantity']), timestamp(r['available_at']))
                         for r in self.records.get('material_supply', []) if r.get('available_at') is not None)
        needs = tuple(MaterialNeed(active[r['operation_id']], r['item_id'], Decimal(r['quantity']))
                      for r in self.records.get('material_need', []) if r['operation_id'] in active)
        if self.materials_ignored:
            supplies,needs=(),()
        source = replace(self.source,
                         operations=tuple(replace(o, setup_seconds=0) for o in self.source.operations),
                         nestings=tuple(replace(n, setup_seconds=0) for n in self.source.nestings))
        task_data,_,_=compile_tasks(source)
        policy=next((r.get('allocation_order') for r in self.tables.get('domain_policy',[]) if r['domain']=='materials'),None)
        if policy=='rank':
            ranks={}
            for r in self.records.get('allocation_rank',[]):
                if r['operation_id'] in active:
                    key=active[r['operation_id']]
                    ranks[key]=min(r['rank'],ranks.get(key,r['rank']))
            priority=tuple(sorted(task_data,key=lambda t:(t not in ranks,ranks.get(t,0),t)))
        else:
            far=datetime.max.replace(tzinfo=UTC)
            priority=tuple(sorted(task_data,key=lambda t:(task_data[t].due is None,task_data[t].due or far,t)))
        result = plan_materials_and_setup_stages(
            source, self.stage_contract, self.machine_sequence, supplies, needs,
            priority, supply_quantities_net_of_external_commitments=True)
        projected,planned,bookings,mapping=_project_stages(result.material_scenario,result.staged,
                                                         self.stage_contract,self.machine_sequence)
        return projected, planned, {
            'path': 'setup-run', 'audited': True, 'workbook_sha256': self.fingerprint,
            'stage_bookings': [primitive(b) for b in bookings],
            'stage_map': [list(m) for m in mapping], 'capacity_scale':10000,
            'material_pegs': [dict(task_id=p.task_id, item_id=p.item_id, supply_id=p.supply_id,
                                   quantity=str(p.quantity)) for p in result.allocation.pegs],
            'erp_publishable': False,
        }


def _stage_contract(tables, scenario):
    operations = {r['id']: r for r in tables.get('operation', [])}
    using = any(RESOURCE_FIELDS.intersection(r) for r in operations.values())
    if not using and not any(tables.get(code) for code in
                            ('stage_requirement','stage_candidate','setup_phase','attendance_policy','machine_state')):
        return None, ()
    explicit_stages=any(tables.get(code) for code in
        ('stage_requirement','stage_candidate','setup_phase','attendance_policy','machine_state'))
    if using and not explicit_stages:
        # A standard bundle already reserves the same physical resources for
        # setup and work. Explicit counts are supported in that exact case:
        # every alternative must agree, quotas are 100%, and zero means zero.
        # Anything else needs the separate stage contract below.
        resource_by_id={r.id:r for r in scenario.resources}
        common=True
        for op in scenario.operations:
            row=operations[op.id]
            if not RESOURCE_FIELDS <= row.keys():
                common=False;break
            for kind in ('machine','operator'):
                count=integer(row[f'run_{kind}_count'],op.id)
                quota=bps(row[f'run_{kind}_utilization'],op.id)
                if integer(row[f'setup_{kind}_count'],op.id)!=count or bps(row[f'setup_{kind}_utilization'],op.id)!=quota or quota!=(10000 if count else 0):
                    common=False;break
                for alt in op.alternatives:
                    demands=[d for d in alt.demands if resource_by_id[d.resource_id].kind==kind]
                    if len(demands)!=count or any(d.units!=1 for d in demands):
                        common=False;break
            if not common:break
        if common:
            return None, ()
    tasks, _, _ = compile_tasks(scenario)
    resource_by_id = {r.id: r for r in scenario.resources}
    if any(t.mode!='forward' or t.fixed_start is not None or t.fixed_alternative is not None or
           t.completed_at is not None or t.external_end is not None for t in tasks.values()):
        _error('setup_phase','il percorso setup/run richiede attività attive in avanti; storico, fissi e modalità diverse richiedono un percorso dedicato')
    phases, positions = [], {}
    source_phases = {r['operation_id']: r for r in tables.get('setup_phase', [])}
    requirements = defaultdict(list)
    for req in tables.get('stage_requirement', []):
        requirements[(req['operation_id'], req['stage'])].append(req)
    candidates = defaultdict(list)
    for row in tables.get('stage_candidate', []):
        candidates[(row['operation_id'], row['stage'], row['requirement_id'])].append(row['resource_id'])
    policies = {(r['operation_id'], r['stage']): r for r in tables.get('attendance_policy', [])}
    keys = {tid if tid.startswith('nest:') else t.operation_ids[0] for tid,t in tasks.items()}
    for code in ('setup_phase', 'stage_requirement', 'stage_candidate', 'attendance_policy'):
        if any(r['operation_id'] not in keys for r in tables.get(code, [])):
            _error(code, 'fase attiva o nesting condiviso inesistente; nessuna riga viene ignorata')
    known_requirements = {(r['operation_id'],r['stage'],r['requirement_id'])
                          for r in tables.get('stage_requirement', [])}
    if set(candidates) - known_requirements:
        _error('stage_candidate', 'requisito di stadio inesistente')
    used_resources = set()
    for task_id, task in tasks.items():
        ids = task.operation_ids
        definitions = [operations[oid] for oid in ids]
        if any(not RESOURCE_FIELDS <= r.keys() for r in definitions):
            _error(task_id, 'gli otto campi numero/utilizzo sono obbligatori per tutte le fasi del percorso setup/run')
        if len({tuple(str(r[k]) for k in sorted(RESOURCE_FIELDS)) for r in definitions}) != 1:
            _error(task_id, 'i membri dello stesso nesting devono concordare sui requisiti condivisi')
        key = task_id if task_id.startswith('nest:') else ids[0]
        phase = source_phases.get(key)
        if phase is None:
            _error(task_id, 'dati di attrezzaggio e macchina esplicita richiesti')
        rank = integer(phase.get('execution_rank'), task_id, 1)
        if rank in positions:
            _error(task_id, 'ordine di esecuzione duplicato')
        positions[rank] = task_id
        main = phase.get('machine_id')
        setup_total = (next(n.setup_seconds for n in scenario.nestings if 'nest:'+n.id == task_id)
                       if task_id.startswith('nest:') else next(o.setup_seconds for o in scenario.operations if o.id == key))
        if 'setup_minutes' in phase and any('setup_frequency' not in r for r in definitions):
            if seconds(phase['setup_minutes'], task_id) != setup_total:
                _error(task_id, 'preparazione nominale e setup totale non concordano; dichiarare la frequenza o allineare il totale condiviso')
        if main not in resource_by_id or resource_by_id[main].kind != 'machine':
            _error(task_id, 'macchina principale sconosciuta o non macchina')
        stages = {}
        raw = definitions[0]
        for stage in ('setup', 'run'):
            stage_reqs = []
            for kind in ('machine', 'operator'):
                count = integer(raw[f'{stage}_{kind}_count'], task_id)
                quota = bps(raw[f'{stage}_{kind}_utilization'], task_id)
                if (count == 0) != (quota == 0):
                    _error(task_id, f'{stage}/{kind}: numero e utilizzo entrambi zero oppure entrambi positivi')
                rows = [r for r in requirements[(key, stage)] if r['kind'] == kind]
                if count == 0:
                    continue
                if len(rows) != 1:
                    _error(task_id, f'{stage}/{kind}: un requisito con candidate esplicite richiesto')
                req = rows[0]
                # Counts/percentages in this normalized view are derived, not
                # another editable owner. Changing the phase's eight values
                # must not require deleting candidate rows or updating caches.
                ids_for_req = candidates[(key, stage, req['requirement_id'])]
                stage_reqs.append(dict(kind=kind, count=count, occupancy_bps=quota, candidate_ids=ids_for_req))
            for req in requirements[(key, stage)]:
                if req['kind'] in ('machine', 'operator'):
                    continue
                stage_reqs.append(dict(kind=req['kind'], count=integer(req.get('count'), task_id, 1),
                                       occupancy_bps=integer(req.get('occupancy_bps'), task_id, 1),
                                       candidate_ids=candidates[(key, stage, req['requirement_id'])]))
            for req in stage_reqs:
                used_resources.update(req['candidate_ids'])
            policy = policies.get((key, stage))
            if policy is None:
                _error(task_id, f'politica di presidio {stage} obbligatoria')
            op_count = integer(raw[f'{stage}_operator_count'], task_id)
            op_quota = bps(raw[f'{stage}_operator_utilization'], task_id)
            if policy['mode'] == 'assente' and op_count != 0 or policy['mode'] == 'continuo' and (not op_count or op_quota != 10000) or policy['mode'] == 'quota_media' and (not op_count or not 0 < op_quota < 10000):
                _error(task_id, f'politica di presidio {stage} incoerente con numero e percentuale')
            stages[stage] = stage_reqs
        if 'target_state' not in phase:
            _error(task_id, 'stato richiesto esplicito o null richiesto')
        phases.append(dict(id=task_id, machine_id=main, target_state=phase['target_state'],
                           setup_seconds=setup_total,
                           setup_locked=phase.get('setup_locked', False),
                           setup_requirements=stages['setup'], run_requirements=stages['run']))
    states = {}
    for row in tables.get('machine_state', []):
        if row['resource_id'] not in resource_by_id or resource_by_id[row['resource_id']].kind != 'machine':
            _error('machine_state', 'risorsa macchina inesistente')
        if 'state' not in row:
            _error('machine_state', 'stato esplicito o null richiesto')
        if timestamp(_instant(row.get('observed_at'), 'UTC', 'machine_state')) != scenario.start:
            _error('machine_state', 'lo stato iniziale deve essere riferito all’inizio dello scenario')
        states[row['resource_id']] = row['state']
    if any(p['machine_id'] not in states for p in phases):
        _error('machine_state', 'stato iniziale dichiarato per ogni macchina principale richiesto')
    if not used_resources <= resource_by_id.keys():
        _error('stage_candidate','risorsa candidata sconosciuta')
    if any(resource_by_id[rid].efficiency_bp!=10000 for rid in used_resources):
        _error('resource.efficiency_bp','efficienza diversa da 100% nel percorso setup/run richiede supporto dedicato; non viene ignorata')
    if any(resource_by_id[rid].capacity is None for rid in used_resources):
        _error('resource.capacity','il percorso setup/run richiede capacità finita esplicita')
    states={rid:state for rid,state in states.items() if rid in used_resources}
    contract = load_setup_contract(json.dumps(dict(schema_version=1,
        resources=[dict(id=rid, kind=resource_by_id[rid].kind) for rid in sorted(used_resources)],
        initial_machine_states=states, phases=phases)))
    return contract, tuple(positions[k] for k in sorted(positions))


def compile_tables(tables, *, fingerprint='', locations=None):
    """Build using the existing canonical composition and quality checks."""
    locations = locations or {}
    normalized = _normalize_tables(deepcopy(tables), locations)
    meta = _one(normalized, 'scenario')
    if meta.get('model_version') != VERSION or meta.get('update_mode') != 'completo':
        _error('scenario', f'versione {VERSION} e aggiornamento completo richiesti')
    zone = meta.get('timezone')
    if not zone:
        _error('scenario', 'fuso orario richiesto')
    NORMALIZERS['timezone'](zone)
    if meta.get('canonical_version')!=1 or meta.get('engine_version')!='0.2.0':
        _error('scenario','versione canonica 1 e motore 0.2.0 richiesti per questa implementazione')
    now = timestamp(_instant(meta.get('build_at'), zone, 'scenario.build_at'))
    snapshot = timestamp(_instant(meta.get('snapshot_at'), zone, 'scenario.snapshot_at'))
    _check_business_constraints(normalized)
    for row in normalized.get('provenance',[]):
        if row.get('transformation','').startswith('unsupported_constraint:'):
            _error('provenance', f'{row["transformation"]}: vincolo sorgente non applicato, da risolvere prima del calcolo')
    _business_links(normalized,zone)
    _material_units(normalized)
    _time_inputs(normalized)
    records = _canonical_records(normalized, zone)
    policies = {r['domain']: r for r in normalized.get('domain_policy', [])}
    domains = {}
    for domain, row in policies.items():
        if row.get('coverage') == 'incompleto':
            _error(domain, 'dominio incompleto: completare i dati prima del calcolo')
        populated = any(records[name] for name in domain_entities(domain))
        if row.get('coverage') == 'non_applicabile':
            if populated or not row.get('reason'):
                _error(domain, 'non applicabile richiede motivazione e nessun dato operativo')
            continue
        if row.get('coverage') != 'gestito':
            _error(domain, 'copertura gestito/non_applicabile/incompleto richiesta')
        value = {'sources': ['workbook']}
        for key in ('residual_policy', 'allocation_order', 'max_age_minutes'):
            if key in row:
                value[key] = _plain(row[key])
        if row.get('nest_filter_mode') == 'stati_elencati':
            value['include_statuses'] = [r['status'] for r in normalized.get('nest_status_policy', [])]
        domains[domain] = value
    if not set(REQUIRED_DOMAINS) <= domains.keys() or 'rules' not in domains:
        _error('domain_policy', 'calendars, resources, orders, routings e rules esplicitamente gestiti richiesti')
    for entity, rows in records.items():
        if rows and ENTITIES[entity].domain not in domains:
            _error(entity, 'dati presenti in dominio non dichiarato gestito')
    raw_binding = dict(schema_version=1, installation=meta.get('dataset_id'),
        scenario=dict(id=meta['id'], start=_instant(meta.get('start'), zone, 'scenario.start'),
                      end=_instant(meta.get('end'), zone, 'scenario.end')),
        profile=meta.get('profile_id'), sources={'workbook': {'connector': 'canonical-json'}}, domains=domains)
    nesting_required = [r['resource_id'] for r in normalized.get('quality_policy',[]) if r.get('resource_id')]
    if nesting_required:
        raw_binding['quality']={'nesting_required_resources':nesting_required}
    perimeter = {}
    for row in normalized.get('perimeter', []):
        name, value = row.get('criterion'), row.get('value')
        if name in ('order_ids','exclude_order_ids','customer_ids','exclude_customer_ids'):
            perimeter.setdefault(name, []).append(_text(value, 'perimetro'))
        elif name in ('due_from','due_to'):
            perimeter[name] = _instant(value, zone, 'perimetro')
        elif name == 'include_without_due':
            perimeter[name] = _bool(value, 'perimetro')
    if perimeter:
        raw_binding['perimeter'] = perimeter
    binding = parse_binding(raw_binding, Path('.'), frozenset(('canonical-json',)))
    source_rows = {r['id']:r for r in normalized.get('source',[])}
    ages = {}
    for d in domains:
        source_ids = [r['source_id'] for r in normalized.get('domain_source',[]) if r['domain']==d]
        dates = []
        for sid in source_ids or source_rows:
            if sid not in source_rows:
                _error('domain_source','fonte inesistente')
            dates.append(timestamp(_instant(source_rows[sid].get('extracted_at'),zone,'source.extracted_at')))
        ages[d] = min(dates) if dates else snapshot
    batches = {d: [SourceBatch('workbook', 'planning-workbook', d, ages[d],
        tuple(Record(e, r, locations.get(e, [])[i] if i < len(locations.get(e, [])) else f'{e}:{i+1}')
              for e in domain_entities(d) for i,r in enumerate(records[e])), fingerprint=fingerprint)] for d in domains}
    corrections = []
    if meta.get('input_basis') not in ('effettivi', 'base_con_correzioni'):
        _error('scenario.input_basis', 'effettivi oppure base_con_correzioni richiesto')
    for row in normalized.get('correction', []):
        if row.get('dataset_id') != meta.get('dataset_id'):
            _error('correction', 'ambito correzione diverso dallo scenario')
        if meta['input_basis'] == 'effettivi' or row.get('status') != 'attiva':
            continue
        entity = row['entity']
        if entity not in ENTITIES or not isinstance(row.get('record_key'), dict):
            _error('correction', 'entità e chiave composta canoniche richieste')
        key = tuple(row['record_key'].get(k) for k in ENTITIES[entity].key)
        action = 'exclude' if row['action']=='escludi' else 'update'
        new_value = None if row['action']=='imposta_null' else row.get('new_value')
        if action != 'exclude' and new_value is not None and row['field'].endswith('_seconds'):
            new_value = seconds(new_value,'correction.new_value')
        values = {} if action=='exclude' else {row['field']:new_value}
        key, values = check_correction(entity, action, key, values)
        corrections.append(Correction(len(corrections)+1, entity, key, action, values,
            _text(row.get('reason'), 'correction.reason'), _text(row.get('actor'),'correction.actor'),
            _instant(row.get('at'),zone,'correction.at')))
    built = build_scenario(binding, batches, now, corrections=tuple(corrections), keep_records=True)
    if built.scenario is None:
        raise ValidationError([f'{i.code}: {i.detail}' for i in built.issues if i.severity=='error'])
    effective = {name: [dict(r.values) for r in built.records.get(name, ())] for name in ENTITIES}
    source = built.material_free or built.scenario
    active_guidance=[]
    ignore_material=False
    for row in normalized.get('guidance',[]):
        if row.get('status') not in ('bozza','confermata','revocata'):
            _error('guidance','stato esplicito richiesto')
        if row['status']!='confermata':
            continue
        _text(row.get('confirmed_by'),'guidance.confirmed_by')
        _instant(row.get('confirmed_at'),zone,'guidance.confirmed_at')
        if row.get('valid_from') and now<timestamp(_instant(row['valid_from'],zone,'guidance.valid_from')):
            continue
        if row.get('valid_to') and now>=timestamp(_instant(row['valid_to'],zone,'guidance.valid_to')):
            continue
        if row['action']=='note':
            _text(row.get('note'),'guidance.note')
        elif row['action']=='ignore_material':
            if row.get('scope_entity') or row.get('scope_key') or built.material_free is None:
                _error('guidance','ignore_material riguarda l’intero scenario con un dominio materiali separato')
            ignore_material=True
        active_guidance.append(row['id'])
    material_policy=policies.get('materials',{}).get('material_policy','vincolanti')
    if (material_policy=='ignorati_con_regola') != ignore_material:
        _error('domain_policy','politica materiali e indicazione ignore_material confermata non concordano')
    stage, sequence = _stage_contract(normalized, source)
    if stage is None and not ignore_material:
        source = built.scenario
    return WorkbookInput(normalized, effective, built, source, stage, sequence, fingerprint,ignore_material)


def load_workbook(path):
    tables, locations, digest = read_tables(path)
    return compile_tables(tables, fingerprint=digest, locations=locations)


def tables_from_records(records, *, scenario_id, start, end, extracted_at, profile_id,
                        dataset_id='workbook', domain_options=None):
    """Export canonical inputs before rules/residual transformations are applied.

    Do not use an already adjusted Scenario to pretend to recover unit times,
    original quantities, machine state or setup-specific operator candidates.
    """
    cat = catalogue()
    tables = {t['code']: [] for t in cat['tables']}
    fs = {f['mapping']: f for f in cat['fields'] if f['mapping']}
    for entity, rows in records.items():
        if entity == 'operation_demand':
            continue
        if entity not in ENTITIES:
            _error(entity, 'entità canonica sconosciuta')
        for row in rows:
            values = {}
            for code, value in row.items():
                field = fs.get(f'{entity}.{code}')
                if field is None:
                    _error(entity, f'campo senza collocazione Excel: {code}')
                values[field['code']] = Decimal(value) / 60 if code.endswith('_seconds') and value is not None else value
            tables[entity].append(values)
    groups, demands = {}, defaultdict(list)
    for row in records.get('operation_demand', []):
        demands[row['operation_id']].append(row)
    for oid, rows in sorted(demands.items()):
        signature = json.dumps(sorted([{k:r[k] for k in ('alternative','resource_id','units') if k in r}
                                       for r in rows], key=lambda r:(r['alternative'],r['resource_id'])),sort_keys=True)
        gid = 'G-' + hashlib.sha256(signature.encode()).hexdigest()[:20]
        tables['phase_group'].append(dict(operation_id=oid, group_id=gid))
        if gid not in groups:
            groups[gid] = True
            tables['resource_group'].append(dict(id=gid,description='Configurazione condivisa'))
            tables['group_demand'] += [dict(group_id=gid, alternative=r['alternative'],
                alternative_code=f'{gid}-A{r["alternative"]}',resource_id=r['resource_id'],units=r.get('units',1)) for r in rows]
    tables['scenario'] = [dict(id=scenario_id,dataset_id=dataset_id,model_version=VERSION,
        canonical_version=1,engine_version='0.2.0',snapshot_at=extracted_at,build_at=extracted_at,
        start=start,end=end,timezone='UTC',profile_id=profile_id,input_basis='effettivi',update_mode='completo')]
    domains = set(REQUIRED_DOMAINS) | {'rules'} | {ENTITIES[e].domain for e,r in records.items() if r}
    for domain in sorted(domains):
        policy = dict(domain=domain,coverage='gestito')
        opts = (domain_options or {}).get(domain,{})
        policy.update({k:v for k,v in opts.items() if k in ('residual_policy','allocation_order','max_age_minutes')})
        if domain=='progress' and records.get('operation_progress') and 'residual_policy' not in policy:
            _error(domain,'politica di residuo originale richiesta per esportare i consuntivi')
        if domain=='materials' and 'allocation_order' not in policy:
            _error(domain,'politica materiali originale richiesta per esportare i fabbisogni')
        if opts.get('include_statuses') is not None:
            policy['nest_filter_mode']='stati_elencati'
            tables['nest_status_policy']=[dict(status=s) for s in opts['include_statuses']]
        tables['domain_policy'].append(policy)
    tables['source']=[dict(id='workbook',system='Snapshot canonico',environment=dataset_id,
                           extracted_at=extracted_at,data_nature='effettivi',
                           content_hash=hashlib.sha256(json.dumps(records,sort_keys=True).encode()).hexdigest())]
    return tables


def export_scenario(scenario, path, *, extracted_at, data_nature='misti'):
    bundle = scenario_to_bundle(scenario, timestamp(extracted_at))
    tables = tables_from_records(bundle['records'],scenario_id=scenario.id,start=primitive(scenario.start),
        end=primitive(scenario.end),extracted_at=extracted_at,profile_id='default')
    tables['source'][0]['data_nature']=data_nature
    return write_tables(path,tables)


def template_tables():
    """An intentionally incomplete template: no invented physical input or dates."""
    from planning_factory.domain.models import Settings
    from planning_factory.canonical.entities import DOMAINS
    tables={t['code']:[] for t in catalogue()['tables']}
    tables['scenario']=[dict(id='NUOVO-SCENARIO',dataset_id='NUOVO-ARCHIVIO',model_version=VERSION,
        canonical_version=1,engine_version='0.2.0',timezone='Europe/Rome',profile_id='standard',
        input_basis='base_con_correzioni',update_mode='completo')]
    defaults=Settings()
    tables['profile']=[dict(id='standard',**primitive(defaults))]
    tables['domain_policy']=[dict(domain=d,coverage='incompleto',reason='Da configurare esplicitamente') for d in DOMAINS]
    tables['source']=[dict(id='workbook',system='Excel',environment='NUOVO-ARCHIVIO',data_nature='misti')]
    return tables


def export_binding(binding_path, path, *, now, data_nature='misti'):
    """Materialize existing CSV/JSON/SQL connector inputs into one workbook.

    Called explicitly by the operator; connector permissions remain unchanged.
    The workbook holds already composed, canonical identities and effective
    corrections. Original source references remain audit metadata, never live
    external dependencies of the resulting workbook.
    """
    from planning_factory import connectors
    from planning_factory.canonical.binding import load_binding
    from planning_factory.canonical.builder import read_sources
    binding = load_binding(Path(binding_path),connectors.available())
    now = timestamp(now)
    batches = read_sources(binding,connectors.create,now)
    built = build_scenario(binding,batches,now,keep_records=True)
    if built.scenario is None:
        raise ValidationError([f'{i.code}: {i.detail}' for i in built.issues if i.severity=='error'])
    records = {e:[dict(r.values) for r in built.records.get(e,())] for e in ENTITIES}
    options = {d.domain:{k:getattr(d,k) for k in ('residual_policy','allocation_order','max_age_minutes','include_statuses')
                         if getattr(d,k) is not None} for d in binding.domains}
    tables = tables_from_records(records,scenario_id=binding.scenario_id,
        start=primitive(binding.horizon_start),end=primitive(binding.horizon_end),
        extracted_at=primitive(now),profile_id=binding.profile or built.manifest.get('profile'),
        dataset_id=binding.installation,domain_options=options)
    tables['source'] = []
    for domain,items in batches.items():
        for n,batch in enumerate(items):
            sid=f'{domain}:{batch.source}:{n}'
            tables['source'].append(dict(id=sid,system=batch.connector,environment=binding.installation,
                extracted_at=primitive(batch.extracted_at),data_nature=data_nature,
                content_hash=batch.fingerprint or 'non_dichiarata'))
            tables['domain_source'].append(dict(domain=domain,source_id=sid))
    counter=0
    for entity,rows in built.records.items():
        for row in rows:
            for original_source,original_key in row.provenance:
                counter+=1
                source_ids=[r['source_id'] for r in tables['domain_source'] if r['domain']==ENTITIES[entity].domain]
                tables['provenance'].append(dict(id=f'P{counter}',entity=entity,
                    record_key=dict(zip(ENTITIES[entity].key,row.key)),source_id=next(
                        (s for s in source_ids if f':{original_source}:' in s),source_ids[0]),
                    external_key=original_key,data_nature='dichiarato'))
    for field in ('order_ids','exclude_order_ids','customer_ids','exclude_customer_ids','due_from','due_to','include_without_due'):
        if binding.perimeter and field in binding.perimeter:
            value=binding.perimeter[field]
            for n,item in enumerate(value if isinstance(value,list) else [value]):
                tables['perimeter'].append(dict(id=f'{field}:{n}',criterion=field,value=item,
                                                reason='Perimetro della configurazione esportata'))
    write_tables(path,tables)
    return tables,built


def include_factory_source(tables, snapshot):
    """Attach rich Factory source evidence without inventing setup eligibility.

    `unitSec`, setup and eight resource values are present in the raw export.
    Without setup candidate IDs and an observed machine state they remain
    source evidence; the exporter does not claim a generated SetupContract.
    Physical restrictions absent from the common contract block calculation.
    """
    result=deepcopy(tables)
    if not isinstance(snapshot,dict) or not isinstance(snapshot.get('operations'),list):
        _error('factory_source','export Factory con elenco operations richiesto')
    known={r['id'] for r in result.get('operation',[])}
    for row in snapshot['operations']:
        oid='/'.join(str(row.get(k,'')) for k in ('orderId','lotId','phaseId'))
        if oid not in known:
            continue
        base=dict(entity='operation',record_key={'id':oid},source_id='factory-evidence',
                  external_key=str(row.get('operationId',oid)),data_nature='dichiarato')
        result['provenance'].append(dict(id=f'FACTORY:{oid}',**base,
            original_value=row,transformation='factory_source_evidence'))
        for name in ('splitMinDurationSec','splitMaxFractions','overlapPct'):
            if row.get(name) not in (None,0,'0'):
                result['provenance'].append(dict(id=f'FACTORY:{oid}:{name}',**base,
                    original_value=row[name],transformation='unsupported_constraint:'+name))
    result['source'].append(dict(id='factory-evidence',system='Factory export normalizzato',
        environment=result['scenario'][0]['dataset_id'],extracted_at=result['scenario'][0]['snapshot_at'],
        data_nature='misti',content_hash=hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()))
    return result


def summary(workbook):
    """Evidence for operators: preserve context, expose paths and limitations."""
    return dict(schema_version=1,format=VERSION,workbook_sha256=workbook.fingerprint,
        validation_accepted=workbook.validation_accepted,
        scenario_id=workbook.source.id,scenario_digest=scenario_digest(workbook.source),
        planning_path='setup-run' if workbook.stage_contract else 'standard',
        materials_ignored=workbook.materials_ignored,
        rows={name:len(rows) for name,rows in workbook.tables.items()},
        canonical_rows={name:len(rows) for name,rows in workbook.records.items() if rows},
        issues=[i.to_dict() for i in workbook.built.issues],
        preserved_context=['clienti, descrizioni e attributi non usati da regole',
                           'provenienza, corrispondenze e fonti già composte',
                           'storico delle correzioni quando input_basis=effettivi'],
        erp_connected=False,erp_publishable=False)
