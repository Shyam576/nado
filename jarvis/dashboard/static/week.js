// dashboard/static/week.js — Week page: outcomes, scorecard, weekly review.
// Same vanilla-JS, re-fetch-after-mutation pattern as today.js.

let state = null;

// --- Weekly review wizard state ---------------------------------------------
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

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function formatDateRange(start, end) {
  const opts = { month: "short", day: "numeric" };
  const s = new Date(start + "T00:00:00").toLocaleDateString(undefined, opts);
  const e = new Date(end + "T00:00:00").toLocaleDateString(undefined, opts);
  return `${s} – ${e}`;
}

async function load() {
  try {
    state = await api("/api/week");
    render();
  } catch (err) {
    showError(err.message);
  }
}

function render() {
  document.getElementById("date-sub").textContent = formatDateRange(state.week_start, state.week_end);
  renderOutcomes();
  renderScorecard();
  if (!wizardActive) {
    renderReview();
  }
}

// --- Outcomes ----------------------------------------------------------------

function renderOutcomes() {
  const list = document.getElementById("outcome-list");
  list.innerHTML = "";

  if (state.outcomes.length === 0) {
    list.innerHTML = '<li class="empty">No outcomes set yet — add 3-5 things that matter most this week.</li>';
    return;
  }

  state.outcomes.forEach((o) => {
    const li = document.createElement("li");
    li.className = "outcome";

    const related = o.related_priorities.length
      ? `<ul class="related">${o.related_priorities
          .map((p) => `<li>${escapeHtml(p.title)} (${p.status.replace("_", " ")})</li>`)
          .join("")}</ul>`
      : "";
    const meta = o.completed_at ? `<div class="meta">Completed ${formatDateTime(o.completed_at)}</div>` : "";

    li.innerHTML = `
      <div class="outcome-row">
        <span class="title${o.status === "done" ? " done" : ""}">${escapeHtml(o.title)}</span>
        ${o.carried_forward ? '<span class="badge carried">Carried forward</span>' : ""}
        <select data-outcome-id="${o.id}">
          <option value="pending" ${o.status === "pending" ? "selected" : ""}>Pending</option>
          <option value="in_progress" ${o.status === "in_progress" ? "selected" : ""}>In progress</option>
          <option value="done" ${o.status === "done" ? "selected" : ""}>Done</option>
          <option value="dropped" ${o.status === "dropped" ? "selected" : ""}>Dropped</option>
        </select>
        <label style="display:flex;align-items:center;gap:.3rem;font-size:.78rem;color:var(--muted);margin:0">
          <input type="checkbox" data-carry-id="${o.id}" ${o.carried_forward ? "checked" : ""}> carried
        </label>
      </div>
      ${meta}
      ${related}
    `;
    list.appendChild(li);
  });

  list.querySelectorAll("select[data-outcome-id]").forEach((el) => {
    el.addEventListener("change", () => updateOutcome(parseInt(el.dataset.outcomeId), el.value, null));
  });
  list.querySelectorAll("input[data-carry-id]").forEach((el) => {
    el.addEventListener("change", () => {
      const outcome = state.outcomes.find((o) => o.id === parseInt(el.dataset.carryId));
      updateOutcome(outcome.id, outcome.status, el.checked);
    });
  });
}

