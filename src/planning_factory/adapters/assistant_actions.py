"""Actions of the Control Room assistant on behalf of the signed-in user.

The assistant does what the user asks in the conversation: it changes the view and its filters, recalculates
with other parameters, simulates downtimes and fixed starts, manages the company rules, corrects input data,
saves revisions and (administrators only) rebuilds the source from the ERP with another start date.

Every action runs with the user's role and through the same validated paths as the Control Room buttons:
each plan is computed by the engine and checked by the independent audit. What persists is written to the
audit log with the user's name and comes back with an undo token the user can use from the chat. Interface
actions (view, filters, focus) are executed by the browser, in order, after the answer.
"""
from dataclasses import replace
from datetime import UTC, date, datetime
import json
import re
import secrets
import threading

from planning_factory.adapters.dashboard_api import (ALLOWED_SETTINGS, BUFFER_SETTING, MAX_BUFFER_SECONDS, _settings_override,
                                                     apply_settings)
from planning_factory.adapters.json_io import dumps
from planning_factory.adapters.planning_tools import PlanningTools
from planning_factory.adapters.rule_memory import FIELDS as RULE_FIELDS
from planning_factory.adapters.service import ROLES
from planning_factory.domain.validation import ValidationError

PLAN_VIEWS = ('machines', 'orders', 'nesting', 'loads', 'materials', 'progress', 'deliveries', 'calendars',
              'daily', 'parameters', 'assistant', 'memory')
ENTITY_VIEW = {
    'order': 'dati-ordini', 'operation': 'dati-ordini', 'operation_demand': 'dati-ordini',
    'operation_dependency': 'dati-ordini', 'resource': 'dati-risorse', 'calendar': 'dati-calendari',
    'calendar_shift': 'dati-calendari', 'calendar_holiday': 'dati-calendari', 'calendar_exception': 'dati-calendari',
    'calendar_exception_shift': 'dati-calendari', 'calendar_closure': 'dati-calendari', 'nest': 'dati-nesting',
    'nest_member': 'dati-nesting', 'material_supply': 'dati-materiali', 'material_need': 'dati-materiali',
    'allocation_rank': 'dati-materiali', 'operation_progress': 'dati-avanzamento', 'operation_state': 'dati-avanzamento',
    'operation_external_end': 'dati-contolavoro', 'profile': 'dati-regole', 'rule': 'dati-regole'}
DATA_VIEWS = tuple(sorted(set(ENTITY_VIEW.values()))) + ('fonti', 'correzioni')
CAUSES = ('capacity', 'expired', 'release', 'lead_time', 'unplaced', 'subcontract', 'other')
RESOURCE_KINDS = ('machine', 'operator', 'tool', 'supplier', 'all')
DATA_STATUS = ('', 'corrected', 'updated', 'inserted', 'excluded')
MAX_WRITES = 8
_DAY = re.compile(r'^\d{4}-\d{2}-\d{2}$')

TEXT = {'type': 'string', 'minLength': 1, 'maxLength': 256}
EMPTY = {'type': 'object', 'properties': {}, 'additionalProperties': False}


def _schema(properties, required=()):
    return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}


_EDIT_CLOSURE = _schema({'resource_id': TEXT, 'start': TEXT, 'end': TEXT, 'reason': TEXT},
                        ('resource_id', 'start', 'end', 'reason'))
_EDIT_FIXED = _schema({'task_id': TEXT, 'start': TEXT, 'alternative': {'type': 'integer', 'minimum': 0}},
                      ('task_id', 'start', 'alternative'))
_RULE = _schema({
    'action': {'type': 'string', 'enum': ['set_priority', 'set_customer_priority', 'add_setup_seconds',
                                          'note', 'ignore_material']},
    'value': {'type': 'integer'}, 'order_id': TEXT, 'resource_id': TEXT, 'customer_id': TEXT, 'material': TEXT,
    'min_thickness_mm': TEXT, 'max_thickness_mm': TEXT, 'valid_from': TEXT, 'valid_to': TEXT,
    'note': {'type': 'string', 'maxLength': 2000}}, ('action',))

