"""Tests for the engine 'doctor': detection, model picking, and recommendation
(all probes injected — no network, no real binaries)."""

from __future__ import annotations

from harness import doctor


def test_pick_coder_model_prefers_coder_then_qwen():
    assert doctor.pick_coder_model(["llama3", "qwen2.5-coder", "phi3"]) == "qwen2.5-coder"
    assert doctor.pick_coder_model(["llama3", "qwen2.5:7b"]) == "qwen2.5:7b"
    assert doctor.pick_coder_model(["mistral-small"]) == "mistral-small"  # falls back to first
    assert doctor.pick_coder_model([]) is None


def test_detect_local_server_reads_ollama_first():
    def get(url):
        if "11434" in url:
            return {"data": [{"id": "qwen2.5-coder"}, {"id": "llama3"}]}
        return None
    local = doctor.detect_local_server(get=get)
    assert local["name"] == "Ollama"
    assert local["base_url"] == "http://localhost:11434/v1"
    assert local["models"] == ["qwen2.5-coder", "llama3"]


def test_detect_local_server_falls_back_to_lmstudio():
    def get(url):
        if "1234" in url:
            return {"data": [{"id": "deepseek-coder"}]}
        return None
    local = doctor.detect_local_server(get=get)
    assert local["name"] == "LM Studio" and local["models"] == ["deepseek-coder"]


def test_detect_local_server_none_when_down():
    assert doctor.detect_local_server(get=lambda u: None) is None


def test_detect_local_servers_returns_all_reachable():
    def get(url):
        if "11434" in url:
            return {"data": [{"id": "qwen2.5-coder"}]}
        if "1234" in url:
            return {"data": [{"id": "llama-3.1-8b"}, {"id": "deepseek-coder-v2"}]}
        return None
    servers = doctor.detect_local_servers(get=get)
    names = {s["name"] for s in servers}
    assert names == {"Ollama", "LM Studio"}
    lm = next(s for s in servers if s["name"] == "LM Studio")
    assert lm["base_url"] == "http://localhost:1234/v1"
    assert "deepseek-coder-v2" in lm["models"]


def test_recommend_prefers_claude_cli():
    rec = doctor.recommend({"claude_cli": True, "anthropic_key": True, "local": None})
    assert rec["engine"] == "claude-cli"


def test_recommend_falls_to_anthropic_then_local():
    rec = doctor.recommend({"claude_cli": False, "anthropic_key": True, "local": None})
    assert rec["engine"] == "anthropic"
    rec2 = doctor.recommend({"claude_cli": False, "anthropic_key": False,
                             "local": {"name": "Ollama", "base_url": "http://localhost:11434/v1",
                                       "models": ["qwen2.5-coder"]}})
    assert rec2["engine"] == "local"
    assert "qwen2.5-coder" in rec2["cmd"] and "11434" in rec2["cmd"]


def test_recommend_none_when_nothing_available():
    assert doctor.recommend({"claude_cli": False, "anthropic_key": False, "local": None}) is None


def test_probe_uses_injected_dependencies():
    state = doctor.probe(
        which=lambda n: "/usr/local/bin/claude" if n == "claude" else None,
        env={"ANTHROPIC_API_KEY": "sk-x"},
        get=lambda u: None)
    assert state["claude_cli"] is True and state["anthropic_key"] is True and state["local"] is None


def test_render_includes_recommendation_and_marks():
    out = doctor.render({"claude_cli": True, "anthropic_key": False, "local": None})
    assert "Claude Code (CLI)" in out and "Recommended" in out
    assert "✓ Claude Code (CLI)" in out

    out2 = doctor.render({"claude_cli": False, "anthropic_key": False, "local": None})
    assert "No engine available" in out2 and "ollama pull" in out2
