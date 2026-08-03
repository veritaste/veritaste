"use strict";

const MEALS = ["Breakfast", "Lunch", "Dinner"];

const API = "/api/v1";
const state = { hall: null, date: null, meal: 1, dish: null, user: null,
                items: new Map(), line: null, hallOpenToday: null,
                mealName: null, correctingMeal: false, wallet: null,
                rsvpVisible: false, declarationOpen: true, serviceStatus: null,
                pane: "menu" };

const server = { mode: null, demo: false };

const notify = { supported: "serviceWorker" in navigator && "PushManager" in window,
                 enabled: false, key: "", sub: null,

                 message: "" };

const $ = s => document.querySelector(s);

function localToday() {
  const d = new Date();
  const p = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (!res.ok) {
    let msg = `HTTP ${res.status}`;

    try { const b = await res.json(); msg = b.error || b.detail || msg; } catch {  }
    const err = new Error(msg);
    err.status = res.status;
    throw err;
  }
  return res.status === 204 ? null : res.json();
}

const PANES = [
  { id: "menu",    label: "Menu",             roles: null },

  { id: "grill",   label: "Order the grill",  roles: null, hideFor: ["staff"] },

  { id: "where",   label: "Where can I eat?", staffLabel: "Interhouse rules",
    roles: null, overflowFor: ["staff"] },

  { id: "survey", label: "Rate dishes", staffLabel: "Create survey",
    roles: null, navFor: ["staff"], overflowFor: ["staff"] },
  { id: "availability", label: "Availability board", roles: ["staff"] },
  { id: "station", label: "Grill station", roles: ["staff"] },
  { id: "feedback", label: "Feedback", roles: ["staff"] },
  { id: "line", label: "Line length", roles: ["staff"], overflowFor: ["staff"] },
  { id: "forecast", label: "Forecast", roles: ["staff"], overflowFor: ["staff"] },
  { id: "account", label: "My account",       roles: null },
];
function paneLabel(pane, user) {
  return (user && user.affiliation === "staff" && pane.staffLabel) || pane.label;
}

const STAFF_ORDER = ["menu", "availability", "station", "feedback",
                     "line", "forecast", "survey", "where", "account"];

function panesFor(user) {
  const role = user ? user.affiliation : null;
  const list = PANES.filter(p =>
    (!p.roles || (role && p.roles.includes(role)))
    && !(p.hideFor && role && p.hideFor.includes(role)));
  if (role === "staff") {
    const rank = id => {
      const i = STAFF_ORDER.indexOf(id);
      return i === -1 ? STAFF_ORDER.length : i;
    };
    list.sort((a, b) => rank(a.id) - rank(b.id));
  }
  return list;
}

function defaultPane(user) {

  return "menu";
}

function renderNav() {
  const list = $("#navList");
  const available = panesFor(state.user);
  const role = state.user ? state.user.affiliation : null;

  const listed = available.filter(p =>
    !p.navFor || (role && p.navFor.includes(role)));
  list.innerHTML = listed.map(p =>
    `<li${p.overflowFor && role && p.overflowFor.includes(role)
        ? ' data-overflow="1"' : ""}><a href="?pane=${p.id}" data-pane="${p.id}"
      ${p.id === state.pane ? 'aria-current="page"' : ""}
      >${esc(paneLabel(p, state.user))}</a></li>`).join("")
    + `<li class="nav__more" id="navMore" hidden>
        <button class="nav__morebtn" id="navMoreBtn" aria-haspopup="true"
          aria-expanded="false" aria-label="More panes">…</button>
        <ul class="nav__menu" id="navMenu"></ul>
      </li>`;
  list.querySelectorAll("a").forEach(a =>
    a.addEventListener("click", e => {
      e.preventDefault();
      showPane(a.dataset.pane);
      list.classList.remove("open");
      $("#navToggle").setAttribute("aria-expanded", "false");
    }));
  $("#navMoreBtn").addEventListener("click", e => {
    e.stopPropagation();
    navMenuToggle();
  });

  const current = available.find(p => p.id === state.pane);
  $("#navCurrent").textContent = current ? paneLabel(current, state.user) : "Menu";
  navOverflow();
}

function navOverflow() {
  const list = $("#navList");
  const more = $("#navMore");
  const menu = $("#navMenu");
  if (!more) return;
  const items = [...list.querySelectorAll("li:not(.nav__more)")];
  items.forEach(li => { li.style.display = ""; });
  navMenuClose();
  more.hidden = true;
  menu.innerHTML = "";
  if (window.innerWidth < 620) return;

  items.filter(li => li.hasAttribute("data-overflow")
                     && !li.querySelector("a[aria-current]"))
       .forEach(li => { li.style.display = "none"; });
  const fits = () => list.scrollWidth <= list.clientWidth;
  more.hidden = items.every(li => li.style.display !== "none") && fits();
  let guard = items.length;
  while (!fits() && guard--) {
    const visible = items.filter(li => li.style.display !== "none");
    if (visible.length <= 1) break;
    let pick = visible[visible.length - 1];
    if (pick.querySelector("a[aria-current]")) pick = visible[visible.length - 2];
    if (!pick) break;
    pick.style.display = "none";
  }
  const overflowed = items.filter(li => li.style.display === "none");
  if (!overflowed.length) { more.hidden = true; return; }
  more.hidden = false;
  menu.innerHTML = overflowed.map(li => {
    const a = li.querySelector("a");
    return `<li><a href="${a.getAttribute("href")}" data-pane="${a.dataset.pane}"
      ${a.hasAttribute("aria-current") ? 'aria-current="page"' : ""}
      >${esc(a.textContent)}</a></li>`;
  }).join("");
  menu.querySelectorAll("a").forEach(a =>
    a.addEventListener("click", e => {
      e.preventDefault();
      navMenuClose();
      showPane(a.dataset.pane);
    }));
}

function navMenuToggle() {
  const menu = $("#navMenu");
  const open = !menu.classList.contains("open");
  menu.classList.toggle("open", open);
  $("#navMoreBtn").setAttribute("aria-expanded", String(open));
}

function navMenuClose() {
  $("#navMenu")?.classList.remove("open");
  $("#navMoreBtn")?.setAttribute("aria-expanded", "false");
}

document.addEventListener("click", e => {
  if (!e.target.closest(".nav__more")) navMenuClose();
});
window.addEventListener("resize", () => {
  clearTimeout(state._navResize);
  state._navResize = setTimeout(navOverflow, 120);
});

if (document.fonts?.ready) document.fonts.ready.then(() => navOverflow());

function mealByClock() {
  const now = new Date();
  const h = now.getHours() + now.getMinutes() / 60;
  return h < 10.5 ? 0 : h < 14.5 ? 1 : 2;
}

function goHome() {
  if (state.dish != null) closeSheet({ pop: false });
  state.date = localToday();
  state.meal = mealByClock();
  syncControls();
  showPane("menu");
  refreshContext();
}
$("#brandHome").addEventListener("click", e => { e.preventDefault(); goHome(); });

document.addEventListener("visibilitychange", () => {
  if (document.hidden) {
    stopLinePoll();
    stopGrillPoll();
  } else if (state.pane === "menu") {
    loadLine();
    startLinePoll();
  } else if (state.pane === "grill") {
    startGrillPoll();
  }
});

function showPane(id, { push = true } = {}) {

  if (id === "queue") id = "survey";
  const available = panesFor(state.user);
  if (!available.some(p => p.id === id)) id = defaultPane(state.user);
  state.pane = id;

  document.querySelectorAll(".pane").forEach(el =>
    el.hidden = el.id !== `pane-${id}`);
  renderNav();

  const pane = PANES.find(p => p.id === id);
  if (pane) document.title = `${paneLabel(pane, state.user)} — Veritaste`;
  if (id === "availability") loadBoard();
  if (id === "feedback") loadFeedbackPane();
  if (id === "line") loadLinePane();
  if (id === "forecast") loadForecastPane();
  if (id === "survey") loadQueuePane();
  if (id === "menu") startLinePoll(); else stopLinePoll();
  if (id === "grill") startGrillPoll(); else stopGrillPoll();
  if (id === "station") startStationPoll(); else stopStationPoll();
  if (push) writeUrl();
  window.scrollTo({ top: 0 });
}
function readUrl() {
  const p = new URLSearchParams(location.search);
  const num = k => (p.has(k) && p.get(k) !== "" ? Number(p.get(k)) : null);
  return { hall: num("hall"), date: p.get("date"), meal: num("meal"),
           dish: num("dish"), pane: p.get("pane") };
}

function writeUrl({ replace = false } = {}) {
  const p = new URLSearchParams();

  if (state.pane && state.pane !== "menu") p.set("pane", state.pane);
  if (state.hall != null) p.set("hall", state.hall);
  if (state.date) p.set("date", state.date);
  if (state.meal != null) p.set("meal", state.meal);
  if (state.dish != null) p.set("dish", state.dish);
  const url = `${location.pathname}?${p.toString()}`;
  if (replace) history.replaceState(null, "", url);
  else history.pushState(null, "", url);
}

function syncControls() {
  if (state.hall != null) $("#hall").value = String(state.hall);
  if (state.date) $("#date").value = state.date;
  document.querySelectorAll("#mealSeg button").forEach(b =>
    b.setAttribute("aria-pressed", String(Number(b.dataset.meal) === state.meal)));
}

function renderMealSeg(options, seg = $("#mealSeg")) {

  if (!options || !options.length)
    options = MEALS.map((name, m) => ({ meal: m, name, served: true }));

  seg.hidden = false;

  seg.innerHTML = options.map(o =>
    `<button data-meal="${o.meal}" aria-pressed="${o.meal === state.meal}"
      class="${o.served ? "" : "unavailable"}">${esc(o.name)}${
        o.served ? "" : "<small>not served</small>"}</button>`).join("");
}

function refreshContext() {
  updateRsvpCopy();
  return Promise.all([loadMenu(), loadLine(), loadInterhouse()]);
}

window.addEventListener("popstate", async () => {
  const u = readUrl();
  const contextMoved =
    (u.hall != null && u.hall !== state.hall) ||
    (u.date && u.date !== state.date) ||
    (u.meal != null && u.meal !== state.meal);

  if (u.hall != null) state.hall = u.hall;
  if (u.date) state.date = u.date;
  if (u.meal != null) state.meal = u.meal;
  showPane(u.pane || "menu", { push: false });
  syncControls();
  if (contextMoved) await refreshContext();

  if (u.dish != null && u.dish !== state.dish) {
    openSheet(u.dish, { push: false });
  } else if (u.dish == null && state.dish != null) {
    closeSheet({ pop: false });
  }
});

let toastTimer;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
}

function renderWho() {
  const box = $("#who");
  if (state.user) {

    box.innerHTML = `<span>${esc(state.user.name)}</span>
      <button class="linkbtn" id="signoutBtn">Sign out</button>`;
    $("#signoutBtn").addEventListener("click", async () => {
      await api("/auth/signout", { method: "POST" });
      state.user = null;
      renderWho();

      loadInterhouse();
      loadWallet();
      renderPush();
      renderAccount();
      renderNav();
      markRsvp(null);
      toast("Signed out.");
    });
  } else {
    box.innerHTML = `<button class="linkbtn" id="signinBtn">Sign in</button>`;
    $("#signinBtn").addEventListener("click", signIn);
  }
}

function signIn() {

  window.location.href = "/signin";
}

async function loadMe() {
  try {
    const me = await api("/me");
    state.user = me.signed_in ? me : null;
  } catch { state.user = null; }
  renderWho();
  loadWallet();
  renderAccount();

  renderNav();
}

