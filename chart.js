/**
 * Quantity Capital — Neon Glass Interactive Chart Engine (Option 2)
 *
 * Features:
 * - Smooth Catmull-Rom cubic spline interpolation for organic price curves
 * - Dual-phase neon gradient (ice platinum -> electric purple/violet) with glowing blur underlayer
 * - Integrated Politician and Insider transaction pins along the spline with luminous buy/sell halos
 * - Split transaction volume pane with buy/sell net flow bars and reference grid
 * - 60 FPS interactive pointer scrubber (mouse hover & mobile touch)
 * - Vertical crosshair line, snapped active node, attached price pill, and bottom date chip
 * - Floating glassmorphic HUD card showing Market Quote (Date, Close, Open/High/Low/Volume if available)
 *   and dynamically expanding with full Filer/Trade disclosure details when near transaction marks
 * - Zero external dependencies, pure native SVG + DOM, fully responsive
 */

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

function hoverDateFmt(iso) {
  const d = new Date(String(iso || "") + "T00:00:00");
  if (isNaN(d.getTime())) return String(iso || "");
  const m = d.getMonth() + 1;
  const y = d.getFullYear();
  return m + "/" + y;
}

function slicePriceRange(points, range) {
  if (!points || points.length < 2 || range === "3y" || range === "all") return points || [];
  const last = points[points.length - 1][0];
  const end = new Date(String(last) + "T00:00:00");
  if (isNaN(end.getTime())) return points;
  const start = new Date(end);
  if (range === "1m") start.setMonth(start.getMonth() - 1);
  else if (range === "3m") start.setMonth(start.getMonth() - 3);
  else if (range === "6m") start.setMonth(start.getMonth() - 6);
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

function formatVol(n) {
  if (!n) return "$0";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1e3) return "$" + Math.round(n / 1e3) + "k";
  return "$" + Math.round(n);
}

function tradeValue(m) {
  if (m.value != null && !isNaN(m.value) && m.value > 0) return Number(m.value);
  if (m.shares != null && m.price != null && m.shares > 0 && m.price > 0) {
    return Number(m.shares) * Number(m.price);
  }
  if (m.amount && typeof QC !== "undefined" && QC.amountHigh) {
    const value = QC.amountHigh(m.amount);
    if (value > 0) return value;
  }
  return 10000;
}

/**
 * Generate a smooth cubic Catmull-Rom spline path string for SVG
 */
function createSplinePath(pts) {
  if (!pts || pts.length < 2) return "";
  let d = "M " + pts[0].x.toFixed(1) + " " + pts[0].y.toFixed(1);
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = i > 0 ? pts[i - 1] : pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = i < pts.length - 2 ? pts[i + 2] : p2;

    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;

    d += " C " + cp1x.toFixed(1) + " " + cp1y.toFixed(1) + ", " +
         cp2x.toFixed(1) + " " + cp2y.toFixed(1) + ", " +
         p2.x.toFixed(1) + " " + p2.y.toFixed(1);
  }
  return d;
}

/**
 * Main drawChart routine
 */
