"""ADR 004: canonical domains, source composition, freshness and the scenario builder."""
import ast
import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import UTC, datetime
from pathlib import Path

from planning_factory import connectors
from planning_factory.adapters.json_io import dumps, loads, scenario_digest
from planning_factory.canonical.binding import parse_binding
from planning_factory.canonical.builder import build_scenario, read_sources
from planning_factory.canonical.decompose import scenario_to_bundle
from planning_factory.canonical.entities import ENTITIES, json_schema
from planning_factory.canonical.model import Record, SourceBatch
from planning_factory.cli import main
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.scheduler import plan

ROOT = Path(__file__).resolve().parents[1]
WORKSHOP = loads((ROOT / 'examples' / 'workshop.json').read_text(encoding='utf-8'))
NOW = datetime(2026, 10, 19, 7, tzinfo=UTC)
ALL = ('resources', 'calendars', 'orders', 'routings', 'nesting', 'subcontract', 'progress', 'rules')


def binding(sources, domains, **extra):
    data = {'schema_version': 1, 'installation': 'test',
            'scenario': {'id': 'officina-demo', 'start': '2026-10-19T00:00:00Z', 'end': '2026-10-26T00:00:00Z'},
            'sources': {name: {'connector': 'canonical-json'} for name in sources}, 'domains': domains}
    data.update(extra)
    return parse_binding(data, Path('.'))


def batches_from(bundles: dict, b, extracted=NOW):
    """In-memory connector: {source: bundle} -> batches per bound domain."""
    result = {}
    for d in b.domains:
        for source in d.sources:
            rows = bundles[source]['records']
            records = tuple(Record(e, row, f'{source}#{e}/{i}') for e, items in rows.items()
                            if ENTITIES[e].domain == d.domain for i, row in enumerate(items))
            result.setdefault(d.domain, []).append(SourceBatch(source, 'memory', d.domain, extracted, records))
    return result


def plan_without_digest(p):
    data = json.loads(dumps(p))
    data.pop('scenario_digest')
    return data


def codes(result):
    return [(i.severity, i.code) for i in result.issues]


class RoundTripTests(unittest.TestCase):
    def test_workshop_rebuilds_the_same_plan(self):
        b = binding(['base'], {d: {'sources': ['base']} for d in ALL})
        result = build_scenario(b, batches_from({'base': scenario_to_bundle(WORKSHOP)}, b), NOW)
        self.assertEqual([], codes(result))
        self.assertTrue(result.publishable)
        self.assertEqual(plan_without_digest(plan(WORKSHOP)), plan_without_digest(plan(result.scenario)))

    def test_two_bindings_with_equivalent_data_give_the_same_scenario(self):
        bundle = scenario_to_bundle(WORKSHOP)
        one = binding(['base'], {d: {'sources': ['base']} for d in ALL})
        split_a = {'schema_version': 1, 'records': {k: v for k, v in bundle['records'].items()
                                                    if ENTITIES[k].domain in ('calendars', 'resources')}}
        split_b = {'schema_version': 1, 'records': {k: list(reversed(v)) for k, v in bundle['records'].items()
                                                    if ENTITIES[k].domain not in ('calendars', 'resources')}}
        two = binding(['erp', 'cam'], {d: {'sources': ['erp' if d in ('calendars', 'resources') else 'cam']} for d in ALL})
        first = build_scenario(one, batches_from({'base': bundle}, one), NOW)
        second = build_scenario(two, batches_from({'erp': split_a, 'cam': split_b}, two), NOW)
        self.assertEqual(scenario_digest(first.scenario), scenario_digest(second.scenario))
        self.assertEqual({d: v['digest'] for d, v in first.manifest['domains'].items()},
                         {d: v['digest'] for d, v in second.manifest['domains'].items()})
        self.assertEqual(['cam'], [s['source'] for s in second.manifest['domains']['routings']['sources']])


