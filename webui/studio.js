"use strict";
/* Studio — a Replit/Lovable-style surface: chat to build/iterate an app and
   watch it live in the preview pane. Reuses $ / $$ / logClass from app.js
   (classic scripts share the global scope; this file loads after app.js). */
(function () {
  let project = null;       // active workspace name (null = new-project mode)
  let currentFile = null;   // path of the file shown in the viewer
  let jobId = null;         // id of the in-flight build/iterate job
  let refImage = null;      // data-URL of an uploaded reference UI screenshot
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

  function onRefFile(e) {
    const f = e.target.files && e.target.files[0];
    if (!f) return;
    const r = new FileReader();
    r.onload = () => {
      refImage = r.result;
      const thumb = $("#st-ref-thumb");
      thumb.src = refImage; thumb.hidden = false;
      $("#st-ref-text").textContent = f.name;
      $("#st-ref-clear").hidden = false;
    };
    r.readAsDataURL(f);
  }
  function clearRef(e) {
    if (e) { e.preventDefault(); e.stopPropagation(); }
    refImage = null;
    $("#st-ref-file").value = "";
    $("#st-ref-thumb").hidden = true; $("#st-ref-thumb").removeAttribute("src");
    $("#st-ref-text").textContent = "＋ Reference UI image (optional)";
    $("#st-ref-clear").hidden = true;
  }
  function refParams() {
    if (!refImage) return {};
    return {
      image: refImage,
      vision_model: $("#st-vision").value.trim() || null,
      vision_base_url: (engineParams().base_url) || null,
      coder_multimodal: $("#st-multimodal").checked,
      visual_check: $("#st-visualcheck").checked,
    };
  }

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
    clearConsole();
    $("#st-preview").src = `/artifact/${encodeURIComponent(project)}/index.html?__dev=1&t=${Date.now()}`;
    $("#st-open").href = `/artifact/${encodeURIComponent(project)}/index.html`;
    $("#st-preview-empty").hidden = true;
  }
  function setDevice(d) { device = d; $("#st-stage").dataset.device = d; }

  // --- devtools console (captures the previewed app's logs/errors/fetches) ---
  let consoleCount = 0;
  function clearConsole() {
    consoleCount = 0;
    const body = $("#st-console-body"); if (body) body.innerHTML = "";
    const c = $("#st-console-count"); if (c) { c.textContent = "0"; c.dataset.has = ""; }
  }
  function onPreviewMessage(e) {
    const d = e && e.data;
    if (!d || !d.__harnessLog) return;
    consoleCount++;
    const c = $("#st-console-count");
    if (c) { c.textContent = String(consoleCount); c.dataset.has = d.level === "error" ? "err" : "1"; }
    const body = $("#st-console-body");
    if (!body) return;
    const line = document.createElement("div");
    line.className = "cline lvl-" + (d.level || "log");
    line.innerHTML = `<span class="ctag">${(d.level || "log").toUpperCase()}</span>`;
    line.appendChild(document.createTextNode(" " + (d.text || "")));
    body.appendChild(line);
    while (body.childElementCount > 400) body.removeChild(body.firstChild);
    body.scrollTop = body.scrollHeight;
  }
  function toggleConsole() {
    const body = $("#st-console-body");
    const open = body.hidden;
    body.hidden = !open;
    $("#st-console-toggle").classList.toggle("active", open);
  }

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
    versions = []; $("#st-diff").textContent = ""; setMode("files");
  }
  function setProject(name) {
    project = name; currentFile = null; versions = [];
    selectLabel(name);
    intoIterateMode();
    setMode("files");
    loadFiles(); reloadPreview(); runTests();
  }

  async function send() {
    const prompt = $("#st-prompt").value.trim();
    if (!prompt) return;
    const eng = engineParams();
    runningUI(true); $("#st-log").textContent = ""; setStatus("running", "working");
    const ref = refParams();
    let url, body;
    if (project) {
      url = "/api/iterate"; body = { workspace: project, instruction: prompt, ...eng, ...ref };
    } else {
      url = "/api/builds";
      body = { prompt, name: ($("#st-name").value || "app").trim(), kind: $("#st-kind").value, ...eng, ...ref };
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
      clearRef();
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
      if (!$("#st-diff-pane").hidden) loadVersions();
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

  // --- version history + diff ---------------------------------------------
  let versions = [];
  function setMode(mode) {
    const diff = mode === "diff";
    $("#st-files-pane").hidden = diff;
    $("#st-diff-pane").hidden = !diff;
    $("#st-mode-files").classList.toggle("active", !diff);
    $("#st-mode-diff").classList.toggle("active", diff);
    if (diff) loadVersions();
  }
  function optLabel(v) {
    return `v${v.id} · ${v.label || "snapshot"}${v.instruction ? " — " + v.instruction.slice(0, 36) : ""}`;
  }
  async function loadVersions(selectLatest = true) {
    if (!project) return;
    try {
      const r = await (await fetch(`/api/versions?dir=${encodeURIComponent(project)}`)).json();
      versions = r.versions || [];
      const fromSel = $("#st-from"), toSel = $("#st-to");
      fromSel.innerHTML = ""; toSel.innerHTML = "";
      // "from" can also be the empty baseline (everything added)
      const base = document.createElement("option");
      base.value = "0"; base.textContent = "v0 · (empty start)";
      fromSel.appendChild(base);
      for (let i = versions.length - 1; i >= 0; i--) {
        const v = versions[i];
        const a = document.createElement("option"); a.value = v.id; a.textContent = optLabel(v);
        const b = a.cloneNode(true);
        fromSel.appendChild(a); toSel.appendChild(b);
      }
      if (versions.length) {
        const latest = versions[versions.length - 1].id;
        const prev = versions.length > 1 ? versions[versions.length - 2].id : 0;
        if (selectLatest) { toSel.value = String(latest); fromSel.value = String(prev); }
        showDiff();
      } else {
        $("#st-diff").textContent = "";
      }
    } catch {}
  }
  function renderDiff(files) {
    const box = $("#st-diff");
    if (!files || !files.length) { box.innerHTML = ""; box.textContent = "No changes in this version."; return; }
    box.innerHTML = files.map((f) => {
      const head = `<div class="diff-file">${f.status} · ${f.path} `
        + `<span class="diff-stat"><span class="add">+${f.added}</span> <span class="del">−${f.removed}</span></span></div>`;
      const lines = (f.diff || "").split("\n").map((ln) => {
        let cls = "";
        if (ln.startsWith("+") && !ln.startsWith("+++")) cls = "l-add";
        else if (ln.startsWith("-") && !ln.startsWith("---")) cls = "l-del";
        else if (ln.startsWith("@@")) cls = "l-hunk";
        else if (ln.startsWith("+++") || ln.startsWith("---")) cls = "l-meta";
        const safe = ln.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return `<span class="dl ${cls}">${safe || " "}</span>`;
      }).join("");
      return head + `<div class="diff-body">${lines}</div>`;
    }).join("");
  }
  async function showDiff() {
    if (!project) return;
    const from = $("#st-from").value || "0";
    const to = $("#st-to").value;
    if (!to) { $("#st-diff").textContent = ""; return; }
    $("#st-diff").textContent = "loading diff…";
    try {
      const r = await (await fetch(
        `/api/diff?dir=${encodeURIComponent(project)}&from=${from}&to=${to}`)).json();
      renderDiff(r.files);
    } catch { $("#st-diff").textContent = "could not load diff"; }
  }
  async function restoreVersion() {
    const vid = Number($("#st-to").value);
    if (!vid) return;
    if (!window.confirm(`Restore the app to v${vid}? The current state is saved as a new version first.`)) return;
    $("#st-restore").disabled = true;
    try {
      const r = await (await fetch("/api/restore", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: project, version: vid }),
      })).json();
      if (r.error) { appendLog("restore failed: " + r.error); }
      else { appendLog(`[version] restored to v${vid}`); currentFile = null; loadFiles(); reloadPreview(); runTests(); loadVersions(); }
    } catch { appendLog("restore failed"); }
    $("#st-restore").disabled = false;
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
    $("#st-mode-files").addEventListener("click", () => setMode("files"));
    $("#st-mode-diff").addEventListener("click", () => setMode("diff"));
    $("#st-from").addEventListener("change", showDiff);
    $("#st-to").addEventListener("change", showDiff);
    $("#st-restore").addEventListener("click", restoreVersion);
    $("#st-console-toggle").addEventListener("click", toggleConsole);
    $("#st-console-clear").addEventListener("click", clearConsole);
    $("#st-ref-file").addEventListener("change", onRefFile);
    $("#st-ref-clear").addEventListener("click", clearRef);
    window.addEventListener("message", onPreviewMessage);
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
    if (qs.get("view") === "diff") setMode("diff");
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
