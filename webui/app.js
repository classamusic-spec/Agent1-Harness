"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let currentWorkspaceName = null;
let wsTimer = null;
let selectedFile = null;

/* ---------- tabs ---------- */
function showTab(name) {
  $$(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  $$(".panel").forEach((p) => (p.hidden = p.id !== `panel-${name}`));
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  if (name === "gallery") { loadArtifacts(); loadUsage(); }
  if (name === "workspace") pollWorkspace();
  if (name === "studio" && window.Studio) window.Studio.show();
  if (name === "office" && window.OfficeView) {
    window.OfficeView.init(document.getElementById("office-canvas"));
    setTimeout(() => window.OfficeView.resize(), 50);
  }
}
$$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
function applyHash() {
  const q = new URLSearchParams(location.search).get("tab");
  const h = q || location.hash.replace("#", "") || "studio";
  if (["studio", "build", "spec", "gallery", "workspace", "office"].includes(h)) showTab(h);
}
window.addEventListener("hashchange", applyHash);

/* ---------- theme ---------- */
function applyTheme(t) {
  if (!t || t === "auto") document.documentElement.removeAttribute("data-theme");
  else document.documentElement.setAttribute("data-theme", t);
}
(function initTheme() {
  let saved = "auto";
  try { saved = localStorage.getItem("harness-theme") || "auto"; } catch {}
  const q = new URLSearchParams(location.search).get("theme");
  if (q) saved = q;  // URL override (shareable + screenshot-friendly)
  applyTheme(saved);
  const sel = $("#theme");
  if (sel) {
    sel.value = saved;
    sel.addEventListener("change", () => {
      applyTheme(sel.value);
      try { localStorage.setItem("harness-theme", sel.value); } catch {}
      if (window.OfficeView) setTimeout(() => window.OfficeView.resize && window.OfficeView.resize(), 60);
    });
  }
})();

/* ---------- health ---------- */
async function health() {
  try {
    const j = await (await fetch("/api/health")).json();
    $("#health").textContent = j.busy ? "building…" : "ready";
  } catch { $("#health").textContent = "offline"; }
}

/* ---------- build ---------- */
async function loadSpecs() {
  try {
    const { specs } = await (await fetch("/api/specs")).json();
    const sel = $("#spec");
    sel.innerHTML = "";
    for (const s of specs) {
      const o = document.createElement("option");
      o.value = s.path; o.textContent = `${s.name}  ·  ${s.kind}`; o.dataset.name = s.name;
      sel.appendChild(o);
    }
    syncWorkspace();
  } catch {}
}
function syncWorkspace() {
  const o = $("#spec").selectedOptions[0];
  if (o) $("#workspace").placeholder = `workspaces/${o.dataset.name}`;
}
function setStatus(state, text) { const e = $("#status"); e.dataset.state = state; e.textContent = text || state; }
function logClass(line) {
  if (/^\[PASS\]|\bPASS\b/.test(line)) return "ok";
  if (/^\[FAIL\]|\bFAIL\b/.test(line)) return "bad";
  if (/^\[SKIP\]/.test(line)) return "muted";
  if (/^={4,}/.test(line)) return "banner";
  if (/^RESULT|^BUILD/.test(line)) return "result";
  if (/^\[(approval|loop|demo-engine)\]/.test(line)) return "note";
  return "";
}
function appendLog(line) {
  const l = $("#log");
  const div = document.createElement("div");
  div.className = "line " + logClass(line);
  div.textContent = line;
  l.appendChild(div);
  l.scrollTop = l.scrollHeight;
}
const radio = (n) => document.querySelector(`input[name="${n}"]:checked`).value;

function buildBody() {
  const tb = Number($("#tokenbudget").value) || 0;
  const db = Number($("#timebudget").value) || 0;
  return {
    spec: $("#spec").value, workspace: $("#workspace").value || null,
    engine: radio("engine"), model: $("#model").value || null, base_url: $("#baseurl").value || null,
    review: $("#review").checked, review_focus: radio("focus"),
    review_panel: $("#panel").checked ? ["quality", "bugs", "a11y", "security"] : [],
    learn: $("#learn").checked, check_only: $("#checkonly").checked,
    test_first: $("#testfirst").checked,
    approve_plan: $("#approveplan").checked, approve_build: $("#approvebuild").checked,
    token_budget: tb > 0 ? tb : null,
    deadline: db > 0 ? db : null,
  };
}
async function queueAdd() {
  const body = buildBody();
  if (!body.spec) return;
  try {
    await fetch("/api/queue", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    pollQueue();
  } catch {}
}
let queueTimer = null;
async function pollQueue() {
  try {
    const q = await (await fetch("/api/queue")).json();
    const box = $("#queue-box"), list = $("#queue-list");
    const items = q.pending || [];
    const active = q.running || items.length;
    box.hidden = !active;
    $("#queue-cur").textContent = q.running ? `running: ${q.current} (${q.current_status || ""})` : "";
    list.innerHTML = items.map((p, i) =>
      `<li class="row"><span>${i + 1}. ${p.name}</span><span class="muted">queued</span></li>`).join("");
    if (active && !queueTimer) queueTimer = setInterval(pollQueue, 2500);
    if (!active && queueTimer) { clearInterval(queueTimer); queueTimer = null; }
  } catch {}
}
async function run() {
  $("#log").textContent = ""; setStatus("running", "running"); $("#run").disabled = true;
  const body = buildBody();
  try {
    const j = await (await fetch("/api/builds", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })).json();
    if (j.error) { setStatus("error", j.error); appendLog("error: " + j.error); $("#run").disabled = false; return; }
    currentWorkspaceName = j.workspace.split("/").pop();
    stream(j.id);
    showTab("workspace"); // jump to the live view to watch the agent work
  } catch { setStatus("error", "request failed"); $("#run").disabled = false; }
}
function stream(id) {
  const es = new EventSource(`/api/jobs/${id}/events`);
  es.onmessage = (ev) => appendLog(ev.data);
  es.addEventListener("done", (ev) => {
    setStatus(ev.data, ev.data); $("#run").disabled = false; es.close(); health(); pollWorkspace();
  });
  es.onerror = () => { $("#run").disabled = false; es.close(); };
}

/* ---------- new spec ---------- */
function parseLines(text, sep) {
  return text.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    if (!sep) return l;
    const i = l.indexOf(sep);
    return i === -1 ? null : { name: l.slice(0, i).trim(), command: l.slice(i + 1).trim() };
  }).filter(Boolean);
}
function specBody() {
  return {
    name: $("#s-name").value, kind: $("#s-kind").value, language: $("#s-lang").value,
    description: $("#s-desc").value,
    scaffold: ($("#s-scaffold").value || null), run: ($("#s-run").value || null),
    constraints: parseLines($("#s-constraints").value, null),
    verification: parseLines($("#s-verify").value, ":"),
  };
}
async function saveSpec() {
  const msg = $("#spec-msg");
  try {
    const j = await (await fetch("/api/specs", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(specBody()),
    })).json();
    if (j.error) { msg.textContent = "✗ " + j.error; msg.style.color = "var(--bad)"; return null; }
    msg.textContent = `✓ Saved ${j.name}.yaml`; msg.style.color = "var(--good)";
    await loadSpecs(); await loadSpecEditList();
    return j;
  } catch { msg.textContent = "✗ request failed"; msg.style.color = "var(--bad)"; return null; }
}
async function saveAndBuild() {
  const j = await saveSpec();
  if (!j) return;
  showTab("build");
  const sel = $("#spec");
  for (const o of sel.options) if (o.dataset.name === j.name) { sel.value = o.value; break; }
  syncWorkspace();
}

