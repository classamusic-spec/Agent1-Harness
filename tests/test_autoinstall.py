"""Tests for dependency auto-install from failing-build error output."""

from __future__ import annotations

import types

from harness import autoinstall


def test_scan_python_missing_module():
    text = "Traceback...\nModuleNotFoundError: No module named 'flask'\n"
    assert autoinstall.scan(text, "python") == ["flask"]


def test_scan_python_alias_and_submodule():
    text = "No module named 'cv2'\nNo module named 'PIL.Image'\n"
    pkgs = autoinstall.scan(text, "python")
    assert "opencv-python" in pkgs and "Pillow" in pkgs


def test_scan_skips_stdlib():
    assert autoinstall.scan("No module named 'os'", "python") == []
    assert autoinstall.scan("No module named 'sqlite3'", "python") == []


def test_scan_node_missing_module():
    text = "Error: Cannot find module 'express'\n    at Function...\n"
    assert autoinstall.scan(text, "node") == ["express"]


def test_scan_node_scoped_and_ignores_relative():
    text = ("Cannot find module './local'\nCannot find module 'node:fs'\n"
            "Cannot find module '@scope/pkg/sub'\nCannot find module 'lodash/merge'\n")
    pkgs = autoinstall.scan(text, "node")
    assert pkgs == ["@scope/pkg", "lodash"]   # relative + node: ignored; scoped kept


def test_scan_dedupes():
    text = "No module named 'flask'\nNo module named 'flask'\n"
    assert autoinstall.scan(text, "python") == ["flask"]


def test_install_command():
    assert autoinstall.install_command("python", ["flask", "requests"]) == \
        "python -m pip install flask requests"
    assert autoinstall.install_command("node", ["express"]) == "npm install express"
    assert autoinstall.install_command("python", []) is None


def test_plan_uses_detected_stack(tmp_path):
    (tmp_path / "app.py").write_text("import flask\n")   # -> python stack
    p = autoinstall.plan("No module named 'flask'", str(tmp_path))
    assert p["stack"] == "python" and p["packages"] == ["flask"]
    assert p["command"] == "python -m pip install flask"


def test_plan_empty_when_nothing_missing(tmp_path):
    (tmp_path / "app.py").write_text("import os\n")
    assert autoinstall.plan("some other error", str(tmp_path)) == {}


class _Runner:
    def __init__(self, rc=0):
        self.rc = rc
        self.calls = []

    def run(self, command, cwd, timeout, env=None):
        self.calls.append(command)
        return types.SimpleNamespace(returncode=self.rc, stdout="", stderr="")


def test_run_installs_and_reports(tmp_path):
    (tmp_path / "app.py").write_text("import flask\n")
    r = _Runner(rc=0)
    res = autoinstall.run("No module named 'flask'", str(tmp_path), runner=r)
    assert res["ran"] and res["ok"]
    assert r.calls == ["python -m pip install flask"]


def test_run_skips_already_attempted(tmp_path):
    (tmp_path / "app.py").write_text("import flask\n")
    r = _Runner(rc=0)
    seen = set()
    first = autoinstall.run("No module named 'flask'", str(tmp_path), runner=r, already=seen)
    assert first["ran"] and "flask" in seen
    second = autoinstall.run("No module named 'flask'", str(tmp_path), runner=r, already=seen)
    assert second["ran"] is False and second["skipped"]
    assert r.calls == ["python -m pip install flask"]   # only installed once


def test_run_no_op_when_clean(tmp_path):
    (tmp_path / "app.py").write_text("import os\n")
    assert autoinstall.run("unrelated failure", str(tmp_path), runner=_Runner())["ran"] is False
