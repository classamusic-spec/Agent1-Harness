"""Offline tests for workspace isolation."""

from __future__ import annotations

import os
import subprocess

import pytest

from harness.isolation import IsolationError, is_git_repo, workspace_session


def test_directory_mode_creates_and_keeps(tmp_path):
    ws = tmp_path / "build"
    with workspace_session("directory", str(ws)) as eff:
        assert os.path.isdir(eff)
        (tmp_path / "build" / "f.txt").write_text("x")
    assert (ws / "f.txt").exists()  # not cleaned up


def _init_repo(path):
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "t"], check=True)
    (path / "base.txt").write_text("from base")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(path), "commit", "-qm", "init"], check=True)


def test_worktree_mode_checks_out_and_cleans_up(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    wt = tmp_path / "wt"

    with workspace_session("worktree", str(wt), base_repo=str(repo), keep=False) as eff:
        assert (os.path.join(eff, "base.txt")) and os.path.isfile(os.path.join(eff, "base.txt"))
        listed = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True
        ).stdout
        assert str(wt) in listed
    # Cleaned up.
    after = subprocess.run(
        ["git", "-C", str(repo), "worktree", "list"], capture_output=True, text=True
    ).stdout
    assert str(wt) not in after


def test_worktree_requires_git_repo(tmp_path):
    not_repo = tmp_path / "plain"
    not_repo.mkdir()
    assert not is_git_repo(str(not_repo))
    with pytest.raises(IsolationError):
        with workspace_session("worktree", str(tmp_path / "wt"), base_repo=str(not_repo)):
            pass


def test_unknown_mode(tmp_path):
    with pytest.raises(IsolationError):
        with workspace_session("nope", str(tmp_path / "x")):
            pass
