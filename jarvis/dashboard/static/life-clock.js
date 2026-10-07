// dashboard/static/life-clock.js — the landing page: a live-ticking counter
// of time lived since config.DATE_OF_BIRTH, plus an estimated time remaining
// against a user-editable "assumed lifespan" (never a claimed fact — there's
// no way to know this in advance, so it's always shown as an assumption and
// the user can change it). Same vanilla-JS api()/showError() pattern as
// today.js/week.js/inbox.js.

let dob = null;
let assumedLifespanYears = 80;
let tickHandle = null;

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

// Calendar-correct breakdown of the time between two dates (a < b) into
// years/months/days/hours/minutes/seconds — not just total-ms division,
// which would misrepresent variable month lengths and leap years.
function diffBreakdown(a, b) {
  let years = b.getFullYear() - a.getFullYear();
  let months = b.getMonth() - a.getMonth();
  let days = b.getDate() - a.getDate();
  let hours = b.getHours() - a.getHours();
  let minutes = b.getMinutes() - a.getMinutes();
  let seconds = b.getSeconds() - a.getSeconds();

  if (seconds < 0) {
    seconds += 60;
    minutes--;
  }
  if (minutes < 0) {
    minutes += 60;
    hours--;
  }
  if (hours < 0) {
    hours += 24;
    days--;
  }
  if (days < 0) {
    const prevMonthLastDay = new Date(b.getFullYear(), b.getMonth(), 0).getDate();
    days += prevMonthLastDay;
    months--;
  }
  if (months < 0) {
    months += 12;
    years--;
  }
  return { years, months, days, hours, minutes, seconds };
}

function addYears(date, years) {
  const whole = Math.floor(years);
  const fractional = years - whole;
  const d = new Date(date);
  d.setFullYear(d.getFullYear() + whole);
  d.setDate(d.getDate() + Math.round(fractional * 365.25));
  return d;
}

function pad2(n) {
  return String(n).padStart(2, "0");
}

function formatAge(b) {
  const parts = [];
  if (b.years) parts.push(`${b.years} year${b.years !== 1 ? "s" : ""}`);
  parts.push(`${b.months} month${b.months !== 1 ? "s" : ""}`);
  parts.push(`${b.days} day${b.days !== 1 ? "s" : ""}`);
  const clock = `${pad2(b.hours)}:${pad2(b.minutes)}:${pad2(b.seconds)}`;
  return `${parts.join(", ")}, ${clock}`;
}

function formatRemaining(b) {
  const parts = [];
  if (b.years) parts.push(`${b.years} year${b.years !== 1 ? "s" : ""}`);
  if (b.months || b.years) parts.push(`${b.months} month${b.months !== 1 ? "s" : ""}`);
  parts.push(`${b.days} day${b.days !== 1 ? "s" : ""}`);
  return `~${parts.join(", ")}`;
}

function tick() {
  if (!dob) return;
  const now = new Date();

  document.getElementById("lc-age").textContent = formatAge(diffBreakdown(dob, now));

  const target = addYears(dob, assumedLifespanYears);
  const totalMs = target - dob;
  const elapsedMs = now - dob;
  const pct = Math.max(0, Math.min(100, (elapsedMs / totalMs) * 100));

  document.getElementById("lc-bar").style.width = `${pct}%`;
  document.getElementById("lc-pct").textContent = `${pct.toFixed(1)}% of an assumed lifespan lived`;

  const remainingEl = document.getElementById("lc-remaining");
  if (target > now) {
    remainingEl.textContent = formatRemaining(diffBreakdown(now, target));
  } else {
    remainingEl.textContent = "— past the assumed lifespan";
  }
}

async function load() {
  try {
    const config = await api("/api/life-clock");
    dob = new Date(config.date_of_birth + "T00:00:00");
    assumedLifespanYears = config.assumed_lifespan_years;
    document.getElementById("lifespan-input").value = Math.round(assumedLifespanYears);

    tick();
    if (tickHandle) clearInterval(tickHandle);
    tickHandle = setInterval(tick, 1000);
  } catch (err) {
    showError(err.message);
  }
}

async function saveLifespan(event) {
  event.preventDefault();
  const input = document.getElementById("lifespan-input");
  const years = parseFloat(input.value);
  if (!years || years <= 0) return;
  try {
    const config = await api("/api/life-clock", {
      method: "POST",
      body: JSON.stringify({ assumed_lifespan_years: years }),
    });
    assumedLifespanYears = config.assumed_lifespan_years;
    tick();
  } catch (err) {
    showError(err.message);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.getElementById("lifespan-form").addEventListener("submit", saveLifespan);
  load();
});
