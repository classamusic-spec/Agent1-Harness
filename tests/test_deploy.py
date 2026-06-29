"""Tests for one-click deploy: config generation, commands, URL parsing, the
plan, and run() behavior when the CLI is present vs missing (no real network)."""

from __future__ import annotations

import subprocess

from harness import deploy


def test_slug_sanitizes():
    assert deploy.slug("My Cool App!") == "my-cool-app"
    assert deploy.slug("") == "app"


def test_deployed_url_per_provider():
    assert deploy.deployed_url("fly", "My App") == "https://my-app.fly.dev"
    assert deploy.deployed_url("cloudflare", "site") == "https://site.pages.dev"
    assert deploy.deployed_url("render", "api") == "https://api.onrender.com"


def test_fly_config_has_dockerfile_and_port(tmp_path):
    files = deploy.config_files("fly", str(tmp_path), "demo", port=9000)
    assert "fly.toml" in files
    assert 'app = "demo"' in files["fly.toml"]
    assert "internal_port = 9000" in files["fly.toml"]


def test_render_config_is_blueprint(tmp_path):
    files = deploy.config_files("render", str(tmp_path), "demo", port=8000)
    assert "render.yaml" in files
    assert "runtime: docker" in files["render.yaml"]


def test_cloudflare_uses_public_dir(tmp_path):
    (tmp_path / "dist").mkdir()
    cmds = deploy.commands("cloudflare", "site", str(tmp_path))
    assert any("wrangler pages deploy dist" in c for c in cmds)


def test_extract_url_from_output_else_fallback():
    out = "Success! Take a peek over at https://abc123.site.pages.dev"
    assert deploy.extract_url("cloudflare", "site", out) == "https://abc123.site.pages.dev"
    assert deploy.extract_url("fly", "demo", "no url here") == "https://demo.fly.dev"


def test_plan_includes_dockerfile_for_docker_providers(tmp_path):
    (tmp_path / "server.py").write_text("x=1")
    p = deploy.plan("fly", str(tmp_path), "demo", port=8000)
    assert p["kind"] == "docker"
    assert "Dockerfile" in p["files"] and "fly.toml" in p["files"]
    assert p["commands"] and p["url"] == "https://demo.fly.dev"


def test_plan_unknown_provider():
    assert "error" in deploy.plan("heroku", "/tmp", "x")


def test_run_without_cli_returns_commands(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: False)
    res = deploy.run("cloudflare", str(tmp_path), "site", write=True)
    assert res["ok"] is False and res["ready"] is False
    assert res["commands"] and "wrangler" in res["commands"][0]
    assert res["url"] == "https://site.pages.dev"


def test_run_with_cli_invokes_runner_and_parses_url(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    (tmp_path / "public").mkdir()
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: True)

    calls = []

    class FakeRunner:
        def run(self, command, cwd, timeout, env=None):
            calls.append(command)
            return subprocess.CompletedProcess(
                command, 0, stdout="Deployed to https://xyz.site.pages.dev", stderr="")

    res = deploy.run("cloudflare", str(tmp_path), "site", runner=FakeRunner())
    assert res["ok"] is True
    assert res["url"] == "https://xyz.site.pages.dev"
    assert any("wrangler pages deploy" in c for c in calls)


def test_run_reports_command_failure(tmp_path, monkeypatch):
    (tmp_path / "server.py").write_text("x=1")
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: True)

    class FailRunner:
        def run(self, command, cwd, timeout, env=None):
            return subprocess.CompletedProcess(command, 1, stdout="", stderr="boom")

    res = deploy.run("fly", str(tmp_path), "demo", runner=FailRunner())
    assert res["ok"] is False and res["ready"] is True
    assert "exited 1" in res["reason"]


def test_list_providers_shape():
    provs = {p["id"] for p in deploy.list_providers()}
    assert {"fly", "cloudflare", "render"} <= provs


def test_check_live_true_on_2xx_3xx():
    assert deploy.check_live("https://x.fly.dev", probe=lambda u, **k: 200)["live"] is True
    assert deploy.check_live("https://x.fly.dev", probe=lambda u, **k: 301)["live"] is True


def test_check_live_false_on_5xx_or_unreachable():
    assert deploy.check_live("https://x.fly.dev", probe=lambda u, **k: 502)["live"] is False
    assert deploy.check_live("https://x.fly.dev", probe=lambda u, **k: 0)["live"] is False
    out = deploy.check_live("", probe=lambda u, **k: 200)
    assert out["live"] is False and out["status"] == 0
