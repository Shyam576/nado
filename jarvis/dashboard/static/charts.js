// dashboard/static/charts.js — minimal inline-SVG line chart, built to the
// dataviz skill's spec: 2px lines w/ round caps, 8px ringed end-markers,
// direct end-labels, hairline gridlines, one crosshair+tooltip layer.
// No charting library — the dataset here is a handful of weekly points,
// well within what's reasonable to hand-roll and keep in exact spec.

const CHART_WIDTH = 560;
const CHART_HEIGHT = 220;
const CHART_PAD = { top: 16, right: 72, bottom: 28, left: 8 };

function ns(tag) {
  return document.createElementNS("http://www.w3.org/2000/svg", tag);
}

/**
 * Render a multi-series line chart into `container`.
 *
 * @param container   DOM element to render into (its content is replaced).
 * @param labels      X-axis category labels, e.g. ["W1", "W2", ...].
 * @param series      [{ name, color, values: [number|null, ...] }, ...]
 * @param opts        { min, max, unit, valueFormat: fn(v) => string }
 */
function renderLineChart(container, labels, series, opts = {}) {
  const unit = opts.unit || "";
  const fmt = opts.valueFormat || ((v) => `${Math.round(v)}${unit}`);
  const allValues = series.flatMap((s) => s.values).filter((v) => v !== null && v !== undefined);
  const min = opts.min !== undefined ? opts.min : Math.min(0, ...allValues);
  const max = opts.max !== undefined ? opts.max : Math.max(...allValues, 1);

  const plotW = CHART_WIDTH - CHART_PAD.left - CHART_PAD.right;
  const plotH = CHART_HEIGHT - CHART_PAD.top - CHART_PAD.bottom;
  const n = labels.length;
  const x = (i) => CHART_PAD.left + (n <= 1 ? plotW / 2 : (plotW * i) / (n - 1));
  const y = (v) => CHART_PAD.top + plotH - ((v - min) / (max - min || 1)) * plotH;

  container.innerHTML = "";
  const svg = ns("svg");
  svg.setAttribute("viewBox", `0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`);
  svg.setAttribute("class", "chart-svg");
  svg.setAttribute("role", "img");

  // Gridlines (hairline, recessive) at 25/50/75/100% of the value range.
  for (let frac = 0; frac <= 1; frac += 0.25) {
    const gy = CHART_PAD.top + plotH * (1 - frac);
    const line = ns("line");
    line.setAttribute("x1", CHART_PAD.left);
    line.setAttribute("x2", CHART_WIDTH - CHART_PAD.right);
    line.setAttribute("y1", gy);
    line.setAttribute("y2", gy);
    line.setAttribute("class", "chart-grid");
    svg.appendChild(line);

    const tick = ns("text");
    tick.setAttribute("x", CHART_WIDTH - CHART_PAD.right + 6);
    tick.setAttribute("y", gy + 3);
    tick.setAttribute("class", "chart-axis-label");
    tick.textContent = fmt(min + (max - min) * frac);
    svg.appendChild(tick);
  }

  // X-axis labels
  labels.forEach((label, i) => {
    const t = ns("text");
    t.setAttribute("x", x(i));
    t.setAttribute("y", CHART_HEIGHT - 8);
    t.setAttribute("class", "chart-axis-label");
    t.setAttribute("text-anchor", "middle");
    t.textContent = label;
    svg.appendChild(t);
  });

  // Lines + end markers + end labels
  series.forEach((s) => {
    const points = s.values
      .map((v, i) => (v === null || v === undefined ? null : [x(i), y(v)]))
      .filter(Boolean);
    if (points.length === 0) return;

    const path = ns("path");
    path.setAttribute("d", points.map((p, i) => `${i === 0 ? "M" : "L"}${p[0]},${p[1]}`).join(" "));
    path.setAttribute("class", "chart-line");
    path.style.stroke = s.color;
    svg.appendChild(path);

    const last = points[points.length - 1];
    const ring = ns("circle");
    ring.setAttribute("cx", last[0]);
    ring.setAttribute("cy", last[1]);
    ring.setAttribute("r", 6);
    ring.setAttribute("class", "chart-marker-ring");
    svg.appendChild(ring);
    const dot = ns("circle");
    dot.setAttribute("cx", last[0]);
    dot.setAttribute("cy", last[1]);
    dot.setAttribute("r", 4);
    dot.style.fill = s.color;
    svg.appendChild(dot);

    const label = ns("text");
    label.setAttribute("x", last[0] + 9);
    label.setAttribute("y", last[1] + 4);
    label.setAttribute("class", "chart-end-label");
    label.style.fill = s.color;
    const lastValue = s.values[s.values.length - 1];
    label.textContent = lastValue === null || lastValue === undefined ? "" : fmt(lastValue);
    svg.appendChild(label);
  });

  // Crosshair + tooltip layer
  const crosshair = ns("line");
  crosshair.setAttribute("y1", CHART_PAD.top);
  crosshair.setAttribute("y2", CHART_HEIGHT - CHART_PAD.bottom);
  crosshair.setAttribute("class", "chart-crosshair");
  crosshair.style.display = "none";
  svg.appendChild(crosshair);

  const hitArea = ns("rect");
  hitArea.setAttribute("x", CHART_PAD.left);
  hitArea.setAttribute("y", CHART_PAD.top);
  hitArea.setAttribute("width", plotW);
  hitArea.setAttribute("height", plotH);
  hitArea.setAttribute("fill", "transparent");
  svg.appendChild(hitArea);

  container.appendChild(svg);

  const tooltip = document.createElement("div");
  tooltip.className = "chart-tooltip";
  tooltip.style.display = "none";
  container.style.position = "relative";
  container.appendChild(tooltip);

  function showTooltip(i) {
    crosshair.style.display = "block";
    crosshair.setAttribute("x1", x(i));
    crosshair.setAttribute("x2", x(i));

    tooltip.innerHTML = "";
    const header = document.createElement("div");
    header.className = "chart-tooltip-header";
    header.textContent = labels[i];
    tooltip.appendChild(header);
    series.forEach((s) => {
      const v = s.values[i];
      if (v === null || v === undefined) return;
      const row = document.createElement("div");
      row.className = "chart-tooltip-row";
      const key = document.createElement("span");
      key.className = "chart-tooltip-key";
      key.style.background = s.color;
      const name = document.createElement("span");
      name.className = "chart-tooltip-name";
      name.textContent = s.name;
      const value = document.createElement("span");
      value.className = "chart-tooltip-value";
      value.textContent = fmt(v);
      row.appendChild(key);
      row.appendChild(name);
      row.appendChild(value);
      tooltip.appendChild(row);
    });
    tooltip.style.display = "block";
    const pxRatio = container.clientWidth / CHART_WIDTH;
    tooltip.style.left = `${Math.min(x(i) * pxRatio + 10, container.clientWidth - 160)}px`;
    tooltip.style.top = `${CHART_PAD.top * (container.clientHeight / CHART_HEIGHT)}px`;
  }

  function hideTooltip() {
    crosshair.style.display = "none";
    tooltip.style.display = "none";
  }

  hitArea.addEventListener("pointermove", (e) => {
    const rect = svg.getBoundingClientRect();
    const relX = ((e.clientX - rect.left) / rect.width) * CHART_WIDTH;
    let nearest = 0;
    let best = Infinity;
    for (let i = 0; i < n; i++) {
      const d = Math.abs(x(i) - relX);
      if (d < best) {
        best = d;
        nearest = i;
      }
    }
    showTooltip(nearest);
  });
  hitArea.addEventListener("pointerleave", hideTooltip);
}
