// @ts-check
import { api } from "./api.js";
import { renderHome } from "./home.js";
import { openSession, leaveSession } from "./session.js";
import { openSettings } from "./settings.js";
import { openSetup } from "./setup.js";
import { state } from "./state.js";
import { $, applyTheme, closeOverlay, currentTheme, esc, overlayOpen } from "./ui.js";

function route() {
  const hash = location.hash || "#/";
  const m = hash.match(/^#\/session\/(\d+)/);
  const home = $("#homeView"), session = $("#sessionView");
  if (overlayOpen() === "setupDlg" && hash !== "#/new") closeOverlay(true);
  if (m) {
    home.hidden = true;
    session.hidden = false;
    openSession(Number(m[1]));
    return;
  }
  leaveSession();
  session.hidden = true;
  home.hidden = false;
  renderHome();
  if (hash === "#/new") openSetup();
}

function loadLogo() {
  // Official brand logo, if placed at static/assets/brand-logo.svg; otherwise the neutral mark stays
  const img = new Image();
  img.alt = "";
  img.onload = () => {
    const mark = $("#brandMark");
    mark.textContent = "";
    mark.appendChild(img);
    mark.classList.add("logo");
  };
  img.src = "/static/assets/brand-logo.svg";
}

async function init() {
  try {
    state.me = await api("/api/me");
  } catch (e) {
    document.body.innerHTML = `<div style="padding:40px;font-family:system-ui"><h1>Looker Helper</h1><p>${esc(e.message)}</p></div>`;
    return;
  }
  let saved = null;
  try { saved = localStorage.getItem("lh-theme"); } catch {}
  applyTheme(saved || state.me.preferences.theme || "system");
  $("#avatar").textContent = state.me.initials;
  $("#avatar").title = `${state.me.name} (${state.me.email})`;
  loadLogo();

  $("#themeBtn").addEventListener("click", () => {
    const next = currentTheme() === "dark" ? "light" : "dark";
    applyTheme(next);
    state.me.preferences.theme = next;
    api("/api/me/preferences", { method: "PUT", body: { theme: next } }).catch(() => {});
  });
  $("#settingsBtn").addEventListener("click", () => openSettings());
  $("#scrim").addEventListener("click", () => closeOverlay());
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeOverlay(); });
  window.addEventListener("hashchange", route);
  route();
}

init();
