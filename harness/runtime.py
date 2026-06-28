"""Run a project's dev stack as a managed process, for live full-stack preview.

A `Service` wraps one dev server (e.g. `npm run dev`, `uvicorn app:app`, or a
static `python -m http.server`): it injects a free `$PORT`, captures stdout/stderr
into a ring buffer, and health-checks the port. The `RuntimeManager` keeps one
live server at a time (preview is single-focus, like builds) and the web console
reverse-proxies the Studio preview to it.

Stdlib only. Processes run on 127.0.0.1; hardening (containers, caps) is future work.
"""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import threading
import time
import urllib.request
from collections import deque


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def detect_command(workspace: str) -> dict | None:
    """Best-effort run command for common stacks. Returns {command, kind, ...} or None."""
    j = os.path.join
    pkg = j(workspace, "package.json")
    if os.path.isfile(pkg):
        try:
            scripts = (json.load(open(pkg)).get("scripts") or {})
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if "dev" in scripts:
            return {"command": "npm run dev", "kind": "node", "needs_install": True}
        if "start" in scripts:
            return {"command": "npm start", "kind": "node", "needs_install": True}
    for f in ("app.py", "main.py", "server.py", "manage.py", "wsgi.py"):
        if os.path.isfile(j(workspace, f)):
            cmd = f"python manage.py runserver 0.0.0.0:$PORT" if f == "manage.py" else f"python {f}"
            return {"command": cmd, "kind": "python",
                    "needs_install": os.path.isfile(j(workspace, "requirements.txt"))}
    if os.path.isfile(j(workspace, "index.html")):
        return {"command": "python -m http.server $PORT", "kind": "static"}
    return None


class Service:
    def __init__(self, workspace: str, command: str, port: int,
                 env: dict | None = None, ready_path: str = "/"):
        self.workspace = workspace
        self.command = command
        self.port = port
        self.env = env or {}
        self.ready_path = ready_path
        self.proc: subprocess.Popen | None = None
        self.status = "starting"  # starting | ready | running | stopped | failed
        self._lines: deque[str] = deque(maxlen=600)
        self._lock = threading.Lock()
        self._stopping = False

    def _log(self, text: str) -> None:
        with self._lock:
            for ln in str(text).splitlines():
                self._lines.append(ln)

    def start(self) -> "Service":
        env = dict(os.environ)
        env.update(self.env)
        env["PORT"] = str(self.port)
        cmd = self.command.replace("$PORT", str(self.port))
        self._log(f"$ {cmd}  (cwd={os.path.basename(self.workspace)})")
        try:
            self.proc = subprocess.Popen(
                cmd, shell=True, cwd=self.workspace, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1, start_new_session=True,
            )
        except OSError as exc:
            self.status = "failed"
            self._log(f"[failed to start: {exc}]")
            return self
        threading.Thread(target=self._pump, daemon=True).start()
        threading.Thread(target=self._healthcheck, daemon=True).start()
        return self

    def _pump(self) -> None:
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            self._log(line.rstrip("\n"))
        rc = self.proc.wait()
        self.status = "stopped" if (rc == 0 or self._stopping) else "failed"
        self._log(f"[process exited: {rc}]")

    def _healthcheck(self, attempts: int = 60, interval: float = 0.5) -> None:
        url = f"http://127.0.0.1:{self.port}{self.ready_path}"
        for _ in range(attempts):
            if self.proc and self.proc.poll() is not None:
                return
            try:
                urllib.request.urlopen(url, timeout=1)
                if self.status == "starting":
                    self.status = "ready"
                    self._log(f"[ready] {url}")
                return
            except Exception:
                time.sleep(interval)
        if self.status == "starting":  # alive but never answered (e.g. wrong path)
            self.status = "running"

    def stop(self) -> None:
        self._stopping = True
        p = self.proc
        if p and p.poll() is None:
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError, OSError):
                try:
                    p.terminate()
                except OSError:
                    pass
        self.status = "stopped"

    def info(self) -> dict:
        return {"command": self.command, "port": self.port, "status": self.status,
                "name": os.path.basename(self.workspace)}

    def logs(self, since: int = 0) -> tuple[list[str], int]:
        with self._lock:
            lines = list(self._lines)
        since = max(0, since)
        return lines[since:], len(lines)


class RuntimeManager:
    """Holds the single live dev server (one preview at a time)."""

    def __init__(self):
        self._svc: Service | None = None
        self._lock = threading.Lock()

    def start(self, workspace: str, command: str, *, port: int | None = None,
              ready_path: str = "/", env: dict | None = None) -> Service:
        self.stop()
        svc = Service(workspace, command, port or free_port(), env=env, ready_path=ready_path)
        svc.start()
        with self._lock:
            self._svc = svc
        return svc

    def current(self) -> Service | None:
        with self._lock:
            return self._svc

    def for_workspace(self, name: str) -> Service | None:
        svc = self.current()
        if svc and os.path.basename(svc.workspace) == os.path.basename(name):
            return svc
        return None

    def stop(self) -> None:
        with self._lock:
            svc, self._svc = self._svc, None
        if svc:
            svc.stop()
