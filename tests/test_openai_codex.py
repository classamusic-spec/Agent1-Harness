"""Tests for OpenAI + Codex engine support: factory dispatch, Codex argv, and the
doctor's detection + recommendation order."""

from __future__ import annotations

from harness import doctor
from harness.config import EngineConfig, HarnessConfig
from harness.engines.base import make_engine
from harness.engines.codex_cli_engine import CodexCLIEngine
from harness.spec import Spec


def _spec():
    return Spec(name="app", description="d", kind="frontend")


def test_factory_openai_uses_local_engine_pointed_at_openai():
    from harness.engines.local_engine import LocalEngine
    cfg = HarnessConfig(workspace="/tmp/ws", engine=EngineConfig(provider="openai", model="gpt-4o"))
    eng = make_engine(_spec(), cfg)
    assert isinstance(eng, LocalEngine)
    assert "openai.com" in eng._engine_cfg.base_url
    assert eng._engine_cfg.api_key_env == "OPENAI_API_KEY"


def test_factory_codex_cli():
    cfg = HarnessConfig(workspace="/tmp/ws", engine=EngineConfig(provider="codex-cli"))
    assert isinstance(make_engine(_spec(), cfg), CodexCLIEngine)


def test_codex_argv_includes_exec_model_and_persona():
    cfg = HarnessConfig(workspace="/tmp/ws", engine=EngineConfig(provider="codex-cli", model="o4-mini"))
    eng = CodexCLIEngine(_spec(), cfg, "PERSONA")
    argv = eng._argv("build the thing")
    assert argv[0].endswith("codex") or argv[0] == "codex"
    assert "exec" in argv
    assert "-m" in argv and "o4-mini" in argv
    assert argv[-1].startswith("PERSONA") and "build the thing" in argv[-1]  # persona folded in


def test_codex_terminate_safe_without_proc():
    eng = CodexCLIEngine(_spec(), HarnessConfig(workspace="/tmp", engine=EngineConfig(provider="codex-cli")), "")
    eng.terminate()  # no subprocess yet -> harmless


def test_unknown_provider_message_lists_new_engines():
    cfg = HarnessConfig(workspace="/tmp/ws", engine=EngineConfig(provider="bogus"))
    try:
        make_engine(_spec(), cfg)
        assert False
    except ValueError as e:
        assert "codex-cli" in str(e) and "openai" in str(e)


def test_doctor_probe_includes_openai_and_codex():
    state = doctor.probe(
        which=lambda n: "/usr/bin/codex" if n == "codex" else None,
        env={"OPENAI_API_KEY": "sk-x"}, get=lambda u: None)
    assert state["codex_cli"] is True and state["openai_key"] is True
    assert state["claude_cli"] is False


def test_doctor_recommends_codex_then_openai():
    # codex CLI (subscription) preferred over an OpenAI API key
    rec = doctor.recommend({"claude_cli": False, "codex_cli": True, "anthropic_key": False,
                            "openai_key": True, "local": None})
    assert rec["engine"] == "codex-cli"
    rec2 = doctor.recommend({"claude_cli": False, "codex_cli": False, "anthropic_key": False,
                             "openai_key": True, "local": None})
    assert rec2["engine"] == "openai" and "gpt-4o" in rec2["cmd"]
