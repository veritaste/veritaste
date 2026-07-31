"use strict";

const $ = s => document.querySelector(s);
const params = new URLSearchParams(location.search);

$("#unlockForm").addEventListener("submit", async e => {
  e.preventDefault();
  const msg = $("#unlockMsg");
  msg.textContent = "";
  const passcode = $("#pcField").value;
  if (!passcode) return;
  $("#unlockBtn").disabled = true;
  try {
    const res = await fetch("/api/v1/auth/kitchen", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ passcode }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      msg.textContent = body.error || `HTTP ${res.status}`;
      return;
    }
    $("#step1").hidden = true;
    $("#stepDone").hidden = false;
    setTimeout(() => {
      location.replace(params.get("then") || "/?pane=availability");
    }, 900);
  } catch {
    msg.textContent = "Could not reach the server.";
  } finally {
    $("#unlockBtn").disabled = false;
  }
});
