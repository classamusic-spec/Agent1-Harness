"""Ship it — package a built app for deployment.

Turns a finished workspace into something you can run anywhere: a stack-aware
`Dockerfile`, a `docker-compose.yml` (port + `.env` wired in), a `.dockerignore`,
and a downloadable `.zip` of the project. Generation is deterministic and reuses
the same stack detection the verifier/runtime use, so what ships matches what was
built and tested. Stdlib only.
"""

from __future__ import annotations

import io
import json
import os
import zipfile

from harness import runtime, stacks

# Files/dirs never worth shipping (caches, local DBs, secrets, VCS, harness state).
_EXCLUDE_DIRS = {".git", "node_modules", "__pycache__", ".studio", ".pytest_cache",
                 ".mypy_cache", ".venv", "venv", "dist", "build", ".next", ".cache"}
_EXCLUDE_FILES = {".env"}  # the real secrets file — ship .env.example instead
_EXCLUDE_SUFFIXES = (".db", ".db-wal", ".db-shm", ".sqlite", ".sqlite3", ".pyc", ".log")


def _node_scripts(workspace: str) -> dict:
    try:
        return (json.load(open(os.path.join(workspace, "package.json"))).get("scripts") or {})
    except (OSError, json.JSONDecodeError):
        return {}


def _node_start(scripts: dict) -> str:
    """The best production-ish start command for a node app, binding host+port."""
    for s in ("start", "preview", "dev"):
        if s in scripts:
            if s in ("preview", "dev"):  # vite & co. need an explicit bind
                return f"npm run {s} -- --host 0.0.0.0 --port ${{PORT}}"
            return "npm start"
    return "npm start"


def dockerfile_for(workspace: str, *, stack: str | None = None,
                   run_command: str | None = None, port: int = 8000) -> str:
    """A stack-aware Dockerfile. Reads $PORT from the environment (default 8000)."""
    stack = stack or stacks.detect_stack(workspace)
    if stack == "node":
        scripts = _node_scripts(workspace)
        build = "RUN npm run build\n" if "build" in scripts else ""
        cmd = run_command or _node_start(scripts)
        return (
            "# syntax=docker/dockerfile:1\n"
            "FROM node:20-slim\n"
            "WORKDIR /app\n"
            "ENV NODE_ENV=production\n"
            f"ENV PORT={port}\n"
            "COPY package*.json ./\n"
            "RUN npm ci || npm install\n"
            "COPY . .\n"
            f"{build}"
            f"EXPOSE {port}\n"
            f'CMD {cmd}\n'
        )
    if stack == "python":
        reqs = os.path.isfile(os.path.join(workspace, "requirements.txt"))
        install = "RUN pip install --no-cache-dir -r requirements.txt\n" if reqs else ""
        cmd = run_command or (runtime.detect_command(workspace) or {}).get("command") or "python server.py"
        cmd = cmd.replace("$PORT", "${PORT}")
        return (
            "# syntax=docker/dockerfile:1\n"
            "FROM python:3.12-slim\n"
            "WORKDIR /app\n"
            "ENV PYTHONUNBUFFERED=1\n"
            f"ENV PORT={port}\n"
            "COPY requirements.txt* ./\n"
            f"{install}"
            "COPY . .\n"
            f"EXPOSE {port}\n"
            f'CMD {cmd}\n'
        )
    # static (or unknown): serve files with a tiny stdlib server.
    return (
        "# syntax=docker/dockerfile:1\n"
        "FROM python:3.12-slim\n"
        "WORKDIR /app\n"
        f"ENV PORT={port}\n"
        "COPY . .\n"
        f"EXPOSE {port}\n"
        'CMD python -m http.server ${PORT}\n'
    )


def compose_for(name: str, *, port: int = 8000, has_env: bool = False) -> str:
    """A docker-compose.yml for the single app service."""
    safe = "".join(c if (c.isalnum() or c in "-_") else "-" for c in (name or "app").lower()).strip("-")
    env_block = "    env_file:\n      - .env\n" if has_env else ""
    return (
        "services:\n"
        f"  {safe or 'app'}:\n"
        "    build: .\n"
        f"    image: {safe or 'app'}:latest\n"
        f'    ports:\n      - "{port}:{port}"\n'
        f"    environment:\n      - PORT={port}\n"
        f"{env_block}"
        "    restart: unless-stopped\n"
    )


