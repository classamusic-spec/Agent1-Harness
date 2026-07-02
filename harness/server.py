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
import base64
import contextlib
import io
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from harness import scaffolds
from harness.runtime import RuntimeManager, detect_command

import yaml

from harness.config import DEFAULT_LOCAL_BASE_URL, DEFAULT_MODEL, EngineConfig, HarnessConfig
from harness.control import BuildControl
from harness.diffing import _SKIP_DIRS
from harness.spec import SpecError, load_spec, parse_spec
from harness.verifier import run_suite

WEB_DIR = Path(__file__).resolve().parent.parent / "webui"
_TREE_CAP = 400

# Injected into a previewed app (only when ?__dev=1) so the Studio can show its
# console.* output, errors, and fetches in a devtools-style panel. Same-origin,
# posts to the parent frame; never present unless the Studio asks for it.
_DEVTOOLS_SNIPPET = (
    "<script>(function(){if(window.__harnessDev)return;window.__harnessDev=1;"
    "function S(l,a){try{parent.postMessage({__harnessLog:1,level:l,"
    "text:[].map.call(a,function(p){try{return typeof p==='object'?JSON.stringify(p):String(p)}"
    "catch(e){return String(p)}}).join(' ')},'*')}catch(e){}}"
    "['log','info','warn','error','debug'].forEach(function(m){var o=console[m]?"
    "console[m].bind(console):function(){};console[m]=function(){S(m,arguments);o.apply(null,arguments)}});"
    "window.addEventListener('error',function(e){S('error',[(e.message||'Error')+"
    "(e.filename?' ('+e.filename.split('/').pop()+':'+e.lineno+')':'')])});"
    "window.addEventListener('unhandledrejection',function(e){var r=e.reason;"
    "S('error',['Unhandled rejection: '+((r&&r.message)||r)])});"
    "var f=window.fetch;if(f){window.fetch=function(){var u=arguments[0];var url=(u&&u.url)||u;"
    "return f.apply(this,arguments).then(function(r){S('net',[r.status+' '+url]);return r},"
    "function(e){S('error',['fetch failed '+url]);throw e})}}"
    "S('info',['devtools attached']);})();</script>"
)


# "Point & edit": when the Studio turns on pick mode, the user hovers to highlight
# and clicks an element; we post a descriptor (selector + label + outerHTML snippet)
# to the parent so the next iterate can target exactly that element. Same-origin.
_PICKER_SNIPPET = (
    "<script>(function(){if(window.__harnessPick)return;window.__harnessPick=1;"
    "var on=false,ov=null;"
    "function box(){if(ov)return ov;ov=document.createElement('div');"
    "ov.style.cssText='position:fixed;z-index:2147483647;pointer-events:none;border:2px solid #5e8cff;"
    "background:rgba(94,140,255,.15);border-radius:3px;transition:all .05s';document.body.appendChild(ov);return ov}"
    "function sel(el){if(el.id)return '#'+el.id;var p=[],n=el;"
    "while(n&&n.nodeType===1&&p.length<4){var s=n.tagName.toLowerCase();"
    "if(n.id){s='#'+n.id;p.unshift(s);break}"
    "if(n.className&&typeof n.className==='string'){var c=n.className.trim().split(/\\s+/).slice(0,2).join('.');"
    "if(c)s+='.'+c}var par=n.parentNode;if(par){var same=[].filter.call(par.children,function(x){return x.tagName===n.tagName});"
    "if(same.length>1)s+=':nth-of-type('+([].indexOf.call(par.children,n)+1)+')'}p.unshift(s);n=n.parentNode}return p.join(' > ')}"
    "function move(e){if(!on)return;var el=e.target;if(!el||el===ov)return;var r=el.getBoundingClientRect();"
    "var b=box();b.style.left=r.left+'px';b.style.top=r.top+'px';b.style.width=r.width+'px';b.style.height=r.height+'px';b.style.display='block'}"
    "function click(e){if(!on)return;e.preventDefault();e.stopPropagation();var el=e.target;"
    "var label=el.tagName.toLowerCase()+(el.id?'#'+el.id:'')+(el.className&&typeof el.className==='string'?"
    "'.'+el.className.trim().split(/\\s+/).slice(0,2).join('.'):'');"
    "var html=(el.outerHTML||'').slice(0,600);var text=(el.textContent||'').trim().slice(0,120);"
    "try{parent.postMessage({__harnessPicked:1,selector:sel(el),label:label,text:text,html:html},'*')}catch(x){}"
    "setMode(false)}"
    "function setMode(v){on=v;document.body.style.cursor=v?'crosshair':'';if(!v&&ov){ov.style.display='none'}}"
    "document.addEventListener('mousemove',move,true);document.addEventListener('click',click,true);"
    "window.addEventListener('message',function(e){var d=e.data;if(d&&d.__harnessPick)setMode(!!d.on)});"
    "})();</script>"
)


def _inject_devtools(html: bytes) -> bytes:
    text = html.decode("utf-8", "replace")
    lower = text.lower()
    i = lower.find("<head>")
    if i != -1:
        pos = i + len("<head>")
    else:  # no <head> — drop it at the very top
        pos = 0
    return (text[:pos] + _DEVTOOLS_SNIPPET + _PICKER_SNIPPET + text[pos:]).encode("utf-8")


