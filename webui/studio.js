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
    "local:ollama":   { model: "qwen2.5-coder", base_url: "http://localhost:11434/v1" },
    "local:lmstudio": { model: "",              base_url: "http://localhost:1234/v1" },
    "local:custom":   { model: "",              base_url: "http://localhost:11434/v1" },
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

  let scaffolds = [];
  async function loadScaffolds() {
    if (scaffolds.length) return;
    try {
      const r = await (await fetch("/api/scaffolds")).json();
      scaffolds = r.scaffolds || [];
      const sel = $("#st-scaffold");
      for (const s of scaffolds) {
        const o = document.createElement("option");
        o.value = s.name;
        o.textContent = s.label + (s.needs ? ` (needs ${s.needs})` : "");
        sel.appendChild(o);
      }
    } catch {}
  }
  function onScaffoldChange() {
    const s = scaffolds.find((x) => x.name === $("#st-scaffold").value);
    $("#st-scaffold-hint").textContent = s ? `run: ${s.run}` : "";
    if (s && $("#st-kind")) $("#st-kind").value = s.kind;
  }

  // On first load, point the engine at whatever's actually available on this
  // machine (the doctor's recommendation): Claude Code CLI, or a local server.
  let recommendedApplied = false;
  async function applyRecommendedEngine() {
    if (recommendedApplied) return;
    recommendedApplied = true;
    let rec, state;
    try {
      const r = await (await fetch("/api/doctor")).json();
      rec = r.recommend; state = r.state;
    } catch { return; }
    const hint = $("#st-engine-hint");
    if (!rec) {
      hint.textContent = "No engine detected — install Claude Code, or start Ollama/LM Studio (see README).";
      hint.hidden = false; return;
    }
    const sel = $("#st-engine");
    if (rec.engine === "claude-cli") {
      sel.value = "claude-cli";
    } else if (rec.engine === "local" && state && state.local) {
      const base = state.local.base_url || "";
      sel.value = base.includes("1234") ? "local:lmstudio"
                : base.includes("11434") ? "local:ollama" : "local:custom";
    } else {
      // anthropic-only: Studio drives builds through Claude Code; keep the default.
      sel.value = "claude-cli";
    }
    hint.textContent = "✓ Using " + (rec.why || rec.engine);
    hint.hidden = false;
    onEngineChange();
  }

  let detectedServers = [];
  function onEngineChange() {
    const sel = $("#st-engine").value;
    const local = sel.startsWith("local");
    $("#st-local-fields").hidden = !local;
    if (local) {
      const p = PRESETS[sel] || PRESETS["local:custom"];
      $("#st-model").placeholder = p.model || "model id";
      $("#st-baseurl").placeholder = p.base_url;
      if (!$("#st-baseurl").value) $("#st-baseurl").value = p.base_url;
      detectLocalModels();  // populate from models already on the machine
    } else {
      $("#st-local-hint").hidden = true;
    }
  }

  // Use models already downloaded on the user's hardware (Ollama / LM Studio / any
  // OpenAI-compatible server). Fills the datalist and auto-selects a sensible model.
  async function detectLocalModels() {
    const hint = $("#st-local-hint");
    try {
      const r = await (await fetch("/api/local-models")).json();
      detectedServers = r.servers || [];
    } catch { detectedServers = []; }
    const sel = $("#st-engine").value;
    const wantBase = (PRESETS[sel] || {}).base_url;
    // Prefer the server matching the chosen preset's port; else the first detected.
    let srv = detectedServers.find((s) => wantBase && s.base_url === wantBase)
              || detectedServers[0];
    const dl = $("#st-models"); dl.innerHTML = "";
    if (srv && srv.models.length) {
      for (const m of srv.models) { const o = document.createElement("option"); o.value = m; dl.appendChild(o); }
      if (!$("#st-baseurl").value || wantBase) $("#st-baseurl").value = srv.base_url;
      if (!$("#st-model").value) {
        const coder = srv.models.find((m) => /coder|qwen|deepseek|codestral/i.test(m)) || srv.models[0];
        $("#st-model").value = coder;
      }
      hint.textContent = `Found ${srv.models.length} model(s) on ${srv.name}: ${srv.models.slice(0, 6).join(", ")}`;
      hint.hidden = false;
    } else {
      hint.textContent = detectedServers.length
        ? "Server reachable but no models loaded — pull/load one (e.g. `ollama pull qwen2.5-coder`)."
        : "No local server detected. Start Ollama (:11434) or LM Studio (:1234), then ↻ detect.";
      hint.hidden = false;
    }
  }

  // --- voice input: dictate the spec / change into the composer (Web Speech API)
  let recog = null, listening = false, micBase = "";
  function setupVoice() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    const btn = $("#st-mic");
    if (!SR || !btn) return;            // unsupported browser → leave the button hidden
    btn.hidden = false;
    recog = new SR();
    recog.continuous = true; recog.interimResults = true; recog.lang = "en-US";
    recog.onresult = (e) => {
      let txt = "";
      for (let i = 0; i < e.results.length; i++) txt += e.results[i][0].transcript;
      const ta = $("#st-prompt");
      ta.value = (micBase ? micBase + " " : "") + txt.trim();
      ta.dispatchEvent(new Event("input"));
    };
    recog.onerror = () => stopVoice();
    recog.onend = () => { if (listening) { try { recog.start(); } catch { stopVoice(); } } };
    btn.addEventListener("click", toggleVoice);
  }
  function toggleVoice() { listening ? stopVoice() : startVoice(); }
  function startVoice() {
    if (!recog) return;
    micBase = $("#st-prompt").value.trim();
    try { recog.start(); listening = true; $("#st-mic").classList.add("active");
          $("#st-mic").title = "Stop dictation"; } catch {}
  }
  function stopVoice() {
    listening = false; $("#st-mic").classList.remove("active");
    $("#st-mic").title = "Dictate (voice input)";
    if (recog) { try { recog.stop(); } catch {} }
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
    if (serverRunning && runPort) {
      // Point at the app's own origin so absolute API calls (/api/...) work.
      const origin = `http://${location.hostname}:${runPort}`;
      $("#st-preview").src = `${origin}/?t=${Date.now()}`;
      $("#st-open").href = `${origin}/`;
    } else {
      $("#st-preview").src = `/artifact/${encodeURIComponent(project)}/index.html?__dev=1&t=${Date.now()}`;
      $("#st-open").href = `/artifact/${encodeURIComponent(project)}/index.html`;
    }
    $("#st-preview-empty").hidden = true;
    lastMtime = 0;  // re-baseline hot-reload after an explicit reload
  }

  // --- point & edit: click an element in the preview, then describe the change
  let pickMode = false;
  let pickTarget = null;
  function setPick(on) {
    pickMode = on;
    $("#st-pick").classList.toggle("active", on);
    const win = $("#st-preview").contentWindow;
    try { win && win.postMessage({ __harnessPick: 1, on }, "*"); } catch {}
  }
  function togglePick() {
    if (!project) return;
    setPick(!pickMode);
  }
  function onElementPicked(d) {
    pickTarget = { selector: d.selector || "", label: d.label || d.selector || "element",
                   text: d.text || "", html: d.html || "" };
    $("#st-target-label").textContent = pickTarget.label;
    $("#st-target").hidden = false;
    setPick(false);
    const ta = $("#st-prompt");
    ta.focus();
    if (!ta.value.trim()) ta.placeholder = `Describe the change to ${pickTarget.label}…`;
  }
  function clearTarget() {
    pickTarget = null;
    $("#st-target").hidden = true;
  }

  // --- hot reload: poll the workspace mtime; reload the preview when files change
  let lastMtime = 0;
  let hotTimer = null;
  async function pollMtime() {
    if (!project || document.hidden) return;
    try {
      const r = await (await fetch(`/api/workspace/mtime?dir=${encodeURIComponent(project)}`)).json();
      if (r.mtime && lastMtime && r.mtime > lastMtime) { lastMtime = r.mtime; reloadPreview(); }
      else if (r.mtime && !lastMtime) { lastMtime = r.mtime; }
    } catch {}
  }
  function startHotReload() {
    if (hotTimer) return;
    hotTimer = setInterval(pollMtime, 1500);
  }

  // --- live dev server (full-stack preview) --------------------------------
  let serverRunning = false;
  let runPort = null;
  let runtimeTimer = null;
  let runLogSince = 0;
  function setRunStatus(state, text) {
    const e = $("#st-run-status"); e.dataset.state = state; e.textContent = text;
  }
  async function refreshRuntime() {
    if (!project) return;
    try {
      const s = await (await fetch(`/api/runtime/status?dir=${encodeURIComponent(project)}`)).json();
      serverRunning = !!s.running;
      if (s.running) {
        runPort = s.info.port;
        $("#st-run-cmd").value = s.info.command;
        $("#st-run-btn").textContent = "Stop server";
        setRunStatus(s.info.status === "ready" ? "ready" : "run", s.info.status + " :" + s.info.port);
        reloadPreview();
        startRuntimePoll();
      } else {
        $("#st-run-btn").textContent = "Run server";
        setRunStatus("off", "static");
        if (s.detected && !$("#st-run-cmd").value) $("#st-run-cmd").placeholder = s.detected;
      }
    } catch {}
  }
  async function toggleServer() {
    if (serverRunning) {
      stopRuntimePoll();
      try { await fetch("/api/runtime/stop", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ workspace: project }) }); } catch {}
      serverRunning = false; $("#st-run-btn").textContent = "Run server"; setRunStatus("off", "static");
      reloadPreview();
      return;
    }
    const command = $("#st-run-cmd").value.trim();
    setRunStatus("run", "starting…"); $("#st-run-btn").disabled = true;
    try {
      const r = await (await fetch("/api/runtime/start", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: project, command }),
      })).json();
      if (r.error) { setRunStatus("off", r.error); $("#st-run-btn").disabled = false; return; }
      serverRunning = true; runLogSince = 0; runPort = r.info.port;
      $("#st-run-cmd").value = r.info.command; $("#st-run-btn").textContent = "Stop server";
      reloadPreview(); startRuntimePoll();
    } catch { setRunStatus("off", "start failed"); }
    $("#st-run-btn").disabled = false;
  }
  function startRuntimePoll() { stopRuntimePoll(); runtimeTimer = setInterval(pollRuntime, 1500); pollRuntime(); }
  function stopRuntimePoll() { if (runtimeTimer) clearInterval(runtimeTimer); runtimeTimer = null; }
  let runReady = false;
  async function pollRuntime() {
    if (!project) return;
    try {
      const r = await (await fetch(`/api/runtime/logs?dir=${encodeURIComponent(project)}&since=${runLogSince}`)).json();
      (r.lines || []).forEach((ln) => appendServerLog(ln));
      runLogSince = r.next || runLogSince;
      if (r.status) setRunStatus(r.status === "ready" ? "ready" : "run", r.status);
      if (r.status === "ready" && !runReady) { runReady = true; reloadPreview(); }
      if (r.status === "stopped" || r.status === "failed") { stopRuntimePoll(); serverRunning = false; }
    } catch {}
  }
  function appendServerLog(text) {
    consoleCount++;
    const c = $("#st-console-count"); if (c) { c.textContent = String(consoleCount); c.dataset.has = "1"; }
    const body = $("#st-console-body"); if (!body) return;
    const d = document.createElement("div");
    d.className = "cline lvl-net";
    d.innerHTML = '<span class="ctag">SRV</span>';
    d.appendChild(document.createTextNode(" " + text));
    body.appendChild(d);
    while (body.childElementCount > 400) body.removeChild(body.firstChild);
    body.scrollTop = body.scrollHeight;
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
    if (d && d.__harnessPicked) { onElementPicked(d); return; }
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
    serverRunning = false; runReady = false; stopRuntimePoll();
    selectLabel(name);
    intoIterateMode();
    setMode("files");
    loadFiles(); reloadPreview(); runTests(); refreshRuntime();
  }

  async function send() {
    const prompt = $("#st-prompt").value.trim();
    if (!prompt) return;
    const eng = engineParams();
    runningUI(true); $("#st-log").textContent = ""; setStatus("running", "working");
    const ref = refParams();
    const runCmd = ($("#st-run-cmd").value || "").trim() || null;
    let url, body;
    if (project) {
      url = "/api/iterate";
      body = { workspace: project, instruction: prompt, ...eng, ...ref };
      if (pickTarget) body.target = pickTarget;
    } else {
      url = "/api/builds";
      body = { prompt, name: ($("#st-name").value || "app").trim(), kind: $("#st-kind").value,
               scaffold: ($("#st-scaffold").value || null), plan: $("#st-plan").checked,
               multi: $("#st-multi").checked, security_scan: $("#st-security").checked,
               run_command: runCmd, ...eng, ...ref };
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
      clearRef(); clearTarget(); stopVoice();
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

  let shipFiles = {};
  async function openShip() {
    if (!project) return;
    const dlg = $("#ship-dialog");
    $("#ship-msg").textContent = "";
    $("#ship-preview").textContent = "Generating deployment files…";
    $("#ship-tabs").innerHTML = "";
    $("#ship-zip").href = `/api/ship/zip?dir=${encodeURIComponent(project)}`;
    $("#ship-zip").setAttribute("download", `${project}.zip`);
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
    loadDeployProviders();
    $("#deploy-out").hidden = true; $("#deploy-url").hidden = true;
    try {
      const r = await (await fetch(`/api/ship?dir=${encodeURIComponent(project)}`)).json();
      if (r.error) { $("#ship-preview").textContent = r.error; return; }
      shipFiles = r.files || {};
      $("#ship-stack").textContent = r.stack || "";
      const names = Object.keys(shipFiles);
      $("#ship-tabs").innerHTML = names.map((n, i) =>
        `<button class="ship-tab${i === 0 ? " active" : ""}" data-f="${n}">${n}</button>`).join("");
      $$("#ship-tabs .ship-tab").forEach((b) => b.addEventListener("click", () => {
        $$("#ship-tabs .ship-tab").forEach((x) => x.classList.remove("active"));
        b.classList.add("active");
        $("#ship-preview").textContent = shipFiles[b.dataset.f] || "";
      }));
      $("#ship-preview").textContent = names.length ? shipFiles[names[0]] : "(nothing to generate)";
    } catch { $("#ship-preview").textContent = "failed to generate deployment files"; }
  }
  async function shipAdd() {
    if (!project) return;
    $("#ship-msg").textContent = "Writing files…";
    try {
      const r = await (await fetch("/api/ship", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: project }),
      })).json();
      if (r.error) { $("#ship-msg").textContent = r.error; return; }
      const w = r.written || [];
      $("#ship-msg").textContent = w.length
        ? `Added to project: ${w.join(", ")}. Run \`docker compose up --build\`.`
        : "All deployment files already present.";
      loadFiles();
    } catch { $("#ship-msg").textContent = "failed to write files"; }
  }
  let deployProviders = [];
  async function loadDeployProviders() {
    const sel = $("#deploy-provider");
    if (deployProviders.length) return;
    try {
      const r = await (await fetch("/api/deploy/providers")).json();
      deployProviders = r.providers || [];
      sel.innerHTML = deployProviders.map((p) =>
        `<option value="${p.id}">${p.label}${p.cli_present ? " ✓" : ""}</option>`).join("");
    } catch {}
  }
  async function runDeploy() {
    if (!project) return;
    const provider = $("#deploy-provider").value;
    const btn = $("#deploy-go"); const old = btn.textContent;
    btn.disabled = true; btn.textContent = "Deploying…";
    $("#deploy-out").hidden = false; $("#deploy-out").textContent = "Working… (running the provider CLI)";
    $("#deploy-url").hidden = true;
    try {
      const r = await (await fetch("/api/deploy", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ workspace: project, provider }),
      })).json();
      if (r.ok && r.url) {
        $("#deploy-url").href = r.url; $("#deploy-url").textContent = "🌐 " + r.url; $("#deploy-url").hidden = false;
        $("#deploy-out").textContent = (r.log || "").slice(-1500) || ("Deployed → " + r.url);
        pollDeployLive(r.url);
      } else if (r.ready === false) {
        $("#deploy-out").textContent =
          (r.reason || "CLI not found") + "\n\n" + (r.commands || []).join("\n") +
          "\n\nThen your app will be live at: " + (r.url || "");
        if (r.url) { $("#deploy-url").href = r.url; $("#deploy-url").textContent = "🌐 " + r.url + " (after deploy)"; $("#deploy-url").hidden = false; }
      } else {
        $("#deploy-out").textContent = (r.reason || r.error || "deploy failed") + "\n\n" + (r.log || "");
      }
      loadFiles();
    } catch (e) { $("#deploy-out").textContent = "deploy request failed"; }
    finally { btn.disabled = false; btn.textContent = old; }
  }
  // --- design profile (project memory): persist the user's house style ---------
  async function openProfile() {
    const dlg = $("#profile-dialog");
    $("#profile-msg").textContent = "";
    try {
      const p = await (await fetch("/api/profile")).json();
      $("#pf-stack").value = p.stack || "";
      $("#pf-style").value = p.ui_style || "";
      $("#pf-palette").value = (p.palette || []).join(", ");
      $("#pf-type").value = p.typography || "";
      $("#pf-components").value = p.components || "";
      $("#pf-tone").value = p.tone || "";
      $("#pf-notes").value = p.notes || "";
      $("#pf-autolearn").checked = p.auto_learn !== false;
      renderSwatches(p.palette || []);
    } catch {}
    if (typeof dlg.showModal === "function") dlg.showModal(); else dlg.setAttribute("open", "");
  }
  function renderSwatches(palette) {
    $("#pf-swatches").innerHTML = (palette || []).map((c) =>
      `<i class="sw" style="background:${c.replace(/[^#0-9a-fA-F]/g, "")}" title="${c}"></i>`).join("");
  }
  async function saveProfile() {
    const body = {
      stack: $("#pf-stack").value, ui_style: $("#pf-style").value,
      palette: $("#pf-palette").value.split(",").map((s) => s.trim()).filter(Boolean),
      typography: $("#pf-type").value, components: $("#pf-components").value,
      tone: $("#pf-tone").value, notes: $("#pf-notes").value,
      auto_learn: $("#pf-autolearn").checked,
    };
    try {
      const p = await (await fetch("/api/profile", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      })).json();
      renderSwatches(p.palette || []);
      $("#profile-msg").textContent = "✓ Saved — applied to every build & iteration.";
    } catch { $("#profile-msg").textContent = "save failed"; }
  }
  function closeProfile() {
    const dlg = $("#profile-dialog");
    if (typeof dlg.close === "function") dlg.close(); else dlg.removeAttribute("open");
  }

  // Poll the deployed URL until it answers, then mark it live ✓.
  async function pollDeployLive(url, attempts = 40) {
    const el = $("#deploy-url");
    el.textContent = "🌐 " + url + " — waiting for it to come up…";
    for (let i = 0; i < attempts; i++) {
      try {
        const r = await (await fetch(`/api/deploy/status?url=${encodeURIComponent(url)}`)).json();
        if (r.live) { el.textContent = "🌐 " + url + " — live ✓"; el.classList.add("live"); return; }
      } catch {}
      await new Promise((res) => setTimeout(res, 3000));
    }
    el.textContent = "🌐 " + url + " (not responding yet)";
  }

  function closeShip() {
    const dlg = $("#ship-dialog");
    if (typeof dlg.close === "function") dlg.close(); else dlg.removeAttribute("open");
  }

  function wire() {
    $("#st-engine").addEventListener("change", onEngineChange);
    $("#st-detect").addEventListener("click", detectLocalModels);
    $("#st-scaffold").addEventListener("change", onScaffoldChange);
    $("#st-send").addEventListener("click", send);
    $("#st-stop").addEventListener("click", stop);
    $("#st-project").addEventListener("change", (e) => {
      const v = e.target.value;
      if (v === NEW) resetToNew(); else setProject(v);
    });
    $("#st-refresh").addEventListener("click", () => { loadFiles(); reloadPreview(); });
    $("#st-reload").addEventListener("click", reloadPreview);
    $("#st-test").addEventListener("click", runTests);
    $("#st-pick").addEventListener("click", togglePick);
    $("#st-target-clear").addEventListener("click", clearTarget);
    $("#st-ship").addEventListener("click", openShip);
    $("#ship-close").addEventListener("click", closeShip);
    $("#ship-add").addEventListener("click", shipAdd);
    $("#deploy-go").addEventListener("click", runDeploy);
    $("#st-style").addEventListener("click", openProfile);
    $("#profile-close").addEventListener("click", closeProfile);
    $("#profile-save").addEventListener("click", saveProfile);
    $("#st-mode-files").addEventListener("click", () => setMode("files"));
    $("#st-mode-diff").addEventListener("click", () => setMode("diff"));
    $("#st-from").addEventListener("change", showDiff);
    $("#st-to").addEventListener("change", showDiff);
    $("#st-restore").addEventListener("click", restoreVersion);
    $("#st-console-toggle").addEventListener("click", toggleConsole);
    $("#st-console-clear").addEventListener("click", clearConsole);
    $("#st-ref-file").addEventListener("change", onRefFile);
    $("#st-ref-clear").addEventListener("click", clearRef);
    $("#st-run-btn").addEventListener("click", toggleServer);
    setupVoice();
    window.addEventListener("message", onPreviewMessage);
    $$('input[name="st-dev"]').forEach((r) => r.addEventListener("change", () => setDevice(r.value)));
    $("#st-prompt").addEventListener("keydown", (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "Enter") send();
    });
  }

  async function show() {
    if (!wired) { wire(); wired = true; }
    applyRecommendedEngine();
    onEngineChange();
    loadScaffolds();
    startHotReload();
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
