// @ts-check
/** Settings sheet: opens over whatever page is active and leaves it unchanged when closed. */
import { api } from "./api.js";
import { state } from "./state.js";
import { $, applyTheme, closeOverlay, esc, icon, showOverlay, toast } from "./ui.js";

/** @type {any} */
let st = null;
const sheet = () => $("#prefSheet");

export async function openSettings() {
  st = { connections: null, cortex: null, projects: null, datasets: null, editing: null, adding: false, open: st?.open || "looker" };
  showOverlay("prefSheet", () => {});
  render();
  try {
    const [connections, cortex, projects] = await Promise.all([api("/api/connections"), api("/api/cortex"), api("/api/meta/projects").catch(() => [])]);
    Object.assign(st, { connections, cortex, projects });
    const p = state.me.preferences.default_project;
    if (p) st.datasets = await api(`/api/meta/projects/${encodeURIComponent(p)}/datasets`).catch(() => []);
  } catch (e) {
    toast(e.message, true);
  }
  render();
}

const statusPill = (c) => c.last_test_status === "ok" ? '<span class="pill ok">Connected</span>'
  : c.last_test_status === "failed" ? `<span class="pill warn" title="${esc(c.last_test_message)}">Check failed</span>` : '<span class="pill">Not tested</span>';

function connForm(c) {
  const v = c || { name: "", base_url: "", bq_connection_name: "", looker_project: "", folder_layout: "standard", client_id: "" };
  const shared = state.me.is_admin && !c ? '<label class="check" for="cfShared"><input type="checkbox" id="cfShared"> Share with everyone (admin)</label>' : "";
  return `
    <div class="form" id="connForm" data-id="${c ? c.id : ""}">
      <h3>${c ? "Edit connection" : "Add a Looker connection"}</h3>
      <div class="fields">
        <div class="field"><label for="cfName">Name</label><input id="cfName" value="${esc(v.name)}" placeholder="e.g. Finance Looker"></div>
        <div class="field"><label for="cfUrl">Looker address</label><input id="cfUrl" value="${esc(v.base_url)}" placeholder="https://yourcompany.cloud.looker.com"></div>
        <div class="field"><label for="cfBq">BigQuery connection in Looker</label><input id="cfBq" value="${esc(v.bq_connection_name)}"><span class="hint">Looker › Admin › Connections</span></div>
        <div class="field"><label for="cfLayout">Folder layout</label><select id="cfLayout">
          <option value="standard" ${v.folder_layout === "standard" ? "selected" : ""}>views/ models/ dashboards/ (standard)</option>
          <option value="flat" ${v.folder_layout === "flat" ? "selected" : ""}>All files in one folder</option></select>
          <span class="hint">Sets the .zip folders and the include paths in the model file</span></div>
        <div class="field"><label for="cfProj">Looker project <span class="muted">(optional)</span></label><input id="cfProj" value="${esc(v.looker_project)}"></div>
        <div class="field"><label for="cfId">API client ID <span class="muted">(optional)</span></label><input id="cfId" value="${esc(v.client_id)}" autocomplete="off"></div>
        <div class="field"><label for="cfSecret">API client secret <span class="muted">(optional)</span></label><input id="cfSecret" type="password" autocomplete="new-password" placeholder="${c?.has_secret ? "Saved. Leave empty to keep it" : ""}"><span class="hint">Stored encrypted, never shown again</span></div>
      </div>
      ${shared}
      <div class="actions"><button class="btn sm primary" type="button" data-act="saveConn">Save</button><button class="btn sm quiet" type="button" data-act="cancelConn">Cancel</button></div>
    </div>`;
}

