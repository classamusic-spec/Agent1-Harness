"""Tests for CI workflow generation: stack-aware content + safe writing, and that
GitHub export includes the workflow."""

from __future__ import annotations

from harness import ci, ghexport


def test_workflow_node(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    wf = ci.workflow_for(str(tmp_path))
    assert "setup-node" in wf and "npm ci" in wf and "npm test --if-present" in wf


def test_workflow_python(tmp_path):
    (tmp_path / "server.py").write_text("x=1")
    wf = ci.workflow_for(str(tmp_path))
    assert "setup-python" in wf and "compileall" in wf and "pytest" in wf


def test_workflow_static(tmp_path):
    (tmp_path / "index.html").write_text("<x>")
    wf = ci.workflow_for(str(tmp_path))
    assert "index.html present" in wf


def test_write_workflow_no_overwrite(tmp_path):
    (tmp_path / "index.html").write_text("<x>")
    assert ci.write_workflow(str(tmp_path)) is True
    dest = tmp_path / ".github" / "workflows" / "ci.yml"
    assert dest.is_file()
    assert ci.write_workflow(str(tmp_path)) is False         # already there
    assert ci.write_workflow(str(tmp_path), overwrite=True) is True


def test_export_adds_ci_workflow(tmp_path, monkeypatch):
    import subprocess
    (tmp_path / "index.html").write_text("<x>")
    monkeypatch.setattr(ghexport, "git_available", lambda which=None: True)
    monkeypatch.setattr(ghexport, "gh_available", lambda which=None: False)

    class R:
        def run(self, command, cwd, timeout, env=None):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    ghexport.export(str(tmp_path), "demo", ci=True, runner=R())
    assert (tmp_path / ".github" / "workflows" / "ci.yml").is_file()


def test_export_can_skip_ci(tmp_path, monkeypatch):
    import subprocess
    (tmp_path / "index.html").write_text("<x>")
    monkeypatch.setattr(ghexport, "git_available", lambda which=None: True)
    monkeypatch.setattr(ghexport, "gh_available", lambda which=None: False)

    class R:
        def run(self, command, cwd, timeout, env=None):
            return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    ghexport.export(str(tmp_path), "demo", ci=False, runner=R())
    assert not (tmp_path / ".github").exists()
