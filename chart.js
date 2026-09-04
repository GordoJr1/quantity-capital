function axisPrice(n) {
  if (n >= 10000) return "$" + Math.round(n / 1000) + "k";
  if (n >= 1000) return "$" + (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  if (n >= 100) return "$" + Math.round(n);
  if (n >= 10) return "$" + n.toFixed(1).replace(/\.0$/, "");
  return "$" + n.toFixed(2);
}

function axisDate(iso) {
  const d = new Date(String(iso || "") + "T00:00:00");
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("en-US", { month: "short", year: "2-digit" });
}

function slicePriceRange(points, range) {
  if (!points || points.length < 2 || range === "3y") return points || [];
  const last = points[points.length - 1][0];
  const end = new Date(String(last) + "T00:00:00");
  if (isNaN(end.getTime())) return points;
  const start = new Date(end);
  if (range === "3m") start.setMonth(start.getMonth() - 3);
  else start.setFullYear(start.getFullYear() - 1);
  const y = start.getFullYear();
  const m = String(start.getMonth() + 1).padStart(2, "0");
  const d = String(start.getDate()).padStart(2, "0");
  const iso = y + "-" + m + "-" + d;
  let i = 0;
  while (i < points.length && points[i][0] < iso) i += 1;
  const cut = points.slice(Math.max(0, i));
  return cut.length >= 2 ? cut : points.slice(-2);
}

function drawChart(points, marks, opts) {
  opts = opts || {};
  const svg = document.getElementById("chart-svg");
  const yBox = document.getElementById("chart-y");
  const xBox = document.getElementById("chart-x");
  if (!svg || !points || points.length < 2) return [];
  const stroke = opts.stroke || "#36999d";
  const w = 720;
  const h = opts.height || 220;
  const pad = { l: 6, r: 14, t: 14, b: 10 };
  svg.setAttribute("viewBox", "0 0 " + w + " " + h);
  const xs = points.map((p) => p[1]);
  const min = Math.min.apply(null, xs);
  const max = Math.max.apply(null, xs);
  const span = max - min || 1;
  const xAt = (i) => pad.l + (i / Math.max(1, points.length - 1)) * (w - pad.l - pad.r);
  const yAt = (px) => pad.t + (1 - (px - min) / span) * (h - pad.t - pad.b);
  const line = points.map((p, i) => (i ? "L" : "M") + xAt(i).toFixed(1) + " " + yAt(p[1]).toFixed(1)).join(" ");
  const lastX = xAt(points.length - 1);
  const lastY = yAt(points[points.length - 1][1]);
  const idxFor = (date) => {
    let idx = 0;
    for (let i = 0; i < points.length; i++) {
      if (points[i][0] <= date) idx = i;
      else break;
    }
    return idx;
  };
  const buys = (marks || []).filter((m) => m.side !== "sale" && m.date);
  let lastBuy = opts.lastBuy || null;
  if (!lastBuy && buys.length) lastBuy = buys.map((m) => m.date).sort().pop();
  let area = "";
  if (lastBuy) {
    const si = idxFor(lastBuy);
    const shadeLine = points.slice(si).map((p, k) => {
      const i = si + k;
      return (k ? "L" : "M") + xAt(i).toFixed(1) + " " + yAt(p[1]).toFixed(1);
    }).join(" ");
    area = shadeLine + " L" + lastX.toFixed(1) + " " + (h - pad.b) +
      " L" + xAt(si).toFixed(1) + " " + (h - pad.b) + " Z";
  }
  const yTicks = [1, 0.75, 0.5, 0.25, 0].map((t) => min + span * t);
  const grid = yTicks.map((px) => {
    const y = yAt(px).toFixed(1);
    return "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + y + "\" y2=\"" + y +
      "\" stroke=\"#2c3440\" stroke-width=\"1\" />";
  }).join("");
  const firstDate = points[0][0];
  const lastDate = points[points.length - 1][0];
  const visibleMarks = (marks || []).filter((m) => m.date >= firstDate && m.date <= lastDate);
  const grouped = {};
  visibleMarks.forEach((m) => {
    const i = idxFor(m.date);
    if (!grouped[i]) grouped[i] = [];
    grouped[i].push(m);
  });
  const plotTop = pad.t + 12;
  const plotBot = h - pad.b - 12;
  const hit = [];
  const dots = Object.keys(grouped).map((key) => {
    const i = Number(key);
    const pack = grouped[i];
    const cx = xAt(i);
    const cy0 = yAt(points[i][1]);
    const gap = 22;
    return pack.map((m, n) => {
      const off = (n - (pack.length - 1) / 2) * gap;
      const cy = Math.max(plotTop, Math.min(plotBot, cy0 + off));
      const jx = pack.length > 1 ? ((n % 2 ? 1 : -1) * (4 + Math.floor(n / 2))) : 0;
      const x = cx + jx;
      const buy = m.side !== "sale";
      const color = buy ? "#2f9e4f" : "#c2302a";
      const letter = buy ? "B" : "S";
      const id = hit.length;
      hit.push({ mark: m, x: x, y: cy, xPct: x / w, yPct: cy / h });
      return "<g class=\"chart-mark\" data-i=\"" + id + "\" style=\"cursor:pointer\">" +
        "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + cy.toFixed(1) +
          "\" r=\"14\" fill=\"transparent\" />" +
        "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + cy.toFixed(1) +
          "\" r=\"9\" fill=\"" + color + "\" stroke=\"#eef2f5\" stroke-width=\"1.6\" />" +
        "<text x=\"" + x.toFixed(1) + "\" y=\"" + (cy + 3.2).toFixed(1) +
          "\" text-anchor=\"middle\" fill=\"#eef2f5\" font-size=\"8.5\" font-weight=\"700\" " +
          "font-family=\"Barlow Condensed, sans-serif\" pointer-events=\"none\">" + letter + "</text>" +
      "</g>";
    }).join("");
  }).join("");
  svg.innerHTML =
    "<defs>" +
      "<linearGradient id=\"qc-area\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
        "<stop offset=\"0%\" stop-color=\"" + stroke + "\" stop-opacity=\"0.2\" />" +
        "<stop offset=\"100%\" stop-color=\"" + stroke + "\" stop-opacity=\"0.04\" />" +
      "</linearGradient>" +
    "</defs>" +
    grid +
    (area ? "<path d=\"" + area + "\" fill=\"url(#qc-area)\" />" : "") +
    "<path d=\"" + line + "\" fill=\"none\" stroke=\"" + stroke + "\" stroke-width=\"2.4\" />" +
    "<circle cx=\"" + lastX.toFixed(1) + "\" cy=\"" + lastY.toFixed(1) +
      "\" r=\"4.2\" fill=\"" + stroke + "\" stroke=\"#eef2f5\" stroke-width=\"1.4\" />" +
    dots;
  if (yBox) {
    // Position labels at the SVG tick coordinates instead of distributing them
    // across the full dual-pane height. This keeps the price axis out of the
    // volume pane and guarantees alignment with the rendered grid lines.
    yBox.innerHTML = yTicks.map((px) => {
      const top = (yAt(px) / h * 100).toFixed(3);
      return "<span style=\"position:absolute; right:6px; top:" + top + "%; transform:translateY(-50%);\">" +
        axisPrice(px) + "</span>";
    }).join("");
  }
  if (xBox) {
    const last = points.length - 1;
    const spots = [0, Math.round(last / 3), Math.round((2 * last) / 3), last];
    xBox.innerHTML = spots.map((i) => "<span>" + axisDate(points[i][0]) + "</span>").join("");
  }
  svg.querySelectorAll(".chart-mark").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const rec = hit[Number(el.getAttribute("data-i"))];
      if (rec && opts.onMark) opts.onMark(rec);
    });
  });
  return hit;
}
