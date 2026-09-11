// dashboard/static/life.js — Life page: read-only glance widgets.
// No write paths anywhere on this page — money/mood/people/tasks are all
// owned by chat/voice input already; this page only visualizes.

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail || `Request failed (${res.status})`);
  }
  return res.json();
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

// --- Money ---------------------------------------------------------------

async function loadMoney() {
  try {
    const money = await api("/api/money");
    renderMoneyTiles(money);
    if (money.budget !== null) {
      renderMeter(document.getElementById("budget-meter"), money.pct_used, {
        label: `${money.month_total.toLocaleString()} / ${money.budget.toLocaleString()} BTN (${Math.round(money.pct_used)}%)`,
      });
    } else {
      document.getElementById("budget-meter").innerHTML = '<p class="empty">No budget set.</p>';
    }
    renderBarChart(
      document.getElementById("spend-chart"),
      money.by_category.map((c) => ({ label: c.category, value: c.total })),
      { unit: "" }
    );
  } catch (err) {
    showError(err.message);
  }
}

function renderMoneyTiles(money) {
  const tiles = [];
  tiles.push(tile("This month", `${money.month_total.toLocaleString()} BTN`));
  if (money.gold) {
    tiles.push(tile("Gold (USD/oz)", `$${money.gold.price.toLocaleString()}`, money.gold.change_24h_pct));
  }
  if (money.ter && money.ter.btn) {
    tiles.push(tile("TER/BTN", money.ter.btn.ask.toFixed(2), money.ter.btn.change_24h_pct));
  }
  document.getElementById("money-tiles").innerHTML = tiles.join("");
}

function tile(label, value, deltaPct) {
  let delta = "";
  if (deltaPct !== undefined && deltaPct !== null) {
    const dir = deltaPct >= 0 ? "up" : "down";
    const arrow = deltaPct >= 0 ? "▲" : "▼";
    delta = `<div class="stat-delta ${dir}">${arrow} ${Math.abs(deltaPct).toFixed(1)}% (24h)</div>`;
  }
  return `<div class="stat-tile"><div class="stat-label">${escapeHtml(label)}</div><div class="stat-value">${escapeHtml(value)}</div>${delta}</div>`;
}

// --- Mood & habits ---------------------------------------------------------

async function loadMood() {
  try {
    const mood = await api("/api/mood");
    const withEnergy = mood.week_trend.filter((e) => e.energy !== null && e.energy !== undefined);
    if (withEnergy.length === 0) {
      document.getElementById("mood-chart").innerHTML = '<p class="empty">No mood entries logged this week.</p>';
    } else {
      const labels = withEnergy.map((e) => new Date(e.created_at).toLocaleDateString(undefined, { weekday: "short" }));
      renderLineChart(
        document.getElementById("mood-chart"),
        labels,
        [{ name: "Energy", color: getComputedStyle(document.querySelector(".viz-root")).getPropertyValue("--series-1").trim(), values: withEnergy.map((e) => e.energy) }],
        { min: 1, max: 10, unit: "" }
      );
    }

    const streaksEl = document.getElementById("habit-streaks");
    if (mood.habits.length === 0) {
      streaksEl.innerHTML = '<p class="empty">No habits tracked yet.</p>';
    } else {
      streaksEl.innerHTML = mood.habits
        .map(
          (h) =>
            `<span class="streak-chip">${escapeHtml(h.habit)}: <span class="streak-count">${h.streak}d</span> streak</span>`
        )
        .join("");
    }
  } catch (err) {
    showError(err.message);
  }
}

// --- People ----------------------------------------------------------------

async function loadPeople() {
  try {
    const data = await api("/api/people");
    renderGlanceList(
      "upcoming-birthdays",
      data.upcoming_birthdays,
      (p) => escapeHtml(p.name),
      (p) => (p.days_away === 0 ? "today" : `in ${p.days_away}d`),
      "No birthdays in the next 30 days."
    );
    renderGlanceList(
      "stale-contacts",
      data.no_contact,
      (p) => escapeHtml(p.name),
      (p) => `${p.days_since_contact}d ago`,
      "Everyone's up to date."
    );
  } catch (err) {
    showError(err.message);
  }
}

function renderGlanceList(elementId, items, mainText, metaText, emptyMessage) {
  const el = document.getElementById(elementId);
  if (!items || items.length === 0) {
    el.innerHTML = `<li class="muted">${escapeHtml(emptyMessage)}</li>`;
    return;
  }
  el.innerHTML = items
    .map((item) => `<li><span>${mainText(item)}</span><span class="glance-meta">${metaText(item)}</span></li>`)
    .join("");
}

// --- System ------------------------------------------------------------------

async function loadSystem() {
  try {
    const system = await api("/api/system");
    const el = document.getElementById("system-tile");
    if (!system.checked) {
      el.innerHTML = '<p class="empty">Couldn\'t reach the cluster right now.</p>';
      return;
    }
    const dotClass = system.healthy ? "healthy" : "unhealthy";
    const summary = system.healthy
      ? `All ${system.deployments.length} deployments healthy in ${escapeHtml(system.namespace)}`
      : `${system.unhealthy_count} of ${system.deployments.length} deployments unhealthy in ${escapeHtml(system.namespace)}`;
    let html = `<div class="system-tile"><span class="system-dot ${dotClass}"></span><span>${summary}</span></div>`;
    const unhealthy = system.deployments.filter((d) => d.status === "unhealthy");
    if (unhealthy.length) {
      html += '<ul class="glance-list">' +
        unhealthy.map((d) => `<li><span>${escapeHtml(d.name)}</span><span class="glance-meta">${escapeHtml(d.detail || "")}</span></li>`).join("") +
        "</ul>";
    }
    el.innerHTML = html;
  } catch (err) {
    showError(err.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  loadMoney();
  loadMood();
  loadPeople();
  loadSystem();
});
