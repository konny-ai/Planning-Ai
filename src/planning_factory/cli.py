"""File-based commands. Diagnostics are machine-readable and exit codes explicit."""
import argparse
import json
from pathlib import Path
import sys
from .adapters.json_io import dumps, loads, plan_loads, scenario_digest
from .adapters.reports import csv_report, html_report, html_comparison
from .domain.validation import ValidationError
from .engine.scheduler import plan
from .engine.verification import verify_plan
from .engine.simulation import compare
from .engine.optimization import optimize
from .engine.local_search import local_search
from .engine.setup_audit import audit_setups, load_setup_rules
from .engine.setup_planner import plan_with_setups
from .engine.setup_search import search_setup_alternatives
from . import canonical_cli, workbook_cli


def _write_stage_html(path, report, *, protected):
    from .adapters.stage_report import stage_html_report
    from .engine.setup_capacity import StageAllocation, StageBooking
    from .domain.models import Interval
    from .adapters.json_io import timestamp
    target = Path(path).resolve()
    if target in protected:
        raise ValidationError(['--html must not overwrite an input or another output'])
    bookings = tuple(StageBooking(
        b['phase_id'], b['stage'],
        tuple(Interval(timestamp(i['start']), timestamp(i['end'])) for i in b['segments']),
        tuple(StageAllocation(a['resource_id'], a['occupancy_bps']) for a in b['allocations']))
        for b in report['bookings'])
    back = report.get('backward') or {}
    Path(path).write_text(stage_html_report(
        bookings, sequence=tuple(report.get('sequence', ())), kpis=report.get('kpis'),
        unplanned=tuple(report.get('unplanned', ())),
        setup_gaps=tuple(sorted((back.get('setup_gaps_seconds') or {}).items())),
        audit_violations=tuple(report.get('audit_violations', ()))), encoding='utf-8')


