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
  if (name === "gallery") loadArtifacts();
  if (name === "workspace") pollWorkspace();
}
$$(".tab").forEach((t) => t.addEventListener("click", () => showTab(t.dataset.tab)));
function applyHash() {
  const h = (location.hash.replace("#", "") || "build");
  if (["build", "spec", "gallery", "workspace"].includes(h)) showTab(h);
}
window.addEventListener("hashchange", applyHash);

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

async function run() {
  $("#log").textContent = ""; setStatus("running", "running"); $("#run").disabled = true;
  const tb = Number($("#tokenbudget").value) || 0;
  const db = Number($("#timebudget").value) || 0;
  const body = {
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
async function saveSpec() {
  const body = {
    name: $("#s-name").value, kind: $("#s-kind").value, language: $("#s-lang").value,
    description: $("#s-desc").value,
    constraints: parseLines($("#s-constraints").value, null),
    verification: parseLines($("#s-verify").value, ":"),
  };
  const msg = $("#spec-msg");
  try {
    const j = await (await fetch("/api/specs", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    })).json();
    if (j.error) { msg.textContent = "✗ " + j.error; msg.style.color = "var(--bad)"; return; }
    msg.textContent = `✓ Saved ${j.name}.yaml`; msg.style.color = "var(--good)";
    await loadSpecs();
  } catch { msg.textContent = "✗ request failed"; msg.style.color = "var(--bad)"; }
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
      li.innerHTML = `<span>${a.name}</span><span class="muted">${a.files} files${a.has_index ? " · web" : ""}</span>`;
      li.addEventListener("click", () => {
        $$("#artifacts .row").forEach((r) => r.classList.remove("sel"));
        li.classList.add("sel");
        $("#preview-title").textContent = a.name;
        $("#preview").src = a.has_index ? `/artifact/${encodeURIComponent(a.name)}/index.html` : "about:blank";
      });
      ul.appendChild(li);
    }
    if (!$("#artifacts .sel")) ul.querySelector(".row")?.click(); // auto-preview first
  } catch {}
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
    updateMeters(c);
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

/* ---------- wire up ---------- */
$("#spec").addEventListener("change", syncWorkspace);
$("#run").addEventListener("click", run);
$("#save-spec").addEventListener("click", saveSpec);
$("#refresh-gallery").addEventListener("click", loadArtifacts);
loadSpecs(); health(); setInterval(health, 4000);
pollCurrent(); setInterval(pollCurrent, 1500); applyHash();
