/**
 * Quantity Capital — Neon Glass Interactive Chart Engine (Option 2)
 *
 * Features:
 * - Smooth Catmull-Rom cubic spline interpolation for organic price curves
 * - Dual-phase neon gradient (ice platinum -> electric cyan) with a thin glowing blur underlayer
 * - Integrated Politician and Insider transaction pins snapped directly onto the price spline
 * - High-clarity, large luminous typography for Y-axis price levels and X-axis date intervals
 * - 60 FPS interactive pointer scrubber (mouse hover & mobile touch)
 * - Vertical crosshair line, snapped active node, attached price pill, and bottom date chip
 * - Zero external dependencies, pure native SVG + DOM, fully responsive
 */

function axisPrice(n) {
  if (n >= 1000) return "$" + Math.round(n / 1000) + "k";
  if (Math.abs(n) >= 10 || Math.abs(n - Math.round(n)) < 1e-8) return "$" + Math.round(n);
  if (Math.abs(n) >= 1) return "$" + n.toFixed(1).replace(/\.0$/, "");
  return "$" + n.toFixed(2);
}

function quotePrice(n) {
  if (n >= 10000) return "$" + Math.round(n / 1000) + "k";
  if (n >= 1000) return "$" + (n / 1000).toFixed(1).replace(/\.0$/, "") + "k";
  if (n >= 100) return "$" + Math.round(n);
  if (n >= 10) return "$" + n.toFixed(1).replace(/\.0$/, "");
  return "$" + n.toFixed(2);
}

function niceNum(range, round) {
  const exp = Math.floor(Math.log10(range || 1));
  const mag = Math.pow(10, exp);
  const f = range / mag;
  let nf;
  if (round) {
    if (f < 1.5) nf = 1;
    else if (f < 3) nf = 2;
    else if (f < 7) nf = 5;
    else nf = 10;
  } else if (f <= 1) nf = 1;
  else if (f <= 2) nf = 2;
  else if (f <= 5) nf = 5;
  else nf = 10;
  return nf * mag;
}

function snapAxisTick(v, step) {
  const decimals = step >= 1 ? 0 : (step >= 0.1 ? 1 : 2);
  return Number((Math.round(v / step) * step).toFixed(decimals));
}

function pickCheapStep(span, dataMax) {
  const allowed = dataMax < 1 ? [0.05, 0.1, 0.2, 0.25, 0.5] : [0.25, 0.5, 1];
  let i = 0;
  while (i < allowed.length - 1 && span / allowed[i] > 5) i += 1;
  return allowed[i];
}