/* ---------- spec authoring: persona preview, scaffold list, edit existing ---------- */
let personaTimer = null;
async function refreshPersona() {
  const kind = $("#s-kind").value;
  $("#persona-kind").textContent = kind;
  try {
    const j = await (await fetch(`/api/persona?kind=${encodeURIComponent(kind)}`)).json();
    $("#persona-prompt").textContent = j.prompt || "";
  } catch {}
}
async function loadSpecScaffolds() {
  try {
    const { scaffolds } = await (await fetch("/api/scaffolds")).json();
    const sel = $("#s-scaffold");
    for (const s of scaffolds) {
      const o = document.createElement("option");
      o.value = s.name; o.textContent = s.label; sel.appendChild(o);
    }
  } catch {}
}
async function loadSpecEditList() {
  try {
    const { specs } = await (await fetch("/api/specs")).json();
    const sel = $("#s-edit");
    const cur = sel.value;
    sel.innerHTML = '<option value="">+ New spec…</option>';
    for (const s of specs) {
      const o = document.createElement("option");
      o.value = s.path; o.textContent = `${s.name} · ${s.kind}`; sel.appendChild(o);
    }
    sel.value = cur;
  } catch {}
}
async function loadSpecForEdit(path) {
  if (!path) { resetSpecForm(); return; }
  try {
    const s = await (await fetch(`/api/spec?path=${encodeURIComponent(path)}`)).json();
    if (s.error) return;
    $("#s-name").value = s.name || "";
    $("#s-kind").value = s.kind || "fullstack";
    $("#s-lang").value = s.language || "";
    $("#s-scaffold").value = s.scaffold || "";
    $("#s-run").value = s.run || "";
    $("#s-desc").value = s.description || "";
    $("#s-constraints").value = (s.constraints || []).join("\n");
    $("#s-verify").value = (s.verification || s.checks || [])
      .map((c) => `${c.name}: ${c.command}`).join("\n");
    $("#spec-heading").textContent = `Edit: ${s.name}`;
    refreshPersona();
  } catch {}
}
function resetSpecForm() {
  for (const id of ["s-name", "s-lang", "s-run", "s-desc", "s-constraints", "s-verify"]) $("#" + id).value = "";
  $("#s-scaffold").value = ""; $("#spec-heading").textContent = "New Spec";
  $("#spec-msg").textContent = "";
}

