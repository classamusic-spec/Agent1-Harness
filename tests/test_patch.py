"""Tests for tolerant unified-diff application (surgical edits)."""

from __future__ import annotations

import pytest

from harness import patch


def test_apply_simple_change():
    original = "line1\nline2\nline3\n"
    diff = "@@ -1,3 +1,3 @@\n line1\n-line2\n+line2-edited\n line3\n"
    out = patch.apply_patch(original, diff)
    assert out == "line1\nline2-edited\nline3\n"


def test_line_numbers_in_header_are_ignored():
    original = "a\nb\nc\nd\n"
    # deliberately WRONG line numbers — should still apply by context
    diff = "@@ -99,2 +99,2 @@\n b\n-c\n+C\n"
    out = patch.apply_patch(original, diff)
    assert "C" in out and "\nc" not in out


def test_addition_only_hunk_with_context():
    original = "import os\n\ndef main():\n    pass\n"
    diff = "@@ @@\n def main():\n+    print('hi')\n     pass\n"
    out = patch.apply_patch(original, diff)
    assert "print('hi')" in out
    assert out.index("print('hi')") < out.index("pass")


def test_whitespace_tolerant_match():
    original = "def f():\n    x = 1   \n    return x\n"  # trailing spaces in file
    diff = "@@ @@\n def f():\n-    x = 1\n+    x = 2\n     return x\n"
    out = patch.apply_patch(original, diff)
    assert "x = 2" in out


def test_unmatched_hunk_raises_with_context():
    original = "alpha\nbeta\n"
    diff = "@@ @@\n nonexistent\n-gamma\n+delta\n"
    with pytest.raises(patch.PatchError) as e:
        patch.apply_patch(original, diff)
    assert "context not found" in str(e.value)


def test_empty_patch_raises():
    with pytest.raises(patch.PatchError):
        patch.apply_patch("x", "no diff here, just prose")


def test_is_creation():
    assert patch.is_creation("@@ @@\n+line one\n+line two\n") is True
    assert patch.is_creation("@@ @@\n context\n+added\n") is False
    assert patch.is_creation("@@ @@\n-removed\n") is False


def test_creation_appends_to_empty():
    out = patch.apply_patch("", "@@ @@\n+hello\n+world\n")
    assert out == "hello\nworld"