def _bookings_json(bookings):
    return [{'phase_id': b.phase_id, 'stage': b.stage,
             'segments': [{'start': i.start.isoformat(), 'end': i.end.isoformat()} for i in b.segments],
             'allocations': [{'resource_id': a.resource_id, 'occupancy_bps': a.occupancy_bps}
                             for a in b.allocations]} for b in bookings]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog='planning-factory')
    sub = parser.add_subparsers(dest='command', required=True)
    validate = sub.add_parser('validate', help='Validate a scenario and its expanded calendars')
    validate.add_argument('scenario')
    schedule = sub.add_parser('schedule', help='Compute and verify a finite capacity plan')
    schedule.add_argument('scenario')
    schedule.add_argument('-o', '--output', required=True)
    schedule.add_argument('--html')
    schedule.add_argument('--csv')
    schedule.add_argument('--record-constraints', action='store_true',
                          help='Record the constraint that fixed each start in the explanation (#88)')
    tuning = sub.add_parser('optimize', help='Find the best independently verified dispatch policy')
    tuning.add_argument('scenario')
    tuning.add_argument('-o', '--output', required=True)
    tuning.add_argument('--html')
    tuning.add_argument('--csv')
    tuning.add_argument('--metrics', help='Optional JSON with all candidate scores and the winner')
    tuning.add_argument('--time-limit', type=float, help='Stop evaluating more candidates after N seconds')
    tuning.add_argument('--policies', help='Comma-separated dispatch policies, priority first; "all" adds '
                                           'min_slack, critical_ratio, atc (#78 P2)')
    tuning.add_argument('--objectives', help='Comma-separated lexicographic objective profile (P17)')
    tuning.add_argument('--published', help='Published plan, needed by the moved_operations objective')
    local = sub.add_parser('local-search', help='Bounded opt-in neighborhood search')
    local.add_argument('scenario')
    local.add_argument('-o', '--output', required=True)
    local.add_argument('--budget', type=int, default=32, help='Planning trials, between 1 and 64')
    local.add_argument('--metrics', help='Optional JSON with baseline, winner and trial count')
    local.add_argument('--time-limit', type=float, help='Stop evaluating more candidates after N seconds')
    local.add_argument('--objectives', help='Comma-separated lexicographic objective profile (P17)')
    local.add_argument('--published', help='Published plan, needed by the moved_operations objective')
    local.add_argument('--html')
    local.add_argument('--csv')
    verify = sub.add_parser('verify', help='Independently audit an existing plan')
    verify.add_argument('scenario')
    verify.add_argument('plan')
    setup_search = sub.add_parser('setup-search', help='Search bounded resource alternatives under setup constraints')
    setup_search.add_argument('scenario')
    setup_search.add_argument('rules')
    setup_search.add_argument('-o', '--output', required=True)
    setup_search.add_argument('--scenario-output', required=True)
    setup_search.add_argument('--budget', type=int, default=8)
    setup_search.add_argument('--repair-budget', type=int, default=12)
    setup_search.add_argument('--time-limit', type=float, help='Stop evaluating more candidates after N seconds')
    setup_plan = sub.add_parser('setup-plan', help='Plan with explicit sequence setup constraints (opt-in)')
    setup_plan.add_argument('scenario')
    setup_plan.add_argument('rules')
    setup_plan.add_argument('-o', '--output', required=True, help='Verified plan JSON')
    setup_plan.add_argument('--scenario-output', required=True, help='Adjusted scenario JSON with setup-derived releases')
    setup_plan.add_argument('--budget', type=int, default=12, help='Repair iterations, 1..32')
    setup_plan.add_argument('--html')
    setup_plan.add_argument('--csv')
    stages = sub.add_parser('setup-stages', help='Plan setup and run as separate audited stages (ADR 003, opt-in)')
    stages.add_argument('scenario', help='Run-only scenario JSON (no v0.2 setup_seconds)')
    stages.add_argument('contract', help='Canonical setup contract v1 JSON')
    stages.add_argument('-o', '--output', required=True, help='Stage plan JSON: sequence, bookings, KPIs, audit')
    stages.add_argument('--sequence', help='Comma-separated phase order; default: earliest due date, precedence-safe')
    stages.add_argument('--start', choices=('edd', 'state-grouped', 'best'), default='edd',
                        help='Default start order when --sequence is absent; state-grouped falls back to edd if infeasible; best needs --objective and keeps the better of the two')
    stages.add_argument('--objective', choices=('delivery', 'setup', 'flow', 'gap'),
                        help='Optional bounded sequence search for this explicit objective')
    stages.add_argument('--neighbourhood', choices=('swap', 'insertion'), default='swap')
    stages.add_argument('--just-in-time-tail', action='store_true',
                        help='First part of --backward (ADR 005): delay runs by the room they have, setups stay early; needs --scenario-output')
    stages.add_argument('--backward', action='store_true',
                        help='ADR 005 option A: runs as late as possible, each setup right before its run, machine sequence fixed first; needs --scenario-output')
    stages.add_argument('--task-modes', action='store_true',
                        help='Honour per-task mode forward/backward/auto (auto: due_distance rule); backward tasks get ADR 005 option A; needs --scenario-output')
    stages.add_argument('--replay', metavar='REPORT',
                        help='Reproduce a previous setup-stages report on the exported scenario: same sequence and setup releases, exact comparison of the bookings, new audit')
    stages.add_argument('--partial', action='store_true',
                        help='ADR 006 option B: plan what has candidate resources and report the rest as not planned (NO_CANDIDATE, PREDECESSOR_UNPLANNED, NEST_MEMBER_UNPLANNED); may add --backward')
    stages.add_argument('--no-candidate', default='',
                        help='With --partial: comma-separated operation IDs to treat as having no candidate resource')
    stages.add_argument('--html', help='Offline HTML report of the stage plan (Gantt, gaps, unplanned phases, audit)')
    stages.add_argument('--scenario-output', help='Scenario JSON with the derived run releases of --backward, --just-in-time-tail or --task-modes')
    stages.add_argument('--restarts', type=int, default=0,
                        help='With --objective (forward search): extra deterministic perturbed starts, 0..8; also tries the state-grouped start')
    stages.add_argument('--seed', type=int, default=0, help='Seed of the perturbed starts')
    stages.add_argument('--budget', type=int, default=24, help='Sequence evaluations, 1..256')
    stages.add_argument('--max-alternatives', type=int, default=32)
    staged = sub.add_parser('staged-plan', help='Material gates + setup/run stages (+ optional backward) from a v0.3 JSON spec')
    staged.add_argument('scenario', help='Scenario JSON; the spec must name its exact SHA-256 digest')
    staged.add_argument('spec', help='v0.3 staged specification JSON (docs/v03-staged-file-preview.md)')
    staged.add_argument('-o', '--output', required=True, help='Report JSON: materials, sequence, bookings, KPIs, audit')
    staged.add_argument('--html', help='Offline HTML report (Gantt, gaps, audit)')
    staged.add_argument('--scenario-output', help='Scenario JSON after the material gates and any backward releases')
    setups = sub.add_parser('audit-setups', help='Audit explicit sequence-changeover rules against a plan')
    setups.add_argument('scenario')
    setups.add_argument('plan')
    setups.add_argument('rules')
    setups.add_argument('-o', '--output', help='Optional machine-readable audit JSON')
    explain = sub.add_parser('explain', help='Constraint chain per unit and finite-capacity critical path per order')
    explain.add_argument('scenario')
    explain.add_argument('plan')
    explain.add_argument('--manifest', help='Build manifest: units with material pegs are labelled material')
    explain.add_argument('-o', '--output', help='Write the analysis JSON to a file')
    fence = sub.add_parser('freeze-window', help='Time fence: keep published units starting inside the window')
    fence.add_argument('scenario', help='New scenario revision')
    fence.add_argument('published', help='Published plan of the previous revision')
    fence.add_argument('-o', '--output', required=True, help='Scenario with frozen units')
    fence.add_argument('--until', help='End of the window (ISO 8601 with offset)')
    fence.add_argument('--hours', type=float, help='Window length in working hours of --calendar')
    fence.add_argument('--calendar', help='Calendar used to count --hours')
    advance = sub.add_parser('nesting-advance', help='P13: nest members worked long before they are needed')
    advance.add_argument('scenario')
    advance.add_argument('plan')
    advance.add_argument('--max-advance-hours', type=float, default=72)
    stable = sub.add_parser('stability', help='P18: how much a recomputed plan moves the published one')
    stable.add_argument('published')
    stable.add_argument('recomputed')
    stable.add_argument('--threshold-minutes', type=int, default=0)
    stable.add_argument('--window-end', help='Only operations published to start before this instant')
    waits = sub.add_parser('waits', help='P6: FS waits longer than the declared max_lag_seconds')
    waits.add_argument('scenario')
    waits.add_argument('plan')
    enforce = sub.add_parser('enforce-waits', help='P6 hard: postpone predecessors to respect max_lag_seconds')
    enforce.add_argument('scenario')
    enforce.add_argument('-o', '--output', required=True, help='Repaired plan')
    enforce.add_argument('--scenario-out', required=True, help='Scenario with the new releases (the plan refers to it)')
    enforce.add_argument('--max-rounds', type=int, default=10)
    overtime = sub.add_parser('overtime', help='P19: evaluate candidate extra working windows in simulation')
    overtime.add_argument('scenario')
    overtime.add_argument('candidates', help='JSON list of {resource_id, start, end}')
    project = sub.add_parser('export-msproject', help='Write a verified plan as MS Project XML (MSPDI)')
    project.add_argument('scenario')
    project.add_argument('plan')
    project.add_argument('-o', '--output', required=True)
    project.add_argument('--timezone', default='Europe/Rome', help='Local time zone of the Project file')
    project.add_argument('--title')
    diff = sub.add_parser('compare', help='Compare two plan snapshots')
    diff.add_argument('before')
    diff.add_argument('after')
    diff.add_argument('--html', help='Write an offline HTML comparison report')
    sub.add_parser('desktop', help='Open the local offline Tk scenario workbench')
    api = sub.add_parser('serve', help='Start the loopback-only development JSON API')
    api.add_argument('--port', type=int, default=8765)
    web = sub.add_parser('web', help='Start browser dashboard on loopback using an anonymized Scenario JSON')
    web.add_argument('--fixture', help='Trusted file-only Scenario path (default: examples/workshop.json)')
    web.add_argument('--port', type=int, default=8766)
    web.add_argument('--v03-spec', help='Opt-in audited file-only MRP/setup/WIP specification paired to Scenario digest')
    secure_web = sub.add_parser('secure-web', help='Authenticated loopback Control Room with snapshot revisions')
    secure_web.add_argument('--fixture', required=True)
    secure_web.add_argument('--credentials', help='Users file with token hashes (not needed with --local-user)')
    secure_web.add_argument('--local-user', nargs='?', const='', metavar='NOME',
                            help='Single user on this PC, no token, loopback only (default name: Windows user)')
    secure_web.add_argument('--env-file', help='Instance .env with AI keys (default: .env next to --store)')
    secure_web.add_argument('--store', required=True)
    secure_web.add_argument('--audit', required=True)
    secure_web.add_argument('--v03-spec')
    secure_web.add_argument('--port', type=int, default=8766)
    secure_web.add_argument('--secure-cookie', action='store_true', help='Require HTTPS for session cookies when using a private TLS proxy')
    mcp = sub.add_parser('mcp', help='Local MCP stdio read tools for a trusted file snapshot')
    mcp.add_argument('--fixture', required=True)
    canonical_cli.register(sub)
    workbook_cli.register(sub)
    service = sub.add_parser('service', help='Authenticated network service with roles and isolated workers')
    service.add_argument('--store', required=True, help='SQLite snapshot store')
    service.add_argument('--credentials', required=True, help='Users file with token hashes (outside the repository)')
    service.add_argument('--audit', required=True, help='Audit log (JSON lines)')
    service.add_argument('--host', default='127.0.0.1')
    service.add_argument('--port', type=int, default=8443)
    service.add_argument('--cert')
    service.add_argument('--key')
    service.add_argument('--behind-proxy', action='store_true', help='Plain HTTP behind a TLS reverse proxy')
    service.add_argument('--workers', type=int, default=2)
    service.add_argument('--job-timeout', type=float, default=120)
    service.add_argument('--memory-mb', type=int, help='Worker memory limit (POSIX only)')
    token = sub.add_parser('service-token', help='Create a user token: prints it once with its credentials entry')
    token.add_argument('--name', required=True)
    token.add_argument('--role', required=True, choices=('reparto', 'planner', 'admin'))
    args = parser.parse_args(argv)
    try:
        if args.command in workbook_cli.COMMANDS:
            return workbook_cli.run(args)
        if args.command in canonical_cli.COMMANDS:
            return canonical_cli.run(args)
        if args.command == 'desktop':
            from .adapters.desktop_ui import launch
            launch()
            return 0
        if args.command == 'service-token':
            from .adapters.service import new_token
            value, entry = new_token(args.name, args.role)
            print(json.dumps({'token': value, 'credentials_entry': entry}, ensure_ascii=False))
            return 0
        if args.command == 'service':
            from .adapters.service import Service, make_service_server
            svc = Service(args.store, args.credentials, args.audit, workers=args.workers, job_timeout=args.job_timeout,
                          memory_mb=args.memory_mb)
            with make_service_server(svc, args.host, args.port, certfile=args.cert, keyfile=args.key,
                                     behind_proxy=args.behind_proxy) as server:
                try:
                    server.serve_forever()
                except KeyboardInterrupt:
                    pass
            return 0
        if args.command == 'mcp':
            from .adapters.dashboard_api import DashboardState
            from .adapters.mcp_stdio import serve_stdio
            serve_stdio(DashboardState(Path(args.fixture)))
            return 0
        if args.command == 'secure-web':
            from .adapters.secure_dashboard import serve_secure_dashboard
            local_user = None
            if args.local_user is not None:
                import getpass
                local_user = args.local_user.strip() or getpass.getuser()
            elif not args.credentials:
                parser.error('secure-web requires --credentials or --local-user')
            serve_secure_dashboard(args.fixture, args.credentials, args.store, args.audit,
                                   staged_spec=args.v03_spec, port=args.port, secure_cookie=args.secure_cookie,
                                   local_user=local_user, env_file=args.env_file)
            return 0
        if args.command == 'web':
            from .adapters.dashboard_api import serve_dashboard, DEFAULT_FIXTURE
            serve_dashboard(args.fixture or DEFAULT_FIXTURE, args.port, staged_spec=args.v03_spec)
            return 0
        if args.command == 'serve':
            from .adapters.http_api import serve
            serve(args.port)
            return 0
        if args.command == 'stability':
            from .adapters.json_io import timestamp
            from .engine.diagnostics import stability
            result = stability(plan_loads(Path(args.published).read_text(encoding='utf-8')),
                               plan_loads(Path(args.recomputed).read_text(encoding='utf-8')),
                               threshold_seconds=args.threshold_minutes * 60,
                               window_end=timestamp(args.window_end) if args.window_end else None)
            print(dumps(result), end='')
            return 0
        if args.command == 'compare':
            old = plan_loads(Path(args.before).read_text(encoding='utf-8'))
            new = plan_loads(Path(args.after).read_text(encoding='utf-8'))
            if args.html:
                output = Path(args.html)
                if output.resolve() in (Path(args.before).resolve(), Path(args.after).resolve()):
                    raise ValidationError(['comparison HTML cannot overwrite either input plan'])
                output.write_text(html_comparison(old, new), encoding='utf-8')
            print(dumps(compare(old, new)), end='')
            return 0
        scenario = loads(Path(args.scenario).read_text(encoding='utf-8'))
        if args.command == 'setup-search':
            rules = load_setup_rules(Path(args.rules).read_text(encoding='utf-8'), scenario)
            result = search_setup_alternatives(scenario, rules, budget=args.budget, repair_budget=args.repair_budget,
                                               time_limit_seconds=args.time_limit)
            output_paths = [Path(args.output), Path(args.scenario_output)]
            protected = {Path(args.scenario).resolve(), Path(args.rules).resolve()}
            if len({p.resolve() for p in output_paths}) != len(output_paths) or any(p.resolve() in protected for p in output_paths):
                raise ValidationError(['setup-search outputs must be distinct and must not overwrite inputs'])
            Path(args.output).write_text(dumps(result.plan), encoding='utf-8')
            Path(args.scenario_output).write_text(dumps(result.scenario), encoding='utf-8')
            print(dumps({'candidates_evaluated': result.candidates_evaluated, 'budget': result.budget,
                         'winner_order': result.winner_order, 'rejected': result.rejected,
                         'setup_audit': result.setup_audit.to_dict(),
                         'time_limit_reached': result.time_limit_reached}), end='')
            return 0 if result.setup_audit.valid and not result.plan.unscheduled else 2
        if args.command == 'staged-plan':
            from .adapters.staged_file import calculate_staged_preview, load_staged_file
            from .engine.setup_capacity import audit_stage_capacity
            from .engine.setup_sequence_search import sequence_metrics
            outputs = [Path(p).resolve() for p in (args.output, args.html, args.scenario_output) if p]
            inputs = {Path(args.scenario).resolve(), Path(args.spec).resolve()}
            if len(set(outputs)) != len(outputs) or inputs & set(outputs):
                raise ValidationError(['staged-plan outputs must be distinct and must not overwrite inputs'])
            request = load_staged_file(Path(args.spec), scenario)
            result = calculate_staged_preview(scenario, request)   # fail-closed: material shortage, audit, ...
            sequence = result.machine_sequence or request.machine_sequence
            violations = ()
            if request.mode == 'normal':   # paused/holds plan on a normalized contract, audited inside
                violations = audit_stage_capacity(
                    request.setup_contract, result.material_scenario, sequence, result.staged.bookings,
                    frozen_bookings=request.frozen_bookings)
            try:
                kpis = sequence_metrics(result.material_scenario, result.staged).as_dict()
            except ValidationError:
                kpis = None
            report = {
                'mode': request.mode, 'sequence': list(sequence),
                'bookings': _bookings_json(result.staged.bookings), 'kpis': kpis,
                'audit_violations': list(violations), 'unplanned': [],
                'materials': {
                    'pegs': [{'task_id': p.task_id, 'item_id': p.item_id, 'supply_id': p.supply_id,
                              'quantity': str(p.quantity)} for p in result.allocation.pegs],
                    'shortages': [[task, item, str(quantity)] for task, item, quantity in result.allocation.shortages]},
                'erp_publishable': False}
            if result.backward is not None:
                report['backward'] = {
                    'option': 'A', 'delayed_runs': [{'phase_id': p, 'seconds': s} for p, s in result.backward.delayed_runs],
                    'setup_gaps_seconds': dict(result.backward.setup_gaps),
                    'setup_releases': {p: r.isoformat() for p, r in result.backward.setup_releases}}
            Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            if args.scenario_output:
                Path(args.scenario_output).write_text(dumps(result.material_scenario), encoding='utf-8')
            if args.html:
                _write_stage_html(args.html, report, protected=set(inputs) | {Path(args.output).resolve()})
            print(json.dumps({'mode': request.mode, 'sequence': report['sequence'], 'kpis': kpis,
                              'shortages': report['materials']['shortages'],
                              'audit_violations': report['audit_violations']}, sort_keys=True))
            return 0 if not violations else 2
        if args.command == 'setup-stages':
            from .domain.setup_contract import load_setup_contract
            from .engine.setup_capacity import audit_stage_capacity
            from .engine.setup_sequence_search import (
                default_machine_sequence, optimize_setup_sequence, sequence_metrics)
            from .engine.setup_stage_scheduler import schedule_setup_stages
            if Path(args.output).resolve() in {Path(args.scenario).resolve(), Path(args.contract).resolve()}:
                raise ValidationError(['setup-stages output must not overwrite its inputs'])
            contract = load_setup_contract(Path(args.contract).read_text(encoding='utf-8'))
            if args.partial:
                from .engine.setup_partial import plan_setup_stages_partial
                if any((args.replay, args.just_in_time_tail, args.task_modes, args.objective)):
                    raise ValidationError(['--partial can only be combined with --backward, --sequence and --no-candidate'])
                if args.backward:
                    if not args.scenario_output:
                        raise ValidationError(['--backward requires --scenario-output'])
                    if Path(args.scenario_output).resolve() in {Path(args.scenario).resolve(), Path(args.contract).resolve(), Path(args.output).resolve()}:
                        raise ValidationError(['--scenario-output must be a distinct new file'])
                given = tuple(x for x in args.sequence.split(',') if x) if args.sequence else None
                extra = tuple(x for x in args.no_candidate.split(',') if x)
                part = plan_setup_stages_partial(scenario, contract, given, extra_without_candidates=extra,
                                                 backward=args.backward, max_alternatives=args.max_alternatives)
                report = {'unplanned': [{'operation_id': u.operation_id, 'code': u.code, 'detail': u.detail}
                                        for u in part.unplanned],
                          'sequence': list(part.machine_sequence), 'bookings': [], 'kpis': None,
                          'audit_violations': []}
                if part.staged is not None:
                    violations = audit_stage_capacity(part.contract, part.scenario, part.machine_sequence,
                                                      part.staged.bookings)
                    report.update(bookings=_bookings_json(part.staged.bookings),
                                  audit_violations=list(violations))
                    try:
                        report['kpis'] = sequence_metrics(part.scenario, part.staged).as_dict()
                    except ValidationError:
                        report['kpis'] = None  # some orders have no planned task: KPIs not defined
                    if part.backward is not None:
                        report['backward'] = {'option': 'A', 'delayed_runs': [
                            {'phase_id': p, 'seconds': s} for p, s in part.backward.delayed_runs],
                            'setup_gaps_seconds': dict(part.backward.setup_gaps),
                            'setup_releases': {p: r.isoformat() for p, r in part.backward.setup_releases}}
                        Path(args.scenario_output).write_text(dumps(part.scenario), encoding='utf-8')
                Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
                if args.html:
                    _write_stage_html(args.html, report, protected={Path(p).resolve() for p in (
                        args.scenario, args.contract, args.output, args.scenario_output, args.replay) if p})
                print(json.dumps({'planned': sorted({b['phase_id'] for b in report['bookings']}),
                                  'unplanned': report['unplanned'],
                                  'audit_violations': report['audit_violations']}, sort_keys=True))
                return 0 if not report['audit_violations'] else 2
            if args.replay:
                from datetime import datetime
                if any((args.backward, args.just_in_time_tail, args.task_modes, args.objective, args.sequence)):
                    raise ValidationError(['--replay cannot be combined with other planning options'])
                previous = json.loads(Path(args.replay).read_text(encoding='utf-8'))
                if (not isinstance(previous, dict) or not isinstance(previous.get('sequence'), list)
                        or not isinstance(previous.get('bookings'), list)
                        or not isinstance(previous.get('backward', {}), dict)):
                    raise ValidationError(['--replay needs a report written by setup-stages '
                                           '(with sequence and bookings)'])
                if Path(args.output).resolve() == Path(args.replay).resolve():
                    raise ValidationError(['--replay output must not overwrite the report being replayed'])
                sequence = tuple(previous['sequence'])
                releases = {pid: datetime.fromisoformat(v) for pid, v in
                            previous.get('backward', {}).get('setup_releases', {}).items()}
                solution = schedule_setup_stages(scenario, contract, sequence,
                                                 max_alternatives=args.max_alternatives,
                                                 setup_releases=releases)
                violations = audit_stage_capacity(contract, scenario, sequence, solution.bookings)
                matches = _bookings_json(solution.bookings) == previous['bookings']
                report = {'sequence': list(sequence), 'bookings': _bookings_json(solution.bookings),
                          'kpis': sequence_metrics(scenario, solution).as_dict(),
                          'audit_violations': list(violations),
                          'replay': {'matches_previous_bookings': matches, 'setup_releases': len(releases)}}
                Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
                if args.html:
                    _write_stage_html(args.html, report, protected={Path(p).resolve() for p in (
                        args.scenario, args.contract, args.output, args.scenario_output, args.replay) if p})
                print(json.dumps({'replay_matches': matches, 'audit_violations': report['audit_violations']}, sort_keys=True))
                return 0 if matches and not violations else 2
            sequence = (tuple(x for x in args.sequence.split(',') if x)
                        if args.sequence else default_machine_sequence(scenario))
            if args.start == 'best' and not args.sequence:
                if not args.objective:
                    raise ValidationError(['--start best requires --objective'])
                from .engine.setup_sequence_search import best_start_sequence
                sequence = best_start_sequence(scenario, contract, objective=args.objective,
                                               max_alternatives=args.max_alternatives)
            if args.start == 'state-grouped' and not args.sequence:
                from .engine.setup_sequence_search import state_grouped_sequence
                try:
                    candidate = state_grouped_sequence(scenario, contract, sequence)
                    schedule_setup_stages(scenario, contract, candidate,
                                          max_alternatives=args.max_alternatives)
                    sequence = candidate
                except ValidationError:
                    pass  # infeasible grouping: keep the due-date order
            if args.restarts and not args.objective:
                raise ValidationError(['--restarts requires --objective'])
            if args.restarts and (args.task_modes or args.partial):
                raise ValidationError(['--restarts is only available for the sequence search '
                                       '(not with --task-modes or --partial)'])
            search = None
            backward_search = None
            if args.objective and args.backward:
                from .engine.setup_sequence_search import optimize_backward_sequence
                if args.restarts:
                    from .engine.setup_sequence_search import optimize_backward_sequence_multistart
                    multi = optimize_backward_sequence_multistart(
                        scenario, contract, objective=args.objective, restarts=args.restarts, seed=args.seed,
                        max_evaluations=args.budget, max_alternatives=args.max_alternatives,
                        neighbourhood=args.neighbourhood)
                    backward_search = multi.best
                else:
                    backward_search = optimize_backward_sequence(
                        scenario, contract, sequence, objective=args.objective, max_evaluations=args.budget,
                        max_alternatives=args.max_alternatives, neighbourhood=args.neighbourhood)
                sequence = backward_search.sequence
                solution = backward_search.result.solution
            elif args.objective and args.restarts:
                from .engine.setup_sequence_search import optimize_setup_sequence_multistart
                multi = optimize_setup_sequence_multistart(
                    scenario, contract, objective=args.objective, restarts=args.restarts, seed=args.seed,
                    max_evaluations=args.budget, max_alternatives=args.max_alternatives,
                    neighbourhood=args.neighbourhood)
                search = multi.best
                solution, sequence = search.winner, search.sequence
            elif args.objective:
                search = optimize_setup_sequence(
                    scenario, contract, sequence, objective=args.objective, max_evaluations=args.budget,
                    max_alternatives=args.max_alternatives, neighbourhood=args.neighbourhood)
                solution, sequence = search.winner, search.sequence
            elif not args.task_modes:
                solution = schedule_setup_stages(scenario, contract, sequence,
                                                 max_alternatives=args.max_alternatives)
            audit_scenario, jit, back, modes = scenario, None, None, None
            if args.task_modes:
                from .engine.setup_sequence_search import plan_setup_stages_by_mode
                if args.backward or args.just_in_time_tail or args.objective:
                    raise ValidationError(['--task-modes cannot be combined with --backward, '
                                           '--just-in-time-tail or --objective'])
                if not args.scenario_output:
                    raise ValidationError(['--task-modes requires --scenario-output'])
                protected = {Path(args.scenario).resolve(), Path(args.contract).resolve(), Path(args.output).resolve()}
                if Path(args.scenario_output).resolve() in protected:
                    raise ValidationError(['--scenario-output must be a distinct new file'])
                aware = plan_setup_stages_by_mode(scenario, contract, sequence,
                                                  max_alternatives=args.max_alternatives)
                back, modes = aware.result, dict(aware.modes)
                solution, audit_scenario = back.solution, back.scenario
                Path(args.scenario_output).write_text(dumps(audit_scenario), encoding='utf-8')
            if args.backward:
                from .engine.setup_sequence_search import backward_setup_before_run
                if args.just_in_time_tail:
                    raise ValidationError(['--backward already includes --just-in-time-tail: use one of them'])
                if not args.scenario_output:
                    raise ValidationError(['--backward requires --scenario-output'])
                protected = {Path(args.scenario).resolve(), Path(args.contract).resolve(), Path(args.output).resolve()}
                if Path(args.scenario_output).resolve() in protected:
                    raise ValidationError(['--scenario-output must be a distinct new file'])
                back = (backward_search.result if backward_search is not None else
                        backward_setup_before_run(scenario, contract, sequence,
                                                  max_alternatives=args.max_alternatives))
                solution, audit_scenario = back.solution, back.scenario
                Path(args.scenario_output).write_text(dumps(audit_scenario), encoding='utf-8')
            if args.just_in_time_tail:
                from .engine.setup_sequence_search import just_in_time_tail
                if not args.scenario_output:
                    raise ValidationError(['--just-in-time-tail requires --scenario-output'])
                protected = {Path(args.scenario).resolve(), Path(args.contract).resolve(), Path(args.output).resolve()}
                if Path(args.scenario_output).resolve() in protected:
                    raise ValidationError(['--scenario-output must be a distinct new file'])
                jit = just_in_time_tail(scenario, contract, sequence, max_alternatives=args.max_alternatives)
                solution, audit_scenario = jit.solution, jit.scenario
                Path(args.scenario_output).write_text(dumps(audit_scenario), encoding='utf-8')
            violations = audit_stage_capacity(contract, audit_scenario, sequence, solution.bookings)
            report = {
                'sequence': list(sequence),
                'bookings': _bookings_json(solution.bookings),
                'kpis': sequence_metrics(audit_scenario, solution).as_dict(),
                'audit_violations': list(violations),
            }
            if modes is not None:
                report['task_modes'] = modes
            if back is not None:
                report['backward'] = {
                    'option': 'A', 'delayed_runs': [{'phase_id': p, 'seconds': s} for p, s in back.delayed_runs],
                    'setup_gaps_seconds': {p: g for p, g in back.setup_gaps},
                    'setup_releases': {p: r.isoformat() for p, r in back.setup_releases},
                    'baseline': back.baseline.as_dict()}
            if jit is not None:
                report['just_in_time_tail'] = {
                    'experimental': True, 'delayed': [{'phase_id': p, 'seconds': s} for p, s in jit.delayed],
                    'rejected': list(jit.rejected), 'baseline': jit.baseline.as_dict()}
            if backward_search is not None:
                report['search'] = {'objective': backward_search.objective, 'evaluated': backward_search.evaluated,
                                    'rejected': backward_search.rejected, 'improved': backward_search.improved,
                                    'evaluated_on': 'backward plan',
                                    'baseline': backward_search.baseline.as_dict(),
                                    'baseline_gap_seconds': backward_search.baseline_gap_seconds,
                                    'selected_gap_seconds': backward_search.selected_gap_seconds}
            if args.restarts and (search is not None or backward_search is not None):
                report['multistart'] = {'starts': len(multi.starts), 'best_start_index': multi.best_start_index,
                                        'evaluated': multi.evaluated, 'rejected_starts': multi.rejected_starts,
                                        'seed': args.seed}
            if search is not None:
                report['search'] = {'objective': search.objective, 'evaluated': search.evaluated,
                                    'rejected': search.rejected, 'improved': search.improved,
                                    'baseline': search.baseline.as_dict()}
            Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
            if args.html:
                _write_stage_html(args.html, report, protected={Path(p).resolve() for p in (
                    args.scenario, args.contract, args.output, args.scenario_output, args.replay) if p})
            print(json.dumps({'sequence': report['sequence'], 'kpis': report['kpis'],
                              'audit_violations': report['audit_violations']}, sort_keys=True))
            return 0 if not violations else 2
        if args.command == 'setup-plan':
            rules = load_setup_rules(Path(args.rules).read_text(encoding='utf-8'), scenario)
            result = plan_with_setups(scenario, rules, budget=args.budget)
            output_paths = [Path(args.output), Path(args.scenario_output)] + [Path(p) for p in (args.html, args.csv) if p]
            protected = {Path(args.scenario).resolve(), Path(args.rules).resolve()}
            if len({p.resolve() for p in output_paths}) != len(output_paths) or any(p.resolve() in protected for p in output_paths):
                raise ValidationError(['setup-plan outputs must be distinct and must not overwrite inputs'])
            Path(args.output).write_text(dumps(result.plan), encoding='utf-8')
            Path(args.scenario_output).write_text(dumps(result.scenario), encoding='utf-8')
            if args.html:
                Path(args.html).write_text(html_report(result.scenario, result.plan), encoding='utf-8')
            if args.csv:
                Path(args.csv).write_text(csv_report(result.plan), encoding='utf-8')
            print(dumps({'iterations': result.iterations, 'budget': result.budget,
                          'scenario_digest': result.plan.scenario_digest,
                          'setup_audit': result.setup_audit.to_dict()}), end='')
            return 0 if result.setup_audit.valid and not result.plan.unscheduled else 2
        if args.command == 'audit-setups':
            p = plan_loads(Path(args.plan).read_text(encoding='utf-8'))
            rules = load_setup_rules(Path(args.rules).read_text(encoding='utf-8'), scenario)
            report = audit_setups(scenario, p, rules)
            payload = dumps(report.to_dict())
            if args.output:
                dest = Path(args.output).resolve()
                if dest in {Path(path).resolve() for path in (args.scenario, args.plan, args.rules)}:
                    raise ValidationError(['setup audit report cannot overwrite its input files'])
                Path(args.output).write_text(payload, encoding='utf-8')
            print(payload, end='')
            return 0 if report.valid else 2
        if args.command == 'explain':
            from .engine.constraint_chain import constraint_chain
            reasons = {}
            if args.manifest:
                manifest = json.loads(Path(args.manifest).read_text(encoding='utf-8'))
                reasons = {peg['unit']: 'material' for peg in manifest.get('materials', {}).get('pegs', [])}
            result = constraint_chain(scenario, plan_loads(Path(args.plan).read_text(encoding='utf-8')), reasons)
            payload = dumps(result)
            if args.output:
                if Path(args.output).resolve() in {Path(p).resolve() for p in (args.scenario, args.plan, args.manifest) if p}:
                    raise ValidationError(['explain output cannot overwrite its inputs'])
                Path(args.output).write_text(payload, encoding='utf-8')
            print(payload, end='')
            return 0
        if args.command == 'freeze-window':
            from .adapters.json_io import timestamp
            from .engine.time_fence import freeze_window, working_until
            if (args.until is None) == (args.hours is None) or (args.hours is not None and not args.calendar):
                raise ValidationError(['give either --until or --hours with --calendar'])
            if Path(args.output).resolve() in (Path(args.scenario).resolve(), Path(args.published).resolve()):
                raise ValidationError(['frozen scenario cannot overwrite its inputs'])
            until = timestamp(args.until) if args.until else working_until(scenario, args.calendar, scenario.start, args.hours)
            result = freeze_window(scenario, plan_loads(Path(args.published).read_text(encoding='utf-8')), until)
            Path(args.output).write_text(dumps(result.scenario), encoding='utf-8')
            print(dumps(result.to_dict()), end='')
            return 0
        if args.command == 'nesting-advance':
            from .engine.diagnostics import nesting_advance
            result = nesting_advance(scenario, plan_loads(Path(args.plan).read_text(encoding='utf-8')),
                                     int(args.max_advance_hours * 3600))
            print(dumps(result), end='')
            return 0
        if args.command == 'waits':
            from .engine.diagnostics import waits_over_limit
            result = waits_over_limit(scenario, plan_loads(Path(args.plan).read_text(encoding='utf-8')))
            print(dumps(result), end='')
            return 0
        if args.command == 'enforce-waits':
            from .engine.max_lag import enforce_max_lag
            outputs = {Path(args.output).resolve(), Path(args.scenario_out).resolve()}
            if len(outputs) < 2 or Path(args.scenario).resolve() in outputs:
                raise ValidationError(['enforce-waits outputs must differ from each other and from the scenario'])
            result = enforce_max_lag(scenario, max_rounds=args.max_rounds)
            Path(args.scenario_out).write_text(dumps(result.scenario), encoding='utf-8')
            Path(args.output).write_text(dumps(result.plan), encoding='utf-8')
            print(dumps(result.to_dict()), end='')
            return 0
        if args.command == 'overtime':
            from .engine.diagnostics import overtime_candidates
            print(dumps(overtime_candidates(scenario, json.loads(Path(args.candidates).read_text(encoding='utf-8')))), end='')
            return 0
        if args.command == 'export-msproject':
            from .adapters.msproject import mspdi
            if Path(args.output).resolve() in (Path(args.scenario).resolve(), Path(args.plan).resolve()):
                raise ValidationError(['MS Project XML cannot overwrite its inputs'])
            p = plan_loads(Path(args.plan).read_text(encoding='utf-8'))
            errors = verify_plan(scenario, p)
            if errors:
                raise ValidationError(list(errors))
            Path(args.output).write_text(mspdi(scenario, p, args.timezone, args.title), encoding='utf-8')
            print(json.dumps({'tasks': len(p.assignments) + len(p.unscheduled), 'scenario_digest': p.scenario_digest}))
            return 0
        if args.command == 'validate':
            from .engine.calendars import expand
            for c in scenario.calendars:
                expand(c, scenario.start, scenario.end)
            print(json.dumps({'valid': True, 'scenario_digest': scenario_digest(scenario)}))
            return 0
        if args.command == 'verify':
            errors = verify_plan(scenario, plan_loads(Path(args.plan).read_text(encoding='utf-8')))
            print(json.dumps({'valid': not errors, 'errors': errors}))
            return 1 if errors else 0
        profile = {}
        if args.command in ('optimize', 'local-search'):
            if args.objectives:
                profile['objectives'] = tuple(x.strip() for x in args.objectives.split(','))
            if args.published:
                profile['published'] = plan_loads(Path(args.published).read_text(encoding='utf-8'))
        if args.command == 'optimize' and args.policies:
            from .engine.optimization import ALL_POLICIES
            profile['policies'] = ALL_POLICIES if args.policies == 'all' else tuple(
                x.strip() for x in args.policies.split(','))
        optimized = optimize(scenario, time_limit_seconds=args.time_limit, **profile) if args.command == 'optimize' else None
        searched = local_search(scenario, budget=args.budget, time_limit_seconds=args.time_limit, **profile) \
            if args.command == 'local-search' else None
        result = searched.plan if searched else (optimized.plan if optimized else
                                                 plan(scenario, record_constraints=getattr(args, 'record_constraints', False)))
        paths = [Path(args.output)] + [Path(p) for p in (args.html, args.csv, getattr(args, 'metrics', None)) if p]
        if len({p.resolve() for p in paths}) != len(paths) or any(p.resolve() == Path(args.scenario).resolve() for p in paths):
            raise ValidationError(['output paths must be distinct and must not overwrite the scenario'])
        Path(args.output).write_text(dumps(result), encoding='utf-8')
        if args.html:
            Path(args.html).write_text(html_report(scenario, result), encoding='utf-8')
        if args.csv:
            Path(args.csv).write_text(csv_report(result), encoding='utf-8')
        if optimized and args.metrics:
            Path(args.metrics).write_text(dumps(optimized), encoding='utf-8')
        if searched and args.metrics:
            Path(args.metrics).write_text(dumps(searched), encoding='utf-8')
        summary = {'assigned': len(result.assignments), 'unscheduled': len(result.unscheduled),
                   'scenario_digest': result.scenario_digest}
        if optimized:
            summary['winner_policy'] = optimized.winner_policy
            summary['candidates_evaluated'] = len(optimized.candidates)
            summary['pareto_frontier'] = list(optimized.pareto_frontier)
            summary['rejected'] = list(optimized.rejected)
            summary['time_limit_reached'] = optimized.time_limit_reached
        if searched:
            summary['improved'] = searched.improved
            summary['evaluated_neighbors'] = searched.evaluated_neighbors
            summary['accepted_improvements'] = searched.accepted_improvements
            summary['time_limit_reached'] = searched.time_limit_reached
        print(json.dumps(summary))
        return 2 if result.unscheduled else 0
    except (ValidationError, OSError, KeyError, TypeError, ValueError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