function renderAccount() {
  const box = $("#acctWhoBody");
  if (!box) return;
  if (!state.user) {
    box.innerHTML = `<p style="margin:0 0 14px;font-size:14px;color:var(--ink-soft)">
      You're browsing signed out. Reading is open to everyone.</p>
      <button class="btn ghost wide" id="acctSignin">Sign in</button>`;
    $("#acctSignin").addEventListener("click", signIn);
    return;
  }

  const staff = state.user.affiliation === "staff";
  const rows = [["Name", state.user.name]];
  if (state.user.house_name) rows.push(["House", state.user.house_name]);
  rows.push(["Role", staff ? "HUDS staff" : "Undergraduate"]);

  box.innerHTML = rows.map(([k, v]) =>
    `<div class="stat"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("")
    + `<p style="margin:14px 0 0;font-size:12.5px;color:var(--ink-mute)">
        A demonstration account. No real credential is ever accepted, and no email
        address is stored.</p>`;
}

async function loadWallet() {
  const card = $("#walletCard");
  if (!state.user || state.user.affiliation !== "student") {
    card.hidden = true;
    return;
  }
  try {
    state.wallet = await api("/rewards/me");
  } catch {
    card.hidden = true;
    return;
  }
  renderWallet(state.wallet);
  card.hidden = false;
}

function renderWallet(w) {
  $("#walletAmt").textContent = w.pending_display;
  $("#walletRates").innerHTML = Object.values(w.earn_rates || {}).map(r =>
    `<div class="wallet__rate"><span>${esc(r.reason)}</span>
      <b>${esc(r.display)}</b></div>`).join("");

  const rows = (w.grants || []).map(g =>
    `<div class="wallet__row"><span>${esc(g.reason)}</span>
      <span class="wallet__when">${esc(g.granted_on)} &nbsp;·&nbsp; ${esc(g.display)}</span>
     </div>`).join("");
  $("#walletLog").innerHTML = rows;
  $("#walletLogSec").hidden = !rows;
}

function creditSuffix(reward) {
  if (!reward || !reward.granted_cents) return "";
  return ` +${reward.granted_display} BoardPlus (simulated).`;
}

async function initPush() {
  try {
    const cfg = await api("/push/vapid");
    notify.enabled = !!cfg.enabled;
    notify.key = cfg.public_key || "";
  } catch { notify.enabled = false; }

  if (notify.enabled && notify.supported) {
    try {
      const reg = await navigator.serviceWorker.getRegistration("/");
      notify.sub = reg ? await reg.pushManager.getSubscription() : null;
    } catch { notify.sub = null; }

    if (notify.sub && state.user) {
      const j = notify.sub.toJSON();
      try {
        await api("/push/subscriptions", {
          method: "POST",
          body: { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth },
        });
      } catch {  }
    }
  }
  renderPush();
}

function renderPush() {
  const card = $("#pushCard");

  if (!notify.enabled || !state.user) { card.hidden = true; return; }
  card.hidden = false;

  $("#pushLead").textContent = state.user.affiliation === "staff"
    ? "Just pre-prep alerts when yesterday's waste needs a decision — nothing else."
    : "Just reminders to tell the kitchen your plans — nothing else.";

  paintPushControl();

  if (notify.message) $("#pushHint").textContent = notify.message;
}

function paintPushControl() {
  const act = $("#pushAct"), hint = $("#pushHint");
  act.innerHTML = "";
  hint.textContent = "";
  if (!notify.supported) {
    act.innerHTML = `<span class="push__off">Not available in this browser</span>`;
    hint.textContent = /iPad|iPhone|iPod/.test(navigator.userAgent)
      ? "On iPhone, notifications work once Veritaste is on your Home Screen: tap Share, then Add to Home Screen, and open it from there."
      : "This browser doesn't support web notifications.";
    return;
  }

  if (Notification.permission === "denied") {

    act.innerHTML = `<span class="push__off">Blocked in your browser</span>
      <button class="btn ghost" id="pushOn">Try again</button>`;
    $("#pushOn").addEventListener("click", enablePush);
    hint.textContent = "Notifications are blocked for this site. Click the bell or lock icon beside the address bar, allow notifications, then try again.";
    return;
  }

  if (notify.sub) {
    act.innerHTML = `<button class="btn ghost" id="pushPrev">Send me a preview</button>
      <button class="btn ghost" id="pushOff">Turn off</button>`;
    $("#pushPrev").addEventListener("click", previewPush);
    $("#pushOff").addEventListener("click", disablePush);
    hint.textContent = "This browser is signed up. A preview arrives straight away, so you can see how one reads.";
    return;
  }

  act.innerHTML = `<button class="btn" id="pushOn">Turn on notifications</button>`;
  $("#pushOn").addEventListener("click", enablePush);
}

function urlB64ToBytes(s) {
  const padded = (s + "=".repeat((4 - s.length % 4) % 4))
    .replace(/-/g, "+").replace(/_/g, "/");
  return Uint8Array.from(atob(padded), c => c.charCodeAt(0));
}

async function enablePush() {
  const btn = $("#pushOn");
  if (btn) { btn.disabled = true; btn.textContent = "Waiting for permission…"; }
  notify.message = "";
  try {

    const granted = Notification.permission === "granted"
      || await Notification.requestPermission() === "granted";

    if (!granted) {

      notify.message = "Your browser blocked the request — no prompt was shown. "
        + "Click the bell or lock icon beside the address bar, allow notifications "
        + "for this site, then try again.";
      return;
    }

    const reg = await navigator.serviceWorker.register("/sw.js", { scope: "/" });
    await navigator.serviceWorker.ready;

    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlB64ToBytes(notify.key),
    });
    const j = sub.toJSON();
    await api("/push/subscriptions", {
      method: "POST",
      body: { endpoint: j.endpoint, p256dh: j.keys.p256dh, auth: j.keys.auth },
    });
    notify.sub = sub;
    toast("Notifications are on.");
  } catch (e) {
    notify.message = `Couldn't turn on notifications: ${e.message}`;
  } finally {

    renderPush();
  }
}

async function disablePush() {
  notify.message = "";
  try {
    if (notify.sub) {
      const endpoint = notify.sub.endpoint;
      await notify.sub.unsubscribe();
      await api(`/push/subscriptions?endpoint=${encodeURIComponent(endpoint)}`,
                { method: "DELETE" });
      notify.sub = null;
    }
    toast("Notifications are off.");
  } catch (e) {
    toast(`Couldn't turn them off: ${e.message}`);
  }
  renderPush();
}

async function previewPush() {
  const btn = $("#pushPrev");
  if (btn) { btn.disabled = true; btn.textContent = "Sending…"; }
  notify.message = "";
  try {
    const r = await api("/push/test", { method: "POST" });
    if (r.sent) {
      toast("Sent — check your notifications.");
    } else {

      notify.message = r.reason
        ? `The push service refused it: ${r.reason}`
        : "The push service rejected it. Turning notifications off and on again "
          + "re-registers this browser and usually fixes it.";
    }
  } catch (e) {
    notify.message = `Couldn't send: ${e.message}`;
  } finally {
    renderPush();
  }
}

const CAT_ORDER = [
  /breakfast entree/i, /breakfast meat/i, /breakfast bakery/i, /breakfast/i,
  /soup|chili|stew/i, /entree/i, /grill/i, /halal/i, /veg,\s*vegan|plant protein/i,
  /pizza|pasta/i, /starch|potato/i, /vegetable/i, /salad bar|salad/i,
  /gut health|deli|sandwich/i, /bread|bakery/i, /dessert/i, /beverage/i,
];
function catRank(name) {
  if (/bag\s*meal|bagged|fly.?by/i.test(name)) return 900;
  const i = CAT_ORDER.findIndex(re => re.test(name));
  return i === -1 ? 500 : i;
}

function chips(it) {
  const out = [];

  if (it.availability && it.availability.status === "low")
    out.push(`<span class="chip plain">Running low</span>`);
  if (it.vegan) out.push(`<span class="chip vgn">Vegan</span>`);
  else if (it.vegetarian) out.push(`<span class="chip veg">Vegetarian</span>`);
  if (it.spice && it.spice.level > 0) {
    out.push(`<span class="chip spice">${"&#9679;".repeat(it.spice.level)} Spiced${
      it.spice.curated ? "" : " <em>(inferred)</em>"}</span>`);
  }
  if (it.allergens && it.allergens.length)
    out.push(`<span class="chip alg">${esc(it.allergens.join(" · "))}</span>`);

  if (it.consumption && it.consumption.band === "rarely_wasted")
    out.push(`<span class="band star"><span aria-hidden="true">&#9733;</span> Rarely wasted</span>`);
  if (it.rating)
    out.push(`<span class="stars">★ ${it.rating.average} <span style="color:var(--ink-mute)">(${it.rating.count})</span></span>`);
  return out.join(" ");
}

function dishRow(it) {
  const meta = [it.calories ? `${it.calories} cal` : null, it.serving_size]
    .filter(Boolean).join(" &nbsp;·&nbsp; ");
  const avail = it.availability;
  const isOut = !!avail && avail.status === "out";

  const availLine = avail
    ? (avail.note || (isOut ? "Ran out for this service." : null))
    : null;
  return `<button class="dish${isOut ? " dish--out" : ""}" data-id="${it.id}">
    <div class="dish__main">
      <div class="dish__name">${esc(it.name)}</div>
      ${meta ? `<div class="dish__meta">${meta}</div>` : ""}
      ${availLine ? `<div class="dish__avail">${esc(availLine)}</div>` : ""}
      <div class="chips">${chips(it)}</div>
    </div>
    <div class="dish__go">&rsaquo;</div>
  </button>`;
}

