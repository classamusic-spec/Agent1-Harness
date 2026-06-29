"""Tests for model warm-up + KV-cache reuse hints (local servers only)."""

from __future__ import annotations

from harness import warmup


def test_is_local_server():
    assert warmup.is_local_server("http://localhost:11434/v1")
    assert warmup.is_local_server("http://192.168.1.5:8080/v1")
    assert not warmup.is_local_server("https://api.openai.com/v1")
    assert not warmup.is_local_server("")
    assert not warmup.is_local_server(None)


def test_keep_alive_only_for_ollama():
    assert warmup.keep_alive_extra("http://localhost:11434/v1") == {"keep_alive": "30m"}
    assert warmup.keep_alive_extra("http://localhost:8080/v1") == {}  # MLX/llama.cpp
    assert warmup.keep_alive_extra("http://localhost:11434/v1", None) == {}


def test_cache_hint_is_stable_per_workspace(tmp_path):
    h1 = warmup.cache_hint(str(tmp_path))
    h2 = warmup.cache_hint(str(tmp_path))
    assert h1 == h2 and h1["prompt_cache_key"].startswith("lathe:")
    assert warmup.cache_hint("") == {}


def test_request_extra_local_vs_cloud(tmp_path):
    extra = warmup.request_extra("http://localhost:11434/v1", str(tmp_path))
    assert extra["keep_alive"] == "30m"
    assert extra["prompt_cache_key"].startswith("lathe:")
    # vLLM/llama.cpp (:8080) -> cache hint but no Ollama keep_alive
    mlx = warmup.request_extra("http://localhost:8080/v1", str(tmp_path))
    assert "keep_alive" not in mlx and "prompt_cache_key" in mlx
    # OpenAI cloud -> nothing provider-specific
    assert warmup.request_extra("https://api.openai.com/v1", str(tmp_path)) == {}


def test_warm_posts_one_token_and_sets_keep_alive(tmp_path):
    calls = []

    def fake_http(method, url, payload=None, timeout=30.0, api_key="local"):
        calls.append((method, url, payload))
        return 200, {"choices": [{"message": {"content": ""}}]}

    info = warmup.warm("http://localhost:11434/v1", "glm-4.6",
                       workspace=str(tmp_path), http=fake_http)
    assert info["ok"] is True and "warm" in info["note"]
    method, url, payload = calls[0]
    assert method == "POST" and url.endswith("/chat/completions")
    assert payload["max_tokens"] == 1
    assert payload["keep_alive"] == "30m"        # Ollama stays resident
    assert payload["model"] == "glm-4.6"


def test_warm_skips_cloud():
    def boom(*a, **k):  # must never be called
        raise AssertionError("should not hit the network for the cloud")

    info = warmup.warm("https://api.openai.com/v1", "gpt-4o", http=boom)
    assert info["ok"] is False and "skipped" in info["note"]


def test_warm_never_raises_on_error():
    def boom(*a, **k):
        raise OSError("connection refused")

    info = warmup.warm("http://localhost:11434/v1", "glm-4.6", http=boom)
    assert info["ok"] is False and "skipped" in info["note"]
