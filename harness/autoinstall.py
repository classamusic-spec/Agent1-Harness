"""Auto-install missing dependencies from a failing build's error output.

A very common build failure is just a missing package: `ModuleNotFoundError: No
module named 'flask'` or node's `Cannot find module 'express'`. Spending a whole
LLM repair round on that is wasteful — the fix is deterministic. This scans the
verification/runtime error text for missing-import signatures, maps them to the
right package, and runs the install directly so the next verify round can proceed.

Module→package mappings cover the usual cases where the import name differs from
the PyPI name. The install command is run through the same sandbox runner as
checks. Pure detection logic is unit-tested; the runner is injectable.
"""

from __future__ import annotations

import re

# Python import name -> PyPI package, for the common mismatches.
_PY_ALIASES = {
    "cv2": "opencv-python", "PIL": "Pillow", "yaml": "pyyaml", "bs4": "beautifulsoup4",
    "sklearn": "scikit-learn", "dotenv": "python-dotenv", "jwt": "PyJWT",
    "dateutil": "python-dateutil", "OpenSSL": "pyOpenSSL", "Crypto": "pycryptodome",
    "psycopg2": "psycopg2-binary", "MySQLdb": "mysqlclient", "serial": "pyserial",
    "git": "GitPython", "docx": "python-docx", "magic": "python-magic",
}
# Modules that ship with Python / Node — never try to install these.
_PY_STDLIB = {"os", "sys", "json", "re", "time", "math", "typing", "asyncio",
              "subprocess", "pathlib", "dataclasses", "collections", "itertools",
              "functools", "datetime", "sqlite3", "http", "urllib", "logging"}

_PY_MISSING = re.compile(r"No module named ['\"]([\w.]+)['\"]")
_NODE_MISSING = re.compile(r"Cannot find module ['\"]([^'\"]+)['\"]")


def _py_package(mod: str) -> str | None:
    top = mod.split(".")[0]
    if not top or top in _PY_STDLIB:
        return None
    return _PY_ALIASES.get(top, top)


def _node_package(mod: str) -> str | None:
    # Ignore relative imports and built-ins (node:fs, ./util, /abs/path).
    if mod.startswith((".", "/")) or mod.startswith("node:"):
        return None
    # Scoped packages keep one '/'; bare packages take the first segment.
    parts = mod.split("/")
    if mod.startswith("@") and len(parts) >= 2:
        return "/".join(parts[:2])
    return parts[0] or None


def scan(text: str, stack: str) -> list[str]:
    """Packages to install for the missing imports found in `text` (deduped, ordered)."""
    if not text:
        return []
    out: list[str] = []
    seen = set()
    if stack == "python":
        for m in _PY_MISSING.findall(text):
            pkg = _py_package(m)
            if pkg and pkg not in seen:
                seen.add(pkg)
                out.append(pkg)
    elif stack == "node":
        for m in _NODE_MISSING.findall(text):
            pkg = _node_package(m)
            if pkg and pkg not in seen:
                seen.add(pkg)
                out.append(pkg)
    return out


def install_command(stack: str, packages: list[str]) -> str | None:
    if not packages:
        return None
    if stack == "python":
        return "python -m pip install " + " ".join(packages)
    if stack == "node":
        return "npm install " + " ".join(packages)
    return None


def plan(text: str, workspace: str) -> dict:
    """What to install for this error, or {} if nothing applies. Stdlib-only detection."""
    from harness import stacks
    stack = stacks.detect_stack(workspace)
    packages = scan(text, stack)
    cmd = install_command(stack, packages)
    if not cmd:
        return {}
    return {"stack": stack, "packages": packages, "command": cmd}


def run(text: str, workspace: str, *, runner=None, timeout: int = 300,
        already: set | None = None) -> dict:
    """Detect + install missing deps. Returns {ran, command?, packages?, ok?, skipped?}.

    `already` is a set of package names tried before (mutated here) so the build
    loop never re-attempts the same install in a loop."""
    p = plan(text, workspace)
    if not p:
        return {"ran": False}
    if already is not None:
        fresh = [pkg for pkg in p["packages"] if pkg not in already]
        if not fresh:
            return {"ran": False, "skipped": "already attempted"}
        already.update(fresh)
        p["packages"] = fresh
        p["command"] = install_command(p["stack"], fresh)
    if runner is None:
        from harness.sandbox import HostRunner
        runner = HostRunner()
    try:
        proc = runner.run(p["command"], workspace, timeout)
    except Exception as exc:  # noqa: BLE001 - install is best-effort
        return {"ran": True, "ok": False, "command": p["command"],
                "packages": p["packages"], "error": f"{type(exc).__name__}: {exc}"}
    return {"ran": True, "ok": proc.returncode == 0, "command": p["command"],
            "packages": p["packages"], "returncode": proc.returncode}