async function loadMenu() {
  const box = $("#menu");
  box.innerHTML = `<div class="state"><div class="spin"></div>Loading menu…</div>`;
  $("#hint").style.display = "none";

  let data;
  try {
    data = await api(`/menu?date=${state.date}&location=${state.hall}&meal=${state.meal}`);
  } catch (e) {
    box.innerHTML = `<div class="state">Couldn't load the menu.<br><small>${esc(e.message)}</small></div>`;
    state.mealOptions = null;
    updateContextNotices(null);
    return;
  }

  const options = data.meal_options || [];
  if (!state.correctingMeal && options.length &&
      !options.some(o => o.meal === state.meal)) {
    state.correctingMeal = true;
    state.meal = (options.find(o => o.served) || options[0]).meal;
    syncControls();
    writeUrl({ replace: true });
    try { await refreshContext(); } finally { state.correctingMeal = false; }
    return;
  }
  renderMealSeg(options);

  state.mealOptions = options;
  if (state.pane === "forecast") {
    renderMealSeg(options, $("#fcSeg"));

    if (state.forecast && (state.forecast.meal !== state.meal
        || state.forecast.location !== state.hall
        || state.forecast.date !== state.date)) loadForecastPane();
  }

  state.items.clear();
  data.categories.forEach(c => c.items.forEach(i => state.items.set(i.id, i)));

  $("#count").textContent = data.item_count ? `${data.item_count} items` : "";

  const f = data.freshness;
  const stale = $("#staleNote");
  if (f && f.warn) {
    stale.className = "note";
    stale.innerHTML = `<b>Showing a stored menu.</b> ${esc(f.message)}`;
    stale.style.display = "";
  } else {
    stale.style.display = "none";
  }

  if (!data.categories.length) {
    const hall = $("#hall").selectedOptions[0]?.textContent || "this hall";
    const names = new Map((data.meal_options || []).map(o => [o.meal, o.name]));
    const served = (data.meals_served || []).map(m => names.get(m) || MEALS[m]).join(", ");
    const thisMeal = data.meal_name || MEALS[state.meal];
    box.innerHTML = `<div class="state">No ${esc(thisMeal.toLowerCase())} served at
      <b>${esc(hall)}</b> on ${esc(state.date)}.${
        served ? `<br><small>Serving ${esc(served.toLowerCase())} today.</small>` : ""}</div>`;
    updateContextNotices(data);
    return;
  }

  const memKey = `menuOpen:${state.hall}:${state.meal}:${state.date}`;
  const stored = sessionStorage.getItem(memKey);
  let openSet;
  try { openSet = stored === null ? null : new Set(JSON.parse(stored)); }
  catch { openSet = null; }

  const cats = [...data.categories]
    .sort((a, b) => catRank(a.name) - catRank(b.name) || a.name.localeCompare(b.name));

  if (openSet === null) {
    openSet = new Set();
    let showing = 0;
    for (const c of cats) {
      if (showing >= 10) break;
      openSet.add(c.name.trim().toLowerCase());
      showing += c.items.length;
    }
  }

  box.innerHTML = `<div class="cat-tools">
      <button class="textlink" data-catall>${
        cats.every(c => openSet.has(c.name.trim().toLowerCase()))
          ? "Close all" : "Open all"}</button></div>`
    + cats.map(c => {
      const key = c.name.trim().toLowerCase();
      const isOpen = openSet.has(key);
      const out = c.items.filter(i => i.availability?.status === "out").length;
      const low = c.items.filter(i => i.availability?.status === "low").length;
      return `<div class="cat${isOpen ? " open" : ""}" data-cat="${esc(key)}">
        <div class="cat__hd">
          <button class="cat__toggle" aria-expanded="${isOpen}">
            <span class="cat__chev" aria-hidden="true"></span>
            <h3>${esc(c.name)}</h3>
            <span class="cat__meta">${
              out ? `<span class="cat__out">${out} out</span>` : ""}${
              low ? `<span class="cat__low">${low} low</span>` : ""}
              <span class="cat__n">${c.items.length}</span></span>
          </button>
          ${key === "from the grill"
            ? `<button class="textlink" data-grillgo>(order from the grill)</button>`
            : ""}
        </div>
        <div class="cat__bd"${isOpen ? "" : " hidden"}>${
          c.items.map(dishRow).join("")}</div>
      </div>`;
    }).join("");

  const persistOpen = () => sessionStorage.setItem(memKey, JSON.stringify(
    [...box.querySelectorAll(".cat.open")].map(d => d.dataset.cat)));

  box.querySelectorAll(".cat__hd").forEach(hd =>
    hd.addEventListener("click", e => {
      if (e.target.closest("[data-grillgo]")) return;
      const cat = hd.closest(".cat");
      const t = hd.querySelector(".cat__toggle");
      const nowOpen = !cat.classList.contains("open");
      cat.classList.toggle("open", nowOpen);
      t.setAttribute("aria-expanded", String(nowOpen));
      cat.querySelector(".cat__bd").hidden = !nowOpen;
      persistOpen();
    }));
  box.querySelector("[data-catall]")?.addEventListener("click", e => {
    const openAll = e.target.textContent === "Open all";
    box.querySelectorAll(".cat").forEach(cat => {
      cat.classList.toggle("open", openAll);
      cat.querySelector(".cat__toggle")
         .setAttribute("aria-expanded", String(openAll));
      cat.querySelector(".cat__bd").hidden = !openAll;
    });
    e.target.textContent = openAll ? "Close all" : "Open all";
    persistOpen();
  });

  box.querySelector("[data-grillgo]")
    ?.addEventListener("click", () => showPane("grill"));
  box.querySelectorAll(".dish").forEach(b =>
    b.addEventListener("click", () => openSheet(Number(b.dataset.id))));

  updateContextNotices(data);

  if (state.pane === "availability") renderBoard();

  if (state.pane === "survey" && state.user?.affiliation === "staff")
    renderQueuePickRows();
}

function updateContextNotices(data) {
  const known = data !== null;
  const hasMenu = !!(data && data.categories && data.categories.length);
  const mealsServed = (data && data.meals_served) || [];
  if (data && data.meal_name) { state.mealName = data.meal_name; updateRsvpCopy(); }
  const month = new Date(state.date + "T12:00:00").getMonth();
  const isSummer = month >= 5 && month <= 7;

  const closedAllDay = known && mealsServed.length === 0;

  const notice = data && data.season_notice;
  const jump = `<a href="#" id="jump">jump to 15 Apr 2026</a>`;
  let hint = null;
  if (server.demo && notice === "hall_closed" && isSummer) {
    hint = `<b>Summer service.</b> Eat at Adams House, Annenberg or a cafe. `
         + `Or ${jump} to see all 13 halls' menus.`;
  } else if (server.demo && notice === "no_menus") {

    hint = `<b>No menus for this date.</b> Nothing is published for any hall; `
         + `most close between terms. You can ${jump} to see all 13 halls' menus.`;
  }
  if (hint) {
    $("#hint").innerHTML = hint;
    $("#hint").style.display = "";
    $("#jump").addEventListener("click", e => {
      e.preventDefault();
      state.date = "2026-04-15";
      syncControls(); writeUrl(); refreshContext();
    });
  } else {
    $("#hint").style.display = "none";
  }

  state.rsvpVisible = hasMenu && (!data || data.takes_attendance !== false);
  state.declarationOpen = !data || data.declaration_open !== false;
  state.serviceStatus = (data && data.service_status) || null;
  renderRsvp();
  state.hallOpenToday = known ? mealsServed.length > 0 : null;
  renderLineCard();
}
const GRILL_POLL_MS = 5000;
const GRILL_WHY = {
  not_serving: "This hall isn't serving right now.",
  closed: "The grill is closed to app orders right now.",
  paused: "The grill has paused online orders — you can order in person "
          + "at the counter.",
  station_offline: "The grill isn't taking online orders right now.",
  walk_up: null,
  wait_cap: "The grill is backed up with online orders — "
            + "come down and order at the counter.",
};

const LINE_POLL_MS = 60000;

function startLinePoll() {
  if (!state.lineTimer)
    state.lineTimer = setInterval(loadLine, LINE_POLL_MS);
}

function stopLinePoll() {
  if (state.lineTimer) { clearInterval(state.lineTimer); state.lineTimer = null; }
}

function startGrillPoll() {
  loadGrill();
  if (!state.grillTimer) state.grillTimer = setInterval(loadGrill, GRILL_POLL_MS);
}
function stopGrillPoll() {
  if (state.grillTimer) { clearInterval(state.grillTimer); state.grillTimer = null; }
}

async function serveryOptions() {
  if (!state.serveryHalls) {
    try {
      const h = await api("/houses");
      state.serveryHalls = new Set(h.houses.map(x => x.location_id));
      state.serveryHalls.add(30);
    } catch { return $("#hall").innerHTML; }
  }
  return [...$("#hall").options]
    .filter(o => state.serveryHalls.has(Number(o.value)))
    .map(o => `<option value="${o.value}">${esc(o.textContent)}</option>`)
    .join("");
}

function snapToServery(sel) {
  if (state.serveryHalls && !state.serveryHalls.has(state.hall)
      && sel.options.length) {
    state.hall = Number(sel.options[0].value);
    writeUrl();
    refreshContext();
  }
  sel.value = String(state.hall);
}

async function loadGrill() {

  const sel = $("#grillHall");
  if (!sel.options.length) {
    sel.innerHTML = await serveryOptions();
    sel.addEventListener("change", () => {
      state.hall = Number(sel.value);
      syncControls();
      writeUrl();
      refreshContext();
      state.grillRaw = null;
      loadGrill();
    });
  }
  snapToServery(sel);
  let g;
  try { g = await api(`/grill?location=${state.hall}`); }
  catch (e) {
    $("#grillBody").innerHTML = `<div class="state">${esc(e.message)}</div>`;
    return;
  }
  const raw = JSON.stringify(g);
  if (raw === state.grillRaw) return;
  state.grillRaw = raw;
  state.grill = g;

  const ost = g.your_order?.status || null;
  if (window.veritasteShell && ost && state.grillOrderStatus
      && ost !== state.grillOrderStatus
      && (ost === "cooking" || ost === "ready")) {
    window.veritasteShell.notify("Veritaste", g.your_order.message);
  }
  state.grillOrderStatus = ost;
  renderGrill();
}

function hallNameById(id) {
  const opt = [...$("#hall").options].find(o => Number(o.value) === id);
  return opt ? opt.textContent : `hall ${id}`;
}

function renderGrill() {
  const g = state.grill;
  const box = $("#grillBody");
  if (!state.grillSel) state.grillSel = { main: null, conds: new Set() };

  if (g.your_order) {
    const o = g.your_order;
    const steps = ["placed", "seen", "cooking", "ready"];
    const at = steps.indexOf(o.status);
    const elsewhere = o.location_id !== state.hall
      ? ` at ${esc(hallNameById(o.location_id))}` : "";
    box.innerHTML = `
      <div class="gorder">
        <div class="gorder__msg">${esc(o.message)}</div>
        <div class="gorder__items">${esc(o.main.name)}${
          o.condiments.length
            ? " · " + esc(o.condiments.map(c => c.name).join(", ")) : ""
        }${elsewhere}</div>
        <div class="gsteps">${steps.map((s, i) => `
          <span class="gstep${i < at ? " on" : ""}${i === at ? " now" : ""}"
            >${s === "seen" ? "queued" : s}</span>`).join("")}</div>
        ${o.cancellable
          ? `<button class="btn ghost" id="grillCancel">Cancel this order</button>`
          : ""}
      </div>`;
    $("#grillCancel")?.addEventListener("click", async () => {
      try { await api(`/grill/orders/${o.id}`, { method: "DELETE" }); }
      catch (e) { grillMsg(e.message); }
      state.grillSel = null;
      state.grillRaw = null;
      loadGrill();
    });
    return;
  }

  if (g.dining_allowed === false) {
    box.innerHTML = `
      <p class="grill-status">Interhouse rules don't allow you to dine here
        for this meal, so the grill here can't take your order${
        g.dining_reason ? ` — ${esc(g.dining_reason)}` : "."}</p>
      <p class="grill-status"><button class="textlink" id="grillWhere">See
        where you can eat</button></p>`;
    $("#grillWhere").addEventListener("click", () => showPane("where"));
    return;
  }

  if (!g.accepting_now) {
    const reasons = g.why_not || [];

    if (reasons.includes("not_serving")) {
      const mins = g.next_service_min;
      box.innerHTML = `<p class="grill-status">${esc(GRILL_WHY.not_serving)}</p>`
        + (mins != null ? `
          <p class="grill-status">Mealtime starts in about ${mins}
            minute${mins === 1 ? "" : "s"}.</p>` : "");
      return;
    }

    const why = reasons
      .map(r => r === "walk_up" ? g.walk_up_message : GRILL_WHY[r])
      .filter(Boolean);

    const counter = reasons.includes("station_offline")
        && !reasons.includes("paused") && !reasons.includes("wait_cap")
      ? `<p class="grill-status">If the grill is open, you may place an
           order at the counter instead.</p>` : "";
    box.innerHTML = `<p class="grill-status">${esc(why[0] || "The grill is not taking app orders.")}</p>`
      + counter
      + (g.station_online && g.open_app_orders
         ? `<p class="grill-status">${g.open_app_orders} app order${
             g.open_app_orders === 1 ? "" : "s"} on the grill right now.</p>` : "");
    return;
  }

  if (!g.mains.length) {
    box.innerHTML = `<p class="grill-status">The grill isn't open during
      this meal.</p>`;
    return;
  }

  if (!state.user) {
    box.innerHTML = `
      <p class="grill-status">The grill is taking app orders${
        g.state === "backed_up"
          ? ` — backed up, about ${g.estimated_wait_min} min before cooking starts`
          : ""}.</p>
      <p class="grill-status">Sign in to place one.</p>
      <button class="btn ghost" id="grillSignin">Sign in</button>`;
    $("#grillSignin").addEventListener("click", signIn);
    return;
  }

  const opt = (i, kind) => {
    const isMain = kind === "main";
    const checked = isMain
      ? state.grillSel.main === i.id
      : state.grillSel.conds.has(i.id);
    const meta = isMain
      ? [i.calories ? `${i.calories} cal` : null, i.serving_size]
          .filter(Boolean).join(" · ")
      : (i.calories ? `${i.calories} cal` : "");
    return `<div class="gopt${i.out ? " out" : ""}${isMain ? " gopt--main" : ""}">
      <label class="gopt__pick">
        <input type="${isMain ? "radio" : "checkbox"}"
               name="${isMain ? "gmain" : "gcond"}"
               value="${i.id}" ${checked ? "checked" : ""} ${i.out ? "disabled" : ""}>
        <span class="gopt__body">
          <span class="gopt__name">${esc(i.name)}${
            i.out ? ` <span class="gopt__ran">ran out</span>` : ""}</span>
          ${meta ? `<span class="gopt__meta">${meta}</span>` : ""}
          ${isMain ? `<span class="chips">${chips(i)}</span>` : ""}
        </span>
      </label>
      ${isMain ? `<button type="button" class="textlink"
        data-sheet="${i.id}">(details)</button>` : ""}
    </div>`;
  };
  const selMain = g.mains.find(m => m.id === state.grillSel.main);
  const conds = selMain
    ? g.condiments.filter(c => (selMain.condiments || []).includes(c.id))
    : g.condiments;

  box.innerHTML = `
    <p class="grill-status">Taking app orders now${g.estimated_wait_min
      ? ` — about ${g.estimated_wait_min} min` : ""}.</p>
    <h3 class="gsec">Choose one</h3>
    ${g.mains.map(i => opt(i, "main")).join("")}
    ${conds.length ? `<h3 class="gsec">With</h3>
      ${conds.map(i => opt(i, "cond")).join("")}`
      : (selMain ? `<p class="grill-status" style="margin-top:14px">This one
          comes as it is.</p>` : "")}
    <p class="grill-msg" id="grillMsg" hidden></p>
    <button class="btn" id="grillPlace" style="margin-top:14px"
            ${state.grillSel.main ? "" : "disabled"}>Place order</button>`;

  box.querySelectorAll("[data-sheet]").forEach(b =>
    b.addEventListener("click", () => openSheet(Number(b.dataset.sheet))));
  box.querySelectorAll("input[name=gmain]").forEach(r =>
    r.addEventListener("change", () => {
      state.grillSel.main = Number(r.value);

      state.grillSel.conds = new Set(
        (g.mains.find(m => m.id === state.grillSel.main)
          ?.default_condiments) || []);
      renderGrill();
    }));
  box.querySelectorAll("input[name=gcond]").forEach(c =>
    c.addEventListener("change", () => {
      const id = Number(c.value);
      if (c.checked) state.grillSel.conds.add(id);
      else state.grillSel.conds.delete(id);
    }));
  $("#grillPlace").addEventListener("click", async () => {
    try {
      await api("/grill/orders", { method: "POST", body: {
        location_id: state.hall,
        main_id: state.grillSel.main,
        condiments: [...state.grillSel.conds],
      } });
      state.grillSel = null;
      state.grillRaw = null;
      loadGrill();
    } catch (e) { grillMsg(e.message); }
  });
}

