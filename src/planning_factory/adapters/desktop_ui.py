"""Offline desktop JSON workbench. Tk is loaded only when explicitly launched.

This is a local scenario editor, not an authenticated ERP UI.
"""
from pathlib import Path
from planning_factory.adapters.json_io import dumps, loads, scenario_digest
from planning_factory.adapters.reports import csv_report, html_report
from planning_factory.domain.validation import ValidationError
from planning_factory.engine.calendars import expand
from planning_factory.engine.local_search import local_search
from planning_factory.engine.optimization import optimize
from planning_factory.engine.scheduler import plan
from planning_factory.engine.verification import verify_plan
from planning_factory.engine.setup_audit import load_setup_rules
from planning_factory.engine.setup_planner import plan_with_setups
from planning_factory.engine.setup_search import search_setup_alternatives

MAX_EDITOR_BYTES = 8 * 1024 * 1024


def evaluate(source: str, method: str = 'schedule', budget: int = 8):
    """Pure, headless-testable UI controller: returns (scenario, plan, details)."""
    if len(source.encode('utf-8')) > MAX_EDITOR_BYTES:
        raise ValidationError(['scenario text exceeds the 8 MiB editor limit'])
    s = loads(source)
    for c in s.calendars:
        expand(c, s.start, s.end)
    if method == 'validate':
        return s, None, {'valid': True, 'scenario_digest': scenario_digest(s)}
    if method == 'schedule':
        p = plan(s)
        details = {'method': 'schedule'}
    elif method == 'optimize':
        result = optimize(s)
        p = result.plan
        details = {'method': method, 'winner_policy': result.winner_policy,
                   'candidates': len(result.candidates), 'rejected': result.rejected}
    elif method == 'local-search':
        result = local_search(s, budget=budget)
        p = result.plan
        details = {'method': method, 'budget': budget, 'improved': result.improved,
                   'evaluated_neighbors': result.evaluated_neighbors,
                   'accepted_improvements': result.accepted_improvements}
    else:
        raise ValidationError([f'unknown desktop action: {method}'])
    errors = verify_plan(s, p)
    if errors:
        raise RuntimeError('desktop controller received unverified plan: ' + '; '.join(errors))
    details.update(assigned=len(p.assignments), unscheduled=len(p.unscheduled),
                   scenario_digest=p.scenario_digest)
    return s, p, details


def evaluate_setup(source: str, rules_text: str, budget: int = 12):
    """Headless controller for explicit setup-aware planning."""
    if len(source.encode('utf-8')) > MAX_EDITOR_BYTES or len(rules_text.encode('utf-8')) > MAX_EDITOR_BYTES:
        raise ValidationError(['scenario or setup rules exceed the 8 MiB editor limit'])
    s = loads(source)
    rules = load_setup_rules(rules_text, s)
    result = plan_with_setups(s, rules, budget=budget)
    errors = verify_plan(result.scenario, result.plan)
    if errors:
        raise RuntimeError('setup-aware desktop plan failed verification: ' + '; '.join(errors))
    return result.scenario, result.plan, {'method': 'setup-plan', 'iterations': result.iterations,
                                         'budget': result.budget, 'setup_valid': result.setup_audit.valid,
                                         'assigned': len(result.plan.assignments),
                                         'unscheduled': len(result.plan.unscheduled),
                                         'scenario_digest': result.plan.scenario_digest}


def evaluate_setup_search(source: str, rules_text: str, budget: int = 8, repair_budget: int = 12):
    """Headless controller for bounded alternative-machine search under setup rules."""
    if len(source.encode('utf-8')) > MAX_EDITOR_BYTES or len(rules_text.encode('utf-8')) > MAX_EDITOR_BYTES:
        raise ValidationError(['scenario or setup rules exceed the 8 MiB editor limit'])
    s = loads(source)
    rules = load_setup_rules(rules_text, s)
    result = search_setup_alternatives(s, rules, budget=budget, repair_budget=repair_budget)
    errors = verify_plan(result.scenario, result.plan)
    if errors or not result.setup_audit.valid:
        raise RuntimeError('setup search returned a plan that failed independent audit')
    return result.scenario, result.plan, {'method': 'setup-search', 'budget': result.budget,
                                         'candidates_evaluated': result.candidates_evaluated,
                                         'winner_order': result.winner_order, 'rejected': result.rejected,
                                         'setup_valid': result.setup_audit.valid,
                                         'assigned': len(result.plan.assignments),
                                         'unscheduled': len(result.plan.unscheduled),
                                         'scenario_digest': result.plan.scenario_digest}


