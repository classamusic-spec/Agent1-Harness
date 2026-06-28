"""Tests for image-referenced builds: vision helpers, config wiring, the design
resolver (two-stage + direct), and the server's reference-image saver."""

from __future__ import annotations

import asyncio
import base64

from harness import vision
from harness.agent import _resolve_design
from harness.config import HarnessConfig
from harness.prompts import build_prompt
from harness.server import save_reference_image
from harness.spec import Spec


def test_mime_for_and_data_url():
    assert vision.mime_for("a/b/shot.PNG") == "image/png"
    assert vision.mime_for("x.jpg") == "image/jpeg"
    assert vision.mime_for("x.unknown") == "image/png"
    url = vision.to_data_url(b"hello", "image/png")
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"hello"


def test_vision_engine_optional():
    assert HarnessConfig(workspace="/w").vision_engine() is None
    cfg = HarnessConfig(workspace="/w", vision_model="qwen2.5-vl", vision_base_url="http://x/v1")
    ve = cfg.vision_engine()
    assert ve is not None and ve.model == "qwen2.5-vl" and ve.base_url == "http://x/v1"


def test_build_prompt_includes_brief_only_when_present():
    spec = Spec(name="app", description="do things", kind="frontend")
    assert "Reference design" not in build_prompt(spec)
    p = build_prompt(spec, "## Layout\nA centered card.")
    assert "Reference design" in p and "centered card" in p


def test_resolve_design_returns_precomputed_brief(tmp_path):
    cfg = HarnessConfig(workspace=str(tmp_path), design_brief="ready brief")
    assert asyncio.run(_resolve_design(cfg, str(tmp_path), False)) == "ready brief"


def test_resolve_design_empty_without_image(tmp_path):
    cfg = HarnessConfig(workspace=str(tmp_path))
    assert asyncio.run(_resolve_design(cfg, str(tmp_path), False)) == ""


def test_resolve_design_direct_stages_image(tmp_path):
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n fake image bytes")
    ws = tmp_path / "ws"; ws.mkdir()
    cfg = HarnessConfig(workspace=str(ws), reference_image=str(ref))  # no vision model -> direct
    out = asyncio.run(_resolve_design(cfg, str(ws), False))
    assert ".studio/reference.png" in out.replace("\\", "/")
    assert (ws / ".studio" / "reference.png").is_file()


def test_resolve_design_two_stage_uses_vision(tmp_path, monkeypatch):
    ref = tmp_path / "ref.png"; ref.write_bytes(b"img")
    ws = tmp_path / "ws"; ws.mkdir()
    cfg = HarnessConfig(workspace=str(ws), reference_image=str(ref),
                        vision_model="qwen2.5-vl", vision_base_url="http://x/v1")

    async def fake_describe(data, mime, engine, **kw):
        assert data == b"img" and engine.model == "qwen2.5-vl"
        return "## Layout\nTwo-column dashboard."

    monkeypatch.setattr(vision, "describe_ui", fake_describe)
    out = asyncio.run(_resolve_design(cfg, str(ws), False))
    assert "Two-column dashboard" in out
    # two-stage does not need to stage the image into the workspace
    assert not (ws / ".studio" / "reference.png").exists()


def test_resolve_design_vision_failure_falls_back(tmp_path, monkeypatch):
    ref = tmp_path / "ref.png"; ref.write_bytes(b"img")
    ws = tmp_path / "ws"; ws.mkdir()
    cfg = HarnessConfig(workspace=str(ws), reference_image=str(ref), vision_model="vl")

    async def boom(*a, **k):
        raise RuntimeError("no vision server")

    monkeypatch.setattr(vision, "describe_ui", boom)
    assert asyncio.run(_resolve_design(cfg, str(ws), False)) == ""


def test_save_reference_image_writes_under_studio(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    data_url = "data:image/png;base64," + base64.b64encode(b"PNGDATA").decode()
    path = save_reference_image(str(ws), data_url)
    assert path.endswith(".png") and ".studio" in path
    with open(path, "rb") as fh:
        assert fh.read() == b"PNGDATA"