def _inject_preview(html: bytes, name: str) -> bytes:
    """For proxied live previews: a <base> so relative URLs resolve under the proxy
    prefix, plus the devtools capture agent (same-origin, so it works)."""
    text = html.decode("utf-8", "replace")
    base = f'<base href="/preview/{name}/">'
    lower = text.lower()
    i = lower.find("<head>")
    pos = i + len("<head>") if i != -1 else 0
    return (text[:pos] + base + _DEVTOOLS_SNIPPET + _PICKER_SNIPPET + text[pos:]).encode("utf-8")


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
        "scaffold": spec.scaffold, "run": spec.run,
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
            entry = {"name": name, "command": cmd}
            if (v or {}).get("needs_server"):
                entry["needs_server"] = True
            if (v or {}).get("allow_failure"):
                entry["allow_failure"] = True
            checks.append(entry)
    if checks:
        doc["verification"] = checks
    if data.get("run"):
        doc["run"] = str(data["run"]).strip()

    if data.get("scaffold"):
        doc["scaffold"] = str(data["scaffold"]).strip()
    parse_spec(doc)  # raises SpecError if invalid
    Path(specs_dir).mkdir(parents=True, exist_ok=True)
    path = os.path.join(specs_dir, f"{_slug(doc['name'])}.yaml")
    Path(path).write_text(yaml.safe_dump(doc, sort_keys=False, width=88))
    return path


_MTIME_SKIP = {".studio", ".git", "node_modules", "__pycache__", ".pytest_cache"}


def workspace_mtime(workspace_dir: str) -> float:
    """Newest source-file mtime in a workspace (for hot-reloading the preview).
    Skips caches/VCS so a rebuild's file writes register but noise doesn't."""
    root = Path(workspace_dir)
    if not root.is_dir():
        return 0.0
    newest = 0.0
    for dp, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _MTIME_SKIP]
        for fn in filenames:
            try:
                m = os.path.getmtime(os.path.join(dp, fn))
            except OSError:
                continue
            if m > newest:
                newest = m
    return round(newest, 3)


def list_artifacts(workspaces_dir: str) -> list[dict]:
    base = Path(workspaces_dir)
    out: list[dict] = []
    if not base.is_dir():
        return out
    for d in sorted(base.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        files = [p for p in d.rglob("*")
                 if p.is_file() and ".studio" not in p.relative_to(d).parts]
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
            dirs[:] = [d for d in dirs if d not in _SKIP_DIRS and d != ".studio"]
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


def _check_to_dict(c) -> dict:
    return {"name": c.name, "command": c.command, "needs_server": c.needs_server,
            "allow_failure": c.allow_failure, "parallel": c.parallel}


def _default_checks(kind: str, workspace: str | None = None) -> list[dict]:
    """Stack-aware default verification suite (incl. a server-backed smoke check)."""
    from harness import stacks
    return [_check_to_dict(c) for c in stacks.default_checks(workspace, kind)]


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


async def _run_engine_turn(engine, instruction: str):
    """Run a single engine turn against an existing workspace. Returns (tokens, text)."""
    async with engine:
        text = await engine.send(_ITERATE_PROMPT.format(instruction=instruction), echo=True)
        return getattr(engine, "total_tokens", 0), text


_IMG_EXT = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp", "image/gif": ".gif"}


def save_reference_image(workspace_dir: str, data_url: str) -> str:
    """Save an uploaded data-URL (or raw base64) screenshot under <ws>/.studio. Returns path."""
    m = re.match(r"data:(image/[\w.+-]+);base64,(.*)", data_url or "", re.DOTALL)
    if m:
        mime, b64 = m.group(1), m.group(2)
    else:
        mime, b64 = "image/png", (data_url or "")
    raw = base64.b64decode(b64)
    d = os.path.join(workspace_dir, ".studio")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "reference" + _IMG_EXT.get(mime, ".png"))
    with open(path, "wb") as fh:
        fh.write(raw)
    return path


def _apply_reference(config, params: dict) -> None:
    """If a reference image was uploaded, point the config at it + any vision model."""
    if not params.get("image"):
        return
    config.reference_image = save_reference_image(config.workspace, params["image"])
    config.vision_model = params.get("vision_model") or None
    config.vision_base_url = params.get("vision_base_url") or None
    config.coder_multimodal = bool(params.get("coder_multimodal"))
    config.visual_check = bool(params.get("visual_check"))


def _snapshot(workspace_dir: str, label: str, instruction: str = "") -> None:
    """Best-effort per-turn snapshot for the Studio history/diff (never fails a build)."""
    try:
        from harness import versions
        meta = versions.snapshot(workspace_dir, label=label, instruction=instruction)
        print(f"[version] saved v{meta['id']} ({label})")
    except Exception as exc:  # snapshots are a convenience, not a gate
        print(f"[version] snapshot skipped: {type(exc).__name__}: {exc}")


def _git_commit(workspace_dir: str, message: str) -> None:
    """Best-effort git commit after a green turn (free undo/history; never fails a build)."""
    try:
        from harness import autocommit
        res = autocommit.commit_all(workspace_dir, message)
        if res.get("sha"):
            print(f"[git] committed {res['sha']} — {message}")
        elif res.get("nochange"):
            print("[git] no file changes to commit")
        elif res.get("reason"):
            print(f"[git] skipped: {res['reason']}")
    except Exception as exc:  # auto-commit is a convenience, not a gate
        print(f"[git] commit skipped: {type(exc).__name__}: {exc}")


