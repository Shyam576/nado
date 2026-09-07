// dashboard/static/mentorship.js — Mentorship page: development action, 5W2H, mentor summary.

async function api(path, options) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.status === 204 ? null : res.json();
}

function showError(message) {
  const banner = document.getElementById("error-banner");
  banner.textContent = message;
  banner.style.display = "block";
  setTimeout(() => (banner.style.display = "none"), 5000);
}

async function loadContext() {
  try {
    const ctx = await api("/api/mentorship-context");
    document.getElementById("development-action").textContent =
      ctx.development_action || "No active development cycle yet — start one on the Progress page.";
    document.getElementById("ctx-what").value = ctx.what || "";
    document.getElementById("ctx-why").value = ctx.why || "";
    document.getElementById("ctx-when").value = ctx.when || "";
    document.getElementById("ctx-who").value = ctx.who || "";
    document.getElementById("ctx-where").value = ctx.where || "";
    document.getElementById("ctx-how").value = ctx.how || "";
    document.getElementById("ctx-how-much").value = ctx.how_much || "";
  } catch (err) {
    showError(err.message);
  }
}

async function saveContext(event) {
  event.preventDefault();
  const body = {
    what: document.getElementById("ctx-what").value || null,
    why: document.getElementById("ctx-why").value || null,
    when: document.getElementById("ctx-when").value || null,
    who: document.getElementById("ctx-who").value || null,
    where: document.getElementById("ctx-where").value || null,
    how: document.getElementById("ctx-how").value || null,
    how_much: document.getElementById("ctx-how-much").value || null,
  };
  try {
    await api("/api/mentorship-context", { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    showError(err.message);
  }
}

async function generateSummary() {
  const btn = document.getElementById("generate-summary-btn");
  const target = document.getElementById("summary-text");
  btn.disabled = true;
  btn.textContent = "Generating...";
  target.textContent = "";
  try {
    const result = await api("/api/mentor-summary");
    target.textContent = result.summary;
  } catch (err) {
    showError(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Generate summary";
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("context-form").addEventListener("submit", saveContext);
  document.getElementById("generate-summary-btn").addEventListener("click", generateSummary);
  loadContext();
});
