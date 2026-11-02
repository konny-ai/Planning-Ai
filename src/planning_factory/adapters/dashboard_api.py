"""Same-origin, loopback-only planning dashboard (no ERP, no SQL).

This is a development/demo workbench for anonymized Scenario JSON files.
It is deliberately NOT the authenticated multi-user production service:
never bind publicly or forward its port without private access controls.
All calculations use the existing verified engine; nothing is persisted.
"""
from collections import OrderedDict
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import json
import threading
from urllib.parse import urlsplit

from planning_factory.adapters.json_io import _no_duplicates, dumps, loads, scenario_digest
from planning_factory.adapters.reports import csv_report
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan

ROOT = Path(__file__).resolve().parents[3]
WEB = Path(__file__).resolve().parents[1] / "web"
DEFAULT_FIXTURE = ROOT / "examples/workshop.json"
MAX_SETTINGS_BODY = 64 * 1024  # bounded parameter + what-if editing payload
# Reject oversized files before JSON parsing. The CLIENTE scenario of 2026-05-04 (1.323 phases,
# 78k operator x machine alternatives) is 21 MB indented: 16 MiB only fitted the demo benches.
MAX_SOURCE_BYTES = 64 * 1024 * 1024
MAX_PROGRESS_ROWS = 1000  # a browser is not an MES event store
MAX_INDEXED_PLANS = 4  # verified plans kept for digest-addressed analyses (CLIENTE scenarios are large)
DESCRIPTIONS_FILE = "descrizioni.json"  # display-only texts next to the fixture (integrations/cliente-export/build_descrizioni.py)
DESCRIPTIONS_SCHEMA = "planning-factory/descrizioni-v1"
MAX_DESCRIPTIONS_BYTES = 16 * 1024 * 1024
ALLOWED_SETTINGS = {
    "mode", "auto_backward_days", "priority_weight", "due_weight",
    "customer_weight", "auto_rule", "auto_slack_seconds",
    "alternative_switch_min_gain_seconds", "delivery_buffer_seconds",
}
# Not a field of the engine Settings: a profile option (#78 P3) applied to the due dates of the Scenario.
BUFFER_SETTING = "delivery_buffer_seconds"
MAX_BUFFER_SECONDS = 90 * 86400
class PlannerBusyError(RuntimeError):
    """Only one bounded demo computation can run at a time."""


ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/data.js": ("data.js", "application/javascript; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
}


def _settings_override(existing, edits):
    if not isinstance(edits, dict) or set(edits) - ALLOWED_SETTINGS:
        raise ValidationError(["unknown or malformed planning settings"])
    if "mode" in edits and edits["mode"] not in ("forward", "backward", "auto"):
        raise ValidationError(["mode must be forward, backward or auto"])
    if "auto_rule" in edits and edits["auto_rule"] not in ("due_distance", "slack"):
        raise ValidationError(["auto_rule must be due_distance or slack"])
    for name, value in edits.items():
        if name not in ("mode", "auto_rule"):
            limit = MAX_BUFFER_SECONDS if name == BUFFER_SETTING else 1_000_000
            if type(value) is not int or not 0 <= value <= limit:
                raise ValidationError([f"{name}: nonnegative bounded integer required"])
    if "auto_backward_days" in edits and edits["auto_backward_days"] > 3660:
        raise ValidationError(["auto_backward_days exceeds planning horizon limit"])
    return replace(existing, **{k: v for k, v in edits.items() if k != BUFFER_SETTING})


def apply_settings(scenario, edits, manifest):
    """Scenario with the planning settings of the user: engine settings plus the delivery buffer (P3)."""
    planned = replace(scenario, settings=_settings_override(scenario.settings, edits))
    if BUFFER_SETTING in edits:
        from planning_factory.canonical.delivery import with_buffer
        planned = with_buffer(planned, (manifest or {}).get("delivery"), edits[BUFFER_SETTING])
    return planned


