"""Tests for the template marketplace: capture, save/list/load, apply (with
path-traversal guard + no overwrite), and from_workspace metadata."""

from __future__ import annotations

from harness import templates


def test_capture_files_skips_caches_secrets_binaries(tmp_path):
    (tmp_path / "index.html").write_text("<h1>hi</h1>")
    (tmp_path / ".env").write_text("SECRET=x")
    (tmp_path / "app.db").write_text("db")
    nm = tmp_path / "node_modules"; nm.mkdir(); (nm / "d.js").write_text("//")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n")
    files = templates.capture_files(str(tmp_path))
    assert "index.html" in files
    assert ".env" not in files and "app.db" not in files
    assert not any("node_modules" in k for k in files)
    assert "logo.png" not in files


def test_from_workspace_and_meta(tmp_path):
    (tmp_path / "index.html").write_text("<x>")
    t = templates.from_workspace(str(tmp_path), "My Starter", "a nice base",
                                 kind="frontend", run="python -m http.server",
                                 profile={"ui_style": "minimal", "palette": ["#5e8cff"]})
    assert t["name"] == "My Starter" and t["kind"] == "frontend"
    assert t["run"] == "python -m http.server"
    assert "index.html" in t["files"]
    assert t["profile"]["ui_style"] == "minimal"


def test_save_list_load_roundtrip(tmp_path):
    store = str(tmp_path / "store")
    templates.save(store, {"name": "Cool Base", "description": "d", "kind": "react",
                           "files": {"a.txt": "x"}, "profile": {"palette": ["#000"]}})
    lst = templates.list_templates(store)
    assert len(lst) == 1
    item = lst[0]
    assert item["id"] == "cool-base" and item["files"] == 1 and item["has_profile"] is True
    full = templates.load(store, "Cool Base")
    assert full["files"]["a.txt"] == "x"
    assert templates.load(store, "missing") is None


def test_save_requires_name(tmp_path):
    try:
        templates.save(str(tmp_path), {"files": {}})
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_apply_writes_files_and_returns_settings(tmp_path):
    t = {"name": "t", "kind": "fullstack", "run": "python server.py", "scaffold": "python-db",
         "plan": True, "multi": False, "profile": {"ui_style": "calm"},
         "files": {"server.py": "print(1)", "public/index.html": "<x>"}}
    ws = tmp_path / "new"
    settings = templates.apply(t, str(ws))
    assert (ws / "server.py").read_text() == "print(1)"
    assert (ws / "public" / "index.html").is_file()
    assert settings["kind"] == "fullstack" and settings["scaffold"] == "python-db"
    assert settings["plan"] is True and settings["profile"]["ui_style"] == "calm"


def test_apply_no_overwrite_and_traversal_guard(tmp_path):
    ws = tmp_path / "new"; ws.mkdir()
    (ws / "keep.txt").write_text("original")
    t = {"name": "t", "files": {"keep.txt": "REPLACED", "../escape.txt": "evil"}}
    templates.apply(t, str(ws))
    assert (ws / "keep.txt").read_text() == "original"      # not overwritten
    assert not (tmp_path / "escape.txt").exists()           # traversal blocked
