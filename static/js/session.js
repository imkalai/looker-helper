// @ts-check
/** Session screen: one conversation with plan cards and package cards. */
import { api, download } from "./api.js";
import { openPackage } from "./package.js";
import { state } from "./state.js";
import { $, esc, icon, thumb, toast } from "./ui.js";

/** @type {any} */
let session = null;
let busy = false;
let currentId = 0;

const root = () => $("#sessionView");

function ticketHtml(s) {
  const n = s.tables.length;
  const conn = s.connection ? ` → ${esc(s.connection.name)}` : "";
  return `<b>${esc(s.dataset_label)}</b> · ${n} table${n === 1 ? "" : "s"}${conn}`;
}

function ticketTitle(s) {
  return `${s.project}.${s.dataset} · ${s.tables.join(", ")}${s.connection ? ` · ${s.connection.bq_connection_name}` : ""} · model ${s.model_name}`;
}

export function leaveSession() {
  $("#topTicket").hidden = true;
  currentId = 0;
}

const face = `<span class="face">${icon("spark")}</span>`;

function planCard(m, latestPending) {
  const p = m.payload;
  const status = p.status;
  const cols = `
    <div class="card-body plan">
      <div><span class="label">Metrics</span><ul>${p.metrics.map((x) => `<li>${esc(x.label)} <span>(${esc(x.text.toLowerCase())})</span></li>`).join("")}</ul></div>
      <div><span class="label">Broken down by</span><ul>${p.breakdowns.length ? p.breakdowns.map((b) => `<li>${esc(b)}</li>`).join("") : "<li><span>Totals only</span></li>"}</ul></div>
      <div><span class="label">Dashboard</span><ul>${p.tiles.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></div>
    </div>`;
  const included = p.included.length ? `<p class="included"><b>Also included:</b> ${esc(p.included.join(", "))}.</p>` : "";
  const notes = p.notes.length ? `<p class="included">${esc(p.notes.join(" "))}</p>` : "";
  let foot;
  if (status === "pending" && m.id === latestPending) {
    foot = `<div class="card-foot"><span class="muted small">Nothing is created until you say so</span>
      <span class="actions"><button class="btn sm" type="button" data-act="change">Change something</button>
      <button class="btn sm primary" type="button" data-act="build" data-plan="${m.id}" ${busy ? "disabled" : ""}>Build it</button></span></div>`;
  } else if (status === "built") {
    foot = `<div class="card-foot"><span class="pill ok">✓ Built</span></div>`;
  } else {
    foot = `<div class="card-foot"><span class="muted small">Replaced by a newer plan</span></div>`;
  }
  return `<div class="card ${status === "superseded" ? "faded" : ""}">${cols}${included}${notes}${foot}</div>`;
}

function packageCard(m) {
  const p = m.payload;
  const pills = [];
  if (p.status === "ready") pills.push('<span class="pill ok">✓ Checked and ready</span>');
  if (p.warnings) pills.push(`<span class="pill warn">${p.warnings} to look at</span>`);
  if (p.tips) pills.push(`<span class="pill warn">${p.tips} tip${p.tips === 1 ? "" : "s"}</span>`);
  return `
    <div class="card result">
      <div class="thumb">${thumb(p.thumb)}</div>
      <div class="info">
        <h3>${esc(p.title)}</h3>
        <p class="muted small">${p.dashboards ? "1 dashboard · " : ""}${p.metrics} metric${p.metrics === 1 ? "" : "s"} · ${p.files} files · version ${p.version}</p>
        <span class="actions" style="gap:6px">${pills.join("")}</span>
        <div class="actions" style="margin-top:auto">
          <button class="btn sm primary" type="button" data-act="open" data-pkg="${p.package_id}">Open package</button>
          <button class="btn sm" type="button" data-act="zip" data-pkg="${p.package_id}">${icon("dl")} .zip</button>
        </div>
      </div>
    </div>`;
}

function messageHtml(m, latestPending) {
  if (m.role === "user") return `<div class="msg user"><div class="bubble">${esc(m.content)}</div></div>`;
  let body = m.kind === "error" ? `<p class="err-box">${esc(m.content)}</p>` : `<p class="text">${esc(m.content)}</p>`;
  if (m.kind === "plan") body += planCard(m, latestPending);
  if (m.kind === "package") body += packageCard(m);
  return `<div class="msg">${face}<div class="body">${body}</div></div>`;
}

function suggestions() {
  const msgs = session.messages;
  const pending = msgs.some((m) => m.kind === "plan" && m.payload.status === "pending");
  const built = msgs.some((m) => m.kind === "package");
  if (pending) return ["Looks good, build it", "Make it weekly", "What does each metric mean?"];
  if (built) return ["Make it weekly", "What does each metric mean?", "Explain how the tables connect"];
  return ["Explain these tables", "What can I measure here?"];
}

