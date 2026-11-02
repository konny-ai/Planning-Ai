"""Rebuild of the canonical source from the ERP with another planning start date (administrators only).

The command belongs to the installation and is written by its administrator in ``regenerate.json`` next to
the store; neither the browser nor the assistant can supply it. It is an argv list where ``{start}`` is
replaced by the requested date (YYYY-MM-DD), with optional ``cwd`` and ``timeout_seconds``::

    {"command": ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                 "C:/.../integrations/cliente-export/run_cliente_locale.ps1", "-Start", "{start}"],
     "timeout_seconds": 1800}

One rebuild at a time, without a shell; its output goes to ``regenerate.log``. When the command ends with
exit code 0 the Control Room re-reads binding and files (``on_done``); otherwise the live data stay as they
were and the status reports the failure.
"""
from datetime import UTC, date, datetime, timedelta
import json
from pathlib import Path
import subprocess
import threading

from planning_factory.domain.validation import ValidationError

CONFIG_FILE = 'regenerate.json'
LOG_FILE = 'regenerate.log'
MAX_TIMEOUT = 4 * 3600


def _now():
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace('+00:00', 'Z')


class Regeneration:
    def __init__(self, command, log_path, on_done, *, cwd=None, timeout_seconds=1800):
        if (not isinstance(command, list) or not command or not all(isinstance(a, str) and a for a in command)
                or sum('{start}' in a for a in command) != 1):
            raise ValueError(f'{CONFIG_FILE}: command must be a list of strings with exactly one {{start}}')
        if type(timeout_seconds) is not int or not 60 <= timeout_seconds <= MAX_TIMEOUT:
            raise ValueError(f'{CONFIG_FILE}: timeout_seconds 60..{MAX_TIMEOUT}')
        self.command, self.cwd, self.timeout = command, cwd, timeout_seconds
        self.log_path, self.on_done = Path(log_path), on_done
        self.lock = threading.Lock()
        self.job = {'state': 'idle'}

    @classmethod
    def load(cls, folder, on_done):
        """The installation's configuration, or None when this instance cannot rebuild its source."""
        path = Path(folder) / CONFIG_FILE
        if not path.is_file():
            return None
        raw = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(raw, dict) or set(raw) - {'command', 'cwd', 'timeout_seconds'}:
            raise ValueError(f'{CONFIG_FILE}: command, cwd, timeout_seconds only')
        return cls(raw.get('command'), Path(folder) / LOG_FILE, on_done, cwd=raw.get('cwd'),
                   timeout_seconds=raw.get('timeout_seconds', 1800))

    def status(self):
        with self.lock:
            job = dict(self.job)
        if job['state'] != 'idle':
            try:
                lines = self.log_path.read_text(encoding='utf-8', errors='replace').splitlines()
            except OSError:
                lines = []
            job['log_tail'] = [line[:300] for line in lines[-12:]]
        return job

    def start(self, start, actor):
        try:
            day = date.fromisoformat(start)
        except (TypeError, ValueError):
            raise ValidationError(['start: data YYYY-MM-DD']) from None
        if not date(2000, 1, 1) <= day <= datetime.now(UTC).date() + timedelta(days=730):
            raise ValidationError(['start: data fuori intervallo'])
        with self.lock:
            if self.job['state'] == 'running':
                raise ValidationError(['una rigenerazione è già in corso'])
            self.job = {'state': 'running', 'start': day.isoformat(), 'actor': actor, 'started_at': _now()}
        threading.Thread(target=self._run, args=(day.isoformat(),), daemon=True).start()
        return self.status()

    def _run(self, start):
        argv = [a.replace('{start}', start) for a in self.command]
        outcome = {}
        try:
            with self.log_path.open('w', encoding='utf-8') as log:
                process = subprocess.Popen(argv, cwd=self.cwd, stdout=log, stderr=subprocess.STDOUT,
                                           stdin=subprocess.DEVNULL)
                try:
                    code = process.wait(timeout=self.timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    code = None
            if code == 0:
                self.on_done()
                outcome = {'state': 'done', 'exit_code': 0}
            else:
                outcome = {'state': 'failed', 'exit_code': code,
                           'error': 'tempo massimo superato' if code is None else f'comando terminato con codice {code}'}
        except Exception as exc:  # the live data stay; the failure is reported, never raised in a thread
            outcome = {'state': 'failed', 'error': str(exc)[:500]}
        with self.lock:
            self.job.update(outcome, ended_at=_now())
