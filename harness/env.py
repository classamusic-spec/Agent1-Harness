"""`.env` / secrets handling for builds.

Loads a workspace `.env` into the environment for the dev server and the
verification checks (so apps read DB paths, secret keys, etc. from config rather
than hard-coding them), and can generate a `.env` with safe defaults + a random
secret when one is missing. Secrets live only in the (gitignored) workspace.
"""

from __future__ import annotations

import os
import secrets


def load_dotenv(path: str) -> dict[str, str]:
    """Parse a .env file into a dict. Tolerant: ignores blanks/comments, strips
    `export ` and surrounding quotes. Returns {} if the file is missing."""
    out: dict[str, str] = {}
    if not os.path.isfile(path):
        return out
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return out
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        if (len(val) >= 2) and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def generate_env(path: str, defaults: dict[str, str] | None = None,
                 *, secret_keys=("SECRET_KEY",)) -> dict[str, str]:
    """Create `path` with `defaults` (+ a random value for each secret key) if it
    doesn't exist. Returns the resulting env mapping. Never overwrites."""
    if os.path.isfile(path):
        return load_dotenv(path)
    env = dict(defaults or {})
    for k in secret_keys:
        env.setdefault(k, secrets.token_hex(24))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    body = "".join(f"{k}={v}\n" for k, v in env.items())
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    return env


_SECRET_HINT = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "APIKEY")
_PLACEHOLDER = ("", "changeme", "change-me", "replace", "xxx", "your-", "todo")


def _looks_secret(key: str, val: str) -> bool:
    ku = key.upper()
    if not any(h in ku for h in _SECRET_HINT):
        return False
    vl = val.strip().lower()
    return any(vl == p or vl.startswith(p) for p in _PLACEHOLDER)


def ensure_env(workspace: str) -> dict[str, str]:
    """Make sure the workspace has a usable `.env`.

    If `.env` is missing but a `.env.example` exists, materialise `.env` from the
    example's defaults, generating a fresh random value for any key that looks like
    a secret with a placeholder value. Returns the resulting env mapping (or {}).
    """
    dotenv = os.path.join(workspace, ".env")
    if os.path.isfile(dotenv):
        return load_dotenv(dotenv)
    example = os.path.join(workspace, ".env.example")
    if not os.path.isfile(example):
        return {}
    defaults = load_dotenv(example)
    secret_keys = tuple(k for k, v in defaults.items() if _looks_secret(k, v))
    for k in secret_keys:
        defaults.pop(k, None)  # let generate_env fill these with a random value
    return generate_env(dotenv, defaults, secret_keys=secret_keys)


def merged_environ(workspace: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    """os.environ + the workspace's .env + extra (later wins)."""
    env = dict(os.environ)
    env.update(load_dotenv(os.path.join(workspace, ".env")))
    if extra:
        env.update(extra)
    return env
