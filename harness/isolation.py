"""Per-build workspace isolation.

Two modes:
  - "directory": build in a plain (ephemeral) directory. Default; right for
    greenfield apps.
  - "worktree": build in a dedicated `git worktree` checked out from a base
    repo, on its own branch, so the agent can never clobber your main working
    tree. Right for modifying an existing repository in isolation.

`workspace_session(...)` is a context manager yielding the *effective* workspace
path the build should use.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from typing import Iterator


class IsolationError(RuntimeError):
    pass


def _git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True
    )


def is_git_repo(path: str) -> bool:
    return _git(["rev-parse", "--is-inside-work-tree"], cwd=path).returncode == 0


def create_worktree(base_repo: str, path: str, branch: str | None) -> None:
    if not is_git_repo(base_repo):
        raise IsolationError(f"not a git repository: {base_repo}")
    if os.path.exists(path) and os.listdir(path):
        raise IsolationError(f"worktree path must be empty/nonexistent: {path}")
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)

    if branch:
        res = _git(["worktree", "add", "-b", branch, os.path.abspath(path), "HEAD"], cwd=base_repo)
        if res.returncode == 0:
            return
        # Branch may already exist; fall through to detached checkout.
    res = _git(["worktree", "add", "--detach", os.path.abspath(path), "HEAD"], cwd=base_repo)
    if res.returncode != 0:
        raise IsolationError(f"git worktree add failed: {res.stderr.strip()}")


def remove_worktree(base_repo: str, path: str) -> None:
    _git(["worktree", "remove", "--force", os.path.abspath(path)], cwd=base_repo)
    # Best-effort: if git left anything behind, remove the dir.
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)


@contextmanager
def workspace_session(
    mode: str,
    workspace: str,
    *,
    base_repo: str | None = None,
    branch: str | None = None,
    keep: bool = True,
) -> Iterator[str]:
    """Yield the effective workspace path for the build.

    directory: ensures `workspace` exists; never auto-deleted (keep the build).
    worktree:  creates a git worktree at `workspace` from `base_repo`; removed
               on exit unless `keep` is True.
    """
    workspace = os.path.abspath(workspace)

    if mode == "directory":
        os.makedirs(workspace, exist_ok=True)
        yield workspace
        return

    if mode == "worktree":
        repo = os.path.abspath(base_repo or os.getcwd())
        create_worktree(repo, workspace, branch)
        try:
            yield workspace
        finally:
            if not keep:
                remove_worktree(repo, workspace)
        return

    raise IsolationError(f"unknown isolation mode: {mode!r} (expected 'directory' or 'worktree')")
