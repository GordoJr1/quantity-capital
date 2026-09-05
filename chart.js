/**
 * Quantity Capital — Neon Glass Interactive Chart Engine (Option 2)
 *
 * Features:
 * - Smooth Catmull-Rom cubic spline interpolation for organic price curves
 * - Dual-phase neon gradient (ice platinum -> electric purple/violet) with glowing blur underlayer
 * - Integrated Politician and Insider transaction pins snapped directly onto the price spline
 * - High-clarity, large luminous typography for Y-axis price levels and X-axis date intervals
 * - 60 FPS interactive pointer scrubber (mouse hover & mobile touch)
 * - Vertical crosshair line, snapped active node, attached price pill, and bottom date chip
 * - Floating glassmorphic HUD card showing Market Quote (Date, Close, Open/High/Low/Volume if available)
 *   and dynamically expanding with full Filer/Trade disclosure details when near transaction marks
 * - Zero external dependencies, pure native SVG + DOM, fully responsive
 */

function axisPrice(n, span) {
  if (n >= 10000) return "$" + Math.round(n / 1000) + "k";
  if (n >= 1000 && (span == null || span >= 100)) return "$" + (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  if (span != null && span < 10) return "$" + n.toFixed(2);
  if (n >= 100) return "$" + Math.round(n);
  if (n >= 10) return "$" + n.toFixed(1).replace(/\.0$/, "");
  return "$" + n.toFixed(2);
}

function axisDate(iso, isShortSpan) {
  const d = new Date(String(iso || "") + "T00:00:00");
  if (isNaN(d.getTime())) return "";
  if (isShortSpan) {
    return d.toLocaleString("en-US", { month: "short", day: "numeric" });
  }
  return d.toLocaleString("en-US", { month: "short", year: "2-digit" });
}

function hoverDateFmt(iso) {
  const d = new Date(String(iso || "") + "T00:00:00");
  if (isNaN(d.getTime())) return String(iso || "");
  const m = d.getMonth() + 1;
  const day = d.getDate();
  const y = d.getFullYear();
  return m + "/" + day + "/" + String(y).slice(-2);
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

// Built-in color palettes for chart spline and accents
const CHART_PALETTES = {
  emerald: {
    name: "Emerald Matrix",
    gradStops: [
      { offset: "0%", color: "#f8fafc" },
      { offset: "36%", color: "#f1f5f9" },
      { offset: "58%", color: "#6ee7b7" },
      { offset: "85%", color: "#10b981" },
      { offset: "100%", color: "#059669" }
    ],
    glowStops: [
      { offset: "0%", color: "#94a3b8", opacity: 0.15 },
      { offset: "60%", color: "#34d399", opacity: 0.28 },
      { offset: "100%", color: "#10b981", opacity: 0.55 }
    ],
    primary: "#10b981",
    light: "#6ee7b7",
    glowFilter: "drop-shadow(0 0 7px rgba(16, 185, 129, 0.55)) drop-shadow(0 0 14px rgba(16, 185, 129, 0.32))",
    terminalPillBg: "#059669",
    terminalPillShadow: "0 4px 12px rgba(5, 150, 105, 0.45)",
    popShadow: "0 16px 36px rgba(0, 0, 0, 0.82), 0 0 1px rgba(255, 255, 255, 0.25), 0 0 20px rgba(16, 185, 129, 0.16)"
  },
  cyan: {
    name: "Electric Cyan",
    gradStops: [
      { offset: "0%", color: "#f8fafc" },
      { offset: "36%", color: "#f1f5f9" },
      { offset: "58%", color: "#67e8f9" },
      { offset: "85%", color: "#06b6d4" },
      { offset: "100%", color: "#0891b2" }
    ],
    glowStops: [
      { offset: "0%", color: "#94a3b8", opacity: 0.15 },
      { offset: "60%", color: "#22d3ee", opacity: 0.28 },
      { offset: "100%", color: "#06b6d4", opacity: 0.55 }
    ],
    primary: "#06b6d4",
    light: "#67e8f9",
    glowFilter: "drop-shadow(0 0 7px rgba(6, 182, 212, 0.55)) drop-shadow(0 0 14px rgba(6, 182, 212, 0.32))",
    terminalPillBg: "#0891b2",
    terminalPillShadow: "0 4px 12px rgba(8, 145, 178, 0.45)",
    popShadow: "0 16px 36px rgba(0, 0, 0, 0.82), 0 0 1px rgba(255, 255, 255, 0.25), 0 0 20px rgba(6, 182, 212, 0.16)"
  },
  amber: {
    name: "Amber Gold",
    gradStops: [
      { offset: "0%", color: "#f8fafc" },
      { offset: "36%", color: "#fef3c7" },
      { offset: "58%", color: "#fcd34d" },
      { offset: "85%", color: "#f59e0b" },
      { offset: "100%", color: "#d97706" }
    ],
    glowStops: [
      { offset: "0%", color: "#94a3b8", opacity: 0.15 },
      { offset: "60%", color: "#fbbf24", opacity: 0.28 },
      { offset: "100%", color: "#f59e0b", opacity: 0.55 }
    ],
    primary: "#f59e0b",
    light: "#fcd34d",
    glowFilter: "drop-shadow(0 0 7px rgba(245, 158, 11, 0.55)) drop-shadow(0 0 14px rgba(245, 158, 11, 0.32))",
    terminalPillBg: "#d97706",
    terminalPillShadow: "0 4px 12px rgba(217, 119, 6, 0.45)",
    popShadow: "0 16px 36px rgba(0, 0, 0, 0.82), 0 0 1px rgba(255, 255, 255, 0.25), 0 0 20px rgba(245, 158, 11, 0.16)"
  },
  blue: {
    name: "Cobalt Ice",
    gradStops: [
      { offset: "0%", color: "#f8fafc" },
      { offset: "36%", color: "#f1f5f9" },
      { offset: "58%", color: "#93c5fd" },
      { offset: "85%", color: "#3b82f6" },
      { offset: "100%", color: "#2563eb" }
    ],
    glowStops: [
      { offset: "0%", color: "#94a3b8", opacity: 0.15 },
      { offset: "60%", color: "#60a5fa", opacity: 0.28 },
      { offset: "100%", color: "#3b82f6", opacity: 0.55 }
    ],
    primary: "#3b82f6",
    light: "#93c5fd",
    glowFilter: "drop-shadow(0 0 7px rgba(59, 130, 246, 0.55)) drop-shadow(0 0 14px rgba(59, 130, 246, 0.32))",
    terminalPillBg: "#2563eb",
    terminalPillShadow: "0 4px 12px rgba(37, 99, 235, 0.45)",
    popShadow: "0 16px 36px rgba(0, 0, 0, 0.82), 0 0 1px rgba(255, 255, 255, 0.25), 0 0 20px rgba(59, 130, 246, 0.16)"
  },
  purple: {
    name: "Neon Purple",
    gradStops: [
      { offset: "0%", color: "#f8fafc" },
      { offset: "36%", color: "#f1f5f9" },
      { offset: "58%", color: "#c084fc" },
      { offset: "85%", color: "#a855f7" },
      { offset: "100%", color: "#c084fc" }
    ],
    glowStops: [
      { offset: "0%", color: "#94a3b8", opacity: 0.15 },
      { offset: "60%", color: "#c084fc", opacity: 0.28 },
      { offset: "100%", color: "#a855f7", opacity: 0.5 }
    ],
    primary: "#a855f7",
    light: "#c084fc",
    glowFilter: "drop-shadow(0 0 7px rgba(168, 85, 247, 0.5)) drop-shadow(0 0 14px rgba(168, 85, 247, 0.28))",
    terminalPillBg: "#8b5cf6",
    terminalPillShadow: "0 4px 12px rgba(139, 92, 246, 0.45)",
    popShadow: "0 16px 36px rgba(0, 0, 0, 0.82), 0 0 1px rgba(255, 255, 255, 0.25), 0 0 20px rgba(168, 85, 247, 0.12)"
  }
};

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
  const isMobile = typeof window !== "undefined" && window.matchMedia("(max-width: 820px)").matches;
  const w = 840;
  const h = opts.height || (isMobile ? 240 : 300);
  const pad = {
    l: isMobile ? 82 : 64,
    r: isMobile ? 54 : 60,
    t: isMobile ? 24 : 22,
    b: isMobile ? 20 : 20
  };
  svg.setAttribute("viewBox", "0 0 " + w + " " + h);

  // Full height for price curve
  const priceTop = pad.t;
  const priceBot = h - pad.b;
  const priceH = priceBot - priceTop;

  const xs = points.map((p) => p[1]);
  const min = Math.min.apply(null, xs);
  const max = Math.max.apply(null, xs);
  const span = max - min || 1;

  const xAt = (i) => pad.l + (i / Math.max(1, points.length - 1)) * (w - pad.l - pad.r);
  const yAt = (px) => priceTop + (1 - (px - min) / span) * priceH;

  // Active color theme (defaults to emerald)
  const themeKey = (opts.theme && CHART_PALETTES[opts.theme]) ? opts.theme : (window.QC_CHART_THEME || "emerald");
  const palette = CHART_PALETTES[themeKey] || CHART_PALETTES.emerald;

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

  // Optional shaded area under curve - disabled per user request
  // (area shading removed for crisp, clean price curve visibility)
  let areaPath = "";
  if (opts.showArea) {
    const buys = (marks || []).filter((m) => m.side !== "sale" && m.date);
    let lastBuy = opts.lastBuy || null;
    if (!lastBuy && buys.length) lastBuy = buys.map((m) => m.date).sort().pop();
    if (lastBuy) {
      const si = idxFor(lastBuy);
      const subPts = pts.slice(si);
      if (subPts.length >= 2) {
        areaPath = createSplinePath(subPts) +
          " L " + lastPt.x.toFixed(1) + " " + priceBot.toFixed(1) +
          " L " + pts[si].x.toFixed(1) + " " + priceBot.toFixed(1) + " Z";
      }
    }
  }

  // Y-axis grid lines (5 horizontal levels: 100%, 75%, 50%, 25%, 0%)
  const yFontSize = isMobile ? 22 : 14.5;
  const yTextOffset = isMobile ? 6.5 : 4.8;
  const yTicks = [1, 0.75, 0.5, 0.25, 0].map((t) => min + span * t);
  const gridLines = yTicks.map((px) => {
    const y = yAt(px).toFixed(1);
    return "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + y + "\" y2=\"" + y +
      "\" stroke=\"rgba(255,255,255,0.08)\" stroke-width=\"1\" stroke-dasharray=\"3 4\" />" +
      "<text x=\"" + (pad.l - 12) + "\" y=\"" + (Number(y) + yTextOffset).toFixed(1) +
      "\" fill=\"#e2e8f0\" font-size=\"" + yFontSize + "\" font-weight=\"700\" text-anchor=\"end\" font-family=\"IBM Plex Sans, sans-serif\">" +
      axisPrice(px, span) + "</text>";
  }).join("");

  // Visible marks along the spline
  const firstDate = points[0][0];
  const lastDate = points[points.length - 1][0];
  const visibleMarks = (marks || []).filter((m) => m.date >= firstDate && m.date <= lastDate);

  // Cluster overlapping trade marks for clean pins
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

  const buyClusters = clusterByX(visibleMarks.filter((m) => m.side !== "sale"), 28);
  const sellClusters = clusterByX(visibleMarks.filter((m) => m.side === "sale"), 28);

  // If a buy cluster and a sell cluster land on the exact same date/spot,
  // separate them slightly horizontally so both remain visible and centered on the price curve.
  buyClusters.forEach((bc) => {
    sellClusters.forEach((sc) => {
      const dist = sc.x - bc.x;
      if (Math.abs(dist) < 22) {
        const shift = (22 - Math.abs(dist)) / 2;
        if (dist >= 0) {
          bc.x = Math.max(pad.l + 10, bc.x - shift);
          sc.x = Math.min(w - pad.r - 10, sc.x + shift);
        } else {
          bc.x = Math.min(w - pad.r - 10, bc.x + shift);
          sc.x = Math.max(pad.l + 10, sc.x - shift);
        }
      }
    });
  });

  function drawCluster(cluster, sale) {
    const n = cluster.items.length;
    const marksList = cluster.items.map((row) => row.mark);
    const mid = cluster.items[Math.floor((n - 1) / 2)];
    // Snap dead-center onto the price line at this date
    const cy = yAt(points[mid.i][1]);
    const color = sale ? "#f87171" : "#22c55e";
    const haloBg = sale ? "rgba(248, 113, 113, 0.22)" : "rgba(34, 197, 94, 0.22)";
    const letter = sale ? "S" : "B";
    const id = hit.length;
    let x = cluster.x;

    const pinR = isMobile ? 8.5 : 5.5;
    const haloR = isMobile ? 14 : 9;
    const pinFontSize = isMobile ? 11 : 7.5;
    const pinTextYOffset = isMobile ? 3.8 : 2.8;

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
        "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + cy.toFixed(1) + "\" r=\"" + (haloR + 6) + "\" fill=\"transparent\" />" +
        "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + cy.toFixed(1) + "\" r=\"" + haloR + "\" fill=\"" + haloBg + "\" />" +
        "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + cy.toFixed(1) + "\" r=\"" + pinR + "\" fill=\"" + color + "\" stroke=\"#090d14\" stroke-width=\"1.5\" />" +
        "<text x=\"" + x.toFixed(1) + "\" y=\"" + (cy + pinTextYOffset).toFixed(1) + "\" text-anchor=\"middle\" fill=\"#ffffff\" font-size=\"" + pinFontSize + "\" font-weight=\"800\" font-family=\"Barlow Condensed, sans-serif\" pointer-events=\"none\">" + letter + "</text>" +
        "</g>";
    }

    const badgeH = isMobile ? 24 : 20;
    const badgeR = isMobile ? 12 : 10;
    const badgeCircleR = isMobile ? 8 : 6;
    const badgeNumFont = isMobile ? 10 : 7.5;
    const badgeWordFont = isMobile ? 12 : 9.5;
    const badgeNumOffset = isMobile ? 3.5 : 2.8;
    const badgeWordOffset = isMobile ? 4 : 3.2;

    const total = marksList.reduce((s, m) => s + tradeValue(m), 0);
    const word = (sale ? "SELLS" : "BUYS") + " · " + formatVol(total);
    const tw = Math.max(isMobile ? 92 : 76, 28 + word.length * (isMobile ? 6.8 : 5.8));
    const x0 = Math.max(pad.l, Math.min(w - pad.r - tw, x - tw / 2));
    x = x0 + tw / 2;
    hit[id].x = x;
    hit[id].xPct = x / w;

    return "<g class=\"chart-mark\" data-i=\"" + id + "\" style=\"cursor:pointer\">" +
      "<rect x=\"" + x0.toFixed(1) + "\" y=\"" + (cy - badgeH / 2).toFixed(1) +
        "\" width=\"" + tw.toFixed(1) + "\" height=\"" + badgeH + "\" rx=\"" + badgeR + "\" fill=\"" +
        (sale ? "rgba(46, 22, 25, 0.9)" : "rgba(18, 38, 28, 0.9)") + "\" stroke=\"" + color + "\" stroke-width=\"1.4\" />" +
      "<circle cx=\"" + (x0 + (isMobile ? 13 : 11)).toFixed(1) + "\" cy=\"" + cy.toFixed(1) + "\" r=\"" + badgeCircleR + "\" fill=\"" + color + "\" />" +
      "<text x=\"" + (x0 + (isMobile ? 13 : 11)).toFixed(1) + "\" y=\"" + (cy + badgeNumOffset).toFixed(1) + "\" text-anchor=\"middle\" fill=\"#ffffff\" font-size=\"" + badgeNumFont + "\" font-weight=\"800\" font-family=\"Barlow Condensed, sans-serif\" pointer-events=\"none\">" + n + "</text>" +
      "<text x=\"" + (x0 + (isMobile ? 22 : 20) + (tw - (isMobile ? 28 : 26)) / 2).toFixed(1) + "\" y=\"" + (cy + badgeWordOffset).toFixed(1) + "\" text-anchor=\"middle\" fill=\"" + color + "\" font-size=\"" + badgeWordFont + "\" font-weight=\"800\" font-family=\"Barlow Condensed, sans-serif\" pointer-events=\"none\">" + word + "</text>" +
      "</g>";
  }

  const tradePinsHtml = buyClusters.map((c) => drawCluster(c, false)).join("") +
                        sellClusters.map((c) => drawCluster(c, true)).join("");

  // Terminal price pill on far right
  const terminalVal = lastPt.px;

  // Render complete SVG
  const gradStopsHtml = palette.gradStops.map((s) =>
    "<stop offset=\"" + s.offset + "\" stop-color=\"" + s.color + "\" />"
  ).join("");

  const glowStopsHtml = palette.glowStops.map((s) =>
    "<stop offset=\"" + s.offset + "\" stop-color=\"" + s.color + "\" stop-opacity=\"" + s.opacity + "\" />"
  ).join("");

  svg.innerHTML =
    "<defs>" +
      "<linearGradient id=\"neonGradient\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"0%\">" +
        gradStopsHtml +
      "</linearGradient>" +
      "<linearGradient id=\"underGlow\" x1=\"0%\" y1=\"0%\" x2=\"100%\" y2=\"0%\">" +
        glowStopsHtml +
      "</linearGradient>" +
      "<linearGradient id=\"qc-sub-area\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
        "<stop offset=\"0%\" stop-color=\"" + palette.primary + "\" stop-opacity=\"0.18\" />" +
        "<stop offset=\"100%\" stop-color=\"" + palette.primary + "\" stop-opacity=\"0.01\" />" +
      "</linearGradient>" +
    "</defs>" +
    // Y Grid lines
    gridLines +
    // Area fill if explicitly enabled
    (areaPath ? "<path d=\"" + areaPath + "\" fill=\"url(#qc-sub-area)\" />" : "") +
    // Glowing underlayer
    "<path d=\"" + spline + "\" fill=\"none\" stroke=\"url(#underGlow)\" stroke-width=\"8\" opacity=\"0.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\" />" +
    // Crisp foreground spline
    "<path class=\"qc-spline-main\" d=\"" + spline + "\" fill=\"none\" stroke=\"url(#neonGradient)\" stroke-width=\"3.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" style=\"filter:" + palette.glowFilter + ";\" />" +
    // Terminal point dot
    "<circle cx=\"" + lastPt.x.toFixed(1) + "\" cy=\"" + lastPt.y.toFixed(1) + "\" r=\"5\" fill=\"" + palette.light + "\" stroke=\"#ffffff\" stroke-width=\"1.8\" />" +
    // Interactive Trade Pins
    tradePinsHtml +
    // Scrubber elements (updated dynamically on pointermove)
    "<g id=\"qc-scrubber-g\" style=\"display:none; pointer-events:none;\">" +
      "<line id=\"qc-scrub-line\" x1=\"0\" y1=\"" + pad.t + "\" x2=\"0\" y2=\"" + priceBot + "\" stroke=\"rgba(255,255,255,0.22)\" stroke-width=\"1.2\" stroke-dasharray=\"3 3\" />" +
      "<circle id=\"qc-scrub-halo\" cx=\"0\" cy=\"0\" r=\"7.5\" fill=\"rgba(255,255,255,0.24)\" />" +
      "<circle id=\"qc-scrub-dot\" cx=\"0\" cy=\"0\" r=\"3.8\" fill=\"#ffffff\" stroke=\"#0d141f\" stroke-width=\"2\" />" +
    "</g>";

  // Bottom X-axis labels
  if (xBox) {
    const last = points.length - 1;
    const spots = [0, Math.round(last / 4), Math.round(last / 2), Math.round((3 * last) / 4), last];
    const daysSpan = (new Date(String(lastDate) + "T00:00:00") - new Date(String(firstDate) + "T00:00:00")) / 86400000;
    const isShortSpan = daysSpan <= 210 || (opts && (opts.range === "1m" || opts.range === "3m" || opts.range === "6m"));
    xBox.innerHTML = spots.map((i) => "<span>" + axisDate(points[i][0], isShortSpan) + "</span>").join("");
  }

  // DOM Badges & Tooltip Container
  ensureInteractiveDomElements(wrap, terminalVal, lastPt, w, h, palette);

  // Hook up mark click events
  svg.querySelectorAll(".chart-mark").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      const rec = hit[Number(el.getAttribute("data-i"))];
      if (rec && opts.onMark) opts.onMark(rec);
    });
  });

  // Canvas click to dismiss trade popover if clicked outside marks
  svg.addEventListener("click", (e) => {
    if (!e.target.closest(".chart-mark")) {
      const pop = wrap.querySelector("#mark-pop");
      if (pop) pop.hidden = true;
    }
  });

  // Attach Pointer Scrubbing
  attachScrubberEvents(svg, wrap, pts, hit, w, h, opts);

  return hit;
}

