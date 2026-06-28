"""Tests for the Ship it exporter: stack-aware Dockerfile, compose, and the
deployment zip (excludes secrets/DBs/caches, includes generated deploy files)."""

from __future__ import annotations

import io
import zipfile

from harness import ship


def _node_ws(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"name":"x","scripts":{"build":"vite build","preview":"vite preview"}}')
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    return tmp_path


def test_dockerfile_node_has_build_and_bind(tmp_path):
    df = ship.dockerfile_for(str(_node_ws(tmp_path)), port=8080)
    assert "FROM node:20-slim" in df
    assert "npm ci || npm install" in df
    assert "RUN npm run build" in df          # build script detected
    assert "EXPOSE 8080" in df and "ENV PORT=8080" in df
    assert "--host 0.0.0.0 --port ${PORT}" in df  # preview needs an explicit bind


def test_dockerfile_python_installs_requirements(tmp_path):
    (tmp_path / "server.py").write_text("print('hi')")
    (tmp_path / "requirements.txt").write_text("flask\n")
    df = ship.dockerfile_for(str(tmp_path))
    assert "FROM python:3.12-slim" in df
    assert "pip install --no-cache-dir -r requirements.txt" in df
    assert "CMD python server.py" in df


def test_dockerfile_python_substitutes_port_placeholder(tmp_path):
    # detect_command yields `python -m http.server $PORT` only for static; a .py
    # file makes it python — provide an explicit run command with $PORT.
    (tmp_path / "app.py").write_text("x=1")
    df = ship.dockerfile_for(str(tmp_path), run_command="uvicorn app:app --port $PORT")
    assert "uvicorn app:app --port ${PORT}" in df  # $PORT normalised to ${PORT}


def test_dockerfile_static_uses_http_server(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    df = ship.dockerfile_for(str(tmp_path))
    assert "FROM python:3.12-slim" in df
    assert "python -m http.server ${PORT}" in df


def test_compose_wires_port_and_env(tmp_path):
    c = ship.compose_for("My App!", port=9000, has_env=True)
    assert "my-app:" in c                  # name sanitised
    assert '"9000:9000"' in c
    assert "PORT=9000" in c
    assert "env_file:" in c and "- .env" in c


def test_compose_without_env_omits_env_file():
    c = ship.compose_for("app", port=8000, has_env=False)
    assert "env_file" not in c


def test_write_export_does_not_overwrite(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>x</title>")
    (tmp_path / "Dockerfile").write_text("# hand-written")
    written = ship.write_export(str(tmp_path), name="app")
    assert "docker-compose.yml" in written and ".dockerignore" in written
    assert "Dockerfile" not in written  # existing one preserved
    assert (tmp_path / "Dockerfile").read_text() == "# hand-written"
    # overwrite=True replaces it
    written2 = ship.write_export(str(tmp_path), name="app", overwrite=True)
    assert "Dockerfile" in written2


def _zip_names(data: bytes) -> set[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return set(zf.namelist())


def test_zip_excludes_secrets_caches_and_dbs(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "server.py").write_text("x=1")
    (ws / "requirements.txt").write_text("flask\n")
    (ws / ".env").write_text("SECRET_KEY=supersecret")
    (ws / ".env.example").write_text("SECRET_KEY=changeme")
    (ws / "app.db").write_text("binary")
    (ws / "out.log").write_text("noise")
    (ws / "node_modules").mkdir()
    (ws / "node_modules" / "dep.js").write_text("//")
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text("y=2")

    names = _zip_names(ship.zip_bytes(str(ws), name="proj"))
    assert "proj/server.py" in names
    assert "proj/src/main.py" in names
    assert "proj/.env.example" in names         # example kept
    assert "proj/.env" not in names             # real secret excluded
    assert "proj/app.db" not in names           # local DB excluded
    assert "proj/out.log" not in names
    assert not any("node_modules" in n for n in names)
    # generated deployment files are added
    assert {"proj/Dockerfile", "proj/docker-compose.yml", "proj/.dockerignore"} <= names


def test_zip_generated_dockerfile_wins_over_disk(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "index.html").write_text("<!doctype html><title>x</title>")
    (ws / "Dockerfile").write_text("# stale on disk")
    data = ship.zip_bytes(str(ws), name="proj")
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        content = zf.read("proj/Dockerfile").decode()
    assert "FROM" in content and "# stale on disk" not in content


def test_export_zip_writes_file(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    (ws / "index.html").write_text("<!doctype html><title>x</title>")
    dest = tmp_path / "out" / "proj.zip"
    path = ship.export_zip(str(ws), str(dest), name="proj")
    assert path == str(dest) and dest.is_file()
    assert _zip_names(dest.read_bytes())  # non-empty archive
