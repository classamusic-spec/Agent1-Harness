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


def test_config_with_meter_cb_still_checkpoints(tmp_path):
    """A runtime callback on the config (the console's live tok/s meter) must not
    poison checkpoint serialization — that silently breaks Resume."""
    import json
    c = HarnessConfig(workspace="/w", engine=EngineConfig(provider="local", model="m"))
    c.meter_cb = lambda tok, rate, el: None
    d = config_to_dict(c)
    json.dumps(d)                       # must be serializable
    assert "meter_cb" not in d
    c2 = config_from_dict(d)
    assert c2.meter_cb is None          # callbacks never come back from disk
    # end-to-end through the checkpoint file
    path = str(tmp_path / "cp.json")
    save_checkpoint(path, Checkpoint(spec={}, config=d, workspace="/w"))
    assert load_checkpoint(path).config.get("engine", {}).get("model") == "m"


def test_config_from_dict_tolerates_stale_meter_cb_key():
    d = config_to_dict(HarnessConfig(workspace="/w"))
    d["meter_cb"] = None                # a stale key from an old checkpoint
    assert config_from_dict(d).workspace == "/w"
