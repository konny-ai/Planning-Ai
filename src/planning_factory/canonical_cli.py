"""CLI commands for canonical data (ADR 004), kept apart from cli.py to limit coupling."""
import json
from datetime import UTC, datetime
from pathlib import Path

from .adapters.json_io import dumps, loads
from .canonical.binding import load_binding
from .canonical.builder import build_scenario, read_sources
from .canonical.changes import manifest_changes
from .canonical.decompose import scenario_to_bundle
from .domain.validation import ValidationError
from . import connectors
from .connectors.files import write_csv_tables

COMMANDS = ('build-scenario', 'export-canonical', 'changes', 'service-levels')


def register(sub) -> None:
    build = sub.add_parser('build-scenario', help='Compose canonical domains from the bound sources into a scenario')
    build.add_argument('binding', help='Installation binding JSON (ADR 004)')
    build.add_argument('-o', '--output', required=True, help='Scenario JSON')
    build.add_argument('--manifest', required=True, help='Per-domain sources, digests and data age')
    build.add_argument('--report', required=True, help='Data quality issues')
    build.add_argument('--now', help='Build instant (ISO 8601 with offset), default current time')
    build.add_argument('--accept-stale', action='store_true', help='Confirm stale data: publishable despite age limits')
    export = sub.add_parser('export-canonical', help='Write an existing scenario as a canonical-json bundle')
    export.add_argument('scenario')
    export.add_argument('-o', '--output', help='canonical-json bundle')
    export.add_argument('--csv-dir', help='Also write one CSV per entity (file connector templates)')
    export.add_argument('--extracted-at', help='Instant recorded as extraction time (ISO 8601 with offset)')
    export.add_argument('--profile-id', default='default')
    levels = sub.add_parser('service-levels', help='On-time orders against internal (buffered) and promised due dates')
    levels.add_argument('scenario')
    levels.add_argument('plan')
    levels.add_argument('manifest')
    changes = sub.add_parser('changes', help='Compare two build manifests: changed domains and recompute event')
    changes.add_argument('before', help='Previous manifest')
    changes.add_argument('after', help='New manifest')


def _instant(value):
    if value is None:
        return datetime.now(UTC).replace(microsecond=0)
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValidationError(['instant needs a timezone offset'])
    return parsed.astimezone(UTC)


def _distinct(outputs, inputs):
    resolved = [Path(p).resolve() for p in outputs]
    if len(set(resolved)) != len(resolved) or set(resolved) & {Path(p).resolve() for p in inputs}:
        raise ValidationError(['output paths must be distinct and must not overwrite inputs'])


def run(args) -> int:
    if args.command == 'service-levels':
        from .adapters.json_io import plan_loads
        from .canonical.delivery import service_levels
        scenario = loads(Path(args.scenario).read_text(encoding='utf-8'))
        manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
        if manifest.get('scenario_digest') is not None and manifest['scenario_digest'] != json.loads(
                Path(args.plan).read_text(encoding='utf-8')).get('scenario_digest'):
            raise ValidationError(['plan and manifest belong to different scenarios'])
        result = service_levels(plan_loads(Path(args.plan).read_text(encoding='utf-8')),
                                {o.id: o.order_id for o in scenario.operations},
                                manifest.get('delivery', {}).get('promised_due', {}))
        print(dumps(result), end='')
        return 0
    if args.command == 'changes':
        old, new = (json.loads(Path(p).read_text(encoding='utf-8')) for p in (args.before, args.after))
        result = manifest_changes(old, new)
        print(dumps(result), end='')
        return 3 if result['recompute'] else 0
    if args.command == 'export-canonical':
        if not args.output and not args.csv_dir:
            raise ValidationError(['export-canonical needs -o and/or --csv-dir'])
        _distinct([p for p in (args.output, args.csv_dir) if p], [args.scenario])
        scenario = loads(Path(args.scenario).read_text(encoding='utf-8'))
        extracted = _instant(args.extracted_at) if args.extracted_at else None
        bundle = scenario_to_bundle(scenario, extracted, args.profile_id)
        if args.output:
            Path(args.output).write_text(json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        summary = {'records': sum(len(v) for v in bundle['records'].values())}
        if args.csv_dir:
            summary['tables'] = write_csv_tables(bundle, Path(args.csv_dir))
        print(json.dumps(summary))
        return 0
    _distinct([args.output, args.manifest, args.report], [args.binding])
    binding = load_binding(Path(args.binding), connectors.available())
    now = _instant(args.now)
    result = build_scenario(binding, read_sources(binding, connectors.create, now), now, args.accept_stale)
    Path(args.manifest).write_text(dumps(result.manifest), encoding='utf-8')
    Path(args.report).write_text(dumps(result.report()), encoding='utf-8')
    if result.scenario is not None:
        Path(args.output).write_text(dumps(result.scenario), encoding='utf-8')
    print(json.dumps({'buildable': result.scenario is not None, 'publishable': result.publishable,
                      'scenario_digest': result.manifest['scenario_digest'], 'issues': result.manifest['issues']}))
    if result.scenario is None:
        return 1
    return 0 if result.publishable else 2
