"""Speculative verification — run the cheap checks the moment files change.

While the engine is still working (especially a slow local model), a background
watcher polls the workspace and, once edits settle, runs only the *fast, static*
checks (compile / typecheck — no install, no server, safe to repeat) and reports
them as advisory "⚡ fast check" lines. You see a typecheck error seconds after the
file is written instead of waiting for the full end-of-turn gate. The authoritative
gate still runs at the end; these are hints. Stdlib only.
"""

from __future__ import annotations

import contextlib
import os
import threading
import time

_SKIP = {".git", "node_modules", "__pycache__", ".studio", ".pytest_cache", ".venv", "venv"}


def _mtime(workspace: str) -> float:
    newest = 0.0
    for dp, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [d for d in dirnames if d not in _SKIP]
        for fn in filenames:
            try:
                m = os.path.getmtime(os.path.join(dp, fn))
            except OSError:
                continue
            if m > newest:
                newest = m
    return newest


def fast_checks(workspace: str):
    """The cheapest static checks for the workspace's stack (safe to run repeatedly
    while files are mid-edit). Empty if nothing fast applies."""
    from harness import stacks
    from harness.verifier import Check
    stack = stacks.detect_stack(workspace)
    out = []
    if stack == "python":
        out.append(Check(name="compiles", command="python -m compileall -q .",
                         cwd=workspace, timeout=60, allow_failure=True))
    elif stack == "node":
        has_tsc = os.path.isfile(os.path.join(workspace, "tsconfig.json"))
        if has_tsc and os.path.isdir(os.path.join(workspace, "node_modules")):
            out.append(Check(name="typecheck", command="npx --no-install tsc --noEmit",
                             cwd=workspace, timeout=120, allow_failure=True))
    return out


class Speculator:
    """Watch a workspace; when edits settle, run fast checks and report results."""

    def __init__(self, workspace: str, on_result, *, interval: float = 1.0,
                 debounce: float = 1.5, runner=None):
        self.workspace = workspace
        self.on_result = on_result
        self.interval = interval
        self.debounce = debounce
        self.runner = runner
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> "Speculator":
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        checks = fast_checks(self.workspace)
        if not checks:
            return
        baseline = _mtime(self.workspace)
        changed_at = None
        ran_at = baseline
        while not self._stop.wait(self.interval):
            m = _mtime(self.workspace)
            if m > baseline:
                baseline = m
                changed_at = time.monotonic()
            elif changed_at and (time.monotonic() - changed_at) >= self.debounce and ran_at != m:
                ran_at = m
                changed_at = None
                self.run_once(checks)

    def run_once(self, checks=None) -> list[dict]:
        from harness.verifier import run_suite
        checks = checks if checks is not None else fast_checks(self.workspace)
        if not checks:
            return []
        report = run_suite(checks, stop_on_failure=False, runner=self.runner)
        results = [{"name": r.name, "ok": r.ok,
                    "error": (r.stderr or r.error or "").strip()[:200]} for r in report.results]
        try:
            self.on_result(results)
        except Exception:
            pass
        return results


def report_line(results: list[dict]) -> str:
    """Render a one-line advisory summary of a speculative run."""
    bad = [r for r in results if not r.get("ok")]
    if bad:
        detail = ", ".join(r["name"] for r in bad)
        return f"⚡ fast check: FAIL — {detail} (advisory; full gate runs at end of turn)"
    names = ", ".join(r["name"] for r in results)
    return f"⚡ fast check: PASS — {names}"


@contextlib.contextmanager
def watch(workspace: str, on_result=None, *, enabled: bool = True, **kw):
    """Run a Speculator for the duration of a `with` block.

    By default prints a `report_line` to stdout for each speculative run; pass
    `on_result` to override. A no-op when `enabled` is False or no fast checks
    apply to the workspace.
    """
    if on_result is None:
        def on_result(results):  # noqa: E306
            print(report_line(results), flush=True)
    spec = None
    if enabled and fast_checks(workspace):
        spec = Speculator(workspace, on_result, **kw).start()
    try:
        yield spec
    finally:
        if spec is not None:
            spec.stop()
