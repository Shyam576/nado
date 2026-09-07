// dashboard/static/week.js — Week page: outcomes, scorecard, weekly review.
// Same vanilla-JS, re-fetch-after-mutation pattern as today.js.

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
  renderReview();
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
  const formEl = document.getElementById("review-form");
  const resultEl = document.getElementById("review-result");

  if (review && !reviewEditMode) {
    formEl.style.display = "none";
    resultEl.style.display = "block";
    document.getElementById("review-summary").innerHTML = `
      <dt>What went well</dt><dd>${review.went_well ? escapeHtml(review.went_well) : "—"}</dd>
      <dt>Failed to follow through on</dt><dd>${review.failed_follow_through ? escapeHtml(review.failed_follow_through) : "—"}</dd>
      <dt>Why</dt><dd>${review.reason ? escapeHtml(review.reason) : "—"}</dd>
      <dt>Pattern noticed</dt><dd>${review.pattern_observed ? escapeHtml(review.pattern_observed) : "—"}</dd>
      <dt>Change for next week</dt><dd>${review.next_week_adjustment ? escapeHtml(review.next_week_adjustment) : "—"}</dd>
    `;
  } else {
    formEl.style.display = "block";
    resultEl.style.display = "none";
    if (review) {
      document.getElementById("went-well").value = review.went_well || "";
      document.getElementById("failed-follow-through").value = review.failed_follow_through || "";
      document.getElementById("reason").value = review.reason || "";
      document.getElementById("pattern-observed").value = review.pattern_observed || "";
      document.getElementById("next-week-adjustment").value = review.next_week_adjustment || "";
    }
  }
}

async function submitReview(event) {
  event.preventDefault();
  const body = {
    went_well: document.getElementById("went-well").value || null,
    failed_follow_through: document.getElementById("failed-follow-through").value || null,
    reason: document.getElementById("reason").value || null,
    pattern_observed: document.getElementById("pattern-observed").value || null,
    next_week_adjustment: document.getElementById("next-week-adjustment").value || null,
  };
  try {
    await api("/api/week/review", { method: "POST", body: JSON.stringify(body) });
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
  document.getElementById("add-outcome-form").addEventListener("submit", addOutcome);
  document.getElementById("review-form").addEventListener("submit", submitReview);
  document.getElementById("edit-review-btn").addEventListener("click", editReview);
  load();
});
