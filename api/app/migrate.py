"""Apply /db/migrations/*.sql in order, once each, and remember which ran.

    python -m app.migrate              # apply anything new (safe to run on every deploy; concurrent runs queue up)
    python -m app.migrate --baseline   # a database that was set up BY HAND from these files: record them as applied

Each file runs in its own transaction: a failing migration changes nothing and is not recorded."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

DEFAULT_DIR = Path(__file__).resolve().parents[2] / "db" / "migrations"
LOCK_KEY = 727_272_001


class MigrationError(Exception):
    pass


def migrate(dsn: str, directory: Path | None = None, *, baseline: bool = False) -> list[str]:
    """Returns the filenames applied (or, with baseline, recorded) by this call."""
    directory = directory or DEFAULT_DIR
    files = sorted(directory.glob("*.sql"))
    if not files:
        raise MigrationError(f"no migration files found in {directory}")
    applied_now: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("select pg_advisory_lock(%s)", [LOCK_KEY])           # two containers starting at once: one waits
        try:
            conn.execute("create table if not exists schema_migrations (filename text primary key, applied_at timestamptz not null default now())")
            done = {r[0] for r in conn.execute("select filename from schema_migrations").fetchall()}
            if not done and conn.execute("select to_regclass('public.clinic') is not null").fetchone()[0]:
                if not baseline:
                    raise MigrationError("This database already has ShowUp tables but no migration history. If it was set up by hand from "
                                         "db/migrations, run `python -m app.migrate --baseline` once to record that, then migrate normally.")
                for f in files:
                    conn.execute("insert into schema_migrations (filename) values (%s)", [f.name])
                    applied_now.append(f.name)
                return applied_now
            if baseline and done:
                raise MigrationError("--baseline is only for a database with no migration history.")
            for f in files:
                if f.name in done:
                    continue
                with conn.transaction():
                    conn.execute(f.read_text(encoding="utf-8"))
                    conn.execute("insert into schema_migrations (filename) values (%s)", [f.name])
                applied_now.append(f.name)
        finally:
            conn.execute("select pg_advisory_unlock(%s)", [LOCK_KEY])
    return applied_now


def main(argv: list[str] | None = None) -> int:
    from .config import get_settings
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--baseline", action="store_true")
    args = ap.parse_args(argv)
    try:
        ran = migrate(get_settings().database_url, baseline=args.baseline)
    except MigrationError as e:
        print(f"migrate: {e}", file=sys.stderr)
        return 1
    print(("recorded as applied: " if args.baseline else "applied: ") + (", ".join(ran) if ran else "nothing to do (up to date)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