def progress_evidence(batches, scenario, *, limit=MAX_PROGRESS_ROWS):
    """Read-only raw progress evidence; never infer live resource occupancy.

    Canonical identity mappings may rename operation IDs. In that case
    no synthetic link to the Scenario or remaining work is manufactured.
    """
    operations = {item.id: item for item in scenario.operations}
    results = []
    total = 0
    for batch in batches.get("progress", ()):
        for record in batch.records:
            if record.entity != "operation_progress":
                continue
            total += 1
            if len(results) >= limit:
                continue
            row = record.values
            operation_id = row.get("operation_id")
            matched = operations.get(operation_id)
            results.append({
                "source": batch.source,
                "operation_id": operation_id,
                "status": row.get("status"),
                "completed_at": row.get("completed_at"),
                "recorded_work_seconds": row.get("recorded_work_seconds"),
                "recorded_setup_seconds": row.get("recorded_setup_seconds"),
                "produced_quantity": (str(row["produced_quantity"]) if row.get("produced_quantity") is not None else None),
                "declared_resource_id": row.get("resource_id"),
                "canonical_id_matched": matched is not None,
                "planned_remaining_run_seconds": matched.duration_seconds if matched else None,
                "planned_remaining_setup_seconds": matched.setup_seconds if matched else None,
            })
    return {
        "rows": results,
        "total_rows": total,
        "truncated": total > limit,
        "not_machine_occupancy_evidence": True,
        "source_only": True,
    }


def evidence_manifest(evidence, depth=0):
    """Source manifest recorded in a plan's evidence, following archived and what-if parents; None if absent."""
    if not isinstance(evidence, dict) or depth > 4:
        return None
    if isinstance(evidence.get("source_manifest"), dict):
        return evidence["source_manifest"]
    parent = evidence.get("parent")
    if isinstance(parent, dict):
        return evidence_manifest(parent.get("evidence", parent), depth + 1)
    return None


