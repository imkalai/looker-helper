// @ts-check
/** New-session dialog: step 1 picks the data, step 2 the Looker connection. */
import { api } from "./api.js";
import { state } from "./state.js";
import { $, closeOverlay, esc, showOverlay, toast } from "./ui.js";

/** @type {any} */
let s = null;

const dlg = () => $("#setupDlg");

function initialState() {
  const prefs = state.me?.preferences || {};
  return {
    step: 1, request: state.pendingRequest || "", busy: false, error: "",
    projects: null, project: prefs.default_project || "", datasets: null, dataset: prefs.default_dataset || "",
    tables: null, selected: new Set(), suggested: false,
    connections: null, connectionId: prefs.default_connection_id || null, addingNew: false, testMsg: null,
    modelName: "",
  };
}

export async function openSetup() {
  s = initialState();
  showOverlay("setupDlg", () => { if (location.hash === "#/new") location.hash = "#/"; });
  render();
  await loadProjects();
}

async function guard(fn) {
  try {
    s.error = "";
    await fn();
  } catch (e) {
    s.error = e.message;
  }
  render();
}

async function loadProjects() {
  await guard(async () => {
    s.projects = await api("/api/meta/projects");
    if (!s.projects.some((p) => p.id === s.project)) s.project = s.projects[0]?.id || "";
    if (s.project) await loadDatasets();
  });
}

async function loadDatasets() {
  s.datasets = null; s.tables = null; render();
  s.datasets = await api(`/api/meta/projects/${encodeURIComponent(s.project)}/datasets`);
  if (!s.datasets.some((d) => d.id === s.dataset)) s.dataset = s.datasets[0]?.id || "";
  if (s.dataset) await loadTables();
}

async function loadTables() {
  s.tables = null; s.selected = new Set(); s.suggested = false; render();
  s.tables = await api(`/api/meta/projects/${encodeURIComponent(s.project)}/datasets/${encodeURIComponent(s.dataset)}/tables`);
  s.modelName = s.dataset;
  render();
  if (s.request.trim()) {
    try {
      const r = await api("/api/meta/suggest-tables", { body: { project: s.project, dataset: s.dataset, request: s.request } });
      if (r.tables.length) { s.selected = new Set(r.tables); s.suggested = true; }
    } catch { /* suggestions are optional */ }
  }
}

async function loadConnections() {
  s.connections = await api("/api/connections");
  if (!s.connections.some((c) => c.id === s.connectionId)) s.connectionId = s.connections[0]?.id || null;
  s.addingNew = s.connections.length === 0;
}

function datasetLabel() {
  return s.datasets?.find((d) => d.id === s.dataset)?.name || s.dataset;
}

function stepOne() {
  const loading = '<div class="skeleton"></div>';
  const projects = s.projects
    ? `<div class="choice">${s.projects.map((p) => `<label><input type="radio" name="proj" value="${esc(p.id)}" ${p.id === s.project ? "checked" : ""}><span>${esc(p.name)} ${p.name !== p.id ? `<small>${esc(p.id)}</small>` : ""}</span></label>`).join("")}</div>`
    : loading;
  const datasets = s.datasets
    ? (s.datasets.length ? `<div class="choice">${s.datasets.map((d) => `<label><input type="radio" name="ds" value="${esc(d.id)}" ${d.id === s.dataset ? "checked" : ""}><span>${esc(d.name)} ${d.name !== d.id ? `<small>${esc(d.id)}</small>` : ""}</span></label>`).join("")}</div>` : '<p class="muted small">No datasets you can read in this project.</p>')
    : (s.projects ? loading : "");
  const tables = s.tables
    ? (s.tables.length ? `<div class="tiles">${s.tables.map((t) => `
        <label class="tile"><input type="checkbox" value="${esc(t.name)}" ${s.selected.has(t.name) ? "checked" : ""}>
          <strong>${esc(t.label)}</strong><small>${esc(t.description || "No description")}</small><em>${esc(t.rows_text)}</em></label>`).join("")}</div>`
      : '<p class="muted small">This dataset has no tables.</p>')
    : (s.datasets ? loading : "");
  const hint = s.suggested ? "suggested from your request" : "pick the tables your question is about";
  return `
    <div class="q"><span>Google Cloud project</span>${projects}</div>
    <div class="q"><span>Dataset</span>${datasets}</div>
    <div class="q"><span>Tables <span class="muted small" style="font-weight:400">· ${s.selected.size} picked · ${hint}</span></span>${tables}</div>`;
}

