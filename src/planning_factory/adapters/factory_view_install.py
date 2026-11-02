"""Creates the documented Factory views that are missing from a database.

The SQL lives in ``integrations/cliente-export/views`` (one file per view). Only
objects that do not exist yet are created: an existing view, even an older one,
is never replaced, because the customer may have changed it. The caller owns the
connection (autocommit for a real installation, a transaction for a rehearsal).
"""
from pathlib import Path
import re

from planning_factory.adapters import factory_views
from planning_factory.domain.validation import ValidationError

PERIMETER = 'dbo.X_CLIENTE_Planning_Perimetro'
_HEADER = re.compile(r'^[ \t]*CREATE[ \t]+OR[ \t]+ALTER[ \t]+VIEW[ \t]+[\w.\[\]]+', re.IGNORECASE | re.MULTILINE)


def sql_folder():
    folder = Path(__file__).resolve().parents[3] / 'integrations' / 'cliente-export' / 'views'
    if not folder.is_dir():
        raise ValidationError(['File SQL delle viste non trovati: usa l’installazione dal repository (avvia.bat).'])
    return folder


def _statement(file, name):
    """The documented CREATE OR ALTER becomes a plain CREATE under the configured name."""
    text = (sql_folder() / file).read_text(encoding='utf-8')
    text, count = _HEADER.subn('CREATE VIEW ' + factory_views.quoted_object(name), text, count=1)
    if count != 1:
        raise ValidationError([file + ': intestazione CREATE VIEW non trovata'])
    return text


def plan(views):
    """(label, configured name, SQL file) in dependency order: the perimeter first."""
    definitions = {v['key']: v for v in factory_views.contract()['views']}
    return [('perimetro', PERIMETER, '00_Perimetro.sql')] + [
        (key, views[key], definitions[key]['sql']) for key in definitions]


def _kind(cursor, name):
    cursor.execute('SELECT type FROM sys.objects WHERE object_id = OBJECT_ID(?)', factory_views.quoted_object(name))
    row = cursor.fetchone()
    return None if row is None else row[0].strip()


def explain(error):
    text = str(error)
    if '42S02' in text or 'Invalid object name' in text:
        return 'manca un oggetto Factory da cui dipende la vista (' + text.split('Invalid object name')[-1][:90].strip(" '.)") + ')'
    if '262' in text or '229' in text or 'permission' in text.lower():
        return 'l’utente SQL non ha il permesso CREATE VIEW sul database'
    return text[:400]


def install(connection, views):
    """Create every missing view; report created / already present / failed."""
    cursor = connection.cursor()
    created, present, failed = [], [], []
    todo = []
    for label, name, file in plan(views):
        kind = _kind(cursor, name)
        if kind == 'V':
            present.append(name)
        elif kind is not None:
            failed.append(dict(view=name, error='esiste già un oggetto con questo nome e non è una vista'))
        else:
            todo.append((label, name, file))
    # The perimeter is only worth creating when a requested view needs it.
    if all(label == 'perimetro' for label, _, _ in todo):
        todo = []
    for label, name, file in todo:
        try:
            cursor.execute(_statement(file, name))
            created.append(name)
        except ValidationError:
            raise
        except Exception as exc:  # one failed view must not hide the others
            failed.append(dict(view=name, error=explain(exc)))
    cursor.close()
    return dict(created=created, present=present, failed=failed)