function niceAxisScale(dataMin, dataMax) {
  const rawSpan = (dataMax - dataMin) || Math.max(Math.abs(dataMax) * 0.25, dataMax < 1 ? 0.2 : 1);
  let lo = dataMin;
  let hi = dataMax;
  if (lo < 0 && dataMin >= 0) lo = 0;
  let step = dataMax >= 5
    ? Math.max(1, Math.round(niceNum(rawSpan / 4, true)) || 1)
    : pickCheapStep(rawSpan, dataMax);
  let niceMin = snapAxisTick(Math.floor(lo / step) * step, step);
  let niceMax = snapAxisTick(Math.ceil(hi / step) * step, step);
  if (niceMin === niceMax) niceMax = snapAxisTick(niceMin + step, step);
  if (niceMin < 0 && dataMin >= 0) niceMin = 0;
  let ticks = [];
  for (let v = niceMin; v <= niceMax + step * 0.0001; v += step) ticks.push(snapAxisTick(v, step));
  if (ticks.length > 7) {
    step = dataMax >= 5 ? step * 2 : pickCheapStep(rawSpan * 2, dataMax);
    niceMin = snapAxisTick(Math.floor(lo / step) * step, step);
    niceMax = snapAxisTick(Math.ceil(hi / step) * step, step);
    if (niceMin < 0 && dataMin >= 0) niceMin = 0;
    ticks = [];
    for (let v = niceMin; v <= niceMax + step * 0.0001; v += step) ticks.push(snapAxisTick(v, step));
  }
  return { min: niceMin, max: niceMax, ticks: ticks };
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
    glowFilter: "drop-shadow(0 0 5px rgba(6, 182, 212, 0.45)) drop-shadow(0 0 10px rgba(6, 182, 212, 0.22))",
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
  const cssH = Math.round(svg.clientHeight || 0);
  const h = opts.height || (cssH >= 160 ? cssH : (isMobile ? 240 : 300));
  const pad = {
    l: isMobile ? 82 : 34,
    r: isMobile ? 22 : 28,
    t: isMobile ? 24 : 22,
    b: isMobile ? 20 : 20
  };
  svg.setAttribute("viewBox", "0 0 " + w + " " + h);

  // Full height for price curve
  const priceTop = pad.t;
  const priceBot = h - pad.b;
  const priceH = priceBot - priceTop;

  const xs = points.map((p) => p[1]);
  const dataMin = Math.min.apply(null, xs);
  const dataMax = Math.max.apply(null, xs);
  const scale = niceAxisScale(dataMin, dataMax);
  const min = scale.min;
  const max = scale.max;
  const span = max - min || 1;
  const labelWide = scale.ticks.some((t) => axisPrice(t).length >= 5);
  if (!isMobile && labelWide) pad.l = 50;

  const xAt = (i) => pad.l + (i / Math.max(1, points.length - 1)) * (w - pad.l - pad.r);
  const yAt = (px) => priceTop + (1 - (px - min) / span) * priceH;

  // Active color theme (defaults to cyan)
  const themeKey = (opts.theme && CHART_PALETTES[opts.theme]) ? opts.theme : (window.QC_CHART_THEME || "cyan");
  const palette = CHART_PALETTES[themeKey] || CHART_PALETTES.cyan;

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
    const buys = (marks || []).filter((m) => m.side !== "sale" && m.side !== "sale_post" && m.date);
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

  // Y-axis grid lines on whole-dollar nice ticks.
  // Labels are HTML (not SVG text) so preserveAspectRatio="none" cannot stretch them.
  const yTicks = scale.ticks;
  const gridLines = yTicks.map((px) => {
    const y = yAt(px).toFixed(1);
    return "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + y + "\" y2=\"" + y +
      "\" stroke=\"rgba(255,255,255,0.08)\" stroke-width=\"1\" stroke-dasharray=\"3 4\" />";
  }).join("");

  // Visible marks along the spline
  const firstDate = points[0][0];
  const lastDate = points[points.length - 1][0];
  const visibleMarks = (marks || []).filter((m) => m.date >= firstDate && m.date <= lastDate);

  // Cluster overlapping trade marks for clean pins
  const hit = [];

  function clusterByDate(arr) {
    const items = arr.map((m) => {
      const i = idxFor(m.date);
      return { mark: m, i: i, x: xAt(i), date: m.date };
    }).sort((a, b) => {
      if (a.date < b.date) return -1;
      if (a.date > b.date) return 1;
      return a.x - b.x;
    });
    const out = [];
    items.forEach((it) => {
      const last = out[out.length - 1];
      if (last && last.date === it.date) {
        last.items.push(it);
        last.x = last.items.reduce((s, row) => s + row.x, 0) / last.items.length;
      } else {
        out.push({ x: it.x, date: it.date, items: [it] });
      }
    });
    return out;
  }

  const buyClusters = clusterByDate(visibleMarks.filter((m) => m.side !== "sale" && m.side !== "sale_post"));
  const sellClusters = clusterByDate(visibleMarks.filter((m) => m.side === "sale" || m.side === "sale_post"));

  // If a buy cluster and a sell cluster land on the exact same date/spot,
  // separate them slightly horizontally so both remain visible and centered on the price curve.
  buyClusters.forEach((bc) => {
    sellClusters.forEach((sc) => {
      const dist = sc.x - bc.x;
      if (Math.abs(dist) < 16) {
        const shift = (16 - Math.abs(dist)) / 2;
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

  // Counter-scale pins so they stay true circles when the SVG is stretched
  // by preserveAspectRatio="none".
  const svgRect = svg.getBoundingClientRect();
  const stretchX = (svgRect.width > 2) ? svgRect.width / w : 1;
  const stretchY = (svgRect.height > 2) ? svgRect.height / h : 1;
  const pinSX = 1 / stretchX;
  const pinSY = 1 / stretchY;
  const pinScale = "scale(" + pinSX.toFixed(4) + " " + pinSY.toFixed(4) + ")";

  function drawCluster(cluster, sale) {
    const n = cluster.items.length;
    const marksList = cluster.items.map((row) => row.mark);
    const mid = cluster.items[Math.floor((n - 1) / 2)];
    // Snap dead-center onto the price line at this date
    const cy = yAt(points[mid.i][1]);
    const color = sale ? "#f87171" : "#22c55e";
    const haloBg = sale ? "rgba(248, 113, 113, 0.22)" : "rgba(34, 197, 94, 0.22)";
    const id = hit.length;
    const x = cluster.x;

    const baseR = isMobile ? 10 : 9;
    const pinRpx = n <= 1
      ? baseR
      : Math.min(isMobile ? 18 : 16, baseR + 3 + Math.min(5, Math.ceil(Math.log2(n))));
    const haloRpx = pinRpx + (isMobile ? 6 : 5);
    const pinFontPx = n <= 1
      ? (isMobile ? 12 : 11)
      : (n >= 10 ? (isMobile ? 12 : 11) : (isMobile ? 13 : 12));
    const pinTextY = pinFontPx * 0.36;
    const label = n > 1 ? String(n) : (sale ? "S" : "B");

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

    return "<g class=\"chart-mark\" data-i=\"" + id + "\" style=\"cursor:pointer\">" +
      "<circle cx=\"" + x.toFixed(1) + "\" cy=\"" + cy.toFixed(1) + "\" r=\"" + (isMobile ? 24 : 18) + "\" fill=\"transparent\" />" +
      "<g transform=\"translate(" + x.toFixed(1) + " " + cy.toFixed(1) + ") " + pinScale + "\">" +
        "<circle cx=\"0\" cy=\"0\" r=\"" + haloRpx + "\" fill=\"" + haloBg + "\" />" +
        "<circle cx=\"0\" cy=\"0\" r=\"" + pinRpx + "\" fill=\"" + color + "\" stroke=\"#090d14\" stroke-width=\"1.5\" />" +
        "<text x=\"0\" y=\"" + pinTextY.toFixed(1) + "\" text-anchor=\"middle\" fill=\"#ffffff\" font-size=\"" + pinFontPx + "\" font-weight=\"800\" font-family=\"Barlow Condensed, sans-serif\" pointer-events=\"none\">" + label + "</text>" +
      "</g>" +
    "</g>";
  }

  const tradePinsHtml = buyClusters.map((c) => drawCluster(c, false)).join("") +
                        sellClusters.map((c) => drawCluster(c, true)).join("");

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
    // Glowing underlayer (delicate, refined blur)
    "<path d=\"" + spline + "\" fill=\"none\" stroke=\"url(#underGlow)\" stroke-width=\"5.5\" opacity=\"0.55\" stroke-linecap=\"round\" stroke-linejoin=\"round\" />" +
    // Crisp foreground spline (sleek, precision 2.2px line)
    "<path class=\"qc-spline-main\" d=\"" + spline + "\" fill=\"none\" stroke=\"url(#neonGradient)\" stroke-width=\"2.2\" stroke-linecap=\"round\" stroke-linejoin=\"round\" style=\"filter:" + palette.glowFilter + ";\" />" +
    // Terminal point dot
    "<circle cx=\"" + lastPt.x.toFixed(1) + "\" cy=\"" + lastPt.y.toFixed(1) + "\" r=\"5.2\" fill=\"" + palette.light + "\" stroke=\"#ffffff\" stroke-width=\"1.7\" />" +
    // Interactive Trade Pins
    tradePinsHtml +
    // Scrubber elements (updated dynamically on pointermove)
    "<g id=\"qc-scrubber-g\" style=\"display:none; pointer-events:none;\">" +
      "<line id=\"qc-scrub-line\" x1=\"0\" y1=\"" + pad.t + "\" x2=\"0\" y2=\"" + priceBot + "\" stroke=\"rgba(255,255,255,0.22)\" stroke-width=\"1.2\" stroke-dasharray=\"3 3\" />" +
      "<circle id=\"qc-scrub-halo\" cx=\"0\" cy=\"0\" r=\"7.5\" fill=\"rgba(255,255,255,0.24)\" />" +
      "<circle id=\"qc-scrub-dot\" cx=\"0\" cy=\"0\" r=\"3.8\" fill=\"#ffffff\" stroke=\"#0d141f\" stroke-width=\"2\" />" +
    "</g>";

  paintYAxisLabels(wrap, svg, yBox, yTicks, yAt, w, h, pad);

  // Bottom X-axis labels
  if (xBox) {
    const last = points.length - 1;
    const spots = [0, Math.round(last / 4), Math.round(last / 2), Math.round((3 * last) / 4), last];
    const daysSpan = (new Date(String(lastDate) + "T00:00:00") - new Date(String(firstDate) + "T00:00:00")) / 86400000;
    const isShortSpan = daysSpan <= 210 || (opts && (opts.range === "1m" || opts.range === "3m" || opts.range === "6m"));
    xBox.innerHTML = spots.map((i) => "<span>" + axisDate(points[i][0], isShortSpan) + "</span>").join("");
    xBox.style.paddingLeft = pad.l + "px";
  }

  // Hover badges (no end-of-chart last-price pill — last price lives in the page header)
  ensureInteractiveDomElements(wrap, palette);

  // Hook up mark click events
  svg.querySelectorAll(".chart-mark").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.stopPropagation();
      hideChartHoverUi(wrap, svg);
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
  attachScrubberEvents(svg, wrap, pts, w, h);

  if (svgRect.width <= 2) {
    requestAnimationFrame(function () {
      if (svg.getBoundingClientRect().width > 2) drawChart(points, marks, opts);
    });
  }

  return hit;
}

/**
 * Ensure hover badges exist in the chart-wrap.
 * Last price is already on the ticker page — do not draw an end-of-chart pill.
 */
function ensureInteractiveDomElements(wrap, palette) {
  if (!wrap) return;

  const leftoverTerm = wrap.querySelector(".qc-terminal-pill");
  if (leftoverTerm) leftoverTerm.remove();

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

  const leftoverHud = wrap.querySelector(".qc-glass-hud");
  if (leftoverHud) leftoverHud.remove();
}

function syncYAxisOverlayBox(yBox, wrap, svg) {
  if (!yBox || !wrap || !svg) return;
  const wr = wrap.getBoundingClientRect();
  const sr = svg.getBoundingClientRect();
  if (sr.width < 2 || sr.height < 2) return;
  yBox.style.left = (sr.left - wr.left) + "px";
  yBox.style.top = (sr.top - wr.top) + "px";
  yBox.style.width = sr.width + "px";
  yBox.style.height = sr.height + "px";
}

function paintYAxisLabels(wrap, svg, yBox, ticks, yAt, w, h, pad) {
  if (!wrap || !svg || !ticks || !ticks.length) return;
  if (!yBox) {
    yBox = wrap.querySelector("#chart-y");
    if (!yBox) {
      yBox = document.createElement("div");
      yBox.id = "chart-y";
      yBox.className = "chart-y";
      wrap.appendChild(yBox);
    }
  }
  yBox.setAttribute("aria-hidden", "true");
  syncYAxisOverlayBox(yBox, wrap, svg);
  const leftPct = (pad.l / w) * 100;
  yBox.innerHTML = ticks.map((px) => {
    const topPct = (yAt(px) / h) * 100;
    return "<span style=\"top:" + topPct.toFixed(2) + "%;left:" + leftPct.toFixed(2) + "%\">" +
      axisPrice(px) + "</span>";
  }).join("");

  if (!wrap._qcYAxisRO && typeof ResizeObserver !== "undefined") {
    wrap._qcYAxisRO = new ResizeObserver(function () {
      syncYAxisOverlayBox(yBox, wrap, svg);
    });
    wrap._qcYAxisRO.observe(svg);
    wrap._qcYAxisRO.observe(wrap);
  }
}

function hideChartHoverUi(wrap, svg) {
  const hoverPricePill = wrap && wrap.querySelector(".qc-hover-price-pill");
  const hoverDatePill = wrap && wrap.querySelector(".qc-hover-date-pill");
  const scrubG = svg && svg.querySelector("#qc-scrubber-g");
  if (hoverPricePill) hoverPricePill.style.display = "none";
  if (hoverDatePill) hoverDatePill.style.display = "none";
  if (scrubG) scrubG.style.display = "none";
}

/**
 * Handle 60 FPS pointer tracking across mouse and touch devices
 */
function attachScrubberEvents(svg, wrap, pts, w, h) {
  const scrubG = svg.querySelector("#qc-scrubber-g");
  const scrubLine = svg.querySelector("#qc-scrub-line");
  const scrubDot = svg.querySelector("#qc-scrub-dot");
  const scrubHalo = svg.querySelector("#qc-scrub-halo");
  const hoverPricePill = wrap.querySelector(".qc-hover-price-pill");
  const hoverDatePill = wrap.querySelector(".qc-hover-date-pill");

  function onPointerMove(e) {
    const pop = wrap.querySelector("#mark-pop");
    if (pop && !pop.hidden) {
      onPointerLeave();
      return;
    }

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
      if (pillLeft + 72 > wr.width - 8) {
        pillLeft = screenX - 78;
      }
      hoverPricePill.style.left = pillLeft + "px";
      hoverPricePill.style.top = screenY + "px";
      hoverPricePill.textContent = quotePrice(pt.px);
    }

    // Bottom Date Pill
    if (hoverDatePill) {
      hoverDatePill.style.display = "block";
      hoverDatePill.style.left = screenX + "px";
      hoverDatePill.textContent = hoverDateFmt(pt.date);
    }
  }

  function onPointerLeave() {
    if (scrubG) scrubG.style.display = "none";
    if (hoverPricePill) hoverPricePill.style.display = "none";
    if (hoverDatePill) hoverDatePill.style.display = "none";
  }

  svg.style.touchAction = "none";

  let lastTouchAt = 0;
  let isTouchScrubbing = false;
  let didTouchMove = false;
  let touchStartedOnMark = false;
  let lingerTimer = 0;

  function recentTouch() {
    return Date.now() - lastTouchAt < 800;
  }

  function clearLinger() {
    if (lingerTimer) {
      window.clearTimeout(lingerTimer);
      lingerTimer = 0;
    }
  }

  function eventOnMark(e) {
    const t = e.target;
    return !!(t && t.closest && t.closest(".chart-mark"));
  }

  svg.addEventListener("mousemove", (e) => {
    if (recentTouch() || isTouchScrubbing) return;
    onPointerMove(e);
  });
  svg.addEventListener("mouseleave", () => {
    if (recentTouch() || isTouchScrubbing) return;
    onPointerLeave();
  });

  svg.addEventListener("touchstart", (e) => {
    lastTouchAt = Date.now();
    isTouchScrubbing = true;
    didTouchMove = false;
    touchStartedOnMark = eventOnMark(e);
    clearLinger();
    // Wait for an actual drag before showing hover pills so a tap does not flash them.
  }, { passive: true });

  svg.addEventListener("touchmove", (e) => {
    if (!isTouchScrubbing || e.touches.length !== 1) return;
    lastTouchAt = Date.now();
    didTouchMove = true;
    if (touchStartedOnMark) return;
    e.preventDefault();
    onPointerMove(e);
  }, { passive: false });

  svg.addEventListener("touchend", (e) => {
    lastTouchAt = Date.now();
    if (!isTouchScrubbing) return;
    isTouchScrubbing = false;
    const openedMark = touchStartedOnMark || eventOnMark(e);
    touchStartedOnMark = false;
    if (!didTouchMove || openedMark) {
      onPointerLeave();
      return;
    }
    lingerTimer = window.setTimeout(() => {
      lingerTimer = 0;
      if (!isTouchScrubbing) onPointerLeave();
    }, 1800);
  });

  svg.addEventListener("touchcancel", () => {
    lastTouchAt = Date.now();
    isTouchScrubbing = false;
    touchStartedOnMark = false;
    clearLinger();
    onPointerLeave();
  });
}

// Export palettes globally
if (typeof window !== "undefined") {
  window.CHART_PALETTES = CHART_PALETTES;
  window.hideChartHoverUi = hideChartHoverUi;
}