function drawChart(points, marks, opts) {
  opts = opts || {};
  const wrap = document.getElementById("chart-wrap");
  const svg = document.getElementById("chart-svg");
  const yBox = document.getElementById("chart-y");
  const xBox = document.getElementById("chart-x");
  if (!svg || !points || points.length < 2) return [];

  // Dimensions & Padding
  const w = 840;
  const h = opts.height || 300;
  const pad = { l: 48, r: 64, t: 18, b: 24 };
  svg.setAttribute("viewBox", "0 0 " + w + " " + h);

  // Price pane (top 76%) and Volume pane (bottom 24%)
  const priceTop = pad.t;
  const priceH = Math.round((h - pad.t - pad.b) * 0.74);
  const priceBot = priceTop + priceH;
  const splitY = priceBot + 8;
  const volTop = splitY + 12;
  const volBot = h - pad.b;
  const volH = Math.max(12, volBot - volTop);

  const xs = points.map((p) => p[1]);
  const min = Math.min.apply(null, xs);
  const max = Math.max.apply(null, xs);
  const span = max - min || 1;

  const xAt = (i) => pad.l + (i / Math.max(1, points.length - 1)) * (w - pad.l - pad.r);
  const yAt = (px) => priceTop + (1 - (px - min) / span) * priceH;

  // Build screen coordinates for each price bar
  const pts = points.map((p, i) => ({
    x: xAt(i),
    y: yAt(p[1]),
    px: p[1],
    date: p[0],
    i: i
  }));

  const spline = createSplinePath(pts);
  const lastPt = pts[pts.length - 1];

  const idxFor = (date) => {
    let idx = 0;
    for (let i = 0; i < points.length; i++) {
      if (points[i][0] <= date) idx = i;
      else break;
    }
    return idx;
  };

  // Optional subtle shaded area under curve from last buy or start
  const buys = (marks || []).filter((m) => m.side !== "sale" && m.date);
  let lastBuy = opts.lastBuy || null;
  if (!lastBuy && buys.length) lastBuy = buys.map((m) => m.date).sort().pop();
  let areaPath = "";
  if (lastBuy) {
    const si = idxFor(lastBuy);
    const subPts = pts.slice(si);
    if (subPts.length >= 2) {
      areaPath = createSplinePath(subPts) +
        " L " + lastPt.x.toFixed(1) + " " + priceBot.toFixed(1) +
        " L " + pts[si].x.toFixed(1) + " " + priceBot.toFixed(1) + " Z";
    }
  }

  // Y-axis grid lines (5 horizontal levels)
  const yTicks = [1, 0.75, 0.5, 0.25, 0].map((t) => min + span * t);
  const gridLines = yTicks.map((px) => {
    const y = yAt(px).toFixed(1);
    return "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + y + "\" y2=\"" + y +
      "\" stroke=\"rgba(255,255,255,0.07)\" stroke-width=\"1\" />" +
      "<text x=\"" + (pad.l - 8) + "\" y=\"" + (Number(y) + 3.5).toFixed(1) +
      "\" fill=\"#828fa3\" font-size=\"10\" text-anchor=\"end\" font-family=\"IBM Plex Sans, sans-serif\">" +
      axisPrice(px) + "</text>";
  }).join("");

  // Visible marks & volume bars
  const firstDate = points[0][0];
  const lastDate = points[points.length - 1][0];
  const visibleMarks = (marks || []).filter((m) => m.date >= firstDate && m.date <= lastDate);

  const groupedVol = {};
  visibleMarks.forEach((m) => {
    const i = idxFor(m.date);
    if (!groupedVol[i]) groupedVol[i] = { buy: 0, sell: 0 };
    groupedVol[i][m.side === "sale" ? "sell" : "buy"] += tradeValue(m);
  });
  let maxVol = 1;
  Object.keys(groupedVol).forEach((key) => {
    maxVol = Math.max(maxVol, groupedVol[key].buy, groupedVol[key].sell);
  });

  const volBarWidth = Math.max(3, Math.min(10, (w - pad.l - pad.r) / Math.max(2, points.length) * 1.8));
  const volBars = Object.keys(groupedVol).map((key) => {
    const i = Number(key);
    const g = groupedVol[i];
    const x = xAt(i);
    const buyH = g.buy ? Math.max(2, (g.buy / maxVol) * (volH - 4)) : 0;
    const sellH = g.sell ? Math.max(2, (g.sell / maxVol) * (volH - 4)) : 0;
    return (buyH ? "<rect x=\"" + (x - volBarWidth / 2).toFixed(1) + "\" y=\"" + (volBot - buyH).toFixed(1) +
      "\" width=\"" + volBarWidth.toFixed(1) + "\" height=\"" + buyH.toFixed(1) + "\" fill=\"#22c55e\" opacity=\".85\" rx=\"1\" />" : "") +
      (sellH ? "<rect x=\"" + (x - volBarWidth / 2).toFixed(1) + "\" y=\"" + volTop.toFixed(1) +
        "\" width=\"" + volBarWidth.toFixed(1) + "\" height=\"" + sellH.toFixed(1) + "\" fill=\"#f87171\" opacity=\".85\" rx=\"1\" />" : "");
  }).join("");

  const volGrid = "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + volBot + "\" y2=\"" + volBot +
    "\" stroke=\"rgba(255,255,255,0.12)\" stroke-width=\"1\" />" +
    "<text x=\"" + (pad.l - 8) + "\" y=\"" + (volBot - 2) + "\" fill=\"#64748b\" font-size=\"8.5\" text-anchor=\"end\" font-family=\"IBM Plex Sans, sans-serif\">$0</text>" +
    (maxVol > 1 ? "<text x=\"" + (pad.l - 8) + "\" y=\"" + (volTop + 8) + "\" fill=\"#64748b\" font-size=\"8.5\" text-anchor=\"end\" font-family=\"IBM Plex Sans, sans-serif\">" + formatVol(maxVol) + "</text>" : "");

  // Cluster overlapping trade marks for clean pins
  const plotTop = priceTop + 14;
  const plotBot = priceBot - 14;
  const hit = [];

  function clusterByX(arr, minGap) {
    const items = arr.map((m) => {
      const i = idxFor(m.date);
      return { mark: m, i: i, x: xAt(i) };
    }).sort((a, b) => a.x - b.x);
    const out = [];
    items.forEach((it) => {
      const last = out[out.length - 1];
      if (last && it.x - last.xMax < minGap) {
        last.items.push(it);
        last.xMax = it.x;
        last.x = last.items.reduce((s, row) => s + row.x, 0) / last.items.length;
      } else {
        out.push({ x: it.x, xMax: it.x, items: [it] });
      }
    });
    return out;
  }

  const buyClusters = clusterByX(visibleMarks.filter((m) => m.side !== "sale"), 24);
  const sellClusters = clusterByX(visibleMarks.filter((m) => m.side === "sale"), 24);

  function drawCluster(cluster, sale) {
    const n = cluster.items.length;
    const marksList = cluster.items.map((row) => row.mark);
    const mid = cluster.items[Math.floor((n - 1) / 2)];
    const cy0 = yAt(points[mid.i][1]);
    const lift = sale ? 16 : -16;
    const cy = Math.max(plotTop, Math.min(plotBot, cy0 + lift));
    const color = sale ? "#f87171" : "#22c55e";
    const haloBg = sale ? "rgba(248, 113, 113, 0.22)" : "rgba(34, 197, 94, 0.22)";
    const letter = sale ? "S" : "B";
    const id = hit.length;
    let x = cluster.x;

    hit.push({
      mark: marksList[0],
      marks: marksList,
      side: sale ? "sale" : "purchase",
      x: x,
      y: cy,
      xPct: x / w,
      yPct: cy / h,
      date: marksList[0].date
    });

    if (n === 1) {
      return "<g class=\"chart-mark\" data-i=\"" + id + "\" style=\"cursor:pointer\">" +
        "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + cy.toFixed(1) + "\" r=\"14\" fill=\"transparent\" />" +
        "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + cy.toFixed(1) + "\" r=\"9\" fill=\"" + haloBg + "\" />" +
        "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + cy.toFixed(1) + "\" r=\"5.5\" fill=\"" + color + "\" stroke=\"#090d14\" stroke-width=\"1.5\" />" +
        "<text x=\"" + x.toFixed(1) + "\" y=\"" + (cy + 3).toFixed(1) + "\" text-anchor=\"middle\" fill=\"#ffffff\" font-size=\"7.5\" font-weight=\"800\" font-family=\"Barlow Condensed, sans-serif\" pointer-events=\"none\">" + letter + "</text>" +
        "</g>";
    }

    const total = marksList.reduce((s, m) => s + tradeValue(m), 0);
    const word = (sale ? "SELLS" : "BUYS") + " · " + formatVol(total);
    const tw = Math.max(76, 28 + word.length * 5.8);
    const x0 = Math.max(pad.l, Math.min(w - pad.r - tw, x - tw / 2));
    x = x0 + tw / 2;
    hit[id].x = x;
    hit[id].xPct = x / w;

    return "<g class=\"chart-mark\" data-i=\"" + id + "\" style=\"cursor:pointer\">" +
      "<rect x=\"" + x0.toFixed(1) + "\" y=\"" + (cy - 10).toFixed(1) +
        "\" width=\"" + tw.toFixed(1) + "\" height=\"20\" rx=\"10\" fill=\"" +
        (sale ? "rgba(46, 22, 25, 0.9)" : "rgba(18, 38, 28, 0.9)") + "\" stroke=\"" + color + "\" stroke-width=\"1.4\" />" +
      "<circle cx=\"" + (x0 + 11).toFixed(1) + "\" cy=\"" + cy.toFixed(1) + "\" r=\"6\" fill=\"" + color + "\" />" +
      "<text x=\"" + (x0 + 11).toFixed(1) + "\" y=\"" + (cy + 3).toFixed(1) + "\" text-anchor=\"middle\" fill=\"#ffffff\" font-size=\"7.5\" font-weight=\"800\" font-family=\"Barlow Condensed, sans-serif\" pointer-events=\"none\">" + n + "</text>" +
      "<text x=\"" + (x0 + 20 + (tw - 26) / 2).toFixed(1) + "\" y=\"" + (cy + 3.2).toFixed(1) + "\" text-anchor=\"middle\" fill=\"" + color + "\" font-size=\"9.5\" font-weight=\"800\" font-family=\"Barlow Condensed, sans-serif\" pointer-events=\"none\">" + word + "</text>" +
      "</g>";
  }

  const tradePinsHtml = buyClusters.map((c) => drawCluster(c, false)).join("") +
                        sellClusters.map((c) => drawCluster(c, true)).join("");

  // Terminal price pill on far right
  const terminalVal = lastPt.px;

  // Render complete SVG
  svg.innerHTML =
    "<defs>" +
      "<linearGradient id=\"neonGradient\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"0%\">" +
        "<stop offset=\"0%\" stop-color=\"#f8fafc\" />" +
        "<stop offset=\"36%\" stop-color=\"#f1f5f9\" />" +
        "<stop offset=\"58%\" stop-color=\"#c084fc\" />" +
        "<stop offset=\"85%\" stop-color=\"#a855f7\" />" +
        "<stop offset=\"100%\" stop-color=\"#c084fc\" />" +
      "</linearGradient>" +
      "<linearGradient id=\"underGlow\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"0%\">" +
        "<stop offset=\"0%\" stop-color=\"#94a3b8\" stop-opacity=\"0.15\" />" +
        "<stop offset=\"60%\" stop-color=\"#c084fc\" stop-opacity=\"0.28\" />" +
        "<stop offset=\"100%\" stop-color=\"#a855f7\" stop-opacity=\"0.5\" />" +
      "</linearGradient>" +
      "<linearGradient id=\"qc-sub-area\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
        "<stop offset=\"0%\" stop-color=\"#a855f7\" stop-opacity=\"0.2\" />" +
        "<stop offset=\"100%\" stop-color=\"#a855f7\" stop-opacity=\"0.02\" />" +
      "</linearGradient>" +
    "</defs>" +
    // Y Grid lines
    gridLines +
    // Area fill if present
    (areaPath ? "<path d=\"" + areaPath + "\" fill=\"url(#qc-sub-area)\" />" : "") +
    // Glowing underlayer
    "<path d=\"" + spline + "\" fill=\"none\" stroke=\"url(#underGlow)\" stroke-width=\"8\" opacity=\"0.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\" />" +
    // Crisp foreground spline
    "<path class=\"qc-spline-main\" d=\"" + spline + "\" fill=\"none\" stroke=\"url(#neonGradient)\" stroke-width=\"3.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" />" +
    // Terminal point dot
    "<circle cx=\"" + lastPt.x.toFixed(1) + "\" cy=\"" + lastPt.y.toFixed(1) + "\" r=\"5\" fill=\"#c084fc\" stroke=\"#ffffff\" stroke-width=\"1.8\" />" +
    // Interactive Trade Pins
    tradePinsHtml +
    // Split line for volume
    "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + splitY + "\" y2=\"" + splitY + "\" stroke=\"rgba(255,255,255,0.14)\" stroke-width=\"1\" stroke-dasharray=\"2 3\" />" +
    // Volume grid & bars
    volGrid +
    volBars +
    // Scrubber elements (updated dynamically on pointermove)
    "<g id=\"qc-scrubber-g\" style=\"display:none; pointer-events:none;\">" +
      "<line id=\"qc-scrub-line\" x1=\"0\" y1=\"" + pad.t + "\" x2=\"0\" y2=\"" + volBot + "\" stroke=\"rgba(255,255,255,0.22)\" stroke-width=\"1.2\" stroke-dasharray=\"3 3\" />" +
      "<circle id=\"qc-scrub-halo\" cx=\"0\" cy=\"0\" r=\"7.5\" fill=\"rgba(255,255,255,0.24)\" />" +
      "<circle id=\"qc-scrub-dot\" cx=\"0\" cy=\"0\" r=\"3.8\" fill=\"#ffffff\" stroke=\"#0d141f\" stroke-width=\"2\" />" +
    "</g>";

  // Bottom X-axis labels
  if (xBox) {
    const last = points.length - 1;
    const spots = [0, Math.round(last / 4), Math.round(last / 2), Math.round((3 * last) / 4), last];
    xBox.innerHTML = spots.map((i) => "<span>" + axisDate(points[i][0]) + "</span>").join("");
  }

  // DOM Badges & Tooltip Container
  ensureInteractiveDomElements(wrap, terminalVal, lastPt, w, h);

  // Hook up mark click events
  svg.querySelectorAll(".chart-mark").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const rec = hit[Number(el.getAttribute("data-i"))];
      if (rec && opts.onMark) opts.onMark(rec);
    });
  });

  // Attach Pointer Scrubbing
  attachScrubberEvents(svg, wrap, pts, hit, w, h, opts);

  return hit;
}

