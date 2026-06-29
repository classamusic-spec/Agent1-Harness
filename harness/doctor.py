"""`python -m harness.doctor` — what engines can I use right now?

Probes the machine for the three ways to drive the harness and prints a plain
summary plus the exact command to start with:

  1. Claude Code (CLI)  — your installed, authenticated `claude` binary (no key).
  2. Anthropic API      — `ANTHROPIC_API_KEY` in the environment.
  3. Local LLM          — an OpenAI-compatible server (Ollama :11434 / LM Studio
                          :1234 / vLLM), with its loaded models listed.

Stdlib only; every probe is fast and failure-tolerant, so this is safe to run as
the last step of the Mac installer.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.request

OLLAMA_URL = "http://localhost:11434/v1/models"
LMSTUDIO_URL = "http://localhost:1234/v1/models"
# A local model good at the tool-calling the coder loop needs, in preference order.
_CODER_HINTS = ("coder", "qwen", "deepseek", "codestral", "starcoder", "granite", "llama")


def _get_json(url: str, timeout: float = 1.5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


def detect_claude_cli(which=shutil.which) -> bool:
    return bool(which("claude"))


def detect_anthropic_key(env=None) -> bool:
    env = os.environ if env is None else env
    return bool(env.get("ANTHROPIC_API_KEY"))


def _models_from(payload) -> list[str]:
    """Pull model ids from an OpenAI-style {"data":[{"id":...}]} response."""
    if not isinstance(payload, dict):
        return []
    return [str(m.get("id")) for m in (payload.get("data") or []) if m.get("id")]


_LOCAL_SERVERS = (
    ("Ollama", OLLAMA_URL, "http://localhost:11434/v1"),
    ("LM Studio", LMSTUDIO_URL, "http://localhost:1234/v1"),
)


def detect_local_servers(get=_get_json) -> list[dict]:
    """Every reachable local LLM server, with the models it has loaded/downloaded."""
    out = []
    for name, url, base in _LOCAL_SERVERS:
        payload = get(url)
        if payload is not None:
            out.append({"name": name, "base_url": base, "models": _models_from(payload)})
    return out


def detect_local_server(get=_get_json) -> dict | None:
    """The first reachable local LLM server (back-compat)."""
    servers = detect_local_servers(get)
    return servers[0] if servers else None


def pick_coder_model(models: list[str]) -> str | None:
    if not models:
        return None
    for hint in _CODER_HINTS:
        for m in models:
            if hint in m.lower():
                return m
    return models[0]


def probe(which=shutil.which, env=None, get=_get_json) -> dict:
    return {
        "claude_cli": detect_claude_cli(which),
        "anthropic_key": detect_anthropic_key(env),
        "local": detect_local_server(get),
    }


def recommend(state: dict) -> dict | None:
    """Pick the best available engine and the exact flags to use it."""
    if state.get("claude_cli"):
        return {"engine": "claude-cli", "cmd": "--engine claude-cli --model sonnet",
                "why": "your authenticated Claude Code CLI (no API key needed)"}
    if state.get("anthropic_key"):
        return {"engine": "anthropic", "cmd": "--engine anthropic",
                "why": "the Anthropic API via $ANTHROPIC_API_KEY"}
    local = state.get("local")
    if local:
        model = pick_coder_model(local["models"]) or "qwen2.5-coder"
        return {"engine": "local",
                "cmd": f"--engine local --base-url {local['base_url']} --model {model}",
                "why": f"{local['name']} at {local['base_url']} (model: {model})"}
    return None


def render(state: dict) -> str:
    def mark(ok):
        return "✓" if ok else "·"
    lines = ["Agent1-Harness — engine check", ""]
    lines.append(f"  {mark(state['claude_cli'])} Claude Code (CLI)   "
                 + ("found" if state["claude_cli"] else "not found — install: npm i -g @anthropic-ai/claude-code"))
    lines.append(f"  {mark(state['anthropic_key'])} Anthropic API key   "
                 + ("set" if state["anthropic_key"] else "not set — export ANTHROPIC_API_KEY=…"))
    local = state.get("local")
    if local:
        models = ", ".join(local["models"][:6]) or "(no models loaded)"
        lines.append(f"  ✓ Local LLM           {local['name']} at {local['base_url']}")
        lines.append(f"      models: {models}")
    else:
        lines.append("  · Local LLM           none reachable "
                     "(Ollama :11434 / LM Studio :1234) — see 'Connect a local LLM' in the README")
    rec = recommend(state)
    lines.append("")
    if rec:
        lines.append(f"Recommended: {rec['why']}")
        lines.append(f"  Web console:  python -m harness.server   then pick the engine in Studio")
        lines.append(f"  CLI build:    appbuilder <spec.yaml> --workspace ./out {rec['cmd']}")
    else:
        lines.append("No engine available yet. Easiest options:")
        lines.append("  • Claude Code:  npm i -g @anthropic-ai/claude-code && claude  (sign in once)")
        lines.append("  • Local LLM:    brew install ollama && ollama serve && ollama pull qwen2.5-coder")
    return "\n".join(lines)


def main(argv=None) -> int:
    print(render(probe()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