/**
 * Ensure the floating HUD, hover badges, and terminal pill exist in the chart-wrap
 */
function ensureInteractiveDomElements(wrap, terminalVal, lastPt, w, h, palette) {
  if (!wrap) return;

  // Terminal price pill on far right
  let termPill = wrap.querySelector(".qc-terminal-pill");
  if (!termPill) {
    termPill = document.createElement("div");
    termPill.className = "qc-terminal-pill";
    wrap.appendChild(termPill);
  }
  termPill.innerHTML = "<span class=\"qc-term-dot\"></span> " + axisPrice(terminalVal);
  if (palette) {
    if (palette.terminalPillBg) termPill.style.background = palette.terminalPillBg;
    if (palette.terminalPillShadow) termPill.style.boxShadow = palette.terminalPillShadow;
  }

  // Update mark-pop box-shadow if active
  const pop = wrap.querySelector("#mark-pop");
  if (pop && palette && palette.popShadow) {
    pop.style.boxShadow = palette.popShadow;
  }

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
  let screenX = (lastPt.x * scaleX) + (sr.left - wr.left) + 10;
  // Ensure pill stays inside the chart card on small screens
  if (screenX + 54 > wr.width - 8) {
    screenX = wr.width - 62;
  }
  const screenY = (lastPt.y * scaleY) + (sr.top - wr.top);
  termPill.style.left = screenX + "px";
  termPill.style.top = screenY + "px";
  termPill.style.transform = "translateY(-50%)";
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
      let pillLeft = screenX + 10;
      if (pillLeft + 54 > wr.width - 8) {
        pillLeft = screenX - 60;
      }
      hoverPricePill.style.left = pillLeft + "px";
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
      const pop = wrap.querySelector("#mark-pop");
      if (pop && !pop.hidden) {
        hud.style.display = "none";
        return;
      }
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
      const isMobile = window.matchMedia("(max-width: 600px)").matches;
      if (isMobile) {
        // On small mobile screens, center HUD at the top of the chart frame to prevent covering the active node
        hud.style.left = "50%";
        hud.style.transform = "translateX(-50%)";
        hud.style.top = "10px";
        hud.style.maxWidth = "calc(100% - 24px)";
      } else {
        hud.style.transform = "none";
        hud.style.maxWidth = "260px";
        let hudLeft = screenX - 230;
        if (hudLeft < 16) hudLeft = screenX + 24;
        let hudTop = screenY - 80;
        if (hudTop < 12) hudTop = 12;
        hud.style.left = hudLeft + "px";
        hud.style.top = hudTop + "px";
      }
    }
  }

  function onPointerLeave() {
    if (scrubG) scrubG.style.display = "none";
    if (hoverPricePill) hoverPricePill.style.display = "none";
    if (hoverDatePill) hoverDatePill.style.display = "none";
    if (hud) hud.style.display = "none";
  }

      svg.style.touchAction = "none";
      let isTouching = false;
      svg.addEventListener("touchstart", (e) => {
        isTouching = true;
        onPointerMove(e);
      }, { passive: false });
      svg.addEventListener("touchmove", (e) => {
        if (isTouching) {
          e.preventDefault(); // prevent page jerk while scrubbing chart on mobile
          onPointerMove(e);
        }
      }, { passive: false });
      svg.addEventListener("touchend", () => {
        isTouching = false;
        // Keep HUD visible for 1.8s after touch release so user can comfortably read it
        setTimeout(() => {
          if (!isTouching) onPointerLeave();
        }, 1800);
      });
}

// Export palettes globally
if (typeof window !== "undefined") {
  window.CHART_PALETTES = CHART_PALETTES;
}

