"""Project scaffolds — known-good starter bases.

Starting from a working, runnable base beats generating everything cold: higher
success rate, fewer integration bugs, and the full-stack gate has a real toolchain
+ dev server to run. Each scaffold materialises files into the workspace and
declares its run command + verification (incl. server-backed checks).

Two scaffolds run with zero dependencies (great here and offline):
  - `static`     — vanilla HTML/CSS/JS SPA (no build step)
  - `python-api` — stdlib http.server full-stack (JSON API + static frontend)
Two are canonical real-world bases (need install on the user's machine):
  - `vite-react` — Vite + React + TypeScript
  - `fastapi`    — FastAPI + SQLite
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from harness.stacks import SMOKE_CMD

_API_HEALTH = (
    "python3 -c \"import urllib.request,sys; "
    "sys.exit(0 if urllib.request.urlopen('$APP_URL/api/health', timeout=6)"
    ".status==200 else 1)\""
)


@dataclass
class Scaffold:
    name: str
    label: str
    kind: str
    run: str
    note: str
    files: dict[str, str]
    checks: list[dict] = field(default_factory=list)
    needs: str = ""  # "", "node", or "python" (install hint for the UI)


# --------------------------------------------------------------------------- #
#  static — vanilla SPA, no build step
# --------------------------------------------------------------------------- #
_STATIC_INDEX = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>App</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <header class="bar"><strong class="brand">App</strong><nav id="nav"></nav></header>
  <main id="app" class="wrap"></main>
  <script src="app.js"></script>
</body>
</html>
"""

_STATIC_CSS = """:root{--bg:#0b0f17;--surface:#151b27;--text:#eef1f8;--muted:#9aa3b8;--accent:#5e8cff;--radius:14px}
@media (prefers-color-scheme:light){:root{--bg:#f3f5fb;--surface:#fff;--text:#1a1d29;--muted:#5b6275}}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--text)}
.bar{display:flex;align-items:center;gap:16px;padding:14px 22px;border-bottom:1px solid rgba(128,128,128,.18)}
.brand{font-weight:700}.bar nav{display:flex;gap:14px}.bar a{color:var(--muted);text-decoration:none;font-size:.9rem}
.bar a.active{color:var(--accent)}.wrap{max-width:760px;margin:0 auto;padding:28px 22px}
.card{background:var(--surface);border:1px solid rgba(128,128,128,.18);border-radius:var(--radius);padding:22px;margin:14px 0}
button{font:inherit;border:0;border-radius:10px;padding:9px 16px;background:var(--accent);color:#fff;cursor:pointer}
h1{letter-spacing:-.02em}
"""

_STATIC_JS = """'use strict';
// Tiny hash router. Add routes to `views` and links to `links`.
const app = document.getElementById('app');
const nav = document.getElementById('nav');
const links = [['#/', 'Home'], ['#/about', 'About']];
const views = {
  '#/': () => `<h1>Welcome</h1>
    <div class="card"><p>Edit <code>app.js</code> / <code>styles.css</code> to build your app.</p>
    <p>Count: <b id="n">0</b></p><button id="inc">+1</button></div>`,
  '#/about': () => `<h1>About</h1><div class="card"><p>A vanilla SPA scaffold — no build step.</p></div>`,
};
function render(){
  const route = location.hash || '#/';
  app.innerHTML = (views[route] || views['#/'])();
  nav.innerHTML = links.map(([h,l]) => `<a href="${h}" class="${h===route?'active':''}">${l}</a>`).join('');
  const inc = document.getElementById('inc');
  if (inc) { let n=0; const el=document.getElementById('n'); inc.onclick=()=>{el.textContent=++n;}; }
  console.log('view:', route);
}
addEventListener('hashchange', render); render();
"""

