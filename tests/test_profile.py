"""Tests for the design profile / project memory: load/save, prompt rendering,
palette extraction + learning, and injection into the build prompt."""

from __future__ import annotations

from harness import profile
from harness.prompts import build_prompt
from harness.spec import Spec


def test_load_missing_returns_empty(tmp_path):
    p = profile.load(str(tmp_path / "nope.json"))
    assert profile.is_empty(p) and p["auto_learn"] is True


def test_save_then_load_round_trips(tmp_path):
    path = str(tmp_path / "profile.json")
    profile.save(path, {"stack": "vanilla JS", "ui_style": "minimal",
                        "palette": "#0b0f17, #5e8cff", "auto_learn": False, "bogus": "x"})
    p = profile.load(path)
    assert p["stack"] == "vanilla JS" and p["ui_style"] == "minimal"
    assert p["palette"] == ["#0b0f17", "#5e8cff"]   # string normalised to list
    assert p["auto_learn"] is False
    assert "bogus" not in p                          # unknown keys dropped


def test_render_includes_set_fields_only():
    note = profile.render({"stack": "React", "palette": ["#5e8cff"], "ui_style": "",
                           "typography": "", "components": "", "tone": "calm", "notes": ""})
    assert "Preferred stack: React" in note
    assert "#5e8cff" in note and "calm" in note
    assert "UI style" not in note                    # blank field omitted


def test_render_empty_is_blank():
    assert profile.render(profile.empty_profile()) == ""
    assert profile.render(None) == ""


def test_extract_palette_ranks_brand_colors(tmp_path):
    (tmp_path / "styles.css").write_text(
        "a{color:#5e8cff}b{color:#5e8cff}c{color:#5e8cff}"
        "d{color:#22c55e}e{color:#000000}f{background:#ffffff}")
    pal = profile.extract_palette(str(tmp_path))
    assert pal[0] == "#5e8cff"          # most frequent first
    assert "#22c55e" in pal
    assert "#000000" not in pal and "#ffffff" not in pal  # generic black/white skipped


def test_extract_palette_skips_node_modules(tmp_path):
    (tmp_path / "app.css").write_text("a{color:#abcdef}")
    nm = tmp_path / "node_modules"; nm.mkdir()
    (nm / "x.css").write_text("b{color:#123456}")
    pal = profile.extract_palette(str(tmp_path))
    assert "#abcdef" in pal and "#123456" not in pal


def test_learn_from_workspace_merges_palette(tmp_path):
    (tmp_path / "s.css").write_text("a{color:#5e8cff}a{color:#5e8cff}")
    prof = profile.empty_profile()
    prof["palette"] = ["#111111"]
    profile.learn_from_workspace(prof, str(tmp_path))
    assert "#111111" in prof["palette"] and "#5e8cff" in prof["palette"]


def test_build_prompt_includes_profile():
    spec = Spec(name="app", description="a todo app", kind="frontend", language="html")
    note = profile.render({"stack": "vanilla JS", "palette": ["#5e8cff"], "ui_style": "minimal",
                           "typography": "", "components": "", "tone": "", "notes": ""})
    prompt = build_prompt(spec, profile_note=note)
    assert "House style" in prompt and "vanilla JS" in prompt and "#5e8cff" in prompt
    # no profile → no house-style section
    assert "House style" not in build_prompt(spec)
