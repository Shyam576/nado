// dashboard/static/inbox.js — Inbox page: all captures, filterable, with
// inline correction/status controls. Same vanilla-JS, re-fetch-after-mutation
// pattern as today.js/week.js. Filtering by status/type/project happens
// client-side against one fetched list (/api/captures supports server-side
// filters too, but a personal inbox is small enough that refetching on every
// filter change would just be extra round-trips for no benefit).

let state = { all: [], types: [], statuses: [] };
let filters = { status: "", type: "", project: "" };

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

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function formatType(type) {
  return type.replace(/_/g, " ");
}

function formatRelativeDate(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

async function load() {
  try {
    const data = await api("/api/captures");
    state = { all: data.captures, types: data.types, statuses: data.statuses };
    populateFilters();
    render();
  } catch (err) {
    showError(err.message);
  }
}

function populateFilters() {
  const statusSelect = document.getElementById("filter-status");
  const typeSelect = document.getElementById("filter-type");
  const projectSelect = document.getElementById("filter-project");

  const projects = [...new Set(state.all.map((c) => c.project).filter(Boolean))].sort();

  statusSelect.innerHTML =
    '<option value="">All statuses</option>' +
    state.statuses.map((s) => `<option value="${s}">${formatType(s)}</option>`).join("");
  typeSelect.innerHTML =
    '<option value="">All types</option>' +
    state.types.map((t) => `<option value="${t}">${formatType(t)}</option>`).join("");
  projectSelect.innerHTML =
    '<option value="">All projects</option>' +
    projects.map((p) => `<option value="${escapeHtml(p)}">${escapeHtml(p)}</option>`).join("");

  statusSelect.value = filters.status;
  typeSelect.value = filters.type;
  projectSelect.value = filters.project;
}

function render() {
  const filtered = state.all.filter(
    (c) =>
      (!filters.status || c.status === filters.status) &&
      (!filters.type || c.type === filters.type) &&
      (!filters.project || c.project === filters.project)
  );

  document.getElementById("count-sub").textContent =
    filtered.length === state.all.length
      ? `${state.all.length} total`
      : `${filtered.length} of ${state.all.length}`;

  renderList(filtered);
}

function statusActions(capture) {
  if (capture.status === "archived") {
    return `<button data-action="reopen" data-id="${capture.id}">Reopen</button>`;
  }
  if (capture.status === "completed") {
    return `
      <button data-action="archive" data-id="${capture.id}">Archive</button>
      <button data-action="reopen" data-id="${capture.id}">Reopen</button>
    `;
  }
  return `
    <button data-action="complete" data-id="${capture.id}" class="primary">Done</button>
    <button data-action="archive" data-id="${capture.id}">Archive</button>
  `;
}

function renderList(filtered) {
  const list = document.getElementById("capture-list");
  list.innerHTML = "";

  if (filtered.length === 0) {
    list.innerHTML =
      state.all.length === 0
        ? '<li class="empty">Nothing captured yet — try the box above, or send /capture &lt;text&gt; to the bot.</li>'
        : '<li class="empty">Nothing matches these filters.</li>';
    return;
  }

  filtered.forEach((c) => {
    const li = document.createElement("li");
    li.className = "capture";
    li.innerHTML = `
      <div class="capture-row">
        <span class="capture-text${c.status === "completed" ? " done" : ""}${c.status === "archived" ? " archived" : ""}">${escapeHtml(c.raw_text)}</span>
        <select class="type-select" data-id="${c.id}">
          ${state.types.map((t) => `<option value="${t}" ${t === c.type ? "selected" : ""}>${formatType(t)}</option>`).join("")}
        </select>
      </div>
      <div class="capture-meta">
        <input type="text" class="project-input" data-id="${c.id}" placeholder="Project" value="${c.project ? escapeHtml(c.project) : ""}">
        <input type="date" class="schedule-input" data-id="${c.id}" value="${c.scheduled_for || ""}">
        <span class="muted">${formatRelativeDate(c.created_at)}</span>
      </div>
      <div class="capture-actions">${statusActions(c)}</div>
    `;
    list.appendChild(li);
  });

  list.querySelectorAll(".type-select").forEach((el) => {
    el.addEventListener("change", () => correctCapture(parseInt(el.dataset.id), { type: el.value }));
  });
  list.querySelectorAll(".project-input").forEach((el) => {
    el.addEventListener("blur", () => correctCapture(parseInt(el.dataset.id), { project: el.value.trim() }));
  });
  list.querySelectorAll(".schedule-input").forEach((el) => {
    el.addEventListener("change", () => {
      const id = parseInt(el.dataset.id);
      if (el.value) {
        scheduleCapture(id, el.value);
      } else {
        correctCapture(id, { scheduled_for: "" });
      }
    });
  });
  list.querySelectorAll("[data-action]").forEach((el) => {
    el.addEventListener("click", () => {
      const id = parseInt(el.dataset.id);
      const status = { complete: "completed", archive: "archived", reopen: "inbox" }[el.dataset.action];
      setStatus(id, status);
    });
  });
}

async function quickCapture(event) {
  event.preventDefault();
  const input = document.getElementById("quick-capture-text");
  const raw_text = input.value.trim();
  if (!raw_text) return;
  try {
    await api("/api/captures", { method: "POST", body: JSON.stringify({ raw_text }) });
    input.value = "";
    await load();
  } catch (err) {
    showError(err.message);
  }
}

async function correctCapture(id, fields) {
  try {
    await api(`/api/captures/${id}`, { method: "PATCH", body: JSON.stringify(fields) });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

async function scheduleCapture(id, scheduled_for) {
  try {
    await api(`/api/captures/${id}/schedule`, { method: "POST", body: JSON.stringify({ scheduled_for }) });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

async function setStatus(id, status) {
  try {
    await api(`/api/captures/${id}/status`, { method: "POST", body: JSON.stringify({ status }) });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("quick-capture-form").addEventListener("submit", quickCapture);
  document.getElementById("filter-status").addEventListener("change", (e) => {
    filters.status = e.target.value;
    render();
  });
  document.getElementById("filter-type").addEventListener("change", (e) => {
    filters.type = e.target.value;
    render();
  });
  document.getElementById("filter-project").addEventListener("change", (e) => {
    filters.project = e.target.value;
    render();
  });
  load();
});
