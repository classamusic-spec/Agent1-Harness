"""A dependency-free web console for the harness.

Features:
  - Browse specs and launch builds (or check-only runs); watch logs live (SSE).
  - Author new specs from the UI (validated before saving).
  - Artifact gallery: browse built workspaces and preview built apps in an iframe.
  - Live workspace view: watch files appear/change as the agent works.

Built on the stdlib http.server — nothing extra to install. Builds are
serialized (one at a time) so stdout capture for the live log stays correct.

Run:  appbuilder-web            # http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import yaml

from harness.config import DEFAULT_LOCAL_BASE_URL, DEFAULT_MODEL, EngineConfig, HarnessConfig
from harness.control import BuildControl
from harness.diffing import _SKIP_DIRS
from harness.spec import SpecError, load_spec, parse_spec
from harness.verifier import run_suite

WEB_DIR = Path(__file__).resolve().parent.parent / "webui"
_TREE_CAP = 400


# --- testable helpers -----------------------------------------------------

def list_specs(specs_dir: str) -> list[dict]:
    out: list[dict] = []
    base = Path(specs_dir)
    if not base.is_dir():
        return out
    for p in sorted(base.glob("*.yaml")):
        try:
            spec = load_spec(p)
            out.append({"name": spec.name, "kind": spec.kind, "path": str(p)})
        except SpecError:
            continue
    return out


def read_spec(path: str) -> dict:
    spec = load_spec(path)
    return {
        "name": spec.name, "kind": spec.kind, "language": spec.language,
        "description": spec.description, "constraints": spec.constraints,
        "verification": [{"name": c.name, "command": c.command} for c in spec.checks],
        "raw": Path(path).read_text(),
    }


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")
    return s or "spec"


def save_spec(specs_dir: str, data: dict) -> str:
    """Validate and write a spec to <specs_dir>/<slug>.yaml. Returns the path."""
    doc = {
        "name": str(data.get("name", "")).strip(),
        "kind": str(data.get("kind", "fullstack")).strip() or "fullstack",
        "language": str(data.get("language", "unspecified")).strip() or "unspecified",
        "description": str(data.get("description", "")).strip(),
    }
    constraints = [str(c).strip() for c in (data.get("constraints") or []) if str(c).strip()]
    if constraints:
        doc["constraints"] = constraints
    checks = []
    for v in data.get("verification") or []:
        name = str((v or {}).get("name", "")).strip()
        cmd = str((v or {}).get("command", "")).strip()
        if name and cmd:
            checks.append({"name": name, "command": cmd})
    if checks:
        doc["verification"] = checks

    parse_spec(doc)  # raises SpecError if invalid
    Path(specs_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(specs_dir, f"{_slug(doc['name'])}.yaml")
    Path(path).write_text(yaml.safe_dump(doc, sort_keys=False, width=88))
    return path


def list_artifacts(workspaces_dir: str) -> list[dict]:
    base = Path(workspaces_dir)
    out: list[dict] = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        files = [p for p in d.rglob("*") if p.is_file()]
        out.append({
            "name": d.name,
            "files": len(files),
            "has_index": (d / "index.html").is_file(),
            "has_checkpoint": (d / ".appbuilder_checkpoint.json").is_file(),
        })
    return out


def _within(root: str, target: str) -> bool:
    r = os.path.realpath(root)
    t = os.path.realpath(target)
    return t == r or t.startswith(r + os.sep)


def workspace_tree(root: str) -> dict:
    base = os.path.abspath(root)
    files: list[dict] = []
    if os.path.isdir(base):
        for dirpath, dirs, names in os.walk(base):
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
            for n in sorted(names):
                fp = os.path.join(dirpath, n)
                try:
                    size = os.path.getsize(fp)
                except OSError:
                    continue
                files.append({"path": os.path.relpath(fp, base), "size": size})
                if len(files) >= _TREE_CAP:
                    return {"root": base, "files": sorted(files, key=lambda f: f["path"])}
    return {"root": base, "files": sorted(files, key=lambda f: f["path"])}


def read_workspace_file(root: str, rel: str, max_bytes: int = 200_000) -> str:
    target = os.path.join(os.path.abspath(root), rel)
    if not _within(root, target) or not os.path.isfile(target):
        raise FileNotFoundError(rel)
    data = Path(target).read_bytes()[:max_bytes]
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return "<binary file>"


# --- Studio (Replit/Lovable-style) helpers --------------------------------

STUDIO_MARKER = ".studio.json"

# Web-ish kinds get a live, browser-previewable target and a default gate.
_WEB_KINDS = {"frontend", "fullstack", "web", "ui", "react", "react-native", "mobile"}


def _default_checks(kind: str) -> list[dict]:
    """A minimal verification gate so freeform web apps still pass a real check."""
    if kind.strip().lower() in _WEB_KINDS:
        return [{
            "name": "app builds",
            "command": (
                "python3 -c \"import pathlib,sys; "
                "p=pathlib.Path('index.html'); "
                "sys.exit(0 if p.is_file() and p.stat().st_size>80 else 1)\""
            ),
        }]
    return []


def studio_meta(workspace_dir: str) -> dict:
    """Read the per-project Studio marker (kind/checks/engine), or {}."""
    p = os.path.join(workspace_dir, STUDIO_MARKER)
    try:
        return json.loads(Path(p).read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def write_studio_meta(workspace_dir: str, meta: dict) -> None:
    Path(workspace_dir).mkdir(parents=True, exist_ok=True)
    Path(os.path.join(workspace_dir, STUDIO_MARKER)).write_text(json.dumps(meta, indent=2))


def synth_spec(name: str, kind: str, description: str, language: str = "",
               verification: list[dict] | None = None):
    """Build an in-memory Spec for freeform / iterate flows (no YAML file needed)."""
    checks = verification if verification is not None else _default_checks(kind)
    return parse_spec({
        "name": name or "app",
        "kind": kind or "frontend",
        "language": language or "html-css-js",
        "description": description or "",
        "verification": checks,
    })


_ITERATE_PROMPT = """\
You are iterating on an existing app in the current working directory. Read the \
files that exist, then apply this change while keeping everything else working \
and the app runnable:

