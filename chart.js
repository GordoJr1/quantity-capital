// Quantity Capital - High-Performance Dual-Pane Financial Chart Engine
// Top Pane (70%): Clean Price Action & Key Levels
// Bottom Pane (30%): Net Insider / Trade Flow Volume Histogram
// Interactive: Synced Crosshair & Touch Scrubber for Desktop & Mobile

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

function formatVol(n) {
  if (!n) return "$0";
  if (n >= 1e9) return "$" + (n / 1e9).toFixed(1).replace(/\.0$/, "") + "B";
  if (n >= 1e6) return "$" + (n / 1e6).toFixed(1).replace(/\.0$/, "") + "M";
  if (n >= 1e3) return "$" + Math.round(n / 1e3) + "k";
  return "$" + Math.round(n);
}

function tradeValue(m) {
  if (m.value != null && !isNaN(m.value) && m.value > 0) return Number(m.value);
  if (m.shares != null && m.price != null && !isNaN(m.shares) && !isNaN(m.price) && m.shares > 0) {
    return Number(m.shares) * Number(m.price);
  }
  if (m.amount) {
    const v = (typeof QC !== "undefined" && QC.amountHigh) ? QC.amountHigh(m.amount) : 0;
    if (v > 0) return v;
  }
  return 10000;
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
  const pop = document.getElementById("mark-pop");
  const wrap = document.getElementById("chart-wrap");

  if (!svg || !points || points.length < 2) return [];

  const stroke = opts.stroke || "#36999d";
  const w = 720;
  const h = opts.height || 310;
  const pad = { l: 8, r: 16, t: 14, b: 8 };

  svg.setAttribute("viewBox", "0 0 " + w + " " + h);

  // Pane layout breakdown
  // Price pane: top ~67%
  const priceTop = pad.t;
  const priceH = Math.round((h - pad.t - pad.b) * 0.67);
  const priceBot = priceTop + priceH;

  // Split Divider line
  const splitY = priceBot + 8;

  // Volume Flow pane: bottom ~33%
  const volTop = splitY + 14;
  const volBot = h - pad.b;
  const volH = volBot - volTop;

  // Price math
  const xs = points.map((p) => p[1]);
  const min = Math.min.apply(null, xs);
  const max = Math.max.apply(null, xs);
  const span = max - min || 1;

  const xAt = (i) => pad.l + (i / Math.max(1, points.length - 1)) * (w - pad.l - pad.r);
  const yAt = (px) => priceTop + (1 - (px - min) / span) * priceH;

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

  // Price area gradient
  const area = line + " L" + lastX.toFixed(1) + " " + priceBot.toFixed(1) +
    " L" + xAt(0).toFixed(1) + " " + priceBot.toFixed(1) + " Z";

  // Price grid lines (4 ticks)
  const yTicks = [1, 0.66, 0.33, 0].map((t) => min + span * t);
  const priceGrid = yTicks.map((px) => {
    const y = yAt(px).toFixed(1);
    return "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + y + "\" y2=\"" + y +
      "\" stroke=\"#222a36\" stroke-width=\"1\" />";
  }).join("");

  // Volume Aggregation
  const firstDate = points[0][0];
  const lastDate = points[points.length - 1][0];
  const visibleMarks = (marks || []).filter((m) => m.date >= firstDate && m.date <= lastDate);

  const grouped = {};
  visibleMarks.forEach((m) => {
    const i = idxFor(m.date);
    if (!grouped[i]) grouped[i] = { buys: [], sells: [], buyVol: 0, sellVol: 0, date: points[i][0] };
    const val = tradeValue(m);
    if (m.side === "sale") {
      grouped[i].sells.push(m);
      grouped[i].sellVol += val;
    } else {
      grouped[i].buys.push(m);
      grouped[i].buyVol += val;
    }
  });

  let maxBuyVol = 0;
  let maxSellVol = 0;
  Object.keys(grouped).forEach((k) => {
    const g = grouped[k];
    if (g.buyVol > maxBuyVol) maxBuyVol = g.buyVol;
    if (g.sellVol > maxSellVol) maxSellVol = g.sellVol;
  });

  const hasBuys = maxBuyVol > 0;
  const hasSells = maxSellVol > 0;
  const hasVol = hasBuys || hasSells;
  const maxVol = Math.max(maxBuyVol, maxSellVol, 1);

  // Determine zero baseline for volume
  let zeroY;
  let maxUpH, maxDownH;
  if (hasBuys && hasSells) {
    zeroY = Math.round(volTop + volH * 0.5);
    maxUpH = zeroY - volTop - 4;
    maxDownH = volBot - zeroY - 4;
  } else if (hasSells) {
    // Only sales - baseline near top so red bars have full pane
    zeroY = volTop + 4;
    maxUpH = 0;
    maxDownH = volBot - zeroY - 4;
  } else {
    // Only buys or no trades - baseline near bottom
    zeroY = volBot - 4;
    maxUpH = zeroY - volTop - 4;
    maxDownH = 0;
  }

  // Draw volume histogram bars
  const stepX = (w - pad.l - pad.r) / Math.max(1, points.length - 1);
  const barW = Math.max(2.4, Math.min(10, stepX * 1.5));

  const volBars = Object.keys(grouped).map((k) => {
    const i = Number(k);
    const g = grouped[k];
    const cx = xAt(i);
    const bx = (cx - barW / 2).toFixed(1);
    let parts = "";

    if (g.buyVol > 0 && maxUpH > 0) {
      const bh = Math.max(3, (g.buyVol / maxVol) * maxUpH);
      const by = (zeroY - bh).toFixed(1);
      parts += "<rect x=\"" + bx + "\" y=\"" + by + "\" width=\"" + barW.toFixed(1) + "\" height=\"" + bh.toFixed(1) +
        "\" fill=\"#2ecc71\" rx=\"1\" opacity=\"0.9\" />";
    }

    if (g.sellVol > 0 && maxDownH > 0) {
      const sh = Math.max(3, (g.sellVol / maxVol) * maxDownH);
      const sy = zeroY.toFixed(1);
      parts += "<rect x=\"" + bx + "\" y=\"" + sy + "\" width=\"" + barW.toFixed(1) + "\" height=\"" + sh.toFixed(1) +
        "\" fill=\"#e04843\" rx=\"1\" opacity=\"0.9\" />";
    }

    return "<g class=\"vol-group\" data-i=\"" + i + "\">" + parts + "</g>";
  }).join("");

  // Subtle dots on line if trade count is small (<= 15 trades)
  let lineDots = "";
  if (visibleMarks.length > 0 && visibleMarks.length <= 15) {
    lineDots = Object.keys(grouped).map((k) => {
      const i = Number(k);
      const g = grouped[k];
      const cx = xAt(i).toFixed(1);
      const cy = yAt(points[i][1]).toFixed(1);
      const isBuy = g.buyVol >= g.sellVol;
      const color = isBuy ? "#2ecc71" : "#e04843";
      const letter = isBuy ? "B" : "S";
      return "<circle cx=\"" + cx + "\" cy=\"" + cy + "\" r=\"7\" fill=\"" + color + "\" stroke=\"#eef2f5\" stroke-width=\"1.4\" />" +
        "<text x=\"" + cx + "\" y=\"" + (Number(cy) + 2.5).toFixed(1) + "\" text-anchor=\"middle\" fill=\"#ffffff\" font-size=\"7.5\" font-weight=\"700\" font-family=\"Barlow Condensed, sans-serif\">" + letter + "</text>";
    }).join("");
  }

  // Divider labels
  const flowTitle = opts.isInsider ? "INSIDER NET FLOW ($)" : "NET TRADE FLOW ($)";
  const peakLabel = hasVol ? "Peak: " + formatVol(maxVol) : "No trades in range";

  // Full SVG Assembly
  svg.innerHTML =
    "<defs>" +
      "<linearGradient id=\"qc-area\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">" +
        "<stop offset=\"0%\" stop-color=\"" + stroke + "\" stop-opacity=\"0.22\" />" +
        "<stop offset=\"100%\" stop-color=\"" + stroke + "\" stop-opacity=\"0.01\" />" +
      "</linearGradient>" +
    "</defs>" +

    // Top Pane Grid & Line
    priceGrid +
    "<path d=\"" + area + "\" fill=\"url(#qc-area)\" />" +
    "<path d=\"" + line + "\" fill=\"none\" stroke=\"" + stroke + "\" stroke-width=\"2.2\" />" +
    "<circle cx=\"" + lastX.toFixed(1) + "\" cy=\"" + lastY.toFixed(1) +
      "\" r=\"4.2\" fill=\"" + stroke + "\" stroke=\"#eef2f5\" stroke-width=\"1.4\" />" +
    lineDots +

    // Divider Line & Sub-Pane Header
    "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + splitY + "\" y2=\"" + splitY +
      "\" stroke=\"#2e3a4b\" stroke-width=\"1\" stroke-dasharray=\"3 3\" />" +
    "<text x=\"" + pad.l + "\" y=\"" + (splitY + 9) + "\" fill=\"#8b96a3\" font-size=\"8.5\" font-weight=\"700\" font-family=\"Barlow Condensed, sans-serif\" letter-spacing=\"0.1em\">" +
      flowTitle +
    "</text>" +
    "<text x=\"" + (w - pad.r) + "\" y=\"" + (splitY + 9) + "\" fill=\"#8b96a3\" font-size=\"8.5\" font-family=\"Barlow Condensed, sans-serif\" text-anchor=\"end\">" +
      peakLabel +
    "</text>" +

    // Bottom Pane: Zero Baseline & Volume Histogram Bars
    "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + zeroY + "\" y2=\"" + zeroY +
      "\" stroke=\"#384659\" stroke-width=\"1\" />" +
    volBars +

    // Interactive Synced Crosshair
    "<g id=\"qc-crosshair\" style=\"display:none; pointer-events:none;\">" +
      "<line id=\"qc-ch-v\" x1=\"0\" x2=\"0\" y1=\"" + priceTop + "\" y2=\"" + volBot + "\" stroke=\"#e3b41a\" stroke-width=\"1.4\" stroke-dasharray=\"3 3\" />" +
      "<circle id=\"qc-ch-dot\" cx=\"0\" cy=\"0\" r=\"5\" fill=\"#e3b41a\" stroke=\"#ffffff\" stroke-width=\"1.5\" />" +
    "</g>" +

    // Invisible Full-Canvas Hit Overlay for Touch & Mouse Scrubbing
    "<rect id=\"qc-hit-rect\" x=\"0\" y=\"0\" width=\"" + w + "\" height=\"" + h + "\" fill=\"transparent\" style=\"cursor:crosshair;\" />";

  // Y-axis Labels
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

  // X-axis Dates
  if (xBox) {
    const last = points.length - 1;
    const spots = [0, Math.round(last / 3), Math.round((2 * last) / 3), last];
    xBox.innerHTML = spots.map((i) => "<span>" + axisDate(points[i][0]) + "</span>").join("");
  }

  // Synced Crosshair & Scrubber Interaction
  const ch = svg.querySelector("#qc-crosshair");
  const chLine = svg.querySelector("#qc-ch-v");
  const chDot = svg.querySelector("#qc-ch-dot");
  const hitOverlay = svg.querySelector("#qc-hit-rect");

  function hideTooltip() {
    if (ch) ch.style.display = "none";
    if (pop) pop.hidden = true;
  }

  function handleScrub(clientX) {
    if (!wrap) return;
    const wr = wrap.getBoundingClientRect();
    const sr = svg.getBoundingClientRect();
    const relX = clientX - sr.left;
    const pct = Math.max(0, Math.min(1, relX / sr.width));
    const svgX = pad.l + pct * (w - pad.l - pad.r);

    let closestIdx = 0;
    let minDiff = Infinity;
    for (let i = 0; i < points.length; i++) {
      const px = xAt(i);
      const diff = Math.abs(px - svgX);
      if (diff < minDiff) {
        minDiff = diff;
        closestIdx = i;
      }
    }

    const pt = points[closestIdx];
    const date = pt[0];
    const price = pt[1];
    const cx = xAt(closestIdx);
    const cy = yAt(price);

    // Update crosshair
    if (ch && chLine && chDot) {
      chLine.setAttribute("x1", cx.toFixed(1));
      chLine.setAttribute("x2", cx.toFixed(1));
      chDot.setAttribute("cx", cx.toFixed(1));
      chDot.setAttribute("cy", cy.toFixed(1));
      ch.style.display = "block";
    }

    // Update popover
    if (pop) {
      const g = grouped[closestIdx];
      let metaHtml = "";
      let tradeHtml = "";

      if (g) {
        const parts = [];
        if (g.sells.length) {
          parts.push("<span style=\"color:#e04843; font-weight:700;\">" + g.sells.length + " " + (g.sells.length === 1 ? "Sale" : "Sales") + " (" + formatVol(g.sellVol) + ")</span>");
        }
        if (g.buys.length) {
          parts.push("<span style=\"color:#2ecc71; font-weight:700;\">" + g.buys.length + " " + (g.buys.length === 1 ? "Buy" : "Buys") + " (" + formatVol(g.buyVol) + ")</span>");
        }
        metaHtml = "<div class=\"pop-meta\">" + parts.join(" · ") + "</div>";

        // Show top filers if available
        const allDeals = g.buys.concat(g.sells);
        const filers = [];
        const seen = {};
        allDeals.forEach((t) => {
          const name = t.filer || "";
          if (name && !seen[name]) {
            seen[name] = true;
            filers.push(name);
          }
        });
        if (filers.length) {
          const shown = filers.slice(0, 2).join(", ") + (filers.length > 2 ? " +" + (filers.length - 2) + " more" : "");
          tradeHtml = "<div style=\"color:#d8dde3; font-size:0.75rem; margin-top:3px;\">" + shown + "</div>";
        }
      }

      pop.innerHTML =
        "<div style=\"display:flex; justify-content:space-between; align-items:center; gap:8px;\">" +
          "<b style=\"color:#eef2f5;\">" + axisPrice(price) + "</b>" +
          "<span style=\"color:#e3b41a; font-family:'Barlow Condensed',sans-serif; font-size:0.85rem; font-weight:700;\">" + (date || "") + "</span>" +
        "</div>" +
        metaHtml +
        tradeHtml;

      const padLimit = 100;
      let left = (sr.left - wr.left) + (cx / w) * sr.width;
      const top = (sr.top - wr.top) + (cy / h) * sr.height;
      left = Math.max(padLimit, Math.min(wr.width - padLimit, left));

      pop.style.left = left + "px";
      pop.style.top = top + "px";
      pop.style.transform = (cy / h) < 0.35
        ? "translate(-50%, 16px)"
        : "translate(-50%, calc(-100% - 14px))";
      pop.hidden = false;
    }
  }

  if (hitOverlay) {
    hitOverlay.addEventListener("mousemove", (e) => {
      handleScrub(e.clientX);
    });

    hitOverlay.addEventListener("mouseleave", () => {
      hideTooltip();
    });

    hitOverlay.addEventListener("touchstart", (e) => {
      if (e.touches && e.touches[0]) {
        handleScrub(e.touches[0].clientX);
      }
    }, { passive: true });

    hitOverlay.addEventListener("touchmove", (e) => {
      if (e.touches && e.touches[0]) {
        handleScrub(e.touches[0].clientX);
      }
    }, { passive: true });

    hitOverlay.addEventListener("touchend", () => {
      // Keep tooltip visible for 2 seconds on mobile so it can be read after lifting thumb
      setTimeout(hideTooltip, 2200);
    });
  }

  return grouped;
}
