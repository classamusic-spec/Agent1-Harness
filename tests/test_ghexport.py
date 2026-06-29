"""Tests for GitHub export: slug, .gitignore safety, command building, URL parse,
and export() for gh-present (fake runner) vs gh-absent (commits + returns commands)."""

from __future__ import annotations

import subprocess

from harness import ghexport


def test_slug():
    assert ghexport.slug("My Cool App!") == "my-cool-app"
    assert ghexport.slug("") == "app"


def test_ensure_gitignore_protects_secrets(tmp_path):
    assert ghexport.ensure_gitignore(str(tmp_path)) is True
    body = (tmp_path / ".gitignore").read_text()
    assert ".env" in body and "node_modules/" in body and "*.db" in body
    assert "!.env.example" in body          # example stays trackable
    assert ghexport.ensure_gitignore(str(tmp_path)) is False  # never overwrite


def test_push_commands_visibility():
    pub = ghexport.push_commands("demo", private=False)
    assert any("--public" in c for c in pub)
    priv = ghexport.push_commands("demo", private=True)
    assert any("--private" in c for c in priv)


def test_extract_repo_url():
    assert ghexport.extract_repo_url("Created repo https://github.com/me/demo") == "https://github.com/me/demo"
    assert ghexport.extract_repo_url("https://github.com/me/demo.git") == "https://github.com/me/demo"
    assert ghexport.extract_repo_url("nope") == ""


class _Runner:
    def __init__(self, url_out=""):
        self.cmds = []
        self.url_out = url_out

    def run(self, command, cwd, timeout, env=None):
        self.cmds.append(command)
        out = self.url_out if command.startswith("gh repo create") else ""
        return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")


def test_export_without_gh_commits_and_returns_commands(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<x>")
    monkeypatch.setattr(ghexport, "git_available", lambda which=None: True)
    monkeypatch.setattr(ghexport, "gh_available", lambda which=None: False)
    r = _Runner()
    out = ghexport.export(str(tmp_path), "Demo", runner=r)
    assert out["ok"] is False and out["committed"] is True
    assert any("git init" in c for c in r.cmds)
    assert any("commit" in c for c in r.cmds)
    assert any("gh repo create" in c for c in out["commands"])
    assert (tmp_path / ".gitignore").exists()


def test_export_with_gh_creates_repo_and_returns_url(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<x>")
    monkeypatch.setattr(ghexport, "git_available", lambda which=None: True)
    monkeypatch.setattr(ghexport, "gh_available", lambda which=None: True)
    r = _Runner(url_out="Created repository https://github.com/me/demo on GitHub")
    out = ghexport.export(str(tmp_path), "Demo", private=False, runner=r)
    assert out["ok"] is True
    assert out["url"] == "https://github.com/me/demo"
    assert any("gh repo create demo --public" in c for c in r.cmds)


def test_export_without_git_returns_instructions(tmp_path, monkeypatch):
    monkeypatch.setattr(ghexport, "git_available", lambda which=None: False)
    out = ghexport.export(str(tmp_path), "Demo")
    assert out["ok"] is False and out["ready"] is False
    assert "git" in out["reason"]
