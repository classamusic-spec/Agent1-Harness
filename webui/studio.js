"use strict";
/* Studio — a Replit/Lovable-style surface: chat to build/iterate an app and
   watch it live in the preview pane. Reuses $ / $$ / logClass from app.js
   (classic scripts share the global scope; this file loads after app.js). */
(function () {
  let project = null;       // active workspace name (null = new-project mode)
  let currentFile = null;   // path of the file shown in the viewer
  let jobId = null;         // id of the in-flight build/iterate job
  let device = "desktop";
  let pollTimer = null;
  let wired = false;
  const NEW = "__new__";    // sentinel option value in the project switcher

  // Local-LLM presets — editable. Model ids depend on what your server exposes
  // (Ollama/LM Studio/vLLM). GLM / MiniMax / Qwen are common OpenAI-compatible.
  const PRESETS = {
    "local:glm":     { model: "glm-4",         base_url: "http://localhost:11434/v1" },
    "local:minimax": { model: "minimax-m1",    base_url: "http://localhost:11434/v1" },
    "local:qwen":    { model: "qwen2.5-coder", base_url: "http://localhost:11434/v1" },
    "local:custom":  { model: "",              base_url: "http://localhost:11434/v1" },
  };

  function engineParams() {
    const sel = $("#st-engine").value;
    if (sel === "claude-cli") return { engine: "claude-cli", model: "", base_url: null };
    const p = PRESETS[sel] || PRESETS["local:custom"];
    return {
      engine: "local",
      model: $("#st-model").value || p.model,
      base_url: $("#st-baseurl").value || p.base_url,
    };
  }

  function onEngineChange() {
    const sel = $("#st-engine").value;
    const local = sel.startsWith("local");
    $("#st-local-fields").hidden = !local;
    if (local) {
      const p = PRESETS[sel] || PRESETS["local:custom"];
      $("#st-model").placeholder = p.model || "model id";
      $("#st-baseurl").placeholder = p.base_url;
    }
  }

  function setStatus(state, text) {
    const e = $("#st-status"); e.dataset.state = state; e.textContent = text || state;
  }
  function appendLog(line) {
    const l = $("#st-log"), d = document.createElement("div");
    d.className = "line " + (typeof logClass === "function" ? logClass(line) : "");
    d.textContent = line; l.appendChild(d); l.scrollTop = l.scrollHeight;
  }

  function reloadPreview() {
    if (!project) return;
    $("#st-preview").src = `/artifact/${encodeURIComponent(project)}/index.html?t=${Date.now()}`;
    $("#st-open").href = `/artifact/${encodeURIComponent(project)}/index.html`;
    $("#st-preview-empty").hidden = true;
  }
  function setDevice(d) { device = d; $("#st-stage").dataset.device = d; }

  async function loadFiles() {
    if (!project) return;
    try {
      const tree = await (await fetch(`/api/workspace?dir=${encodeURIComponent(project)}`)).json();
      const ul = $("#st-tree"); ul.innerHTML = "";
      const files = (tree.files || []).filter(
        (f) => !f.path.startsWith(".studio") && !f.path.startsWith(".appbuilder"));
      if (!files.length) { ul.innerHTML = '<li class="empty-row">(waiting for files…)</li>'; return; }
      let selected = null;
      for (const f of files) {
        const li = document.createElement("li"); li.className = "row";
        li.innerHTML = `<span>${f.path}</span><span class="muted">${f.size}</span>`;
        li.addEventListener("click", () => openFile(f.path, li));
        ul.appendChild(li);
        if (f.path === currentFile) { li.classList.add("sel"); selected = li; }
      }
      // Auto-open the entry point on first load so the viewer isn't empty.
      if (!currentFile || !selected) {
        const entry = files.find((f) => f.path === "index.html") || files[0];
        const row = [...ul.children].find((li) => li.querySelector("span")?.textContent === entry.path);
        openFile(entry.path, row);
      }
    } catch {}
  }
  async function openFile(path, li) {
    currentFile = path;
    $$("#st-tree .row").forEach((r) => r.classList.remove("sel"));
    if (li) li.classList.add("sel");
    try {
      const j = await (await fetch(
        `/api/workspace/file?dir=${encodeURIComponent(project)}&file=${encodeURIComponent(path)}`)).json();
      $("#st-file").textContent = j.content !== undefined ? j.content : (j.error || "");
    } catch {}
  }

  function runningUI(on) {
    $("#st-send").disabled = on;
    $("#st-stop").hidden = !on;
    $("#st-project").disabled = on;
  }

  // --- project switcher ----------------------------------------------------
  async function populateProjects() {
    const sel = $("#st-project");
    try {
      const { artifacts } = await (await fetch("/api/artifacts")).json();
      const names = (artifacts || []).map((a) => a.name);
      sel.innerHTML = "";
      for (const n of names) {
        const o = document.createElement("option"); o.value = n; o.textContent = n;
        sel.appendChild(o);
      }
      const newo = document.createElement("option");
      newo.value = NEW; newo.textContent = "＋ New project…";
      sel.appendChild(newo);
      sel.value = project || NEW;
      return names;
    } catch { return []; }
  }
  function selectLabel(name) {
    const sel = $("#st-project");
    if (name && ![...sel.options].some((o) => o.value === name)) {
      const o = document.createElement("option"); o.value = name; o.textContent = name;
      sel.insertBefore(o, sel.firstChild);
    }
    sel.value = name || NEW;
  }

  function intoIterateMode() {
    $("#st-new").hidden = true;
    $("#st-prompt-label").textContent = "Describe a change";
    $("#st-prompt").placeholder = "e.g. Add a dark-mode toggle, and a confetti burst when the timer ends.";
    $("#st-send").textContent = "Send change";
    $("#st-hint").textContent = "Your engine edits the app, then re-verifies. The preview reloads on the right.";
  }
  function resetToNew() {
    project = null; currentFile = null;
    selectLabel(null);
    $("#st-new").hidden = false;
    $("#st-prompt-label").textContent = "Describe the app to build";
    $("#st-send").textContent = "Generate app";
    $("#st-preview").src = "about:blank"; $("#st-preview-empty").hidden = false;
    $("#st-tree").innerHTML = ""; $("#st-file").textContent = "";
    $("#st-tests").hidden = true; $("#st-log").textContent = ""; setStatus("idle", "idle");
  }
  function setProject(name) {
    project = name; currentFile = null;
    selectLabel(name);
    intoIterateMode();
    loadFiles(); reloadPreview(); runTests();
  }

  async function send() {
    const prompt = $("#st-prompt").value.trim();
    if (!prompt) return;
    const eng = engineParams();
    runningUI(true); $("#st-log").textContent = ""; setStatus("running", "working");
    let url, body;
    if (project) {
      url = "/api/iterate"; body = { workspace: project, instruction: prompt, ...eng };
    } else {
      url = "/api/builds";
      body = { prompt, name: ($("#st-name").value || "app").trim(), kind: $("#st-kind").value, ...eng };
    }
    try {
      const j = await (await fetch(url, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      })).json();
      if (j.error) { setStatus("error", j.error); appendLog("error: " + j.error); runningUI(false); return; }
      jobId = j.id;
      project = project || j.workspace.split("/").pop();
      selectLabel(project);
      $("#st-prompt").value = "";
      stream(j.id); startPoll();
    } catch { setStatus("error", "request failed"); runningUI(false); }
  }

  async function stop() {
    if (!jobId) return;
    $("#st-stop").disabled = true; appendLog("[control] stop requested…");
    try {
      await fetch(`/api/jobs/${jobId}/control`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action: "cancel" }),
      });
    } catch {}
    $("#st-stop").disabled = false;
  }

  function stream(id) {
    const es = new EventSource(`/api/jobs/${id}/events`);
    es.onmessage = (ev) => appendLog(ev.data);
    es.addEventListener("done", (ev) => {
      setStatus(ev.data, ev.data); runningUI(false); jobId = null; es.close();
      stopPoll(); intoIterateMode(); populateProjects(); loadFiles(); reloadPreview(); runTests();
    });
    es.onerror = () => { runningUI(false); es.close(); stopPoll(); };
  }

  function startPoll() { stopPoll(); pollTimer = setInterval(pollOnce, 1500); pollOnce(); }
  function stopPoll() { if (pollTimer) clearInterval(pollTimer); pollTimer = null; }
  async function pollOnce() {
    try {
      const c = await (await fetch("/api/current")).json();
      $("#st-stat").textContent = c.tokens ? `${c.tokens} tokens · ${c.elapsed || 0}s` : "";
      if (c.busy && project) loadFiles();
      if (!c.busy) stopPoll();
    } catch {}
  }

  async function runTests() {
    if (!project) return;
    const strip = $("#st-tests"); strip.hidden = false;
    strip.innerHTML = '<span class="muted">running tests…</span>';
    try {
      const r = await (await fetch("/api/test", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: project }),
      })).json();
      if (r.error) { strip.innerHTML = `<span class="chip bad">${r.error}</span>`; return; }
      if (!r.results || !r.results.length) { strip.innerHTML = '<span class="chip ok">no checks defined</span>'; return; }
      strip.innerHTML = r.results.map((t) => {
        const tip = (t.output || "").replace(/"/g, "&quot;");
        return `<span class="chip ${t.ok ? "ok" : "bad"}" title="${tip}">${t.ok ? "✓" : "✗"} ${t.name}</span>`;
      }).join("");
    } catch { strip.innerHTML = '<span class="chip bad">test run failed</span>'; }
  }

  function wire() {
    $("#st-engine").addEventListener("change", onEngineChange);
    $("#st-send").addEventListener("click", send);
    $("#st-stop").addEventListener("click", stop);
    $("#st-project").addEventListener("change", (e) => {
      const v = e.target.value;
      if (v === NEW) resetToNew(); else setProject(v);
    });
    $("#st-refresh").addEventListener("click", () => { loadFiles(); reloadPreview(); });
    $("#st-reload").addEventListener("click", reloadPreview);
    $("#st-test").addEventListener("click", runTests);
    $$('input[name="st-dev"]').forEach((r) => r.addEventListener("change", () => setDevice(r.value)));
    $("#st-prompt").addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send();
    });
  }

  async function show() {
    if (!wired) { wire(); wired = true; }
    onEngineChange();
    const names = await populateProjects();
    // Deep-link support: /?tab=studio&proj=<name>&dev=mobile
    const qs = new URLSearchParams(location.search);
    const dev = qs.get("dev");
    if (dev === "mobile" || dev === "desktop") {
      const radio = document.querySelector(`input[name="st-dev"][value="${dev}"]`);
      if (radio) radio.checked = true;
      setDevice(dev);
    }
    const proj = qs.get("proj");
    if (proj && project !== proj) setProject(proj);
    else if (!project && names.length) setProject(names[0]);
    reconnectIfBusy();
  }

  // If a build/iterate is already running (e.g. the page was reloaded mid-run),
  // re-attach: show Stop, stream the log, and resume polling.
  async function reconnectIfBusy() {
    if (jobId) return;
    try {
      const c = await (await fetch("/api/current")).json();
      if (c.busy && c.job) {
        jobId = c.job;
        if (c.name) { project = c.name; selectLabel(project); intoIterateMode(); loadFiles(); reloadPreview(); }
        runningUI(true); setStatus("running", "working");
        stream(jobId); startPoll();
      }
    } catch {}
  }

  window.Studio = { show };

  // app.js runs applyHash() before this script executes, so init here if the
  // Studio panel is already the active one on load.
  const panel = document.getElementById("panel-studio");
  if (panel && !panel.hidden) show();
})();