/**
 * Ensure the floating HUD, hover badges, and terminal pill exist in the chart-wrap
 */
function ensureInteractiveDomElements(wrap, terminalVal, lastPt, w, h) {
  if (!wrap) return;

  // Terminal price pill on far right
  let termPill = wrap.querySelector(".qc-terminal-pill");
  if (!termPill) {
    termPill = document.createElement("div");
    termPill.className = "qc-terminal-pill";
    wrap.appendChild(termPill);
  }
  termPill.innerHTML = "<span class=\"qc-term-dot\"></span> " + axisPrice(terminalVal);

  // Attached price pill on cursor
  let hoverPricePill = wrap.querySelector(".qc-hover-price-pill");
  if (!hoverPricePill) {
    hoverPricePill = document.createElement("div");
    hoverPricePill.className = "qc-hover-price-pill";
    hoverPricePill.style.display = "none";
    wrap.appendChild(hoverPricePill);
  }

  // Bottom date chip on cursor
  let hoverDatePill = wrap.querySelector(".qc-hover-date-pill");
  if (!hoverDatePill) {
    hoverDatePill = document.createElement("div");
    hoverDatePill.className = "qc-hover-date-pill";
    hoverDatePill.style.display = "none";
    wrap.appendChild(hoverDatePill);
  }

  // Floating Glass HUD
  let hud = wrap.querySelector(".qc-glass-hud");
  if (!hud) {
    hud = document.createElement("div");
    hud.className = "qc-glass-hud";
    hud.style.display = "none";
    wrap.appendChild(hud);
  }

  // Initial positioning of terminal pill
  updateTerminalPillPosition(wrap, termPill, lastPt, w, h);
}

