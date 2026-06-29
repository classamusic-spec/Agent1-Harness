"""One-click deploy — push a built app to a host and get a live URL.

Builds on Ship it (Dockerfile / static output) and adds provider adapters that
generate the host config, run the provider's CLI if it's installed + authenticated,
and parse the resulting URL. When the CLI isn't present we don't fail — we return
the exact commands to run, so the button is always useful.

Providers:
  - fly        — Fly.io (Docker); `fly deploy`               → https://<app>.fly.dev
  - cloudflare — Cloudflare Pages (static); `wrangler pages` → https://<app>.pages.dev
  - render     — Render blueprint (render.yaml)              → https://<app>.onrender.com

Stdlib only. The deterministic parts (config, commands, URL) are unit-tested; the
actual deploy runs the user's authenticated CLI on their machine.
"""

from __future__ import annotations

import os
import re
import shutil

from harness import ship


def slug(name: str) -> str:
    s = "".join(c if (c.isalnum() or c in "-") else "-" for c in (name or "app").lower())
    return s.strip("-") or "app"


PROVIDERS = {
    "fly": {"label": "Fly.io", "cli": "fly", "alt_cli": "flyctl", "kind": "docker"},
    "cloudflare": {"label": "Cloudflare Pages", "cli": "wrangler", "kind": "static"},
    "render": {"label": "Render", "cli": "render", "kind": "blueprint"},
}


def list_providers() -> list[dict]:
    return [{"id": k, "label": v["label"], "kind": v["kind"],
             "cli_present": cli_available(k)} for k, v in PROVIDERS.items()]


def cli_available(provider: str, which=shutil.which) -> bool:
    p = PROVIDERS.get(provider)
    if not p:
        return False
    return bool(which(p["cli"]) or (p.get("alt_cli") and which(p["alt_cli"])))


def _public_dir(workspace: str) -> str:
    """The directory of static assets to publish (for Pages-style deploys)."""
    for cand in ("dist", "build", "public", "out"):
        if os.path.isdir(os.path.join(workspace, cand)):
            return cand
    return "."


def deployed_url(provider: str, name: str) -> str:
    s = slug(name)
    return {"fly": f"https://{s}.fly.dev",
            "cloudflare": f"https://{s}.pages.dev",
            "render": f"https://{s}.onrender.com"}.get(provider, "")


def config_files(provider: str, workspace: str, name: str, port: int = 8000) -> dict[str, str]:
    """Provider config to write into the workspace (filename -> content)."""
    s = slug(name)
    if provider == "fly":
        return {"fly.toml": (
            f'app = "{s}"\nprimary_region = "iad"\n\n'
            f'[build]\n  dockerfile = "Dockerfile"\n\n'
            f'[http_service]\n  internal_port = {port}\n  force_https = true\n'
            f'  auto_stop_machines = true\n  auto_start_machines = true\n  min_machines_running = 0\n\n'
            f'[[vm]]\n  size = "shared-cpu-1x"\n  memory = "512mb"\n')}
    if provider == "render":
        return {"render.yaml": (
            f'services:\n  - type: web\n    name: {s}\n    runtime: docker\n'
            f'    dockerfilePath: ./Dockerfile\n    envVars:\n      - key: PORT\n        value: {port}\n'
            f'    healthCheckPath: /\n    plan: free\n')}
    if provider == "cloudflare":
        # Pages publishes a directory; no required config file (project name is a flag).
        return {}
    return {}


def commands(provider: str, name: str, workspace: str, *, port: int = 8000) -> list[str]:
    s = slug(name)
    if provider == "fly":
        return [f"fly deploy --now --ha=false  # first time: fly launch --copy-config --name {s} --yes"]
    if provider == "cloudflare":
        pub = _public_dir(workspace)
        return [f"wrangler pages deploy {pub} --project-name {s}"]
    if provider == "render":
        return ["# Commit render.yaml, then on Render: New > Blueprint and connect this repo",
                "# (or trigger a deploy hook): curl -fsSL $RENDER_DEPLOY_HOOK"]
    return []


_URL_RE = re.compile(r"https://[^\s'\"]+\.(?:fly\.dev|pages\.dev|onrender\.com)[^\s'\"]*")


def extract_url(provider: str, name: str, output: str) -> str:
    """Pull the live URL from CLI output; fall back to the deterministic host URL."""
    m = _URL_RE.search(output or "")
    return m.group(0).rstrip("/.") if m else deployed_url(provider, name)


def plan(provider: str, workspace: str, name: str, *, port: int = 8000) -> dict:
    """What a deploy would do — config + commands + URL — without running anything."""
    if provider not in PROVIDERS:
        return {"error": f"unknown provider: {provider}"}
    meta = PROVIDERS[provider]
    files = dict(config_files(provider, workspace, name, port))
    # Docker-based providers also need the Ship it Dockerfile.
    if meta["kind"] == "docker":
        files.setdefault("Dockerfile", ship.dockerfile_for(workspace, port=port))
    return {
        "provider": provider, "label": meta["label"], "kind": meta["kind"],
        "cli": meta["cli"], "cli_present": cli_available(provider),
        "files": files, "commands": commands(provider, name, workspace, port=port),
        "url": deployed_url(provider, name),
    }


def run(provider: str, workspace: str, name: str, *, port: int = 8000,
        runner=None, write: bool = True) -> dict:
    """Write provider config (+ ship files) and run the deploy. If the CLI is not
    installed, return the ready-to-run commands instead of failing."""
    p = plan(provider, workspace, name, port=port)
    if "error" in p:
        return {"ok": False, **p}

    if write:
        # Materialise the Ship it deploy files (Dockerfile/.dockerignore/.env) and
        # the provider config, without clobbering anything the user wrote.
        ship.write_export(workspace, name=name, port=port)
        for rel, content in p["files"].items():
            dest = os.path.join(workspace, rel)
            if not os.path.exists(dest):
                with open(dest, "w", encoding="utf-8") as fh:
                    fh.write(content)

    if not p["cli_present"]:
        return {"ok": False, "ready": False, "provider": provider, "label": p["label"],
                "reason": f"{p['cli']} CLI not found — install it, authenticate, then run:",
                "commands": p["commands"], "url": p["url"], "files": list(p["files"])}

    if runner is None:
        from harness.sandbox import HostRunner
        runner = HostRunner()
    logs, url = [], p["url"]
    for cmd in p["commands"]:
        if cmd.strip().startswith("#"):
            continue
        proc = runner.run(cmd, workspace, 1800)
        out = (proc.stdout or "") + (proc.stderr or "")
        logs.append(f"$ {cmd}\n{out}")
        if proc.returncode != 0:
            return {"ok": False, "ready": True, "provider": provider, "label": p["label"],
                    "reason": f"`{cmd}` exited {proc.returncode}", "log": "\n".join(logs),
                    "commands": p["commands"], "url": url}
        url = extract_url(provider, name, out) or url
    return {"ok": True, "provider": provider, "label": p["label"], "url": url,
            "log": "\n".join(logs)}
