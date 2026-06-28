"""Tests for the .env / secrets layer: tolerant parsing, generation with a random
secret, and ensure_env materialising .env from .env.example."""

from __future__ import annotations

import os

from harness import env as envmod


def test_load_dotenv_is_tolerant(tmp_path):
    p = tmp_path / ".env"
    p.write_text(
        "# a comment\n"
        "\n"
        "DATABASE_URL=app.db\n"
        "export SECRET_KEY='abc123'\n"
        'QUOTED="with spaces"\n'
        "NOEQUALS\n"
        "EMPTY=\n"
    )
    env = envmod.load_dotenv(str(p))
    assert env["DATABASE_URL"] == "app.db"
    assert env["SECRET_KEY"] == "abc123"  # export + quotes stripped
    assert env["QUOTED"] == "with spaces"
    assert env["EMPTY"] == ""
    assert "NOEQUALS" not in env


def test_load_dotenv_missing_returns_empty(tmp_path):
    assert envmod.load_dotenv(str(tmp_path / "nope.env")) == {}


def test_generate_env_creates_secret_and_never_overwrites(tmp_path):
    p = tmp_path / ".env"
    env = envmod.generate_env(str(p), {"DATABASE_URL": "app.db"})
    assert env["DATABASE_URL"] == "app.db"
    assert len(env["SECRET_KEY"]) >= 32  # random hex
    assert p.is_file()
    # A second call must not overwrite the existing secret.
    again = envmod.generate_env(str(p), {"DATABASE_URL": "other.db"})
    assert again["SECRET_KEY"] == env["SECRET_KEY"]
    assert again["DATABASE_URL"] == "app.db"


def test_ensure_env_materialises_from_example_with_random_secret(tmp_path):
    (tmp_path / ".env.example").write_text("DATABASE_URL=app.db\nSECRET_KEY=changeme\n")
    env = envmod.ensure_env(str(tmp_path))
    assert env["DATABASE_URL"] == "app.db"
    assert env["SECRET_KEY"] != "changeme" and len(env["SECRET_KEY"]) >= 32
    assert (tmp_path / ".env").is_file()
    # Idempotent: existing .env wins, no regeneration.
    again = envmod.ensure_env(str(tmp_path))
    assert again["SECRET_KEY"] == env["SECRET_KEY"]


def test_ensure_env_no_example_is_noop(tmp_path):
    assert envmod.ensure_env(str(tmp_path)) == {}
    assert not (tmp_path / ".env").exists()


def test_merged_environ_layers_workspace_over_os(tmp_path):
    (tmp_path / ".env").write_text("DATABASE_URL=app.db\n")
    merged = envmod.merged_environ(str(tmp_path), {"EXTRA": "1"})
    assert merged["DATABASE_URL"] == "app.db"
    assert merged["EXTRA"] == "1"
    assert "PATH" in merged  # carried from os.environ
