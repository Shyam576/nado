// dashboard/static/today.js — Today page: morning plan, priorities, evening review.
// Vanilla JS, no build step. Every mutation re-fetches /api/today and re-renders —
// simple and correct for a single-user, low-frequency personal tool.

let state = null;

// --- Evening review wizard state -------------------------------------------
let wizardActive = false;
let wizardSteps = [];
let wizardIndex = 0;
let wizardAnswers = {};
let wizardSuggestions = null;

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
  renderHero();
  renderMorningPlan();
  renderPriorities();
  if (!wizardActive) {
    renderReview();
  }
}

// --- Hero zone: must-not-slip + top priority, nothing else competing for attention ---

function renderHero() {
  const slipEl = document.getElementById("hero-must-not-slip");
  if (state.plan.must_not_slip) {
    slipEl.innerHTML =
      '<span class="hero-slip-label">Must not slip</span>' + escapeHtml(state.plan.must_not_slip);
    slipEl.style.display = "block";
  } else {
    slipEl.style.display = "none";
  }

  const priorityEl = document.getElementById("hero-top-priority");
  const topPending = state.priorities.find((p) => p.status === "pending");
  if (topPending) {
    priorityEl.innerHTML = '<span class="star">★</span>' + escapeHtml(topPending.title);
    priorityEl.style.display = "block";
  } else {
    priorityEl.style.display = "none";
  }

  document.getElementById("hero-empty").style.display =
    !state.plan.must_not_slip && !topPending ? "block" : "none";
}

// --- Context panel: pending tasks + today's calendar, read-only ---------------

async function loadContext() {
  try {
    const context = await api("/api/today/context");
    renderContextList("context-tasks", context.pending_tasks, (t) => escapeHtml(t.title), "No pending tasks.");
    if (context.calendar_events === null) {
      renderContextMessage("context-calendar", "Calendar not available.");
    } else {
      renderContextList(
        "context-calendar",
        context.calendar_events,
        (e) => `<span class="time">${escapeHtml(e.time)}</span>${escapeHtml(e.summary)}`,
        "Nothing on the calendar today."
      );
    }
  } catch {
    renderContextMessage("context-tasks", "Couldn't load.");
    renderContextMessage("context-calendar", "Couldn't load.");
  }
}

function renderContextList(elementId, items, formatItem, emptyMessage) {
  const el = document.getElementById(elementId);
  if (!items || items.length === 0) {
    renderContextMessage(elementId, emptyMessage);
    return;
  }
  el.innerHTML = items.map((item) => `<li>${formatItem(item)}</li>`).join("");
}