def dockerignore() -> str:
    return "\n".join([".git", "node_modules", "__pycache__", "*.pyc", ".studio",
                      ".venv", "venv", "dist", "build", ".next", ".cache",
                      "*.db", "*.db-wal", "*.db-shm", "*.sqlite*", ".env", ""])


def export_files(workspace: str, *, name: str = "app", port: int = 8000,
                 stack: str | None = None, run_command: str | None = None) -> dict[str, str]:
    """The deployment files to write for `workspace` (content keyed by filename)."""
    has_env = os.path.isfile(os.path.join(workspace, ".env")) or \
        os.path.isfile(os.path.join(workspace, ".env.example"))
    files = {
        "Dockerfile": dockerfile_for(workspace, stack=stack, run_command=run_command, port=port),
        "docker-compose.yml": compose_for(name, port=port, has_env=has_env),
        ".dockerignore": dockerignore(),
    }
    return files


def write_export(workspace: str, *, name: str = "app", port: int = 8000,
                 stack: str | None = None, run_command: str | None = None,
                 overwrite: bool = False) -> list[str]:
    """Write the deployment files into the workspace. Returns the files written.
    Existing files are left untouched unless `overwrite` is set."""
    written = []
    # Materialise a real .env (from .env.example) so `docker compose` — which the
    # generated compose wires via env_file — has the file it expects locally. The
    # zip never includes .env, so the deployed copy regenerates its own secret.
    from harness import env as envmod
    if envmod.ensure_env(workspace):
        written.append(".env")
    for rel, content in export_files(workspace, name=name, port=port, stack=stack,
                                     run_command=run_command).items():
        dest = os.path.join(workspace, rel)
        if os.path.exists(dest) and not overwrite:
            continue
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
        written.append(rel)
    return written


def _should_skip(rel_parts: tuple[str, ...], filename: str) -> bool:
    if any(part in _EXCLUDE_DIRS for part in rel_parts):
        return True
    if filename in _EXCLUDE_FILES:
        return True
    return filename.endswith(_EXCLUDE_SUFFIXES)


def zip_bytes(workspace: str, *, name: str = "app", port: int = 8000,
             stack: str | None = None, run_command: str | None = None,
             include_deploy: bool = True) -> bytes:
    """Zip the workspace (minus caches/secrets/DBs) into bytes, optionally adding
    the generated deployment files. Real `.env` is excluded; `.env.example` stays."""
    buf = io.BytesIO()
    root = os.path.abspath(workspace)
    base = os.path.basename(root.rstrip("/")) or "app"
    deploy = export_files(workspace, name=name, port=port, stack=stack,
                          run_command=run_command) if include_deploy else {}
    written: set[str] = set()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(root):
            rel_dir = os.path.relpath(dirpath, root)
            parts = () if rel_dir == "." else tuple(rel_dir.split(os.sep))
            dirnames[:] = [d for d in dirnames if d not in _EXCLUDE_DIRS]
            for fn in filenames:
                if _should_skip(parts, fn):
                    continue
                rel = fn if rel_dir == "." else os.path.join(rel_dir, fn)
                # Deployment files we generate take precedence over any on disk.
                if rel in deploy:
                    continue
                zf.write(os.path.join(dirpath, fn), os.path.join(base, rel))
                written.add(rel)
        for rel, content in deploy.items():
            if rel not in written:
                zf.writestr(os.path.join(base, rel), content)
    return buf.getvalue()


def export_zip(workspace: str, dest_path: str, **kw) -> str:
    """Write a deployment zip to `dest_path`. Returns the path."""
    data = zip_bytes(workspace, **kw)
    os.makedirs(os.path.dirname(os.path.abspath(dest_path)) or ".", exist_ok=True)
    with open(dest_path, "wb") as fh:
        fh.write(data)
    return dest_path