class CompositionTests(unittest.TestCase):
    def setUp(self):
        self.bundle = scenario_to_bundle(WORKSHOP)
        self.domains = {d: {'sources': ['erp']} for d in ALL}

    def manual(self, **records):
        return {'schema_version': 1, 'records': records}

    def operation(self, op_id='ord-a/piega'):
        return next(o for o in self.bundle['records']['operation'] if o['id'] == op_id)

    def test_conflict_without_precedence_is_an_error(self):
        op = dict(self.operation(), duration_seconds=1)
        self.domains['routings'] = {'sources': ['erp', 'manual']}
        b = binding(['erp', 'manual'], self.domains)
        result = build_scenario(b, batches_from({'erp': self.bundle, 'manual': self.manual(operation=[op])}, b), NOW)
        self.assertIsNone(result.scenario)
        self.assertIn(('error', 'source_conflict'), codes(result))

    def test_declared_precedence_wins_and_is_reported(self):
        op = dict(self.operation(), duration_seconds=1)
        self.domains['routings'] = {'sources': ['erp', 'manual'],
                                    'precedence': {'operation.duration_seconds': ['manual', 'erp']}}
        b = binding(['erp', 'manual'], self.domains)
        result = build_scenario(b, batches_from({'erp': self.bundle, 'manual': self.manual(operation=[op])}, b), NOW)
        built = {o.id: o for o in result.scenario.operations}
        self.assertEqual(1, built[op['id']].duration_seconds)
        self.assertEqual([('warn', 'source_override')], codes(result))
        self.assertTrue(result.publishable)

    def test_records_known_to_one_source_are_kept(self):
        self.domains['calendars'] = {'sources': ['erp', 'manual']}
        b = binding(['erp', 'manual'], self.domains)
        extra = self.manual(calendar_holiday=[{'calendar_id': 'shop', 'day': '2026-10-23'}])
        result = build_scenario(b, batches_from({'erp': self.bundle, 'manual': extra}, b), NOW)
        self.assertEqual([], codes(result))
        shop = next(c for c in result.scenario.calendars if c.id == 'shop')
        self.assertEqual(('2026-10-23',), tuple(d.isoformat() for d in shop.holidays))

    def test_order_values_apply_only_where_the_operation_is_silent(self):
        records = self.bundle['records']
        first, second = records['operation'][0], records['operation'][1]
        for op in (first, second):
            op.pop('due', None)
        second['due'] = '2026-10-23T12:00:00Z'
        for order in records['order']:
            if order['id'] in (first['order_id'], second['order_id']):
                order['due'] = '2026-10-24T12:00:00Z'
        b = binding(['erp'], self.domains)
        built = {o.id: o for o in build_scenario(b, batches_from({'erp': self.bundle}, b), NOW).scenario.operations}
        self.assertEqual(datetime(2026, 10, 24, 12, tzinfo=UTC), built[first['id']].due)
        self.assertEqual(datetime(2026, 10, 23, 12, tzinfo=UTC), built[second['id']].due)

    def test_invalid_and_duplicate_records_point_to_their_source_row(self):
        records = self.bundle['records']
        records['operation'][0] = {k: v for k, v in records['operation'][0].items() if k != 'setup_seconds'}
        records['resource'].append(dict(records['resource'][0]))
        records['calendar'][0]['colour'] = 'red'
        b = binding(['erp'], self.domains)
        result = build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)
        self.assertIsNone(result.scenario)
        found = {(i.code, i.detail, i.original_key) for i in result.issues}
        self.assertIn(('invalid_record', 'missing field setup_seconds', 'erp#operation/0'), found)
        self.assertIn(('invalid_record', 'unknown field colour', 'erp#calendar/0'), found)
        self.assertIn('duplicate_key', {c for c, _, _ in found})

    def test_unknown_references_and_alternative_gaps_are_errors(self):
        records = self.bundle['records']
        records['resource'][0]['calendar_id'] = 'missing-calendar'
        demand = next(d for d in records['operation_demand'] if d['alternative'] == 0)
        records['operation_demand'].append(dict(demand, alternative=5, resource_id=records['resource'][1]['id']))
        records['nest_member'].append({'nest_id': 'ghost', 'operation_id': records['operation'][0]['id']})
        b = binding(['erp'], self.domains)
        result = build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)
        self.assertIsNone(result.scenario)
        self.assertTrue({'unknown_reference', 'alternative_gap'} <= {c for _, c in codes(result)})

    def test_rules_unbound_uses_engine_defaults_with_a_warning(self):
        del self.domains['rules']
        b = binding(['erp'], self.domains)
        result = build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)
        self.assertEqual([('warn', 'default_profile')], codes(result))
        self.assertIsNone(result.manifest['profile'])

    def test_profile_must_be_selected_when_several_exist(self):
        self.bundle['records']['profile'].append(dict(self.bundle['records']['profile'][0], id='rush', mode='backward'))
        b = binding(['erp'], self.domains)
        self.assertIn(('error', 'profile_selection'), codes(build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)))
        b = binding(['erp'], self.domains, profile='rush')
        result = build_scenario(b, batches_from({'erp': self.bundle}, b), NOW)
        self.assertEqual('backward', result.scenario.settings.mode)
        self.assertEqual('rush', result.manifest['profile'])