function connForm() {
  return `
    <div class="form" id="newConnForm">
      <h3>New Looker connection</h3>
      <div class="fields">
        <div class="field"><label for="ncName">Name</label><input id="ncName" placeholder="e.g. Finance Looker"><span class="hint">Any name that helps you recognise it</span></div>
        <div class="field"><label for="ncUrl">Looker address</label><input id="ncUrl" placeholder="https://yourcompany.cloud.looker.com"><span class="hint">The address you open Looker with</span></div>
        <div class="field"><label for="ncBq">BigQuery connection in Looker</label><input id="ncBq" placeholder="e.g. bq_finance"><span class="hint">Ask your Looker admin if unsure</span></div>
        <div class="field"><label for="ncLayout">Folder layout</label><select id="ncLayout"><option value="standard">views/ models/ dashboards/ (standard)</option><option value="flat">All files in one folder</option></select></div>
        <div class="field"><label for="ncId">API client ID <span class="muted">(optional)</span></label><input id="ncId" autocomplete="off"></div>
        <div class="field"><label for="ncSecret">API client secret <span class="muted">(optional)</span></label><input id="ncSecret" type="password" autocomplete="new-password"><span class="hint">Stored encrypted</span></div>
      </div>
      <div class="actions">
        <button class="btn sm" type="button" data-act="testConn">Test</button>
        <button class="btn sm primary" type="button" data-act="saveConn">Save and use</button>
        ${s.testMsg ? `<span class="${s.testMsg.status === "failed" ? "err-msg" : "ok-msg"}">${esc(s.testMsg.message)}</span>` : ""}
      </div>
    </div>`;
}

function stepTwo() {
  if (!s.connections) return '<div class="skeleton"></div>';
  const pill = (c) => c.last_test_status === "ok" ? '<span class="pill ok">Connected</span>' : c.last_test_status === "failed" ? '<span class="pill warn">Check failed</span>' : '<span class="pill">Not tested</span>';
  const tiles = s.connections.map((c) => `
    <label class="tile"><input type="radio" name="conn" value="${c.id}" ${c.id === s.connectionId && !s.addingNew ? "checked" : ""}>
      <strong>${esc(c.name)}${c.shared ? ' <span class="muted small">· shared</span>' : ""}</strong><small>${esc(c.base_url.replace(/^https?:\/\//, ""))} · “${esc(c.bq_connection_name)}”</small>${pill(c)}</label>`).join("");
  return `
    ${s.connections.length ? "" : '<p class="muted small">You don’t have a Looker connection yet. Add one below, or start without one and add it later in Settings.</p>'}
    <div class="conn-tiles">${tiles}
      <label class="tile add"><input type="radio" name="conn" value="new" ${s.addingNew ? "checked" : ""}><strong>+ Add a new Looker connection</strong><small>Saved to your settings for next time</small></label>
    </div>
    ${s.addingNew ? connForm() : ""}
    <details class="dev">
      <summary>Developer options</summary>
      <div class="fields">
        <div class="field"><label for="modelName">Model name</label><input id="modelName" value="${esc(s.modelName)}"><span class="hint">Creates <span class="mono">${esc(s.modelName || "model")}.model.lkml</span>. Use an existing model's name to add to it.</span></div>
      </div>
    </details>`;
}

