"""Tests for the repo map / context packer."""

from __future__ import annotations

from harness import repomap


def test_collect_extracts_python_symbols(tmp_path):
    (tmp_path / "app.py").write_text(
        "import os\n\ndef main():\n    pass\n\nclass Server:\n    def handle(self):\n        pass\n")
    files = repomap.collect(str(tmp_path))
    assert len(files) == 1
    f = files[0]
    assert f["path"] == "app.py"
    assert "def main" in f["symbols"] and "class Server" in f["symbols"]


def test_collect_extracts_js_symbols(tmp_path):
    (tmp_path / "app.js").write_text(
        "export function render() {}\nconst API = '/x';\nclass Widget {}\n")
    syms = repomap.collect(str(tmp_path))[0]["symbols"]
    assert "function render" in syms and "API" in syms and "class Widget" in syms


def test_collect_skips_vendor_and_nonsource(tmp_path):
    (tmp_path / "main.py").write_text("def a():\n    pass\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("function nope(){}")
    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n")
    paths = {f["path"] for f in repomap.collect(str(tmp_path))}
    assert paths == {"main.py"}


def test_render_is_compact_and_lists_files(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n")
    (tmp_path / "b.css").write_text("body { color: red; }\n")
    out = repomap.render(str(tmp_path))
    assert "Repo map (2 file(s))" in out
    assert "a.py" in out and "def foo" in out
    assert "b.css" in out          # non-code files are listed (no symbols needed)


def test_render_empty_for_no_sources(tmp_path):
    (tmp_path / "data.bin").write_bytes(b"\x00\x01")
    assert repomap.render(str(tmp_path)) == ""


def test_render_respects_byte_ceiling(tmp_path):
    for i in range(40):
        (tmp_path / f"mod{i}.py").write_text("\n".join(f"def fn{j}():\n    pass" for j in range(20)))
    out = repomap.render(str(tmp_path), max_bytes=800)
    assert len(out) <= 900 and "truncated" in out


def test_symbol_cap_per_file(tmp_path):
    (tmp_path / "big.py").write_text("\n".join(f"def fn{i}():\n    pass" for i in range(50)))
    syms = repomap.collect(str(tmp_path), max_symbols=5)[0]["symbols"]
    assert len(syms) == 5
