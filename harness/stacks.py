"""Stack detection + real toolchain default checks.

Turns a workspace into a meaningful verification suite: install deps, build,
typecheck, lint, unit tests for the detected stack — plus a server-backed
**smoke** check that boots the app and asserts it actually responds.

Checks are plain `Check`s, so they run through the normal verifier/sandbox. The
smoke check is marked `needs_server=True` and uses `$APP_URL` (substituted with
the running dev server's address by `harness.fullstack`).
"""

from __future__ import annotations

import json
import os

from harness.verifier import Check

_WEB_KINDS = {"frontend", "fullstack", "web", "ui", "react", "react-native", "mobile", "api"}

# Self-contained smoke: GET the running app and require a 200 with real content.
SMOKE_CMD = (
    "python3 -c \"import urllib.request,sys; "
    "r=urllib.request.urlopen('$APP_URL/', timeout=8); d=r.read(); "
    "sys.exit(0 if getattr(r,'status',200)==200 and len(d)>50 else 1)\""
)


def _has(ws: str, *names: str) -> bool:
    return any(os.path.exists(os.path.join(ws, n)) for n in names)


def detect_stack(workspace: str) -> str:
    if _has(workspace, "package.json"):
        return "node"
    if _has(workspace, "requirements.txt", "pyproject.toml", "manage.py") or \
            any(f.endswith(".py") for f in (os.listdir(workspace) if os.path.isdir(workspace) else [])):
        return "python"
    if _has(workspace, "index.html"):
        return "static"
    return "unknown"


def _node_checks(ws: str) -> list[Check]:
    out = [Check(name="install", command="npm ci || npm install", timeout=900)]
    try:
        pkg = json.load(open(os.path.join(ws, "package.json")))
    except (OSError, json.JSONDecodeError):
        pkg = {}
    scripts = pkg.get("scripts") or {}
    deps = {**(pkg.get("dependencies") or {}), **(pkg.get("devDependencies") or {})}
    if "build" in scripts:
        out.append(Check(name="build", command="npm run build", timeout=900))
    if _has(ws, "tsconfig.json") or "typescript" in deps:
        out.append(Check(name="typecheck", command="npx --no-install tsc --noEmit", timeout=600))
    if _has(ws, ".eslintrc", ".eslintrc.json", ".eslintrc.cjs", "eslint.config.js") or "eslint" in deps:
        out.append(Check(name="lint", command="npx --no-install eslint .", timeout=600,
                         allow_failure=True))
    test = scripts.get("test", "")
    if test and "no test specified" not in test:
        out.append(Check(name="tests", command="npm test", timeout=900))
    return out


def _python_checks(ws: str) -> list[Check]:
    out: list[Check] = []
    if _has(ws, "requirements.txt"):
        out.append(Check(name="install", command="pip install -r requirements.txt", timeout=900))
    has_tests = _has(ws, "tests") or any(
        f.startswith("test_") or f.endswith("_test.py")
        for f in (os.listdir(ws) if os.path.isdir(ws) else [])
    )
    if has_tests:
        out.append(Check(name="tests", command="pytest -q", timeout=600))
    out.append(Check(name="compiles", command="python -m compileall -q .", timeout=120,
                     allow_failure=True))
    return out


def default_checks(workspace: str | None = None, kind: str = "frontend",
                   *, smoke: bool = True) -> list[Check]:
    """A real default verification suite for the detected stack (+ a smoke check)."""
    checks: list[Check] = []
    stack = detect_stack(workspace) if (workspace and os.path.isdir(workspace)) else "unknown"
    if stack == "node":
        checks += _node_checks(workspace)
    elif stack == "python":
        checks += _python_checks(workspace)
    elif kind.strip().lower() in _WEB_KINDS or stack == "static":
        checks.append(Check(
            name="app builds",
            command=("python3 -c \"import pathlib,sys; p=pathlib.Path('index.html'); "
                     "sys.exit(0 if p.is_file() and p.stat().st_size>80 else 1)\""),
        ))
    if smoke and (kind.strip().lower() in _WEB_KINDS or stack in ("node", "python", "static")):
        checks.append(Check(name="app responds", command=SMOKE_CMD, needs_server=True, timeout=60))
    return checks
