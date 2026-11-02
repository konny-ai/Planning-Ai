"""Reproducible synthetic material/setup/WIP scenarios through strict file input.

Expected rejections are evidence, not customer acceptance. No CLIENTE quantities,
state, machine names or business rules are inferred from these examples.
"""
import argparse
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path

from planning_factory.adapters.json_io import dumps, primitive, scenario_digest
from planning_factory.adapters.staged_file import load_staged_file, calculate_staged_preview
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.verification import verify_plan
from tools.generate_v03_stage_demo import make_example


def decimal_json(value):
    if isinstance(value, Decimal):
        return str(value)
    raise TypeError(f"unsupported evidence value: {type(value).__name__}")


def cases():
    _, base = make_example()
    rows = []
    def add(name, edit=None, rejection=None, checks=None):
        value = deepcopy(base)
        if edit:
            edit(value)
        rows.append((name, value, rejection, checks or {}))
    add('net-normal', checks={'quantity': '4', 'setups': 1, 'runs': 2})
    def gross(v):
        v['stock']['accounting'] = 'gross'
        v['stock']['supplies'][0]['quantity'] = '6'
        v['stock']['external_commitments'] = [{'supply_id': 'SYNTH-SUPPLY-001', 'quantity': '1'}]
    add('gross-reserved', gross, checks={'quantity': '4', 'setups': 1, 'runs': 2})
    def fractional(v):
        v['stock']['supplies'] = [
            {'id': 'FRACTION-STOCK', 'item_id': 'SYNTH-PART-001', 'quantity': '1.1', 'available_at': '2026-10-19T08:00:00Z'},
            {'id': 'FRACTION-PO', 'item_id': 'SYNTH-PART-001', 'quantity': '1.1', 'available_at': '2026-10-20T10:00:00Z'}]
        for row in v['needs']:
            row['quantity'] = '1.1'
    add('fractional-delayed-purchase', fractional, checks={'quantity': '2.2', 'runs': 2, 'last_run_after': '2026-10-20T10:00:00+00:00'})
    def short(v):
        v['stock']['supplies'][0]['quantity'] = '3.999'
    add('shortage-blocks-all-stages', short, 'material shortages')
    def overcommit(v):
        gross(v)
        v['stock']['external_commitments'][0]['quantity'] = '6.1'
    add('external-overreservation', overcommit, 'commitments exceed quantity')
    def double_net(v):
        v['stock']['external_commitments'] = []
    add('double-net-forbidden', double_net, 'net stock forbids commitments')
    def stale(v):
        v['source_scenario_digest'] = '0' * 64
    add('stale-source-rejected', stale, 'does not match')
    def unknown(v):
        v['setup_contract']['initial_machine_states']['WORKCELL_1'] = None
    add('unknown-state-requires-setup', unknown, checks={'setups': 1, 'runs': 2})
    def holds(v):
        v['mode'] = 'holds'
        v['setup_contract']['initial_machine_states']['WORKCELL_1'] = 'PROGRAM_BLUE'
        v['active_holds'] = [{'resource_id': 'WORKCELL_1', 'observed_at': '2026-10-19T00:00:00Z',
                              'reserved_until': '2026-10-19T10:00:00Z', 'source_ref': 'SYNTH:MES:r1',
                              'occupying_task_id': 'job:EXTERNAL-RUN-NOT-IN-SCENARIO'}]
    add('running-machine-hold', holds, checks={'setups': 1, 'runs': 2, 'all_stages_after': '2026-10-19T10:00:00+00:00'})
    def duplicate(v):
        holds(v)
        v['active_holds'][0]['occupying_task_id'] = 'job:CUT-A'
    add('running-task-not-scheduled-twice', duplicate, 'must be excluded')
    def paused(v):
        v['mode'] = 'paused'
        v['residual_material_requirements_confirmed'] = True
        v['observations'] = [{'machine_id': 'WORKCELL_1', 'observed_at': '2026-10-19T00:00:00Z',
                              'state_id': 'PROGRAM_BLUE', 'source_ref': 'SYNTH:MES:r2', 'idle_confirmed': True}]
        v['paused_progresses'] = [{'task_id': 'job:CUT-A', 'machine_id': 'WORKCELL_1', 'original_seconds': 3600,
                                   'worked_seconds': 1200, 'remaining_seconds': 2400,
                                   'paused_at': '2026-10-19T00:00:00Z', 'source_ref': 'SYNTH:progress:r2'}]
        v['needs'][0]['quantity'] = '0.5'  # explicitly supplied residual, not inferred by time ratio
    add('paused-residual-materials', paused, checks={'setups': 0, 'runs': 2, 'quantity': '2.5', 'first_run_seconds': 2400})
    def no_residual(v):
        paused(v)
        v['residual_material_requirements_confirmed'] = False
    add('paused-needs-confirmation', no_residual, 'explicitly confirmed as residual')
    def stale_observation(v):
        paused(v)
        v['observations'][0]['observed_at'] = '2026-10-18T00:00:00Z'
    add('stale-wip-observation', stale_observation, 'must match horizon start')
    def inconsistent(v):
        paused(v)
        v['paused_progresses'][0]['remaining_seconds'] = 2500
    add('inconsistent-wip-residual', inconsistent, 'inconsistent exact remaining')
    return rows


