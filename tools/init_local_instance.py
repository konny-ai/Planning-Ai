"""Create a NEW private loopback instance from explicitly selected JSON files.

The token is returned once to the caller, never persisted in clear text.
No SQL, shell, provider login or production deployment is performed.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

from planning_factory.adapters.dashboard_api import DashboardState
from planning_factory.adapters.service import new_token
from planning_factory.adapters.store import SnapshotStore
from planning_factory.adapters.rule_memory import RuleMemory
from planning_factory.adapters.json_io import dumps
from planning_factory.domain.validation import ValidationError


def initialize(destination, fixture, stage_spec=None, *, user='planner1'):
    state = DashboardState(Path(fixture), staged_spec=Path(stage_spec) if stage_spec else None)
    if state.file_binding:
        raise ValidationError(['initializer accepts Scenario JSON only; run secure-web against the original canonical binding to preserve its provenance'])
    # Fail before creating anything if data or the optional paired specification
    # cannot be independently planned. The technical checks do not certify inputs.
    state.bootstrap()
    if stage_spec:
        state.staged_preview()
    folder = Path(destination).resolve()
    folder.mkdir(mode=0o700)  # no exist_ok: never reuse or replace an installation
    try:
        os.chmod(folder, 0o700)
        token, entry = new_token(user, 'planner')
        (folder / 'scenario.json').write_text(dumps(state.source), encoding='utf-8')
        if stage_spec:
            shutil.copyfile(stage_spec, folder / 'stage-spec.json')
        (folder / 'users.json').write_text(json.dumps({'users': [entry]}, indent=2) + '\n', encoding='utf-8')
        (folder / 'audit.jsonl').touch()
        SnapshotStore(folder / 'plans.sqlite')
        RuleMemory(folder / 'plans.sqlite')
        for path in folder.iterdir():
            os.chmod(path, 0o600)
        (folder / 'installation.json').write_text(json.dumps({
            'format_version': 1, 'fixture_kind': 'materialized_scenario',
            'source_provenance_archived': False, 'source_binding_used': state.file_binding,
            'production_approved': False, 'llm_configured': False}, indent=2) + '\n', encoding='utf-8')
        os.chmod(folder / 'installation.json', 0o600)
        return folder, token
    except BaseException:
        # Only the new directory owned by this invocation can be removed.
        shutil.rmtree(folder)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path, help='new instance directory')
    parser.add_argument('--fixture', required=True, type=Path)
    parser.add_argument('--stage-spec', type=Path)
    parser.add_argument('--user', default='planner1')
    args = parser.parse_args(argv)
    folder, token = initialize(args.out, args.fixture, args.stage_spec, user=args.user)
    print('Instance:', folder)
    print('Personal token (shown once; store privately):', token)
    command = [sys.executable, '-m', 'planning_factory', 'secure-web', '--fixture', str(folder / 'scenario.json'),
               '--credentials', str(folder / 'users.json'), '--store', str(folder / 'plans.sqlite'),
               '--audit', str(folder / 'audit.jsonl')]
    if args.stage_spec:
        command += ['--v03-spec', str(folder / 'stage-spec.json')]
    print('Start command arguments (JSON; no token):', json.dumps(command))
    print('Loopback technical instance; real CLIENTE acceptance remains required.')


if __name__ == '__main__':
    main()