class FreshnessTests(unittest.TestCase):
    def setUp(self):
        self.bundle = scenario_to_bundle(WORKSHOP)
        domains = {d: {'sources': ['erp']} for d in ALL}
        domains['progress']['max_age_minutes'] = 30
        self.b = binding(['erp'], domains)

    def test_stale_domain_blocks_publication_but_still_builds(self):
        result = build_scenario(self.b, batches_from({'erp': self.bundle}, self.b, datetime(2026, 10, 19, 6, tzinfo=UTC)), NOW)
        self.assertIsNotNone(result.scenario)
        self.assertFalse(result.publishable)
        self.assertEqual([('block', 'stale_data')], codes(result))
        progress = result.manifest['domains']['progress']['sources'][0]
        self.assertEqual((3600, True), (progress['age_seconds'], progress['stale']))

    def test_confirmation_makes_stale_data_publishable_and_keeps_the_trace(self):
        result = build_scenario(self.b, batches_from({'erp': self.bundle}, self.b, datetime(2026, 10, 19, 6, tzinfo=UTC)),
                                NOW, accept_stale=True)
        self.assertTrue(result.publishable)
        self.assertEqual([('warn', 'stale_data_accepted')], codes(result))


class BindingTests(unittest.TestCase):
    def base(self):
        return {'schema_version': 1, 'installation': 'x', 'sources': {'erp': {'connector': 'canonical-json'}},
                'scenario': {'id': 's', 'start': '2026-10-19T00:00:00Z', 'end': '2026-10-26T00:00:00Z'},
                'domains': {d: {'sources': ['erp']} for d in ('resources', 'calendars', 'orders', 'routings')}}

    def rejects(self, data, text):
        with self.assertRaises(ValidationError) as ctx:
            parse_binding(data, Path('.'), connectors.available())
        self.assertIn(text, str(ctx.exception))

    def test_credentials_are_never_accepted(self):
        data = self.base()
        data['sources']['erp']['options'] = {'dsn': 'AS400', 'password': 'x'}
        self.rejects(data, 'credentials are not allowed')
        data['sources']['erp']['options'] = {'connection': 'DRIVER=x;UID=u;PWD=secret'}
        self.rejects(data, 'connection strings with passwords')
        data['sources']['erp']['options'] = {'path': 'a.json', 'password_env': 'CLIENTE_AS400_PASSWORD'}
        parse_binding(data, Path('.'))

    def test_required_reserved_and_unknown_domains(self):
        data = self.base()
        del data['domains']['orders']
        self.rejects(data, 'binding.domains.orders: required domain is not bound')
        data = self.base()
        data['domains']['transport'] = {'sources': ['erp']}
        self.rejects(data, 'not composed yet')
        data = self.base()
        data['domains']['materials'] = {'sources': ['erp']}
        self.rejects(data, 'allocation_order')
        data = self.base()
        data['domains']['weather'] = {'sources': ['erp']}
        self.rejects(data, 'unknown domain')

    def test_sources_connectors_and_precedence_are_checked(self):
        data = self.base()
        data['domains']['routings'] = {'sources': ['erp', 'nowhere']}
        self.rejects(data, 'declared sources')
        data = self.base()
        data['sources']['erp']['connector'] = 'telepathy'
        self.rejects(data, 'unknown connector')
        data = self.base()
        data['domains']['routings']['precedence'] = {'operation.id': ['erp']}
        self.rejects(data, 'non-key field')
        data = self.base()
        data['domains']['routings']['precedence'] = {'resource.capacity': ['erp']}
        self.rejects(data, 'non-key field')


