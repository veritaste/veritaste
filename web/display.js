"use strict";

const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const PARAMS = new URLSearchParams(location.search);
const HALL = Number(PARAMS.get("hall") || 9);
const POLL_MS = 15000;
const CONTEXT_MS = 5 * 60000;
let lastOk = null;

function currentMeal() {
  if (PARAMS.get("meal") !== null) return Number(PARAMS.get("meal"));
  const now = new Date();
  const h = now.getHours() + now.getMinutes() / 60;
  return h < 10.5 ? 0 : h < 14.5 ? 1 : 2;
}

let context = null;
let lastCounts = null;

function fmtTime(t) {
  if (!t) return "";

  const d = new Date(t);
  if (!Number.isNaN(d.getTime())) return timeNow(d);
  const m = /^(\d{1,2}):(\d{2})/.exec(t);
  if (!m) return t;
  let h = Number(m[1]);
  const ap = h >= 12 ? "PM" : "AM";
  h = h % 12 || 12;
  return `${h}:${m[2]} ${ap}`;
}

const timeNow = d => d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });

async function fetchJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function hallName() {
  try {
    const locations = await fetchJson("/api/v1/locations");
    const here = locations.find(l => l.id === HALL);
    const name = here ? here.name : `Hall ${HALL}`;
    $("#hall").textContent = name;
    document.title = `Veritaste — ${name}`;
  } catch {
    $("#hall").textContent = `Hall ${HALL}`;
  }
}

function render(marks) {
  const box = $("#list");
  if (!marks.length) {
    box.innerHTML = `<div class="allgood">
      <div class="allgood__line">Everything on the menu is available.</div>
      <div class="allgood__sub">Marks made on the Availability Board appear here the moment they happen.</div>
    </div>`;
    return;
  }
  box.innerHTML = `<div class="rows">` + marks.map(m => `
    <div class="row">
      <span class="tag ${m.status}">${m.status === "out" ? "Out" : "Low"}</span>
      <span class="name">${esc(m.name || `Dish ${m.recipe_id}`)}</span>
      ${m.note ? `<span class="note">${esc(m.note)}</span>` : ""}
    </div>`).join("") + `</div>`;
}

function renderDemand() {
  const box = $("#demand");
  if (!context) { box.hidden = true; return; }
  box.hidden = false;
  const name = (context.meal_name || "this service").toLowerCase();
  if (!context.takes) {
    box.innerHTML = `<div class="tile__label">Declared for ${esc(name)}</div>
      <div class="tile__na">Walk-up service — this location takes no declarations.</div>`;
    return;
  }
  const yes = lastCounts ? lastCounts.declared_attending : "—";
  const no = lastCounts ? lastCounts.declared_absent : "—";
  box.innerHTML = `
    <div class="tile__label">Declared for ${esc(name)}</div>
    <div class="tile__big">${yes}<span class="tile__word">coming</span></div>
    <div class="tile__no">${no} not coming</div>
    <div class="tile__cutoff">${context.open
      ? `Declarations close ${esc(fmtTime(context.closes))}`
      : "Declarations are closed for this service"}</div>`;
}

async function refreshServiceContext() {
  const meal = currentMeal();
  try {
    const m = await fetchJson(`/api/v1/menu?location=${HALL}&meal=${meal}`);
    context = {
      meal,
      meal_name: m.meal_name,
      takes: m.takes_attendance !== false,
      open: m.declaration_open !== false,
      closes: m.declaration_closes_at,
    };
  } catch {  }
  renderDemand();
}

async function refresh() {
  try {
    const data = await fetchJson(`/api/v1/availability?location=${HALL}`);
    lastOk = Date.now();
    $("#stale").hidden = true;
    render(data.marks);
  } catch {
    if (lastOk) $("#staleTime").textContent = timeNow(new Date(lastOk));
    else $("#staleTime").textContent = "startup";
    $("#stale").hidden = false;
  }
  try {
    lastCounts = await fetchJson(
      `/api/v1/attendance?location=${HALL}&meal=${currentMeal()}`);
  } catch {  }
  renderDemand();
}

function tickClock() {
  $("#clock").textContent = timeNow(new Date());
}

hallName();
tickClock();
refreshServiceContext();
refresh();
setInterval(tickClock, 1000);
setInterval(refresh, POLL_MS);
setInterval(refreshServiceContext, CONTEXT_MS);
