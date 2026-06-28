"""Tests for project scaffolds: registry, materialization, and that the two
zero-dependency scaffolds actually boot + pass their server-backed checks."""

from __future__ import annotations

import os

from harness import fullstack, scaffolds
from harness.verifier import Check


def test_registry_lists_expected_scaffolds():
    names = {s["name"] for s in scaffolds.list_scaffolds()}
    assert {"static", "python-api", "python-db", "vite-react", "fastapi"} <= names
    for s in scaffolds.list_scaffolds():
        assert s["run"] and s["label"] and s["kind"]


def test_apply_writes_files_without_overwriting(tmp_path):
    sc = scaffolds.apply("static", str(tmp_path))
    assert sc and sc.name == "static"
    for rel in ("index.html", "styles.css", "app.js"):
        assert (tmp_path / rel).is_file()
    # don't clobber edits
    (tmp_path / "app.js").write_text("// edited")
    scaffolds.apply("static", str(tmp_path))
    assert (tmp_path / "app.js").read_text() == "// edited"


def test_apply_nested_paths(tmp_path):
    scaffolds.apply("python-api", str(tmp_path))
    assert (tmp_path / "server.py").is_file()
    assert (tmp_path / "public" / "index.html").is_file()
    assert (tmp_path / "public" / "app.js").is_file()


def test_apply_unknown_returns_none(tmp_path):
    assert scaffolds.apply("nope", str(tmp_path)) is None


def _run_scaffold_checks(name: str, tmp_path) -> bool:
    sc = scaffolds.apply(name, str(tmp_path))
    checks = [Check(name=c["name"], command=c["command"], cwd=str(tmp_path),
                    needs_server=bool(c.get("needs_server")),
                    allow_failure=bool(c.get("allow_failure"))) for c in sc.checks]
    rep = fullstack.verify_checks(checks, str(tmp_path), run_command=sc.run, stop_on_failure=True)
    return rep.ok, {r.name: (r.ok, r.skipped) for r in rep.results}


def test_static_scaffold_boots_and_passes(tmp_path):
    ok, results = _run_scaffold_checks("static", tmp_path)
    assert ok, results
    assert results["app responds"][0] is True  # server booted, smoke passed


def test_python_api_scaffold_boots_and_serves_api(tmp_path):
    ok, results = _run_scaffold_checks("python-api", tmp_path)
    assert ok, results
    assert results["api health"][0] is True   # /api/health responded 200
    assert results["app responds"][0] is True  # static frontend served


def test_python_db_scaffold_migrates_boots_and_persists(tmp_path):
    ok, results = _run_scaffold_checks("python-db", tmp_path)
    assert ok, results
    assert results["migrations apply"][0] is True  # migrate.py ran against the DB
    assert results["api health"][0] is True
    assert results["notes api"][0] is True         # POST /api/notes wrote a row
    # The data layer is real: migration created the table + tracking, POST persisted.
    import sqlite3
    db = tmp_path / "app.db"
    assert db.is_file()
    conn = sqlite3.connect(str(db))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"notes", "schema_migrations"} <= tables
    assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] >= 1
    conn.close()


def test_python_db_generates_env_with_secret(tmp_path):
    # ensure_env (called inside verify_checks) materialises .env from .env.example.
    scaffolds.apply("python-db", str(tmp_path))
    from harness import env as envmod
    env = envmod.ensure_env(str(tmp_path))
    assert env["DATABASE_URL"] == "app.db"
    assert env["SECRET_KEY"] != "changeme" and len(env["SECRET_KEY"]) >= 32
