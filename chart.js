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

function drawChart(points, marks, opts) {
  opts = opts || {};
  const svg = document.getElementById("chart-svg");
  const yBox = document.getElementById("chart-y");
  const xBox = document.getElementById("chart-x");
  if (!svg || !points || points.length < 2) return;
  const stroke = opts.stroke || "#36999d";
  const fill = opts.fill || "rgba(54,153,157,0.12)";
  const w = 720;
  const h = 220;
  const pad = { l: 4, r: 8, t: 10, b: 8 };
  const xs = points.map((p) => p[1]);
  const min = Math.min.apply(null, xs);
  const max = Math.max.apply(null, xs);
  const span = max - min || 1;
  const xAt = (i) => pad.l + (i / Math.max(1, points.length - 1)) * (w - pad.l - pad.r);
  const yAt = (px) => pad.t + (1 - (px - min) / span) * (h - pad.t - pad.b);
  const line = points.map((p, i) => (i ? "L" : "M") + xAt(i).toFixed(1) + " " + yAt(p[1]).toFixed(1)).join(" ");
  const area = line + " L" + xAt(points.length - 1).toFixed(1) + " " + (h - pad.b) +
    " L" + xAt(0).toFixed(1) + " " + (h - pad.b) + " Z";
  const idxFor = (date) => {
    let idx = 0;
    for (let i = 0; i < points.length; i++) {
      if (points[i][0] <= date) idx = i;
      else break;
    }
    return idx;
  };
  const yTicks = [1, 2 / 3, 1 / 3, 0].map((t) => min + span * t);
  const grid = yTicks.map((px) => {
    const y = yAt(px).toFixed(1);
    return "<line x1=\"" + pad.l + "\" x2=\"" + (w - pad.r) + "\" y1=\"" + y + "\" y2=\"" + y +
      "\" stroke=\"#2c3440\" stroke-width=\"1\" />";
  }).join("");
  const dots = (marks || []).map((m) => {
    const i = idxFor(m.date);
    const color = m.side === "sale" ? "#ef7a74" : "#7dcf7a";
    return "<circle cx=\"" + xAt(i).toFixed(1) + "\" cy=\"" + yAt(points[i][1]).toFixed(1) +
      "\" r=\"4.5\" fill=\"" + color + "\" stroke=\"#151b24\" stroke-width=\"1.5\" />";
  }).join("");
  svg.innerHTML = grid +
    "<path d=\"" + area + "\" fill=\"" + fill + "\" />" +
    "<path d=\"" + line + "\" fill=\"none\" stroke=\"" + stroke + "\" stroke-width=\"2\" />" +
    dots;
  if (yBox) {
    yBox.innerHTML = yTicks.map((px) => "<span>" + axisPrice(px) + "</span>").join("");
  }
  if (xBox) {
    const last = points.length - 1;
    const spots = [0, Math.round(last / 3), Math.round((2 * last) / 3), last];
    xBox.innerHTML = spots.map((i) => "<span>" + axisDate(points[i][0]) + "</span>").join("");
  }
}