function grillMsg(text) {
  const el = $("#grillMsg");
  if (!el) return;
  el.hidden = !text;
  el.textContent = text || "";
}

const STATION_POLL_MS = 3000;

function startStationPoll() {
  loadStation();
  if (!state.stationTimer)
    state.stationTimer = setInterval(loadStation, STATION_POLL_MS);
}

function stopStationPoll() {
  if (state.stationTimer) {
    clearInterval(state.stationTimer);
    state.stationTimer = null;
  }
}

async function loadStation() {
  if (!state.user || state.user.affiliation !== "staff") return;

  const sel = $("#stationHall");
  if (!sel.options.length) {
    sel.innerHTML = await serveryOptions();
    sel.addEventListener("change", () => {
      state.hall = Number(sel.value);
      syncControls();
      writeUrl();
      refreshContext();
      state.station = null;
      loadStation();
    });
  }
  snapToServery(sel);

  if (!state.user.kitchen) {
    stopStationPoll();
    $("#stationBody").innerHTML = `
      <div class="board__lockednote">
        <p>The station screen polls the kitchen — that poll is what tells
          students the grill can see their orders, so running it needs the
          kitchen unlock.</p>
        <p><a class="textlink" href="/staffunlock?then=${
          encodeURIComponent("/?pane=station")}">Unlock kitchen actions</a>
          to run the station.</p>
      </div>`;
    return;
  }

  let st;
  try { st = await api(`/grill/station?location=${state.hall}`); }
  catch (e) {
    $("#stationBody").innerHTML = `<div class="state">${esc(e.message)}</div>`;
    return;
  }
  state.station = st;
  renderStation();
}

const _STATION_ACT = {
  seen: ["cooking", "Start cooking"],
  cooking: ["ready", "Ready"],
  ready: ["collected", "Collected"],
};

function renderStation() {
  const st = state.station;
  const box = $("#stationBody");

  const age = placedAt => {
    const s = Math.max(0, Math.floor((Date.now() - Date.parse(placedAt + "Z")) / 1000));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  };

  box.innerHTML = `
    <div class="st-lever" role="group" aria-label="Grill state">
      ${["accepting", "paused", "closed"].map(s => `
        <button class="st-state${st.state === s ? " on" : ""}" data-state="${s}">
          ${s[0].toUpperCase() + s.slice(1)}
        </button>`).join("")}
    </div>
    <p class="st-head">${st.open_app_orders
      ? `${st.open_app_orders} online order${st.open_app_orders === 1 ? "" : "s"}`
      : ""}
      <span class="st-cap">max queued orders: <b>${st.app_cap}</b>
        <button class="btn ghost small" data-cap="${st.app_cap - 1}"
                ${st.app_cap <= 1 ? "disabled" : ""}>−</button>
        <button class="btn ghost small" data-cap="${st.app_cap + 1}"
                ${st.app_cap >= 10 ? "disabled" : ""}>+</button></span></p>
    ${st.orders.length ? st.orders.map(o => {
      const act = _STATION_ACT[o.status];
      return `<div class="st-order">
        <div class="st-order__main">
          <span class="chip ${o.status === "ready" ? "veg" : "plain"}">${o.status}</span>
          <span class="sname">${esc(o.main)}</span>
          <span class="st-who">${esc(o.who || "")}</span>
          <span class="st-age">${age(o.placed_at)}</span>
        </div>
        ${o.condiments.length
          ? `<div class="st-conds">${esc(o.condiments.join(" · "))}</div>` : ""}
        <div class="st-actions">
          ${act ? `<button class="btn small" data-adv="${o.id}"
                     data-to="${act[0]}">${act[1]}</button>` : ""}
          <button class="textlink" data-scancel="${o.id}">(cancel order)</button>
        </div>
      </div>`;
    }).join("")
    : `<p class="board__empty">No online orders.</p>`}`;

  const act = fn => async e => {
    try { await fn(e); } catch (err) {  }
    loadStation();
  };
  box.querySelectorAll("[data-state]").forEach(b =>
    b.addEventListener("click", act(() => {

      if (b.dataset.state === "closed"
          && (state.station?.orders?.length ?? 0) > 0
          && !window.confirm("Closing cancels ALL outstanding online orders "
              + "and notifies those students that the grill had to close.\n\n"
              + "Close the grill?")) return Promise.resolve();
      return api("/grill/station", { method: "POST",
        body: { location_id: state.hall, state: b.dataset.state } });
    })));
  box.querySelectorAll("[data-cap]").forEach(b =>
    b.addEventListener("click", act(() => api("/grill/station", {
      method: "POST",
      body: { location_id: state.hall, app_cap: Number(b.dataset.cap) } }))));
  box.querySelectorAll("[data-adv]").forEach(b =>
    b.addEventListener("click", act(() => api(`/grill/orders/${b.dataset.adv}/advance`, {
      method: "POST", body: { to: b.dataset.to } }))));
  box.querySelectorAll("[data-scancel]").forEach(b =>
    b.addEventListener("click", act(() => api(`/grill/orders/${b.dataset.scancel}`, {
      method: "DELETE" }))));
}

const LINE_BANDS = { no_wait: "No wait", short: "Short", long: "Long" };

async function loadLinePane() {
  if (!state.user || state.user.affiliation !== "staff") return;

  const sel = $("#linrHall");
  if (!sel.options.length) {
    sel.innerHTML = await serveryOptions();
    sel.addEventListener("change", () => {
      state.hall = Number(sel.value);
      syncControls();
      writeUrl();
      refreshContext();
      loadLinePane();
    });
  }
  snapToServery(sel);
  try { state.lineReport = await api(`/line/${state.hall}`); }
  catch { state.lineReport = null; }
  renderLinePane();
  if (!state.linrWired) {
    state.linrWired = true;
    $("#linrSeg").querySelectorAll("button").forEach(b =>
      b.addEventListener("click", () => sendLineReport(b.dataset.band)));
  }
}

function renderLinePane() {
  const r = state.lineReport;
  const fresh = r && r.source === "staff" && r.band;

  const locked = !(state.user && state.user.kitchen);
  $("#linrLocked").hidden = !locked;
  $("#linrSeg").style.display = locked ? "none" : "";
  $("#linrState").style.display = locked ? "none" : "";
  $("#linrSeg").querySelectorAll("button").forEach(b =>
    b.setAttribute("aria-pressed", String(!!fresh && r.band === b.dataset.band)));
  clearInterval(state.linrTimerId);
  const el = $("#linrState");
  if (fresh) {
    el.innerHTML = `Expires in <b class="linr__timer" id="linrTimer"></b>
      <a href="#" id="linrClear">Clear now</a>`;
    $("#linrClear").addEventListener("click", e => {
      e.preventDefault(); sendLineReport(null);
    });

    linrTick();
    state.linrTimerId = setInterval(linrTick, 1000);
    if (state.linrPulse) {
      state.linrPulse = false;

      const timer = $("#linrTimer");
      timer.classList.add("linr__timer--reset");
      setTimeout(() => timer.classList.remove("linr__timer--reset"), 600);
    }
  } else {
    el.textContent = "No current report.";
  }
}
function linrTick() {
  const r = state.lineReport;
  const t = $("#linrTimer");
  if (!t || !r || !r.expires_at) { clearInterval(state.linrTimerId); return; }
  const left = new Date(String(r.expires_at).slice(0, 19) + "Z") - Date.now();
  if (left <= 0) {
    clearInterval(state.linrTimerId);
    loadLinePane();
    loadLine();
    return;
  }
  const m = Math.floor(left / 60000);
  const s = Math.floor((left % 60000) / 1000);
  t.textContent = `${m}:${String(s).padStart(2, "0")}`;
}

async function sendLineReport(band) {
  try {
    await api(`/line/${state.hall}/report`, { method: "POST", body: { band } });
    $("#linrMsg").hidden = true;

    state.linrPulse = true;
  } catch (e) {
    $("#linrMsg").hidden = false;
    $("#linrMsg").innerHTML = e.status === 403 && !state.user?.kitchen
      ? `Reporting needs the kitchen unlock —
         <a href="/staffunlock">enter the passcode</a> once on this browser.`
      : esc(e.message);
  }
  await loadLinePane();

  loadLine();
}

const FBX_MEALS = { all: "All", 0: "Breakfast", 1: "Lunch", 2: "Dinner" };