function updateTerminalPillPosition(wrap, termPill, lastPt, w, h) {
  const svg = wrap.querySelector("#chart-svg");
  if (!svg || !termPill || !lastPt) return;
  const wr = wrap.getBoundingClientRect();
  const sr = svg.getBoundingClientRect();
  const scaleX = sr.width / w;
  const scaleY = sr.height / h;
  const screenX = (lastPt.x * scaleX) + (sr.left - wr.left);
  const screenY = (lastPt.y * scaleY) + (sr.top - wr.top);
  termPill.style.left = screenX + "px";
  termPill.style.top = screenY + "px";
}

/**
 * Handle 60 FPS pointer tracking across mouse and touch devices
 */
function attachScrubberEvents(svg, wrap, pts, hitMarks, w, h, opts) {
  const scrubG = svg.querySelector("#qc-scrubber-g");
  const scrubLine = svg.querySelector("#qc-scrub-line");
  const scrubDot = svg.querySelector("#qc-scrub-dot");
  const scrubHalo = svg.querySelector("#qc-scrub-halo");
  const hoverPricePill = wrap.querySelector(".qc-hover-price-pill");
  const hoverDatePill = wrap.querySelector(".qc-hover-date-pill");
  const hud = wrap.querySelector(".qc-glass-hud");

  function onPointerMove(e) {
    const sr = svg.getBoundingClientRect();
    const wr = wrap.getBoundingClientRect();
    const clientX = e.clientX || (e.touches && e.touches[0] ? e.touches[0].clientX : null);
    if (clientX == null) return;

    const relX = clientX - sr.left;
    const svgX = (relX / sr.width) * w;

    // Binary search or linear closest search on points
    let closestIdx = 0;
    let minDist = Infinity;
    for (let i = 0; i < pts.length; i++) {
      const dist = Math.abs(pts[i].x - svgX);
      if (dist < minDist) {
        minDist = dist;
        closestIdx = i;
      }
    }
    const pt = pts[closestIdx];
    if (!pt) return;

    // Show scrubber elements
    if (scrubG) scrubG.style.display = "";
    if (scrubLine) {
      scrubLine.setAttribute("x1", pt.x.toFixed(1));
      scrubLine.setAttribute("x2", pt.x.toFixed(1));
    }
    if (scrubDot) {
      scrubDot.setAttribute("cx", pt.x.toFixed(1));
      scrubDot.setAttribute("cy", pt.y.toFixed(1));
    }
    if (scrubHalo) {
      scrubHalo.setAttribute("cx", pt.x.toFixed(1));
      scrubHalo.setAttribute("cy", pt.y.toFixed(1));
    }

    const scaleX = sr.width / w;
    const scaleY = sr.height / h;
    const screenX = (pt.x * scaleX) + (sr.left - wr.left);
    const screenY = (pt.y * scaleY) + (sr.top - wr.top);

    // Attached Price Pill
    if (hoverPricePill) {
      hoverPricePill.style.display = "block";
      hoverPricePill.style.left = screenX + "px";
      hoverPricePill.style.top = screenY + "px";
      hoverPricePill.textContent = axisPrice(pt.px);
    }

    // Bottom Date Pill
    if (hoverDatePill) {
      hoverDatePill.style.display = "block";
      hoverDatePill.style.left = screenX + "px";
      hoverDatePill.textContent = hoverDateFmt(pt.date);
    }

    // Find if a trade mark is near this index (within 24px horizontal distance)
    const nearby = hitMarks.find((m) => Math.abs(m.x - pt.x) < 24);

    // Update Floating HUD
    if (hud) {
      hud.style.display = "block";
      let hudContent =
        "<div class=\"qc-hud-section\">Market Quote</div>" +
        "<div class=\"qc-hud-row\"><span>Date</span><span class=\"qc-hud-val\">" + pt.date + "</span></div>" +
        "<div class=\"qc-hud-row\"><span>Close</span><span class=\"qc-hud-val\">" + axisPrice(pt.px) + "</span></div>";

      if (nearby) {
        const marks = nearby.marks || [nearby.mark];
        const isBuy = nearby.side !== "sale";
        const tagClass = isBuy ? "tag-buy" : "tag-sell";
        const tagWord = isBuy ? "BUY" : "SELL";
        const primaryFiler = marks[0].filer || "Official Tape";
        const amountStr = marks[0].amount ? (typeof QC !== "undefined" ? QC.formatAmountRange(marks[0].amount) : marks[0].amount) : (marks[0].value ? formatVol(marks[0].value) : "");

        hudContent +=
          "<div class=\"qc-hud-trade-box\">" +
            "<div class=\"qc-hud-section\" style=\"color:" + (isBuy ? "var(--green)" : "var(--red)") + "\">Signal Intelligence</div>" +
            "<span class=\"qc-hud-tag " + tagClass + "\">" + tagWord + (amountStr ? " · " + amountStr : "") + "</span>" +
            "<div class=\"qc-hud-filer\">" + (typeof QC !== "undefined" ? QC.esc(primaryFiler) : primaryFiler) + "</div>" +
            (marks.length > 1 ? "<div class=\"qc-hud-sub\">+" + (marks.length - 1) + " other trades grouped on this date</div>" :
             "<div class=\"qc-hud-sub\">Official disclosure via eFD / Form 4</div>") +
          "</div>";
      }

      hud.innerHTML = hudContent;

      // Position HUD avoiding edge collisions
      let hudLeft = screenX - 230;
      if (hudLeft < 16) hudLeft = screenX + 24;
      let hudTop = screenY - 80;
      if (hudTop < 12) hudTop = 12;
      hud.style.left = hudLeft + "px";
      hud.style.top = hudTop + "px";
    }
  }

  function onPointerLeave() {
    if (scrubG) scrubG.style.display = "none";
    if (hoverPricePill) hoverPricePill.style.display = "none";
    if (hoverDatePill) hoverDatePill.style.display = "none";
    if (hud) hud.style.display = "none";
  }

  svg.addEventListener("mousemove", onPointerMove);
  svg.addEventListener("mouseleave", onPointerLeave);
  svg.addEventListener("touchmove", onPointerMove, { passive: true });
  svg.addEventListener("touchend", onPointerLeave);
}
