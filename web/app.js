"use strict";

const MEALS = ["Breakfast", "Lunch", "Dinner"];

const API = "/api/v1";
const state = { hall: null, date: null, meal: 1, dish: null, user: null,
                items: new Map(), line: null, hallOpenToday: null,
                mealName: null, correctingMeal: false };

const $ = s => document.querySelector(s);
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

function readUrl() {
  const p = new URLSearchParams(location.search);
  const num = k => (p.has(k) && p.get(k) !== "" ? Number(p.get(k)) : null);
  return { hall: num("hall"), date: p.get("date"), meal: num("meal"), dish: num("dish") };
}

function writeUrl({ replace = false } = {}) {
  const p = new URLSearchParams();
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

function renderMealSeg(options) {
  const seg = $("#mealSeg");

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
      markRsvp(null);
      toast("Signed out.");
    });
  } else {
    box.innerHTML = `<button class="linkbtn" id="signinBtn">Sign in</button>`;
    $("#signinBtn").addEventListener("click", signIn);
  }
}
function signIn() {

  window.location.href = "/signin.html";
}

async function loadMe() {
  try {
    const me = await api("/me");
    state.user = me.signed_in ? me : null;
  } catch { state.user = null; }
  renderWho();
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
  return `<button class="dish" data-id="${it.id}">
    <div class="dish__main">
      <div class="dish__name">${esc(it.name)}</div>
      ${meta ? `<div class="dish__meta">${meta}</div>` : ""}
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

  box.innerHTML = [...data.categories]
    .sort((a, b) => catRank(a.name) - catRank(b.name) || a.name.localeCompare(b.name))
    .map(c => `<div class="cat"><h3>${esc(c.name)}</h3>${c.items.map(dishRow).join("")}</div>`)
    .join("");

  box.querySelectorAll(".dish").forEach(b =>
    b.addEventListener("click", () => openSheet(Number(b.dataset.id))));

  updateContextNotices(data);
}

function updateContextNotices(data) {
  const known = data !== null;
  const hasMenu = !!(data && data.categories && data.categories.length);
  const mealsServed = (data && data.meals_served) || [];
  if (data && data.meal_name) { state.mealName = data.meal_name; updateRsvpCopy(); }
  const month = new Date(state.date + "T12:00:00").getMonth();
  const isSummer = month >= 5 && month <= 7;

  const closedAllDay = known && mealsServed.length === 0;

  if (isSummer && closedAllDay) {
    $("#hint").innerHTML = `<b>Summer service.</b> Only Adams House and Annenberg
      appear in HUDS data right now. <a href="#" id="jump">Jump to 15 Apr 2026</a>
      to see all 13 halls.`;
    $("#hint").style.display = "";
    $("#jump").addEventListener("click", e => {
      e.preventDefault();
      state.date = "2026-04-15";
      syncControls(); writeUrl(); refreshContext();
    });
  } else {
    $("#hint").style.display = "none";
  }

  $("#tonight").style.display = hasMenu ? "" : "none";

  state.hallOpenToday = known ? mealsServed.length > 0 : null;
  renderLineCard();
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
  try { r = await api(`/recipes/${id}`); }
  catch (e) { $("#shBody").innerHTML = `<div class="state">${esc(e.message)}</div>`; return; }

  $("#shTitle").textContent = r.name;
  const rows = NUTRI.map(([k, label]) => {
    const v = r[k];
    if (!v || !v.amount) return "";
    return `<tr><th>${label}</th><td>${esc(v.amount)}</td>
      <td class="dv">${v.percent != null ? Math.round(v.percent) + "%" : ""}</td></tr>`;
  }).join("");

  const signedOut = !state.user;
  $("#shBody").innerHTML = `
    <div class="kcal">
      <b>${r.calories ?? "—"}</b><span>calories</span>
      <span style="flex:1"></span><span>${esc(r.serving_size || "")}</span>
    </div>

    <div class="chips" style="margin-top:14px">${chips({
      vegan: r.vegan, vegetarian: r.vegetarian, allergens: r.allergens,
      spice: r.spice, consumption: r.consumption, rating: r.rating,
    })}
    ${(r.allergens || []).length === 0
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

    <div class="sec">
      <h4>Rate this dish</h4>
      <div class="rate" id="sheetRate">
        ${[1, 2, 3, 4, 5].map(v =>
          `<button data-v="${v}" ${signedOut ? "disabled" : ""}>${v}</button>`).join("")}
      </div>
      <div id="rateMsg" style="font-size:13.5px;color:var(--ink-mute)">
        ${signedOut
          ? `Reading is open to everyone. <a href="#" id="sheetSignin">Sign in</a> to rate — feedback is attributed so the kitchen can trust it.`
          : `Your rating goes to the kitchen that cooked it.`}
      </div>
    </div>

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
    $("#sheetRate").querySelectorAll("button").forEach(b =>
      b.addEventListener("click", () => submitRating(id, Number(b.dataset.v), b)));
  }
  $("#shClose").focus();
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
      `Recorded. Now averaging ${r.average} from ${r.count} rating${r.count === 1 ? "" : "s"}.`;
    const it = state.items.get(recipeId);
    if (it) { it.rating = { average: r.average, count: r.count }; loadMenuRowChips(recipeId); }
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
    const [now, typical] = await Promise.all([
      api(`/line/${state.hall}`),
      api(`/line/${state.hall}/typical?date=${state.date}`),
    ]);
    state.line = now;
    renderLineCard();

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

  $("#rsvpQ").textContent = rsvpQuestion();

  markRsvp(null);
}

function markRsvp(attending) {
  $("#yesBtn").setAttribute("aria-pressed", String(attending === true));
  $("#noBtn").setAttribute("aria-pressed", String(attending === false));
}

async function declare(attending) {
  if (!state.user) { signIn(); return; }
  try {
    await api("/attendance", {
      method: "POST",
      body: { location_id: state.hall, meal: state.meal, served_on: state.date, attending },
    });
    markRsvp(attending);
    toast(attending ? "Thanks — you're counted in." : "Noted. The kitchen will cook less.");
  } catch (e) { toast(`Couldn't record that: ${e.message}`); }
}

async function init() {

  const params = new URLSearchParams(location.search);
  if (params.has("nobanner")) {
    const bar = $("#simbar");
    if (bar) bar.hidden = true;
  }

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
  const known = new Set(sorted.map(l => l.id));
  state.hall = known.has(u.hall) ? u.hall : Number(sorted[0].id);
  state.date = /^\d{4}-\d{2}-\d{2}$/.test(u.date || "")
    ? u.date : new Date().toISOString().slice(0, 10);
  state.meal = [0, 1, 2].includes(u.meal) ? u.meal : 1;
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

  $("#yesBtn").addEventListener("click", () => declare(true));
  $("#noBtn").addEventListener("click", () => declare(false));
  $("#scrim").addEventListener("click", () => closeSheet());
  $("#shClose").addEventListener("click", () => closeSheet());
  document.addEventListener("keydown", e => {
    if (e.key === "Escape" && state.dish != null) closeSheet();
  });
  await loadMe();
  await refreshContext();

  if (u.dish != null) openSheet(u.dish, { push: false });
}

init();
