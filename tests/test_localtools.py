"""Offline tests for the local-engine toolbox (no LLM, no network)."""

from __future__ import annotations

import pytest

from harness.localtools import ToolBox, ToolError
from harness.spec import Spec
from harness.verifier import Check


def _box(tmp_path) -> ToolBox:
    spec = Spec(name="t", description="d", checks=[Check(name="ok", command="true")])
    return ToolBox(str(tmp_path), spec)


def test_write_then_read(tmp_path):
    box = _box(tmp_path)
    box.write_file("a/b.txt", "hello")
    assert box.read_file("a/b.txt") == "hello"


def test_write_escaping_workspace_is_blocked(tmp_path):
    box = _box(tmp_path)
    with pytest.raises(ToolError):
        box.write_file("../escape.txt", "nope")


def test_edit_requires_unique_match(tmp_path):
    box = _box(tmp_path)
    box.write_file("f.txt", "x x")
    with pytest.raises(ToolError):
        box.edit_file("f.txt", "x", "y")  # not unique
    box.write_file("g.txt", "alpha beta")
    assert "edited" in box.edit_file("g.txt", "beta", "gamma")
    assert box.read_file("g.txt") == "alpha gamma"


def test_edit_missing_text(tmp_path):
    box = _box(tmp_path)
    box.write_file("f.txt", "abc")
    with pytest.raises(ToolError):
        box.edit_file("f.txt", "zzz", "y")


def test_search(tmp_path):
    box = _box(tmp_path)
    box.write_file("a.py", "import os\nDEBUG = True\n")
    out = box.search("DEBUG")
    assert "a.py:2" in out


def test_run_bash_runs_in_workspace(tmp_path):
    box = _box(tmp_path)
    box.write_file("hi.txt", "x")
    out = box.run_bash("ls")
    assert "hi.txt" in out
    assert "exit 0" in out


def test_run_bash_blocks_dangerous(tmp_path):
    box = _box(tmp_path)
    with pytest.raises(ToolError):
        box.run_bash("sudo rm -rf /")


def test_verify_uses_suite(tmp_path):
    box = _box(tmp_path)
    assert "ALL CHECKS PASSED" in box.verify()


def test_dispatch_wraps_errors_as_strings(tmp_path):
    box = _box(tmp_path)
    assert box.dispatch("read_file", {"path": "missing"}).startswith("ERROR:")
    assert box.dispatch("nope", {}).startswith("ERROR: unknown tool")
    assert box.dispatch("write_file", {"path": "x"}).startswith("ERROR: missing argument")


def test_schemas_shape(tmp_path):
    box = _box(tmp_path)
    names = {s["function"]["name"] for s in box.schemas()}
    assert {"read_file", "write_file", "edit_file", "run_bash", "verify"} <= names
    for s in box.schemas():
        assert s["type"] == "function"
        assert "parameters" in s["function"]