function fbxTime(iso) {

  const d = new Date(String(iso).slice(0, 19) + "Z");
  return Number.isNaN(d.getTime()) ? "" :
    d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function relAgo(iso) {
  const ms = Date.now() - new Date(String(iso).slice(0, 19) + "Z");
  const m = Math.round(ms / 60000);
  if (!Number.isFinite(m) || m <= 0) return "just now";
  return m === 1 ? "1 minute ago" : `${m} minutes ago`;
}

async function loadFeedbackPane() {
  if (!state.user || state.user.affiliation !== "staff") return;
  const sel = $("#fbxHall");
  if (!state.fbxWired) {
    state.fbxWired = true;
    state.fbxMeal = "all";
    $("#fbxRefresh").addEventListener("click", loadFeedbackPane);

    sel.innerHTML = $("#hall").innerHTML;
    sel.addEventListener("change", () => {
      state.hall = Number(sel.value);
      syncControls();
      writeUrl();
      refreshContext();
      loadFeedbackPane();
    });
  }
  sel.value = String(state.hall);
  try {
    const data = await api(`/feedback?location=${state.hall}`);
    state.fbxNotes = data.notes;
    $("#fbxMsg").hidden = true;
  } catch (e) {
    state.fbxNotes = null;
    $("#fbxMsg").hidden = false;
    $("#fbxMsg").innerHTML = e.status === 403 && !state.user.kitchen
      ? `Reading students' notes needs the kitchen unlock —
         <a href="/staffunlock">enter the passcode</a> once on this browser.`
      : esc(e.message);
  }
  renderFbx();
}

function renderFbx() {
  const chips = $("#fbxChips");
  chips.innerHTML = Object.entries(FBX_MEALS).map(([k, label]) =>
    `<button data-k="${k}"
      aria-pressed="${String(k) === String(state.fbxMeal)}">${label}</button>`).join("");
  chips.querySelectorAll("button").forEach(b =>
    b.addEventListener("click", () => { state.fbxMeal = b.dataset.k; renderFbx(); }));

  const box = $("#fbxList");
  const notes = state.fbxNotes;
  if (!notes) { box.innerHTML = ""; return; }
  const shown = notes.filter(n =>
    state.fbxMeal === "all" || n.meal === Number(state.fbxMeal));
  if (!shown.length) {
    box.innerHTML = `<p class="fbx__empty">No feedback.</p>`;
    return;
  }

  const groups = new Map();
  for (const n of shown) {
    const key = n.name || `Dish ${n.recipe_id}`;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(n);
  }
  box.innerHTML = [...groups.entries()].map(([dish, list]) => `
    <div class="fbx__group">
      <h3 class="fbx__dish">${esc(dish)}</h3>
      ${list.map(fbxRow).join("")}
    </div>`).join("");
  box.querySelectorAll("[data-fbx-block]").forEach(b =>
    b.addEventListener("click", e => {
      e.preventDefault(); fbxConfirm(Number(b.dataset.fbxBlock));
    }));
  box.querySelectorAll("[data-fbx-unblock]").forEach(b =>
    b.addEventListener("click", e => {
      e.preventDefault();
      fbxAct(() => api(`/feedback/blocks?note_id=${b.dataset.fbxUnblock}`,
                       { method: "DELETE" }));
    }));
}

function fbxRow(n) {
  const meta = [
    fbxTime(n.updated_at),
    FBX_MEALS[n.meal] ?? `Meal ${n.meal}`,
    n.signed_name ? esc(n.signed_name) : "Anonymous",
    n.rating_score != null ? `rated ${n.rating_score} of 5` : null,
    n.edited ? "(edited)" : null,
  ].filter(Boolean).join(" · ");
  return `<div class="fbx__note">
    <p class="fbx__text">${esc(n.text)}</p>
    <div class="fbx__meta">${meta}
      ${n.blocked
        ? `<span class="fbx__blocked">blocked</span>
           <a href="#" data-fbx-unblock="${n.id}">Unblock</a>`
        : `<a href="#" data-fbx-block="${n.id}">Block author</a>`}
    </div>
    <div class="fbx__confirm" id="fbxConfirm-${n.id}" hidden></div>
  </div>`;
}

function fbxConfirm(noteId) {
  const el = $(`#fbxConfirm-${noteId}`);
  if (!el) return;
  const link = el.parentElement.querySelector("[data-fbx-block]");
  if (link) link.hidden = true;
  el.hidden = false;
  el.innerHTML = `Block this author from feedback?
    <button class="btn" data-c="cancel">Cancel</button>
    <button class="btn" data-c="block">Block</button>`;
  el.querySelector('[data-c="cancel"]').addEventListener("click", e => {
    e.preventDefault(); el.hidden = true; el.innerHTML = "";
    if (link) link.hidden = false;
  });
  el.querySelector('[data-c="block"]').addEventListener("click", e => {
    e.preventDefault();
    fbxAct(() => api("/feedback/blocks",
                     { method: "POST", body: { note_id: noteId } }));
  });
}

async function fbxAct(fn) {
  try { await fn(); $("#fbxMsg").hidden = true; }
  catch (e) { $("#fbxMsg").hidden = false; $("#fbxMsg").textContent = e.message; }
  await loadFeedbackPane();
}

async function loadBoard() {
  if (!state.user || state.user.affiliation !== "staff") return;
  if (!state.boardWired) {
    state.boardWired = true;
    $("#boardFilter").addEventListener("input", renderBoardItems);
    $("#boardLock").addEventListener("click", async () => {
      try { await api("/auth/kitchen", { method: "DELETE" }); } catch {  }
      await loadMe();
      loadBoard();
    });
  }
  const hallName = $("#hall").selectedOptions[0]?.textContent || `hall ${state.hall}`;
  $("#boardContext").textContent = `${hallName} · ${MEALS[state.meal]}`;
  $("#boardTv").href = `/display?hall=${state.hall}`;
  $("#boardLock").hidden = !state.user?.kitchen;
  try {
    const data = await api(`/availability?location=${state.hall}`);
    state.boardMarks = new Map(data.marks.map(m => [m.recipe_id, m]));
    boardMsg(null);
  } catch (e) {
    state.boardMarks = new Map();
    boardMsg(e.message);
  }
  renderBoard();
}

function boardMsg(text) {
  const el = $("#boardMsg");
  el.hidden = !text;
  el.textContent = text || "";
}

async function boardAct(fn) {
  try { await fn(); boardMsg(null); }
  catch (e) { boardMsg(e.message); }
  await loadBoard();

  refreshContext();
}

function renderBoard() {
  const marks = [...(state.boardMarks?.values() ?? [])];
  const mbox = $("#boardMarked");

  const locked = !state.user?.kitchen;
  const dis = locked ? "disabled" : "";
  const banner = locked
    ? `<p class="board__lockednote">Viewing read-only — <a class="textlink"
         href="/staffunlock?then=${encodeURIComponent("/?pane=availability")}"
         >unlock kitchen actions</a> to mark and restock. One passcode entry is
         good for 24 hours on this browser.</p>`
    : "";
  mbox.innerHTML = banner + (marks.length ? marks.map(m => {
    const name = m.name || state.items.get(m.recipe_id)?.name || `Dish ${m.recipe_id}`;
    return `<div class="bmark" data-id="${m.recipe_id}">
      <div class="bmark__row">
        <span class="chip ${m.status === "out" ? "plain" : "alg"}">${
          m.status === "out" ? "Out" : "Low"}</span>
        <span class="bmark__name">${esc(name)}</span>
        <button class="btn ghost small" ${dis} data-restock="${m.recipe_id}">Restock</button>
      </div>
      <div class="bmark__noterow">
        ${m.note ? `<span class="bmark__note">${esc(m.note)}</span>` : ""}
        <button class="textlink" ${dis} data-note="${m.recipe_id}">(${
          m.note ? "edit note" : "add note"})</button>
      </div>
      <div class="bmark__noteui" hidden>
        <input type="text" maxlength="140" value="${esc(m.note || "")}"
               placeholder="Anything students should know? (optional)"
               aria-label="Note for students">
        <button class="btn ghost small" data-save="${m.recipe_id}">Save note</button>
        <div class="bmark__links">
          <button class="textlink" data-cancel="${m.recipe_id}">Cancel</button>
          ${m.note ? `<button class="textlink" data-unnote="${m.recipe_id}">Remove note</button>` : ""}
        </div>
      </div>
    </div>`;
  }).join("") : `<p class="board__empty">Nothing is marked — the line is fully stocked.</p>`);

  mbox.querySelectorAll("[data-note]").forEach(b =>
    b.addEventListener("click", () => {
      const card = mbox.querySelector(`.bmark[data-id="${b.dataset.note}"]`);
      card.querySelector(".bmark__noteui").hidden = false;
      card.querySelector(".bmark__noterow").hidden = true;
      card.querySelector(".bmark__noteui input").focus();
    }));
  mbox.querySelectorAll("[data-save]").forEach(b =>
    b.addEventListener("click", () => {
      const id = Number(b.dataset.save);
      const mark = state.boardMarks.get(id);
      const note = mbox.querySelector(
        `.bmark[data-id="${id}"] .bmark__noteui input`).value.trim();
      boardAct(() => api("/availability", { method: "POST", body: {
        location_id: state.hall, recipe_id: id, status: mark.status,
        note: note || null } }));
    }));
  mbox.querySelectorAll("[data-cancel]").forEach(b =>
    b.addEventListener("click", () => {
      const card = mbox.querySelector(`.bmark[data-id="${b.dataset.cancel}"]`);
      const ui = card.querySelector(".bmark__noteui");

      ui.querySelector("input").value =
        state.boardMarks.get(Number(b.dataset.cancel))?.note || "";
      ui.hidden = true;
      card.querySelector(".bmark__noterow").hidden = false;
    }));
  mbox.querySelectorAll("[data-unnote]").forEach(b =>
    b.addEventListener("click", () => {
      const id = Number(b.dataset.unnote);
      const mark = state.boardMarks.get(id);
      boardAct(() => api("/availability", { method: "POST", body: {
        location_id: state.hall, recipe_id: id, status: mark.status,
        note: null } }));
    }));
  mbox.querySelectorAll("[data-restock]").forEach(b =>
    b.addEventListener("click", () => boardAct(() =>
      api(`/availability?location=${state.hall}&recipe=${b.dataset.restock}`,
          { method: "DELETE" }))));
  renderBoardItems();
}

function renderBoardItems() {
  const box = $("#boardItems");
  const q = ($("#boardFilter").value || "").trim().toLowerCase();
  const all = [...state.items.values()]
    .filter(i => !state.boardMarks?.has(i.id))
    .filter(i => !q || (i.name || "").toLowerCase().includes(q))
    .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  const shown = all.slice(0, 30);

  if (!shown.length) {
    box.innerHTML = `<p class="board__empty">${state.items.size
      ? "No dish matches."
      : "No menu is loaded — open the Menu pane and pick the hall and meal first."}</p>`;
    return;
  }
  const dis = state.user?.kitchen ? "" : "disabled";
  box.innerHTML = shown.map(i => `
    <div class="brow">
      <span class="brow__name">${esc(i.name)}</span>
      <button class="btn ghost small low" ${dis} data-low="${i.id}">Mark as low</button>
      <button class="btn ghost small" ${dis} data-out="${i.id}">Mark as out</button>
    </div>`).join("") + (all.length > shown.length
      ? `<p class="board__more">…and ${all.length - shown.length} more — type to find a dish.</p>`
      : "");

  box.querySelectorAll("[data-low]").forEach(b =>
    b.addEventListener("click", () => boardAct(() =>
      api("/availability", { method: "POST", body: {
        location_id: state.hall, recipe_id: Number(b.dataset.low),
        status: "low" } }))));
  box.querySelectorAll("[data-out]").forEach(b =>
    b.addEventListener("click", () => boardAct(() =>
      api("/availability", { method: "POST", body: {
        location_id: state.hall, recipe_id: Number(b.dataset.out),
        status: "out" } }))));
}

async function forecastHallOptions() {
  await serveryOptions();
  if (!state.serveryHalls) return $("#hall").innerHTML;
  const ok = new Set(state.serveryHalls);
  ok.add(29);
  return [...$("#hall").options]
    .filter(o => ok.has(Number(o.value)))
    .map(o => `<option value="${o.value}">${esc(o.textContent)}</option>`)
    .join("");
}

async function loadForecastPane() {
  if (!state.user || state.user.affiliation !== "staff") return;
  const sel = $("#fcHall");
  if (!state.fcWired) {
    state.fcWired = true;
    sel.innerHTML = await forecastHallOptions();
    sel.addEventListener("change", () => {
      state.hall = Number(sel.value);
      syncControls();
      writeUrl();
      refreshContext();
      loadForecastPane();
    });
    $("#fcDate").addEventListener("change", e => {
      state.date = e.target.value;
      syncControls();
      writeUrl();
      refreshContext();
      loadForecastPane();
    });

    $("#fcSeg").addEventListener("click", e => {
      const b = e.target.closest("button[data-meal]");
      if (!b || Number(b.dataset.meal) === state.meal) return;
      state.meal = Number(b.dataset.meal);
      renderMealSeg(state.mealOptions, $("#fcSeg"));
      syncControls();
      writeUrl();
      refreshContext();
      loadForecastPane();
    });
    $("#fcRefresh").addEventListener("click", () => loadForecastPane());
  }

  if (state.serveryHalls && state.hall !== 29
      && !state.serveryHalls.has(state.hall) && sel.options.length) {
    state.hall = Number(sel.options[0].value);
    writeUrl();
    refreshContext();
  }
  sel.value = String(state.hall);
  $("#fcDate").value = state.date;
  renderMealSeg(state.mealOptions, $("#fcSeg"));

  let f;
  try {
    f = await api(`/forecast?location=${state.hall}&meal=${state.meal}&date=${state.date}`);
  } catch (e) {

    $("#fcGrid").hidden = true;
    const locked = e.status === 403 && !state.user.kitchen;
    $("#fcLocked").hidden = !locked;
    $("#fcMsg").hidden = locked;
    if (!locked) $("#fcMsg").textContent = e.message;
    return;
  }
  $("#fcLocked").hidden = true;
  $("#fcMsg").hidden = true;
  state.forecast = f;
  renderForecast();
}

function fcChip(p, id) {
  if (p === "live") return `<span class="sim rep">live</span>`;
  if (p === "simulated") return `<span class="sim">simulated</span>`;
  return `<span class="sim off">${id === "history" ? "no data yet" : "not connected"}</span>`;
}

function fcDishRow(r, extra) {
  return `<button class="fcrow" data-fc="${r.recipe_id}">
    <span class="fcrow__name">${esc(r.name || `Dish ${r.recipe_id}`)}</span>
    ${extra}
    <span class="dish__go">&rsaquo;</span>
  </button>`;
}

function fcStars(r) {
  return r.average != null
    ? `<span class="stars">★ ${r.average} <span style="color:var(--ink-mute)">(${r.count})</span></span>`
    : `<span class="fcrow__val" style="color:var(--ink-mute)">not yet rated</span>`;
}

function renderForecast() {
  const f = state.forecast;
  $("#fcGrid").hidden = false;

  const base = f.baseline;
  $("#fcAttend").innerHTML = `
    <div class="fcnums">
      <div class="fcnum"><b>${f.declared.coming}</b><span>declared coming</span></div>
      <div class="fcnum"><b>${f.declared.not_coming}</b><span>declared not coming</span></div>
    </div>
    <p class="fchint" style="margin:0 0 10px">Declarations are a floor, not a headcount.</p>
    <div class="stat"><span>Prior ${esc(base.weekday)}s here</span>
      <b>${base.days
        ? `avg ${Math.round(base.average)} declared &middot; ${base.days} date${base.days === 1 ? "" : "s"}`
        : "none recorded yet"}</b></div>
    <p class="fchint">No predicted total appears until a real forecast model runs.</p>`;

  $("#fcVars").innerHTML = `
    <div class="fvar fvar--hd" aria-hidden="true">
      <span>Input</span><span>Data</span><span>Impact</span></div>
    ${f.variables.map(v => `
      <div class="fvar">
        <span class="fvar__label">${esc(v.label)}</span>
        ${fcChip(v.provenance, v.id)}
        <b class="fvar__imp">${v.impact == null ? "&mdash;" : esc(String(v.impact))}</b>
        ${v.value ? `<span class="fvar__val">${esc(v.value)}</span>` : ""}
      </div>`).join("")}`;

  const top = f.rated.top || [];
  const topIds = new Set(top.map(r => r.recipe_id));
  const bottom = (f.rated.bottom || []).filter(r => !topIds.has(r.recipe_id));
  $("#fcRated").innerHTML = !top.length
    ? `<p class="board__empty">No ratings at this hall yet.</p>`
    : `<div class="sec" style="margin-top:0"><h4>Highest rated here</h4>
        ${top.map(r => fcDishRow(r, fcStars(r))).join("")}</div>
      <div class="sec"><h4>Lowest rated here</h4>
        ${bottom.length
          ? bottom.map(r => fcDishRow(r, fcStars(r))).join("")
          : `<p class="board__empty">Too few rated dishes to split highest from lowest.</p>`}</div>`;

  $("#fcWasted").innerHTML = (f.wasted || []).length
    ? f.wasted.map(w => fcDishRow(w,
        `<b class="fcrow__val">${Number(w.wasted_lb)} lb</b>`)).join("")
      + `<p class="fchint">A Winnow-shaped mock feed — a real bin feed would
          drop in unchanged.</p>`
    : `<p class="board__empty">No waste observations in the week before this date.</p>`;

  $("#fcNew").innerHTML = (f.new_items || []).length
    ? f.new_items.map(n => fcDishRow(n,
        `<span class="chip new">New</span>${fcStars(n)}`)).join("")
      + `<p class="fchint">First time on this hall's menu in the past six days.</p>`
    : `<p class="board__empty">Nothing new.</p>`;

  $("#fcGrid").querySelectorAll("[data-fc]").forEach(btn =>
    btn.addEventListener("click", () => openSheet(Number(btn.dataset.fc))));
}

const RQ_REASON = {
  staff:  ["The kitchen wants your take", "kitchen"],
  new:    ["New this week", "new"],
  recent: ["Recently served", "plain"],
};

async function loadQueuePane() {

  if (!state.user) {
    window.location.href = "/signin?then="
      + encodeURIComponent(location.pathname + location.search);
    return;
  }
  const sel = $("#rqHall");
  if (!state.rqWired) {
    state.rqWired = true;

    sel.innerHTML = $("#hall").innerHTML;
    sel.addEventListener("change", () => {
      state.hall = Number(sel.value);
      syncControls();
      writeUrl();
      refreshContext();
      loadQueuePane();
    });
  }
  sel.value = String(state.hall);
  try {
    state.rq = await api(`/rating-queue?location=${state.hall}`);
    $("#rqMsg").hidden = true;
  } catch (e) {
    state.rq = null;
    $("#rqMsg").hidden = false;
    $("#rqMsg").textContent = e.message;
    $("#rqList").innerHTML = "";
    return;
  }
  renderQueue();
}
function renderQueue() {
  const q = state.rq;
  const box = $("#rqList");
  const isStaff = state.user && state.user.affiliation === "staff";

  $("#rqTitle").textContent = isStaff ? "Create survey" : "Rate dishes";
  $("#rqLead").innerHTML = isStaff
    ? "You compose it: add from the recommendations, or search the menu.<br>"
      + "Students see only what you add — six dishes at most."
    : "Rate what you tried, or say what you'd try.<br>"
      + "Answers go to the kitchen that cooks it.";
  renderQueueStaff();
  if (!q.items.length) {

    box.innerHTML = `<p class="board__empty">${isStaff
      ? "Nothing in the survey yet — add dishes above."
      : "There's no survey for this hall right now."}</p>`;
    return;
  }

  const items = [...q.items.filter(i => i.your_rating == null),
                 ...q.items.filter(i => i.your_rating != null)];
  box.innerHTML = items.map(i => {
      const [label, cls] = RQ_REASON[i.reason] || ["", "plain"];

      return `<div class="rq" data-rq="${i.recipe_id}">
        <div class="rq__hd">
          <span class="rq__name">${esc(i.name || `Dish ${i.recipe_id}`)}</span>
          <button class="textlink" data-rq-sheet="${i.recipe_id}">(details)</button>
          <span class="chip ${cls}">${esc(label)}</span>
        </div>
        ${isStaff ? `<div class="rq__meta">${i.count
            ? `★ ${i.average} (${i.count})` : "not yet rated"}${
          i.intents
            ? ` &nbsp;·&nbsp; <span class="rq__agg">would try ${i.intents.try}
                · would skip ${i.intents.skip}</span>` : ""}
            <button class="textlink"
              data-rq-unpick="${i.recipe_id}">(remove from survey)</button></div>`
        : i.your_rating != null

          ? `<p class="rq__meta">You've rated this dish — that already says
              more than a would-try.</p>`
          : `
        <div class="rq__int" data-rq-int="${i.recipe_id}">
          <span>Didn't try it?</span>
          <button data-int="try"
            aria-pressed="${i.your_intent === "try"}">Would try</button>
          <button data-int="skip"
            aria-pressed="${i.your_intent === "skip"}">Would skip</button>
        </div>`}
        <p class="rq__msg" hidden></p>
      </div>`;
    }).join("");

  box.querySelectorAll("[data-rq-sheet]").forEach(b =>
    b.addEventListener("click", () => openSheet(Number(b.dataset.rqSheet))));
  box.querySelectorAll("[data-rq-unpick]").forEach(b =>
    b.addEventListener("click", async () => {
      try {
        await api(`/rating-queue/items?location=${state.hall}`
                  + `&recipe=${b.dataset.rqUnpick}`, { method: "DELETE" });
      } catch (e) { rqCardMsg(b.dataset.rqUnpick, e.message); return; }
      loadQueuePane();
    }));
  box.querySelectorAll("[data-rq-int] button").forEach(b =>
    b.addEventListener("click", () => rqIntent(b)));
}

async function rqIntent(btn) {
  const wrap = btn.closest("[data-rq-int]");
  const recipeId = Number(wrap.dataset.rqInt);

  const val = btn.getAttribute("aria-pressed") === "true"
    ? null : btn.dataset.int;
  try {
    await api("/intents", { method: "POST", body: {
      recipe_id: recipeId, location_id: state.hall, intent: val } });
    wrap.querySelectorAll("button").forEach(x =>
      x.setAttribute("aria-pressed", String(!!val && x.dataset.int === val)));
    rqCardMsg(recipeId, val === null ? "Noted — answer withdrawn."
      : val === "try" ? "Noted — the kitchen hears a would-try."
      : "Noted — the kitchen hears a would-skip.");
  } catch (e) {
    rqCardMsg(recipeId, e.message);
  }
}

function rqCardMsg(recipeId, text) {
  const el = document.querySelector(`.rq[data-rq="${recipeId}"] .rq__msg`);
  if (!el) return;
  el.hidden = !text;
  el.textContent = text || "";
}

function renderQueueStaff() {
  const boxS = $("#rqStaff");
  if (!state.user || state.user.affiliation !== "staff") {
    boxS.innerHTML = "";
    return;
  }
  const locked = !state.user.kitchen;
  const dis = locked ? "disabled" : "";
  const recs = state.rq?.recommendations || [];
  boxS.innerHTML = `
    ${locked ? `<p class="board__lockednote">Viewing read-only —
        <a class="textlink" href="/staffunlock?then=${
          encodeURIComponent("/?pane=survey")}">unlock kitchen actions</a>
        to compose the survey.</p>` : ""}
    <h3 class="board__sub">Recommended for this survey</h3>
    <div id="rqRecs">${recs.length ? recs.map(r => {
      const [label, cls] = RQ_REASON[r.reason] || ["", "plain"];
      return `<div class="brow">
        <span class="brow__name">${esc(r.name || `Dish ${r.recipe_id}`)}</span>
        <span class="chip ${cls}">${esc(label)}</span>
        <button class="btn ghost small" ${dis}
                data-rq-pick="${r.recipe_id}">Add</button>
      </div>`;
    }).join("")
      : `<p class="board__empty">Nothing to recommend right now.</p>`}</div>
    <h3 class="board__sub">Or search the menu</h3>
    <input id="rqFilter" type="search" autocomplete="off"
           placeholder="Type to find a dish on the current menu…"
           aria-label="Find dishes">
    <div id="rqPickRows"></div>
    <h3 class="board__sub">In the survey for students</h3>
    <p class="fchint" style="margin:0 0 10px">Removing a dish returns it to
      the recommendations. No survey composed means nothing goes out for
      this hall — students are only ever invited to a survey that exists.</p>`;
  $("#rqFilter").addEventListener("input", renderQueuePickRows);
  $("#rqRecs").querySelectorAll("[data-rq-pick]").forEach(b =>
    b.addEventListener("click", () => rqAddPick(b.dataset.rqPick)));
  renderQueuePickRows();
}

