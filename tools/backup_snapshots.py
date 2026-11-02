"""Consistent SQLite backup to a NEW file. Never replaces or restores a live database."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


def backup(source: Path, destination: Path):
    source = Path(source).resolve(strict=True)
    destination = Path(destination).resolve()
    # Reserve exclusively before sqlite opens it. Existing evidence stays intact.
    with destination.open('xb'):
        pass
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=10)) as src:
            with closing(sqlite3.connect(destination)) as dst:
                src.backup(dst)
                if dst.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('backup integrity check failed')
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args(argv)
    print(backup(args.source, args.out))


if __name__ == '__main__':
    main()
