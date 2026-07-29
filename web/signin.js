"use strict";

const API = "/api/v1";
const $ = s => document.querySelector(s);
const esc = s => String(s ?? "").replace(/[&<>"]/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

const state = { accounts: [], account: null, step: 1 };

if (new URLSearchParams(location.search).has("nobanner")) {
  const bar = $("#simbar");
  if (bar) bar.hidden = true;
}

function initials(name) {
  return name.split(/\s+/).filter(p => /[A-Za-z]/.test(p))
    .slice(0, 2).map(p => p[0].toUpperCase()).join("");
}

function showStep(n, { push = true } = {}) {
  state.step = n;
  [1, 2, 3, 4].forEach(i => {
    const el = $("#step" + i);
    if (el) el.hidden = i !== n;
  });
  if (push) {
    const url = `${location.pathname}${location.search}#step${n}`;
    if (n === 4) history.replaceState({ step: n }, "", url);
    else history.pushState({ step: n }, "", url);
  }
  window.scrollTo({ top: 0, behavior: "smooth" });
}
window.addEventListener("popstate", e => {
  const n = (e.state && e.state.step) ||
            Number((location.hash.match(/^#step(\d)$/) || [])[1]) || 1;

  showStep(state.account ? n : 1, { push: false });
});

function chosenMarkup(a) {
  return `<div class="chosen__av">${esc(initials(a.name))}</div>
    <div><div class="chosen__n">${esc(a.name)}</div>
    <div class="chosen__e">${esc(a.netid)} &middot; ${esc(a.residence)}</div></div>`;
}

let active = -1;

function optionMarkup(a, i) {
  return `<li class="combo__opt" role="option" id="opt-${esc(a.netid)}"
    data-netid="${esc(a.netid)}" data-i="${i}" aria-selected="false">
    <span class="combo__av">${esc(initials(a.name))}</span>
    <span class="combo__b">
      <span class="combo__n">${esc(a.name)}<span class="combo__tag ${
        a.affiliation === "staff" ? "staff" : ""}">${esc(a.affiliation)}</span></span>
      <span class="combo__e">${esc(a.netid)} &middot; ${esc(a.residence)}</span>
    </span>
  </li>`;
}

function openList(open) {
  const list = $("#comboList"), btn = $("#comboBtn");
  list.hidden = !open;
  btn.setAttribute("aria-expanded", String(open));
  if (open) {
    active = state.account
      ? state.accounts.findIndex(a => a.netid === state.account.netid) : 0;
    highlight(active);
    list.querySelector(".combo__opt.active")?.scrollIntoView({ block: "nearest" });
  } else {
    btn.removeAttribute("aria-activedescendant");
  }
}

function highlight(i) {
  const opts = [...document.querySelectorAll(".combo__opt")];
  if (!opts.length) return;
  active = Math.max(0, Math.min(opts.length - 1, i));
  opts.forEach((o, n) => o.classList.toggle("active", n === active));
  $("#comboBtn").setAttribute("aria-activedescendant", opts[active].id);
  opts[active].scrollIntoView({ block: "nearest" });
}

function choose(netid) {
  state.account = state.accounts.find(a => a.netid === netid) || null;
  document.querySelectorAll(".combo__opt").forEach(o =>
    o.setAttribute("aria-selected", String(o.dataset.netid === netid)));

  const val = $("#comboVal");
  if (state.account) {
    val.classList.remove("placeholder");
    val.innerHTML = `${esc(state.account.name)}<small>${
      esc(state.account.netid)} &middot; ${esc(state.account.residence)}</small>`;
  } else {
    val.classList.add("placeholder");
    val.textContent = "Select an account";
  }

  $("#toStep2").disabled = !state.account;
  openList(false);
  $("#comboBtn").focus();
}

async function loadAccounts() {
  const list = $("#comboList");
  let data;
  try {
    const res = await fetch(`${API}/auth/accounts`);
    if (!res.ok) throw new Error("HTTP " + res.status);
    data = await res.json();
  } catch (e) {
    $("#comboVal").textContent = "Couldn't load accounts";
    $("#acctHint").textContent = e.message;
    $("#comboBtn").disabled = true;
    return;
  }
  state.accounts = data.accounts;
  $("#comboVal").classList.add("placeholder");
  list.innerHTML = data.accounts.map(optionMarkup).join("");

  list.querySelectorAll(".combo__opt").forEach(o => {
    o.addEventListener("click", () => choose(o.dataset.netid));
    o.addEventListener("mousemove", () => highlight(Number(o.dataset.i)));
  });
}

$("#comboBtn").addEventListener("click", () => {
  openList($("#comboList").hidden);
});

$("#comboBtn").addEventListener("keydown", e => {
  const closed = $("#comboList").hidden;
  switch (e.key) {
    case "ArrowDown": e.preventDefault(); closed ? openList(true) : highlight(active + 1); break;
    case "ArrowUp":   e.preventDefault(); closed ? openList(true) : highlight(active - 1); break;
    case "Home":      if (!closed) { e.preventDefault(); highlight(0); } break;
    case "End":       if (!closed) { e.preventDefault(); highlight(state.accounts.length - 1); } break;
    case "Enter":
    case " ":
      if (!closed) {
        e.preventDefault();
        const opt = document.querySelectorAll(".combo__opt")[active];
        if (opt) choose(opt.dataset.netid);
      }
      break;
    case "Escape": if (!closed) { e.preventDefault(); openList(false); } break;
  }
});

document.addEventListener("click", e => {
  if (!$("#combo").contains(e.target)) openList(false);
});

function startChallenge() {

  $("#pushNum").textContent = String(10 + Math.floor(Math.random() * 90));
}

async function approve() {
  const btn = $("#approveBtn");
  btn.disabled = true;
  btn.textContent = "Submitting…";
  try {
    const res = await fetch(`${API}/auth/demo-signin`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ netid: state.account.netid }),
    });
    if (!res.ok) throw new Error("HTTP " + res.status);
    const out = await res.json();
    $("#doneMsg").textContent = `Signed in as ${out.name}. Returning you to Veritaste…`;
    showStep(4);
    setTimeout(() => { window.location.href = "/"; }, 1400);
  } catch (e) {
    btn.disabled = false;
    btn.textContent = "Submit";
    $("#doneMsg") && ($("#doneMsg").textContent = "");
    alert("Sign-in failed: " + e.message);
  }
}

$("#toStep2").addEventListener("click", () => {
  if (!state.account) return;
  ["#chosen2", "#chosen3", "#chosen4"].forEach(sel => {
    const el = $(sel);
    if (el) el.innerHTML = chosenMarkup(state.account);
  });
  showStep(2);
});

$("#toStep3").addEventListener("click", () => { startChallenge(); showStep(3); });
$("#approveBtn").addEventListener("click", approve);

document.querySelectorAll("[data-back]").forEach(b =>
  b.addEventListener("click", e => {
    e.preventDefault();
    const target = Number(b.dataset.back);
    const delta = target - state.step;
    if (delta < 0) history.go(delta);
    else showStep(target);
  }));

loadAccounts();