async function rqAddPick(id) {
  try {
    await api("/rating-queue/picks", { method: "POST", body: {
      location_id: state.hall, recipe_id: Number(id) } });
  } catch (e) {
    $("#rqMsg").hidden = false;
    $("#rqMsg").textContent = e.message;
    return;
  }
  $("#rqMsg").hidden = true;
  loadQueuePane();
}

function renderQueuePickRows() {
  const box = $("#rqPickRows");
  if (!box) return;
  const filter = ($("#rqFilter")?.value || "").trim().toLowerCase();

  if (!filter) {
    box.innerHTML = "";
    return;
  }
  const queued = new Set((state.rq?.items || []).map(i => i.recipe_id));
  const all = [...state.items.values()]
    .filter(i => !queued.has(i.id))
    .filter(i => (i.name || "").toLowerCase().includes(filter))
    .sort((a, b) => (a.name || "").localeCompare(b.name || ""));
  const shown = all.slice(0, 30);
  const dis = state.user?.kitchen ? "" : "disabled";
  if (!shown.length) {
    box.innerHTML = `<p class="board__empty">${state.items.size
      ? "No dish matches."
      : "No menu is loaded — open the Menu pane and pick the hall and meal first."}</p>`;
    return;
  }
  box.innerHTML = shown.map(i => `
    <div class="brow">
      <span class="brow__name">${esc(i.name)}</span>
      <button class="btn ghost small" ${dis} data-rq-pick="${i.id}">Add to survey</button>
    </div>`).join("") + (all.length > shown.length
    ? `<p class="board__more">…and ${all.length - shown.length} more — type to find a dish.</p>`
    : "");
  box.querySelectorAll("[data-rq-pick]").forEach(b =>
    b.addEventListener("click", () => rqAddPick(b.dataset.rqPick)));
}

