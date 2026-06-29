"""Tests for the local-server connection self-test (reachability + tool-calling probe)."""

from __future__ import annotations

from harness import doctor, localcheck


def _http_factory(models, tool_calls=True, fail_models=False, fail_completion=False):
    def http(method, url, payload=None, timeout=20.0):
        if url.endswith("/models"):
            if fail_models:
                raise OSError("refused")
            return 200, {"data": [{"id": m} for m in models]}
        if url.endswith("/chat/completions"):
            if fail_completion:
                raise OSError("boom")
            msg = {"content": "ok"}
            if tool_calls:
                msg["tool_calls"] = [{"id": "c1", "function": {"name": "ping", "arguments": "{}"}}]
            return 200, {"choices": [{"message": msg}]}
        raise AssertionError(url)
    return http


def test_connection_ready_with_tool_calling():
    r = localcheck.test_connection("http://localhost:8080/v1", "glm-4.6",
                                   http=_http_factory(["glm-4.6", "qwen2.5-coder"]))
    assert r["reachable"] and r["ok"] and r["tool_calling"] is True
    assert "glm-4.6" in r["models"] and "ready" in r["note"]


def test_connection_warns_without_tool_calling():
    r = localcheck.test_connection("http://localhost:8080/v1", "tiny",
                                   http=_http_factory(["tiny"], tool_calls=False))
    assert r["reachable"] and r["tool_calling"] is False
    assert "didn't make a tool call" in r["note"]


def test_connection_model_not_listed():
    r = localcheck.test_connection("http://localhost:8080/v1", "missing-model",
                                   http=_http_factory(["glm-4.6"]))
    assert r["ok"] is False and "not in the server's model list" in r["note"]


def test_connection_unreachable():
    r = localcheck.test_connection("http://localhost:9/v1", "x",
                                   http=_http_factory([], fail_models=True))
    assert r["reachable"] is False and "can't reach" in r["error"]


def test_connection_no_base_url():
    assert localcheck.test_connection("", "x")["error"] == "no base URL"


def test_doctor_detects_mlx_and_vllm():
    def get(url):
        if "8080" in url:
            return {"data": [{"id": "GLM-4.6-mlx"}]}
        if "8000" in url:
            return {"data": [{"id": "MiniMax-M3"}]}
        return None
    servers = {s["name"]: s for s in doctor.detect_local_servers(get=get)}
    assert "MLX / llama.cpp" in servers and "vLLM" in servers
    assert servers["MLX / llama.cpp"]["base_url"] == "http://localhost:8080/v1"


def test_doctor_picks_glm_minimax_as_coder():
    assert doctor.pick_coder_model(["llama3", "GLM-4.6"]) == "GLM-4.6"
    assert doctor.pick_coder_model(["phi", "MiniMax-M3"]) == "MiniMax-M3"
