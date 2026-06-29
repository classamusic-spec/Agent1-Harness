"""Template marketplace — save, share, and reuse whole project starters.

A template bundles a project's files + its settings (kind, run command, scaffold,
plan/multi) + a design profile, as one JSON file. Save a built app as a template,
browse them, "use" one to seed a new project (files + house style applied), and
share by downloading / importing the JSON. Stdlib only.
"""

from __future__ import annotations

import json
import os

# Don't capture caches, VCS, deps, local DBs, or secrets into a shareable template.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".studio", ".pytest_cache",
              ".venv", "venv", "dist", "build", ".next", ".cache", ".github"}
_SKIP_FILES = {".env", ".deploys.json", ".deploy-settings.json", ".previews.json"}
_SKIP_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pyc", ".log", ".zip", ".png", ".jpg")
_MAX_FILE = 200_000
_MAX_TOTAL = 2_000_000


def slug(name: str) -> str:
    s = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (name or "template").lower())
    return s.strip("-_") or "template"


def capture_files(workspace: str) -> dict[str, str]:
    """Collect a workspace's source files (text only, minus caches/secrets/DBs)."""
    root = os.path.abspath(workspace)
    out: dict[str, str] = {}
    total = 0
    for dp, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fn in filenames:
            if fn in _SKIP_FILES or fn.endswith(_SKIP_SUFFIXES):
                continue
            full = os.path.join(dp, fn)
            try:
                if os.path.getsize(full) > _MAX_FILE:
                    continue
                data = open(full, "rb").read()
            except OSError:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue  # skip binaries
            rel = os.path.relpath(full, root)
            out[rel] = text
            total += len(data)
            if total > _MAX_TOTAL:
                return out
    return out


def from_workspace(workspace: str, name: str, description: str = "", *,
                   kind: str = "frontend", run: str = "", scaffold: str | None = None,
                   plan: bool = False, multi: bool = False,
                   profile: dict | None = None) -> dict:
    return {
        "name": name, "description": description, "kind": kind, "run": run,
        "scaffold": scaffold, "plan": bool(plan), "multi": bool(multi),
        "profile": profile or {}, "files": capture_files(workspace),
    }


def _meta(t: dict) -> dict:
    return {"name": t.get("name", ""), "description": t.get("description", ""),
            "kind": t.get("kind", ""), "files": len(t.get("files") or {}),
            "builtin": bool(t.get("builtin")),
            "has_profile": bool((t.get("profile") or {}).get("palette") or
                                (t.get("profile") or {}).get("ui_style"))}


# Curated starters shipped out of the box — each built from a known-good scaffold
# plus a tasteful default design profile, so a new user has something to "Use" on
# day one (and to remix into their own templates).
_BUILTIN_DEFS = [
    {"id": "minimal-spa", "name": "Minimal SPA", "scaffold": "static", "kind": "frontend",
     "description": "Vanilla HTML/CSS/JS single-page app — no build step.",
     "profile": {"ui_style": "minimal, clean, lots of whitespace, rounded corners",
                 "palette": ["#0b0f17", "#5e8cff", "#eef1f8"], "tone": "calm and concise"}},
    {"id": "notes-sqlite", "name": "Notes API (SQLite)", "scaffold": "python-db",
     "kind": "fullstack", "description": "Stdlib full-stack notes app with SQLite migrations.",
     "profile": {"ui_style": "calm, card-based, soft shadows",
                 "palette": ["#0b0f17", "#5e8cff", "#eef1f8"]}},
    {"id": "json-api", "name": "Stdlib Full-stack API", "scaffold": "python-api",
     "kind": "fullstack", "description": "Zero-dependency JSON API + static frontend.",
     "profile": {"ui_style": "minimal, functional"}},
]


def builtins() -> list[dict]:
    """The curated starter templates (materialised from scaffolds)."""
    from harness import scaffolds
    out = []
    for d in _BUILTIN_DEFS:
        sc = scaffolds.get(d["scaffold"])
        if not sc:
            continue
        out.append({"name": d["name"], "description": d["description"], "kind": d["kind"],
                    "run": sc.run, "scaffold": d["scaffold"], "plan": False, "multi": False,
                    "profile": d["profile"], "files": dict(sc.files), "builtin": True})
    return out


def list_templates(templates_dir: str, *, include_builtins: bool = True) -> list[dict]:
    out, seen = [], set()
    if os.path.isdir(templates_dir):
        for fn in sorted(os.listdir(templates_dir)):
            if not fn.endswith(".json"):
                continue
            try:
                t = json.load(open(os.path.join(templates_dir, fn), encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            out.append({**_meta(t), "id": fn[:-5]})
            seen.add(fn[:-5])
    if include_builtins:
        for d, t in zip(_BUILTIN_DEFS, builtins()):
            if d["id"] not in seen:  # a user template with the same id overrides it
                out.append({**_meta(t), "id": d["id"]})
    return out


def load(templates_dir: str, name: str) -> dict | None:
    path = os.path.join(templates_dir, slug(name) + ".json")
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    for d, t in zip(_BUILTIN_DEFS, builtins()):  # fall back to a built-in starter
        if d["id"] == slug(name):
            return t
    return None


def save(templates_dir: str, template: dict) -> dict:
    if not str(template.get("name") or "").strip():
        raise ValueError("template needs a name")
    if not isinstance(template.get("files"), dict):
        template["files"] = {}
    os.makedirs(templates_dir, exist_ok=True)
    path = os.path.join(templates_dir, slug(template["name"]) + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(template, fh, indent=2)
    return {**_meta(template), "id": slug(template["name"])}


def apply(template: dict, workspace: str) -> dict:
    """Write a template's files into a (new) workspace and return its settings.
    Existing files are not overwritten."""
    os.makedirs(workspace, exist_ok=True)
    for rel, content in (template.get("files") or {}).items():
        dest = os.path.normpath(os.path.join(workspace, rel))
        if not dest.startswith(os.path.abspath(workspace)):
            continue  # path-traversal guard
        if os.path.exists(dest):
            continue
        os.makedirs(os.path.dirname(dest) or workspace, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
    return {"kind": template.get("kind", "frontend"), "run": template.get("run", ""),
            "scaffold": template.get("scaffold"), "plan": bool(template.get("plan")),
            "multi": bool(template.get("multi")), "profile": template.get("profile") or {}}
