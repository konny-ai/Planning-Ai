"""Workbook-backed input screens and company memory, without database overlays."""
from copy import deepcopy
import threading

from planning_factory.adapters.data_workspace import DataWorkspace
from planning_factory.adapters.rule_memory import RuleMemory
from planning_factory.adapters.json_io import scenario_digest
from planning_factory.canonical.entities import ENTITIES
from planning_factory.domain.validation import ValidationError


def edit_in_excel(*args, **kwargs):
    raise ValidationError(['Excel è la sorgente unica: modifica il file e premi Rileggi Excel nelle Fonti.'])


class WorkbookWorkspace(DataWorkspace):
    editable = False

    def __init__(self, state):
        self.state = state
        self.lock = threading.RLock()
        self._rows = {}
        self.startup_error = None

    def bound_domains(self):
        return {r['domain'] for r in self.state.workbook_input.tables['domain_policy'] if r['coverage']=='gestito'}

    def catalog(self):
        result = super().catalog()
        result.update(editable=False, source_kind='planning_workbook', input_file=str(self.state.path),
                      corrections_active=sum(1 for r in self.history() if r['active']))
        return result

    def history(self, entity=None, key=None):
        rows = []
        for p in self.state.workbook_input.tables.get('provenance', ()):
            if p.get('transformation') != 'correzione_gia_materializzata':
                continue
            item = deepcopy(p['original_value'])
            if entity and item['entity'] != entity:
                continue
            if key is not None and list(item['key']) != list(key):
                continue
            item.update(active=True, previous=None, revoked_at=None, revoked_by=None, revoke_reason=None)
            rows.append(item)
        return rows

    correct = correct_many = revoke = exclude_orders_before = edit_in_excel

    def reload(self):
        with self.lock:
            self.state.reload_workbook()
            self._rows.clear()
            self._filled = (None, {})
            return self.sources()

    def sources(self):
        state = self.state
        wb = state.workbook_input
        manifest = state.manifest
        groups = {}
        for issue in state.build_issues:
            group = groups.setdefault((issue.severity, issue.domain, issue.code), dict(
                severity=issue.severity, domain=issue.domain, code=issue.code, count=0, examples=[]))
            group['count'] += 1
            if len(group['examples']) < 5:
                group['examples'].append(issue.to_dict())
        domains = []
        per_domain = {}
        for p in wb.tables['domain_policy']:
            domain = p['domain']
            if p['coverage'] != 'gestito':
                continue
            info = manifest.get('domains', {}).get(domain, {})
            counts = {e:len(rows) for e, rows in wb.records.items() if rows and ENTITIES[e].domain==domain}
            per_domain[domain] = counts
            domains.append(dict(domain=domain,sources=['Excel'],records=info.get('records'),
                digest=info.get('digest'),max_age_minutes=p.get('max_age_minutes'),
                policies={k:p[k] for k in ('residual_policy','allocation_order','material_policy') if k in p},precedence={}))
        meta = wb.tables['scenario'][0]
        import hashlib
        changed = hashlib.sha256(state.path.read_bytes()).hexdigest() != wb.fingerprint
        return dict(installation=meta['dataset_id'],scenario_id=meta['id'],source_kind='planning_workbook',
            editable=False,input_file=str(state.path),binding_file=str(state.path),binding_digest=wb.fingerprint,
            horizon=manifest.get('horizon'),built_at=meta['build_at'],read_at=state.read_at.isoformat(),
            scenario_digest=scenario_digest(state.source),publishable=manifest.get('publishable'),
            validation_accepted=wb.validation_accepted,material_constraint_ignored=wb.materials_ignored,
            issue_counts=manifest.get('issues'),sources=[dict(name='Excel',connector='planning-workbook',
                files=[self._file(state.path.parent,state.path.name,'Excel completo')],domains=per_domain,
                extracted_at=meta['snapshot_at'],errors=[])],domains=domains,issues=list(groups.values()),
            corrections=dict(active=len(self.history())),perimeter=manifest.get('perimeter'),
            data_version=state.data_version,file_changed=changed)


class WorkbookMemory:
    """Read the embedded memory; it has already been applied by the workbook build."""
    readonly = True

    def __init__(self, state):
        self.state = state

    @property
    def source(self):
        return self.state.source

    @property
    def scope_context(self):
        return (self.state.manifest or {}).get('rule_scope')

    scope_catalog = RuleMemory.scope_catalog

    def list(self):
        wb = self.state.workbook_input
        entries = {}
        for row in wb.tables.get('rule', ()):
            entries[row['id']] = dict(id=row['id'],version=1,status='confirmed',rule=deepcopy(row),
                actor=row.get('confirmed_by'),at=row.get('confirmed_at'))
        for row in wb.tables.get('guidance', ()):
            rule = {k:v for k,v in row.items() if k!='status'}
            if rule.get('scope_entity'):
                rule[rule.pop('scope_entity')] = rule.pop('scope_key')
            entries[row['id']] = dict(id=row['id'],version=1,
                status={'bozza':'draft','confermata':'confirmed','revocata':'revoked'}[row['status']],
                rule=rule,actor=row.get('confirmed_by'),at=row.get('confirmed_at'))
        # Revoked non-operational rules remain visible as migration history.
        for p in wb.tables.get('provenance', ()):
            if p.get('transformation')=='memoria_istanza_migrata':
                entry=p['original_value']
                if entry['id'] not in entries:
                    entries[entry['id']]=deepcopy(entry)
                else:
                    entries[entry['id']]['version']=entry['version']
        return sorted(entries.values(),key=lambda r:r['id'])

    def guidance(self, now=None):
        # Same time basis as the workbook calculation, not the wall clock.
        return RuleMemory.guidance(self, now or self.state.workbook_input.tables['scenario'][0]['build_at'])

    _in_window = staticmethod(RuleMemory._in_window)

    def cache_key(self):
        return self.state.workbook_input.fingerprint

    def apply(self, scenario, *, preview_rule=None):
        if preview_rule:
            edit_in_excel()
        wb=self.state.workbook_input
        return scenario, dict(source='planning_workbook',rules_in_workbook=True,
            material_constraint=dict(ignored=wb.materials_ignored,
                rules=[r['id'] for r in self.guidance() if r['action']=='ignore_material']),
            notes=[r for r in self.guidance() if r['action']=='note'],
            versions=[{k:r[k] for k in ('id','version','status')} for r in self.list()],
            source_scenario_digest=scenario_digest(scenario),preview_only=False)

    create_draft = decide = edit_in_excel