def require_current_plan(source: str, planned_scenario, current_plan) -> None:
    """Prevent exporting a result after unsaved edits changed its scenario."""
    if current_plan is None or planned_scenario is None:
        raise ValidationError(['calculate a plan before exporting'])
    current = loads(source)
    if scenario_digest(current) != current_plan.scenario_digest:
        raise ValidationError(['scenario changed since planning: recalculate before exporting'])
    errors = verify_plan(current, current_plan)
    if errors:
        raise ValidationError(['plan verification failed before export: ' + '; '.join(errors)])


def export(source: str, planned_scenario, current_plan, format: str) -> str:
    require_current_plan(source, planned_scenario, current_plan)
    if format == 'json':
        return dumps(current_plan)
    if format == 'html':
        return html_report(planned_scenario, current_plan)
    if format == 'csv':
        return csv_report(current_plan)
    raise ValidationError([f'unknown desktop export format: {format}'])


def launch() -> None:
    """Interactive desktop, never binds a socket or accesses an ERP database."""
    try:
        import tkinter as tk
        from tkinter import filedialog, messagebox, ttk
    except ImportError as exc:
        raise ValidationError(['Tkinter is not installed on this Python interpreter']) from exc
    try:
        root = tk.Tk()
    except tk.TclError as exc:
        raise ValidationError(['desktop requires a graphical display']) from exc
    root.title('Planning Factory · Workbench offline')
    root.geometry('1040x760')
    state = {'scenario': None, 'plan': None}
    outer = ttk.Frame(root, padding=12)
    outer.pack(fill='both', expand=True)
    ttk.Label(outer, text='Scenario JSON · lavoro locale, nessun accesso ERP',
              font=('TkDefaultFont', 13, 'bold')).pack(anchor='w')
    toolbar = ttk.Frame(outer)
    toolbar.pack(fill='x', pady=8)
    editor_frame = ttk.Frame(outer)
    editor_frame.pack(fill='both', expand=True)
    editor = tk.Text(editor_frame, wrap='none', undo=True, font=('TkFixedFont', 10))
    yscroll = ttk.Scrollbar(editor_frame, orient='vertical', command=editor.yview)
    xscroll = ttk.Scrollbar(editor_frame, orient='horizontal', command=editor.xview)
    editor.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
    editor.grid(row=0, column=0, sticky='nsew')
    yscroll.grid(row=0, column=1, sticky='ns')
    xscroll.grid(row=1, column=0, sticky='ew')
    editor_frame.grid_rowconfigure(0, weight=1)
    editor_frame.grid_columnconfigure(0, weight=1)
    footer = ttk.Label(outer, text='Apri un file JSON o inserisci uno scenario manualmente.',
                       anchor='w')
    footer.pack(fill='x', pady=10)

    def source_text():
        return editor.get('1.0', 'end-1c')

    def fail(error):
        footer.configure(text='Errore: ' + str(error))
        messagebox.showerror('Planning Factory', str(error), parent=root)

    def load_file():
        name = filedialog.askopenfilename(parent=root, filetypes=[('JSON', '*.json'), ('Tutti', '*.*')])
        if not name:
            return
        try:
            file = Path(name)
            if file.stat().st_size > MAX_EDITOR_BYTES:
                raise ValidationError(['scenario file exceeds the 8 MiB editor limit'])
            text = file.read_text(encoding='utf-8')
            editor.delete('1.0', 'end')
            editor.insert('1.0', text)
            state.update(scenario=None, plan=None)
            footer.configure(text='Caricato: ' + str(file))
        except (OSError, ValueError) as exc:
            fail(exc)

    def save_scenario():
        name = filedialog.asksaveasfilename(parent=root, defaultextension='.json',
                                            filetypes=[('JSON', '*.json')])
        if not name:
            return
        try:
            # Save only structurally and semantically valid scenarios.
            s, _, _ = evaluate(source_text(), 'validate')
            Path(name).write_text(dumps(s), encoding='utf-8')
            footer.configure(text='Scenario salvato: ' + name)
        except (ValueError, OSError) as exc:
            fail(exc)

    def calculate(method):
        try:
            s, result, info = evaluate(source_text(), method)
            state.update(scenario=s, plan=result)
            if result is None:
                footer.configure(text='Scenario valido · ' + info['scenario_digest'][:12])
            else:
                note = (' · migliorato: ' + str(info['improved']) if method == 'local-search'
                        else ' · strategia: ' + str(info['winner_policy']) if method == 'optimize'
                        else '')
                footer.configure(text=f"Piano verificato · {info['assigned']} assegnate"
                                      f" · {info['unscheduled']} non collocate" + note)
        except (ValueError, RuntimeError, OSError) as exc:
            state.update(scenario=None, plan=None)
            fail(exc)

    def calculate_setup():
        name = filedialog.askopenfilename(parent=root, filetypes=[('Regole setup JSON', '*.json'), ('Tutti', '*.*')])
        if not name:
            return
        try:
            rules_text = Path(name).read_text(encoding='utf-8')
            adjusted, result, info = evaluate_setup(source_text(), rules_text)
            editor.delete('1.0', 'end')
            editor.insert('1.0', dumps(adjusted))
            state.update(scenario=adjusted, plan=result)
            footer.configure(text=f"Piano con setup verificato · {info['assigned']} assegnate · {info['unscheduled']} non collocate · iterazioni {info['iterations']}")
        except (ValueError, RuntimeError, OSError) as exc:
            state.update(scenario=None, plan=None)
            fail(exc)

    def calculate_setup_search():
        name = filedialog.askopenfilename(parent=root, filetypes=[('Regole setup JSON', '*.json'), ('Tutti', '*.*')])
        if not name:
            return
        try:
            rules_text = Path(name).read_text(encoding='utf-8')
            adjusted, result, info = evaluate_setup_search(source_text(), rules_text)
            editor.delete('1.0', 'end')
            editor.insert('1.0', dumps(adjusted))
            state.update(scenario=adjusted, plan=result)
            footer.configure(text=f"Ricerca setup verificata · {info['candidates_evaluated']} candidati · {info['assigned']} assegnate")
        except (ValueError, RuntimeError, OSError) as exc:
            state.update(scenario=None, plan=None)
            fail(exc)

    def save_output(format):
        extension = {'json': '.json', 'csv': '.csv', 'html': '.html'}[format]
        name = filedialog.asksaveasfilename(parent=root, defaultextension=extension,
                                            filetypes=[(format.upper(), '*' + extension)])
        if not name:
            return
        try:
            contents = export(source_text(), state['scenario'], state['plan'], format)
            Path(name).write_text(contents, encoding='utf-8')
            footer.configure(text='Report salvato: ' + name)
        except (ValueError, RuntimeError, OSError) as exc:
            fail(exc)

    for label, action in (
        ('Apri JSON', load_file), ('Salva scenario', save_scenario),
        ('Valida', lambda: calculate('validate')),
        ('Pianifica', lambda: calculate('schedule')),
        ('Ottimizza', lambda: calculate('optimize')),
        ('Ricerca locale', lambda: calculate('local-search')),
        ('Pianifica con setup', calculate_setup),
        ('Cerca alternative setup', calculate_setup_search),
    ):
        ttk.Button(toolbar, text=label, command=action).pack(side='left', padx=3)
    outputs = ttk.Frame(outer)
    outputs.pack(fill='x', before=footer)
    for label, kind in (('Salva piano JSON', 'json'), ('Esporta Gantt HTML', 'html'),
                        ('Esporta CSV', 'csv')):
        ttk.Button(outputs, text=label, command=lambda fmt=kind: save_output(fmt)).pack(side='left', padx=4)
    root.mainloop()