function renderContextMessage(elementId, message) {
  document.getElementById(elementId).innerHTML = `<li class="muted">${escapeHtml(message)}</li>`;
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

async function carryForwardPriority(id, presetReason) {
  let reason = presetReason;
  if (reason === undefined) {
    reason = window.prompt("Why is this being carried forward? (optional)", "");
    if (reason === null) return;
  }
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
  const startEl = document.getElementById("review-start");
  const wizardEl = document.getElementById("review-wizard");
  const resultEl = document.getElementById("review-result");

  if (plan.review_completed) {
    startEl.style.display = "none";
    wizardEl.style.display = "none";
    resultEl.style.display = "block";
    renderReviewResult(plan);
  } else {
    startEl.style.display = "block";
    wizardEl.style.display = "none";
    resultEl.style.display = "none";
  }
}

function renderReviewResult(plan) {
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
}

// --- Evening review wizard: one question at a time, pre-filled where the data
// already says something (auto-computed punctuality, a suggested score, an
// LLM-drafted adjustment) — the user confirms or overrides, never composes
// from scratch. See modules/execution.py's get_review_suggestions(). --------

function buildWizardSteps() {
  const pendingPriorities = state.priorities.filter((p) => p.status === "pending");
  return [
    { type: "start-time" },
    { type: "punctual" },
    ...pendingPriorities.map((p) => ({ type: "priority", priority: p })),
    { type: "worked-by-priority" },
    { type: "score" },
    { type: "adjustment" },
    { type: "confirm" },
  ];
}

async function startWizard() {
  const plan = state.plan;
  let suggestions;
  try {
    suggestions = await api("/api/today/review-suggestions");
  } catch (err) {
    showError(err.message);
    suggestions = {
      suggested_actual_start_time: null,
      suggested_punctual: null,
      suggested_score: null,
      suggested_adjustment: "",
      carry_forward_reasons: [],
    };
  }
  wizardSuggestions = suggestions;

  // Editing an already-completed review re-opens with what was actually
  // submitted, not fresh algorithmic guesses; a first-time review starts
  // from the suggestions.
  wizardAnswers = plan.review_completed
    ? {
        actual_start_time: plan.actual_start_time,
        punctual: plan.punctual === null ? null : plan.punctual === 1,
        worked_by_priority: plan.worked_by_priority === null ? null : plan.worked_by_priority === 1,
        execution_score: plan.execution_score,
        adjustment_for_tomorrow: plan.adjustment_for_tomorrow,
      }
    : {
        actual_start_time: suggestions.suggested_actual_start_time,
        punctual: suggestions.suggested_punctual,
        worked_by_priority: null,
        execution_score: suggestions.suggested_score || 5,
        adjustment_for_tomorrow: suggestions.suggested_adjustment,
      };

  wizardActive = true;
  wizardIndex = 0;
  wizardSteps = buildWizardSteps();

  document.getElementById("review-start").style.display = "none";
  document.getElementById("review-result").style.display = "none";
  document.getElementById("review-wizard").style.display = "block";
  renderWizardStep();
}

function renderWizardStep() {
  const step = wizardSteps[wizardIndex];
  const container = document.getElementById("wizard-step");
  const nextBtn = document.getElementById("wizard-next");

  document.getElementById("wizard-progress").textContent = `Step ${wizardIndex + 1} of ${wizardSteps.length}`;
  document.getElementById("wizard-back").disabled = wizardIndex === 0;
  nextBtn.style.display = step.type === "priority" ? "none" : "inline-block";
  nextBtn.textContent = step.type === "confirm" ? "Submit review" : "Next";

  const renderers = {
    "start-time": renderStartTimeStep,
    punctual: renderPunctualStep,
    priority: (el) => renderPriorityStep(el, step.priority),
    "worked-by-priority": renderWorkedByPriorityStep,
    score: renderScoreStep,
    adjustment: renderAdjustmentStep,
    confirm: renderConfirmStep,
  };
  renderers[step.type](container);
}

function wizardAdvance() {
  wizardIndex++;
  renderWizardStep();
}

function wizardBack() {
  if (wizardIndex > 0) {
    wizardIndex--;
    renderWizardStep();
  }
}

function wireWizardPills(container, currentValue, onSelect) {
  const pills = container.querySelectorAll(".pill");
  pills.forEach((el) => {
    const isYes = el.dataset.value === "yes";
    el.classList.toggle("selected", currentValue === isYes);
    el.classList.toggle("yes", isYes);
    el.classList.toggle("no", !isYes);
    el.addEventListener("click", () => {
      pills.forEach((p) => p.classList.remove("selected"));
      el.classList.add("selected");
      onSelect(isYes);
    });
  });
}

function renderStartTimeStep(container) {
  container.innerHTML = `
    <label for="wizard-start-time">Actual start/arrival time</label>
    <input type="time" id="wizard-start-time">
    ${wizardSuggestions.suggested_actual_start_time ? '<p class="muted" style="font-size:.78rem;margin-top:.4rem">Suggested from your earliest activity today — adjust if that\'s wrong.</p>' : ""}
  `;
  const input = document.getElementById("wizard-start-time");
  input.value = wizardAnswers.actual_start_time || "";
  input.addEventListener("input", () => {
    wizardAnswers.actual_start_time = input.value || null;
  });
}

function renderPunctualStep(container) {
  container.innerHTML = `
    <label>Was I punctual?</label>
    <div class="pill-group">
      <div class="pill" data-value="yes">Yes</div>
      <div class="pill" data-value="no">No</div>
    </div>
  `;
  wireWizardPills(container, wizardAnswers.punctual, (v) => {
    wizardAnswers.punctual = v;
  });
}

function renderPriorityStep(container, priority) {
  container.innerHTML = `
    <label>Did you finish "${escapeHtml(priority.title)}"?</label>
    <div class="pill-group">
      <div class="pill" data-value="yes">Yes, done</div>
      <div class="pill" data-value="no">No</div>
    </div>
    <div id="carry-forward-chips" style="display:none;margin-top:1rem">
      <label>Why? (carries it forward to tomorrow)</label>
      <div class="chip-group" id="reason-chips"></div>
      <input type="text" id="reason-freeform" placeholder="Or type your own reason, then press Enter">
    </div>
  `;
  container.querySelector('[data-value="yes"]').addEventListener("click", async (e) => {
    e.target.closest(".pill-group").querySelectorAll(".pill").forEach((p) => p.classList.remove("selected"));
    e.target.classList.add("selected");
    await completePriority(priority.id);
    wizardAdvance();
  });
  container.querySelector('[data-value="no"]').addEventListener("click", (e) => {
    e.target.closest(".pill-group").querySelectorAll(".pill").forEach((p) => p.classList.remove("selected"));
    e.target.classList.add("selected");
    document.getElementById("carry-forward-chips").style.display = "block";
    renderReasonChips(priority);
  });
}

function renderReasonChips(priority) {
  const chipsEl = document.getElementById("reason-chips");
  chipsEl.innerHTML = "";
  wizardSuggestions.carry_forward_reasons.forEach((reason) => {
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = reason;
    chip.addEventListener("click", async () => {
      await carryForwardPriority(priority.id, reason);
      wizardAdvance();
    });
    chipsEl.appendChild(chip);
  });

  const freeform = document.getElementById("reason-freeform");
  freeform.addEventListener("keydown", async (e) => {
    if (e.key === "Enter" && freeform.value.trim()) {
      e.preventDefault();
      await carryForwardPriority(priority.id, freeform.value.trim());
      wizardAdvance();
    }
  });
}

function renderWorkedByPriorityStep(container) {
  container.innerHTML = `
    <label>Did you work according to priorities, or mostly react to incoming work?</label>
    <div class="pill-group">
      <div class="pill" data-value="yes">Followed the plan</div>
      <div class="pill" data-value="no">Mostly reactive</div>
    </div>
  `;
  wireWizardPills(container, wizardAnswers.worked_by_priority, (v) => {
    wizardAnswers.worked_by_priority = v;
  });
}

function renderScoreStep(container) {
  const value = wizardAnswers.execution_score || 5;
  container.innerHTML = `
    <label for="wizard-score">Daily execution score</label>
    <div class="score-row">
      <input type="range" id="wizard-score" min="1" max="10" value="${value}">
      <span class="score-value" id="wizard-score-value">${value}</span>
    </div>
    <p class="muted" style="font-size:.78rem;margin-top:.5rem">
      Suggested from today's completion rate and punctuality — drag to adjust.
    </p>
  `;
  const slider = document.getElementById("wizard-score");
  slider.addEventListener("input", () => {
    wizardAnswers.execution_score = parseInt(slider.value);
    document.getElementById("wizard-score-value").textContent = slider.value;
  });
}

function renderAdjustmentStep(container) {
  container.innerHTML = `
    <label for="wizard-adjustment">One adjustment for tomorrow</label>
    <textarea id="wizard-adjustment" placeholder="e.g. Start 15 minutes earlier"></textarea>
    <p class="muted" style="font-size:.78rem;margin-top:.4rem">Drafted from today's pattern — edit freely, or leave as-is.</p>
  `;
  const textarea = document.getElementById("wizard-adjustment");
  textarea.value = wizardAnswers.adjustment_for_tomorrow || "";
  textarea.addEventListener("input", () => {
    wizardAnswers.adjustment_for_tomorrow = textarea.value || null;
  });
}

function renderConfirmStep(container) {
  const a = wizardAnswers;
  container.innerHTML = `
    <p class="wizard-confirm-title">Ready to submit?</p>
    <dl class="review-summary">
      <dt>Actual start time</dt><dd>${a.actual_start_time || "—"}</dd>
      <dt>Punctual?</dt><dd>${a.punctual === true ? "Yes" : a.punctual === false ? "No" : "—"}</dd>
      <dt>Worked by priority?</dt><dd>${a.worked_by_priority === true ? "Yes" : a.worked_by_priority === false ? "No, mostly reactive" : "—"}</dd>
      <dt>Execution score</dt><dd>${a.execution_score !== null && a.execution_score !== undefined ? a.execution_score + " / 10" : "—"}</dd>
      <dt>Adjustment for tomorrow</dt><dd>${a.adjustment_for_tomorrow ? escapeHtml(a.adjustment_for_tomorrow) : "—"}</dd>
    </dl>
  `;
}

async function wizardSubmit() {
  try {
    await api("/api/today/evening-review", { method: "POST", body: JSON.stringify(wizardAnswers) });
    wizardActive = false;
    await load();
  } catch (err) {
    showError(err.message);
  }
}

function editReview() {
  startWizard();
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("morning-plan-form").addEventListener("submit", saveMorningPlan);
  document.getElementById("add-priority-form").addEventListener("submit", addPriority);
  document.getElementById("start-review-btn").addEventListener("click", startWizard);
  document.getElementById("edit-review-btn").addEventListener("click", editReview);
  document.getElementById("wizard-back").addEventListener("click", wizardBack);
  document.getElementById("wizard-next").addEventListener("click", () => {
    const step = wizardSteps[wizardIndex];
    if (step.type === "confirm") {
      wizardSubmit();
    } else {
      wizardAdvance();
    }
  });
  load();
  loadContext();
});
