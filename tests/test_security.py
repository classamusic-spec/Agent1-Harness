"""Tests for the deterministic security scanner: detection, placeholder filtering,
workspace walking, severity gating, and the CLI."""

from __future__ import annotations

from harness import security


def _rules(findings):
    return {f.rule for f in findings}


def test_detects_hardcoded_secrets():
    f = security.scan_text("config.py",
                           'API_KEY = "sk-live-abcd1234efgh5678ijkl"\n')
    assert "generic-secret" in _rules(f)
    assert f[0].severity == "high" and f[0].line == 1


def test_detects_private_key_and_aws():
    pk = security.scan_text("id_rsa", "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB\n")
    assert "private-key" in _rules(pk)
    aws = security.scan_text("a.py", 'key = "AKIAIOSFODNN7EXAMPLE"\n')
    assert "aws-access-key" in _rules(aws)


def test_ignores_placeholder_and_env_reads():
    assert security.scan_text("a.py", 'password = "changeme"\n') == []
    assert security.scan_text("a.py", 'password = os.environ["PW"]\n') == []
    assert security.scan_text("a.py", 'SECRET_KEY = "your-secret-here"\n') == []
    assert security.scan_text(".env", "SECRET_KEY=${SECRET_KEY}\n") == []


def test_detects_sql_injection():
    f = security.scan_text("db.py",
                           'cursor.execute(f"SELECT * FROM users WHERE id = {uid}")\n')
    assert "sql-injection" in _rules(f)
    g = security.scan_text("db.py",
                           'cursor.execute("SELECT * FROM t WHERE n = " + name)\n')
    assert "sql-injection" in _rules(g)


def test_parameterised_sql_is_clean():
    assert security.scan_text("db.py",
                              'cursor.execute("SELECT * FROM t WHERE id = ?", (uid,))\n') == []


def test_detects_command_injection():
    assert "shell-injection" in _rules(
        security.scan_text("a.py", "subprocess.run(cmd, shell=True)\n"))
    assert "os-system" in _rules(security.scan_text("a.py", "os.system(user_input)\n"))
    assert "js-child-exec" in _rules(
        security.scan_text("a.js", "child_process.exec(userCmd)\n"))


def test_detects_unsafe_deserialization_and_eval():
    assert "pickle-loads" in _rules(security.scan_text("a.py", "data = pickle.loads(raw)\n"))
    assert "yaml-load" in _rules(security.scan_text("a.py", "cfg = yaml.load(s)\n"))
    assert "py-eval-exec" in _rules(security.scan_text("a.py", "eval(expr)\n"))
    assert "js-eval" in _rules(security.scan_text("a.js", "eval(x)\n"))


def test_yaml_safeloader_is_clean():
    assert security.scan_text("a.py", "yaml.load(s, Loader=yaml.SafeLoader)\n") == []


def test_detects_dom_xss_and_weak_crypto_and_tls():
    assert "dom-xss" in _rules(security.scan_text("a.js", "el.innerHTML = user\n"))
    assert "weak-hash" in _rules(security.scan_text("a.py", "hashlib.md5(p).hexdigest()\n"))
    assert "tls-verify-off" in _rules(
        security.scan_text("a.py", "requests.get(u, verify=False)\n"))


def test_comment_lines_skip_code_rules_but_not_secrets():
    # commented-out eval is not flagged
    assert security.scan_text("a.py", "# eval(x) here\n") == []
    # but a secret in a comment still is
    assert "generic-secret" in _rules(
        security.scan_text("a.py", '# api_key = "sk-abcd1234efgh"\n'))


def test_scan_workspace_walks_and_skips(tmp_path):
    (tmp_path / "app.py").write_text('token = "AKIAIOSFODNN7EXAMPLE"\n')
    (tmp_path / ".env.example").write_text('SECRET_KEY=changeme\n')  # skipped
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("eval(x)\n")  # skipped dir
    sub = tmp_path / "src"; sub.mkdir()
    (sub / "db.py").write_text('cursor.execute(f"SELECT {x}")\n')
    findings = security.scan_workspace(str(tmp_path))
    files = {f.file for f in findings}
    assert "app.py" in files
    assert any(f.endswith("db.py") for f in files)
    assert not any("node_modules" in f for f in files)
    assert not any(".env.example" in f for f in files)


def test_blocking_and_counts():
    findings = security.scan_text("a.py",
        'api_key = "sk-abcd1234efgh"\n'   # high
        "hashlib.md5(p)\n")               # low
    c = security.counts(findings)
    assert c["high"] == 1 and c["low"] == 1
    assert len(security.blocking(findings)) == 1            # high blocks by default
    assert len(security.blocking(findings, ("high", "low"))) == 2


def test_scan_workspace_sorted_high_first(tmp_path):
    (tmp_path / "a.py").write_text("hashlib.md5(p)\n")          # low
    (tmp_path / "b.py").write_text('api_key = "sk-abcd1234efgh"\n')  # high
    findings = security.scan_workspace(str(tmp_path))
    assert findings[0].severity == "high"


def test_cli_exit_code(tmp_path, capsys):
    (tmp_path / "a.py").write_text('api_key = "sk-abcd1234efgh5678"\n')
    rc = security._main([str(tmp_path)])
    assert rc == 1  # high finding -> non-zero
    out = capsys.readouterr().out
    assert "high" in out and "generic-secret" in out
    # only blocking on low leaves the high unblocked -> exit 0
    assert security._main([str(tmp_path), "--block", "low"]) == 0


def test_clean_project_passes(tmp_path):
    (tmp_path / "a.py").write_text(
        "import os\n"
        'API_KEY = os.environ["API_KEY"]\n'
        'cursor.execute("SELECT * FROM t WHERE id = ?", (uid,))\n')
    assert security.scan_workspace(str(tmp_path)) == []