# --------------------------------------------------------------------------- #
#  python-api — stdlib full-stack (no dependencies)
# --------------------------------------------------------------------------- #
_PYAPI_SERVER = '''"""Zero-dependency full-stack server: static frontend (./public) + JSON API (/api).
Storage is a JSON file (data.json). Reads PORT from the environment. Stdlib only."""
import json, os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")
DB = os.path.join(ROOT, "data.json")


def load():
    try:
        return json.load(open(DB))
    except Exception:
        return {"items": []}


def save(d):
    json.dump(d, open(DB, "w"), indent=2)


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json({"ok": True})
        if path == "/api/items":
            return self._json(load()["items"])
        return self._static(path)

    def do_POST(self):
        if urlparse(self.path).path == "/api/items":
            n = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json({"error": "bad json"}, 400)
            d = load()
            item = {"id": len(d["items"]) + 1, "text": str(payload.get("text", ""))}
            d["items"].append(item)
            save(d)
            return self._json(item, 201)
        return self._json({"error": "not found"}, 404)

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(PUBLIC, rel))
        if not target.startswith(PUBLIC) or not os.path.isfile(target):
            return self._json({"error": "not found"}, 404)
        ctype = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                 ".json": "application/json"}.get(os.path.splitext(target)[1], "text/plain")
        data = open(target, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    print(f"serving on http://127.0.0.1:{port}")
    ThreadingHTTPServer(("127.0.0.1", port), H).serve_forever()
'''

_PYAPI_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Full-stack app</title><link rel="stylesheet" href="styles.css" /></head>
<body><main class="wrap">
  <h1>Items</h1>
  <form id="f" class="row"><input id="t" placeholder="New item" /><button>Add</button></form>
  <ul id="list"></ul>