{instruction}

Keep the same overall stack and structure. Do not delete unrelated features. \
When done, make sure the app still loads."""


async def _iterate_once(spec, config: HarnessConfig, instruction: str):
    """Run a single engine turn against an existing workspace. Returns (tokens, text)."""
    from harness.engines.base import make_engine
    engine = make_engine(spec, config)
    async with engine:
        text = await engine.send(_ITERATE_PROMPT.format(instruction=instruction), echo=True)
    return getattr(engine, "total_tokens", 0), text


def run_tests(workspace_dir: str, checks: list | None = None) -> dict:
    """Run the verification suite for a workspace on demand (for the Run tests button)."""
    from harness.spec import Check
    if checks is None:
        meta = studio_meta(workspace_dir)
        checks = meta.get("checks") or _default_checks(meta.get("kind", "frontend"))
    suite = [Check(name=c["name"], command=c["command"], cwd=workspace_dir) for c in checks]
    if not suite:
        return {"ok": True, "results": [], "note": "no checks defined"}
    report = run_suite(suite, stop_on_failure=False)
    return {
        "ok": report.ok,
        "results": [
            {"name": r.name, "ok": r.ok, "skipped": r.skipped,
             "returncode": r.returncode,
             "output": ((r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")).strip()[:2000]}
            for r in report.results
        ],
    }


# --- job runner -----------------------------------------------------------

class Job:
    def __init__(self, job_id: str, workspace: str):
        self.id = job_id
        self.workspace = workspace
        self.status = "running"
        self.lines: list[str] = []
        self.done = threading.Event()
        self.pending: dict | None = None  # awaiting human approval
        self.decision = None
        self.approve_event = threading.Event()
        self.tokens = 0
        self.elapsed = 0.0
        self.token_budget = None
        self.deadline = None
        self.control = BuildControl()

    def log(self, text: str) -> None:
        for line in text.splitlines():
            self.lines.append(line)


class ServerApproval:
    """Approval gate that pauses the build until the UI posts a decision."""

    def __init__(self, job: Job, timeout: float = 900.0):
        self.job = job
        self.timeout = timeout

    async def request(self, kind: str, payload: dict):
        from harness.approval import Decision
        self.job.approve_event.clear()
        self.job.decision = None
        self.job.pending = {"kind": kind, "payload": payload}
        self.job.log(f"[approval] waiting for sign-off on the {kind}…")
        got = await asyncio.to_thread(self.job.approve_event.wait, self.timeout)
        self.job.pending = None
        if not got or self.job.decision is None:
            return Decision(False, "approval timed out")
        return self.job.decision


class _JobStream(io.TextIOBase):
    def __init__(self, job: Job):
        self.job = job

    def write(self, s: str) -> int:
        self.job.log(s)
        return len(s)


class Console:
    def __init__(self, specs_dir: str, workspaces_dir: str = "workspaces"):
        self.specs_dir = specs_dir
        self.workspaces_dir = workspaces_dir
        self._lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.current: Job | None = None

    def busy(self) -> bool:
        return self._lock.locked()

    def start(self, params: dict) -> Job:
        if params.get("resume"):
            name = os.path.basename(params.get("workspace") or "")
            workspace = os.path.abspath(os.path.join(self.workspaces_dir, name))
        else:
            # Freeform (Studio): a natural-language prompt with no spec file —
            # synthesize and persist a spec so the project is first-class.
            if not params.get("spec") and params.get("prompt"):
                params["spec"] = save_spec(self.specs_dir, {
                    "name": params.get("name") or "app",
                    "kind": params.get("kind") or "frontend",
                    "language": params.get("language") or "html-css-js",
                    "description": params["prompt"],
                    "verification": params.get("verification")
                    or _default_checks(params.get("kind") or "frontend"),
                })
            spec_path = params["spec"]
            workspace = os.path.abspath(
                params.get("workspace") or os.path.join(self.workspaces_dir, Path(spec_path).stem)
            )
        job = Job(str(int(time.time() * 1000)), workspace)
        self.jobs[job.id] = job
        self.current = job
        threading.Thread(target=self._run, args=(job, params), daemon=True).start()
        return job

    def iterate(self, params: dict) -> Job:
        """Lovable-style conversational edit: one engine turn on an existing app."""
        name = os.path.basename(params.get("workspace") or "")
        workspace = os.path.abspath(os.path.join(self.workspaces_dir, name))
        job = Job(str(int(time.time() * 1000)), workspace)
        self.jobs[job.id] = job
        self.current = job
        threading.Thread(target=self._run_iterate, args=(job, params), daemon=True).start()
        return job

    def _run_iterate(self, job: Job, params: dict) -> None:
        if not self._lock.acquire(blocking=False):
            job.status = "error"
            job.log("error: a build is already running")
            job.done.set()
            return
        try:
            with contextlib.redirect_stdout(_JobStream(job)):
                self._execute_iterate(job, params)
        except Exception as exc:
            job.status = "error"
            job.log(f"error: {type(exc).__name__}: {exc}")
        finally:
            job.done.set()
            self._lock.release()

    def _execute_iterate(self, job: Job, params: dict) -> None:
        instruction = str(params.get("instruction", "")).strip()
        if not instruction:
            job.status = "error"
            print("error: instruction is required")
            return

        meta = studio_meta(job.workspace)
        kind = params.get("kind") or meta.get("kind") or "frontend"
        provider = params.get("engine") or meta.get("engine") or "claude-cli"
        model = params.get("model") or meta.get("model") or ""
        base_url = params.get("base_url") or meta.get("base_url")
        spec = synth_spec(os.path.basename(job.workspace), kind, instruction,
                          verification=meta.get("checks"))

        engine_cfg = EngineConfig(
            provider=provider, model=model,
            base_url=base_url or (DEFAULT_LOCAL_BASE_URL if provider == "local" else None),
            api_key_env="OPENAI_API_KEY" if provider == "local" else "ANTHROPIC_API_KEY",
        )
        config = HarnessConfig(workspace=job.workspace, engine=engine_cfg)

        print(f"[iterate] {provider} · {model or '(default)'} — applying change…")
        print(f"› {instruction}")
        tokens, _text = asyncio.run(_iterate_once(spec, config, instruction))
        job.tokens = tokens
        # Verify against the project's gate so the preview reflects a passing app.
        result = run_tests(job.workspace, meta.get("checks"))
        for r in result["results"]:
            print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['name']}")
        job.status = "passed" if result["ok"] else "failed"
        print(f"RESULT: {job.status.upper()} · {tokens} tokens")

    def _run(self, job: Job, params: dict) -> None:
        if not self._lock.acquire(blocking=False):
            job.status = "error"
            job.log("error: another build is already running")
            job.done.set()
            return
        try:
            with contextlib.redirect_stdout(_JobStream(job)):
                self._execute(job, params)
        except Exception as exc:
            job.status = "error"
            job.log(f"error: {type(exc).__name__}: {exc}")
        finally:
            job.done.set()
            self._lock.release()

    def _execute(self, job: Job, params: dict) -> None:
        cp_path = os.path.join(job.workspace, ".appbuilder_checkpoint.json")

        def on_progress(p):
            job.tokens = p.get("tokens", job.tokens)
            job.elapsed = p.get("elapsed", job.elapsed)

        if params.get("resume"):
            from harness.agent import resume
            print(f"Resuming build in {job.workspace}…")
            result = asyncio.run(resume(cp_path, echo=True, on_progress=on_progress, control=job.control))
            job.status = "passed" if result.ok else "failed"
            job.tokens, job.elapsed = result.tokens_used, result.elapsed_seconds
            print(f"RESULT: {job.status.upper()} ({result.stop_reason}) after {result.rounds} round(s)")
            return

        spec = load_spec(params["spec"], cwd=job.workspace)
        # Persist a Studio marker so conversational iterate / Run tests know the
        # project's persona, gate, and engine without the original spec file.
        write_studio_meta(job.workspace, {
            "kind": spec.kind,
            "engine": params.get("engine", "anthropic"),
            "model": params.get("model") or "",
            "base_url": params.get("base_url"),
            "checks": [{"name": c.name, "command": c.command} for c in spec.checks],
        })
        if params.get("check_only"):
            report = run_suite(spec.checks, stop_on_failure=True)
            print(report.to_feedback() or "(no checks defined)")
            job.status = "passed" if report.ok else "failed"
            print("RESULT:", job.status.upper())
            return

        provider = params.get("engine", "anthropic")
        model = params.get("model") or (DEFAULT_MODEL if provider == "anthropic" else "")
        engine = EngineConfig(
            provider=provider, model=model,
            base_url=params.get("base_url") or (DEFAULT_LOCAL_BASE_URL if provider == "local" else None),
            api_key_env="ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY",
        )
        approve_plan = bool(params.get("approve_plan"))
        approve_build = bool(params.get("approve_build"))
        panel = params.get("review_panel") or []
        config = HarnessConfig(
            workspace=job.workspace, engine=engine,
            enable_review=bool(params.get("review")) or bool(panel),
            review_focus=params.get("review_focus", "quality"),
            review_panel=list(panel),
            learn=bool(params.get("learn")),
            memory_path=params.get("memory") or os.path.join(self.workspaces_dir, ".lessons.jsonl"),
            test_first=bool(params.get("test_first")),
            approve_plan=approve_plan, approve_build=approve_build,
            max_tokens_budget=params.get("token_budget"),
            deadline_seconds=params.get("deadline"),
            checkpoint_path=cp_path,
        )
        job.token_budget = params.get("token_budget")
        job.deadline = params.get("deadline")
        from harness.agent import build

        gate = ServerApproval(job) if (approve_plan or approve_build) else None

        print(f"engine={provider} model={model or '(unset)'} kind={spec.kind}")
        result = asyncio.run(build(spec, config, echo=True, approval=gate,
                                   on_progress=on_progress, control=job.control))
        job.status = "passed" if result.ok else "failed"
        job.tokens, job.elapsed = result.tokens_used, result.elapsed_seconds
        print(f"RESULT: {job.status.upper()} ({result.stop_reason}) after {result.rounds} round(s)")
        print(f"Telemetry: {result.tokens_used} tokens · {result.elapsed_seconds:.1f}s")


# --- HTTP layer -----------------------------------------------------------

def make_handler(console: Console):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, code, body: bytes, ctype="application/json"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj).encode())

        def do_GET(self):
            u = urlparse(self.path)
            path, q = u.path, parse_qs(u.query)
            if path in ("/", "/index.html"):
                return self._file("index.html", "text/html")
            if path.startswith("/static/"):
                return self._file(path[len("/static/"):], None)
            if path.startswith("/artifact/"):
                return self._artifact(path[len("/artifact/"):])
            if path == "/api/health":
                return self._json({"ok": True, "busy": console.busy()})
            if path == "/api/specs":
                return self._json({"specs": list_specs(console.specs_dir)})
            if path == "/api/spec":
                try:
                    return self._json(read_spec(q.get("path", [""])[0]))
                except (SpecError, OSError) as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/artifacts":
                return self._json({"artifacts": list_artifacts(console.workspaces_dir)})
            if path == "/api/current":
                j = console.current
                return self._json({"job": j.id if j else None,
                                   "status": j.status if j else None,
                                   "workspace": j.workspace if j else None,
                                   "name": os.path.basename(j.workspace) if j else None,
                                   "pending": j.pending if j else None,
                                   "tokens": j.tokens if j else 0,
                                   "elapsed": round(j.elapsed, 1) if j else 0.0,
                                   "token_budget": j.token_budget if j else None,
                                   "deadline": j.deadline if j else None,
                                   "busy": console.busy()})
            if path == "/api/workspace":
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json(workspace_tree(root))
            if path == "/api/workspace/file":
                root = self._ws_root(q.get("dir", [""])[0])
                try:
                    return self._json({"content": read_workspace_file(root, q.get("file", [""])[0])})
                except (FileNotFoundError, OSError):
                    return self._json({"error": "not found"}, 404)
            if path.startswith("/api/jobs/") and path.endswith("/events"):
                return self._sse(path.split("/")[3])
            self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
            if u.path == "/api/builds":
                if not body.get("spec") and not body.get("resume") and not body.get("prompt"):
                    return self._json({"error": "spec, prompt, or resume is required"}, 400)
                try:
                    job = console.start(body)
                except SpecError as e:
                    return self._json({"error": str(e)}, 400)
                return self._json({"id": job.id, "workspace": job.workspace})
            if u.path == "/api/iterate":
                if not body.get("workspace") or not body.get("instruction"):
                    return self._json({"error": "workspace and instruction are required"}, 400)
                if console.busy():
                    return self._json({"error": "a build is already running"}, 409)
                job = console.iterate(body)
                return self._json({"id": job.id, "workspace": job.workspace})
            if u.path == "/api/test":
                name = os.path.basename(body.get("workspace") or "")
                root = os.path.join(os.path.abspath(console.workspaces_dir), name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                try:
                    return self._json(run_tests(root, body.get("checks")))
                except Exception as e:  # surface check-runner errors to the UI
                    return self._json({"error": f"{type(e).__name__}: {e}"}, 500)
            if u.path == "/api/specs":
                try:
                    p = save_spec(console.specs_dir, body)
                    return self._json({"ok": True, "path": p, "name": Path(p).stem})
                except SpecError as e:
                    return self._json({"error": str(e)}, 400)
            if u.path.startswith("/api/jobs/") and u.path.endswith("/control"):
                job = console.jobs.get(u.path.split("/")[3])
                if not job:
                    return self._json({"error": "unknown job"}, 404)
                action = body.get("action")
                if action == "pause":
                    job.control.pause()
                elif action == "cancel":
                    job.control.cancel()
                else:
                    return self._json({"error": "action must be pause|cancel"}, 400)
                job.log(f"[control] {action} requested")
                return self._json({"ok": True})
            if u.path.startswith("/api/jobs/") and u.path.endswith("/approve"):
                from harness.approval import Decision
                job = console.jobs.get(u.path.split("/")[3])
                if not job:
                    return self._json({"error": "unknown job"}, 404)
                job.decision = Decision(bool(body.get("approved")), str(body.get("message", "")))
                job.approve_event.set()
                return self._json({"ok": True})
            self._send(404, b'{"error":"not found"}')

        # helpers
        def _ws_root(self, name: str) -> str:
            name = os.path.basename(name or "")
            return os.path.join(os.path.abspath(console.workspaces_dir), name)

        def _file(self, rel, ctype):
            p = (WEB_DIR / rel).resolve()
            if not str(p).startswith(str(WEB_DIR.resolve())) or not p.is_file():
                return self._send(404, b"not found", "text/plain")
            ctype = ctype or {".css": "text/css", ".js": "text/javascript",
                              ".html": "text/html"}.get(p.suffix, "application/octet-stream")
            self._send(200, p.read_bytes(), ctype)

        def _artifact(self, rest):
            parts = rest.split("/", 1)
            if len(parts) != 2:
                return self._send(404, b"not found", "text/plain")
            name, rel = os.path.basename(parts[0]), parts[1]
            root = os.path.join(os.path.abspath(console.workspaces_dir), name)
            target = os.path.join(root, rel)
            if not _within(root, target) or not os.path.isfile(target):
                return self._send(404, b"not found", "text/plain")
            mime = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                    ".json": "application/json", ".svg": "image/svg+xml", ".png": "image/png"}
            self._send(200, Path(target).read_bytes(), mime.get(Path(target).suffix, "text/plain"))

        def _sse(self, job_id):
            job = console.jobs.get(job_id)
            if not job:
                return self._send(404, b'{"error":"unknown job"}')
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            i = 0
            try:
                while True:
                    while i < len(job.lines):
                        self.wfile.write(f"data: {job.lines[i]}\n\n".encode())
                        i += 1
                    self.wfile.flush()
                    if job.done.is_set() and i >= len(job.lines):
                        self.wfile.write(f"event: done\ndata: {job.status}\n\n".encode())
                        self.wfile.flush()
                        break
                    time.sleep(0.25)
            except (BrokenPipeError, ConnectionResetError):
                pass

    return Handler


def serve(host: str, port: int, specs_dir: str, workspaces_dir: str = "workspaces") -> None:
    console = Console(specs_dir, workspaces_dir)
    httpd = ThreadingHTTPServer((host, port), make_handler(console))
    print(f"Agent1-Harness console on http://{host}:{port}  (specs: {specs_dir})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="appbuilder-web", description=__doc__)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--specs", default="specs")
    p.add_argument("--workspaces", default="workspaces")
    args = p.parse_args(argv)
    serve(args.host, args.port, args.specs, args.workspaces)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
