"""A small, dependency-free web console for the harness.

Lets you browse specs, kick off a build (or a check-only run), and watch the
log stream live in the browser. Built on the stdlib http.server so there's
nothing extra to install. Builds are serialized (one at a time) which keeps
stdout capture correct for the live log.

Run:  appbuilder-web            # serves http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from harness.config import DEFAULT_LOCAL_BASE_URL, DEFAULT_MODEL, EngineConfig, HarnessConfig
from harness.spec import SpecError, load_spec
from harness.verifier import run_suite

WEB_DIR = Path(__file__).resolve().parent.parent / "webui"


def list_specs(specs_dir: str) -> list[dict]:
    """Return [{name, kind, path}] for every YAML spec in `specs_dir`."""
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


class Job:
    def __init__(self, job_id: str):
        self.id = job_id
        self.status = "running"  # running | passed | failed | error
        self.lines: list[str] = []
        self.done = threading.Event()

    def log(self, text: str) -> None:
        for line in text.splitlines():
            self.lines.append(line)


class _JobStream(io.TextIOBase):
    def __init__(self, job: Job):
        self.job = job

    def write(self, s: str) -> int:
        self.job.log(s)
        return len(s)


class Console:
    """Serializes builds and tracks the current/last job."""

    def __init__(self, specs_dir: str):
        self.specs_dir = specs_dir
        self._lock = threading.Lock()
        self.jobs: dict[str, Job] = {}

    def busy(self) -> bool:
        return self._lock.locked()

    def start(self, params: dict) -> Job:
        job = Job(job_id=str(int(time.time() * 1000)))
        self.jobs[job.id] = job
        threading.Thread(target=self._run, args=(job, params), daemon=True).start()
        return job

    def _run(self, job: Job, params: dict) -> None:
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            job.status = "error"
            job.log("error: another build is already running")
            job.done.set()
            return
        try:
            stream = _JobStream(job)
            with contextlib.redirect_stdout(stream):
                self._execute(job, params)
        except Exception as exc:  # never let the thread die silently
            job.status = "error"
            job.log(f"error: {type(exc).__name__}: {exc}")
        finally:
            job.done.set()
            self._lock.release()

    def _execute(self, job: Job, params: dict) -> None:
        spec_path = params["spec"]
        workspace = os.path.abspath(params.get("workspace") or f"workspaces/{Path(spec_path).stem}")
        spec = load_spec(spec_path, cwd=workspace)

        if params.get("check_only"):
            report = run_suite(spec.checks, stop_on_failure=True)
            print(report.to_feedback() or "(no checks defined)")
            job.status = "passed" if report.ok else "failed"
            print("RESULT:", job.status.upper())
            return

        provider = params.get("engine", "anthropic")
        model = params.get("model") or (DEFAULT_MODEL if provider == "anthropic" else "")
        engine = EngineConfig(
            provider=provider,
            model=model,
            base_url=params.get("base_url") or (DEFAULT_LOCAL_BASE_URL if provider == "local" else None),
            api_key_env="ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY",
        )
        config = HarnessConfig(
            workspace=workspace,
            engine=engine,
            enable_review=bool(params.get("review")),
            review_focus=params.get("review_focus", "quality"),
            learn=bool(params.get("learn")),
            memory_path=params.get("memory") or os.path.join("workspaces", ".lessons.jsonl"),
        )
        from harness.agent import build  # lazy: keeps check-only path SDK-free

        print(f"engine={provider} model={model or '(unset)'} kind={spec.kind}")
        result = asyncio.run(build(spec, config, echo=True))
        job.status = "passed" if result.ok else "failed"
        print(f"RESULT: {job.status.upper()} after {result.rounds} round(s)")


# --- HTTP layer -----------------------------------------------------------

def make_handler(console: Console):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
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
            if self.path == "/" or self.path == "/index.html":
                return self._file("index.html", "text/html")
            if self.path.startswith("/static/"):
                return self._file(self.path[len("/static/"):], None)
            if self.path == "/api/specs":
                return self._json({"specs": list_specs(console.specs_dir)})
            if self.path == "/api/health":
                return self._json({"ok": True, "busy": console.busy()})
            if self.path.startswith("/api/jobs/") and self.path.endswith("/events"):
                return self._sse(self.path.split("/")[3])
            self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            if self.path == "/api/builds":
                length = int(self.headers.get("Content-Length", 0))
                params = json.loads(self.rfile.read(length) or b"{}")
                if not params.get("spec"):
                    return self._json({"error": "spec is required"}, 400)
                job = console.start(params)
                return self._json({"id": job.id})
            self._send(404, b'{"error":"not found"}')

        def _file(self, rel, ctype):
            path = (WEB_DIR / rel).resolve()
            if not str(path).startswith(str(WEB_DIR.resolve())) or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            if ctype is None:
                ext = path.suffix
                ctype = {".css": "text/css", ".js": "text/javascript",
                         ".html": "text/html"}.get(ext, "application/octet-stream")
            self._send(200, path.read_bytes(), ctype)

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


def serve(host: str, port: int, specs_dir: str) -> None:
    console = Console(specs_dir)
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
    p.add_argument("--specs", default="specs", help="Directory of spec files")
    args = p.parse_args(argv)
    serve(args.host, args.port, args.specs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