def run(destination):
    folder = Path(destination).resolve()
    folder.mkdir(parents=True)
    source, _ = make_example()
    original_digest = scenario_digest(source)
    (folder / 'scenario.json').write_text(dumps(source), encoding='utf-8')
    rows = []
    for name, raw, rejection, checks in cases():
        case = folder / name
        case.mkdir()
        path = case / 'spec.json'
        path.write_text(json.dumps(raw, indent=2) + '\n', encoding='utf-8')
        try:
            request = load_staged_file(path, source)
            result = calculate_staged_preview(source, request)
            if rejection:
                raise AssertionError(f'{name}: expected rejection containing {rejection!r}')
            if verify_plan(result.staged.scenario, result.staged.plan) or result.staged.plan.unscheduled:
                raise AssertionError(f'{name}: incomplete or unaudited plan')
            bookings = result.staged.bookings
            runs = [b for b in bookings if b.stage == 'run']
            actual = {'setups': sum(b.stage == 'setup' for b in bookings), 'runs': len(runs),
                      'quantity': str(sum((p.quantity for p in result.allocation.pegs), Decimal())),
                      'first_run_seconds': sum(s.seconds for s in runs[0].segments)}
            for key in ('setups', 'runs', 'first_run_seconds'):
                if key in checks and checks[key] != actual[key]:
                    raise AssertionError(f'{name}: {key}: {actual[key]} != {checks[key]}')
            if 'quantity' in checks and Decimal(checks['quantity']) != Decimal(actual['quantity']):
                raise AssertionError(f'{name}: incorrect material allocation')
            if 'last_run_after' in checks and max(b.segments[0].start.isoformat() for b in runs) < checks['last_run_after']:
                raise AssertionError(f'{name}: ran before purchased material availability')
            if 'all_stages_after' in checks and min(b.segments[0].start.isoformat() for b in bookings) < checks['all_stages_after']:
                raise AssertionError(f'{name}: occupied machine was reused')
            evidence = {'name': name, 'status': 'passed', 'outcome': 'audited_plan', 'metrics': actual}
            (case / 'result.json').write_text(json.dumps(primitive(result), indent=2, default=decimal_json) + '\n', encoding='utf-8')
        except ValidationError as exc:
            if not rejection or rejection not in str(exc):
                raise
            evidence = {'name': name, 'status': 'passed', 'outcome': 'expected_rejection', 'reason': str(exc)}
        if scenario_digest(source) != original_digest:
            raise AssertionError('source was mutated')
        rows.append(evidence)
        (case / 'verification.json').write_text(json.dumps(evidence, indent=2) + '\n', encoding='utf-8')
    summary = {'passed': True, 'synthetic': True, 'cases': rows, 'source_unchanged': True,
               'production_approved': False, 'sql_server_used': False, 'cliente_data_used': False}
    (folder / 'summary.json').write_text(json.dumps(summary, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(summary, indent=2))
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='new directory; evidence is never overwritten')
    run(parser.parse_args(argv).out)


if __name__ == '__main__':
    main()