/* ---------- usage dashboard ---------- */
function fmtNum(n) {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}
function fmtDur(s) {
  s = Math.round(s || 0);
  if (s >= 3600) return (s / 3600).toFixed(1) + "h";
  if (s >= 60) return Math.round(s / 60) + "m";
  return s + "s";
}
async function loadUsage() {
  try {
    const u = await (await fetch("/api/usage")).json();
    const stats = [
      ["Builds", u.runs || 0],
      ["Tokens", fmtNum(u.tokens || 0)],
      ["Time", fmtDur(u.seconds || 0)],
      ["Passed", `${u.passed || 0}/${u.runs || 0}`],
    ];
    $("#usage-stats").innerHTML = stats.map(([k, v]) =>
      `<div class="ustat"><div class="ustat-v">${v}</div><div class="ustat-k">${k}</div></div>`).join("");
    $("#usage-sub").textContent = (u.projects && u.projects.length)
      ? `top: ${u.projects.slice(0, 3).map((p) => `${p.name} (${fmtNum(p.tokens)})`).join(", ")}` : "";
    // a tiny tokens-per-day sparkline
    const days = u.by_day || [];
    const max = Math.max(1, ...days.map((d) => d.tokens));
    $("#usage-spark").innerHTML = days.length
      ? days.map((d) => `<i style="height:${Math.max(4, Math.round(36 * d.tokens / max))}px" title="${d.day}: ${fmtNum(d.tokens)} tokens"></i>`).join("")
      : '<span class="muted">No builds yet.</span>';
  } catch {}
}

/* ---------- gallery ---------- */
async function loadArtifacts() {
  const ul = $("#artifacts"); ul.innerHTML = "";
  try {
    const { artifacts } = await (await fetch("/api/artifacts")).json();
    if (!artifacts.length) { ul.innerHTML = '<li class="empty-row">No builds yet.</li>'; return; }
    for (const a of artifacts) {
      const li = document.createElement("li");
      li.className = "row";
      const meta = `${a.files} files${a.has_index ? " · web" : ""}`;
      const right = document.createElement("span");
      right.className = "muted";
      right.textContent = meta;
      const label = document.createElement("span");
      label.textContent = a.name;
      li.append(label, right);
      if (a.has_checkpoint) {
        const btn = document.createElement("button");
        btn.className = "ghost"; btn.textContent = "Resume";
        btn.addEventListener("click", (e) => { e.stopPropagation(); resumeBuild(a.name); });
        li.appendChild(btn);
      }
      li.addEventListener("click", () => selectArtifact(a));
      ul.appendChild(li);
    }
    if (!$("#artifacts .sel")) ul.querySelector(".row")?.click(); // auto-preview first
  } catch {}
}