class ConnectorAndCliTests(unittest.TestCase):
    def write(self, folder, bundle, domains=None):
        Path(folder, 'bundle.json').write_text(json.dumps(bundle), encoding='utf-8')
        data = {'schema_version': 1, 'installation': 'demo',
                'scenario': {'id': 'officina-demo', 'start': '2026-10-19T00:00:00Z', 'end': '2026-10-26T00:00:00Z'},
                'sources': {'files': {'connector': 'canonical-json', 'options': {'path': 'bundle.json'}}},
                'domains': domains or {d: {'sources': ['files']} for d in ALL}}
        path = Path(folder, 'binding.json')
        path.write_text(json.dumps(data), encoding='utf-8')
        return path

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args))
        return code, out.getvalue(), err.getvalue()

    def test_build_scenario_cli_writes_scenario_manifest_and_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, scenario_to_bundle(WORKSHOP, datetime(2026, 10, 19, 6, tzinfo=UTC)))
            out = [str(Path(tmp, n)) for n in ('s.json', 'm.json', 'r.json')]
            code, stdout, _ = self.cli('build-scenario', str(path), '-o', out[0], '--manifest', out[1], '--report', out[2],
                                       '--now', '2026-10-19T07:00:00Z')
            self.assertEqual(0, code)
            manifest = json.loads(Path(out[1]).read_text(encoding='utf-8'))
            scenario = loads(Path(out[0]).read_text(encoding='utf-8'))
            self.assertEqual(scenario_digest(scenario), manifest['scenario_digest'])
            self.assertEqual(json.loads(stdout)['scenario_digest'], manifest['scenario_digest'])
            source = manifest['domains']['routings']['sources'][0]
            self.assertEqual(('files', 3600), (source['source'], source['age_seconds']))
            self.assertTrue(source['fingerprint'].startswith('sha256:'))

    def test_missing_source_file_is_reported_not_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, scenario_to_bundle(WORKSHOP))
            Path(tmp, 'bundle.json').unlink()
            out = [str(Path(tmp, n)) for n in ('s.json', 'm.json', 'r.json')]
            code, _, _ = self.cli('build-scenario', str(path), '-o', out[0], '--manifest', out[1], '--report', out[2])
            self.assertEqual(1, code)
            self.assertFalse(Path(out[0]).exists())
            report = json.loads(Path(out[2]).read_text(encoding='utf-8'))
            self.assertIn('source_unavailable', {i['code'] for i in report['issues']})

    def test_unknown_entity_in_bundle_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.write(tmp, {'schema_version': 1, 'records': {'weather': []}})
            b = parse_binding(json.loads(path.read_text(encoding='utf-8')), Path(tmp), connectors.available())
            batches = read_sources(b, connectors.create, NOW)
            self.assertIn('unknown entities', batches['routings'][0].issues[0].detail)

    def test_export_canonical_cli_and_output_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp, 'bundle.json')
            code, _, _ = self.cli('export-canonical', str(ROOT / 'examples' / 'workshop.json'), '-o', str(target))
            self.assertEqual(0, code)
            self.assertEqual(scenario_to_bundle(WORKSHOP), json.loads(target.read_text(encoding='utf-8')))
            code, _, err = self.cli('export-canonical', str(target), '-o', str(target))
            self.assertEqual(1, code)
            self.assertIn('must not overwrite', err)


class ContractTests(unittest.TestCase):
    def test_committed_json_schema_matches_the_entity_table(self):
        committed = json.loads((ROOT / 'contracts' / 'canonical-v1.schema.json').read_text(encoding='utf-8'))
        self.assertEqual(json_schema(), committed)

    def test_engine_and_domain_never_import_canonical_data_or_connectors(self):
        forbidden = ('planning_factory.canonical', 'planning_factory.connectors', 'planning_factory.canonical_cli')
        for layer in ('engine', 'domain'):
            for path in (ROOT / 'src' / 'planning_factory' / layer).glob('*.py'):
                tree = ast.parse(path.read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                        [node.module or ''] if isinstance(node, ast.ImportFrom) else []
                    relative = isinstance(node, ast.ImportFrom) and node.level and (node.module or '').split('.')[0] in (
                        'canonical', 'connectors', 'canonical_cli')
                    self.assertFalse(relative or any(n.startswith(forbidden) for n in names), f'{path.name} imports {names}')


if __name__ == '__main__':
    unittest.main()