</main><script src="app.js"></script></body></html>
"""

_PYAPI_FRONT_JS = """'use strict';
const list = document.getElementById('list');
async function load(){
  const items = await (await fetch('/api/items')).json();
  list.innerHTML = items.map(i => `<li>#${i.id} ${i.text}</li>`).join('') || '<li class="muted">No items yet.</li>';
  console.log('loaded', items.length, 'items');
}
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const t = document.getElementById('t');
  if (!t.value.trim()) return;
  await fetch('/api/items', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text:t.value})});
  t.value=''; load();
});
load();
"""

_PYAPI_CSS = """:root{--bg:#0b0f17;--surface:#151b27;--text:#eef1f8;--muted:#9aa3b8;--accent:#5e8cff}
@media(prefers-color-scheme:light){:root{--bg:#f3f5fb;--surface:#fff;--text:#1a1d29;--muted:#5b6275}}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--text)}
.wrap{max-width:560px;margin:0 auto;padding:32px 22px}.row{display:flex;gap:8px}
input{flex:1;padding:10px 12px;border-radius:10px;border:1px solid rgba(128,128,128,.3);background:transparent;color:inherit}
button{border:0;border-radius:10px;padding:10px 16px;background:var(--accent);color:#fff;cursor:pointer}
ul{list-style:none;padding:0;margin:18px 0}li{padding:10px 12px;border-bottom:1px solid rgba(128,128,128,.18)}
.muted{color:var(--muted)}h1{letter-spacing:-.02em}
"""

# --------------------------------------------------------------------------- #
#  python-db — stdlib full-stack with a SQLite data layer + migrations
# --------------------------------------------------------------------------- #
_PYDB_SERVER = '''"""Zero-dependency full-stack server with a SQLite data layer.
Serves the frontend in ./public + a JSON API under /api backed by SQLite.
DB path and secret come from the environment (.env); stdlib only."""
import json, os, sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

ROOT = os.path.dirname(os.path.abspath(__file__))
PUBLIC = os.path.join(ROOT, "public")


def db_path():
    p = os.environ.get("DATABASE_URL", "app.db")
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def connect():
    c = sqlite3.connect(db_path())
    c.row_factory = sqlite3.Row
    return c


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json({"ok": True})
        if path == "/api/notes":
            with connect() as c:
                rows = c.execute("SELECT id, body, created_at FROM notes ORDER BY id DESC").fetchall()
            return self._json([dict(r) for r in rows])
        return self._static(path)

    def do_POST(self):
        if urlparse(self.path).path == "/api/notes":
            n = int(self.headers.get("Content-Length", 0))
            try:
                payload = json.loads(self.rfile.read(n) or b"{}")
            except Exception:
                return self._json({"error": "bad json"}, 400)
            body = str(payload.get("body", "")).strip()
            if not body:
                return self._json({"error": "body required"}, 400)
            with connect() as c:
                cur = c.execute("INSERT INTO notes (body) VALUES (?)", (body,))
                row = c.execute("SELECT id, body, created_at FROM notes WHERE id=?",
                                (cur.lastrowid,)).fetchone()
            return self._json(dict(row), 201)
        return self._json({"error": "not found"}, 404)

    def _static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        target = os.path.normpath(os.path.join(PUBLIC, rel))
        if not target.startswith(PUBLIC) or not os.path.isfile(target):
            return self._json({"error": "not found"}, 404)
        ctype = {".html": "text/html", ".css": "text/css", ".js": "text/javascript",
                 ".json": "application/json"}.get(os.path.splitext(target)[1], "text/plain")
        data = open(target, "rb").read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


if __name__ == "__main__":
    # Apply migrations on startup so a fresh deploy (e.g. a container) has its
    # schema before serving a request. Idempotent — a no-op once up to date.
    try:
        import migrate
        migrate.main()
    except Exception as exc:
        print(f"[warn] migrations did not run: {exc}")
    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f"db={db_path()}  serving on http://{host}:{port}")
    ThreadingHTTPServer((host, port), H).serve_forever()
'''

_PYDB_MIGRATE = '''"""Self-contained SQLite migration runner — applies migrations/*.sql in order,
idempotently, tracking applied files in a schema_migrations table. Stdlib only.
Usage: python migrate.py   (DB path from $DATABASE_URL, default app.db)."""
import glob, os, sqlite3, sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def db_path():
    p = os.environ.get("DATABASE_URL", "app.db")
    return p if os.path.isabs(p) else os.path.join(ROOT, p)


def main():
    conn = sqlite3.connect(db_path())
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                 "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))")
    done = {r[0] for r in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for path in sorted(glob.glob(os.path.join(ROOT, "migrations", "*.sql"))):
        name = os.path.basename(path)
        if name in done:
            continue
        try:
            conn.executescript(open(path, encoding="utf-8").read())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (?)", (name,))
            conn.commit()
        except Exception as exc:
            conn.rollback()
            print(f"migration {name} failed: {exc}", file=sys.stderr)
            return 1
        applied.append(name)
    print(f"applied {len(applied)} migration(s): {', '.join(applied)}" if applied
          else "database is up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

_PYDB_MIGRATION_001 = """-- 001: notes table
CREATE TABLE IF NOT EXISTS notes (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

_PYDB_SEED = """-- Optional sample data. Run with: python migrate.py && python -c \
-- "import sqlite3,os;sqlite3.connect('app.db').executescript(open('seed.sql').read())"
INSERT INTO notes (body) VALUES ('Welcome to your notes app');
"""

_PYDB_ENV_EXAMPLE = """# App configuration. Copy to .env (the harness generates one automatically,
# filling SECRET_KEY with a random value).
DATABASE_URL=app.db
SECRET_KEY=changeme
"""

_PYDB_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Notes</title><link rel="stylesheet" href="styles.css" /></head>
<body><main class="wrap">
  <h1>Notes</h1>
  <form id="f" class="row"><input id="t" placeholder="Write a note…" autocomplete="off" /><button>Add</button></form>
  <ul id="list"></ul>
</main><script src="app.js"></script></body></html>
"""

_PYDB_FRONT_JS = """'use strict';
const list = document.getElementById('list');
async function load(){
  const notes = await (await fetch('/api/notes')).json();
  list.innerHTML = notes.map(n => `<li><span>${n.body}</span><time>${n.created_at}</time></li>`).join('')
    || '<li class="muted">No notes yet.</li>';
  console.log('loaded', notes.length, 'notes');
}
document.getElementById('f').addEventListener('submit', async (e) => {
  e.preventDefault();
  const t = document.getElementById('t');
  if (!t.value.trim()) return;
  await fetch('/api/notes', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({body:t.value})});
  t.value=''; load();
});
load();
"""

_PYDB_CSS = """:root{--bg:#0b0f17;--surface:#151b27;--text:#eef1f8;--muted:#9aa3b8;--accent:#5e8cff}
@media(prefers-color-scheme:light){:root{--bg:#f3f5fb;--surface:#fff;--text:#1a1d29;--muted:#5b6275}}
*{box-sizing:border-box}body{margin:0;font-family:system-ui,sans-serif;background:var(--bg);color:var(--text)}
.wrap{max-width:560px;margin:0 auto;padding:32px 22px}.row{display:flex;gap:8px}
input{flex:1;padding:10px 12px;border-radius:10px;border:1px solid rgba(128,128,128,.3);background:transparent;color:inherit}
button{border:0;border-radius:10px;padding:10px 16px;background:var(--accent);color:#fff;cursor:pointer}
ul{list-style:none;padding:0;margin:18px 0}
li{display:flex;justify-content:space-between;gap:12px;align-items:baseline;padding:11px 12px;border-bottom:1px solid rgba(128,128,128,.18)}
li time{color:var(--muted);font-size:.78rem;white-space:nowrap}.muted{color:var(--muted)}h1{letter-spacing:-.02em}
"""

# --------------------------------------------------------------------------- #
#  vite-react — Vite + React + TypeScript (needs npm)
# --------------------------------------------------------------------------- #
_VITE_PKG = """{
  "name": "app",
  "private": true,
  "type": "module",
  "scripts": { "dev": "vite", "build": "tsc -b && vite build", "preview": "vite preview" },
  "dependencies": { "react": "^18.3.1", "react-dom": "^18.3.1" },
  "devDependencies": { "@vitejs/plugin-react": "^4.3.1", "typescript": "^5.5.4", "vite": "^5.4.0",
    "@types/react": "^18.3.3", "@types/react-dom": "^18.3.0" }
}
"""
_VITE_INDEX = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>App</title></head>
<body><div id="root"></div><script type="module" src="/src/main.tsx"></script></body></html>
"""
_VITE_MAIN = """import React from 'react';
import { createRoot } from 'react-dom/client';
import App from './App';
import './index.css';
createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
"""
_VITE_APP = """import { useState } from 'react';
export default function App() {
  const [n, setN] = useState(0);
  return (
    <main style={{maxWidth: 640, margin: '0 auto', padding: 32, fontFamily: 'system-ui'}}>
      <h1>Vite + React + TS</h1>
      <p>Edit <code>src/App.tsx</code> to build your app.</p>
      <button onClick={() => setN(n + 1)}>count is {n}</button>
    </main>
  );
}
"""
_VITE_CSS = ":root{color-scheme:light dark}body{margin:0}\n"
_VITE_TSCONFIG = """{
  "compilerOptions": { "target": "ES2020", "useDefineForClassFields": true, "lib": ["ES2020","DOM","DOM.Iterable"],
    "module": "ESNext", "skipLibCheck": true, "moduleResolution": "bundler", "jsx": "react-jsx",
    "strict": true, "noEmit": true },
  "include": ["src"]
}
"""
_VITE_CONFIG = """import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
export default defineConfig({ plugins: [react()], server: { host: true } });
"""

# --------------------------------------------------------------------------- #
#  fastapi — FastAPI + SQLite (needs pip)
# --------------------------------------------------------------------------- #
_FASTAPI_APP = '''from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os, sqlite3

DB = os.path.join(os.path.dirname(__file__), "app.db")
app = FastAPI()


def db():
    c = sqlite3.connect(DB)
    c.execute("CREATE TABLE IF NOT EXISTS items (id INTEGER PRIMARY KEY, text TEXT)")
    return c


class Item(BaseModel):
    text: str


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/items")
def list_items():
    with db() as c:
        return [{"id": i, "text": t} for i, t in c.execute("SELECT id, text FROM items")]


@app.post("/api/items")
def add_item(item: Item):
    with db() as c:
        cur = c.execute("INSERT INTO items (text) VALUES (?)", (item.text,))
        return {"id": cur.lastrowid, "text": item.text}


app.mount("/", StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True), name="static")
'''
_FASTAPI_REQ = "fastapi\nuvicorn[standard]\n"
_FASTAPI_INDEX = """<!doctype html><html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" /><title>FastAPI app</title></head>
<body style="font-family:system-ui;max-width:560px;margin:0 auto;padding:32px">
<h1>Items</h1><form id="f"><input id="t" placeholder="New item" /><button>Add</button></form><ul id="list"></ul>
<script>
async function load(){const r=await fetch('/api/items');document.getElementById('list').innerHTML=(await r.json()).map(i=>`<li>#${i.id} ${i.text}</li>`).join('');}
document.getElementById('f').onsubmit=async e=>{e.preventDefault();const t=document.getElementById('t');await fetch('/api/items',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t.value})});t.value='';load();};
load();
</script></body></html>
"""


SCAFFOLDS: dict[str, Scaffold] = {
    "static": Scaffold(
        name="static", label="Vanilla SPA (no build)", kind="frontend",
        run="python -m http.server $PORT",
        note=("You are starting from the **vanilla SPA** scaffold (index.html, styles.css, app.js — "
              "no build step). app.js has a tiny hash router; add views/links and styles. Keep it "
              "dependency-free. The dev server is a static file server."),
        files={"index.html": _STATIC_INDEX, "styles.css": _STATIC_CSS, "app.js": _STATIC_JS},
        checks=[
            {"name": "app builds", "command": ("python3 -c \"import pathlib,sys; "
             "p=pathlib.Path('index.html'); sys.exit(0 if p.is_file() and p.stat().st_size>80 else 1)\"")},
            {"name": "app responds", "command": SMOKE_CMD, "needs_server": True},
        ],
    ),
    "python-api": Scaffold(
        name="python-api", label="Stdlib full-stack API (no deps)", kind="fullstack",
        run="python server.py",
        note=("You are starting from the **stdlib full-stack** scaffold: server.py serves the frontend "
              "in ./public AND a JSON API under /api (/api/health, /api/items GET+POST), with file-backed "
              "storage in data.json. NO dependencies — stdlib only. Add endpoints in server.py and UI in "
              "./public. server.py reads PORT from the environment."),
        files={"server.py": _PYAPI_SERVER, "public/index.html": _PYAPI_INDEX,
               "public/app.js": _PYAPI_FRONT_JS, "public/styles.css": _PYAPI_CSS},
        checks=[
            {"name": "compiles", "command": "python -m py_compile server.py"},
            {"name": "api health", "command": _API_HEALTH, "needs_server": True},
            {"name": "app responds", "command": SMOKE_CMD, "needs_server": True},
        ],
    ),
    "python-db": Scaffold(
        name="python-db", label="Stdlib full-stack + SQLite (migrations)", kind="fullstack",
        run="python server.py",
        note=("You are starting from the **stdlib full-stack + SQLite** scaffold: server.py serves the "
              "frontend in ./public AND a JSON API under /api (/api/health, /api/notes GET+POST) backed by "
              "a **SQLite database**. The schema lives in migrations/*.sql — add a new numbered migration "
              "(e.g. migrations/002_*.sql) to change the schema; run `python migrate.py` to apply (it's "
              "idempotent and tracked in schema_migrations). Config (DATABASE_URL, SECRET_KEY) comes from "
              ".env — read it via os.environ, never hard-code. NO dependencies — stdlib sqlite3 only. Add "
              "tables via migrations and endpoints in server.py."),
        files={"server.py": _PYDB_SERVER, "migrate.py": _PYDB_MIGRATE,
               "migrations/001_init.sql": _PYDB_MIGRATION_001, "seed.sql": _PYDB_SEED,
               ".env.example": _PYDB_ENV_EXAMPLE, "public/index.html": _PYDB_INDEX,
               "public/app.js": _PYDB_FRONT_JS, "public/styles.css": _PYDB_CSS},
        checks=[
            {"name": "compiles", "command": "python -m py_compile server.py migrate.py"},
            {"name": "migrations apply", "command": "python migrate.py"},
            {"name": "api health", "command": _API_HEALTH, "needs_server": True},
            {"name": "notes api", "command": (
                "python3 -c \"import urllib.request,json,sys; "
                "req=urllib.request.Request('$APP_URL/api/notes', "
                "data=json.dumps({'body':'smoke test'}).encode(), "
                "headers={'Content-Type':'application/json'}); "
                "r=urllib.request.urlopen(req,timeout=6); "
                "sys.exit(0 if r.status==201 else 1)\""), "needs_server": True},
            {"name": "app responds", "command": SMOKE_CMD, "needs_server": True},
        ],
        needs="",
    ),
    "vite-react": Scaffold(
        name="vite-react", label="Vite + React + TypeScript", kind="react", needs="node",
        run="npm run dev -- --port $PORT --host 127.0.0.1",
        note=("You are starting from the **Vite + React + TypeScript** scaffold. Edit src/App.tsx and add "
              "components under src/. Run `npm install` first; dev server is `npm run dev`. Keep it typed "
              "(strict)."),
        files={"package.json": _VITE_PKG, "index.html": _VITE_INDEX, "src/main.tsx": _VITE_MAIN,
               "src/App.tsx": _VITE_APP, "src/index.css": _VITE_CSS, "tsconfig.json": _VITE_TSCONFIG,
               "vite.config.ts": _VITE_CONFIG},
        checks=[
            {"name": "install", "command": "npm ci || npm install"},
            {"name": "typecheck", "command": "npx --no-install tsc --noEmit"},
            {"name": "build", "command": "npm run build"},
            {"name": "app responds", "command": SMOKE_CMD, "needs_server": True},
        ],
    ),
    "fastapi": Scaffold(
        name="fastapi", label="FastAPI + SQLite", kind="api", needs="python",
        run="uvicorn app:app --host 127.0.0.1 --port $PORT",
        note=("You are starting from the **FastAPI + SQLite** scaffold (app.py with /api/health and a CRUD "
              "for items, serving ./static). Run `pip install -r requirements.txt`; dev server is uvicorn. "
              "Add routes in app.py and UI in ./static."),
        files={"app.py": _FASTAPI_APP, "requirements.txt": _FASTAPI_REQ, "static/index.html": _FASTAPI_INDEX},
        checks=[
            {"name": "install", "command": "pip install -r requirements.txt"},
            {"name": "compiles", "command": "python -m py_compile app.py"},
            {"name": "api health", "command": _API_HEALTH, "needs_server": True},
            {"name": "app responds", "command": SMOKE_CMD, "needs_server": True},
        ],
    ),
}


def list_scaffolds() -> list[dict]:
    return [{"name": s.name, "label": s.label, "kind": s.kind, "needs": s.needs, "run": s.run}
            for s in SCAFFOLDS.values()]


def get(name: str) -> Scaffold | None:
    return SCAFFOLDS.get((name or "").strip())


def apply(name: str, workspace: str) -> Scaffold | None:
    """Write a scaffold's files into the workspace (never overwrites existing files)."""
    sc = get(name)
    if not sc:
        return None
    for rel, content in sc.files.items():
        dest = os.path.join(workspace, rel)
        if os.path.exists(dest):
            continue
        os.makedirs(os.path.dirname(dest) or workspace, exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(content)
    return sc