let selectedArtifact = null;
function selectArtifact(a) {
  selectedArtifact = a;
  $$("#artifacts .row").forEach((r) => r.classList.remove("sel"));
  [...$$("#artifacts .row")].find((r) => r.querySelector("span")?.textContent === a.name)?.classList.add("sel");
  $("#preview-title").textContent = a.name;
  const idx = `/artifact/${encodeURIComponent(a.name)}/index.html`;
  $("#gal-thumb").hidden = true;
  $("#preview").hidden = false;
  $("#preview").src = a.has_index ? idx : "about:blank";
  $("#gal-open").href = a.has_index ? idx : "#";
  $("#gal-open").style.display = a.has_index ? "" : "none";
  $("#gal-zip").href = `/api/ship/zip?dir=${encodeURIComponent(a.name)}`;
  $("#gal-zip").setAttribute("download", `${a.name}.zip`);
  $("#gal-shot").style.display = a.has_index ? "" : "none";
}
async function screenshotArtifact() {
  if (!selectedArtifact) return;
  const btn = $("#gal-shot"); const old = btn.textContent;
  btn.textContent = "rendering…"; btn.disabled = true;
  try {
    const r = await fetch(`/api/screenshot?dir=${encodeURIComponent(selectedArtifact.name)}`);
    if (!r.ok) { const e = await r.json().catch(() => ({})); btn.textContent = e.error || "no screenshot"; return; }
    const blob = await r.blob();
    const img = $("#gal-thumb");
    img.src = URL.createObjectURL(blob);
    img.hidden = false; $("#preview").hidden = true;
    btn.textContent = old;
  } catch { btn.textContent = "failed"; } finally { btn.disabled = false; }
}

async function resumeBuild(name) {
  $("#log").textContent = ""; setStatus("running", "running");
  try {
    const j = await (await fetch("/api/builds", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ resume: true, workspace: name }),
    })).json();
    if (j.error) { setStatus("error", j.error); return; }
    currentWorkspaceName = name;
    showTab("workspace");
    stream(j.id);
  } catch { setStatus("error", "resume failed"); }
}

/* ---------- workspace (live) ---------- */
async function pollWorkspace() {
  try {
    const cur = await (await fetch("/api/current")).json();
    if (cur.name) currentWorkspaceName = cur.name;
    if (!currentWorkspaceName) {
      const { artifacts } = await (await fetch("/api/artifacts")).json();
      if (artifacts.length) currentWorkspaceName = artifacts[0].name; // show the latest build
    }
    $("#ws-title").textContent = currentWorkspaceName ? `Workspace · ${currentWorkspaceName}` : "Workspace";
    const live = $("#ws-live");
    live.dataset.state = cur.busy ? "running" : "idle";
    live.textContent = cur.busy ? "live" : (cur.status || "idle");
    if (!currentWorkspaceName) { $("#tree").innerHTML = '<li class="empty-row">Start a build to watch it here.</li>'; return; }

    const tree = await (await fetch(`/api/workspace?dir=${encodeURIComponent(currentWorkspaceName)}`)).json();
    const ul = $("#tree"); ul.innerHTML = "";
    if (!tree.files.length) { ul.innerHTML = '<li class="empty-row">(empty — waiting for files…)</li>'; }
    for (const f of tree.files) {
      const li = document.createElement("li");
      li.className = "row" + (f.path === selectedFile ? " sel" : "");
      li.innerHTML = `<span>${f.path}</span><span class="muted">${f.size}</span>`;
      li.addEventListener("click", () => openFile(f.path));
      ul.appendChild(li);
    }
    if (!selectedFile && tree.files.length) {
      const idx = tree.files.find((f) => f.path === "index.html") || tree.files[0];
      openFile(idx.path);
    } else if (selectedFile) {
      refreshFile();
    }
    const ifr = $("#ws-preview");
    if (!ifr.hidden) ifr.src = `/artifact/${encodeURIComponent(currentWorkspaceName)}/index.html?t=${Date.now()}`;
  } catch {}

  clearTimeout(wsTimer);
  if (!$("#panel-workspace").hidden) wsTimer = setTimeout(pollWorkspace, 1500);
}
async function openFile(path) {
  selectedFile = path;
  $("#ws-preview").hidden = true; $("#ws-file").hidden = false;
  $("#ws-file-title").textContent = path;
  refreshFile();
}
async function refreshFile() {
  if (!currentWorkspaceName || !selectedFile) return;
  try {
    const j = await (await fetch(`/api/workspace/file?dir=${encodeURIComponent(currentWorkspaceName)}&file=${encodeURIComponent(selectedFile)}`)).json();
    if (j.content !== undefined) $("#ws-file").textContent = j.content;
  } catch {}
}
$("#ws-preview-btn").addEventListener("click", () => {
  const ifr = $("#ws-preview"), pre = $("#ws-file");
  const show = ifr.hidden;
  ifr.hidden = !show; pre.hidden = show;
  $("#ws-file-title").textContent = show ? "Live preview" : (selectedFile || "File");
  if (show && currentWorkspaceName) ifr.src = `/artifact/${encodeURIComponent(currentWorkspaceName)}/index.html?t=${Date.now()}`;
});