const NUTRI = [
  ["total_fat", "Total Fat"], ["sat_fat", "Saturated Fat"], ["trans_fat", "Trans Fat"],
  ["cholesterol", "Cholesterol"], ["sodium", "Sodium"], ["total_carb", "Total Carbohydrate"],
  ["dietary_fiber", "Dietary Fiber"], ["sugars", "Sugars"], ["protein", "Protein"],
];

async function openSheet(id, { push = true } = {}) {
  state.dish = id;
  if (push) writeUrl();
  $("#shTitle").textContent = state.items.get(id)?.name || "Loading…";
  $("#shBody").innerHTML = `<div class="state"><div class="spin"></div>Loading…</div>`;
  $("#sheet").classList.add("open");
  $("#scrim").classList.add("open");
  document.body.style.overflow = "hidden";

  let r;

  const mealQ = state.meal != null ? `&meal=${state.meal}` : "";
  try { r = await api(`/recipes/${id}?location=${state.hall}${mealQ}`); }
  catch (e) { $("#shBody").innerHTML = `<div class="state">${esc(e.message)}</div>`; return; }
  state.sheetNote = r.your_feedback || null;
  state.fbSource = null;

  $("#shTitle").textContent = r.name;
  const rows = NUTRI.map(([k, label]) => {
    const v = r[k];
    if (!v || !v.amount) return "";
    return `<tr><th>${label}</th><td>${esc(v.amount)}</td>
      <td class="dv">${v.percent != null ? Math.round(v.percent) + "%" : ""}</td></tr>`;
  }).join("");

  const signedOut = !state.user;

  const survey = state.pane === "survey";

  const fbToday = state.date === localToday();
  const shAvail = r.availability
    ? (r.availability.note
       || (r.availability.status === "out" ? "Ran out for this service."
                                           : "Running low."))
    : null;
  $("#shBody").innerHTML = `
    ${shAvail ? `<div class="dish__avail dish__avail--sheet">${esc(shAvail)}</div>` : ""}
    <div class="kcal">
      <b>${r.calories ?? "—"}</b><span>calories</span>
      <span style="flex:1"></span><span>${esc(r.serving_size || "")}</span>
    </div>

    <div class="chips" style="margin-top:14px">${chips({
      vegan: r.vegan, vegetarian: r.vegetarian, allergens: r.allergens,
      spice: r.spice, consumption: r.consumption,
      rating: survey ? null : r.rating,
    })}
    ${!survey && (r.allergens || []).length === 0
      ? `<span class="chip plain">No top-9 allergens listed</span>` : ""}</div>

    ${r.consumption ? `<div class="sec">
      <h4>Waste record <span class="sim">mock feed</span></h4>
      <p style="margin:0;font-size:14px;color:var(--ink-soft)">
        ${Math.round(r.consumption.rate * 100)}% of what was prepared got eaten,
        across ${r.consumption.observations} measured service${r.consumption.observations === 1 ? "" : "s"}.
      </p></div>` : ""}
    ${rows ? `<div class="sec"><h4>Nutrition</h4>
      <table class="nutri">${rows}</table>
      <p style="font-size:12px;color:var(--ink-mute);margin:8px 0 0">
        Right column is % Daily Value.</p></div>` : ""}

    ${survey ? "" : `<div class="sec">
      <h4>Rate this dish</h4>
      <div class="rate" id="sheetRate">
        ${[1, 2, 3, 4, 5].map(v =>
          `<button data-v="${v}" ${signedOut ? "disabled" : ""}
            aria-pressed="${r.your_rating === v}">${v}</button>`).join("")}
      </div>
      <div id="rateMsg" style="font-size:13.5px;color:var(--ink-mute)">
        ${signedOut
          ? `Reading is open to everyone. <a href="#" id="sheetSignin">Sign in</a> to rate.`
          : r.your_rating

            ? `You rated this ${r.your_rating}. Choosing again replaces it — one rating per dish, per hall.`
            : `Your rating goes to the kitchen that cooked it.`}
      </div>
    </div>`}

    ${!survey && fbToday && state.meal != null ? `<div class="sec" id="fbSec">
      <h4>Tell the kitchen about this dish</h4>
      ${signedOut
        ? `<div class="fb__note">Notes here go to the kitchen that cooks this
            dish. <a href="#" id="fbSignin">Sign in</a> to tell them.</div>`
        : state.user.feedback_paused

          ? `${state.sheetNote
                ? `<p class="fb__sent">${esc(state.sheetNote.text)}</p>` : ""}
             <div class="fb__paused">Your feedback is valued, but sending from
              this account is paused right now. The pause can be temporary —
              ask at the servery if you have questions.</div>`
          : state.sheetNote ? fbSentHtml(state.sheetNote)
          : `<div class="fb__note fb__collapsed">
              <a href="#" id="fbOpen">(add a note for the kitchen)</a></div>`}
    </div>` : ""}

    ${r.ingredients ? `<div class="sec"><h4>Ingredients</h4>
      <div class="ingred">${esc(r.ingredients)}</div></div>` : ""}

    ${r.spice ? `<div class="sec"><h4>Spice level</h4>
      <p style="margin:0;font-size:13.5px;color:var(--ink-soft)">
        ${r.spice.level === 0 ? "No heat detected" : "Level " + r.spice.level + " of 3"} —
        ${esc(r.spice.basis)}. ${r.spice.curated
          ? "Curated entry."
          : "Inferred from the ingredient list; HUDS publishes no spice data."}
      </p></div>` : ""}`;

  if (signedOut) {
    $("#sheetSignin")?.addEventListener("click", e => { e.preventDefault(); signIn(); });
  } else {

    $("#sheetRate")?.querySelectorAll("button").forEach(b =>
      b.addEventListener("click", () => submitRating(id, Number(b.dataset.v), b)));
  }
  wireFeedback(id);
  $("#shClose").focus();
}

function fbFieldHtml(prefill = "", signed = false) {
  return `
    <textarea id="fbText" maxlength="500" rows="3"
      placeholder="What should the kitchen know about this dish?">${esc(prefill)}</textarea>
    <div class="fb__row">
      <label class="fb__sign"><input type="checkbox" id="fbSign"${signed ? " checked" : ""}>
        Put my name on this note (for kitchen staff to see)</label>
      <span class="fb__count" id="fbCount" hidden></span>
    </div>
    <div class="fb__row">
      <button class="btn" id="fbSend">Send note</button>
      <span class="fb__msg" id="fbMsg" role="status"></span>
    </div>`;
}

function fbSentHtml(note) {
  return `<p class="fb__sent">${esc(note.text)}</p>
    <div class="fb__meta">Sent to the kitchen${note.signed ? ", signed" : ""}.
      <a href="#" id="fbEdit">(edit note)</a></div>`;
}

function fbExpand(recipeId, focus = true) {
  const sec = $("#fbSec");
  if (!sec) return;
  if ($("#fbText")) { if (focus) $("#fbText").focus(); return; }
  const closed = sec.querySelector(".fb__collapsed");
  if (!closed) return;
  closed.remove();
  sec.insertAdjacentHTML("beforeend", fbFieldHtml());
  wireFeedback(recipeId);
  if (focus) $("#fbText").focus();
}

function wireFeedback(recipeId) {
  $("#fbSignin")?.addEventListener("click", e => { e.preventDefault(); signIn(); });
  $("#fbOpen")?.addEventListener("click", e => {
    e.preventDefault(); state.fbSource = "sheet"; fbExpand(recipeId, true);
  });
  $("#fbEdit")?.addEventListener("click", e => {
    e.preventDefault();
    const sec = $("#fbSec");
    sec.querySelector(".fb__sent")?.remove();
    sec.querySelector(".fb__meta")?.remove();
    sec.insertAdjacentHTML("beforeend",
      fbFieldHtml(state.sheetNote?.text || "", !!state.sheetNote?.signed));
    wireFeedback(recipeId);
    $("#fbText").focus();
  });
  const ta = $("#fbText");
  if (!ta) return;
  ta.addEventListener("input", () => {
    const left = 500 - ta.value.length;
    const c = $("#fbCount");
    c.hidden = left > 100;
    c.textContent = `${left} left`;
  });
  $("#fbSend").addEventListener("click", () => submitFeedback(recipeId));
}

async function submitFeedback(recipeId) {
  const msg = $("#fbMsg");
  const text = $("#fbText").value.trim();
  if (!text) { msg.textContent = "Nothing to send yet."; return; }
  const btn = $("#fbSend");
  btn.disabled = true;
  btn.textContent = "Sending…";
  try {
    await api("/feedback", { method: "POST", body: {
      recipe_id: recipeId, location_id: state.hall, meal: state.meal,
      text, signed: $("#fbSign").checked, source: state.fbSource || "sheet",
    } });
    state.sheetNote = { text, signed: $("#fbSign").checked };
    state.fbSource = null;
    const sec = $("#fbSec");
    sec.querySelectorAll("textarea, .fb__row").forEach(el => el.remove());
    sec.insertAdjacentHTML("beforeend", fbSentHtml(state.sheetNote));
    wireFeedback(recipeId);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Send note";

    msg.textContent = e.message || "Couldn't send — try again.";
  }
}

async function submitRating(recipeId, score, btn) {
  const group = btn.parentElement;
  group.querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", "false"));
  btn.setAttribute("aria-pressed", "true");
  try {
    const r = await api("/ratings", {
      method: "POST",
      body: { recipe_id: recipeId, score, location_id: state.hall, served_on: state.date },
    });
    $("#rateMsg").textContent =
      `${r.changed ? "Updated" : "Recorded"}. Now averaging ${r.average} from `
      + `${r.count} rating${r.count === 1 ? "" : "s"} here.`
      + creditSuffix(r.reward);

    if (!state.sheetNote && !(state.user && state.user.feedback_paused)) {
      state.fbSource = "rating";
      fbExpand(recipeId, false);
    }
    const it = state.items.get(recipeId);
    if (it) { it.rating = { average: r.average, count: r.count }; loadMenuRowChips(recipeId); }
    if (r.reward && r.reward.granted_cents) loadWallet();
  } catch (e) {
    $("#rateMsg").textContent = e.status === 401
      ? "Session expired — sign in again." : `Couldn't save: ${e.message}`;
  }
}

