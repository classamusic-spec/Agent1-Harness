"""Enrich a local server's model list with the two things that decide whether a
model is usable for the build loop: its **context length** (can it hold the
workspace?) and its **tool-calling** ability (can it drive the file/bash tools?).

The picker shows both at a glance so you don't start a build on a 4k-context or
non-tool-calling model and discover it half-way through. Context length comes
from server metadata where available (vLLM `max_model_len`, Ollama `/api/show`)
and falls back to a hint parsed from the model name. Tool-calling is a heuristic
from the model family; the UI can still run a live probe (localcheck) for ground
truth. Stdlib only.
"""

from __future__ import annotations

import json
import re
import urllib.request

# Families that reliably make OpenAI-style tool calls (good for the coder loop).
_GOOD = ("glm", "minimax", "qwen", "coder", "codestral", "devstral", "mistral",
         "mixtral", "hermes", "command-r", "command", "kimi", "granite",
         "firefunction", "functionary", "nemotron", "deepseek", "watt",
         "llama-3.1", "llama-3.2", "llama-3.3", "llama3.1", "llama3.2", "llama3.3",
         "llama-4", "llama4")
# Families that typically do NOT tool-call well (chat-only or base/completion).
_WEAK = ("gemma", "phi-", "phi3", "phi2", "tinyllama", "llama-2", "llama2",
         "vicuna", "orca", "stablelm", "gpt2", "starcoder", "smollm")

_CTX_FIELDS = ("max_model_len", "context_length", "context_window",
               "max_context_length", "n_ctx")


def tool_calling_status(model_id: str) -> str:
    """'likely' | 'weak' | 'unknown' for whether the model can drive tool calls."""
    m = (model_id or "").lower()
    if any(g in m for g in _GOOD):
        return "likely"
    if any(w in m for w in _WEAK):
        return "weak"
    return "unknown"


def context_from_name(model_id: str) -> int | None:
    """A soft context-length hint parsed from the model name (e.g. '...-128k', '1m')."""
    m = (model_id or "").lower()
    mk = re.search(r"(?<![a-z0-9])(\d{1,4})k(?![a-z])", m)
    if mk:
        return int(mk.group(1)) * 1024
    mm = re.search(r"(?<![a-z0-9])(\d)m(?![a-z])", m)
    if mm:
        return int(mm.group(1)) * 1024 * 1024
    return None


def _context_from_entry(entry: dict) -> int | None:
    for f in _CTX_FIELDS:
        v = entry.get(f)
        if isinstance(v, (int, float)) and v > 0:
            return int(v)
    return None


def _http_json(method: str, url: str, payload=None, timeout: float = 3.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _ollama_context(base_url: str, model: str, http) -> int | None:
    """Ollama exposes real context length via its native /api/show endpoint."""
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        info = http("POST", root.rstrip("/") + "/api/show", {"name": model})
    except Exception:
        return None
    mi = info.get("model_info") if isinstance(info, dict) else None
    if isinstance(mi, dict):
        for k, v in mi.items():
            if k.endswith(".context_length") and isinstance(v, (int, float)) and v > 0:
                return int(v)
    return None


def fmt_context(n: int | None) -> str:
    if not n:
        return "?"
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.0f}M"
    if n >= 1000:
        return f"{round(n / 1024)}k"
    return str(n)


def list_models(base_url: str, *, http=_http_json) -> dict:
    """Every model the server exposes, each annotated with context length + a
    tool-calling status. Returns {base_url, models: [...], error?}."""
    from harness import warmup
    base = (base_url or "").rstrip("/")
    out: dict = {"base_url": base_url, "models": []}
    if not base:
        out["error"] = "no base URL"
        return out
    try:
        payload = http("GET", base + "/models", None)
    except Exception as exc:
        out["error"] = f"can't reach {base}/models ({type(exc).__name__})"
        return out

    entries = payload.get("data") if isinstance(payload, dict) else None
    ollama = warmup.is_ollama(base)
    models = []
    for entry in (entries or []):
        if not isinstance(entry, dict) or not entry.get("id"):
            continue
        mid = str(entry["id"])
        ctx = _context_from_entry(entry)
        if ctx is None and ollama:
            ctx = _ollama_context(base, mid, http)
        if ctx is None:
            ctx = context_from_name(mid)
        models.append({
            "id": mid,
            "context_length": ctx,
            "context": fmt_context(ctx),
            "tool_calling": tool_calling_status(mid),
        })
    # Strong tool-callers with the most context float to the top of the picker.
    rank = {"likely": 0, "unknown": 1, "weak": 2}
    models.sort(key=lambda m: (rank.get(m["tool_calling"], 1), -(m["context_length"] or 0)))
    out["models"] = models
    return out
