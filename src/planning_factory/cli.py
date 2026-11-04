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
    stages.add_argument('--objective', choices=('delivery', 'setup', 'flow'),
                        help='Optional bounded sequence search for this explicit objective')
    stages.add_argument('--neighbourhood', choices=('swap', 'insertion'), default='swap')
    stages.add_argument('--just-in-time-tail', action='store_true',
                        help='EXPERIMENTAL (ADR 005, not approved): delay terminal runs by their delivery slack; needs --scenario-output')
    stages.add_argument('--backward', action='store_true',
                        help='ADR 005 option A: runs as late as possible, each setup right before its run, machine sequence fixed first; needs --scenario-output')
    stages.add_argument('--scenario-output', help='Scenario JSON with the derived releases of --just-in-time-tail')
    stages.add_argument('--budget', type=int, default=24, help='Sequence evaluations, 1..256')
    stages.add_argument('--max-alternatives', type=int, default=32)
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
        if args.command == 'setup-stages':
            from .domain.setup_contract import load_setup_contract
            from .engine.setup_capacity import audit_stage_capacity
            from .engine.setup_sequence_search import (
                default_machine_sequence, optimize_setup_sequence, sequence_metrics)
            from .engine.setup_stage_scheduler import schedule_setup_stages
            if Path(args.output).resolve() in {Path(args.scenario).resolve(), Path(args.contract).resolve()}:
                raise ValidationError(['setup-stages output must not overwrite its inputs'])
            contract = load_setup_contract(Path(args.contract).read_text(encoding='utf-8'))
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
            search = None
            if args.objective:
                search = optimize_setup_sequence(
                    scenario, contract, sequence, objective=args.objective, max_evaluations=args.budget,
                    max_alternatives=args.max_alternatives, neighbourhood=args.neighbourhood)
                solution, sequence = search.winner, search.sequence
            else:
                solution = schedule_setup_stages(scenario, contract, sequence,
                                                 max_alternatives=args.max_alternatives)
            audit_scenario, jit, back = scenario, None, None
            if args.backward:
                from .engine.setup_sequence_search import backward_setup_before_run
                if args.just_in_time_tail:
                    raise ValidationError(['--backward already includes --just-in-time-tail: use one of them'])
                if not args.scenario_output:
                    raise ValidationError(['--backward requires --scenario-output'])
                protected = {Path(args.scenario).resolve(), Path(args.contract).resolve(), Path(args.output).resolve()}
                if Path(args.scenario_output).resolve() in protected:
                    raise ValidationError(['--scenario-output must be a distinct new file'])
                back = backward_setup_before_run(scenario, contract, sequence,
                                                 max_alternatives=args.max_alternatives)
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
                'bookings': [{'phase_id': b.phase_id, 'stage': b.stage,
                              'segments': [{'start': i.start.isoformat(), 'end': i.end.isoformat()}
                                           for i in b.segments],
                              'allocations': [{'resource_id': a.resource_id, 'occupancy_bps': a.occupancy_bps}
                                              for a in b.allocations]} for b in solution.bookings],
                'kpis': sequence_metrics(audit_scenario, solution).as_dict(),
                'audit_violations': list(violations),
            }
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
            if search is not None:
                report['search'] = {'objective': search.objective, 'evaluated': search.evaluated,
                                    'rejected': search.rejected, 'improved': search.improved,
                                    'baseline': search.baseline.as_dict()}
            Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + '\n', encoding='utf-8')
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
