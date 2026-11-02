"""Three-way source refresh. The workbook owns planning choices; conflicts stop publication."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import json

from planning_factory.adapters.workbook_io import catalogue
from planning_factory.adapters.planning_workbook import _normalize_tables, seconds
from planning_factory.domain.validation import ValidationError

LOCAL_TABLES=frozenset(('profile','rule','guidance','correction','domain_policy','perimeter',
    'quality_policy','nest_status_policy','setup_phase','stage_requirement','stage_candidate',
    'attendance_policy','attendance_window','machine_state','objective','qualification'))
SOURCE_TABLES=frozenset(('source','domain_source'))
ABSENT=object()
MARKER='factory-sync-baseline'


def json_bytes(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),
        default=lambda v:str(v) if isinstance(v,Decimal) else (_ for _ in ()).throw(TypeError(type(v).__name__))).encode()


def digest(value):
    return hashlib.sha256(json_bytes(value)).hexdigest()


def _rows(rows, keys):
    return {tuple(str(r[k]) for k in keys):r for r in rows}


def equal(a,b,field=''):
    if a is ABSENT or b is ABSENT:return a is b
    if isinstance(a,dict) and isinstance(b,dict):
        return a.keys()==b.keys() and all(equal(a[k],b[k],k) for k in a)
    if field.endswith(('_seconds','_minutes')) and a is not None and b is not None:
        try:return seconds(a,field)==seconds(b,field)
        except ValidationError:pass
    return a==b


def merge_tables(base,local,remote,*,resolution=None):
    """Use one persisted last-source baseline, never the database as a planning input.

    Missing, null and zero are different. Both sides changing a field or a
    delete/update collision is a conflict. A supplied global resolution is an
    explicit administrator decision on the already displayed conflict report.
    """
    if resolution not in (None,'excel','factory'):
        raise ValidationError(['risoluzione excel oppure factory richiesta'])
    base,local,remote=(_normalize_tables(deepcopy(t),{}) for t in (base,local,remote))
    result={};conflicts=[];kept=updated=0
    def choose(code,key,field,b,l,r):
        nonlocal kept,updated
        if equal(l,r,field):return l
        if equal(l,b,field):
            updated+=1;return r
        if equal(r,b,field):
            kept+=1;return l
        row=dict(table=code,key=list(key),field=field,
            base=None if b is ABSENT else b,excel=None if l is ABSENT else l,
            factory=None if r is ABSENT else r,
            base_absent=b is ABSENT,excel_absent=l is ABSENT,factory_absent=r is ABSENT)
        conflicts.append(row)
        return r if resolution=='factory' else l
    for table in catalogue()['tables']:
        code=table['code'];keys=[k.strip() for k in table['key'].split(',')]
        if code in LOCAL_TABLES:
            result[code]=deepcopy(local.get(code,[]));continue
        if code in SOURCE_TABLES:
            result[code]=deepcopy(remote.get(code,[]));continue
        b,l,r=(_rows(t.get(code,[]),keys) for t in (base,local,remote))
        rows=[]
        for key in sorted(b.keys()|l.keys()|r.keys()):
            bv,lv,rv=(t.get(key,ABSENT) for t in (b,l,r))
            if any(v is ABSENT for v in (bv,lv,rv)):
                value=choose(code,key,'*',bv,lv,rv)
            else:
                value={}
                for field in sorted(bv.keys()|lv.keys()|rv.keys()):
                    v=choose(code,key,field,*(x.get(field,ABSENT) for x in (bv,lv,rv)))
                    if v is not ABSENT:value[field]=deepcopy(v)
            if value is not ABSENT:rows.append(deepcopy(value))
        result[code]=rows
    # Horizon/date were explicitly requested for this extraction. The local
    # profile and dataset remain workbook-owned and are not connector settings.
    meta=deepcopy(remote['scenario'][0])
    meta.update({k:local['scenario'][0][k] for k in ('dataset_id','profile_id','timezone','input_basis')})
    result['scenario']=[meta]
    conflicts=[c for c in conflicts if c['table']!='scenario']
    # The legacy correction audit is workbook-owned, not a fresh ERP record.
    provenance={p['id']:p for p in result.get('provenance',[])}
    for p in local.get('provenance',[]):
        if p.get('transformation') in ('correzione_gia_materializzata','memoria_istanza_migrata'):
            provenance[p['id']]=deepcopy(p)
    result['provenance']=list(provenance.values())
    return result,dict(conflicts=conflicts,conflict_count=len(conflicts),
        local_changes_preserved=kept,source_changes=updated,resolution=resolution)