function lookerSection() {
  if (!st.connections) return '<div class="skeleton"></div>';
  const def = st.connections.find((c) => c.is_default);
  const rows = st.connections.map((c) => `
    <div class="row">
      <div><strong>${esc(c.name)}</strong> ${c.is_default ? '<span class="pill accent">Default</span>' : ""} ${c.shared ? '<span class="pill">Shared by admin</span>' : ""}
        <small>${esc(c.base_url.replace(/^https?:\/\//, ""))} · “${esc(c.bq_connection_name)}” · ${c.folder_layout === "flat" ? "all files in one folder" : "standard folders"}</small>
        ${c.last_test_status === "failed" ? `<small class="err-msg">${esc(c.last_test_message)}</small>` : ""}</div>
      ${statusPill(c)}
      <div class="row-btns">
        <button class="btn sm quiet" type="button" data-act="testConn" data-id="${c.id}">Test</button>
        ${c.can_edit ? `<button class="btn sm quiet" type="button" data-act="editConn" data-id="${c.id}">Edit</button>` : ""}
        ${c.is_default ? "" : `<button class="btn sm quiet" type="button" data-act="defaultConn" data-id="${c.id}">Make default</button>`}
        ${c.can_edit ? `<button class="btn sm quiet danger" type="button" data-act="removeConn" data-id="${c.id}">Remove</button>` : ""}
      </div>
    </div>`).join("");
  const editing = st.editing ? st.connections.find((c) => c.id === st.editing) : null;
  return {
    summary: `${st.connections.length} saved${def ? ` · ${esc(def.name)} is your default` : ""}`,
    body: `
      ${st.connections.length ? `<div>${rows}</div>` : '<p class="muted small">No Looker connections yet.</p>'}
      ${st.adding || editing ? connForm(editing) : `<button class="btn sm" type="button" data-act="addConn" style="align-self:flex-start">${icon("plus")} Add connection</button>`}`,
  };
}

