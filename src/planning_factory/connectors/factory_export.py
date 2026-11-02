"""Factory connector (#83, step 1): the existing normalized Factory export as a canonical source.

It wraps ``adapters.factory.import_snapshot`` unchanged (identity CONUM/LOCOD/IDFAS,
LOCLP lots, explicit resource bundles, residual policy) and splits its result into
canonical domains, so Factory can be combined with other sources per domain (e.g.
materials from AS400, nesting from a CAM) with declared precedence. Reading the
Factory database live, per domain, is the next step and stays read-only.
"""
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from planning_factory.adapters.factory import import_snapshot
from planning_factory.adapters.json_io import loads
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.canonical.entities import ENTITIES
from planning_factory.canonical.model import ReadRequest, Record, SourceBatch
from planning_factory.domain.validation import ValidationError
from .canonical_json import _no_duplicates, resolve

OPTIONS = {'template', 'snapshot', 'progress_policy', 'zero_phase_policy', 'profile_id'}


class FactoryExportConnector:
    kind = 'factory-export'

    def __init__(self, source: str, options: dict, base_dir: Path):
        unknown = set(options) - OPTIONS
        if unknown:
            raise ValidationError([f'{source}: unknown factory-export options {sorted(unknown)}'])
        self.source = source
        self.template = resolve(base_dir, options.get('template'))
        self.snapshot = resolve(base_dir, options.get('snapshot'))
        self.progress_policy = options.get('progress_policy', 'none')
        self.zero_phase_policy = options.get('zero_phase_policy', 'reject')
        self.profile_id = options.get('profile_id', 'factory')
        if self.progress_policy not in ('none', 'time', 'quantity'):
            raise ValidationError([f'{source}.progress_policy: "none", "time" or "quantity" required'])
        if self.zero_phase_policy not in ('reject', 'skip'):
            raise ValidationError([f'{source}.zero_phase_policy: "reject" or "skip" required'])
        self._loaded = None

    @classmethod
    def from_options(cls, source: str, options: dict, base_dir: Path):
        return cls(source, options, base_dir)

    def _load(self):
        if self._loaded is None:
            raw_template, raw_snapshot = self.template.read_bytes(), self.snapshot.read_bytes()
            try:
                snapshot = json.loads(raw_snapshot.decode('utf-8'), object_pairs_hook=_no_duplicates)
            except ValueError as exc:
                raise ValidationError([f'{self.snapshot.name}: invalid JSON: {exc}']) from None
            scenario = import_snapshot(loads(raw_template.decode('utf-8')), snapshot,
                                       progress_policy=self.progress_policy, zero_phase_policy=self.zero_phase_policy)
            extracted = datetime.fromtimestamp(int(self.snapshot.stat().st_mtime), UTC)
            digest = hashlib.sha256(raw_template + b'\0' + raw_snapshot).hexdigest()
            self._loaded = (scenario_to_bundle(scenario, profile_id=self.profile_id), extracted, digest)
        return self._loaded

    def read(self, request: ReadRequest) -> SourceBatch:
        bundle, extracted, digest = self._load()
        records = []
        for entity in request.entities:
            spec = ENTITIES[entity]
            for row in bundle['records'].get(entity, []):
                key = '/'.join(str(row[k]) for k in spec.key)
                records.append(Record(entity, row, f'factory:{entity}:{key}'))
        return SourceBatch(self.source, self.kind, request.domain, extracted, tuple(records), fingerprint=f'sha256:{digest}')
