// dashboard/static/progress.js — Progress page: cycle header, trend charts, table.

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

function formatDate(iso) {
  return new Date(iso + "T00:00:00").toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

async function load() {
  try {
    const cycle = await api("/api/cycle/active");
    if (!cycle) {
      document.getElementById("no-cycle").style.display = "block";
      document.getElementById("cycle-content").style.display = "none";
      document.getElementById("date-sub").textContent = "No development cycle started yet";
      return;
    }
    document.getElementById("no-cycle").style.display = "none";
    document.getElementById("cycle-content").style.display = "block";
    document.getElementById("date-sub").textContent = `${formatDate(cycle.start_date)} – ${formatDate(cycle.end_date)}`;

    const progress = await api("/api/progress");
    renderCycleHeader(cycle, progress.weeks);
    renderCharts(progress.weeks);
    renderTable(progress.weeks);
  } catch (err) {
    showError(err.message);
  }
}

function renderCycleHeader(cycle, weeks) {
  document.getElementById("cycle-title-display").textContent = cycle.title;
  const totalWeeks = Math.ceil(
    (new Date(cycle.end_date) - new Date(cycle.start_date)) / (7 * 24 * 3600 * 1000)
  ) + 1;
  document.getElementById("cycle-meta-display").textContent = `Week ${weeks.length} of ${totalWeeks}`;
}

function renderCharts(weeks) {
  const labels = weeks.map((_, i) => `W${i + 1}`);
  const rootStyle = getComputedStyle(document.querySelector(".viz-root"));
  const series1 = rootStyle.getPropertyValue("--series-1").trim();
  const series2 = rootStyle.getPropertyValue("--series-2").trim();
  const series3 = rootStyle.getPropertyValue("--series-3").trim();

  const pctSeries = [
    { name: "Punctuality", color: series1, values: weeks.map((w) => w.punctuality_pct) },
    { name: "Completion", color: series2, values: weeks.map((w) => w.completion_pct) },
    { name: "Review consistency", color: series3, values: weeks.map((w) => w.review_consistency_pct) },
  ];
  renderLineChart(document.getElementById("chart-percentages"), labels, pctSeries, { min: 0, max: 100, unit: "%" });
  renderLegend(document.getElementById("legend-percentages"), pctSeries);

  renderLineChart(
    document.getElementById("chart-carry-forward"),
    labels,
    [{ name: "Carried forward", color: series1, values: weeks.map((w) => w.carry_forward_count) }],
    { min: 0, unit: "" }
  );

  renderLineChart(
    document.getElementById("chart-execution-score"),
    labels,
    [{ name: "Avg score", color: series1, values: weeks.map((w) => w.avg_execution_score) }],
    { min: 0, max: 10, unit: "", valueFormat: (v) => v.toFixed(1) }
  );
}

function renderLegend(container, series) {
  container.innerHTML = "";
  series.forEach((s) => {
    const item = document.createElement("span");
    item.className = "chart-legend-item";
    const key = document.createElement("span");
    key.className = "chart-legend-key";
    key.style.background = s.color;
    const name = document.createElement("span");
    name.textContent = s.name;
    item.appendChild(key);
    item.appendChild(name);
    container.appendChild(item);
  });
}

function renderTable(weeks) {
  const pct = (v) => (v === null || v === undefined ? "—" : `${Math.round(v)}%`);
  const body = document.getElementById("progress-table-body");
  body.innerHTML = "";
  weeks.forEach((w, i) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td>W${i + 1}</td>
      <td>${pct(w.punctuality_pct)}</td>
      <td>${pct(w.completion_pct)}</td>
      <td>${w.carry_forward_count}</td>
      <td>${w.reviews_completed}/${w.days_elapsed}</td>
      <td>${w.avg_execution_score === null || w.avg_execution_score === undefined ? "—" : w.avg_execution_score.toFixed(1)}</td>
    `;
    body.appendChild(tr);
  });
}

async function startCycle(event) {
  event.preventDefault();
  const body = {
    title: document.getElementById("cycle-title").value.trim(),
    start_date: document.getElementById("cycle-start").value.trim(),
    end_date: document.getElementById("cycle-end").value.trim(),
    development_action: document.getElementById("cycle-action").value.trim() || null,
  };
  try {
    await api("/api/cycle", { method: "POST", body: JSON.stringify(body) });
    await load();
  } catch (err) {
    showError(err.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("start-cycle-form").addEventListener("submit", startCycle);
  load();
});