/* ---------- telemetry + approval (global poll) ---------- */
let currentJobId = null;
async function pollCurrent() {
  try {
    const c = await (await fetch("/api/current")).json();
    currentJobId = c.job;
    $("#stat").textContent = c.tokens ? `${c.tokens} tokens · ${c.elapsed}s` : "";
    $("#controls").hidden = !c.busy;
    updateMeters(c);
    if (window.OfficeView) window.OfficeView.setState(c);
    const banner = $("#approval");
    if (c.pending) {
      banner.hidden = false;
      $("#approval-title").textContent = `Approve the ${c.pending.kind}?`;
      $("#approval-detail").textContent = formatPending(c.pending);
    } else {
      banner.hidden = true;
    }
  } catch {}
}
function setBar(barId, valId, used, budget, fmt) {
  const pct = Math.min(100, (used / budget) * 100);
  const bar = $(barId);
  bar.style.width = pct + "%";
  bar.className = pct >= 100 ? "over" : pct >= 80 ? "warn" : "";
  $(valId).textContent = `${fmt(used)} / ${fmt(budget)}`;
}
function updateMeters(c) {
  const meters = $("#meters");
  const hasTok = c.token_budget > 0;
  const hasTime = c.deadline > 0;
  if (!hasTok && !hasTime) { meters.hidden = true; return; }
  meters.hidden = false;
  const tokMeter = $("#bar-tokens").closest(".meter");
  const timeMeter = $("#bar-time").closest(".meter");
  tokMeter.style.display = hasTok ? "" : "none";
  timeMeter.style.display = hasTime ? "" : "none";
  if (hasTok) setBar("#bar-tokens", "#meter-tokens", c.tokens || 0, c.token_budget, (n) => `${n}`);
  if (hasTime) setBar("#bar-time", "#meter-time", c.elapsed || 0, c.deadline, (n) => `${Number(n).toFixed(0)}s`);
}
function formatPending(p) {
  if (p.kind === "plan") return (p.payload.checks || []).map((c) => `${c[0]}: ${c[1]}`).join("\n");
  return `workspace: ${p.payload.workspace || ""}\n${p.payload.reason || ""}`;
}
async function decide(approved) {
  if (!currentJobId) return;
  const message = $("#approval-msg").value;
  $("#approval").hidden = true; $("#approval-msg").value = "";
  await fetch(`/api/jobs/${currentJobId}/approve`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ approved, message }),
  });
}
$("#approve").addEventListener("click", () => decide(true));
$("#reject").addEventListener("click", () => decide(false));

async function control(action) {
  if (!currentJobId) return;
  await fetch(`/api/jobs/${currentJobId}/control`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action }),
  });
}
$("#pause").addEventListener("click", () => control("pause"));
$("#cancel").addEventListener("click", () => control("cancel"));

/* ---------- wire up ---------- */
$("#spec").addEventListener("change", syncWorkspace);
$("#run").addEventListener("click", run);
$("#queue-add").addEventListener("click", queueAdd);
pollQueue();
$("#save-spec").addEventListener("click", saveSpec);
$("#build-spec").addEventListener("click", saveAndBuild);
$("#s-kind").addEventListener("change", () => {
  clearTimeout(personaTimer); personaTimer = setTimeout(refreshPersona, 80);
});
$("#s-edit").addEventListener("change", (e) => loadSpecForEdit(e.target.value));
$("#refresh-gallery").addEventListener("click", loadArtifacts);
$("#gal-shot").addEventListener("click", screenshotArtifact);
loadSpecs(); loadSpecScaffolds(); loadSpecEditList(); refreshPersona();
health(); setInterval(health, 4000);
pollCurrent(); setInterval(pollCurrent, 1500); applyHash();
