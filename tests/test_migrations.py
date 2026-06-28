"""Tests for the SQLite migration runner: ordered apply, idempotency, tracking,
seeding, and the CLI entrypoint."""

from __future__ import annotations

import sqlite3

from harness import migrations


def _write(migrations_dir, name, sql):
    (migrations_dir / name).write_text(sql)


def test_apply_migrations_in_order_and_tracks(tmp_path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    _write(mdir, "001_init.sql", "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);")
    _write(mdir, "002_add_col.sql", "ALTER TABLE notes ADD COLUMN created_at TEXT;")
    db = str(tmp_path / "app.db")

    applied = migrations.apply_migrations(db, str(mdir))
    assert applied == ["001_init.sql", "002_add_col.sql"]

    conn = sqlite3.connect(db)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(notes)")}
    assert {"id", "body", "created_at"} <= cols
    tracked = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    assert tracked == {"001_init.sql", "002_add_col.sql"}
    conn.close()


def test_apply_migrations_is_idempotent(tmp_path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    _write(mdir, "001_init.sql", "CREATE TABLE t (id INTEGER PRIMARY KEY);")
    db = str(tmp_path / "app.db")
    assert migrations.apply_migrations(db, str(mdir)) == ["001_init.sql"]
    # Second run applies nothing (and does not error re-creating the table).
    assert migrations.apply_migrations(db, str(mdir)) == []


def test_apply_only_new_migration(tmp_path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    _write(mdir, "001_init.sql", "CREATE TABLE t (id INTEGER PRIMARY KEY);")
    db = str(tmp_path / "app.db")
    migrations.apply_migrations(db, str(mdir))
    _write(mdir, "002_more.sql", "CREATE TABLE u (id INTEGER PRIMARY KEY);")
    assert migrations.apply_migrations(db, str(mdir)) == ["002_more.sql"]


def test_failed_migration_rolls_back_and_raises(tmp_path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    _write(mdir, "001_ok.sql", "CREATE TABLE t (id INTEGER PRIMARY KEY);")
    _write(mdir, "002_bad.sql", "THIS IS NOT SQL;")
    db = str(tmp_path / "app.db")
    try:
        migrations.apply_migrations(db, str(mdir))
        assert False, "expected an error"
    except Exception:
        pass
    conn = sqlite3.connect(db)
    tracked = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    assert tracked == {"001_ok.sql"}  # the good one stuck, the bad one did not
    conn.close()


def test_seed_runs_script(tmp_path):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    _write(mdir, "001_init.sql", "CREATE TABLE notes (id INTEGER PRIMARY KEY, body TEXT);")
    db = str(tmp_path / "app.db")
    migrations.apply_migrations(db, str(mdir))
    seed = tmp_path / "seed.sql"
    seed.write_text("INSERT INTO notes (body) VALUES ('hello');")
    assert migrations.seed(db, str(seed)) is True
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 1
    conn.close()
    assert migrations.seed(db, str(tmp_path / "missing.sql")) is False


def test_no_migrations_dir_is_empty(tmp_path):
    assert migrations.apply_migrations(str(tmp_path / "app.db"), str(tmp_path / "nope")) == []


def test_cli_applies_and_reports(tmp_path, capsys):
    mdir = tmp_path / "migrations"
    mdir.mkdir()
    _write(mdir, "001_init.sql", "CREATE TABLE t (id INTEGER PRIMARY KEY);")
    db = str(tmp_path / "app.db")
    rc = migrations._main(["--db", db, "--dir", str(mdir)])
    assert rc == 0
    assert "applied 1 migration" in capsys.readouterr().out
