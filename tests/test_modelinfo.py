"""Tests for the models picker: context length + tool-calling status per model."""

from __future__ import annotations

from harness import modelinfo


def test_tool_calling_status():
    assert modelinfo.tool_calling_status("glm-4.6") == "likely"
    assert modelinfo.tool_calling_status("qwen2.5-coder:32b") == "likely"
    assert modelinfo.tool_calling_status("MiniMax-M3") == "likely"
    assert modelinfo.tool_calling_status("gemma2:9b") == "weak"
    assert modelinfo.tool_calling_status("phi-3-mini") == "weak"
    assert modelinfo.tool_calling_status("some-random-model") == "unknown"


def test_context_from_name():
    assert modelinfo.context_from_name("qwen2.5-coder-128k") == 128 * 1024
    assert modelinfo.context_from_name("model-32k-instruct") == 32 * 1024
    assert modelinfo.context_from_name("glm-4.6-1m") == 1024 * 1024
    assert modelinfo.context_from_name("llama-3.1-8b") is None     # 8b is params, not ctx
    assert modelinfo.context_from_name("plain-model") is None


def test_fmt_context():
    assert modelinfo.fmt_context(None) == "?"
    assert modelinfo.fmt_context(32768) == "32k"
    assert modelinfo.fmt_context(1024 * 1024) == "1M"
    assert modelinfo.fmt_context(512) == "512"


def test_list_models_uses_entry_context_and_sorts(tmp_path):
    """vLLM-style /models with max_model_len; strong tool-callers + more context first."""
    def fake_http(method, url, payload=None, timeout=3.0):
        assert url.endswith("/v1/models")
        return {"data": [
            {"id": "gemma2-9b", "max_model_len": 8192},
            {"id": "glm-4.6", "max_model_len": 131072},
            {"id": "qwen2.5-coder", "max_model_len": 32768},
        ]}

    out = modelinfo.list_models("http://localhost:8000/v1", http=fake_http)
    ids = [m["id"] for m in out["models"]]
    # glm + qwen (likely) before gemma (weak); glm (128k) before qwen (32k)
    assert ids == ["glm-4.6", "qwen2.5-coder", "gemma2-9b"]
    glm = out["models"][0]
    assert glm["context_length"] == 131072 and glm["context"] == "128k"
    assert glm["tool_calling"] == "likely"
    assert out["models"][-1]["tool_calling"] == "weak"


def test_list_models_ollama_uses_api_show(tmp_path):
    calls = []

    def fake_http(method, url, payload=None, timeout=3.0):
        calls.append((method, url, payload))
        if url.endswith("/v1/models"):
            return {"data": [{"id": "qwen2.5-coder:7b"}]}   # no context in /models
        assert url.endswith("/api/show") and payload == {"name": "qwen2.5-coder:7b"}
        return {"model_info": {"qwen2.context_length": 32768, "general.name": "qwen"}}

    out = modelinfo.list_models("http://localhost:11434/v1", http=fake_http)
    m = out["models"][0]
    assert m["context_length"] == 32768 and m["context"] == "32k"
    # it hit the native /api/show endpoint (root, not /v1)
    assert any(u.endswith("/api/show") for _, u, _ in calls)


def test_list_models_unreachable():
    def boom(*a, **k):
        raise OSError("refused")

    out = modelinfo.list_models("http://localhost:9999/v1", http=boom)
    assert out["models"] == [] and "can't reach" in out["error"]


def test_list_models_no_base_url():
    out = modelinfo.list_models("")
    assert out["error"] == "no base URL"
