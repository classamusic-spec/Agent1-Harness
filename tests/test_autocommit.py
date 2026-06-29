"""Tests for git auto-commit per green iteration (uses real git)."""

from __future__ import annotations

import shutil

import pytest

from harness import autocommit

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def test_commit_all_inits_managed_repo_and_commits(tmp_path):
    (tmp_path / "app.py").write_text("print('v1')\n")
    res = autocommit.commit_all(str(tmp_path), "Initial build: app")
    assert res["ok"] and res.get("sha")
    assert autocommit.is_git_repo(str(tmp_path)) and autocommit.manages(str(tmp_path))
    hist = autocommit.history(str(tmp_path))
    assert hist[0]["message"] == "Initial build: app"


def test_gitignore_excludes_studio_and_env(tmp_path):
    (tmp_path / "app.py").write_text("x=1\n")
    (tmp_path / ".env").write_text("SECRET=shh\n")
    (tmp_path / ".studio").mkdir()
    (tmp_path / ".studio" / "junk.json").write_text("{}")
    autocommit.commit_all(str(tmp_path), "build")
    tracked = autocommit._git(str(tmp_path), "ls-files").stdout.split()
    assert "app.py" in tracked
    assert ".env" not in tracked
    assert not any(t.startswith(".studio/") for t in tracked)


def test_nochange_commit_is_noop(tmp_path):
    (tmp_path / "a.txt").write_text("hi\n")
    autocommit.commit_all(str(tmp_path), "first")
    res = autocommit.commit_all(str(tmp_path), "again")   # nothing changed
    assert res["ok"] and res.get("nochange")


def test_refuses_foreign_repo(tmp_path):
    autocommit._git(str(tmp_path), "init", "-q")    # a repo WE didn't mark
    (tmp_path / "f.txt").write_text("x\n")
    res = autocommit.commit_all(str(tmp_path), "nope")
    assert not res["ok"] and res.get("skipped")
    assert "existing git repo" in res["reason"]


def test_undo_last_reverts(tmp_path):
    (tmp_path / "app.py").write_text("v1\n")
    autocommit.commit_all(str(tmp_path), "v1")
    (tmp_path / "app.py").write_text("v2-broken\n")
    autocommit.commit_all(str(tmp_path), "v2")
    res = autocommit.undo_last(str(tmp_path))
    assert res["ok"]
    assert (tmp_path / "app.py").read_text() == "v1\n"     # back to v1 contents
    assert len(autocommit.history(str(tmp_path))) == 3      # revert is a new commit


def test_undo_refuses_with_only_initial_commit(tmp_path):
    (tmp_path / "a.txt").write_text("x\n")
    autocommit.commit_all(str(tmp_path), "only")
    res = autocommit.undo_last(str(tmp_path))
    assert not res["ok"] and "nothing to undo" in res["reason"]


def test_restore_to_past_commit(tmp_path):
    (tmp_path / "app.py").write_text("one\n")
    first = autocommit.commit_all(str(tmp_path), "one")["sha"]
    (tmp_path / "app.py").write_text("two\n")
    autocommit.commit_all(str(tmp_path), "two")
    res = autocommit.restore(str(tmp_path), first)
    assert res["ok"]
    assert (tmp_path / "app.py").read_text() == "one\n"


def test_history_empty_for_non_repo(tmp_path):
    assert autocommit.history(str(tmp_path)) == []
