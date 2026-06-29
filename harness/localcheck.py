"""Test a local LLM server before building.

Confirms an OpenAI-compatible server is reachable, lists its models, and probes
whether the chosen model actually makes **tool calls** — the one thing the agentic
build loop needs and that some local models/servers don't do well. Surfaces this up
front so vibe coding stays smooth (no silent half-working builds). Stdlib only.
"""

from __future__ import annotations

import json
import time
import urllib.request

_PROBE_TOOL = [{
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Reply to confirm tool calling works.",
        "parameters": {"type": "object", "properties": {"msg": {"type": "string"}},
                       "required": ["msg"]},
    },
}]


def _http_json(method: str, url: str, payload=None, timeout: float = 20.0):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json", "Authorization": "Bearer local"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return getattr(r, "status", 200), json.loads(r.read().decode("utf-8", "replace"))


def test_connection(base_url: str, model: str = "", *, http=_http_json, timeout: float = 20.0) -> dict:
    base = (base_url or "").rstrip("/")
    out: dict = {"base_url": base_url, "model": model, "reachable": False,
                 "models": [], "tool_calling": False, "ok": False}
    if not base:
        out["error"] = "no base URL"
        return out
    try:
        _, data = http("GET", base + "/models", None, timeout)
        out["models"] = [m.get("id") for m in (data.get("data") or []) if m.get("id")]
        out["reachable"] = True
    except Exception as exc:
        out["error"] = f"can't reach {base}/models ({type(exc).__name__})"
        return out

    payload = {
        "model": model or (out["models"][0] if out["models"] else "local"),
        "messages": [{"role": "user", "content": "Call the ping tool with msg='ok'."}],
        "tools": _PROBE_TOOL, "tool_choice": "auto", "max_tokens": 64, "temperature": 0,
    }
    try:
        t0 = time.monotonic()
        _, resp = http("POST", base + "/chat/completions", payload, max(timeout, 60.0))
        out["latency_ms"] = int((time.monotonic() - t0) * 1000)
        msg = ((resp.get("choices") or [{}])[0] or {}).get("message") or {}
        out["tool_calling"] = bool(msg.get("tool_calls"))
        out["sample"] = (msg.get("content") or "")[:160]
    except Exception as exc:
        out["error"] = f"completion failed ({type(exc).__name__})"
        return out

    model_ok = (not model) or (not out["models"]) or (model in out["models"])
    out["ok"] = out["reachable"] and model_ok
    if not model_ok:
        out["note"] = (f"'{model}' not in the server's model list — pick one of: "
                       + ", ".join(out["models"][:6]))
    elif out["tool_calling"]:
        out["note"] = "ready — tool calling works, good for the build loop"
    else:
        out["note"] = ("reachable, but the model didn't make a tool call. Pick a strong "
                       "tool-calling model (GLM-4.6 / MiniMax / Qwen-Coder) for builds.")
    return out