class DashboardState:
    def __init__(self, fixture: Path, staged_spec: Path | None = None):
        self.path = Path(fixture).expanduser().resolve(strict=True)
        workbook_source = self.path.suffix.lower() == '.xlsx'
        if self.path.suffix.lower() not in ('.json','.xlsx'):
            raise ValueError("Scenario/canonical JSON or a complete planning XLSX required")
        if self.path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError(f"dashboard source exceeds {MAX_SOURCE_BYTES // (1024 * 1024)} MiB file-only limit")
        source_text = None if workbook_source else self.path.read_text(encoding="utf-8")
        raw = None if workbook_source else json.loads(
            source_text, object_pairs_hook=_no_duplicates,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(
                f"nonfinite source constant {value!r}")))
        self.file_binding = False
        self.manifest = None
        self.build_issues = ()
        self.progress_snapshot = None
        self.company_memory = None
        self.material_free = None
        self._company_context = {}
        self.binding = None
        self.batches = None
        self.records = None
        self.excluded = None
        self.corrections = ()
        self.data_version = 0
        self._plans = OrderedDict()
        self._scheduled = OrderedDict()  # digest -> plan produced here by the scheduler and verified
        self._plans_lock = threading.Lock()
        self.workbook_input = None
        self._workbook_result = None
        self._workbook_evidence = None
        if workbook_source:
            if staged_spec is not None:
                raise ValidationError(['il workbook completo contiene già gli input setup/run: sidecar non ammesso'])
            from planning_factory.adapters.planning_workbook import load_workbook
            self.workbook_input = load_workbook(self.path)
            self.source = self.workbook_input.source
            self.manifest = self.workbook_input.built.manifest
            self.build_issues = self.workbook_input.built.issues
            self.records = self.workbook_input.built.records
            self.excluded = self.workbook_input.built.excluded
            from datetime import UTC, datetime
            self.read_at = datetime.now(UTC)
        elif isinstance(raw, dict) and {'installation', 'sources', 'domains'} <= set(raw):
            self.binding = self._parse_binding(raw)
            self.read_files()
            self.commit_build(self.try_build(()), ())
            self.file_binding = True
        else:
            self.source = loads(source_text)
        self._lock = threading.Lock()
        self._compute_slot = threading.BoundedSemaphore(1)
        self._default = None
        self._default_rule_tag = None
        self._stage_spec = None
        self._staged_lock = threading.Lock()
        self._stage_cache = None
        if staged_spec is not None:
            if self.file_binding:
                raise ValidationError([
                    "v0.3 preview requires an original run-only Scenario JSON, not a "
                    "canonical binding with potentially pre-applied material releases"
                ])
            from planning_factory.adapters.staged_file import load_staged_file
            self._stage_spec = load_staged_file(Path(staged_spec), self.source)

    def _parse_binding(self, raw):
        # The dashboard explicitly forbids SQL/ODBC/HTTP connectors.
        if not isinstance(raw['sources'], dict) or any(
                not isinstance(v, dict) or v.get('connector') not in ('file', 'canonical-json')
                for v in raw['sources'].values()):
            raise ValueError("dashboard bindings only permit local file/canonical-json sources")
        from planning_factory.canonical.binding import parse_binding
        return parse_binding(raw, self.path.parent, frozenset(('file', 'canonical-json')))

    def reread_binding(self):
        """Read the binding file again (a rebuild may move the horizon); the next read of the files uses it."""
        if not self.file_binding:
            raise ValidationError(["the source is not a canonical binding"])
        raw = json.loads(self.path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
        if not isinstance(raw, dict) or not {'installation', 'sources', 'domains'} <= set(raw):
            raise ValidationError(["the rebuilt binding is not a canonical binding"])
        self.binding = self._parse_binding(raw)

    def read_files(self, commit=True):
        """(Re)read every bound file. With ``commit=False`` the live data stay until a new build commits."""
        from datetime import UTC, datetime
        from planning_factory.canonical.builder import read_sources
        from planning_factory import connectors
        now = datetime.now(UTC).replace(microsecond=0)
        batches = read_sources(self.binding, connectors.create, now)
        if commit:
            self.batches = batches
        self.read_at = now
        return batches

    def try_build(self, corrections, batches=None):
        """Build the Scenario with the given corrections without changing the live state."""
        from datetime import UTC, datetime
        from planning_factory.canonical.builder import build_scenario
        built = build_scenario(self.binding, batches if batches is not None else self.batches,
                               datetime.now(UTC).replace(microsecond=0), corrections=tuple(corrections),
                               keep_records=True)
        if built.scenario is None or any(issue.severity == "error" for issue in built.issues):
            raise ValidationError(["file-only canonical binding cannot produce a valid Scenario",
                                   *[f"{i.code}: {i.detail}" + (f" ({i.entity} {i.key})" if i.entity else "")
                                     for i in built.issues if i.severity == 'error'][:50]])
        return built

    def commit_build(self, built, corrections, batches=None):
        # A business 'block' (e.g. MRP shortage) prevents publishing,
        # not read-only inspection. Expose the blocker explicitly.
        if batches is not None:
            self.batches = batches
        self.source = built.scenario
        self.material_free = (scenario_digest(built.scenario), built.material_free) if built.material_free else None
        self.manifest = built.manifest
        self.build_issues = built.issues
        self.records = built.records
        self.excluded = built.excluded
        self.corrections = tuple(corrections)
        self.progress_snapshot = progress_evidence(self.batches, self.source)
        self.data_version += 1
        lock = getattr(self, '_lock', None)
        if lock is not None:
            with lock:
                self._default = None
                self._stage_cache = None
        if self.company_memory is not None:
            self.company_memory.source = self.source
            scope = (self.manifest or {}).get('rule_scope')
            self.company_memory.scope_context = json.loads(json.dumps(scope)) if scope else None
            self.company_memory.material_free = self.material_free

    def staged_preview(self):
        """Calculate once using a strictly paired file-only v0.3 specification."""
        if getattr(self, 'workbook_input', None) is not None and self.workbook_input.stage_contract is not None:
            scenario,result = self.calculate()
            return {**self._workbook_evidence,
                    'source_scenario_digest': scenario_digest(self.source),
                    'technical_only': True, 'erp_connected': False, 'publishable': False,
                    'mode': 'setup-run', 'stock_accounting': 'net',
                    'scenario': json.loads(dumps(scenario)), 'plan': json.loads(dumps(result)),
                    'partial_plan': bool(result.unscheduled), 'unscheduled_count': len(result.unscheduled),
                    'warning': 'Piano da workbook completo; dati dichiarati, nessuna pubblicazione ERP.'}
        if self._stage_spec is None:
            raise ValidationError(["no opt-in v0.3 specification configured"])
        if self.company_memory is not None:
            adjusted, _ = self.company_memory.apply(self.source)
            if scenario_digest(adjusted) != scenario_digest(self.source):
                raise ValidationError(['company rules changed the Scenario: regenerate the paired v0.3 specification'])
        with self._staged_lock:
            if self._stage_cache is not None:
                return self._stage_cache
            if not self._compute_slot.acquire(blocking=False):
                raise PlannerBusyError("planning already in progress")
            try:
                from planning_factory.adapters.staged_file import calculate_staged_preview
                from planning_factory.adapters.json_io import primitive
                result = calculate_staged_preview(self.source, self._stage_spec)
                preview = {
                    "source_scenario_digest": scenario_digest(self.source),
                    "technical_only": True,
                    "erp_connected": False,
                    "publishable": False,
                    "mode": self._stage_spec.mode,
                    "stock_accounting": "net" if self._stage_spec.inventory_net else "gross",
                    "material_pegs": [{
                        "task_id": peg.task_id, "item_id": peg.item_id,
                        "supply_id": peg.supply_id, "quantity": str(peg.quantity)
                    } for peg in result.allocation.pegs],
                    "stage_bookings": [primitive(booking) for booking in result.staged.bookings],
                    "stage_map": [list(row) for row in result.staged.stage_map],
                    "scenario": json.loads(dumps(result.staged.scenario)),
                    "plan": json.loads(dumps(result.staged.plan)),
                    "partial_plan": bool(result.staged.plan.unscheduled),
                    "unscheduled_count": len(result.staged.plan.unscheduled),
                    "audited": True,
                    "warning": (
                        "Technical file-based setup/run preview, not an ERP publishable plan. "
                        "Inputs are asserted by the supplying system, not MES-certified by this UI."
                    ),
                }
                self._stage_cache = preview
                return preview
            finally:
                self._compute_slot.release()

    def descriptions(self):
        """Presentation texts in ``descrizioni.json`` next to the fixture, or None.

        Names, customers and articles read from the source system for display only: the engine never
        reads them and they do not change the Scenario digest. A missing or invalid file means no texts.
        """
        if self.workbook_input is not None:
            tables = self.workbook_input.tables
            context = {name:{} for name in ('order','operation','resource','nest')}
            for row in tables.get('provenance', []):
                if row.get('transformation')=='display_context' and row.get('entity') in context:
                    value=row.get('original_value')
                    if isinstance(value,dict) and row.get('record_key',{}).get('id'):
                        context[row['entity']][row['record_key']['id']]=dict(value)
            customers = {r['id']:r.get('name') for r in tables.get('customer',[])}
            orders = {r['id']:{**context['order'].get(r['id'],{}),'code':context['order'].get(r['id'],{}).get('code',r['id']),'customer_code':r.get('customer_id'),
                               'customer':customers.get(r.get('customer_id'))}
                      for r in tables.get('order',[])}
            operations = {r['id']:{**context['operation'].get(r['id'],{}),'description':r.get('description',r['id']),
                                  'phase':r.get('description',r['id']),
                                  **({'phase_code':r['phase_type']} if r.get('phase_type') else {}),
                                  **({'sequence':int(r['display_sequence'])} if str(r.get('display_sequence','')).isdigit() else {}),
                                  **({'uom':r['uom_id']} if r.get('uom_id') else {})}
                          for r in tables.get('operation',[])}
            operations.update({'setup:'+r['id']:{'description':r.get('description',r['id'])+' — attrezzaggio'}
                               for r in tables.get('operation',[])})
            for stage_id,original,stage in (self._workbook_evidence or {}).get('stage_map',[]):
                oid = original.removeprefix('job:')
                text = operations.get(oid,{}).get('description',oid)
                operations[stage_id.removeprefix('job:')]={'description':text + (' — attrezzaggio' if stage=='setup' else ' — lavorazione')}
            return {'schema':DESCRIPTIONS_SCHEMA,'installation':tables['scenario'][0]['dataset_id'],
                    'resources':{r['id']:{**context['resource'].get(r['id'],{}),
                        'name':r.get('description',r['id']),'description':r.get('description',r['id'])} for r in tables.get('resource',[])},
                    'orders':orders,'operations':operations,'nests':context['nest']}
        path = self.path.with_name(DESCRIPTIONS_FILE)
        try:
            if not path.is_file() or path.stat().st_size > MAX_DESCRIPTIONS_BYTES:
                return None
            raw = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_no_duplicates)
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict) or raw.get("schema") != DESCRIPTIONS_SCHEMA:
            return None
        return {key: raw[key] for key in ("schema", "note", "installation", "source",
                                          "resources", "orders", "operations", "nests")
                if isinstance(raw.get(key), (dict, str))}

    def customer_names(self):
        """Company name of each customer code in ``descrizioni.json`` (display only)."""
        described = (self.descriptions() or {}).get("orders") or {}
        return {r["customer_code"]: r["customer"] for r in described.values()
                if isinstance(r, dict) and isinstance(r.get("customer_code"), str) and isinstance(r.get("customer"), str)}

    def order_texts(self, manifest=None):
        """Order code and customer of each canonical order id, for the read tools and the assistant.

        With a canonical source the customer code is the canonical ``customer_id`` (corrections included);
        without one it is the code in ``descrizioni.json``. The company name is looked up by customer code
        in ``descrizioni.json``. A text the source does not give stays absent.

        ``manifest`` is the source manifest recorded with the plan being read (an archived revision, an
        older plan); by default the live one. ``False`` means a canonical source whose manifest for that
        plan is unknown: customers are left out rather than taken from today's data.
        """
        described = (self.descriptions() or {}).get("orders") or {}
        described = {k: v for k, v in described.items() if isinstance(v, dict)}
        names = self.customer_names()
        unattested = manifest is False
        scope = None if unattested else ((self.manifest if manifest is None else manifest) or {}).get("rule_scope")
        canonical = {r["id"]: r.get("customer_id") for r in scope.get("orders", [])} if scope else None
        texts = {}
        for oid in sorted(set(described) | set(canonical or ())):
            row = described.get(oid, {})
            code = None if unattested else canonical.get(oid) if canonical is not None else row.get("customer_code")
            text = {"code": row.get("code"), "customer_code": code,
                    "customer": names.get(code) if code is not None else None}
            text = {k: v for k, v in text.items() if isinstance(v, str)}
            if text:
                texts[oid] = text
        return texts

    def canonical_context(self):
        if self.manifest is None:
            return None
        # This is the provenance of the original file snapshot only.
        # After a what-if edit the result Scenario has a different digest.
        return {
            "source_manifest_digest": self.manifest.get("scenario_digest"),
            "built_at": self.manifest.get("built_at"),
            "source_only": True,
            "publishable": bool(self.manifest.get("publishable")),
            "issues": [issue.to_dict() for issue in self.build_issues],
            "domains": {
                name: {"digest": value.get("digest"),
                       "records": value.get("records")}
                for name, value in self.manifest.get("domains", {}).items()
            },
            "rules": self.manifest.get("rules", {"applied": [], "skipped": []}),
            "delivery_buffer_seconds": (self.manifest.get("delivery") or {}).get("delivery_buffer_seconds", 0),
            "materials": self.manifest.get("materials"),
            "progress": self.progress_snapshot,
        }

    def _audited_plan(self, scenario):
        if self.workbook_input is not None and self.workbook_input.stage_contract is not None:
            raise ValidationError(['la simulazione setup/run richiede la modifica e ricarica del workbook completo'])
        # Same digest, same Scenario: a plan the scheduler of this server already produced and verified is
        # reused, so the browser can load a plan the assistant has just calculated without computing it
        # twice. Only scheduler results are reused: plans uploaded for analysis or reopened from the archive
        # are indexed for the analyses (_plans), never served as the outcome of a calculation.
        digest = scenario_digest(scenario)
        with self._plans_lock:
            held = self._scheduled.get(digest)
            if held is not None:
                self._scheduled.move_to_end(digest)
                return held
        # The demo is intentionally not an unbounded remote compute service.
        # Use the authenticated worker-based service for enterprise hosting.
        if not self._compute_slot.acquire(blocking=False):
            raise PlannerBusyError("planning already in progress")
        try:
            result = plan(scenario)
            findings = verify_plan(scenario, result)
            if findings:
                raise ValidationError(["independent plan audit failed", *findings])
            with self._plans_lock:
                self._scheduled[digest] = result
                while len(self._scheduled) > MAX_INDEXED_PLANS:
                    self._scheduled.popitem(last=False)
            return result
        finally:
            self._compute_slot.release()

    def calculate(self, settings=None):
        self._check_workbook_snapshot()
        source, evidence = self.company_memory.apply(self.source) if self.company_memory else (self.source, None)
        s = apply_settings(source, settings or {}, self.manifest)
        if self.workbook_input is not None:
            if scenario_digest(s) != scenario_digest(self.source):
                raise ValidationError(['i parametri del workbook vanno modificati nel file completo e ricaricati'])
            if self._workbook_result is None:
                if not self._compute_slot.acquire(blocking=False):
                    raise PlannerBusyError('planning already in progress')
                try:
                    result = self.workbook_input.calculate()
                    self._workbook_result = result[:2]
                    self._workbook_evidence = result[2]
                finally:
                    self._compute_slot.release()
            s,result = self._workbook_result
            self._remember_company_context(result.scenario_digest,evidence)
            self.remember_plan(s,result)
            return s,result
        result = self._audited_plan(s)
        self._remember_company_context(result.scenario_digest, evidence)
        self.remember_plan(s, result)
        return s, result

    def sheet_attributes(self):
        """Material and thickness of operations and nesting programs as the canonical records carry them
        (corrections included), for the analyses and the assistant. Empty without a canonical source."""
        found = {}
        for entity, label in (('operation', 'operations'), ('nest', 'nests')):
            found[label] = {str(r.key[0]): {k: r.values[k] for k in ('material', 'thickness_mm') if r.values.get(k) is not None}
                            for r in (self.records or {}).get(entity, ())}
            found[label] = {k: v for k, v in found[label].items() if v}
        return found

    def remember_plan(self, scenario, result, *, capacity_scale=None):
        """Index a verified plan by digest so analyses need not re-upload the Scenario."""
        from planning_factory.adapters.plan_insights import PlanInsights
        with self._plans_lock:
            if result.scenario_digest not in self._plans:
                own_workbook = self._workbook_result is not None and result.scenario_digest == self._workbook_result[1].scenario_digest
                scale = capacity_scale if capacity_scale is not None else ((self._workbook_evidence or {}).get('capacity_scale',1) if own_workbook else 1)
                self._plans[result.scenario_digest] = PlanInsights(scenario, result, self.sheet_attributes(),capacity_scale=scale)
            self._plans.move_to_end(result.scenario_digest)
            found = self._plans[result.scenario_digest]
            while len(self._plans) > MAX_INDEXED_PLANS:
                self._plans.popitem(last=False)
            return found

    def insights(self, digest):
        """Analyses of a plan computed and verified by this server, or None when no longer held."""
        if not isinstance(digest, str):
            raise ValidationError(['scenario_digest required'])
        with self._plans_lock:
            found = self._plans.get(digest)
            if found is not None:
                self._plans.move_to_end(digest)
            return found

    def _remember_company_context(self, digest, evidence):
        self._company_context[digest] = evidence
        if len(self._company_context) > 128:
            self._company_context.pop(next(iter(self._company_context)))

    def simulate(self, edits, settings=None):
        if self.workbook_input is not None and self.workbook_input.stage_contract is not None:
            raise ValidationError(['la simulazione setup/run richiede la modifica e ricarica del workbook completo'])
        from planning_factory.adapters.dashboard_simulation import apply_whatif_edits
        source, evidence = self.company_memory.apply(self.source) if self.company_memory else (self.source, None)
        adjusted = apply_whatif_edits(source, edits)
        adjusted = apply_settings(adjusted, settings or {}, self.manifest)
        candidate = self._audited_plan(adjusted)
        self._remember_company_context(candidate.scenario_digest, evidence)
        self.remember_plan(adjusted, candidate)
        result = {
            "scenario": json.loads(dumps(adjusted)),
            "plan": json.loads(dumps(candidate)),
            "meta": {
                "dataset": "What-if da file canonico",
                "file_name": self.path.name,
                "source": "whatif",
                "erp_connected": False,
                "read_only": True,
                "engine_verified": True,
                "partial_plan": bool(candidate.unscheduled),
                "v03_available": self._stage_spec is not None or bool(self.workbook_input and self.workbook_input.stage_contract),
                "source_publishable": self.manifest.get("publishable") if self.manifest else None,
                "simulated": True,
                "company_memory": self._company_context.get(candidate.scenario_digest),
                "canonical": self.canonical_context(),
                "note": "Scenario temporaneo: modifiche non salvate né pubblicate su Factory.",
            },
        }
        return result

    def _check_workbook_snapshot(self):
        if self.workbook_input is not None:
            import hashlib
            if hashlib.sha256(self.path.read_bytes()).hexdigest() != self.workbook_input.fingerprint:
                raise ValidationError(['workbook modificato su disco: premi Rileggi Excel nelle Fonti oppure riavvia la Control Room'])

    def reload_workbook(self):
        if self.workbook_input is None:
            raise ValidationError(['sorgente Excel richiesta'])
        if not self._compute_slot.acquire(blocking=False):
            raise PlannerBusyError('planning already in progress')
        try:
            # Build first: an invalid or half-saved file cannot replace the
            # last valid snapshot. Keep the locks held by concurrent requests.
            candidate = DashboardState(self.path)
            self.adopt_workbook(candidate)
        finally:
            self._compute_slot.release()

    def adopt_workbook(self,candidate,calculation=None):
        """Install an internally validated snapshot while the compute gate is held."""
        with self._lock:
            candidate.company_memory = self.company_memory
            candidate.data_version = self.data_version + 1
            if calculation is not None:
                candidate._workbook_result=calculation[:2]
                candidate._workbook_evidence=calculation[2]
            old_locks = {k:self.__dict__[k] for k in ('_lock','_compute_slot','_plans_lock','_staged_lock')}
            self.__dict__.update(candidate.__dict__)
            self.__dict__.update(old_locks)

    def bootstrap(self, settings=None):
        self._check_workbook_snapshot()
        if not settings:
            with self._lock:
                tag = self.company_memory.cache_key() if self.company_memory else None
                if tag != self._default_rule_tag:
                    self._default = None
                if self._default is None:
                    self._default = self.calculate()
                    self._default_rule_tag = tag
                s, p = self._default
        else:
            s, p = self.calculate(settings)
        return {
            "scenario": json.loads(dumps(s)),
            "plan": json.loads(dumps(p)),
            "meta": {
                "dataset": "Workbook completo" if self.workbook_input else "Scenario di esempio" if self.path.name == "workshop.json"
                           else "Binding canonico CSV/JSON" if self.file_binding else "Scenario JSON fornito",
                "file_name": self.path.name,
                "source": "planning_workbook" if self.workbook_input else "csv_binding" if self.file_binding else "scenario_json",
                "erp_connected": False,
                "read_only": True,
                "engine_verified": True,
                "partial_plan": bool(p.unscheduled),
                "v03_available": self._stage_spec is not None or bool(self.workbook_input and self.workbook_input.stage_contract),
                "source_publishable": self.manifest.get("publishable") if self.manifest else None,
                "canonical": self.canonical_context(),
                "workbook": self._workbook_evidence if self.workbook_input else None,
                "company_memory": self._company_context.get(p.scenario_digest),
                "note": "Piano tecnico calcolato da file, non pubblicato su Factory.",
            },
        }