def run_tests(workspace_dir: str, checks: list | None = None) -> dict:
    """Run the verification suite for a workspace on demand (for the Run tests button)."""
    from harness.spec import Check
    meta = studio_meta(workspace_dir)
    if checks is None:
        checks = meta.get("checks") or _default_checks(meta.get("kind", "frontend"), workspace_dir)
    suite = [Check(name=c["name"], command=c["command"], cwd=workspace_dir,
                   needs_server=bool(c.get("needs_server")),
                   allow_failure=bool(c.get("allow_failure")),
                   parallel=bool(c.get("parallel"))) for c in checks]
    if not suite:
        return {"ok": True, "results": [], "note": "no checks defined"}
    if any(c.needs_server for c in suite):
        from harness import fullstack
        report = fullstack.verify_checks(suite, workspace_dir,
                                         run_command=meta.get("run"), stop_on_failure=False)
    else:
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
        self.tok_per_sec = 0.0   # live decode rate from the local engine's stream
        self.token_budget = None
        self.deadline = None
        self.control = BuildControl()
        self.engine = None      # live engine (set during iterate) so cancel can kill it
        self.deploy_url = ""    # set by one-shot build→ship→deploy
        self.cancelled = False

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
        self.runtime = RuntimeManager()  # the live dev server for full-stack preview
        self.profile_path = os.path.join(os.path.abspath(workspaces_dir), ".profile.json")
        self.templates_dir = os.path.join(os.path.abspath(workspaces_dir), ".templates")
        self.usage_path = os.path.join(os.path.abspath(workspaces_dir), ".usage.jsonl")
        self.session_path = os.path.join(os.path.abspath(workspaces_dir), ".session.json")
        self.queue: list = []                 # (Job, params) pending builds
        self.queue_lock = threading.Lock()
        self._queue_worker = None
        # Local-model leaderboard: status + ranked rows from the last/current run.
        self.leaderboard = {"status": "idle", "results": [], "current": None,
                            "base_url": "", "done": 0, "total": 0}
        self._lb_thread = None

    def design_profile(self) -> dict:
        from harness import profile
        return profile.load(self.profile_path)

    def load_session(self) -> dict:
        """The last active Studio session (project + engine), so a reopened/reclaimed
        container resumes where you left off."""
        try:
            with open(self.session_path, encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def save_session(self, patch: dict) -> dict:
        """Merge `patch` into the persisted session and return the result."""
        data = self.load_session()
        data.update({k: v for k, v in (patch or {}).items() if v is not None})
        # Drop a project that no longer exists on disk (stale after a cleanup).
        proj = data.get("project")
        if proj and not os.path.isdir(os.path.join(self.workspaces_dir, os.path.basename(str(proj)))):
            data.pop("project", None)
        try:
            tmp = self.session_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.replace(tmp, self.session_path)
        except OSError:
            pass
        return data

    def start_leaderboard(self, params: dict) -> dict:
        """Kick off a background benchmark of local models. Returns the initial state."""
        if self.leaderboard["status"] == "running":
            return {"error": "a benchmark is already running"}
        if self.busy():
            return {"error": "a build is running — benchmark when it finishes "
                             "(they'd fight over the model server)"}
        base_url = str(params.get("base_url") or "").strip()
        if not base_url:
            return {"error": "base_url is required"}
        models = params.get("models") or []
        if not models:
            from harness import modelinfo
            info = modelinfo.list_models(base_url)
            # Only models that can plausibly drive the build loop are worth timing.
            models = [m["id"] for m in info.get("models", []) if m["tool_calling"] != "weak"]
        if not models:
            return {"error": "no candidate models found on the server"}
        self.leaderboard = {"status": "running", "results": [], "current": None,
                            "base_url": base_url, "done": 0, "total": len(models)}
        self._lb_thread = threading.Thread(
            target=self._leaderboard_worker, args=(base_url, list(models)), daemon=True)
        self._lb_thread.start()
        return dict(self.leaderboard)

    def _leaderboard_worker(self, base_url: str, models: list[str]) -> None:
        from harness import leaderboard
        rows = []

        def on_result(row):
            rows.append(row)
            self.leaderboard["results"] = leaderboard.rank(list(rows))
            self.leaderboard["done"] = len(rows)

        try:
            for model in models:
                self.leaderboard["current"] = model
                row = leaderboard.run_model(base_url, model, root=self.workspaces_dir)
                on_result(row)
        finally:
            self.leaderboard["current"] = None
            self.leaderboard["status"] = "done"

    def _await_diff_approval(self, job: "Job", timeout: float = 900.0) -> bool:
        """Pause after a green iterate and let the user approve the diff before it's
        committed. Returns True to commit, False to discard. Defaults to commit on an
        empty diff or a timeout (never silently lose work)."""
        from harness import autocommit
        diff = autocommit.working_diff(job.workspace)
        if not diff.strip():
            return True
        job.approve_event.clear()
        job.decision = None
        job.pending = {"kind": "diff", "payload": {
            "diff": diff, "workspace": os.path.basename(job.workspace)}}
        job.log("[review] waiting for sign-off on the change diff…")
        got = job.approve_event.wait(timeout)
        job.pending = None
        if not got or job.decision is None:
            return True
        return bool(getattr(job.decision, "approved", False))

    @staticmethod
    def _meter_cb(job: "Job"):
        """A callback the local engine pulses with live (tokens, rate, elapsed) so the
        UI can show a token/sec meter during a local build."""
        def cb(tokens, rate, elapsed):
            job.tok_per_sec = round(float(rate or 0), 1)
        return cb

    def _ship_and_deploy(self, job: "Job", provider: str) -> None:
        """After a green build: package (Ship it) and deploy to `provider`, into the
        same job log. Sets job.deploy_url so the UI can surface the live link."""
        from harness import deploy, ship
        name = os.path.basename(job.workspace)
        print(f"\n======== ship → deploy ({provider}) ========")
        written = ship.write_export(job.workspace, name=name)
        print("ship: wrote " + (", ".join(written) if written else "(all present)"))
        try:
            res = deploy.run(provider, job.workspace, name or "app")
        except Exception as exc:
            print(f"deploy error: {type(exc).__name__}: {exc}")
            return
        job.deploy_url = res.get("url") or ""
        if res.get("ok"):
            print(f"deploy: live at {res['url']}")
        elif res.get("ready") is False:
            print(f"deploy: {res.get('reason', 'CLI not found')}")
            for c in res.get("commands", []):
                print("  " + c)
            print(f"  → will be live at {res.get('url', '')}")
        else:
            print(f"deploy failed: {res.get('reason') or res.get('error') or 'unknown'}")

    def _record_usage(self, job: "Job", kind: str, run_type: str,
                      engine: str = "", model: str = "") -> None:
        from harness import usage
        import datetime
        usage.record(self.usage_path, {
            "ts": datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "name": os.path.basename(job.workspace), "kind": kind, "type": run_type,
            "engine": engine, "model": model,
            "status": job.status, "tokens": int(job.tokens or 0),
            "elapsed": round(float(job.elapsed or 0), 1)})

    def _learn_profile(self, workspace: str) -> None:
        """After a successful build/iterate, absorb the app's palette into the
        profile so the look converges over time (only when auto-learn is on)."""
        from harness import profile
        prof = profile.load(self.profile_path)
        if not prof.get("auto_learn", True):
            return
        before = list(prof.get("palette") or [])
        profile.learn_from_workspace(prof, workspace)
        if prof.get("palette") != before:
            profile.save(self.profile_path, prof)

    def busy(self) -> bool:
        return self._lock.locked()

    def _prepare_job(self, params: dict) -> Job:
        """Resolve the spec/workspace for a build and create its Job (not started)."""
        if params.get("resume"):
            name = os.path.basename(params.get("workspace") or "")
            workspace = os.path.abspath(os.path.join(self.workspaces_dir, name))
        else:
            # Freeform (Studio): a natural-language prompt with no spec file —
            # synthesize and persist a spec so the project is first-class.
            if not params.get("spec") and params.get("prompt"):
                sc = scaffolds.get(params.get("scaffold") or "")
                params["spec"] = save_spec(self.specs_dir, {
                    "name": params.get("name") or "app",
                    "kind": sc.kind if sc else (params.get("kind") or "frontend"),
                    "language": params.get("language") or "html-css-js",
                    "description": params["prompt"],
                    "run": params.get("run_command") or (sc.run if sc else None),
                    "scaffold": sc.name if sc else None,
                    "verification": [] if sc else (
                        params.get("verification") or _default_checks(params.get("kind") or "frontend")),
                })
            spec_path = params["spec"]
            workspace = os.path.abspath(
                params.get("workspace") or os.path.join(self.workspaces_dir, Path(spec_path).stem)
            )
        job = Job(str(int(time.time() * 1000) + len(self.jobs)), workspace)
        self.jobs[job.id] = job
        return job

    def start(self, params: dict) -> Job:
        job = self._prepare_job(params)
        self.current = job
        threading.Thread(target=self._run, args=(job, params), daemon=True).start()
        return job

    def enqueue(self, params: dict) -> Job:
        """Add a build to the queue; a worker runs queued builds back-to-back."""
        job = self._prepare_job(params)
        job.status = "queued"
        with self.queue_lock:
            self.queue.append((job, params))
            if self._queue_worker is None or not self._queue_worker.is_alive():
                self._queue_worker = threading.Thread(target=self._drain_queue, daemon=True)
                self._queue_worker.start()
        return job

    def _drain_queue(self) -> None:
        while True:
            with self.queue_lock:
                if not self.queue:
                    return
                job, params = self.queue.pop(0)
            self.current = job
            try:
                self._run(job, params)   # serializes on self._lock; sets job.done
            except Exception as exc:
                job.status = "error"
                job.log(f"error: {type(exc).__name__}: {exc}")
                job.done.set()

    def queue_status(self) -> dict:
        with self.queue_lock:
            pending = [{"id": j.id, "name": os.path.basename(j.workspace)} for j, _ in self.queue]
        cur = self.current
        running = self.busy() and cur is not None and not cur.done.is_set()
        return {"running": bool(running),
                "current": (os.path.basename(cur.workspace) if running else None),
                "current_status": (cur.status if running else None),
                "pending": pending}

    def iterate(self, params: dict) -> Job:
        """Lovable-style conversational edit: one engine turn on an existing app."""
        name = os.path.basename(params.get("workspace") or "")
        workspace = os.path.abspath(os.path.join(self.workspaces_dir, name))
        job = Job(str(int(time.time() * 1000)), workspace)
        self.jobs[job.id] = job
        self.current = job
        self.save_session({"project": name, "engine": params.get("engine"),
                           "model": params.get("model"), "base_url": params.get("base_url")})
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
        _iter_start = time.monotonic()
        instruction = str(params.get("instruction", "")).strip()
        raw_instruction = instruction  # user's words, for the conversation log
        if not instruction:
            job.status = "error"
            print("error: instruction is required")
            return

        # "Point & edit": the user clicked an element in the live preview, so anchor
        # the change to that element (selector + the actual markup they pointed at).
        tgt = params.get("target") or {}
        if isinstance(tgt, dict) and (tgt.get("selector") or tgt.get("html")):
            label = str(tgt.get("label") or tgt.get("selector") or "the selected element")
            block = (f"\n\nThe user pointed at this element in the live preview — apply the "
                     f"change to it (and closely related markup/styles):\n"
                     f"- selector: {tgt.get('selector', '')}\n- label: {label}")
            if tgt.get("text"):
                block += f"\n- text: {str(tgt['text'])[:160]}"
            if tgt.get("html"):
                block += f"\n- current markup:\n{str(tgt['html'])[:600]}"
            instruction = instruction + block
            # Ship the full content of the file most relevant to the pointed element,
            # so the model edits the right place without a read round.
            try:
                from harness import repomap
                hint = " ".join(str(tgt.get(k, "")) for k in ("selector", "label", "text", "html"))
                fb = repomap.focus_block(job.workspace, hint)
                if fb:
                    instruction = instruction + "\n\n" + fb
            except Exception:
                pass

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
        config.meter_cb = self._meter_cb(job)  # live token/sec meter

        # Reference UI image (optional): describe it (vision) or stage it (direct),
        # then fold the design context into the change instruction.
        _apply_reference(config, params)
        if config.reference_image:
            from harness.agent import _resolve_design
            brief = asyncio.run(_resolve_design(config, job.workspace, True))
            if brief:
                instruction = f"{instruction}\n\nReference design context:\n{brief}"

        # Fold the user's house style into the change so iterations stay on-brand.
        from harness import profile as profmod
        note = profmod.render(self.design_profile())
        if note:
            instruction = f"{instruction}\n\n## House style (honor it)\n{note}"

        # Conversation memory: the model sees what was asked before and how it went,
        # so each change builds on the last instead of starting cold.
        from harness import chatlog
        convo = chatlog.render(job.workspace)
        if convo:
            instruction = f"{instruction}\n\n{convo}"

        from harness.engines.base import make_engine
        engine = make_engine(spec, config)
        job.engine = engine  # so a Stop/cancel can kill the in-flight turn

        print(f"[iterate] {provider} · {model or '(default)'} — applying change…")
        print(f"› {instruction}")
        try:
            from harness import speculative
            with speculative.watch(job.workspace):
                tokens, _text = asyncio.run(_run_engine_turn(engine, instruction))
        except Exception as exc:
            if job.cancelled:
                job.status = "cancelled"
                print("RESULT: CANCELLED")
                return
            raise
        finally:
            job.engine = None
        job.tokens = tokens
        # Verify against the project's gate so the preview reflects a passing app.
        result = run_tests(job.workspace, meta.get("checks"))
        for r in result["results"]:
            print(f"[{'PASS' if r['ok'] else 'FAIL'}] {r['name']}")
        job.status = "passed" if result["ok"] else "failed"
        job.elapsed = round(time.monotonic() - _iter_start, 1)
        print(f"RESULT: {job.status.upper()} · {tokens} tokens")
        failed = ", ".join(r["name"] for r in result["results"] if not r["ok"])
        chatlog.record_change(job.workspace, raw_instruction, job.status,
                              detail=f"failing: {failed}" if failed else "")
        _snapshot(job.workspace, "Change", instruction)
        if result["ok"]:
            msg = f"Change: {instruction.splitlines()[0][:72]}"
            if params.get("review_diff") and self._await_diff_approval(job):
                _git_commit(job.workspace, msg)
                self._learn_profile(job.workspace)
            elif params.get("review_diff"):
                from harness import autocommit
                autocommit.discard_changes(job.workspace)
                job.status = "discarded"
                print("RESULT: change DISCARDED by reviewer — workspace rolled back")
            else:
                _git_commit(job.workspace, msg)
                self._learn_profile(job.workspace)
        self._record_usage(job, kind, "iterate", engine=provider, model=model)

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
        sc = scaffolds.get(spec.scaffold or "")
        run_cmd = params.get("run_command") or spec.run or (sc.run if sc else None)
        meta_checks = sc.checks if sc else [_check_to_dict(c) for c in spec.checks]
        write_studio_meta(job.workspace, {
            "kind": spec.kind,
            "engine": params.get("engine", "anthropic"),
            "model": params.get("model") or "",
            "base_url": params.get("base_url"),
            "run": run_cmd,
            "scaffold": spec.scaffold,
            "checks": meta_checks,
        })
        self.save_session({"project": os.path.basename(job.workspace),
                           "engine": params.get("engine"), "model": params.get("model"),
                           "base_url": params.get("base_url")})
        if params.get("check_only"):
            report = run_tests(job.workspace, [_check_to_dict(c) for c in spec.checks])
            for r in report.get("results", []):
                print(f"[{'PASS' if r['ok'] else ('SKIP' if r.get('skipped') else 'FAIL')}] {r['name']}")
            job.status = "passed" if report["ok"] else "failed"
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
            run_command=run_cmd,
            scaffold=spec.scaffold,
            plan=bool(params.get("plan")),
            max_milestones=int(params.get("max_milestones") or 6),
            multi=bool(params.get("multi")),
            multi_parallel=not bool(params.get("multi_sequential")),
            security_scan=bool(params.get("security_scan")),
            patch_review=bool(params.get("patch_review")),
            design_profile=self.design_profile(),
        )
        config.meter_cb = self._meter_cb(job)  # live token/sec meter
        job.token_budget = params.get("token_budget")
        job.deadline = params.get("deadline")
        _apply_reference(config, params)  # reference UI image + optional vision model
        from harness.agent import build

        gate = ServerApproval(job) if (approve_plan or approve_build) else None

        if config.reference_image:
            print(f"reference image: {os.path.basename(config.reference_image)}"
                  f"{' · vision=' + config.vision_model if config.vision_model else ' · direct (multimodal coder)'}")
        print(f"engine={provider} model={model or '(unset)'} kind={spec.kind}")
        from harness import speculative
        with speculative.watch(job.workspace):
            result = asyncio.run(build(spec, config, echo=True, approval=gate,
                                       on_progress=on_progress, control=job.control))
        job.status = "passed" if result.ok else "failed"
        job.tokens, job.elapsed = result.tokens_used, result.elapsed_seconds
        print(f"RESULT: {job.status.upper()} ({result.stop_reason}) after {result.rounds} round(s)")
        print(f"Telemetry: {result.tokens_used} tokens · {result.elapsed_seconds:.1f}s")
        _snapshot(job.workspace, "Initial build", spec.description)
        if result.ok:
            _git_commit(job.workspace, f"Initial build: {spec.name}")
            self._learn_profile(job.workspace)

        # Visual-diff refinement: converge the look toward the reference image.
        if result.ok and config.visual_check and config.reference_image and config.vision_engine():
            from harness.agent import visual_refine
            vr, vt = asyncio.run(visual_refine(spec, config, echo=True, on_progress=on_progress))
            if vr:
                job.tokens += vt
                _snapshot(job.workspace, "Visual match", "matched reference image")
                print(f"Visual refinement: {vr} repair round(s), {vt} tokens")

        # One-shot: build → ship → deploy, in the same job/log.
        if result.ok and params.get("then_deploy"):
            self._ship_and_deploy(job, str(params.get("then_deploy")))

        self._record_usage(job, spec.kind, "build", engine=provider, model=model)


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
            if path == "/api/usage":
                from harness import pricing, usage
                return self._json(usage.summary(
                    usage.load(console.usage_path), cost_of=pricing.cost_of))
            if path == "/api/queue":
                return self._json(console.queue_status())
            if path == "/api/scaffolds":
                return self._json({"scaffolds": scaffolds.list_scaffolds()})
            if path == "/api/profile":
                return self._json(console.design_profile())
            if path == "/api/templates":
                from harness import templates
                return self._json({"templates": templates.list_templates(console.templates_dir)})
            if path == "/api/templates/get":
                from harness import templates
                t = templates.load(console.templates_dir, q.get("name", [""])[0])
                return self._json(t if t else {"error": "not found"}, 200 if t else 404)
            if path == "/api/share/export":
                from harness import share
                bundle = share.export_bundle(console.templates_dir, console.design_profile())
                data = json.dumps(bundle, indent=2).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Disposition",
                                 'attachment; filename="agent1-harness-bundle.json"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/local-models":
                from harness import doctor
                return self._json({"servers": doctor.detect_local_servers()})
            if path == "/api/model-picker":
                from harness import modelinfo
                base = (q.get("base_url", [""])[0] or "").strip()
                return self._json(modelinfo.list_models(base))
            if path == "/api/leaderboard":
                return self._json(console.leaderboard)
            if path == "/api/session":
                return self._json({"session": console.load_session()})
            if path == "/api/report":
                from harness import timeline
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json({"report": timeline.load(root)})
            if path == "/api/chat":
                from harness import chatlog
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json({"turns": chatlog.history(root, limit=50)})
            if path == "/api/git-history":
                from harness import autocommit
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json({"managed": autocommit.manages(root),
                                   "commits": autocommit.history(root)})
            if path == "/api/doctor":
                from harness import doctor
                state = doctor.probe()
                return self._json({"state": state, "recommend": doctor.recommend(state)})
            if path == "/api/persona":
                from harness import personas
                kind = (q.get("kind", ["fullstack"])[0] or "fullstack").strip()
                return self._json({"kind": kind, "prompt": personas.system_prompt(kind)})
            if path == "/api/screenshot":
                from harness import screenshot
                name = os.path.basename(q.get("dir", [""])[0])
                index = os.path.join(self._ws_root(name), "index.html")
                if not os.path.isfile(index):
                    return self._json({"error": "no index.html to screenshot"}, 404)
                png = screenshot.capture(index)
                if not png:
                    return self._json({"error": "no headless browser available"}, 503)
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Content-Length", str(len(png)))
                self.end_headers()
                self.wfile.write(png)
                return
            if path == "/api/current":
                j = console.current
                return self._json({"job": j.id if j else None,
                                   "status": j.status if j else None,
                                   "workspace": j.workspace if j else None,
                                   "name": os.path.basename(j.workspace) if j else None,
                                   "pending": j.pending if j else None,
                                   "tokens": j.tokens if j else 0,
                                   "elapsed": round(j.elapsed, 1) if j else 0.0,
                                   "tok_per_sec": round(getattr(j, "tok_per_sec", 0.0), 1) if j else 0.0,
                                   "token_budget": j.token_budget if j else None,
                                   "deadline": j.deadline if j else None,
                                   "deploy_url": getattr(j, "deploy_url", "") if j else "",
                                   "busy": console.busy()})
            if path == "/api/workspace":
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json(workspace_tree(root))
            if path == "/api/workspace/mtime":
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json({"mtime": workspace_mtime(root)})
            if path == "/api/workspace/file":
                root = self._ws_root(q.get("dir", [""])[0])
                try:
                    return self._json({"content": read_workspace_file(root, q.get("file", [""])[0])})
                except (FileNotFoundError, OSError):
                    return self._json({"error": "not found"}, 404)
            if path == "/api/versions":
                from harness import versions
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json({"versions": versions.list_versions(root)})
            if path == "/api/diff":
                from harness import versions
                root = self._ws_root(q.get("dir", [""])[0])
                try:
                    frm = int(q.get("from", ["0"])[0])
                    to_raw = q.get("to", [""])[0]
                    to = int(to_raw) if to_raw and to_raw != "current" else None
                    return self._json({"files": versions.diff(root, frm, to)})
                except (ValueError, OSError) as e:
                    return self._json({"error": str(e)}, 400)
            if path == "/api/ship":
                from harness import ship
                root = self._ws_root(q.get("dir", [""])[0])
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                meta = studio_meta(root)
                files = ship.export_files(root, name=os.path.basename(root),
                                          run_command=meta.get("run"))
                from harness import stacks
                return self._json({"stack": stacks.detect_stack(root), "files": files})
            if path == "/api/deploy/providers":
                from harness import deploy
                return self._json({"providers": deploy.list_providers()})
            if path == "/api/deploy/status":
                from harness import deploy
                return self._json(deploy.check_live(q.get("url", [""])[0]))
            if path == "/api/deploy/history":
                from harness import deploy
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json({"history": deploy.history(root)})
            if path == "/api/deploy/settings":
                from harness import deploy
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json(deploy.settings(root))
            if path == "/api/deploy/previews":
                from harness import deploy
                root = self._ws_root(q.get("dir", [""])[0])
                return self._json({"previews": deploy.previews(root)})
            if path == "/api/deploy/plan":
                from harness import deploy
                name = os.path.basename(q.get("dir", [""])[0])
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                provider = q.get("provider", ["fly"])[0]
                return self._json(deploy.plan(provider, root, name or "app"))
            if path == "/api/ship/zip":
                from harness import ship
                name = os.path.basename(q.get("dir", [""])[0])
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                meta = studio_meta(root)
                data = ship.zip_bytes(root, name=name or "app", run_command=meta.get("run"))
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{name or "app"}.zip"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path == "/api/runtime/status":
                name = os.path.basename(q.get("dir", [""])[0])
                svc = console.runtime.for_workspace(name)
                ws = self._ws_root(name)
                det = detect_command(ws) if os.path.isdir(ws) else None
                return self._json({
                    "running": bool(svc), "info": svc.info() if svc else None,
                    "detected": (det or {}).get("command"),
                })
            if path == "/api/runtime/logs":
                name = os.path.basename(q.get("dir", [""])[0])
                svc = console.runtime.for_workspace(name)
                since = int(q.get("since", ["0"])[0] or 0)
                if not svc:
                    return self._json({"lines": [], "next": 0, "status": "stopped"})
                lines, nxt = svc.logs(since)
                return self._json({"lines": lines, "next": nxt, "status": svc.status})
            if path.startswith("/preview/"):
                rest = path[len("/preview/"):]
                if u.query:
                    rest += "?" + u.query
                return self._preview(rest, "GET")
            if path.startswith("/api/jobs/") and path.endswith("/events"):
                return self._sse(path.split("/")[3])
            self._send(404, b'{"error":"not found"}')

        def do_POST(self):
            u = urlparse(self.path)
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length) if length else b""
            if u.path.startswith("/preview/"):  # proxy app POSTs (raw body, not JSON)
                rest = u.path[len("/preview/"):] + (("?" + u.query) if u.query else "")
                return self._preview(rest, "POST", raw)
            body = json.loads(raw or b"{}")
            if u.path == "/api/runtime/start":
                name = os.path.basename(body.get("workspace") or "")
                ws = os.path.join(os.path.abspath(console.workspaces_dir), name)
                if not os.path.isdir(ws):
                    return self._json({"error": "unknown workspace"}, 404)
                cmd = (body.get("command") or "").strip() or (detect_command(ws) or {}).get("command")
                if not cmd:
                    return self._json({"error": "no run command (none detected)"}, 400)
                svc = console.runtime.start(ws, cmd, ready_path=body.get("ready_path", "/"))
                return self._json({"ok": True, "info": svc.info()})
            if u.path == "/api/runtime/stop":
                console.runtime.stop()
                return self._json({"ok": True})
            if u.path == "/api/builds":
                if not body.get("spec") and not body.get("resume") and not body.get("prompt"):
                    return self._json({"error": "spec, prompt, or resume is required"}, 400)
                try:
                    job = console.start(body)
                except SpecError as e:
                    return self._json({"error": str(e)}, 400)
                return self._json({"id": job.id, "workspace": job.workspace})
            if u.path == "/api/ship":
                from harness import ship
                name = os.path.basename(body.get("workspace") or "")
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                meta = studio_meta(root)
                written = ship.write_export(root, name=name or "app",
                                            run_command=meta.get("run"),
                                            overwrite=bool(body.get("overwrite")))
                return self._json({"ok": True, "written": written})
            if u.path == "/api/profile":
                from harness import profile
                return self._json(profile.save(console.profile_path, body))
            if u.path == "/api/templates":  # save current project as a template
                from harness import templates
                name = os.path.basename(body.get("workspace") or "")
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                meta = studio_meta(root)
                t = templates.from_workspace(
                    root, str(body.get("name") or name), str(body.get("description") or ""),
                    kind=meta.get("kind", "frontend"), run=meta.get("run") or "",
                    scaffold=meta.get("scaffold"), profile=console.design_profile())
                return self._json(templates.save(console.templates_dir, t))
            if u.path == "/api/share/import":
                from harness import share
                res = share.import_bundle(
                    console.templates_dir, console.profile_path, body.get("bundle") or body,
                    apply_profile=body.get("apply_profile", True) is not False)
                return self._json(res)
            if u.path == "/api/templates/import":
                from harness import templates
                t = body.get("template") or body
                try:
                    return self._json(templates.save(console.templates_dir, t))
                except ValueError as e:
                    return self._json({"error": str(e)}, 400)
            if u.path == "/api/templates/use":
                from harness import profile, templates
                t = templates.load(console.templates_dir, str(body.get("name") or ""))
                if not t:
                    return self._json({"error": "template not found"}, 404)
                new = os.path.basename(str(body.get("new_name") or t.get("name") or "app"))
                root = os.path.join(os.path.abspath(console.workspaces_dir), new)
                settings = templates.apply(t, root)
                write_studio_meta(root, {
                    "kind": settings["kind"], "engine": "claude-cli", "model": "",
                    "run": settings["run"], "scaffold": settings["scaffold"], "checks": []})
                if settings.get("profile"):
                    profile.save(console.profile_path, settings["profile"])
                return self._json({"ok": True, "workspace": new, "settings": settings})
            if u.path == "/api/github/export":
                from harness import ghexport
                name = os.path.basename(body.get("workspace") or "")
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                try:
                    return self._json(ghexport.export(
                        root, str(body.get("name") or name or "app"),
                        private=body.get("private", True) is not False,
                        ci=body.get("ci", True) is not False))
                except Exception as e:
                    return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            if u.path == "/api/deploy":
                from harness import deploy
                name = os.path.basename(body.get("workspace") or "")
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                provider = str(body.get("provider") or "fly")
                try:
                    return self._json(deploy.run(provider, root, name or "app"))
                except Exception as e:
                    return self._json({"ok": False, "error": f"{type(e).__name__}: {e}"}, 500)
            if u.path == "/api/deploy/preview":
                from harness import deploy
                name = os.path.basename(body.get("workspace") or "")
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                return self._json(deploy.preview(
                    str(body.get("provider") or "cloudflare"), root, name or "app",
                    label=str(body.get("label") or "preview")))
            if u.path == "/api/deploy/preview/destroy":
                from harness import deploy
                name = os.path.basename(body.get("workspace") or "")
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                return self._json(deploy.destroy_preview(
                    str(body.get("provider") or "cloudflare"), root, name or "app",
                    str(body.get("id") or "")))
            if u.path == "/api/deploy/secrets":
                from harness import deploy
                name = os.path.basename(body.get("workspace") or "")
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                return self._json(deploy.push_secrets(
                    str(body.get("provider") or "fly"), root, name or "app"))
            if u.path == "/api/deploy/domain":
                from harness import deploy
                name = os.path.basename(body.get("workspace") or "")
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                return self._json(deploy.add_domain(
                    str(body.get("provider") or "fly"), root, name or "app",
                    str(body.get("domain") or "")))
            if u.path in ("/api/deploy/logs", "/api/deploy/rollback"):
                from harness import deploy
                name = os.path.basename(body.get("workspace") or "")
                root = self._ws_root(name)
                if not os.path.isdir(root):
                    return self._json({"error": "unknown workspace"}, 404)
                action = "logs" if u.path.endswith("logs") else "rollback"
                provider = str(body.get("provider") or "fly")
                return self._json(deploy.run_action(provider, action, root, name or "app"))
            if u.path == "/api/queue":
                builds = body.get("builds")
                if not isinstance(builds, list) or not builds:
                    builds = [body]   # a single build params object
                jobs = []
                for p in builds:
                    if not (p.get("spec") or p.get("prompt") or p.get("resume")):
                        continue
                    try:
                        j = console.enqueue(p)
                        jobs.append({"id": j.id, "name": os.path.basename(j.workspace)})
                    except SpecError as e:
                        return self._json({"error": str(e)}, 400)
                return self._json({"queued": jobs, "status": console.queue_status()})
            if u.path == "/api/local-test":
                from harness import localcheck
                return self._json(localcheck.test_connection(
                    str(body.get("base_url") or ""), str(body.get("model") or "")))
            if u.path == "/api/leaderboard/run":
                res = console.start_leaderboard(body)
                return self._json(res, 409 if res.get("error") else 200)
            if u.path == "/api/session":
                return self._json({"session": console.save_session(body if isinstance(body, dict) else {})})
            if u.path == "/api/git-undo":
                from harness import autocommit
                root = self._ws_root(str(body.get("workspace") or body.get("dir") or ""))
                res = autocommit.undo_last(root)
                return self._json(res, 200 if res.get("ok") else 409)
            if u.path == "/api/git-restore":
                from harness import autocommit
                root = self._ws_root(str(body.get("workspace") or body.get("dir") or ""))
                res = autocommit.restore(root, str(body.get("sha") or ""))
                return self._json(res, 200 if res.get("ok") else 409)
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
            if u.path == "/api/restore":
                from harness import versions
                name = os.path.basename(body.get("workspace") or "")
                root = os.path.join(os.path.abspath(console.workspaces_dir), name)
                if console.busy():
                    return self._json({"error": "a build is running"}, 409)
                try:
                    meta = versions.restore(root, int(body.get("version")))
                    return self._json({"ok": True, "version": meta})
                except (FileNotFoundError, ValueError, TypeError) as e:
                    return self._json({"error": str(e)}, 400)
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
                    job.cancelled = True
                    eng = getattr(job, "engine", None)
                    if eng is not None and hasattr(eng, "terminate"):
                        try:
                            eng.terminate()  # kill the in-flight CLI turn immediately
                        except Exception:
                            pass
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
            data = Path(target).read_bytes()
            suffix = Path(target).suffix
            if suffix == ".html" and "__dev=1" in urlparse(self.path).query:
                data = _inject_devtools(data)
            self._send(200, data, mime.get(suffix, "text/plain"))

        def _preview(self, rest, method="GET", body=b""):
            parts = rest.split("/", 1)
            name = os.path.basename(parts[0])
            rel = parts[1] if len(parts) > 1 else ""
            svc = console.runtime.for_workspace(name)
            if not svc or not svc.port:
                return self._send(502, b"no dev server running for this project", "text/plain")
            target = f"http://127.0.0.1:{svc.port}/{rel}"
            req = urllib.request.Request(target, data=(body or None), method=method)
            ct = self.headers.get("Content-Type")
            if ct and method == "POST":
                req.add_header("Content-Type", ct)
            try:
                resp = urllib.request.urlopen(req, timeout=30)
                status, data = resp.status, resp.read()
                ctype = resp.headers.get("Content-Type", "text/html")
            except urllib.error.HTTPError as e:
                status, data = e.code, e.read()
                ctype = e.headers.get("Content-Type", "text/plain")
            except Exception as e:  # dev server not up / connection refused
                return self._send(502, f"dev server error: {e}".encode(), "text/plain")
            if "text/html" in ctype:
                data = _inject_preview(data, name)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

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
    print(f"Lathe console on http://{host}:{port}  (specs: {specs_dir})")
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
