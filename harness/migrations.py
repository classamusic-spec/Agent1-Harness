"""Stdlib SQLite migration runner — apply ordered `.sql` files idempotently.

Migrations live as `NNN_name.sql` files in a directory; they apply in filename
order, and each applied file is recorded in a `schema_migrations` table so a
re-run is a no-op (this is what makes a build's `migrate` check deterministic and
repeatable). `seed()` runs a one-off SQL script. No third-party ORM — sqlite3 from
the stdlib keeps the data layer dependency-free and runnable here and offline.
"""

from __future__ import annotations

import glob
import os
import sqlite3

_TRACK_TABLE = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
)
"""


def migration_files(migrations_dir: str) -> list[str]:
    """Return the migration `.sql` files in apply order (sorted by filename)."""
    if not os.path.isdir(migrations_dir):
        return []
    return sorted(glob.glob(os.path.join(migrations_dir, "*.sql")))


def applied_migrations(conn: sqlite3.Connection) -> set[str]:
    conn.execute(_TRACK_TABLE)
    return {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}


def apply_migrations(db_path: str, migrations_dir: str) -> list[str]:
    """Apply any not-yet-applied migrations, in order. Returns the names applied
    this run (empty if already up to date). Each file runs in its own transaction;
    a failure rolls that file back and aborts (later files are not applied)."""
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    applied: list[str] = []
    try:
        done = applied_migrations(conn)
        for path in migration_files(migrations_dir):
            name = os.path.basename(path)
            if name in done:
                continue
            sql = open(path, encoding="utf-8").read()
            try:
                conn.executescript(sql)
                conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (name,))
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            applied.append(name)
    finally:
        conn.close()
    return applied


def seed(db_path: str, seed_path: str) -> bool:
    """Run a seed SQL script against the database. Returns False if missing."""
    if not os.path.isfile(seed_path):
        return False
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(open(seed_path, encoding="utf-8").read())
        conn.commit()
    finally:
        conn.close()
    return True


def _main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(description="Apply SQLite migrations.")
    ap.add_argument("--db", default=os.environ.get("DATABASE_URL", "app.db"),
                    help="SQLite database path (default: $DATABASE_URL or app.db)")
    ap.add_argument("--dir", default="migrations", help="migrations directory")
    ap.add_argument("--seed", default=None, help="optional seed .sql to run after migrating")
    args = ap.parse_args(argv)

    applied = apply_migrations(args.db, args.dir)
    if applied:
        print(f"applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("database is up to date")
    if args.seed:
        print("seeded" if seed(args.db, args.seed) else f"no seed file at {args.seed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