function formatDateTime(iso) {
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

async function addOutcome(event) {
  event.preventDefault();
  const input = document.getElementById("new-outcome-title");
  const title = input.value.trim();
  if (!title) return;
  try {
    await api("/api/week/outcomes", { method: "POST", body: JSON.stringify({ title }) });
    input.value = "";
    await load();
  } catch (err) {
    showError(err.message);
  }
}

async function updateOutcome(id, status, carriedForward) {
  const body = { status };
  if (carriedForward !== null) body.carried_forward = carriedForward;
  try {
    await api(`/api/week/outcomes/${id}`, { method: "PATCH", body: JSON.stringify(body) });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

// --- Scorecard -----------------------------------------------------------------

function renderScorecard() {
  const s = state.scorecard;
  const pct = (v) => (v === null ? "—" : `${Math.round(v)}%`);
  document.getElementById("scorecard").innerHTML = `
    <dt>Punctuality</dt><dd>${pct(s.punctuality_pct)}</dd>
    <dt>Priority completion</dt><dd>${pct(s.completion_pct)}</dd>
    <dt>Carried-forward items</dt><dd>${s.carry_forward_count}</dd>
    <dt>Daily reviews</dt><dd>${s.reviews_completed}/${s.days_elapsed}</dd>
    <dt>Weekly review</dt><dd>${s.weekly_review_done ? "✓" : "—"}</dd>
  `;
}

// --- Weekly review ---------------------------------------------------------------

function renderReview() {
  const review = state.review;
  const startEl = document.getElementById("review-start");
  const wizardEl = document.getElementById("review-wizard");
  const resultEl = document.getElementById("review-result");

  if (review) {
    startEl.style.display = "none";
    wizardEl.style.display = "none";
    resultEl.style.display = "block";
    document.getElementById("review-summary").innerHTML = `
      <dt>What went well</dt><dd>${review.went_well ? escapeHtml(review.went_well) : "—"}</dd>
      <dt>Failed to follow through on</dt><dd>${review.failed_follow_through ? escapeHtml(review.failed_follow_through) : "—"}</dd>
      <dt>Why</dt><dd>${review.reason ? escapeHtml(review.reason) : "—"}</dd>
      <dt>Pattern noticed</dt><dd>${review.pattern_observed ? escapeHtml(review.pattern_observed) : "—"}</dd>
      <dt>Change for next week</dt><dd>${review.next_week_adjustment ? escapeHtml(review.next_week_adjustment) : "—"}</dd>
    `;
  } else {
    startEl.style.display = "block";
    wizardEl.style.display = "none";
    resultEl.style.display = "none";
  }
}

// --- Weekly review wizard: one question at a time. Chips for "what did I
// fail to follow through on" and "why" are seeded from what actually
// carried forward this week (modules/execution.py's
// get_weekly_review_suggestions()) instead of composed from scratch. -------

function buildWizardSteps() {
  return ["went-well", "failed-follow-through", "reason", "pattern-observed", "next-week-adjustment", "confirm"].map(
    (type) => ({ type })
  );
}

async function startWizard() {
  const review = state.review;
  try {
    wizardSuggestions = await api(`/api/week/review-suggestions?week_start=${state.week_start}`);
  } catch (err) {
    showError(err.message);
    wizardSuggestions = { incomplete_items: { outcomes: [], priorities: [] }, carry_forward_reasons: [], common_adjustment: null };
  }

  wizardAnswers = review
    ? {
        went_well: review.went_well,
        failed_follow_through: review.failed_follow_through,
        reason: review.reason,
        pattern_observed: review.pattern_observed,
        next_week_adjustment: review.next_week_adjustment,
      }
    : {
        went_well: null,
        failed_follow_through: null,
        reason: null,
        pattern_observed: null,
        next_week_adjustment: null,
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

  document.getElementById("wizard-progress").textContent = `Step ${wizardIndex + 1} of ${wizardSteps.length}`;
  document.getElementById("wizard-back").disabled = wizardIndex === 0;
  document.getElementById("wizard-next").textContent = step.type === "confirm" ? "Submit weekly review" : "Next";

  const renderers = {
    "went-well": () => renderTextStep(container, "What went well this week?", "went_well"),
    "failed-follow-through": renderFailedFollowThroughStep,
    reason: renderReasonStep,
    "pattern-observed": () => renderTextStep(container, "What pattern am I noticing?", "pattern_observed"),
    "next-week-adjustment": renderNextWeekAdjustmentStep,
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

function renderTextStep(container, label, field) {
  container.innerHTML = `<label for="wizard-text">${escapeHtml(label)}</label><textarea id="wizard-text"></textarea>`;
  const textarea = document.getElementById("wizard-text");
  textarea.value = wizardAnswers[field] || "";
  textarea.addEventListener("input", () => {
    wizardAnswers[field] = textarea.value || null;
  });
}

function renderFailedFollowThroughStep(container) {
  const items = [...wizardSuggestions.incomplete_items.outcomes, ...wizardSuggestions.incomplete_items.priorities];
  container.innerHTML = `
    <label>What did I fail to follow through on?</label>
    ${items.length ? '<div class="chip-group" id="ffc-chips"></div>' : ""}
    <textarea id="wizard-text" placeholder="${items.length ? "Tap items above to add them, or type your own" : "e.g. Architecture doc, client follow-up"}"></textarea>
  `;
  const textarea = document.getElementById("wizard-text");
  textarea.value = wizardAnswers.failed_follow_through || "";
  textarea.addEventListener("input", () => {
    wizardAnswers.failed_follow_through = textarea.value || null;
  });

  if (items.length) {
    const chipsEl = document.getElementById("ffc-chips");
    items.forEach((item) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = item.title;
      chip.addEventListener("click", () => {
        const current = textarea.value.trim();
        textarea.value = current ? `${current}, ${item.title}` : item.title;
        wizardAnswers.failed_follow_through = textarea.value;
        chip.classList.add("selected");
      });
      chipsEl.appendChild(chip);
    });
  }
}

function renderReasonStep(container) {
  const reasons = wizardSuggestions.carry_forward_reasons;
  container.innerHTML = `
    <label>Why?</label>
    ${reasons.length ? '<div class="chip-group" id="reason-chips"></div>' : ""}
    <textarea id="wizard-text" placeholder="What got in the way?"></textarea>
  `;
  const textarea = document.getElementById("wizard-text");
  textarea.value = wizardAnswers.reason || "";
  textarea.addEventListener("input", () => {
    wizardAnswers.reason = textarea.value || null;
  });

  if (reasons.length) {
    const chipsEl = document.getElementById("reason-chips");
    reasons.forEach((r) => {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.innerHTML = `${escapeHtml(r.reason)}${r.count ? `<span class="chip-count">×${r.count}</span>` : ""}`;
      chip.addEventListener("click", () => {
        const alreadySelected = chip.classList.contains("selected");
        chipsEl.querySelectorAll(".chip").forEach((c) => c.classList.remove("selected"));
        if (alreadySelected) {
          textarea.value = "";
        } else {
          chip.classList.add("selected");
          textarea.value = r.reason;
        }
        wizardAnswers.reason = textarea.value || null;
      });
      chipsEl.appendChild(chip);
    });
  }
}

function renderNextWeekAdjustmentStep(container) {
  const common = wizardSuggestions.common_adjustment;
  container.innerHTML = `
    <label>What ONE thing will I change next week?</label>
    ${common ? '<div class="chip-group" id="adjustment-chips"></div>' : ""}
    <textarea id="wizard-text"></textarea>
  `;
  const textarea = document.getElementById("wizard-text");
  textarea.value = wizardAnswers.next_week_adjustment || "";
  textarea.addEventListener("input", () => {
    wizardAnswers.next_week_adjustment = textarea.value || null;
  });

  if (common) {
    const chipsEl = document.getElementById("adjustment-chips");
    const chip = document.createElement("span");
    chip.className = "chip";
    chip.textContent = `${common} (came up all week)`;
    chip.addEventListener("click", () => {
      textarea.value = common;
      wizardAnswers.next_week_adjustment = common;
      chip.classList.add("selected");
    });
    chipsEl.appendChild(chip);
  }
}

function renderConfirmStep(container) {
  const a = wizardAnswers;
  container.innerHTML = `
    <p class="wizard-confirm-title">Ready to submit?</p>
    <dl class="review-summary">
      <dt>What went well</dt><dd>${a.went_well ? escapeHtml(a.went_well) : "—"}</dd>
      <dt>Failed to follow through on</dt><dd>${a.failed_follow_through ? escapeHtml(a.failed_follow_through) : "—"}</dd>
      <dt>Why</dt><dd>${a.reason ? escapeHtml(a.reason) : "—"}</dd>
      <dt>Pattern noticed</dt><dd>${a.pattern_observed ? escapeHtml(a.pattern_observed) : "—"}</dd>
      <dt>Change for next week</dt><dd>${a.next_week_adjustment ? escapeHtml(a.next_week_adjustment) : "—"}</dd>
    </dl>
  `;
}

async function wizardSubmit() {
  try {
    await api("/api/week/review", { method: "POST", body: JSON.stringify({ ...wizardAnswers, week_start: state.week_start }) });
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
  document.getElementById("add-outcome-form").addEventListener("submit", addOutcome);
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
});
