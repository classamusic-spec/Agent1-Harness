"""Demonstrate the full build loop end-to-end and produce a real, runnable app.

There is no LLM in this demo: `DemoEngine` is a deterministic stand-in for the
model so the loop can be exercised without an API key or a local server. The
*harness* is real — it runs the actual verification gate against the files the
engine writes, and emits a verified, runnable app.

To build the same spec with a real model instead:

    appbuilder specs/notes-app.yaml -w workspaces/notes                 # Claude
    appbuilder specs/notes-app.yaml -w workspaces/notes \\
        --engine local --base-url http://localhost:11434/v1 --model qwen2.5-coder

Usage:
    python scripts/demo_build.py --workspace workspaces/notes
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from harness.agent import build  # noqa: E402
from harness.config import EngineConfig, HarnessConfig  # noqa: E402
from harness.engines.base import Engine  # noqa: E402
from harness.spec import load_spec  # noqa: E402

INDEX_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Quick Notes</title>
  <link rel="stylesheet" href="styles.css" />
</head>
<body>
  <header class="bar">
    <h1>Quick Notes</h1>
    <span class="count" id="count" aria-live="polite"></span>
  </header>

  <main class="wrap">
    <form id="form" class="composer" autocomplete="off">
      <input id="text" type="text" placeholder="Add a note and press Enter" aria-label="New note" />
      <button type="submit" class="add">Add</button>
    </form>

    <ul id="list" class="list" aria-label="Notes"></ul>
    <p id="empty" class="empty" hidden>Nothing yet. Add your first note above.</p>
  </main>

  <footer class="foot">Stored locally in your browser.</footer>
  <script src="app.js"></script>
</body>
</html>
"""

STYLES_CSS = """:root {
  --bg: #f5f5f7;
  --surface: #ffffff;
  --field: #f2f2f5;
  --text: #1d1d1f;
  --muted: #6e6e73;
  --hairline: rgba(0,0,0,0.10);
  --accent: #0071e3;
  --done: #1a8f5b;
  --radius: 16px;
  --sp: 8px;
  --font: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Roboto, sans-serif;
}
@media (prefers-color-scheme: dark) {
  :root { --bg:#000; --surface:#1c1c1e; --field:#2c2c2e; --text:#f5f5f7;
    --muted:#98989d; --hairline:rgba(255,255,255,0.14); --accent:#2997ff; --done:#41d6a3; }
}
* { box-sizing: border-box; }
body { margin: 0; min-height: 100vh; font-family: var(--font); color: var(--text);
  background: radial-gradient(1000px 480px at 80% -10%, #e7f0ff 0%, var(--bg) 60%);
  -webkit-font-smoothing: antialiased; }
@media (prefers-color-scheme: dark) {
  body { background: radial-gradient(1000px 480px at 80% -10%, #0f1b2e 0%, #000 60%); }
}
.bar { display: flex; align-items: baseline; justify-content: space-between;
  max-width: 640px; margin: 0 auto; padding: calc(var(--sp)*5) calc(var(--sp)*3) calc(var(--sp)*2); }
.bar h1 { margin: 0; font-size: 1.7rem; font-weight: 700; letter-spacing: -0.02em; }
.count { color: var(--muted); font-size: 0.85rem; }
.wrap { max-width: 640px; margin: 0 auto; padding: 0 calc(var(--sp)*3); }
.composer { display: flex; gap: var(--sp); margin-bottom: calc(var(--sp)*2.5); }
#text { flex: 1; padding: 13px 15px; font: inherit; color: var(--text);
  background: var(--surface); border: 1px solid var(--hairline); border-radius: 12px; }
#text:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 4px rgba(0,113,227,0.15); }
.add { padding: 0 18px; font: inherit; font-weight: 600; color: #fff; background: var(--accent);
  border: 0; border-radius: 12px; cursor: pointer; transition: filter 0.15s ease; }
.add:hover { filter: brightness(1.05); }
.list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp); }
.item { display: flex; align-items: center; gap: 12px; padding: 13px 15px;
  background: var(--surface); border: 1px solid var(--hairline); border-radius: 14px;
  box-shadow: 0 1px 2px rgba(0,0,0,0.04); }
.item input[type="checkbox"] { width: 20px; height: 20px; accent-color: var(--done); flex: none; }
.item .label { flex: 1; }
.item.done .label { color: var(--muted); text-decoration: line-through; }
.item .del { border: 0; background: none; color: var(--muted); font-size: 1.1rem; cursor: pointer;
  padding: 4px 8px; border-radius: 8px; }
.item .del:hover { color: #d9304a; background: var(--field); }
.empty { text-align: center; color: var(--muted); padding: calc(var(--sp)*6) 0; }
.foot { max-width: 640px; margin: calc(var(--sp)*5) auto; padding: 0 calc(var(--sp)*3);
  color: var(--muted); font-size: 0.8rem; text-align: center; }
@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
"""

