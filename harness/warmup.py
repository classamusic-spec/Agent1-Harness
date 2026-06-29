"""Model warm-up + KV-cache reuse hints for local OpenAI-compatible servers.

Big local models (GLM / MiniMax on a Mac Studio) pay two latency taxes the cloud
hides: a large one-time cost to load the weights into RAM/VRAM, and a per-request
cost to rebuild the prompt's KV cache. This module trims both so iterating feels
snappy:

* warm() fires a tiny 1-token completion so the server loads the model *before*
  the first real turn — the first token isn't stuck behind a cold model load.
* keep_alive_extra() asks Ollama to keep the model resident between turns instead
  of unloading it after each request.
* cache_hint() returns a stable prompt_cache_key derived from the workspace, so
  servers with prefix caching (vLLM, llama.cpp) reuse this project's KV bucket
  across iterations.

Stdlib only (urllib); no openai dependency, so it stays importable everywhere.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request


def _http_json(method: str, url: str, payload=None, timeout: float = 30.0,
               api_key: str = "local"):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return getattr(r, "status", 200), json.loads(r.read().decode("utf-8", "replace"))


def is_local_server(base_url: str | None) -> bool:
    """A self-hosted OpenAI-compatible server (not the OpenAI cloud)."""
    b = (base_url or "").lower()
    return bool(b) and "openai.com" not in b and "api.anthropic.com" not in b


def is_ollama(base_url: str | None) -> bool:
    b = (base_url or "").lower()
    return ":11434" in b or "/ollama" in b


def keep_alive_extra(base_url: str | None, keep_alive: str | None = "30m") -> dict:
    """Ollama-only: keep the model loaded between requests (else it unloads ~5m
    after each call and the next turn pays a cold load again)."""
    if keep_alive and is_ollama(base_url):
        return {"keep_alive": keep_alive}
    return {}


def cache_hint(workspace: str | None) -> dict:
    """A stable key so prefix-caching servers bucket KV reuse to this project.

    Servers without prompt_cache_key ignore the extra field, so this is safe to
    always include for self-hosted servers."""
    if not workspace:
        return {}
    name = os.path.basename(os.path.abspath(workspace)) or "workspace"
    return {"prompt_cache_key": f"lathe:{name}"}


def request_extra(base_url: str | None, workspace: str | None,
                  keep_alive: str | None = "30m") -> dict:
    """The `extra_body` to attach to each completion for a local server: keep the
    model warm (Ollama) and hint prompt-cache reuse (vLLM/llama.cpp). Empty for
    the OpenAI cloud so we never send it provider-specific junk."""
    if not is_local_server(base_url):
        return {}
    extra: dict = {}
    extra.update(keep_alive_extra(base_url, keep_alive))
    extra.update(cache_hint(workspace))
    return extra


def warm(base_url: str | None, model: str, *, api_key: str = "local",
         keep_alive: str | None = "30m", workspace: str | None = None,
         http=_http_json, timeout: float = 120.0) -> dict:
    """Load the model into memory with a 1-token completion. Returns
    {ok, latency_ms, note}. Never raises — a failed warm-up just means the first
    real turn pays the cold-load cost (it isn't fatal)."""
    out: dict = {"ok": False, "model": model}
    if not is_local_server(base_url):
        out["note"] = "skipped (not a local server)"
        return out
    base = base_url.rstrip("/")
    payload = {
        "model": model or "local",
        "messages": [{"role": "user", "content": "ok"}],
        "max_tokens": 1, "temperature": 0,
    }
    payload.update(keep_alive_extra(base_url, keep_alive))
    try:
        t0 = time.monotonic()
        http("POST", base + "/chat/completions", payload, timeout, api_key)
        out["latency_ms"] = int((time.monotonic() - t0) * 1000)
        out["ok"] = True
        out["note"] = f"model warm ({out['latency_ms']}ms to first response)"
    except Exception as exc:  # noqa: BLE001 - warm-up is best-effort
        out["note"] = f"warm-up skipped ({type(exc).__name__})"
    return out
