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


def test_history_record_and_read(tmp_path):
    assert deploy.history(str(tmp_path)) == []
    deploy.record(str(tmp_path), {"provider": "fly", "url": "https://a.fly.dev", "ok": True}, ts="t1")
    deploy.record(str(tmp_path), {"provider": "fly", "url": "https://a.fly.dev", "ok": True}, ts="t2")
    h = deploy.history(str(tmp_path))
    assert len(h) == 2 and h[0]["ts"] == "t2"   # newest first


def test_logs_and_rollback_commands():
    assert deploy.logs_command("fly", "demo") == "fly logs"
    assert "wrangler pages deployment tail" in deploy.logs_command("cloudflare", "demo")
    assert deploy.rollback_command("render", "demo") == "render rollbacks create demo"
    assert deploy.logs_command("nope", "x") == ""


def test_run_action_without_cli_returns_command(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: False)
    r = deploy.run_action("fly", "logs", str(tmp_path), "demo")
    assert r["ok"] is False and r["ready"] is False and r["command"] == "fly logs"


def test_preview_target_per_provider():
    fly = deploy.preview_target("fly", "demo", "dark-mode", "abc123")
    assert fly["app"] == "demo-pv-abc123" and fly["url"] == "https://demo-pv-abc123.fly.dev"
    cf = deploy.preview_target("cloudflare", "demo", "Dark Mode!", "abc123")
    assert cf["branch"] == "dark-mode" and cf["url"] == "https://dark-mode.demo.pages.dev"


def test_preview_without_cli_returns_url_and_commands(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: False)
    r = deploy.preview("cloudflare", str(tmp_path), "demo", label="feature-x", pid="zz99")
    assert r["preview"] is True and r["ready"] is False
    assert r["url"] == "https://feature-x.demo.pages.dev"
    assert any("wrangler pages deploy" in c and "--branch feature-x" in c for c in r["commands"])
    # not recorded — it hasn't actually deployed without the CLI
    assert deploy.previews(str(tmp_path)) == []


def test_preview_with_cli_runs_and_records(tmp_path, monkeypatch):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "index.html").write_text("<x>")
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: True)

    class R:
        def run(self, command, cwd, timeout, env=None):
            return subprocess.CompletedProcess(command, 0,
                                               stdout="https://feature-x.demo.pages.dev", stderr="")

    r = deploy.preview("cloudflare", str(tmp_path), "demo", label="feature-x", runner=R(), pid="p1")
    assert r["ok"] is True and r["url"] == "https://feature-x.demo.pages.dev"
    assert any(p["id"] == "p1" for p in deploy.previews(str(tmp_path)))


def test_destroy_preview_untracks(tmp_path, monkeypatch):
    deploy._save_previews(str(tmp_path), [{"id": "p1", "app": "demo-pv-p1", "url": "u"}])
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: False)
    r = deploy.destroy_preview("fly", str(tmp_path), "demo", "p1")
    assert "fly apps destroy demo-pv-p1" in r["command"]
    assert deploy.previews(str(tmp_path)) == []   # removed from the list


def test_settings_save_and_read(tmp_path):
    assert deploy.settings(str(tmp_path)) == {}
    deploy.save_settings(str(tmp_path), provider="fly", domain="app.x.com")
    deploy.save_settings(str(tmp_path), url="https://app.x.com")  # merges, keeps provider
    s = deploy.settings(str(tmp_path))
    assert s["provider"] == "fly" and s["domain"] == "app.x.com" and s["url"] == "https://app.x.com"


def test_run_remembers_provider(tmp_path, monkeypatch):
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: True)

    class R:
        def run(self, command, cwd, timeout, env=None):
            return subprocess.CompletedProcess(command, 0,
                                               stdout="https://demo.pages.dev", stderr="")

    deploy.run("cloudflare", str(tmp_path), "demo", runner=R())
    assert deploy.settings(str(tmp_path))["provider"] == "cloudflare"


def test_add_domain_remembers_domain(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: False)
    deploy.add_domain("fly", str(tmp_path), "demo", "app.example.com")
    s = deploy.settings(str(tmp_path))
    assert s["provider"] == "fly" and s["domain"] == "app.example.com"


def test_push_secrets_never_leaks_values(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: True)
    captured = {}

    class R:
        def run(self, command, cwd, timeout, env=None):
            captured["cmd"] = command  # the REAL command (has values) stays server-side
            return subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")

    r = deploy.push_secrets("fly", str(tmp_path), "demo",
                            secrets={"SECRET_KEY": "s3cr3t", "DATABASE_URL": "app.db"}, runner=R())
    assert r["ok"] is True
    assert r["keys"] == ["DATABASE_URL", "SECRET_KEY"]
    # the masked command + nothing in the response exposes the value
    assert "***" in r["command"]
    blob = repr(r)
    assert "s3cr3t" not in blob
    # but the real command actually carried the value
    assert "s3cr3t" in captured["cmd"]


def test_push_secrets_empty_env(tmp_path):
    r = deploy.push_secrets("fly", str(tmp_path), "demo", secrets={})
    assert r["ok"] is False and "no secrets" in r["reason"]


def test_push_secrets_non_fly_returns_commands(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: True)
    r = deploy.push_secrets("cloudflare", str(tmp_path), "demo", secrets={"K": "v"})
    assert r["ok"] is False and "wrangler pages secret put K" in r["command"]
    assert "v" not in r["command"].replace("***", "")   # value masked


def test_push_secrets_reads_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("API_KEY=abc123\nEMPTY=\n")
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: False)
    r = deploy.push_secrets("fly", str(tmp_path), "demo")
    assert r["keys"] == ["API_KEY"]      # EMPTY skipped
    assert r["ready"] is False


def test_domain_command_and_dns_hint():
    assert deploy.domain_command("fly", "demo", "app.x.com") == "fly certs add app.x.com"
    assert "pages.dev" in deploy.dns_hint("cloudflare", "demo", "app.x.com")
    assert "onrender.com" in deploy.dns_hint("render", "demo", "app.x.com")


def test_add_domain_rejects_bad_domain(tmp_path):
    r = deploy.add_domain("fly", str(tmp_path), "demo", "not a domain")
    assert r["ok"] is False and "valid domain" in r["reason"]


def test_add_domain_without_cli_returns_command_and_dns(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: False)
    r = deploy.add_domain("fly", str(tmp_path), "demo", "app.example.com")
    assert r["ready"] is False and r["command"] == "fly certs add app.example.com"
    assert "example.com" in r["dns"]


def test_add_domain_runs_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: True)

    class R:
        def run(self, command, cwd, timeout, env=None):
            return subprocess.CompletedProcess(command, 0, stdout="cert created", stderr="")

    r = deploy.add_domain("fly", str(tmp_path), "demo", "app.example.com", runner=R())
    assert r["ok"] is True and r["url"] == "https://app.example.com"


def test_run_action_runs_cli_and_strips_comment(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "cli_available", lambda prov, which=None: True)
    calls = []

    class R:
        def run(self, command, cwd, timeout, env=None):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, stdout="rolled back", stderr="")

    r = deploy.run_action("fly", "rollback", str(tmp_path), "demo", runner=R())
    assert r["ok"] is True and r["output"] == "rolled back"
    assert calls == ["fly releases"]   # inline comment stripped
