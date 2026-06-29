"""Tests for team sharing: export a bundle (templates + profile) and import it
into a fresh store."""

from __future__ import annotations

from harness import profile, share, templates


def _seed_store(d):
    templates.save(d, {"name": "Base A", "description": "a", "kind": "frontend",
                       "files": {"index.html": "<a>"}})
    templates.save(d, {"name": "Base B", "description": "b", "kind": "fullstack",
                       "files": {"server.py": "print(1)"}})


def test_export_bundle_includes_user_templates_and_profile(tmp_path):
    store = str(tmp_path / "store")
    _seed_store(store)
    bundle = share.export_bundle(store, {"ui_style": "minimal", "palette": ["#5e8cff"]})
    assert bundle["kind"] == share.BUNDLE_KIND
    assert {t["name"] for t in bundle["templates"]} == {"Base A", "Base B"}
    assert bundle["profile"]["ui_style"] == "minimal"
    # built-ins are not exported
    assert all("Minimal SPA" != t["name"] for t in bundle["templates"])


def test_import_bundle_into_fresh_store(tmp_path):
    src = str(tmp_path / "src"); _seed_store(src)
    bundle = share.export_bundle(src, {"ui_style": "calm", "palette": ["#000"]})

    dst = str(tmp_path / "dst")
    prof = str(tmp_path / "dst-profile.json")
    res = share.import_bundle(dst, prof, bundle)
    assert res["imported"] == 2 and res["profile_applied"] is True
    names = {t["id"] for t in templates.list_templates(dst, include_builtins=False)}
    assert {"base-a", "base-b"} <= names
    assert profile.load(prof)["ui_style"] == "calm"


def test_import_can_skip_profile(tmp_path):
    bundle = {"templates": [{"name": "x", "files": {"a": "b"}}], "profile": {"ui_style": "z"}}
    prof = str(tmp_path / "p.json")
    res = share.import_bundle(str(tmp_path / "d"), prof, bundle, apply_profile=False)
    assert res["imported"] == 1 and res["profile_applied"] is False
    assert profile.load(prof)["ui_style"] == ""  # untouched


def test_import_accepts_single_template(tmp_path):
    res = share.import_bundle(str(tmp_path / "d"), str(tmp_path / "p.json"),
                              {"name": "solo", "files": {"a": "b"}})
    assert res["imported"] == 1


def test_import_rejects_garbage(tmp_path):
    assert "error" in share.import_bundle(str(tmp_path / "d"), str(tmp_path / "p"), "nope")
