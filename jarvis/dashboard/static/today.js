// dashboard/static/today.js — Today page: morning plan, priorities, evening review.
// Vanilla JS, no build step. Every mutation re-fetches /api/today and re-renders —
// simple and correct for a single-user, low-frequency personal tool.

let state = null;
let reviewEditMode = false;

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

async function load() {
  try {
    state = await api("/api/today");
    render();
  } catch (err) {
    showError(err.message);
  }
}

function render() {
  document.getElementById("date-sub").textContent = formatDate(state.plan.date);
  renderMorningPlan();
  renderPriorities();
  renderReview();
}

function formatDate(iso) {
  const d = new Date(iso + "T00:00:00");
  return d.toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

// --- Morning plan ----------------------------------------------------------

function renderMorningPlan() {
  document.getElementById("target-start-time").value = state.plan.target_start_time || "";
  document.getElementById("must-not-slip").value = state.plan.must_not_slip || "";
}

async function saveMorningPlan(event) {
  event.preventDefault();
  const target_start_time = document.getElementById("target-start-time").value || null;
  const must_not_slip = document.getElementById("must-not-slip").value || null;
  try {
    await api("/api/today/morning-plan", {
      method: "POST",
      body: JSON.stringify({ target_start_time, must_not_slip }),
    });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

// --- Priorities --------------------------------------------------------------

function renderPriorities() {
  const list = document.getElementById("priority-list");
  list.innerHTML = "";

  if (state.priorities.length === 0) {
    list.innerHTML = '<li class="empty">No priorities yet — add your top few for today below.</li>';
    return;
  }

  state.priorities.forEach((p, idx) => {
    const li = document.createElement("li");
    li.className = "priority";

    const orderBtns = document.createElement("div");
    orderBtns.className = "order-btns";
    orderBtns.innerHTML = `
      <button class="ghost" ${idx === 0 ? "disabled" : ""} data-move="up" data-id="${p.id}">▲</button>
      <button class="ghost" ${idx === state.priorities.length - 1 ? "disabled" : ""} data-move="down" data-id="${p.id}">▼</button>
    `;

    const checkbox = document.createElement("input");
    checkbox.type = "checkbox";
    checkbox.checked = p.status === "done";
    checkbox.disabled = p.status === "carried_forward";
    checkbox.addEventListener("change", () =>
      checkbox.checked ? completePriority(p.id) : uncompletePriority(p.id)
    );

    const title = document.createElement("span");
    title.className = "title" + (p.status === "done" ? " done" : "") + (p.status === "carried_forward" ? " carried" : "");
    const badge = idx === 0 && p.status === "pending" ? '<span class="badge">★ Most important</span>' : "";
    title.innerHTML = badge + escapeHtml(p.title) + (p.status === "carried_forward" ? " (carried forward)" : "");
    title.title = "Click to edit";
    title.style.cursor = "pointer";
    title.addEventListener("click", () => editPriority(p));

    const actions = document.createElement("div");
    actions.className = "actions";
    if (p.status === "pending") {
      const carryBtn = document.createElement("button");
      carryBtn.className = "ghost";
      carryBtn.textContent = "Carry forward";
      carryBtn.addEventListener("click", () => carryForwardPriority(p.id));
      actions.appendChild(carryBtn);
    }

    li.appendChild(orderBtns);
    li.appendChild(checkbox);
    li.appendChild(title);
    li.appendChild(actions);
    list.appendChild(li);
  });

  list.querySelectorAll("[data-move]").forEach((btn) => {
    btn.addEventListener("click", () => movePriority(parseInt(btn.dataset.id), btn.dataset.move));
  });
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

async function addPriority(event) {
  event.preventDefault();
  const input = document.getElementById("new-priority-title");
  const title = input.value.trim();
  if (!title) return;
  try {
    await api("/api/today/priorities", { method: "POST", body: JSON.stringify({ title }) });
    input.value = "";
    await load();
  } catch (err) {
    showError(err.message);
  }
}

async function editPriority(priority) {
  const next = window.prompt("Edit priority:", priority.title);
  if (next === null || next.trim() === "" || next.trim() === priority.title) return;
  try {
    await api(`/api/today/priorities/${priority.id}`, {
      method: "PATCH",
      body: JSON.stringify({ title: next.trim() }),
    });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

async function completePriority(id) {
  try {
    await api(`/api/today/priorities/${id}/complete`, { method: "POST" });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

async function uncompletePriority(id) {
  try {
    await api(`/api/today/priorities/${id}/uncomplete`, { method: "POST" });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

async function carryForwardPriority(id) {
  const reason = window.prompt("Why is this being carried forward? (optional)", "");
  if (reason === null) return;
  try {
    await api(`/api/today/priorities/${id}/carry-forward`, {
      method: "POST",
      body: JSON.stringify({ reason: reason || null }),
    });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

async function movePriority(id, direction) {
  const ids = state.priorities.map((p) => p.id);
  const idx = ids.indexOf(id);
  const swapWith = direction === "up" ? idx - 1 : idx + 1;
  if (swapWith < 0 || swapWith >= ids.length) return;
  [ids[idx], ids[swapWith]] = [ids[swapWith], ids[idx]];
  try {
    await api("/api/today/priorities/reorder", {
      method: "POST",
      body: JSON.stringify({ date: state.plan.date, ordered_priority_ids: ids }),
    });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

// --- Evening review ----------------------------------------------------------

function renderReview() {
  const plan = state.plan;
  const formEl = document.getElementById("review-form");
  const resultEl = document.getElementById("review-result");

  if (plan.review_completed && !reviewEditMode) {
    formEl.style.display = "none";
    resultEl.style.display = "block";

    const completed = state.priorities.filter((p) => p.status === "done");
    const carried = state.priorities.filter((p) => p.status === "carried_forward");

    document.getElementById("review-summary").innerHTML = `
      <dt>Actual start time</dt><dd>${plan.actual_start_time || "—"}</dd>
      <dt>Punctual?</dt><dd>${plan.punctual === 1 ? "Yes" : plan.punctual === 0 ? "No" : "—"}</dd>
      <dt>Worked by priority?</dt><dd>${plan.worked_by_priority === 1 ? "Yes, followed the plan" : plan.worked_by_priority === 0 ? "No, mostly reactive" : "—"}</dd>
      <dt>Priorities completed</dt><dd>${completed.length ? completed.map((p) => escapeHtml(p.title)).join(", ") : "None"}</dd>
      <dt>Carried forward</dt><dd>${carried.length ? carried.map((p) => `${escapeHtml(p.title)}${p.carry_forward_reason ? " — " + escapeHtml(p.carry_forward_reason) : ""}`).join("; ") : "None"}</dd>
      <dt>Execution score</dt><dd>${plan.execution_score !== null ? plan.execution_score + " / 10" : "—"}</dd>
      <dt>Adjustment for tomorrow</dt><dd>${plan.adjustment_for_tomorrow ? escapeHtml(plan.adjustment_for_tomorrow) : "—"}</dd>
    `;
  } else {
    formEl.style.display = "block";
    resultEl.style.display = "none";

    document.getElementById("actual-start-time").value = plan.actual_start_time || "";
    setPill("punctual", plan.punctual);
    setPill("worked-by-priority", plan.worked_by_priority);
    document.getElementById("execution-score").value = plan.execution_score || 5;
    document.getElementById("execution-score-value").textContent = plan.execution_score || 5;
    document.getElementById("adjustment-tomorrow").value = plan.adjustment_for_tomorrow || "";
  }
}

function setPill(group, value) {
  document.querySelectorAll(`[data-pill-group="${group}"]`).forEach((el) => {
    const isYes = el.dataset.pillValue === "yes";
    el.classList.toggle("selected", (value === 1 && isYes) || (value === 0 && !isYes));
    el.classList.toggle("yes", isYes);
    el.classList.toggle("no", !isYes);
  });
}

function wirePills() {
  document.querySelectorAll("[data-pill-group]").forEach((el) => {
    el.addEventListener("click", () => {
      const group = el.dataset.pillGroup;
      const isYes = el.dataset.pillValue === "yes";
      document.querySelectorAll(`[data-pill-group="${group}"]`).forEach((sibling) => {
        sibling.classList.remove("selected");
      });
      el.classList.add("selected", isYes ? "yes" : "no");
      el.dataset.selected = "true";
    });
  });
}

function pillValue(group) {
  const selected = document.querySelector(`[data-pill-group="${group}"].selected`);
  if (!selected) return null;
  return selected.dataset.pillValue === "yes";
}

async function submitReview(event) {
  event.preventDefault();
  const body = {
    actual_start_time: document.getElementById("actual-start-time").value || null,
    punctual: pillValue("punctual"),
    worked_by_priority: pillValue("worked-by-priority"),
    execution_score: parseInt(document.getElementById("execution-score").value),
    adjustment_for_tomorrow: document.getElementById("adjustment-tomorrow").value || null,
  };
  try {
    await api("/api/today/evening-review", { method: "POST", body: JSON.stringify(body) });
    reviewEditMode = false;
    await load();
  } catch (err) {
    showError(err.message);
  }
}

function editReview() {
  reviewEditMode = true;
  render();
}

document.addEventListener("DOMContentLoaded", () => {
  wirePills();
  document.getElementById("morning-plan-form").addEventListener("submit", saveMorningPlan);
  document.getElementById("add-priority-form").addEventListener("submit", addPriority);
  document.getElementById("review-form").addEventListener("submit", submitReview);
  document.getElementById("edit-review-btn").addEventListener("click", editReview);
  document.getElementById("execution-score").addEventListener("input", (e) => {
    document.getElementById("execution-score-value").textContent = e.target.value;
  });
  load();
});