def analysis(state: DashboardState, route, body):
    """Read-only analyses of a plan this server computed; the browser sends only its digest."""
    if route == "/api/explain":
        if set(body) != {"scenario_digest", "order_id"}:
            raise ValidationError(["expected exactly scenario_digest and order_id"])
    elif route in ("/api/quality", "/api/delays"):
        if set(body) != {"scenario_digest"}:
            raise ValidationError(["expected exactly scenario_digest"])
    elif set(body) - {"scenario_digest", "start", "days", "kind"} or "scenario_digest" not in body:
        raise ValidationError(["expected scenario_digest and optional start, days, kind"])
    insights = state.insights(body["scenario_digest"])
    if insights is None:
        return 409, {"error": "piano non più in memoria del server: ricalcola per analizzarlo",
                     "code": "plan_not_indexed"}
    if route == "/api/explain":
        return 200, insights.explain_order(body["order_id"])
    if route == "/api/quality":
        return 200, insights.quality()
    if route == "/api/delays":
        return 200, insights.delays()
    return 200, insights.daily_loads(body.get("start"), body.get("days", 14), body.get("kind", "machine"))


def make_handler(state: DashboardState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "PlanningFactoryDashboard/0.1"

        def _headers(self, status, mime, length):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(length))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy",
                             "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                             "img-src 'self' data:; connect-src 'self'; "
                             "font-src 'self'; base-uri 'none'; form-action 'none'; "
                             "frame-ancestors 'none'")
            self.end_headers()

        def _trusted_host(self):
            """Deny DNS-rebinding hosts; allow only loopback and GitHub private port proxy."""
            host=self.headers.get("Host", "").split(":", 1)[0].lower()
            return host in ("127.0.0.1", "localhost") or host.endswith(".app.github.dev")

        max_body = MAX_SETTINGS_BODY

        def _drain_body(self):
            # Answering with the body still unread makes Windows reset the socket (WinError
            # 10053): the client sees an abort instead of the status. Drain it, then close.
            self.body_unread = False
            self.close_connection = True
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return
            if not self.headers.get("Transfer-Encoding") and 0 < length <= self.max_body:
                self.connection.settimeout(15)
                self.rfile.read(length)

        def respond(self, code, obj):
            if code >= 400 and getattr(self, "body_unread", False):
                self._drain_body()
            raw = json.dumps(obj, ensure_ascii=False, allow_nan=False).encode("utf-8")
            self._headers(code, "application/json; charset=utf-8", len(raw))
            self.wfile.write(raw)

        def do_GET(self):
            if not self._trusted_host():
                return self.respond(403, {"error":"untrusted host"})
            if self.headers.get("Sec-Fetch-Site") not in (None,"none","same-origin"):
                return self.respond(403, {"error":"cross-origin requests forbidden"})
            route = urlsplit(self.path).path
            if route == "/api/health":
                return self.respond(200, {"status": "ok", "source": "file_only"})
            if route == "/api/bootstrap":
                try:
                    return self.respond(200, state.bootstrap())
                except PlannerBusyError as exc:
                    return self.respond(503, {"error": str(exc)})
                except (ValueError, ValidationError) as exc:
                    return self.respond(422, {"error": str(exc)})
            if route == "/api/descriptions":
                return self.respond(200, {"descriptions": state.descriptions()})
            if route == "/api/plan.csv":
                try:
                    _, p = state.calculate()
                    raw = csv_report(p).encode("utf-8")
                except PlannerBusyError as exc:
                    return self.respond(503, {"error": str(exc)})
                except (ValueError, ValidationError) as exc:
                    return self.respond(422, {"error": str(exc)})
                self._headers(200, "text/csv; charset=utf-8", len(raw))
                return self.wfile.write(raw)
            asset = ASSETS.get(route)
            if asset is None:
                return self.respond(404, {"error": "not found"})
            filename, mime = asset
            try:
                raw = (WEB / filename).read_bytes()
            except OSError:
                return self.respond(503, {"error": "dashboard asset not installed"})
            self._headers(200, mime, len(raw))
            self.wfile.write(raw)

        def do_POST(self):
            self.body_unread = True
            if not self._trusted_host():
                return self.respond(403, {"error":"untrusted host"})
            route = urlsplit(self.path).path
            if route not in ("/api/recalculate", "/api/simulate", "/api/v03-preview", "/api/explain", "/api/loads", "/api/quality", "/api/delays"):
                return self.respond(404, {"error": "not found"})
            # Same-origin protection: don't enable credential-free cross-site
            # POST from another web page through loopback.
            if self.headers.get("Sec-Fetch-Site") not in (None, "none", "same-origin"):
                return self.respond(403, {"error": "cross-origin requests forbidden"})
            origin = self.headers.get("Origin")
            if origin is not None:
                host = self.headers.get('Host', '')
                if origin not in (f"http://{host}", f"https://{host}"):
                    return self.respond(403, {"error": "cross-origin requests forbidden"})
            if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
                return self.respond(415, {"error": "application/json required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_SETTINGS_BODY:
                return self.respond(413, {"error": "invalid request length"})
            self.connection.settimeout(20)
            try:
                text = self.rfile.read(length)
                self.body_unread = False
                body = json.loads(
                    text.decode("utf-8"),
                    object_pairs_hook=_no_duplicates,
                    parse_constant=lambda value: (_ for _ in ()).throw(ValueError(
                        f"nonfinite request constant {value!r}")))
                if not isinstance(body, dict):
                    raise ValidationError(["JSON object required"])
                if route == "/api/v03-preview":
                    if body:
                        raise ValidationError(["v0.3 preview must not accept browser-supplied data"])
                    if state._stage_spec is None and not (state.workbook_input and state.workbook_input.stage_contract):
                        return self.respond(404, {"error": "no v0.3 file specification loaded"})
                    return self.respond(200, state.staged_preview())
                if route in ("/api/explain", "/api/loads", "/api/quality", "/api/delays"):
                    return self.respond(*analysis(state, route, body))
                if route == "/api/recalculate":
                    if set(body) != {"settings"}:
                        raise ValidationError(["expected object with exactly settings"])
                    return self.respond(200, state.bootstrap(body["settings"]))
                if set(body) != {"edits", "settings"}:
                    raise ValidationError(["simulation needs exactly edits and settings"])
                return self.respond(200, state.simulate(body["edits"], body["settings"]))
            except PlannerBusyError as exc:
                return self.respond(503, {"error": str(exc)})
            except (ValueError, TypeError, ValidationError, KeyError) as exc:
                return self.respond(422, {"error": str(exc)})
            except TimeoutError:
                return self.respond(408, {"error": "request timed out"})

        def log_message(self, fmt, *args):
            pass  # No manufacturing data, paths or caller payloads in logs.

    return Handler


class LocalDashboardServer(ThreadingHTTPServer):
    daemon_threads = True


def make_dashboard_server(fixture=DEFAULT_FIXTURE, *, staged_spec=None, host="127.0.0.1", port=8766):
    if host != "127.0.0.1":
        raise ValueError("demo dashboard may only bind on loopback")
    return LocalDashboardServer((host, port), make_handler(DashboardState(Path(fixture), staged_spec=staged_spec)))


def serve_dashboard(fixture=DEFAULT_FIXTURE, port=8766, *, staged_spec=None):
    with make_dashboard_server(fixture, port=port, staged_spec=staged_spec) as server:
        print(f"Planning Factory UI: http://127.0.0.1:{server.server_port}", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
