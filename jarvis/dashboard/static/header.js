// dashboard/static/header.js — persistent header strip, loaded on every page.
// Deliberately isolated from each page's own JS/state — a failure here
// (e.g. an unrelated 401) must never block the page's own content.

async function loadHeader() {
  try {
    const res = await fetch("/api/header");
    if (!res.ok) return; // e.g. a stale session — the page's own script will surface that
    const data = await res.json();
    renderHeader(data);
  } catch {
    // Silent — the header strip is a nice-to-have, not load-bearing for the page.
  }
}

function renderHeader(data) {
  const dateEl = document.getElementById("header-date");
  dateEl.textContent = new Date(data.date + "T00:00:00").toLocaleDateString(undefined, {
    weekday: "short",
    month: "short",
    day: "numeric",
  });

  const slipEl = document.getElementById("header-must-not-slip");
  if (data.must_not_slip) {
    slipEl.textContent = data.must_not_slip;
    slipEl.style.display = "inline";
  } else {
    slipEl.style.display = "none";
  }

  const reminderEl = document.getElementById("header-next-reminder");
  if (data.next_reminder) {
    const when = new Date(data.next_reminder.fire_at).toLocaleTimeString(undefined, {
      hour: "numeric",
      minute: "2-digit",
    });
    reminderEl.textContent = `Next: ${when} — ${data.next_reminder.message}`;
    reminderEl.style.display = "inline";
  } else {
    reminderEl.style.display = "none";
  }

  const badge = document.getElementById("header-k8s-badge");
  if (!data.k8s_healthy && data.k8s_unhealthy_count > 0) {
    document.getElementById("header-k8s-count").textContent =
      data.k8s_unhealthy_count === 1 ? "1 deployment unhealthy" : `${data.k8s_unhealthy_count} deployments unhealthy`;
    badge.style.display = "inline-flex";
  } else {
    badge.style.display = "none";
  }
}

document.addEventListener("DOMContentLoaded", loadHeader);