function loadMenuRowChips(recipeId) {
  const row = document.querySelector(`.dish[data-id="${recipeId}"] .chips`);
  const it = state.items.get(recipeId);
  if (row && it) row.innerHTML = chips(it);
}

function closeSheet({ pop = true } = {}) {

  if (pop && state.dish != null) { history.back(); return; }
  state.dish = null;
  $("#sheet").classList.remove("open");
  $("#scrim").classList.remove("open");
  document.body.style.overflow = "";
}

function renderLineCard() {
  const now = state.line;
  const seasonClosed = state.hallOpenToday === false;

  if (!seasonClosed && now && now.source === "staff") {
    $("#lineDetail").style.display = "";
    $("#lineTypical").style.display = "none";
    const chip = $("#lineSim");
    chip.style.display = "";
    chip.textContent = "KITCHEN-REPORTED";
    chip.classList.add("rep");
    const words = { no_wait: "No wait", short: "Short line", long: "Long line" };
    if (now.band) {
      $("#waitNow").textContent = words[now.band] || now.band;
      $("#waitSub").textContent =
        `Reported by the kitchen ${relAgo(now.reported_at)}`;
      const lit = { no_wait: 2, short: 6, long: 11 }[now.band] || 0;
      const cls = { no_wait: "lo", short: "mid", long: "hi" }[now.band];
      $("#gauge").innerHTML = Array.from({ length: 12 },
        (_, i) => `<i class="${i < lit ? "on " + cls : ""}"></i>`).join("");
    } else {
      $("#waitNow").textContent = "No report";
      $("#waitSub").textContent =
        "The kitchen hasn't reported the line recently.";
      $("#gauge").innerHTML = Array.from({ length: 12 },
        () => "<i></i>").join("");
    }
    return;
  }
  const simChip = $("#lineSim");
  simChip.textContent = "SIMULATED";
  simChip.classList.remove("rep");
  const closed = seasonClosed || (now && now.busyness === 0);

  $("#lineDetail").style.display = closed ? "none" : "";

  $("#lineTypical").style.display = seasonClosed ? "none" : "";
  $("#lineSim").style.display = seasonClosed ? "none" : "";

  if (closed) {
    $("#waitNow").textContent = "Closed";
    $("#waitSub").textContent = "Not serving at the moment";
    return;
  }
  if (!now) { $("#waitNow").textContent = "—"; $("#waitSub").textContent = ""; return; }

  $("#waitNow").textContent = now.wait_minutes <= 1 ? "No wait" : `~${now.wait_minutes} min`;
  $("#waitSub").textContent =
      now.busyness > .75 ? "Busiest stretch — consider 20 min later"
    : now.busyness > .45 ? "Moderate flow at the servery"
    : "Quiet — good time to go";

  const lit = Math.round(now.busyness * 12);
  const cls = now.busyness > .75 ? "hi" : now.busyness > .45 ? "mid" : "lo";
  $("#gauge").innerHTML = Array.from({ length: 12 },
    (_, i) => `<i class="${i < lit ? "on " + cls : ""}"></i>`).join("");
}

async function loadLine() {
  try {
    const now = await api(`/line/${state.hall}`);
    state.line = now;
    renderLineCard();
    const typical = await api(`/line/${state.hall}/typical?date=${state.date}`);

    if (!typical.series || !typical.series.length) {
      $("#lineTypical").style.display = "none";
      return;
    }
    if (state.hallOpenToday !== false) $("#lineTypical").style.display = "";

    const series = typical.series;
    const peak = Math.max(...series.map(s => s.busyness), 0.01);
    const nowHM = new Date().toTimeString().slice(0, 5);
    let nearest = 0, best = 1e9;
    series.forEach((s, i) => {
      const d = Math.abs(Number(s.time.slice(0, 2)) * 60 + Number(s.time.slice(3))
        - (Number(nowHM.slice(0, 2)) * 60 + Number(nowHM.slice(3))));
      if (d < best) { best = d; nearest = i; }
    });
    $("#spark").innerHTML = series.map((s, i) =>
      `<div class="${i === nearest ? "now" : s.busyness === peak ? "peak" : ""}"
        style="height:${Math.round(s.busyness / peak * 100)}%" title="${s.time}"></div>`).join("");
  } catch (e) {
    state.line = null;
    $("#waitNow").textContent = "—";
    $("#waitSub").textContent = "Line data unavailable";
    $("#lineDetail").style.display = "none";
    $("#lineTypical").style.display = "none";
    $("#lineSim").style.display = "none";
  }
}

const ACCESS_LABEL = {
  open: "You can eat here",
  guest_only: "Only as a resident's guest",
  residents_only: "Residents only",
  unknown: "Rule not confirmed",
};

const ACCESS_LABEL_STAFF = {
  open: "Open to other Houses",
  guest_only: "Guests of residents only",
  residents_only: "Closed to other Houses",
  unknown: "Rule not confirmed",
};

async function loadInterhouse() {
  const box = $("#interhouse");
  const isStaff = state.user && state.user.affiliation === "staff";

  if (!state.user || (!state.user.house_key && !isStaff)) {
    $("#whereTitle").textContent = "Where can I eat?";
    box.innerHTML = `
      <p style="margin:0 0 14px;font-size:14px;color:var(--ink-soft)">
        Sign in to find out where you can eat.</p>
      <button class="btn ghost wide" id="ihSignin">Sign in</button>`;
    $("#ihSignin").addEventListener("click", signIn);
    return;
  }

  $("#whereTitle").textContent = isStaff ? "Interhouse rules" : "Where can I eat?";

  let data;
  try {
    data = await api(`/interhouse?meal=${state.meal}&date=${state.date}`);
  } catch (e) {
    box.innerHTML = `<div class="state" style="padding:20px 0">${esc(e.message)}</div>`;
    return;
  }
  const labels = isStaff ? ACCESS_LABEL_STAFF : ACCESS_LABEL;
  const rows = data.halls.map(h => `
    <div class="ih">
      <span class="ih__dot ${h.access}"></span>
      <div class="ih__body">
        <div class="ih__name">${esc(h.location_name)}${
          h.is_home ? " · your House" : ""}</div>
        <div class="ih__why">${esc(labels[h.access])} — ${esc(h.reason)}</div>
      </div>
    </div>`).join("");

  const when = `${data.meal_name.toLowerCase()} on ${esc(data.weekday)}`;
  const who = isStaff
    ? `Access rules in force for ${when}, as they apply to a student from another House:`
    : `As a <b>${esc(data.viewer_house_name || data.viewer_house)}</b> resident, for ${when}:`;

  box.innerHTML = `
    <p style="margin:0 0 10px;font-size:13.5px;color:var(--ink-soft)">${who}</p>
    ${rows}
    <div class="ih__legend">
      <span><i style="background:var(--ivy)"></i>${labels.open}</span>
      <span><i style="background:var(--gold)"></i>${labels.guest_only}</span>
      <span><i style="background:var(--crimson)"></i>${labels.residents_only}</span>
      <span><i style="background:var(--shade)"></i>Unconfirmed</span>
    </div>
    <p style="margin:12px 0 0;font-size:12.5px;color:var(--ink-mute)">${esc(data.caveat)}</p>`;

}

function rsvpQuestion() {
  const hall = $("#hall").selectedOptions[0]?.textContent || "here";

  const meal = (state.mealName || MEALS[state.meal] || "").toLowerCase();
  return `Eating at ${hall} for ${meal}?`;
}

function updateRsvpCopy() {
  renderRsvp();

  markRsvp(null);
}

const RSVP_WHY_OPEN = `Telling the kitchen you're <em>not</em> coming is the useful
  part — it helps them learn demand is light before the food is cooked.`;

function renderRsvp() {
  const card = $("#tonight");
  const act = document.querySelector(".rsvp__act");
  const why = $("#rsvpWhy");

  if (!state.rsvpVisible || state.serviceStatus === "over") {
    card.style.display = "none";
    return;
  }
  card.style.display = "";

  if (state.declarationOpen) {
    $("#rsvpQ").textContent = rsvpQuestion();
    why.innerHTML = RSVP_WHY_OPEN;
    why.style.display = "";
    act.style.display = "";
    return;
  }

  $("#rsvpQ").textContent =
    `${state.mealName || MEALS[state.meal] || "This meal"} is currently being served.`;
  why.style.display = "none";
  act.style.display = "none";
}

function markRsvp(attending) {
  $("#yesBtn").setAttribute("aria-pressed", String(attending === true));
  $("#noBtn").setAttribute("aria-pressed", String(attending === false));
}
async function declare(attending) {
  if (!state.user) { signIn(); return; }
  try {
    const r = await api("/attendance", {
      method: "POST",
      body: { location_id: state.hall, meal: state.meal, served_on: state.date, attending },
    });
    markRsvp(attending);

    toast((attending ? "Thanks — you're counted in." : "Noted. The kitchen will cook less.")
          + creditSuffix(r.reward));
    if (r.reward && r.reward.granted_cents) loadWallet();
  } catch (e) { toast(`Couldn't record that: ${e.message}`); }
}

async function init() {

  const params = new URLSearchParams(location.search);
  if (params.has("nobanner")) {
    const bar = $("#simbar");
    if (bar) bar.hidden = true;
  }

  try {
    const meta = await api("/meta");
    server.mode = meta.mode || null;
    server.demo = meta.mode === "demo";
  } catch { server.demo = false; }

  const HOUSE = /House|Annenberg|Fly-By/i;
  let locs = [];
  try { locs = await api("/locations"); }
  catch (e) {
    $("#menu").innerHTML = `<div class="state">Backend unreachable.<br><small>${esc(e.message)}</small></div>`;
    return;
  }
  const sorted = [...locs].sort((a, b) =>
    (HOUSE.test(b.name) - HOUSE.test(a.name)) || a.name.localeCompare(b.name));
  $("#hall").innerHTML = sorted.map(l => `<option value="${l.id}">${esc(l.name)}</option>`).join("");

  const u = readUrl();
  state.pane = u.pane || "menu";
  const known = new Set(sorted.map(l => l.id));
  state.hall = known.has(u.hall) ? u.hall : Number(sorted[0].id);
  state.date = /^\d{4}-\d{2}-\d{2}$/.test(u.date || "") ? u.date : localToday();

  state.meal = [0, 1, 2].includes(u.meal) ? u.meal : mealByClock();
  syncControls();
  writeUrl({ replace: true });

  $("#hall").addEventListener("change", e => {
    state.hall = Number(e.target.value);
    writeUrl(); refreshContext();
  });
  $("#date").addEventListener("change", e => {
    state.date = e.target.value;
    writeUrl(); refreshContext();
  });
  $("#mealSeg").addEventListener("click", e => {
    const b = e.target.closest("button[data-meal]");
    if (!b) return;
    const meal = Number(b.dataset.meal);
    if (meal === state.meal) return;
    state.meal = meal;
    syncControls(); writeUrl(); refreshContext();
  });

  $("#navToggle").addEventListener("click", () => {
    const list = $("#navList");
    const open = list.classList.toggle("open");
    $("#navToggle").setAttribute("aria-expanded", String(open));
  });

  $("#yesBtn").addEventListener("click", () => declare(true));
  $("#noBtn").addEventListener("click", () => declare(false));
  $("#scrim").addEventListener("click", () => closeSheet());
  $("#shClose").addEventListener("click", () => closeSheet());
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && state.dish != null) closeSheet();
  });

  await loadMe();
  showPane(state.pane, { push: false });
  initPush();
  await refreshContext();

  if (u.dish != null) openSheet(u.dish, { push: false });
}

init();
