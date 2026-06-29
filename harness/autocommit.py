"""Git auto-commit: every green build/iteration becomes a commit, for free undo.

Vibe-coding without version control is risky — one bad iteration can clobber a
working app with no way back. This commits the workspace to a local git repo
after each *passing* turn, on a dedicated history branch, so you get a timeline
and a one-click undo without ever thinking about git.

It only manages repos it created (marked with a sentinel) and refuses to touch a
pre-existing repo the user owns, so it never rewrites someone's real history. The
internal .studio version store and secrets (.env) are kept out of the commits.
Stdlib only (shells out to git); the git runner is injectable for tests.
"""

from __future__ import annotations

import os
import subprocess

SENTINEL = os.path.join(".studio", ".autocommit")  # marks a lathe-managed repo
BRANCH = "lathe-history"
_IGNORE = [".studio/", ".env", "__pycache__/", "*.pyc", "node_modules/", ".venv/",
           "venv/", ".DS_Store", "*.sqlite", "*.db"]


def _git(workspace: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=workspace, capture_output=True, text=True)


def is_git_repo(workspace: str) -> bool:
    return os.path.isdir(os.path.join(workspace, ".git"))


def manages(workspace: str) -> bool:
    """True if this is a repo Lathe created (safe to auto-commit to)."""
    return os.path.isfile(os.path.join(workspace, SENTINEL))


def _ensure_gitignore(workspace: str) -> None:
    path = os.path.join(workspace, ".gitignore")
    existing = ""
    if os.path.isfile(path):
        existing = open(path, encoding="utf-8", errors="replace").read()
    missing = [p for p in _IGNORE if p not in existing.split()]
    if missing:
        with open(path, "a", encoding="utf-8") as fh:
            if existing and not existing.endswith("\n"):
                fh.write("\n")
            fh.write("\n".join(missing) + "\n")


def ensure_repo(workspace: str) -> dict:
    """Init a managed repo if needed. Refuse to manage a pre-existing foreign repo."""
    if is_git_repo(workspace) and not manages(workspace):
        return {"ok": False, "reason": "workspace is an existing git repo (not auto-committing)"}
    if not is_git_repo(workspace):
        init = _git(workspace, "init", "-q")
        if init.returncode != 0:
            return {"ok": False, "error": (init.stderr or "git init failed").strip()}
        _git(workspace, "config", "user.email", "lathe@localhost")
        _git(workspace, "config", "user.name", "Lathe")
        _git(workspace, "checkout", "-q", "-B", BRANCH)
        os.makedirs(os.path.join(workspace, ".studio"), exist_ok=True)
        with open(os.path.join(workspace, SENTINEL), "w") as fh:
            fh.write("lathe auto-commit\n")
        _ensure_gitignore(workspace)
    return {"ok": True}


def commit_all(workspace: str, message: str) -> dict:
    """Stage everything and commit. Returns {ok, sha?, nochange?, skipped?/reason?}."""
    res = ensure_repo(workspace)
    if not res.get("ok"):
        return {"ok": False, "skipped": True, "reason": res.get("reason") or res.get("error")}
    _git(workspace, "add", "-A")
    status = _git(workspace, "status", "--porcelain")
    if not status.stdout.strip():
        return {"ok": True, "nochange": True}
    commit = _git(workspace, "commit", "-q", "-m", message)
    if commit.returncode != 0:
        return {"ok": False, "error": (commit.stderr or "commit failed").strip()}
    sha = _git(workspace, "rev-parse", "--short", "HEAD").stdout.strip()
    return {"ok": True, "sha": sha, "message": message}


def history(workspace: str, limit: int = 30) -> list[dict]:
    if not is_git_repo(workspace):
        return []
    out = _git(workspace, "log", f"-{limit}", "--pretty=%h%x09%s%x09%cr")
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            rows.append({"sha": parts[0], "message": parts[1],
                         "when": parts[2] if len(parts) > 2 else ""})
    return rows


def undo_last(workspace: str) -> dict:
    """Safely undo the most recent commit by reverting it (keeps history intact)."""
    if not (is_git_repo(workspace) and manages(workspace)):
        return {"ok": False, "reason": "not a lathe-managed repo"}
    count = _git(workspace, "rev-list", "--count", "HEAD").stdout.strip()
    if count.isdigit() and int(count) < 2:
        return {"ok": False, "reason": "nothing to undo (only the initial commit)"}
    rev = _git(workspace, "revert", "--no-edit", "HEAD")
    if rev.returncode != 0:
        return {"ok": False, "error": (rev.stderr or "revert failed").strip()}
    sha = _git(workspace, "rev-parse", "--short", "HEAD").stdout.strip()
    return {"ok": True, "sha": sha}


def restore(workspace: str, sha: str) -> dict:
    """Restore the working tree to a past commit's files, then commit (non-destructive)."""
    if not (is_git_repo(workspace) and manages(workspace)):
        return {"ok": False, "reason": "not a lathe-managed repo"}
    if not sha or not sha.isalnum():
        return {"ok": False, "reason": "invalid commit id"}
    co = _git(workspace, "checkout", sha, "--", ".")
    if co.returncode != 0:
        return {"ok": False, "error": (co.stderr or "checkout failed").strip()}
    return commit_all(workspace, f"Restore to {sha}")