APP_JS = """"use strict";
const KEY = "quick-notes";
const $ = (s) => document.querySelector(s);

function load() {
  try { return JSON.parse(localStorage.getItem(KEY)) || null; } catch { return null; }
}
function save(notes) { localStorage.setItem(KEY, JSON.stringify(notes)); }

let notes = load();
if (notes === null) {
  notes = [
    { id: 1, text: "Welcome to Quick Notes — click to check off", done: false },
    { id: 2, text: "Built by the Agent1-Harness loop", done: true },
    { id: 3, text: "Everything is stored locally", done: false },
  ];
  save(notes);
}

function render() {
  const list = $("#list");
  list.innerHTML = "";
  for (const n of notes) {
    const li = document.createElement("li");
    li.className = "item" + (n.done ? " done" : "");
    const cb = document.createElement("input");
    cb.type = "checkbox"; cb.checked = n.done;
    cb.setAttribute("aria-label", "Mark complete");
    cb.addEventListener("change", () => { n.done = cb.checked; save(notes); render(); });
    const span = document.createElement("span");
    span.className = "label"; span.textContent = n.text;
    const del = document.createElement("button");
    del.className = "del"; del.textContent = "×";
    del.setAttribute("aria-label", "Delete note");
    del.addEventListener("click", () => { notes = notes.filter((x) => x.id !== n.id); save(notes); render(); });
    li.append(cb, span, del);
    list.append(li);
  }
  const remaining = notes.filter((n) => !n.done).length;
  $("#count").textContent = notes.length ? remaining + " open · " + notes.length + " total" : "";
  $("#empty").hidden = notes.length > 0;
}

$("#form").addEventListener("submit", (e) => {
  e.preventDefault();
  const text = $("#text").value.trim();
  if (!text) return;
  notes.push({ id: Date.now(), text, done: false });
  save(notes); $("#text").value = ""; render(); $("#text").focus();
});

render();
"""

FILES = {"index.html": INDEX_HTML, "styles.css": STYLES_CSS, "app.js": APP_JS}


class DemoEngine(Engine):
    """Deterministic stand-in for an LLM: writes the app on the first turn."""

    def __init__(self, workspace: str):
        self.workspace = workspace
        self._wrote = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def send(self, prompt: str, *, echo: bool = True) -> str:
        if not self._wrote:
            for name, content in FILES.items():
                Path(self.workspace, name).write_text(content)
            self._wrote = True
            if echo:
                print(f"[demo-engine] wrote {', '.join(FILES)} to {self.workspace}")
            return "Implemented Quick Notes (index.html, styles.css, app.js)."
        return "No further changes needed."


async def _main(workspace: str) -> int:
    ws = os.path.abspath(workspace)
    spec = load_spec("specs/notes-app.yaml", cwd=ws)
    config = HarnessConfig(
        workspace=ws, engine=EngineConfig(provider="local", model="demo-engine"),
        checkpoint_path=os.path.join(ws, ".appbuilder_checkpoint.json"),
    )
    result = await build(spec, config, echo=True, builder_factory=lambda s, c: DemoEngine(c.workspace))

    print("\n" + "=" * 40)
    print(f"BUILD {'SUCCEEDED' if result.ok else 'FAILED'} ({result.stop_reason}) in {result.rounds} round(s)")
    print(f"Failing checks per round: {result.progress}")
    print(f"App: {os.path.join(ws, 'index.html')}")
    return 0 if result.ok else 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Demo: build a real app through the loop (no LLM).")
    p.add_argument("--workspace", "-w", default="workspaces/notes")
    args = p.parse_args(argv)
    return asyncio.run(_main(args.workspace))


if __name__ == "__main__":
    raise SystemExit(main())
