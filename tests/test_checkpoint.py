"""Offline tests for checkpoint serialization and config/spec roundtrips."""

from __future__ import annotations

from harness.checkpoint import Checkpoint, checkpoint_exists, load_checkpoint, save_checkpoint
from harness.config import EngineConfig, HarnessConfig, config_from_dict, config_to_dict
from harness.spec import Spec, parse_spec, spec_to_dict
from harness.verifier import Check


def test_checkpoint_roundtrip(tmp_path):
    cp = Checkpoint(spec={"name": "x"}, config={"workspace": "/w"}, workspace="/w",
                    status="failed", stop_reason="verify-failed", rounds=2, tokens_used=99,
                    progress=[1, 0])
    path = str(tmp_path / "cp.json")
    assert not checkpoint_exists(path)
    save_checkpoint(path, cp)
    assert checkpoint_exists(path)
    loaded = load_checkpoint(path)
    assert loaded.status == "failed" and loaded.rounds == 2 and loaded.progress == [1, 0]


def test_config_roundtrip():
    c = HarnessConfig(workspace="/w", engine=EngineConfig(provider="local", model="m"),
                      review_panel=["quality", "bugs"], max_tokens_budget=500, checkpoint_path="/cp")
    c2 = config_from_dict(config_to_dict(c))
    assert c2 == c


def test_spec_roundtrip():
    s = Spec(name="app", description="d", kind="frontend", language="html",
             constraints=["x"], checks=[Check(name="t", command="true", timeout=30)])
    s2 = parse_spec(spec_to_dict(s))
    assert s2.name == "app" and s2.kind == "frontend"
    assert s2.checks[0].command == "true" and s2.checks[0].timeout == 30
