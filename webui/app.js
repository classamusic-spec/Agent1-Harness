"use strict";

const $ = (sel) => document.querySelector(sel);

async function loadSpecs() {
  try {
    const res = await fetch("/api/specs");
    const { specs } = await res.json();
    const sel = $("#spec");
    sel.innerHTML = "";
    for (const s of specs) {
      const opt = document.createElement("option");
      opt.value = s.path;
      opt.textContent = `${s.name}  ·  ${s.kind}`;
      opt.dataset.name = s.name;
      sel.appendChild(opt);
    }
    syncWorkspace();
  } catch (e) {
    setStatus("error", "could not load specs");
  }
}

function syncWorkspace() {
  const opt = $("#spec").selectedOptions[0];
  if (opt) $("#workspace").placeholder = `workspaces/${opt.dataset.name}`;
}

async function health() {
  try {
    const res = await fetch("/api/health");
    const j = await res.json();
    $("#health").textContent = j.busy ? "busy" : "ready";
  } catch {
    $("#health").textContent = "offline";
  }
}

function setStatus(state, text) {
  const el = $("#status");
  el.dataset.state = state;
  el.textContent = text || state;
}

function appendLog(line) {
  const log = $("#log");
  log.textContent += line + "\n";
  log.scrollTop = log.scrollHeight;
}

function engine() {
  return document.querySelector('input[name="engine"]:checked').value;
}
function focus() {
  return document.querySelector('input[name="focus"]:checked').value;
}

function payload() {
  return {
    spec: $("#spec").value,
    workspace: $("#workspace").value || null,
    engine: engine(),
    model: $("#model").value || null,
    base_url: $("#baseurl").value || null,
    review: $("#review").checked,
    review_focus: focus(),
    learn: $("#learn").checked,
    check_only: $("#checkonly").checked,
  };
}

async function run() {
  $("#log").textContent = "";
  setStatus("running", "running");
  $("#run").disabled = true;
  try {
    const res = await fetch("/api/builds", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload()),
    });
    const j = await res.json();
    if (j.error) {
      setStatus("error", j.error);
      appendLog("error: " + j.error);
      $("#run").disabled = false;
      return;
    }
    stream(j.id);
  } catch (e) {
    setStatus("error", "request failed");
    $("#run").disabled = false;
  }
}

function stream(id) {
  const es = new EventSource(`/api/jobs/${id}/events`);
  es.onmessage = (ev) => appendLog(ev.data);
  es.addEventListener("done", (ev) => {
    setStatus(ev.data, ev.data);
    $("#run").disabled = false;
    es.close();
    health();
  });
  es.onerror = () => {
    $("#run").disabled = false;
    es.close();
  };
}

$("#spec").addEventListener("change", syncWorkspace);
$("#run").addEventListener("click", run);
loadSpecs();
health();
setInterval(health, 5000);