# (name, minimum role, needs, description, input schema). ``needs`` is 'data' (canonical binding),
# 'regeneration' (configured rebuild command) or None.
CATALOG = (
    ('show_view', 'reparto', None,
     'Open a screen of the Control Room for the user. Plan screens: ' + ', '.join(PLAN_VIEWS) +
     '. Input-data screens (dati-*), fonti (sources) and correzioni (corrections ledger) when available.',
     _schema({'view': {'type': 'string', 'enum': list(PLAN_VIEWS + DATA_VIEWS)}}, ('view',))),
    ('filter_view', 'reparto', None,
     'Filter what the user sees. text: search box of the current (or given) screen; resource_kind: machine '
     'screens; delay_cause and customer_code: deliveries screen; entity, text and data_status: input-data '
     'screens (opens the screen of the entity). Empty string clears a filter.',
     _schema({'view': {'type': 'string', 'enum': list(PLAN_VIEWS + DATA_VIEWS)},
              'text': {'type': 'string', 'maxLength': 120},
              'resource_kind': {'type': 'string', 'enum': list(RESOURCE_KINDS)},
              'delay_cause': {'type': 'string', 'enum': list(CAUSES) + ['']},
              'customer_code': {'type': 'string', 'maxLength': 64},
              'entity': {'type': 'string', 'enum': sorted(ENTITY_VIEW)},
              'data_status': {'type': 'string', 'enum': list(DATA_STATUS)}})),
    ('focus', 'reparto', None,
     'Show one task (opens its detail), one order (expands it in the order Gantt) and/or move the time window '
     'to a day (YYYY-MM-DD) with a span of days.',
     _schema({'task_id': TEXT, 'order_id': TEXT, 'day': {'type': 'string', 'pattern': '^\\d{4}-\\d{2}-\\d{2}$'},
              'span_days': {'type': 'integer', 'minimum': 1, 'maximum': 62}})),
    ('recalculate', 'planner', None,
     'Recalculate the plan with other planning parameters (only the keys to change): mode forward|backward|auto, '
     'auto_rule due_distance|slack, auto_backward_days, priority_weight, due_weight, customer_weight, '
     'auto_slack_seconds, alternative_switch_min_gain_seconds, delivery_buffer_seconds (nonnegative integers). '
     'delivery_buffer_seconds is the delivery margin: the engine plans against the promised due date minus this many '
     'elapsed seconds (2 days = 172800) for every order that has no margin rule of its own; 0 removes the margin. '
     'Use it for "with N days of margin on the delivery". Keeps the current '
     'simulation. The user sees the new plan; returns before/after figures. Nothing is saved: a margin or a '
     'weight that must hold for every plan goes in the profile with correct_data (entity profile) or, per '
     'customer or order, in a company rule.',
     _schema({'settings': {'type': 'object', 'additionalProperties': False, 'properties': {
         'mode': {'type': 'string', 'enum': ['forward', 'backward', 'auto']},
         'auto_rule': {'type': 'string', 'enum': ['due_distance', 'slack']},
         **{k: {'type': 'integer', 'minimum': 0, 'maximum': MAX_BUFFER_SECONDS if k == BUFFER_SETTING else 1000000}
            for k in sorted(ALLOWED_SETTINGS - {'mode', 'auto_rule'})}}}}, ('settings',))),
    ('simulate', 'planner', None,
     'Apply a what-if simulation and show it: downtimes of resources (UTC ISO times) and fixed starts of tasks '
     '(job:ID, or nest:ID for a whole cutting program, with the resource alternative index, 0 when the nesting has one machine), added to the current simulation unless replace is true; '
     'unfix_task_ids removes fixed starts. Nothing is saved; returns before/after figures.',
     _schema({'closures': {'type': 'array', 'maxItems': 10, 'items': _EDIT_CLOSURE},
              'fixed_tasks': {'type': 'array', 'maxItems': 10, 'items': _EDIT_FIXED},
              'unfix_task_ids': {'type': 'array', 'maxItems': 20, 'items': TEXT},
              'replace': {'type': 'boolean'}})),
    ('reset_simulation', 'planner', None, 'Drop every simulated edit and show the source plan again.', EMPTY),
    ('list_company_rules', 'reparto', None,
     'Company memory: every rule and textual indication with id, version, status (draft, confirmed, revoked) '
     'and scope, plus the scope values the source supports.', EMPTY),
    ('create_rule', 'planner', None,
     'Create a company rule (draft). Numeric rules (set_priority, set_customer_priority, add_setup_seconds) need '
     'an explicit scope (order_id, resource_id, customer_id, material, thickness interval in mm, combinable) and '
     'an integer value (priority: lower runs first, levels in list_company_rules); put the user\'s sentence in '
     'note. Validity valid_from/valid_to (UTC) is the real period the rule holds. A textual indication that is not one of them is action note with the text in note (scope optional, '
     'no value): once confirmed you receive it in every conversation. "Do not consider the material" and the '
     'like is action ignore_material, without scope or value: once confirmed the plan of the source ignores '
     'material availability. Optional validity in UTC. confirm true also confirms it after the engine has '
     'verified the plan with it; confirmed rules change the plan of the source.',
     _schema({'rule': _RULE, 'confirm': {'type': 'boolean'}}, ('rule',))),
    ('decide_rule', 'planner', None,
     'Confirm a draft rule (verified by the engine), revoke a confirmed rule or discard a draft (revoke).',
     _schema({'rule_id': TEXT, 'decision': {'type': 'string', 'enum': ['confirm', 'revoke']}},
             ('rule_id', 'decision'))),
    ('get_data_rows', 'reparto', 'data',
     'Read input data records of one canonical entity (e.g. order, operation, resource, material_supply) with '
     'their key, values and correction status; text searches every value, filters match field contains value.',
     _schema({'entity': {'type': 'string', 'enum': sorted(ENTITY_VIEW)}, 'text': {'type': 'string', 'maxLength': 120},
              'filters': {'type': 'object', 'additionalProperties': {'type': 'string', 'maxLength': 120}},
              'sort': {'type': 'string', 'maxLength': 64}, 'descending': {'type': 'boolean'},
              'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 50}},
             ('entity',))),
    ('correct_data', 'planner', 'data',
     'Correct input data as a tracked correction (never written to the ERP): update fields of a record, insert '
     'a record or exclude one. key is the list of key values of the record; reason is mandatory. The scenario '
     'is rebuilt and the plan recalculated. For bulk removal of orders due before a date, use '
     'exclude_orders_before so related rows are handled in one atomic batch.',
     _schema({'entity': {'type': 'string', 'enum': sorted(ENTITY_VIEW)},
              'action': {'type': 'string', 'enum': ['update', 'insert', 'exclude']},
              'key': {'type': 'array', 'maxItems': 4, 'items': {'type': ['string', 'integer']}},
              'values': {'type': 'object'}, 'reason': {'type': 'string', 'minLength': 3, 'maxLength': 500}},
             ('entity', 'action', 'reason'))),
    ('exclude_orders_before', 'planner', 'data',
     'Exclude in one atomic change every active order whose own due date, or earliest phase due date when the order '
     'has no due date, is before the given date, plus '
     'all canonical rows that reference those orders or their operations (demands, dependencies, material needs, '
     'progress, subcontract ends, allocation ranks and nesting memberships; shared nests with other members stay). '
     'The complete batch is validated with one canonical rebuild and the plan is recalculated once. This is reversible '
     'as one chat action. Use only when the user explicitly asks to exclude all orders before a cutoff date.',
     _schema({'before': {'type': 'string', 'pattern': '^\\d{4}-\\d{2}-\\d{2}$'},
              'reason': {'type': 'string', 'minLength': 3, 'maxLength': 500}}, ('before', 'reason'))),
    ('revoke_corrections', 'planner', 'data',
     'Revoke active data corrections by id (the source values come back) with a reason.',
     _schema({'ids': {'type': 'array', 'minItems': 1, 'maxItems': 20, 'items': {'type': 'integer', 'minimum': 1}},
              'reason': {'type': 'string', 'minLength': 3, 'maxLength': 500}}, ('ids', 'reason'))),
    ('save_revision', 'planner', None,
     'Save the plan the user is looking at (with its scenario) as a new verified revision in the archive.', EMPTY),
    ('regeneration_status', 'reparto', 'regeneration',
     'State of the last rebuild of the source from the ERP: running, done or failed, start date, last log lines.',
     EMPTY),
    ('regenerate_from_source', 'admin', 'regeneration',
     'Rebuild the source from the ERP with another planning start date (YYYY-MM-DD): new extraction, rules, '
     'canonical scenario and plan. Takes minutes and runs in the background; the user sees its progress.',
     _schema({'start': {'type': 'string', 'pattern': '^\\d{4}-\\d{2}-\\d{2}$'}}, ('start',))),
)
_BY_NAME = {item[0]: item for item in CATALOG}
WRITES = {'recalculate', 'simulate', 'reset_simulation', 'create_rule', 'decide_rule', 'correct_data',
          'exclude_orders_before', 'revoke_corrections', 'save_revision', 'regenerate_from_source'}


def _now():
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


def clean_ui(ui):
    """Interface state sent by the browser: current view, simulated edits, settings, archived flag."""
    if ui is None:
        ui = {}
    if not isinstance(ui, dict) or set(ui) - {'view', 'edits', 'settings', 'archived'}:
        raise ValidationError(['ui: view, edits, settings, archived only'])
    edits = ui.get('edits') or {'fixed_tasks': [], 'closures': []}
    if (not isinstance(edits, dict) or set(edits) != {'fixed_tasks', 'closures'}
            or not all(isinstance(edits[k], list) and len(edits[k]) <= 50 for k in edits)):
        raise ValidationError(['ui.edits: fixed_tasks and closures arrays required'])
    settings = ui.get('settings') or {}
    if not isinstance(settings, dict) or set(settings) - ALLOWED_SETTINGS:
        raise ValidationError(['ui.settings: planning settings only'])
    view = ui.get('view')
    if view is not None and view not in PLAN_VIEWS + DATA_VIEWS + ('impostazioni', 'staged'):
        raise ValidationError(['ui.view: unknown screen'])
    return {'view': view, 'edits': json.loads(json.dumps(edits)), 'settings': dict(settings),
            'archived': ui.get('archived') is True}


class UndoRegistry:
    """Inverse operations of persistent actions, kept by the server: the browser only holds a token."""

    def __init__(self, limit=200):
        self.items, self.limit, self.lock = {}, limit, threading.Lock()

    def add(self, actor, label, spec):
        token = secrets.token_urlsafe(16)
        with self.lock:
            self.items[token] = (actor, label, spec)
            while len(self.items) > self.limit:
                self.items.pop(next(iter(self.items)))
        return token

    def take(self, token, actor):
        """Claim a token (a second click cannot run the same undo twice); ``give_back`` if it did not happen."""
        with self.lock:
            found = self.items.get(token) if isinstance(token, str) else None
            if found is None or found[0] != actor:
                raise ValidationError(['azione da annullare non trovata o già annullata'])
            return self.items.pop(token)

    def give_back(self, token, entry):
        with self.lock:
            self.items[token] = entry


class AssistantActions:
    """One conversation turn (or one undo) of one user: executes actions and tracks the plan they lead to."""

    def __init__(self, app, user, ui, insights=None, meta=None, order_texts=None):
        self.app, self.state, self.user = app, app.state, user
        self.ui = clean_ui(ui)
        self.insights = insights
        self.meta = meta or {'canonical': self.state.canonical_context(), 'company_memory': None}
        # Texts of the plan being read (an archived one keeps its own); a new plan uses today's source.
        self.texts = order_texts if order_texts is not None else self.state.order_texts()
        self.client, self.undo, self.writes = [], [], 0
        self._tools = None

    # ------------------------------------------------------------------ catalog
    def _available(self, item):
        name, role, needs = item[0], item[1], item[2]
        if getattr(self.state,'workbook_input',None) is not None and name in {
                'create_rule','decide_rule','correct_data','revoke_corrections','exclude_orders_before','regenerate_from_source'}:
            return False
        if ROLES[self.user[1]] < ROLES[role]:
            return False
        if needs == 'data' and getattr(self.app, 'data', None) is None:
            return False
        if needs == 'regeneration' and getattr(self.app, 'regeneration', None) is None:
            return False
        return not (self.ui['archived'] and name in WRITES)

    def definitions(self):
        return [{'name': name, 'description': description, 'input_schema': schema}
                for name, role, needs, description, schema in CATALOG if self._available(_BY_NAME[name])]

    @property
    def names(self):
        return {d['name'] for d in self.definitions()}

    @property
    def digest(self):
        return self.insights.plan.scenario_digest if self.insights is not None else None

    def tools(self):
        """Read tools bound to the plan the conversation is on now (it changes after a recalculation)."""
        if self._tools is None or self._tools[0] != self.digest:
            snapshot = {'plan': json.loads(dumps(self.insights.plan)), 'meta': self.meta, 'order_texts': self.texts}
            self._tools = (self.digest, PlanningTools(snapshot, self.insights))
        return self._tools[1]

    # ------------------------------------------------------------------ dispatch
    def call(self, name, arguments):
        item = _BY_NAME.get(name)
        if item is None or not self._available(item):
            if item is not None and self.ui['archived'] and name in WRITES:
                raise ValidationError(['revisione archiviata in sola lettura: riapri la fonte per agire sul piano'])
            raise ValidationError([f'azione non disponibile per il ruolo {self.user[1]}'])
        if not isinstance(arguments, dict):
            raise ValidationError(['arguments must be an object'])
        if name in WRITES:
            if self.writes >= MAX_WRITES:
                raise ValidationError([f'al massimo {MAX_WRITES} azioni di modifica per risposta'])
            self.writes += 1
        return getattr(self, '_' + name)(arguments)

    def _audit(self, action, **metadata):
        self.app.audit(self.user, 'assistant_' + action, 200, **metadata)

    def _remember_undo(self, label, spec):
        token = self.app.undo.add(self.user[0], label, spec)
        self.undo.append({'token': token, 'label': label})
        return token

    # ------------------------------------------------------------------ interface
    def _show_view(self, args):
        view = args.get('view')
        if view not in PLAN_VIEWS + DATA_VIEWS or (view in DATA_VIEWS and getattr(self.app, 'data', None) is None):
            raise ValidationError(['schermata non disponibile'])
        self.client.append({'type': 'view', 'view': view})
        self.ui['view'] = view
        return {'shown': view}

    def _filter_view(self, args):
        allowed = {'view', 'text', 'resource_kind', 'delay_cause', 'customer_code', 'entity', 'data_status'}
        if set(args) - allowed or not args:
            raise ValidationError(['at least one filter required'])
        action = {'type': 'filter'}
        if 'entity' in args:
            if getattr(self.app, 'data', None) is None or args['entity'] not in ENTITY_VIEW:
                raise ValidationError(['dati in ingresso non disponibili'])
            action.update(entity=args['entity'], view=ENTITY_VIEW[args['entity']])
        elif 'view' in args:
            if args['view'] not in PLAN_VIEWS + DATA_VIEWS:
                raise ValidationError(['schermata non disponibile'])
            action['view'] = args['view']
        if 'text' in args:
            if not isinstance(args['text'], str) or len(args['text']) > 120:
                raise ValidationError(['text: up to 120 characters'])
            action['text'] = args['text'].strip()
        for key, choices in (('resource_kind', RESOURCE_KINDS), ('delay_cause', CAUSES + ('',)),
                             ('data_status', DATA_STATUS)):
            if key in args:
                if args[key] not in choices:
                    raise ValidationError([f'{key}: one of {", ".join(c or "(vuoto)" for c in choices)}'])
                action[key] = args[key]
        if 'customer_code' in args:
            code = args['customer_code']
            known = {t.get('customer_code') for t in self.texts.values()}
            if code and code not in known:
                raise ValidationError(['customer_code sconosciuto: usa get_customers o search_plan'])
            action['customer_code'] = code
            action.setdefault('view', 'deliveries')
        self.client.append(action)
        if action.get('view'):
            self.ui['view'] = action['view']
        return {'filtered': {k: v for k, v in action.items() if k != 'type'}}

    def _focus(self, args):
        if set(args) - {'task_id', 'order_id', 'day', 'span_days'} or not args:
            raise ValidationError(['task_id, order_id, day or span_days required'])
        plan = self.insights.plan
        action = {'type': 'focus'}
        if 'task_id' in args:
            if args['task_id'] not in {a.task_id for a in plan.assignments}:
                raise ValidationError(['task_id non pianificato in questo piano'])
            action['task_id'] = args['task_id']
        if 'order_id' in args:
            if args['order_id'] not in {o.order_id for o in plan.orders}:
                raise ValidationError(['order_id non presente nel piano: usa search_plan'])
            action['order_id'] = args['order_id']
        if 'day' in args:
            if not isinstance(args['day'], str) or not _DAY.match(args['day']):
                raise ValidationError(['day: YYYY-MM-DD'])
            date.fromisoformat(args['day'])
            action['day'] = args['day']
        if 'span_days' in args:
            if type(args['span_days']) is not int or not 1 <= args['span_days'] <= 62:
                raise ValidationError(['span_days: 1..62'])
            action['span_days'] = args['span_days']
        self.client.append(action)
        return {'focused': {k: v for k, v in action.items() if k != 'type'}}

    # ------------------------------------------------------------------ plan
    def replan(self):
        """Plan of the current source with the interface's edits and settings, verified (or reused) here."""
        from planning_factory.adapters.dashboard_simulation import apply_whatif_edits
        state, edits = self.state, self.ui['edits']
        source, evidence = state.company_memory.apply(state.source) if state.company_memory else (state.source, None)
        scenario = apply_whatif_edits(source, edits) if edits['fixed_tasks'] or edits['closures'] else source
        scenario = apply_settings(scenario, self.ui['settings'], state.manifest)
        result = state._audited_plan(scenario)
        state._remember_company_context(result.scenario_digest, evidence)
        before = self.insights
        self.insights = state.remember_plan(scenario, result)
        self.meta = {'canonical': state.canonical_context(), 'company_memory': evidence}
        self.texts = state.order_texts()
        self.client = [a for a in self.client if a['type'] != 'plan']
        self.client.append({'type': 'plan', 'settings': dict(self.ui['settings']), 'edits': json.loads(json.dumps(edits))})
        return self._figures(before.plan if before is not None else None, result)

    @staticmethod
    def _figures(before, after):
        def numbers(p):
            return {'late_orders': sum(1 for o in p.orders if (o.tardiness_seconds or 0) > 0),
                    'incomplete_orders': sum(1 for o in p.orders if not o.complete),
                    'unscheduled_tasks': len(p.unscheduled),
                    'tardiness_hours': round(sum(o.tardiness_seconds or 0 for o in p.orders) / 3600, 1)}
        result = {'scenario_digest': after.scenario_digest, 'after': numbers(after), 'verified': True}
        if before is not None:
            moved = {oid for a in before.assignments for oid in a.operation_ids}
            old = {oid: a.segments for a in before.assignments for oid in a.operation_ids}
            new = {oid: a.segments for a in after.assignments for oid in a.operation_ids}
            result.update(before=numbers(before), changed_operations=sum(
                1 for oid in moved | set(new) if old.get(oid) != new.get(oid)))
        return result

    def _recalculate(self, args):
        if set(args) != {'settings'} or not isinstance(args['settings'], dict) or not args['settings']:
            raise ValidationError(['settings with at least one key required'])
        merged = {**self.ui['settings'], **args['settings']}
        _settings_override(self.state.source.settings, merged)
        previous = self.ui['settings']
        self.ui['settings'] = merged
        try:
            figures = self.replan()
        except Exception:
            self.ui['settings'] = previous
            raise
        self._audit('recalculate', scenario_digest=figures['scenario_digest'], settings=args['settings'])
        return {**figures, 'settings': merged}

    def _simulate(self, args):
        if set(args) - {'closures', 'fixed_tasks', 'unfix_task_ids', 'replace'} or not args:
            raise ValidationError(['closures, fixed_tasks, unfix_task_ids or replace required'])
        current = self.ui['edits'] if args.get('replace') is not True else {'fixed_tasks': [], 'closures': []}
        drop = set(args.get('unfix_task_ids') or ())
        added = args.get('fixed_tasks') or []
        replaced = {f['task_id'] for f in added if isinstance(f, dict)} | drop
        edits = {'fixed_tasks': [f for f in current['fixed_tasks'] if f.get('task_id') not in replaced] + list(added),
                 'closures': list(current['closures']) + list(args.get('closures') or [])}
        previous = self.ui['edits']
        self.ui['edits'] = edits
        try:
            figures = self.replan()
        except Exception:
            self.ui['edits'] = previous
            raise
        self._audit('simulate', scenario_digest=figures['scenario_digest'],
                    closures=len(edits['closures']), fixed_tasks=len(edits['fixed_tasks']))
        return {**figures, 'simulation': edits, 'saved': False}

    def _reset_simulation(self, args):
        if args:
            raise ValidationError(['no arguments'])
        self.ui['edits'] = {'fixed_tasks': [], 'closures': []}
        figures = self.replan()
        self._audit('reset_simulation', scenario_digest=figures['scenario_digest'])
        return figures

    # ------------------------------------------------------------------ company memory
    def _list_company_rules(self, args):
        rules = self.app.rules.list()
        catalog = self.app.rules.scope_catalog()
        return {'total': len(rules), 'rules': rules[-100:], 'truncated': len(rules) > 100,
                'scope_values': {'customer_id': len(catalog['customer_id']), 'material': catalog['material'][:50],
                                 'thickness_available': catalog['thickness_available']},
                'priority': {'lower_runs_first': True, 'levels': catalog['priority_levels'],
                             'weights': {k: getattr(self.state.source.settings, k)
                                         for k in ('priority_weight', 'customer_weight', 'due_weight')}}}

    def _rule_fields(self, rule):
        return {k: v for k, v in rule.items() if k in RULE_FIELDS and v is not None}

    def _decide(self, rule_id, decision):
        app = self.app
        current = next((r for r in app.rules.list() if r['id'] == rule_id), None)
        if current is None:
            raise ValidationError(['regola non trovata: usa list_company_rules'])

        def validate():
            adjusted, _ = app.rules.apply(self.state.source, preview_rule=rule_id)
            self.state._audited_plan(adjusted)
        decided = app.rules.decide(rule_id, current['version'], decision, self.user[0],
                                   validate=validate if decision == 'confirm' else None)
        with self.state._lock:
            self.state._default = None
            self.state._stage_cache = None
        self._audit('rule_' + decision, rule_id=rule_id, version=decided['version'])
        self.client.append({'type': 'rules'})
        return current, decided

    def _create_rule(self, args):
        if set(args) - {'rule', 'confirm'} or not isinstance(args.get('rule'), dict):
            raise ValidationError(['rule required'])
        draft = self.app.rules.create_draft(args['rule'], self.user[0])
        self._audit('rule_draft', rule_id=draft['id'])
        result = {'rule': draft}
        if args.get('confirm') is True:
            _, decided = self._decide(draft['id'], 'confirm')
            result.update(rule=decided, plan=self.replan())
            self._remember_undo(f"Revoca la regola {draft['id'][:13]}", {'kind': 'rule', 'rule_id': draft['id'],
                                                                        'decision': 'revoke'})
        else:
            self.client.append({'type': 'rules'})
            self._remember_undo(f"Scarta la bozza {draft['id'][:13]}", {'kind': 'rule', 'rule_id': draft['id'],
                                                                       'decision': 'revoke'})
        return result

    def _decide_rule(self, args):
        if set(args) != {'rule_id', 'decision'} or args['decision'] not in ('confirm', 'revoke'):
            raise ValidationError(['rule_id and decision confirm|revoke required'])
        current, decided = self._decide(args['rule_id'], args['decision'])
        fields = self._rule_fields(current['rule'])
        if args['decision'] == 'confirm':
            self._remember_undo(f"Revoca la regola {args['rule_id'][:13]}",
                                {'kind': 'rule', 'rule_id': args['rule_id'], 'decision': 'revoke'})
        elif current['status'] == 'confirmed':
            self._remember_undo('Ripristina la regola revocata', {'kind': 'rule_restore', 'rule': fields, 'confirm': True})
        else:
            self._remember_undo('Ripristina la bozza scartata', {'kind': 'rule_restore', 'rule': fields, 'confirm': False})
        plan = self.replan() if current['status'] == 'confirmed' or args['decision'] == 'confirm' else None
        return {'rule': decided, **({'plan': plan} if plan else {})}

    # ------------------------------------------------------------------ input data
    def _get_data_rows(self, args):
        if set(args) - {'entity', 'text', 'filters', 'sort', 'descending', 'offset', 'limit'}:
            raise ValidationError(['unknown arguments'])
        filters = args.get('filters') or {}
        if not isinstance(filters, dict) or len(filters) > 8 or not all(isinstance(v, str) for v in filters.values()):
            raise ValidationError(['filters: field -> text'])
        result = self.app.data.rows(args['entity'], q=args.get('text') or '', filters=filters,
                                    sort=args.get('sort') or None, descending=args.get('descending') is True,
                                    offset=args.get('offset', 0), limit=args.get('limit', 20))
        return {'entity': result['entity'], 'total': result['total'], 'offset': result['offset'],
                'rows': [{'key': r['key'], 'values': r['values'], 'status': r['status'], 'corrections': r['corrections']}
                         for r in result['rows']]}

    def _correct_data(self, args):
        if set(args) - {'entity', 'action', 'key', 'values', 'reason'}:
            raise ValidationError(['unknown arguments'])
        values = args.get('values') or {}
        if not isinstance(values, dict):
            raise ValidationError(['values must be an object'])
        result = self.app.data.correct(args['entity'], args['action'], args.get('key'), values,
                                       args['reason'], self.user[0])
        correction = result['correction']
        self._audit('data_correction', correction=correction['id'], entity=args['entity'],
                    correction_action=args['action'])
        self._remember_undo(f"Revoca la correzione {correction['id']}",
                            {'kind': 'corrections_revoke', 'ids': [correction['id']]})
        self.client.append({'type': 'data', 'entity': args['entity']})
        return {'correction': correction, 'plan': self.replan()}

    def _exclude_orders_before(self, args):
        if set(args) != {'before', 'reason'}:
            raise ValidationError(['before and reason required'])
        result = self.app.data.exclude_orders_before(args['before'], args['reason'], self.user[0])
        ids = list(result['corrections'])
        self._audit('data_correction_batch', corrections=len(ids), excluded_orders=result['affected']['orders'],
                    excluded_operations=result['affected']['operations'], before=args['before'])
        self._remember_undo(f"Ripristina {result['affected']['orders']} commesse escluse",
                            {'kind': 'corrections_revoke', 'ids': ids})
        self.client.append({'type': 'data'})
        return {'affected': result['affected'], 'order_ids': result['order_ids'],
                'corrections': len(ids), 'plan': self.replan()}

    def _revoke_corrections(self, args):
        if set(args) != {'ids', 'reason'} or not isinstance(args['ids'], list):
            raise ValidationError(['ids and reason required'])
        ids = {int(i) for i in args['ids']}
        history = {h['id']: h for h in self.app.data.history() if h['id'] in ids and h['active']}
        result = self.app.data.revoke(sorted(ids), args['reason'], self.user[0])
        self._audit('data_correction_revoke', corrections=sorted(ids))
        self._remember_undo('Ripristina ' + ('la correzione' if len(ids) == 1 else f'{len(ids)} correzioni'),
                            {'kind': 'corrections_reapply', 'items': [
                                {k: h[k] for k in ('entity', 'action', 'key', 'values', 'reason')}
                                for _, h in sorted(history.items())]})
        self.client.append({'type': 'data'})
        return {'revoked': result['revoked'], 'plan': self.replan()}

    # ------------------------------------------------------------------ archive and source
    def _save_revision(self, args):
        if args:
            raise ValidationError(['no arguments'])
        scenario, result = self.insights.scenario, self.insights.plan
        evidence = {'canonical': self.meta.get('canonical'), 'company_memory': self.meta.get('company_memory'),
                    'workbook': self.meta.get('workbook'),
                    'source_manifest': self.state.manifest if self.meta.get('canonical') else None,
                    'source': 'assistant', 'recorded_at': datetime.now(UTC).isoformat(), 'source_certified': False,
                    'lineage': {'edits': self.ui['edits'], 'settings': self.ui['settings']}}
        revision = self.app.save_snapshot(scenario, result, expected_revision=self.app.store.current_revision(scenario.id),
                                                evidence=evidence, actor=self.user[0])
        self._audit('save_snapshot', scenario_id=scenario.id, revision=revision)
        self.client.append({'type': 'revision', 'revision': revision})
        return {'scenario_id': scenario.id, 'revision': revision, 'scenario_digest': result.scenario_digest,
                'note': "revisione archiviata: l'archivio non si cancella"}

    def _regeneration_status(self, args):
        return self.app.regeneration.status()

    def _regenerate_from_source(self, args):
        if set(args) != {'start'} or not isinstance(args['start'], str) or not _DAY.match(args['start']):
            raise ValidationError(['start: YYYY-MM-DD'])
        job = self.app.regeneration.start(args['start'], self.user[0])
        self._audit('regenerate', start=args['start'])
        self.client.append({'type': 'job', 'job': 'regeneration'})
        return {**job, 'note': 'rigenerazione avviata in background: il piano si aggiorna da solo alla fine'}

    # ------------------------------------------------------------------ undo
    def run_undo(self, token):
        if self.ui['archived']:
            raise ValidationError(['revisione archiviata in sola lettura: riapri la fonte e riprova'])
        entry = self.app.undo.take(token, self.user[0])
        self._undone = False
        try:
            return self._undo(*entry[1:])
        except Exception:
            # Nothing was reverted: the token stays valid and the user can retry. Once the inverse is stored
            # the token is spent, even if the following recalculation fails.
            if not self._undone:
                self.app.undo.give_back(token, entry)
            raise

    def _undo(self, label, spec):
        kind = spec['kind']
        if kind == 'rule':
            current = next((r for r in self.app.rules.list() if r['id'] == spec['rule_id']), None)
            if current is None or current['status'] == 'revoked':
                raise ValidationError(['la regola è già revocata'])
            was_confirmed = current['status'] == 'confirmed'
            self._decide(spec['rule_id'], spec['decision'])
            self._undone = True
            if was_confirmed:
                self.replan()
        elif kind == 'rule_restore':
            draft = self.app.rules.create_draft(spec['rule'], self.user[0])
            self._undone = True
            self._audit('rule_draft', rule_id=draft['id'])
            if spec['confirm']:
                self._decide(draft['id'], 'confirm')
                self.replan()
        elif kind == 'corrections_revoke':
            self.app.data.revoke(spec['ids'], 'annullata dalla chat', self.user[0])
            self._undone = True
            self._audit('data_correction_revoke', corrections=spec['ids'])
            self.client.append({'type': 'data'})
            self.replan()
        elif kind == 'corrections_reapply':
            for item in spec['items']:
                result = self.app.data.correct(item['entity'], item['action'], item['key'], item['values'],
                                               item['reason'], self.user[0])
                self._undone = True
                self._audit('data_correction', correction=result['correction']['id'], entity=item['entity'],
                            correction_action=item['action'])
            self.client.append({'type': 'data'})
            self.replan()
        else:
            raise ValidationError(['azione da annullare non supportata'])
        self._audit('undo', undone=label)
        return {'undone': label, 'actions': self.client, 'scenario_digest': self.digest}
