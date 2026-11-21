// @ts-check
import { api, download } from "./api.js";
import { state } from "./state.js";
import { $, esc, icon, relTime, thumb } from "./ui.js";

const EXAMPLES = [
  ["Sales by region", "Revenue by region for each month, as a dashboard"],
  ["Customer churn", "Number of churn events by reason each month"],
  ["Top products", "Top 10 products by revenue"],
];

let filter = "all";
let query = "";
/** @type {any[]} */
let sessions = [];

const STATUS = { ready: ["ok", "Ready"], review: ["warn", "Needs a look"], new: ["", "Not built yet"] };

function card(s) {
  const p = s.package;
  const [cls, label] = STATUS[s.status] || STATUS.new;
  const what = p ? `${p.dashboards ? "1 dashboard" : "No dashboard yet"} · ${p.metrics} metric${p.metrics === 1 ? "" : "s"}` : "Nothing built yet";
  return `
    <article class="pkg" data-status="${esc(s.status)}">
      <div class="thumb">${thumb(p?.thumb)}</div>
      <div class="pkg-body">
        <a href="#/session/${s.id}">${esc(s.title)}</a>
        <span class="sub">${esc(s.dataset_label)} · ${esc(relTime(s.updated_at))}</span>
        <span class="sub">${esc(what)}</span>
      </div>
      <div class="pkg-foot">
        <span class="pill ${cls}">${label}</span>
        ${p ? `<button class="btn sm" type="button" data-zip="${s.id}" aria-label="Download ${esc(s.title)} as .zip">${icon("dl")} .zip</button>` : ""}
      </div>
    </article>`;
}

function renderShelf() {
  const shelf = $("#shelf");
  const q = query.toLowerCase();
  const visible = sessions.filter((s) => (filter === "all" || s.status === filter) && `${s.title} ${s.dataset_label}`.toLowerCase().includes(q));
  const newCard = `<a class="pkg new" href="#/new"><span class="plus">${icon("plus", "i")}</span><strong>Start something new</strong><span class="small">Pick your data, then ask</span></a>`;
  shelf.innerHTML = newCard + visible.map(card).join("");
  $("#shelfEmpty").hidden = sessions.length > 0;
}

export async function renderHome() {
  const root = $("#homeView");
  root.innerHTML = `
    <div class="home-inner">
      <div class="hero">
        <h1>What would you like to measure?</h1>
        <p>Describe it in your own words. We'll turn your BigQuery tables into Looker metrics and a dashboard.</p>
        <div class="prompt">
          <label for="homeAsk" class="label" style="padding:4px 10px 0">Your request</label>
          <textarea id="homeAsk" rows="2" placeholder="e.g. Revenue by region for each month, as a dashboard">${esc(state.pendingRequest)}</textarea>
          <div class="prompt-foot">
            <div class="chips">${EXAMPLES.map(([l, t]) => `<button class="chip" type="button" data-ex="${esc(t)}">${esc(l)}</button>`).join("")}</div>
            <button class="btn primary" type="button" id="homeStart">Start ${icon("send")}</button>
          </div>
        </div>
      </div>
      <div style="display:flex;flex-direction:column;gap:16px">
        <div class="work-head">
          <div><h2>Your work</h2><p class="muted small">Each session is a package you can reopen or download as one .zip.</p></div>
          <div class="work-tools">
            <input id="workSearch" placeholder="Search your work…" aria-label="Search your work" value="${esc(query)}">
            <div class="seg" role="group" aria-label="Filter by status">
              <button type="button" data-filter="all" aria-pressed="${filter === "all"}">All</button>
              <button type="button" data-filter="ready" aria-pressed="${filter === "ready"}">Ready</button>
              <button type="button" data-filter="review" aria-pressed="${filter === "review"}">Needs a look</button>
            </div>
          </div>
        </div>
        <div class="shelf" id="shelf"><div class="skeleton" style="height:200px"></div></div>
        <p class="empty" id="shelfEmpty" hidden>Your sessions will appear here. Describe what you'd like to measure above to start the first one.</p>
      </div>
    </div>`;

  const ask = /** @type {HTMLTextAreaElement} */ ($("#homeAsk"));
  const start = () => { state.pendingRequest = ask.value.trim(); location.hash = "#/new"; };
  $("#homeStart").addEventListener("click", start);
  ask.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); start(); } });
  ask.addEventListener("input", () => (state.pendingRequest = ask.value));
  root.querySelectorAll("[data-ex]").forEach((b) => b.addEventListener("click", () => {
    ask.value = /** @type {HTMLElement} */ (b).dataset.ex || "";
    state.pendingRequest = ask.value;
    ask.focus();
  }));
  $("#workSearch").addEventListener("input", (e) => { query = /** @type {HTMLInputElement} */ (e.target).value; renderShelf(); });
  root.querySelectorAll("[data-filter]").forEach((b) => b.addEventListener("click", () => {
    filter = /** @type {HTMLElement} */ (b).dataset.filter || "all";
    root.querySelectorAll("[data-filter]").forEach((x) => x.setAttribute("aria-pressed", String(x === b)));
    renderShelf();
  }));
  $("#shelf").addEventListener("click", (e) => {
    const zip = /** @type {HTMLElement} */ (e.target).closest("[data-zip]");
    if (zip) download(`/api/sessions/${/** @type {HTMLElement} */ (zip).dataset.zip}/download`);
  });

  try {
    sessions = await api("/api/sessions");
  } catch (e) {
    sessions = [];
    $("#shelf").innerHTML = `<p class="err-box">${esc(e.message)}</p>`;
    return;
  }
  renderShelf();
}
