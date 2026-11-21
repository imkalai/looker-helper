// @ts-check
/** Package sheet: Preview, Metrics, Checks and Files for one built version. */
import { api, download } from "./api.js";
import { $, BAR_CHART, LINE_CHART, closeOverlay, copyText, esc, highlight, highlightDiff, icon, showOverlay, toast } from "./ui.js";

/** @type {any} */
let pkg = null;
let tab = "preview";
let file = 0;
let diffText = "";

const sheet = () => $("#pkgSheet");

function preview() {
  const s = pkg.summary;
  const kpis = s.tiles.filter((t) => t.type === "kpi");
  const charts = s.tiles.filter((t) => t.type !== "kpi");
  const chartHtml = charts.map((t, i) => {
    const wide = charts.length % 2 === 1 && i === charts.length - 1;
    const art = t.type === "line" ? LINE_CHART : t.type === "table"
      ? `<p class="muted small" style="padding:8px 0">${esc(t.metrics.join(" · "))}</p>` : BAR_CHART;
    return `<div class="chart ${wide ? "wide" : ""}"><span>${esc(t.title)}</span>${art}</div>`;
  }).join("");
  return `
    <div class="dash">
      <div style="display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap"><h3>${esc(s.title)}</h3><span class="muted small">Layout preview</span></div>
      ${kpis.length ? `<div class="kpis">${kpis.map((k) => `<div class="kpi"><span>${esc(k.title)}</span><strong>—</strong></div>`).join("")}</div>` : ""}
      ${chartHtml ? `<div class="charts">${chartHtml}</div>` : ""}
    </div>
    <p class="muted small">This shows the layout. The real numbers appear once the files are added to your Looker project.
      ${s.time ? "Viewers can change the date range, and clicking a chart filters the others." : "Clicking a chart filters the others."}</p>`;
}

function metrics() {
  const s = pkg.summary;
  const fmt = { currency: "Money", percent: "Percentage", number: "Number" };
  return `
    <div>${s.metric_details.map((m) => `<div class="metric"><strong>${esc(m.label)}</strong><span class="pill accent">${fmt[m.format] || "Number"}</span><p>${esc(m.text)}.</p></div>`).join("")}
    ${s.breakdowns.map((b) => `<div class="metric"><strong>${esc(b.label)}</strong><span class="pill">Breakdown</span><p>${esc(b.source)}.</p></div>`).join("")}
    ${s.time ? `<div class="metric"><strong>Date</strong><span class="pill">Breakdown</span><p>By ${esc(s.time.grain === "date" ? "day" : s.time.grain)}. Also available by day, week, month, quarter and year.</p></div>` : ""}</div>`;
}

function checks() {
  const marks = { ok: "✓", warn: "!", tip: "!", error: "×" };
  return `
    <div class="trust-sec"><span class="label">Checked for you</span>
      <ul class="tlist">${pkg.checks.map((c) => `<li><span class="mark ${esc(c.status)}">${marks[c.status] || "✓"}</span><span>${esc(c.title)}<small>${esc(c.detail)}</small></span></li>`).join("")}</ul></div>
    <div class="trust-sec"><span class="label">Where the numbers come from</span>
      <div class="lin">${pkg.summary.lineage.map((l) => `<span>${esc(l.name)}</span><span>${esc(l.source)}</span>`).join("")}</div></div>`;
}

function files() {
  const f = pkg.files[file];
  const prev = pkg.versions.filter((v) => v.version < pkg.version).pop();
  return `
    <p class="muted small">For Looker developers. These are the files in the .zip.</p>
    <div class="ftabs" role="tablist" aria-label="Files">${pkg.files.map((x, i) => `<button role="tab" type="button" aria-selected="${i === file}" data-file="${i}">${esc(x.path.split("/").pop())}</button>`).join("")}</div>
    <div class="code-wrap">
      <div class="code-tools"><span class="muted">${esc(f.path)} · version ${pkg.version}</span>
        <span class="actions"><button class="btn sm" type="button" data-act="copy">Copy</button>
        ${prev ? `<button class="btn sm" type="button" data-act="diff" data-against="${prev.id}">${diffText ? "Show file" : `Compare with version ${prev.version}`}</button>` : ""}</span></div>
      <pre class="code">${diffText ? highlightDiff(diffText) : highlight(f.content)}</pre>
    </div>`;
}

function render() {
  const tabs = [["preview", "Preview"], ["metrics", "Metrics"], ["checks", "Checks"], ["files", "Files"]];
  const body = { preview, metrics, checks, files }[tab]();
  const versions = pkg.versions.length > 1
    ? `<select id="pkgVersion" aria-label="Version">${pkg.versions.map((v) => `<option value="${v.id}" ${v.id === pkg.id ? "selected" : ""}>Version ${v.version}</option>`).join("")}</select>` : "";
  const ok = pkg.summary.status === "ready";
  sheet().innerHTML = `
    <div class="sheet-head">
      <div><span class="label">Package</span><h2>${esc(pkg.summary.title)}</h2></div>
      ${versions}
      <button class="icon-btn" type="button" data-act="close" aria-label="Close">${icon("x", "i")}</button>
    </div>
    <div class="sheet-tabs"><div class="seg" role="group" aria-label="Package view">
      ${tabs.map(([k, l]) => `<button type="button" aria-pressed="${k === tab}" data-tab="${k}">${l}</button>`).join("")}</div></div>
    <div class="sheet-body">${body}</div>
    <div class="sheet-foot">
      <span>${ok ? '<span class="pill ok">✓ Checked and ready</span>' : '<span class="pill warn">See Checks</span>'}</span>
      <button class="btn primary" type="button" data-act="zip">${icon("dl")} Download .zip</button>
    </div>`;
}

/** @param {number} id @param {boolean} [codeFirst] */
export async function openPackage(id, codeFirst = false) {
  showOverlay("pkgSheet", () => {});
  sheet().innerHTML = '<div class="sheet-body"><div class="skeleton" style="height:240px"></div></div>';
  tab = codeFirst ? "files" : "preview";
  await load(id);
}

async function load(id) {
  try {
    pkg = await api(`/api/packages/${id}`);
    file = 0;
    diffText = "";
    render();
  } catch (e) {
    sheet().innerHTML = `<div class="sheet-body"><p class="err-box">${esc(e.message)}</p></div>`;
  }
}

sheet().addEventListener("click", async (e) => {
  const el = /** @type {HTMLElement} */ (e.target).closest("[data-act], [data-tab], [data-file]");
  if (!el || !(el instanceof HTMLElement) || !pkg) return;
  if (el.dataset.tab) { tab = el.dataset.tab; diffText = ""; return render(); }
  if (el.dataset.file) { file = Number(el.dataset.file); diffText = ""; return render(); }
  const act = el.dataset.act;
  if (act === "close") return closeOverlay();
  if (act === "zip") return download(`/api/packages/${pkg.id}/download`);
  if (act === "copy") return copyText(pkg.files[file].content, el);
  if (act === "diff") {
    if (diffText) { diffText = ""; return render(); }
    try {
      const r = await api(`/api/packages/${pkg.id}/diff?against=${el.dataset.against}&path=${encodeURIComponent(pkg.files[file].path)}`);
      diffText = r.diff;
      render();
    } catch (err) { toast(err.message, true); }
  }
});

sheet().addEventListener("change", (e) => {
  const t = /** @type {HTMLSelectElement} */ (e.target);
  if (t.id === "pkgVersion") load(Number(t.value));
});
