"""Per-turn workspace snapshots + diffs for the Studio.

Each build/iterate turn snapshots the workspace so you can see exactly what a
change did (a unified diff) and roll back to any earlier version. Snapshots live
inside the workspace under `.studio/versions/<id>/` and are ignored by the file
tree, the gallery, and future snapshots.

Dependency-free (stdlib only) and unit-tested offline.
"""

from __future__ import annotations

import difflib
import json
import os
import shutil
import time
from pathlib import Path

VERSIONS_REL = os.path.join(".studio", "versions")
_SKIP_DIRS = {".studio", ".git", "node_modules", "__pycache__", ".venv", "venv",
              "dist", "build", ".next", ".cache", ".pytest_cache"}
_MARKERS = {".studio.json", ".appbuilder_checkpoint.json"}
_MAX_FILES = 300
_MAX_VERSIONS = 30


def _iter_files(root: Path):
    """Relative paths of tracked source files under root (skips build/marker noise)."""
    root = Path(root)
    if not root.is_dir():
        return
    count = 0
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for n in sorted(names):
            if n in _MARKERS:
                continue
            yield os.path.relpath(os.path.join(dirpath, n), root)
            count += 1
            if count >= _MAX_FILES:
                return


def _read_text(path: Path) -> str | None:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None


def _versions_root(workspace: str) -> Path:
    return Path(workspace) / VERSIONS_REL


def _tree(workspace: str, vid: int) -> Path:
    return _versions_root(workspace) / str(vid) / "tree"


def list_versions(workspace: str) -> list[dict]:
    """All snapshots, oldest first."""
    root = _versions_root(workspace)
    out: list[dict] = []
    if not root.is_dir():
        return out
    for p in root.iterdir():
        if p.is_dir() and p.name.isdigit() and (p / "meta.json").is_file():
            try:
                out.append(json.loads((p / "meta.json").read_text()))
            except (OSError, json.JSONDecodeError):
                continue
    out.sort(key=lambda m: m.get("id", 0))
    return out


def snapshot(workspace: str, label: str = "", instruction: str = "") -> dict:
    """Copy the workspace's source files into a new version. Returns the meta."""
    base = Path(workspace)
    root = _versions_root(workspace)
    root.mkdir(parents=True, exist_ok=True)
    existing = [int(p.name) for p in root.iterdir() if p.is_dir() and p.name.isdigit()]
    vid = (max(existing) + 1) if existing else 1
    tree = _tree(workspace, vid)
    files: list[str] = []
    for rel in _iter_files(base):
        src, dst = base / rel, tree / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
            files.append(rel)
        except OSError:
            continue
    meta = {
        "id": vid,
        "label": (label or "").strip(),
        "instruction": (instruction or "").strip(),
        "time": time.time(),
        "files": sorted(files),
    }
    (root / str(vid) / "meta.json").write_text(json.dumps(meta, indent=2))
    _prune(workspace)
    return meta


def _prune(workspace: str) -> None:
    versions = list_versions(workspace)
    excess = len(versions) - _MAX_VERSIONS
    for m in versions[:max(0, excess)]:
        shutil.rmtree(_versions_root(workspace) / str(m["id"]), ignore_errors=True)


def diff(workspace: str, frm: int, to: int | None = None) -> list[dict]:
    """Per-file unified diff from version `frm` to `to` (or the live workspace)."""
    a_root = _tree(workspace, frm)
    b_root = Path(workspace) if to is None else _tree(workspace, to)
    a_files = set(_iter_files(a_root))
    b_files = set(_iter_files(b_root))
    results: list[dict] = []
    for rel in sorted(a_files | b_files):
        a = _read_text(a_root / rel) if rel in a_files else None
        b = _read_text(b_root / rel) if rel in b_files else None
        if a == b:
            continue
        status = "added" if a is None else "deleted" if b is None else "modified"
        a_label = f"v{frm}/{rel}"
        b_label = (f"v{to}/{rel}" if to is not None else f"current/{rel}")
        ud = difflib.unified_diff(
            (a or "").splitlines(), (b or "").splitlines(),
            fromfile=a_label, tofile=b_label, lineterm="",
        )
        added = 0
        removed = 0
        body = "\n".join(ud)
        for line in body.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                added += 1
            elif line.startswith("-") and not line.startswith("---"):
                removed += 1
        results.append({"path": rel, "status": status, "added": added,
                        "removed": removed, "diff": body})
    return results


def restore(workspace: str, vid: int) -> dict:
    """Restore the workspace to version `vid`, then snapshot the result as a new version."""
    tree = _tree(workspace, vid)
    if not tree.is_dir():
        raise FileNotFoundError(f"version {vid} not found")
    base = Path(workspace)
    for rel in list(_iter_files(base)):
        try:
            (base / rel).unlink()
        except OSError:
            pass
    for rel in _iter_files(tree):
        src, dst = tree / rel, base / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(src, dst)
        except OSError:
            continue
    return snapshot(workspace, label=f"Reverted to v{vid}")
