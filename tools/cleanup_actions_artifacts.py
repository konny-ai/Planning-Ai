#!/usr/bin/env python3
"""Inventory and optionally delete old GitHub Actions artifacts, not releases.

Uses the user's existing gh auth identity, never payment APIs. Only two
Planning Factory repositories are in scope; default invocation changes nothing.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys

REPOS = ("konny-ai/planning-factory", "repository privato dei dati di test")
PROTECTED_WORDS = ("backup", "snapshot", "bancoa", "database", "release")


def parse_pages(text: str) -> list[dict]:
    decoder, items, pos = json.JSONDecoder(), [], 0
    while pos < len(text):
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if pos >= len(text):
            break
        page, pos = decoder.raw_decode(text, pos)
        if not isinstance(page, dict) or not isinstance(page.get("artifacts"), list):
            raise ValueError("unexpected Actions artifacts API response")
        items.extend(page["artifacts"])
    return items


def plan_cleanup(artifacts: list[dict], *, today: datetime, older_than_days: int,
                 keep_latest: int = 2) -> tuple[list[dict], list[dict]]:
    if older_than_days < 1 or keep_latest < 1:
        raise ValueError("retain at least one artifact and require minimum age >= 1 day")
    grouped: dict[str, list[dict]] = {}
    for a in artifacts:
        grouped.setdefault(str(a["name"]), []).append(a)
    to_delete, preserved = [], []
    cutoff = today - timedelta(days=older_than_days)
    for name, group in grouped.items():
        by_newest = sorted(group, key=lambda a: (a["created_at"], a["id"]), reverse=True)
        for position, art in enumerate(by_newest):
            created = datetime.fromisoformat(art["created_at"].replace("Z", "+00:00"))
            safe = (position >= keep_latest and created < cutoff and
                    not any(word in name.lower() for word in PROTECTED_WORDS))
            (to_delete if safe else preserved).append(art)
    return to_delete, preserved


def gh(*args: str) -> str:
    p = subprocess.run(("gh", *args), capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f"gh {args[0]} failed (exit {p.returncode}): {p.stderr[:400]}")
    return p.stdout


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--older-than", type=int, default=14)
    parser.add_argument("--execute", action="store_true", help="actually DELETE qualifying Actions artifacts")
    parser.add_argument("--yes", action="store_true", help="explicitly confirm deletion")
    args = parser.parse_args(argv)
    if args.execute and not args.yes:
        parser.error("deletion requires --execute --yes")
    if args.older_than < 1:
        parser.error("--older-than must be >= 1")
    try:
        gh("auth", "status")
        total = 0
        now = datetime.now(timezone.utc)
        for repo in REPOS:
            raw = gh("api", "--paginate", f"/repos/{repo}/actions/artifacts?per_page=100")
            artifacts = parse_pages(raw)
            to_delete, preserved = plan_cleanup(artifacts, today=now,
                                                older_than_days=args.older_than)
            bytes_to_delete = sum(a.get("size_in_bytes", 0) for a in to_delete)
            print(f"{repo}: {len(artifacts)} artifacts; {len(to_delete)} eligible; "
                  f"{len(preserved)} preserved; {bytes_to_delete / 1048576:.1f} MiB to free")
            for a in to_delete:
                print(f"  {'DELETE' if args.execute else 'DRY-RUN'} #{a['id']}: {a['name']}")
                if args.execute:
                    gh("api", "-X", "DELETE", f"/repos/{repo}/actions/artifacts/{a['id']}")
                    total += 1
        print(f"Deleted: {total}. Backups, releases, database snapshots, and the latest 2 of each name are protected.")
        return 0
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        print(f"Cleanup stopped: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