function renderThread() {
  const latestPending = [...session.messages].reverse().find((m) => m.kind === "plan" && m.payload.status === "pending")?.id;
  const welcome = session.messages.length ? "" :
    `<div class="msg">${face}<div class="body"><p class="text">This session uses ${esc(session.tables.length)} table${session.tables.length === 1 ? "" : "s"} from ${esc(session.dataset_label)}. What would you like to see? Describe it in your own words.</p></div></div>`;
  $("#threadInner").innerHTML = `
    <div class="divider"><span class="ticket" title="${esc(ticketTitle(session))}">${icon("lock")}<span>${ticketHtml(session)}</span></span></div>
    ${welcome}
    ${session.messages.map((m) => messageHtml(m, latestPending)).join("")}
    ${busy ? '<div class="thinking"><i></i><i></i><i></i> Working on it…</div>' : ""}`;
  $("#chips").innerHTML = suggestions().map((t) => `<button class="chip" type="button" data-say="${esc(t)}">${esc(t)}</button>`).join("");
  const thread = $("#thread");
  thread.scrollTop = thread.scrollHeight;
  /** @type {HTMLButtonElement} */ ($("#sendBtn")).disabled = busy;
}

/** @param {string} text */
async function send(text) {
  text = text.trim();
  if (!text || busy || !session) return;
  const id = session.id;
  busy = true;
  session.messages.push({ id: -1, role: "user", kind: "text", content: text, payload: {} });
  renderThread();
  try {
    await api(`/api/sessions/${id}/messages`, { body: { text } });
    if (currentId !== id) return;
    // A change can supersede earlier plans: reload the session so every card shows its latest state
    session = await api(`/api/sessions/${id}`);
  } catch (e) {
    session.messages = session.messages.filter((m) => m.id !== -1);
    toast(e.message, true);
    /** @type {HTMLTextAreaElement} */ ($("#chatIn")).value = text;
  } finally {
    busy = false;
    if (currentId === id) renderThread();
  }
}

/** @param {number} planId */
async function build(planId) {
  if (busy) return;
  const id = session.id;
  busy = true;
  renderThread();
  try {
    await api(`/api/sessions/${id}/build`, { body: { plan_message_id: planId } });
    session = await api(`/api/sessions/${id}`);
  } catch (e) {
    toast(e.message, true);
  } finally {
    busy = false;
    if (currentId === id) renderThread();
  }
}

/** @param {number} id */
export async function openSession(id) {
  if (currentId === id && session) return;
  currentId = id;
  root().innerHTML = `
    <div class="thread" id="thread"><div class="thread-inner" id="threadInner"><div class="skeleton" style="height:120px"></div></div></div>
    <div class="composer"><div class="composer-inner">
      <div class="chips" id="chips"></div>
      <div class="box"><textarea id="chatIn" rows="1" placeholder="Ask for something, or a change, in your own words…" aria-label="Message"></textarea>
        <button class="btn primary sm" type="button" id="sendBtn" aria-label="Send">${icon("send")}</button></div>
    </div></div>`;
  const input = /** @type {HTMLTextAreaElement} */ ($("#chatIn"));
  const submit = () => { const t = input.value; input.value = ""; input.style.height = ""; send(t); };
  input.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); submit(); } });
  input.addEventListener("input", () => { input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 160) + "px"; });
  $("#sendBtn").addEventListener("click", submit);
  if (!root().dataset.wired) {
    root().addEventListener("click", onClick);
    root().dataset.wired = "1";
  }

  try {
    session = await api(`/api/sessions/${id}`);
  } catch (e) {
    $("#threadInner").innerHTML = `<p class="err-box">${esc(e.message)} <a href="#/">Back to Home</a></p>`;
    return;
  }
  if (currentId !== id) return;
  $("#topTicketText").innerHTML = ticketHtml(session);
  $("#topTicket").title = ticketTitle(session);
  $("#topTicket").hidden = false;
  renderThread();
  if (state.firstMessage?.sessionId === id) {
    const text = state.firstMessage.text;
    state.firstMessage = null;
    send(text);
  } else {
    input.focus();
  }
}

/** @param {MouseEvent} e */
function onClick(e) {
  const el = /** @type {HTMLElement} */ (e.target).closest("[data-act], [data-say]");
  if (!el || !(el instanceof HTMLElement)) return;
  if (el.dataset.say) return send(el.dataset.say);
  const act = el.dataset.act;
  if (act === "build") build(Number(el.dataset.plan));
  else if (act === "change") {
    const input = /** @type {HTMLTextAreaElement} */ ($("#chatIn"));
    input.placeholder = "Tell me what to change, e.g. “leave out cancelled orders”";
    input.focus();
  } else if (act === "open") openPackage(Number(el.dataset.pkg), state.me?.preferences?.show_code_first);
  else if (act === "zip") download(`/api/packages/${el.dataset.pkg}/download`);
}