function render() {
  if (!s) return;
  const one = s.step === 1;
  const title = one ? "Which data should we use?" : "Where will this be used in Looker?";
  const hint = one ? (s.tables ? "Only these tables will be used in this session" : "") : "These settings stay fixed for this session";
  const canNext = one && s.selected.size > 0 && !s.busy;
  const none = s.connections?.length === 0;
  const canStart = !one && !s.busy && !!s.connections && (s.addingNew ? none : (!!s.connectionId || none));
  dlg().innerHTML = `
    <div class="dlg-head">
      <div class="dlg-title">
        <div><span class="label">Step ${s.step} of 2</span><h2 id="dlgTitle">${title}</h2></div>
        <button class="icon-btn" type="button" data-act="close" aria-label="Close"><svg class="i"><use href="#i-x"/></svg></button>
      </div>
      <div class="progress" aria-hidden="true"><i class="on"></i><i class="${one ? "" : "on"}"></i></div>
      <div class="request"><label class="muted" for="dlgRequest">Your request (optional)</label>
        <textarea id="dlgRequest" rows="1" placeholder="e.g. Revenue by region for each month">${esc(s.request)}</textarea></div>
    </div>
    <div class="dlg-body">
      ${s.error ? `<p class="err-box">${esc(s.error)}</p>` : ""}
      ${one ? stepOne() : stepTwo()}
    </div>
    <div class="dlg-foot">
      <span class="muted small">${esc(hint)}</span>
      <span class="actions">
        ${one ? "" : '<button class="btn sm" type="button" data-act="back">Back</button>'}
        ${one ? `<button class="btn sm primary" type="button" data-act="next" ${canNext ? "" : "disabled"}>Next</button>`
               : `<button class="btn sm primary" type="button" data-act="start" ${canStart ? "" : "disabled"}>${s.busy ? "Starting…" : "Start session"}</button>`}
      </span>
    </div>`;
}

function formValues() {
  const v = (id) => /** @type {HTMLInputElement} */ ($("#" + id, dlg()))?.value?.trim() || "";
  return { name: v("ncName"), base_url: v("ncUrl"), bq_connection_name: v("ncBq"), folder_layout: v("ncLayout") || "standard",
           client_id: v("ncId"), client_secret: v("ncSecret") };
}

async function onAction(act) {
  if (act === "close") return closeOverlay();
  if (act === "next") {
    s.step = 2; render();
    return guard(loadConnections);
  }
  if (act === "back") { s.step = 1; return render(); }
  if (act === "testConn") {
    const f = formValues();
    if (!f.base_url || !f.bq_connection_name) { s.testMsg = { status: "failed", message: "Add the Looker address and BigQuery connection name first." }; return render(); }
    return guard(async () => { s.testMsg = await api("/api/connections/test", { body: { ...f, name: f.name || "Test" } }); });
  }
  if (act === "saveConn") {
    const f = formValues();
    if (!f.name || !f.base_url || !f.bq_connection_name) { s.testMsg = { status: "failed", message: "Name, Looker address and BigQuery connection are needed." }; return render(); }
    return guard(async () => {
      const c = await api("/api/connections", { body: f });
      await loadConnections();
      s.connectionId = c.id; s.addingNew = false; s.testMsg = null;
      toast(c.last_test_status === "failed" ? c.last_test_message : "Connection saved");
    });
  }
  if (act === "start") {
    const model = /** @type {HTMLInputElement} */ ($("#modelName", dlg()))?.value?.trim() || s.dataset;
    s.busy = true; render();
    try {
      const created = await api("/api/sessions", { body: {
        project: s.project, dataset: s.dataset, dataset_label: datasetLabel(), tables: [...s.selected],
        looker_connection_id: s.addingNew ? null : s.connectionId, model_name: model,
      } });
      if (s.request.trim()) state.firstMessage = { sessionId: created.id, text: s.request.trim() };
      state.pendingRequest = "";
      closeOverlay(true);
      location.hash = `#/session/${created.id}`;
    } catch (e) {
      s.busy = false; s.error = e.message; render();
    }
  }
}

document.addEventListener("click", (e) => {
  const el = /** @type {HTMLElement} */ (e.target).closest("#setupDlg [data-act]");
  if (el) onAction(/** @type {HTMLElement} */ (el).dataset.act);
});

document.addEventListener("change", async (e) => {
  const t = /** @type {HTMLInputElement} */ (e.target);
  if (!t.closest("#setupDlg") || !s) return;
  if (t.name === "proj") { s.project = t.value; await guard(loadDatasets); }
  else if (t.name === "ds") { s.dataset = t.value; await guard(loadTables); }
  else if (t.type === "checkbox" && t.closest(".tiles")) {
    t.checked ? s.selected.add(t.value) : s.selected.delete(t.value);
    s.suggested = false; render();
  } else if (t.name === "conn") {
    s.addingNew = t.value === "new";
    if (!s.addingNew) s.connectionId = Number(t.value);
    s.testMsg = null; render();
  }
});

document.addEventListener("input", (e) => {
  const t = /** @type {HTMLInputElement} */ (e.target);
  if (!s) return;
  if (t.id === "dlgRequest") s.request = t.value;
  if (t.id === "modelName") s.modelName = t.value;
});