function cortexSection() {
  const c = st.cortex;
  if (!c) return { summary: "", body: '<div class="skeleton"></div>' };
  const dis = c.can_edit ? "" : "disabled";
  const modeNote = c.mode === "mock"
    ? '<p class="muted small">The app is using a built-in stand-in for Cortex (development mode). These settings take effect once the Cortex client is connected and LH_CORTEX_MODE=real.</p>' : "";
  return {
    summary: esc(c.message),
    pill: c.ok ? '<span class="pill ok">OK</span>' : '<span class="pill warn">Not connected</span>',
    body: `
      ${modeNote}
      ${c.can_edit ? "" : '<p class="muted small">Only administrators can change these settings.</p>'}
      <div class="fields">
        <div class="field wide"><label for="cxUrl">Cortex endpoint address</label><input id="cxUrl" value="${esc(c.endpoint)}" placeholder="https://cortex.internal.example.com/v1/chat" ${dis}><span class="hint">The proxy address from your platform team</span></div>
        <div class="field"><label for="cxAuth">Sign-in method</label><select id="cxAuth" ${dis}>
          ${[["api_key", "API key"], ["sso_token", "Company sign-in (SSO token)"], ["service_account", "Service account"]].map(([v, l]) => `<option value="${v}" ${c.auth_method === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
        <div class="field"><label for="cxKey">API key</label><input id="cxKey" type="password" autocomplete="off" placeholder="${c.has_key ? "Saved. Leave empty to keep it" : ""}" ${dis}></div>
        <div class="field"><label for="cxModel">AI model</label><input id="cxModel" value="${esc(c.model)}" ${dis}><span class="hint">“default” uses Cortex's recommended model</span></div>
        <div class="field"><label for="cxTimeout">Time limit per request (seconds)</label><input id="cxTimeout" type="number" min="5" max="600" value="${esc(c.timeout)}" ${dis}></div>
      </div>
      <details class="dev"><summary>Advanced (headers, proxy, retries)</summary>
        <div class="fields">
          <div class="field"><label for="cxHeaders">Extra request headers</label><input id="cxHeaders" value="${esc(c.headers)}" placeholder="X-Team-Id: analytics" ${dis}></div>
          <div class="field"><label for="cxRetries">Retries on failure</label><input id="cxRetries" type="number" min="0" max="5" value="${esc(c.retries)}" ${dis}></div>
          <div class="field"><label for="cxProxy">HTTPS proxy</label><input id="cxProxy" value="${esc(c.proxy)}" placeholder="http://proxy.example.com:8080" ${dis}></div>
          <div class="field"><label for="cxCa">Custom CA certificate</label><input id="cxCa" value="${esc(c.ca_bundle)}" placeholder="/etc/ssl/company-root.pem" ${dis}></div>
        </div></details>
      <div class="actions"><button class="btn sm" type="button" data-act="testCortex">Test connection</button>
        ${c.can_edit ? '<button class="btn sm primary" type="button" data-act="saveCortex">Save</button>' : ""}</div>`,
  };
}

function appearanceSection() {
  const p = state.me.preferences;
  const theme = p.theme || "system";
  const sw = (id, checked, title, text) => `<div class="row"><label for="${id}"><strong>${title}</strong><small>${text}</small></label><span class="switch"><input type="checkbox" id="${id}" ${checked ? "checked" : ""}><i></i></span></div>`;
  return {
    summary: "Theme, code view, plan confirmation",
    body: `
      <div class="q"><span>Theme</span><div class="choice">
        ${[["light", "Light"], ["dark", "Dark"], ["system", "Match my computer"]].map(([v, l]) => `<label><input type="radio" name="theme" value="${v}" ${theme === v ? "checked" : ""}><span>${l}</span></label>`).join("")}</div></div>
      <div>${sw("prefCode", p.show_code_first, "Open packages on the Files tab", "For Looker developers who want the code first")}
           ${sw("prefPlan", p.confirm_plan !== false, "Confirm the plan before building", "Recommended. The assistant waits for your OK.")}</div>`,
  };
}

function defaultsSection() {
  const p = state.me.preferences;
  const projects = st.projects || [];
  const datasets = st.datasets || [];
  const conns = st.connections || [];
  return {
    summary: "Pre-filled when you start a session",
    body: `
      <div class="fields">
        <div class="field"><label for="dfProj">Google Cloud project</label><select id="dfProj"><option value="">Ask me each time</option>
          ${projects.map((x) => `<option value="${esc(x.id)}" ${p.default_project === x.id ? "selected" : ""}>${esc(x.name)}${x.name !== x.id ? ` (${esc(x.id)})` : ""}</option>`).join("")}</select></div>
        <div class="field"><label for="dfDs">Dataset</label><select id="dfDs"><option value="">Ask me each time</option>
          ${datasets.map((x) => `<option value="${esc(x.id)}" ${p.default_dataset === x.id ? "selected" : ""}>${esc(x.name)}</option>`).join("")}</select></div>
        <div class="field"><label for="dfConn">Looker connection</label><select id="dfConn"><option value="">None</option>
          ${conns.map((c) => `<option value="${c.id}" ${p.default_connection_id === c.id ? "selected" : ""}>${esc(c.name)}</option>`).join("")}</select></div>
        <div class="field"><label for="dfKeep">Keep sessions for</label><select id="dfKeep">
          ${[[90, "90 days"], [365, "1 year"], [0, "Forever"]].map(([v, l]) => `<option value="${v}" ${Number(p.keep_days ?? 90) === v ? "selected" : ""}>${l}</option>`).join("")}</select></div>
      </div>
      <div class="actions"><button class="btn sm primary" type="button" data-act="saveDefaults">Save</button></div>`,
  };
}

function render() {
  const lk = lookerSection();
  const sections = [
    ["looker", "Looker connections", typeof lk === "string" ? { summary: "", body: lk } : lk],
    ["cortex", "AI assistant (Cortex)", cortexSection()],
    ["look", "Appearance and behaviour", appearanceSection()],
    ["defaults", "Defaults for new sessions", defaultsSection()],
  ];
  sheet().innerHTML = `
    <div class="sheet-head">
      <div><span class="label">Settings</span><h2>Your preferences</h2></div>
      <button class="icon-btn" type="button" data-act="close" aria-label="Close">${icon("x", "i")}</button>
    </div>
    <div class="sheet-body">
      ${sections.map(([k, title, sec]) => `
        <details class="acc" data-sec="${k}" ${st.open === k ? "open" : ""}>
          <summary><span class="sum-text"><h3>${title}</h3><small>${sec.summary || ""}</small></span>${sec.pill || ""}</summary>
          <div class="acc-body">${sec.body}</div>
        </details>`).join("")}
    </div>`;
}

const val = (id) => /** @type {HTMLInputElement} */ ($("#" + id, sheet()))?.value?.trim() ?? "";

async function act(name, id) {
  try {
    if (name === "close") return closeOverlay();
    if (name === "addConn") { st.adding = true; st.editing = null; return render(); }
    if (name === "editConn") { st.editing = id; st.adding = false; return render(); }
    if (name === "cancelConn") { st.adding = false; st.editing = null; return render(); }
    if (name === "saveConn") {
      const secret = val("cfSecret");
      const body = {
        name: val("cfName"), base_url: val("cfUrl"), bq_connection_name: val("cfBq"), folder_layout: val("cfLayout"),
        looker_project: val("cfProj"), client_id: val("cfId"), client_secret: secret || (st.editing ? null : ""),
        shared: /** @type {HTMLInputElement} */ ($("#cfShared", sheet()))?.checked || false,
      };
      if (!body.name || !body.base_url || !body.bq_connection_name) return toast("Name, Looker address and BigQuery connection are needed.", true);
      const saved = st.editing ? await api(`/api/connections/${st.editing}`, { method: "PUT", body }) : await api("/api/connections", { body });
      toast(saved.last_test_status === "failed" ? saved.last_test_message : "Connection saved", saved.last_test_status === "failed");
      st.adding = false; st.editing = null;
    }
    if (name === "testConn") {
      const c = await api(`/api/connections/${id}/test`, { body: {} });
      toast(c.last_test_message || "Tested", c.last_test_status === "failed");
    }
    if (name === "defaultConn") { await api(`/api/connections/${id}/default`, { body: {} }); state.me.preferences.default_connection_id = id; }
    if (name === "removeConn") {
      const c = st.connections.find((x) => x.id === id);
      if (st.confirmRemove !== id) { st.confirmRemove = id; return toast(`Click Remove again to delete “${c?.name}”.`); }
      await api(`/api/connections/${id}`, { method: "DELETE" });
      st.confirmRemove = null;
      toast("Connection removed");
    }
    if (["saveConn", "testConn", "defaultConn", "removeConn"].includes(name)) {
      st.connections = await api("/api/connections");
      return render();
    }
    if (name === "testCortex") {
      const r = await api("/api/cortex/test", { body: {} });
      return toast(r.message, !r.ok);
    }
    if (name === "saveCortex") {
      const key = val("cxKey");
      await api("/api/cortex", { method: "PUT", body: {
        endpoint: val("cxUrl"), auth_method: val("cxAuth"), api_key: key || null, model: val("cxModel"),
        timeout: Number(val("cxTimeout")) || 60, retries: Number(val("cxRetries")) || 0, headers: val("cxHeaders"),
        proxy: val("cxProxy"), ca_bundle: val("cxCa"),
      } });
      st.cortex = await api("/api/cortex");
      toast("Cortex settings saved");
      return render();
    }
    if (name === "saveDefaults") {
      const body = { default_project: val("dfProj") || null, default_dataset: val("dfDs") || null,
                     default_connection_id: Number(val("dfConn")) || null, keep_days: Number(val("dfKeep")) };
      const r = await api("/api/me/preferences", { method: "PUT", body });
      state.me.preferences = { ...state.me.preferences, ...r.preferences };
      toast("Defaults saved");
    }
  } catch (e) {
    toast(e.message, true);
  }
}

async function savePref(body) {
  try {
    const r = await api("/api/me/preferences", { method: "PUT", body });
    state.me.preferences = { ...state.me.preferences, ...r.preferences };
  } catch (e) { toast(e.message, true); }
}

sheet().addEventListener("click", (e) => {
  const el = /** @type {HTMLElement} */ (e.target).closest("[data-act]");
  if (el instanceof HTMLElement && st) act(el.dataset.act || "", Number(el.dataset.id) || 0);
});

sheet().addEventListener("toggle", (e) => {
  const d = /** @type {HTMLDetailsElement} */ (e.target);
  if (d.classList?.contains("acc") && d.open && st) st.open = d.dataset.sec;
}, true);

sheet().addEventListener("change", async (e) => {
  const t = /** @type {HTMLInputElement} */ (e.target);
  if (!st) return;
  if (t.name === "theme") { applyTheme(t.value); state.me.preferences.theme = t.value; savePref({ theme: t.value }); }
  if (t.id === "prefCode") savePref({ show_code_first: t.checked });
  if (t.id === "prefPlan") savePref({ confirm_plan: t.checked });
  if (t.id === "dfProj") {
    st.datasets = t.value ? await api(`/api/meta/projects/${encodeURIComponent(t.value)}/datasets`).catch(() => []) : [];
    const ds = /** @type {HTMLSelectElement} */ ($("#dfDs", sheet()));
    ds.innerHTML = '<option value="">Ask me each time</option>' + st.datasets.map((x) => `<option value="${esc(x.id)}">${esc(x.name)}</option>`).join("");
  }
});
