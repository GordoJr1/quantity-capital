const CATALOG_URL = "claims/companies.json";
const QUEBEC_CENTER = [-72.5, 51.5];
const CANADA_CENTER = [-96, 56];
const NEARBY_PAD_DEG = 0.2;
const CSV_FIELDS = [
  "holder", "claim_id", "claim_name", "status", "recorded_date",
  "anniversary_or_expiry", "area_ha", "tenure_type", "jurisdiction",
  "source", "as_of", "role", "company_id",
];

const map = new maplibregl.Map({
  container: "map",
  style: {
    version: 8,
    sources: {
      esri: {
        type: "raster",
        tiles: [
          "https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}",
        ],
        tileSize: 256,
        attribution: "Tiles &copy; Esri &mdash; Esri, OSM contributors",
        maxzoom: 16,
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#0b1016" } },
      { id: "basemap", type: "raster", source: "esri" },
    ],
  },
  center: CANADA_CENTER,
  zoom: 3.4,
  attributionControl: true,
});

window.qcClaimsMap = map;
map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "bottom-right");
map.addControl(new maplibregl.ScaleControl({ maxWidth: 140, unit: "metric" }), "bottom-right");
map.on("load", () => map.resize());

let popup = null;
let catalog = null;
let searchIndex = [];
let hiddenHolders = new Set();
let extractCache = {};
let urlCache = {};
let viewGen = 0;
let currentCompany = null;
let currentAssetId = null;
let paintedFc = { type: "FeatureCollection", features: [] };
let allLoad = null;
let extractsReady = false;

// Same-holder needles as the first producer extract batch (GESTIM / MLAS / MTA).
const FEATURED_NEEDLES = {
  iamgold: ["iamgold"],
  "agnico-eagle": ["agnico"],
  barrick: ["barrick"],
  "gold-fields": ["groupe minier windfall"],
  "alamos-gold": ["alamos gold"],
  "eldorado-gold": ["eldorado gold"],
  "wesdome-gold-mines": ["wesdome"],
  newmont: ["newmont", "pretium"],
  "centerra-gold": ["thompson creek", "centerra"],
  "artemis-gold": ["bw gold", "artemis"],
  evolution: ["evolution"],
  "equinox-gold": ["greenstone", "musselwhite", "equinox"],
};

const NAME_DROPS = {
  inc: 1, ltd: 1, ltee: 1, limited: 1, limitee: 1, corp: 1, corporation: 1,
  co: 1, company: 1, the: 1, llc: 1, ulc: 1, plc: 1, lp: 1, llp: 1,
};

function normName(s) {
  let t = String(s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  t = t.replace(/\(\s*[\d.]+\s*\)/g, " ");
  t = t.replace(/&/g, " and ");
  t = t.replace(/[^a-z0-9]+/g, " ").trim();
  return t.split(/\s+/).filter((p) => p && !NAME_DROPS[p]).join(" ");
}

function featuredIdForHolder(name) {
  const n = String(name || "").toLowerCase();
  if (!n) return null;
  const keys = Object.keys(FEATURED_NEEDLES);
  for (let i = 0; i < keys.length; i++) {
    const id = keys[i];
    const needles = FEATURED_NEEDLES[id];
    for (let j = 0; j < needles.length; j++) {
      if (n.indexOf(needles[j]) !== -1) return id;
    }
  }
  return null;
}

function slugName(s) {
  const n = normName(s) || "holder";
  return n.replace(/\s+/g, "-").slice(0, 80);
}

function findIndexed(id) {
  return searchIndex.find((c) => c.id === id) || (catalog.companies || []).find((c) => c.id === id);
}

function buildSearchIndex() {
  const featured = catalog.companies || [];
  const entries = featured.map((c) => {
    const extra = FEATURED_NEEDLES[c.id] || [];
    const names = (c.names || []).slice();
    extra.forEach((n) => {
      if (names.indexOf(n) === -1) names.push(n);
    });
    return Object.assign({}, c, { kind: "featured", names: names });
  });
  const usedIds = {};
  entries.forEach((c) => { usedIds[c.id] = 1; });
  const lumps = {};
  featured.forEach((c) => {
    (c.neighbors || []).forEach((row) => {
      const holder = row.holder;
      if (!holder || featuredIdForHolder(holder)) return;
      const key = normName(holder);
      if (!key) return;
      let e = lumps[key];
      if (!e) {
        let id = slugName(holder);
        if (usedIds[id]) id = id + "-holder";
        usedIds[id] = 1;
        e = {
          id: id,
          kind: "holder",
          holder: holder,
          names: [holder],
          color: "#e8b040",
          sources: [],
          quebec_count: 0,
          ontario_count: 0,
          bc_count: 0,
          claim_count: 0,
          neighbor_count: 0,
          extract: null,
          mines: [],
          neighbors: [],
        };
        lumps[key] = e;
      } else if (e.names.indexOf(holder) === -1) {
        e.names.push(holder);
      }
      e.sources.push({ companyId: c.id, count: row.count || 0 });
    });
  });
  Object.keys(lumps).forEach((key) => {
    const e = lumps[key];
    e.sources.sort((a, b) => (b.count || 0) - (a.count || 0));
    const primary = featured.find((c) => c.id === e.sources[0].companyId);
    if (primary && primary.extract) {
      e.extract = primary.extract;
      e.quebec_count = e.sources[0].count || 0;
      e.claim_count = e.quebec_count;
    }
    entries.push(e);
  });
  searchIndex = entries;
}

function assetUrl(name) {
  return new URL(name, window.location.href).href;
}

function provinceOn(id) {
  const el = document.getElementById(id);
  return !!(el && el.checked);
}

function closePopup() {
  if (popup) {
    popup.remove();
    popup = null;
  }
}

function popupHtml(p) {
  const row = (label, value) =>
    "<dt>" + label + "</dt><dd>" + (value == null || value === "" ? "—" : value) + "</dd>";
  const ha = p.area_ha == null || p.area_ha === "" ? "—" : p.area_ha + " ha";
  const role = p.role === "focus" ? "Company claim" : "Neighbor";
  return "<div class=\"pop\"><div class=\"holder\">" + (p.holder || "Unknown holder") + "</div><dl>" +
    row("Role", role) +
    row("Claim", p.claim_id) +
    row("Name", p.claim_name) +
    row("Status", p.status) +
    row("Recorded", p.recorded_date) +
    row("Expiry", p.anniversary_or_expiry) +
    row("Area", ha) +
    row("Type", p.tenure_type) +
    row("Where", p.jurisdiction) +
    row("Source", p.source) +
    row("As of", p.as_of) +
    "</dl></div>";
}

function openProps(lngLat, props) {
  closePopup();
  popup = new maplibregl.Popup({ closeOnClick: false, maxWidth: "320px" })
    .setLngLat(lngLat)
    .setHTML(popupHtml(props))
    .addTo(map);
}

function setHud(text) {
  const el = document.getElementById("hud-sub");
  if (el) el.textContent = text;
}

function setStatus(extra) {
  const el = document.getElementById("status");
  const asOf = catalog && (catalog.sources && catalog.sources.as_of_on_bc || catalog.as_of) ? (catalog.sources && catalog.sources.as_of_on_bc || catalog.as_of) : "";
  const base = "Quebec GESTIM · Ontario MLAS (unofficial viewing) · BC MTA" +
    (asOf ? " · " + asOf : "") +
    " · <span style=\"color:#d97a6c\">Not legal title.</span> Confirm on GESTIM / MLAS / Mineral Titles.";
  el.innerHTML = extra ? extra + " · " + base : base;
}

function companyMatches(c, needle) {
  if ((c.names || []).some((n) => String(n).toLowerCase().includes(needle))) return true;
  if (String(c.holder || "").toLowerCase().includes(needle)) return true;
  if (String(c.id || "").toLowerCase().includes(needle)) return true;
  const nds = FEATURED_NEEDLES[c.id];
  if (nds && nds.some((n) => n.includes(needle) || needle.includes(n))) return true;
  return false;
}

function hitScore(c, needle) {
  const names = (c.names || []).concat([c.holder, c.id]).map((s) => String(s || "").toLowerCase());
  let s = 0;
  if (names.some((n) => n === needle)) s += 100;
  if (names.some((n) => n.startsWith(needle))) s += 40;
  if (c.kind !== "holder") s += 25;
  if (hasAnyExtract(c)) s += 8;
  s += Math.min(15, Math.log10((c.claim_count || 0) + 1) * 8);
  return s;
}

function matchCompanies(q) {
  const needle = q.trim().toLowerCase();
  if (!needle || !searchIndex.length) return [];
  return searchIndex
    .filter((c) => companyMatches(c, needle))
    .sort((a, b) => hitScore(b, needle) - hitScore(a, needle))
    .slice(0, 40);
}

function titleBits(c) {
  const bits = [];
  if ((c.quebec_count || 0) > 0) bits.push((c.quebec_count || 0).toLocaleString("en-CA") + " QC");
  if ((c.ontario_count || 0) > 0) bits.push((c.ontario_count || 0).toLocaleString("en-CA") + " ON");
  if ((c.bc_count || 0) > 0) bits.push((c.bc_count || 0).toLocaleString("en-CA") + " BC");
  if (!bits.length) return "0 titles in QC/ON/BC extracts";
  if (c.kind === "holder") return bits.join(" · ") + " · in committed extracts";
  return bits.join(" · ") + " · whole company";
}

function renderHits(hits) {
  const box = document.getElementById("search-results");
  if (!hits.length) {
    box.hidden = true;
    box.innerHTML = "";
    return;
  }
  box.hidden = false;
  box.innerHTML = hits.map((c, i) =>
    "<button type=\"button\" class=\"hit" + (i === 0 ? " active" : "") + "\" data-id=\"" + c.id + "\">" +
      "<div class=\"who\">" + (c.holder || c.names[0]) + "</div>" +
      "<div class=\"meta\">" + titleBits(c) + "</div>" +
    "</button>"
  ).join("");
  box.querySelectorAll("button.hit").forEach((btn) => {
    btn.addEventListener("click", () => selectCompany(btn.dataset.id));
  });
}

function applyHolderFilter() {
  if (!map.getLayer("focus-fill")) return;
  const hide = Array.from(hiddenHolders);
  const notHidden = hide.length
    ? ["!", ["in", ["get", "holder"], ["literal", hide]]]
    : true;
  if (currentCompany) {
    map.setFilter("neighbor-fill", ["all", ["==", ["get", "role"], "neighbor"], notHidden]);
    map.setFilter("neighbor-line", ["all", ["==", ["get", "role"], "neighbor"], notHidden]);
    if (map.getLayer("neighbor-dot")) {
      map.setFilter("neighbor-dot", ["all", ["==", ["get", "role"], "neighbor"], notHidden]);
    }
    map.setFilter("focus-fill", ["==", ["get", "role"], "focus"]);
    map.setFilter("focus-line", ["==", ["get", "role"], "focus"]);
    if (map.getLayer("focus-dot")) {
      map.setFilter("focus-dot", ["==", ["get", "role"], "focus"]);
    }
  } else {
    map.setFilter("focus-fill", ["all", ["==", ["get", "role"], "focus"], notHidden]);
    map.setFilter("focus-line", ["all", ["==", ["get", "role"], "focus"], notHidden]);
    if (map.getLayer("focus-dot")) {
      map.setFilter("focus-dot", ["all", ["==", ["get", "role"], "focus"], notHidden]);
    }
    map.setFilter("neighbor-fill", ["==", ["get", "role"], "neighbor"]);
    map.setFilter("neighbor-line", ["==", ["get", "role"], "neighbor"]);
    if (map.getLayer("neighbor-dot")) {
      map.setFilter("neighbor-dot", ["==", ["get", "role"], "neighbor"]);
    }
  }
}

function paintAllLegend(features) {
  const box = document.getElementById("legend");
  const hint = document.getElementById("company-hint");
  const counts = {};
  const order = [];
  (features || []).forEach((f) => {
    const id = (f.properties && f.properties.company_id) || "";
    if (!id) return;
    if (!counts[id]) {
      counts[id] = 0;
      order.push(id);
    }
    counts[id] += 1;
  });
  if (!order.length) {
    box.hidden = true;
    hint.hidden = false;
    hint.textContent = "No titles in the provinces that are switched on.";
    return;
  }
  hint.hidden = true;
  box.hidden = false;
  const rows = order.map((id) => {
    const c = findIndexed(id) || { holder: id, color: "#e8b040" };
    return { holder: c.holder, count: counts[id], color: c.color || "#e8b040" };
  }).sort((a, b) => (b.count || 0) - (a.count || 0));
  box.innerHTML = "<h2>Holders</h2>" + rows.map((r) => {
    const on = !hiddenHolders.has(r.holder);
    return "<label class=\"swatch\"><input type=\"checkbox\" data-holder=\"" +
      r.holder.replace(/"/g, "&quot;") + "\"" + (on ? " checked" : "") +
      "> <span class=\"chip\" style=\"background:" + r.color + "\"></span>" +
      "<span class=\"nm\">" + r.holder + "</span>" +
      "<span class=\"n\">" + (r.count || 0).toLocaleString("en-CA") + "</span></label>";
  }).join("");
  box.querySelectorAll("input[data-holder]").forEach((input) => {
    input.addEventListener("change", () => {
      const name = input.getAttribute("data-holder");
      if (input.checked) hiddenHolders.delete(name);
      else hiddenHolders.add(name);
      applyHolderFilter();
    });
  });
}

function paintLegend(company, features) {
  const box = document.getElementById("legend");
  const hint = document.getElementById("company-hint");
  if (!company) {
    paintAllLegend(features);
    return;
  }
  const vis = visibleCounts(company);
  if (vis.total === 0) {
    box.hidden = true;
    hint.hidden = false;
    hint.textContent = "No titles in the provinces that are switched on.";
    return;
  }
  hint.hidden = true;
  box.hidden = false;
  const extra = (company.neighbors || []).length > 10 && provinceOn("ly-quebec")
    ? "<p class=\"hint\">" + ((company.neighbors || []).length - 10) + " more Quebec neighbor holders share a gray.</p>"
    : "";
  const rows = [
    { holder: company.holder, count: vis.total, color: company.color, focus: true },
  ].concat(provinceOn("ly-quebec") ? (company.neighbors || []).slice(0, 10) : []);
  box.innerHTML = "<h2>Holders</h2>" + rows.map((r) => {
    const on = r.focus || !hiddenHolders.has(r.holder);
    return "<label class=\"swatch\"><input type=\"checkbox\" data-holder=\"" +
      r.holder.replace(/"/g, "&quot;") + "\"" + (on ? " checked" : "") + (r.focus ? " disabled" : "") +
      "> <span class=\"chip\" style=\"background:" + r.color + "\"></span>" +
      "<span class=\"nm\">" + r.holder + "</span>" +
      "<span class=\"n\">" + (r.count || 0).toLocaleString("en-CA") + "</span></label>";
  }).join("") + extra;
  box.querySelectorAll("input[data-holder]").forEach((input) => {
    if (input.disabled) return;
    input.addEventListener("change", () => {
      const name = input.getAttribute("data-holder");
      if (input.checked) hiddenHolders.delete(name);
      else hiddenHolders.add(name);
      applyHolderFilter();
    });
  });
}

function ensureCompanyLayers() {
  if (map.getSource("company")) return;
  map.addSource("company", {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
    tolerance: 0.75,
  });
  map.addSource("company-dots", {
    type: "geojson",
    data: { type: "FeatureCollection", features: [] },
  });
  map.addLayer({
    id: "neighbor-fill",
    type: "fill",
    source: "company",
    filter: ["==", ["get", "role"], "neighbor"],
    paint: {
      "fill-color": ["coalesce", ["get", "color"], "#90a4ae"],
      "fill-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.7, 8, 0.38],
    },
  });
  map.addLayer({
    id: "neighbor-line",
    type: "line",
    source: "company",
    filter: ["==", ["get", "role"], "neighbor"],
    paint: {
      "line-color": ["coalesce", ["get", "color"], "#90a4ae"],
      "line-width": ["interpolate", ["linear"], ["zoom"], 3, 1.6, 8, 0.4],
    },
  });
  map.addLayer({
    id: "focus-fill",
    type: "fill",
    source: "company",
    filter: ["==", ["get", "role"], "focus"],
    paint: {
      "fill-color": ["coalesce", ["get", "color"], "#e8b040"],
      "fill-opacity": ["interpolate", ["linear"], ["zoom"], 3, 0.85, 8, 0.55],
    },
  });
  map.addLayer({
    id: "focus-line",
    type: "line",
    source: "company",
    filter: ["==", ["get", "role"], "focus"],
    paint: {
      "line-color": "#f3d48a",
      "line-width": ["interpolate", ["linear"], ["zoom"], 3, 2.2, 8, 0.8],
    },
  });
  map.addLayer({
    id: "neighbor-dot",
    type: "circle",
    source: "company-dots",
    maxzoom: 6.5,
    filter: ["==", ["get", "role"], "neighbor"],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 1.6, 6, 2.4],
      "circle-color": ["coalesce", ["get", "color"], "#90a4ae"],
      "circle-opacity": 0.55,
    },
  });
  map.addLayer({
    id: "focus-dot",
    type: "circle",
    source: "company-dots",
    maxzoom: 6.5,
    filter: ["==", ["get", "role"], "focus"],
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 3, 2.2, 6, 3.2],
      "circle-color": ["coalesce", ["get", "color"], "#e8b040"],
      "circle-opacity": 0.8,
    },
  });
  ["focus-fill", "neighbor-fill", "focus-dot", "neighbor-dot"].forEach((id) => {
    map.on("click", id, (e) => {
      if (!e.features || !e.features.length) return;
      openProps(e.lngLat, e.features[0].properties);
    });
    map.on("mouseenter", id, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", id, () => { map.getCanvas().style.cursor = ""; });
  });
}

function mergeBbox(a, b) {
  if (!a || a.length !== 4) return b;
  if (!b || b.length !== 4) return a;
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[2], b[2]), Math.max(a[3], b[3])];
}

function expandBbox(b, pad) {
  if (!b || b.length !== 4) return b;
  return [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad];
}

function bboxIntersects(a, b) {
  if (!a || !b) return false;
  return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
}

function visibleCounts(company) {
  const qc = provinceOn("ly-quebec") ? (company.quebec_count || 0) : 0;
  const on = provinceOn("ly-ontario") ? (company.ontario_count || 0) : 0;
  const bc = provinceOn("ly-bc") ? (company.bc_count || 0) : 0;
  return { qc, on, bc, total: qc + on + bc };
}

function visibleBbox(company) {
  let b = null;
  if (provinceOn("ly-quebec")) b = mergeBbox(b, company.bbox);
  if (provinceOn("ly-ontario")) b = mergeBbox(b, company.ontario_bbox);
  if (provinceOn("ly-bc")) b = mergeBbox(b, company.bc_bbox);
  return b;
}

function fitCompany(company, asset) {
  if (asset && Number.isFinite(asset.lat) && Number.isFinite(asset.lon)) {
    map.fitBounds(
      [[asset.lon - 0.35, asset.lat - 0.22], [asset.lon + 0.35, asset.lat + 0.22]],
      { padding: 48, duration: 900, maxZoom: 11 }
    );
    return;
  }
  const b = visibleBbox(company);
  if (!b || b.length !== 4) return;
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 56, duration: 1100, maxZoom: 9 });
}

function fitFc(fc, maxZoom) {
  const b = bboxFromFc(fc);
  if (!b || b.length !== 4) {
    map.easeTo({ center: CANADA_CENTER, zoom: 3.4, duration: 800 });
    return;
  }
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 56, duration: 1100, maxZoom: maxZoom || 6 });
}

function whenMapReady(fn) {
  if (map.loaded()) fn();
  else map.once("load", fn);
}

function filterByProvince(fc) {
  const allow = {
    Quebec: provinceOn("ly-quebec"),
    Ontario: provinceOn("ly-ontario"),
    "British Columbia": provinceOn("ly-bc"),
  };
  const features = (fc.features || []).filter((f) => {
    const j = (f.properties && f.properties.jurisdiction) || "Quebec";
    return allow[j] !== false;
  });
  return { type: "FeatureCollection", features };
}

async function loadOne(url) {
  if (urlCache[url]) return urlCache[url];
  const res = await fetch(assetUrl(url));
  if (!res.ok) throw new Error("Could not load " + url);
  const json = await res.json();
  urlCache[url] = json;
  return json;
}

function walkCoords(geom, fn) {
  if (!geom || !geom.coordinates) return;
  const t = geom.type;
  const c = geom.coordinates;
  if (t === "Polygon") c.forEach((ring) => ring.forEach(fn));
  else if (t === "MultiPolygon") c.forEach((poly) => poly.forEach((ring) => ring.forEach(fn)));
  else if (t === "Point") fn(c);
  else if (t === "MultiPoint" || t === "LineString") c.forEach(fn);
  else if (t === "MultiLineString") c.forEach((line) => line.forEach(fn));
}

function bboxFromFc(fc) {
  let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity, n = 0;
  (fc.features || []).forEach((f) => {
    walkCoords(f.geometry, (xy) => {
      if (!xy || xy.length < 2) return;
      n += 1;
      minx = Math.min(minx, xy[0]);
      miny = Math.min(miny, xy[1]);
      maxx = Math.max(maxx, xy[0]);
      maxy = Math.max(maxy, xy[1]);
    });
  });
  return n ? [minx, miny, maxx, maxy] : null;
}

function featureBbox(f) {
  if (f.__b) return f.__b;
  f.__b = bboxFromFc({ type: "FeatureCollection", features: [f] });
  return f.__b;
}

function claimKey(p) {
  return String((p && p.jurisdiction) || "") + "|" + String((p && p.claim_id) || "");
}

function filterToHolders(fc, company) {
  const exact = {};
  const norms = {};
  (company.names || []).forEach((n) => {
    exact[String(n).toLowerCase()] = 1;
    const k = normName(n);
    if (k) norms[k] = 1;
  });
  const features = [];
  (fc.features || []).forEach((f) => {
    const h = (f.properties && f.properties.holder) || "";
    if (!exact[h.toLowerCase()] && !norms[normName(h)]) return;
    const props = Object.assign({}, f.properties, {
      role: "focus",
      color: company.color || "#e8b040",
      company_id: company.id,
    });
    features.push(Object.assign({}, f, { properties: props }));
  });
  return { type: "FeatureCollection", features: features };
}

function stampCompanyId(fc, companyId) {
  (fc.features || []).forEach((f) => {
    if (!f.properties) f.properties = {};
    if (!f.properties.company_id) f.properties.company_id = companyId;
  });
}

async function loadExtract(company) {
  if (extractCache[company.id]) return extractCache[company.id];
  if (company.kind === "holder") {
    if (!company.extract) {
      extractCache[company.id] = { type: "FeatureCollection", features: [] };
      return extractCache[company.id];
    }
    const raw = await loadOne(company.extract);
    const filtered = filterToHolders(raw, company);
    const byJ = { Quebec: 0, Ontario: 0, "British Columbia": 0 };
    filtered.features.forEach((f) => {
      const j = (f.properties && f.properties.jurisdiction) || "Quebec";
      if (byJ[j] != null) byJ[j] += 1;
    });
    company.quebec_count = byJ.Quebec;
    company.ontario_count = byJ.Ontario;
    company.bc_count = byJ["British Columbia"];
    company.claim_count = filtered.features.length;
    company.bbox = bboxFromFc(filtered);
    extractCache[company.id] = filtered;
    return filtered;
  }
  const jobs = [];
  if (company.extract) jobs.push(loadOne(company.extract));
  if (company.ontario_extract) jobs.push(loadOne(company.ontario_extract));
  if (company.bc_extract) jobs.push(loadOne(company.bc_extract));
  if (!jobs.length) {
    extractCache[company.id] = { type: "FeatureCollection", features: [] };
    return extractCache[company.id];
  }
  const parts = await Promise.all(jobs);
  const features = [];
  parts.forEach((p) => { (p.features || []).forEach((f) => features.push(f)); });
  const fc = { type: "FeatureCollection", features };
  stampCompanyId(fc, company.id);
  extractCache[company.id] = fc;
  return fc;
}

function loadAllExtracts() {
  if (allLoad) return allLoad;
  const companies = (catalog.companies || []).filter(hasAnyExtract);
  allLoad = Promise.all(companies.map((c) =>
    loadExtract(c).catch(() => {
      extractCache[c.id] = extractCache[c.id] || { type: "FeatureCollection", features: [] };
      return extractCache[c.id];
    })
  )).then((rows) => {
    extractsReady = true;
    return rows;
  });
  return allLoad;
}

function hasAnyExtract(company) {
  return !!(company.extract || company.ontario_extract || company.bc_extract);
}

function isFocusFeature(f) {
  const role = f.properties && f.properties.role;
  return role !== "neighbor";
}

function collectAllFocus() {
  const features = [];
  const seen = {};
  (catalog.companies || []).forEach((c) => {
    const fc = extractCache[c.id];
    if (!fc) return;
    (fc.features || []).forEach((f) => {
      if (!isFocusFeature(f)) return;
      const p = f.properties || {};
      const key = claimKey(p);
      if (seen[key]) return;
      seen[key] = 1;
      const props = Object.assign({}, p, {
        role: "focus",
        color: c.color || p.color || "#e8b040",
        company_id: c.id,
      });
      features.push(Object.assign({}, f, { properties: props }));
    });
  });
  return filterByProvince({ type: "FeatureCollection", features });
}

function companyPlusNearbyFc(company) {
  const own = extractCache[company.id] || { type: "FeatureCollection", features: [] };
  const ownVis = filterByProvince(own);
  const features = [];
  const seen = {};
  ownVis.features.forEach((f) => {
    const p = f.properties || {};
    const key = claimKey(p);
    if (seen[key]) return;
    seen[key] = 1;
    const role = p.role === "neighbor" ? "neighbor" : "focus";
    const props = Object.assign({}, p, {
      role: role,
      color: role === "focus" ? (company.color || p.color || "#e8b040") : (p.color || "#90a4ae"),
      company_id: p.company_id || company.id,
    });
    features.push(Object.assign({}, f, { properties: props }));
  });
  const focusOnly = {
    type: "FeatureCollection",
    features: ownVis.features.filter(isFocusFeature),
  };
  const box = expandBbox(bboxFromFc(focusOnly) || visibleBbox(company), NEARBY_PAD_DEG);
  if (box) {
    (catalog.companies || []).forEach((c) => {
      if (c.id === company.id) return;
      const fc = extractCache[c.id];
      if (!fc) return;
      filterByProvince(fc).features.forEach((f) => {
        if (!isFocusFeature(f)) return;
        const p = f.properties || {};
        const key = claimKey(p);
        if (seen[key]) return;
        if (!bboxIntersects(featureBbox(f), box)) return;
        seen[key] = 1;
        const props = Object.assign({}, p, {
          role: "neighbor",
          color: c.color || p.color || "#90a4ae",
          company_id: c.id,
        });
        features.push(Object.assign({}, f, { properties: props }));
      });
    });
  }
  return { type: "FeatureCollection", features };
}

function centroidsFc(fc) {
  const features = [];
  (fc.features || []).forEach((f) => {
    const b = featureBbox(f);
    if (!b) return;
    features.push({
      type: "Feature",
      properties: f.properties || {},
      geometry: { type: "Point", coordinates: [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2] },
    });
  });
  return { type: "FeatureCollection", features };
}

function setPainted(fc) {
  paintedFc = fc;
  map.resize();
  map.getSource("company").setData(fc);
  if (map.getSource("company-dots")) map.getSource("company-dots").setData(centroidsFc(fc));
  applyHolderFilter();
}

function jurisdictionBits(features) {
  const n = { Quebec: 0, Ontario: 0, "British Columbia": 0 };
  (features || []).forEach((f) => {
    const j = (f.properties && f.properties.jurisdiction) || "Quebec";
    if (n[j] != null) n[j] += 1;
  });
  const bits = [];
  if (n.Quebec) bits.push(n.Quebec.toLocaleString("en-CA") + " QC");
  if (n.Ontario) bits.push(n.Ontario.toLocaleString("en-CA") + " ON");
  if (n["British Columbia"]) bits.push(n["British Columbia"].toLocaleString("en-CA") + " BC");
  return bits;
}

function paintAllClaims(fit) {
  ensureCompanyLayers();
  const data = collectAllFocus();
  setPainted(data);
  if (fit) fitFc(data, 6);
  paintLegend(null, data.features);
  setHud("All claims · QC / ON / BC");
  const bits = jurisdictionBits(data.features);
  const extra = "<strong>All companies</strong>" +
    (bits.length ? " · " + bits.join(" · ") : " · no titles in on provinces") +
    " · search to highlight one holder";
  setStatus(extra);
}

function paintCompanyPlusNearby(company, asset, opts) {
  ensureCompanyLayers();
  const data = companyPlusNearbyFc(company);
  setPainted(data);
  if (!opts || opts.fit !== false) fitCompany(company, asset);
  paintLegend(company, data.features);
  setHud((company.holder || company.names[0]) + " · QC / ON / BC");
  const vis = visibleCounts(company);
  const bits = [];
  if (vis.qc) bits.push(vis.qc.toLocaleString("en-CA") + " QC");
  if (vis.on) bits.push(vis.on.toLocaleString("en-CA") + " ON");
  if (vis.bc) bits.push(vis.bc.toLocaleString("en-CA") + " BC");
  let extra = "<strong>" + (company.holder || company.names[0]) + "</strong>";
  extra += bits.length ? " · " + bits.join(" · ") : " · no titles in on provinces";
  const nearbyN = data.features.filter((f) => (f.properties || {}).role === "neighbor").length;
  if (nearbyN) extra += " · " + nearbyN.toLocaleString("en-CA") + " nearby";
  if (asset) {
    extra += " · around " + asset.name;
    extra += " · " + (asset.note || "");
  }
  setStatus(extra);
}

function showAllClaims(opts) {
  const gen = ++viewGen;
  currentCompany = null;
  currentAssetId = null;
  hiddenHolders = new Set();
  const fit = !!(opts && opts.fit);
  setHud("All claims · QC / ON / BC");
  whenMapReady(() => {
    if (gen !== viewGen) return;
    ensureCompanyLayers();
    if (extractsReady) {
      paintAllClaims(fit);
      return;
    }
    setStatus("Loading <strong>all claims</strong>…");
    loadAllExtracts().then(() => {
      if (gen !== viewGen) return;
      paintAllClaims(fit);
    }).catch(() => {
      if (gen !== viewGen) return;
      setStatus("Could not load claims extracts");
    });
  });
}

function selectCompany(id, assetId) {
  const company = findIndexed(id);
  if (!company) return;
  currentCompany = company;
  currentAssetId = assetId || null;
  document.getElementById("search-results").hidden = true;
  document.getElementById("search").value = company.names[0] || company.holder;
  hiddenHolders = new Set();
  paintLegend(company);
  setHud((company.holder || company.names[0]) + " · QC / ON / BC");
  const gen = ++viewGen;
  const asset = (company.mines || []).find((m) => m.id === assetId);
  whenMapReady(() => {
    if (gen !== viewGen) return;
    ensureCompanyLayers();
    if (!hasAnyExtract(company)) {
      setPainted({ type: "FeatureCollection", features: [] });
      fitCompany(company, asset);
      let extra = "<strong>" + (company.holder || company.names[0]) + "</strong> · no QC/ON/BC titles in extracts";
      if (asset) extra += " · around " + asset.name + " · " + (asset.note || "");
      setStatus(extra);
      loadAllExtracts();
      return;
    }
    setStatus("Loading <strong>" + company.holder + "</strong> claims…");
    loadExtract(company).then(() => {
      if (gen !== viewGen) return;
      paintCompanyPlusNearby(company, asset);
      loadAllExtracts().then(() => {
        if (gen !== viewGen) return;
        paintCompanyPlusNearby(company, asset, { fit: false });
      });
    }).catch(() => {
      if (gen !== viewGen) return;
      setStatus("Could not load company extract");
    });
  });
}

function csvEscape(v) {
  const s = v == null ? "" : String(v);
  if (/[",\n\r]/.test(s)) return "\"" + s.replace(/"/g, "\"\"") + "\"";
  return s;
}

function visibleDownloadRows() {
  return (paintedFc.features || []).filter((f) => {
    const p = f.properties || {};
    if (hiddenHolders.has(p.holder)) return false;
    return true;
  });
}

function downloadVisibleClaims() {
  const rows = visibleDownloadRows();
  if (!rows.length) {
    setStatus("No claims to download");
    return;
  }
  const lines = [CSV_FIELDS.join(",")];
  rows.forEach((f) => {
    const p = f.properties || {};
    lines.push(CSV_FIELDS.map((k) => csvEscape(p[k])).join(","));
  });
  const blob = new Blob(["\uFEFF" + lines.join("\n") + "\n"], { type: "text/csv;charset=utf-8" });
  const name = currentCompany ? "qc-claims-" + currentCompany.id + ".csv" : "qc-claims.csv";
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1500);
}

["ly-quebec", "ly-ontario", "ly-bc"].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("change", () => {
    if (currentCompany) selectCompany(currentCompany.id, currentAssetId);
    else if (extractsReady) paintAllClaims(false);
  });
});

document.getElementById("search-form").addEventListener("submit", (e) => {
  e.preventDefault();
  const hits = matchCompanies(document.getElementById("search").value);
  renderHits(hits);
  if (hits.length) selectCompany(hits[0].id);
});

document.getElementById("search").addEventListener("input", (e) => {
  const q = e.target.value;
  if (q.trim().length < 2) {
    renderHits([]);
    if (!q.trim() && currentCompany) showAllClaims({ fit: true });
    return;
  }
  renderHits(matchCompanies(q));
});

document.getElementById("download-claims").addEventListener("click", (e) => {
  e.preventDefault();
  downloadVisibleClaims();
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closePopup();
    document.getElementById("search-results").hidden = true;
  }
});

map.on("click", () => {
  document.getElementById("search-results").hidden = true;
});

fetch(CATALOG_URL).then((r) => r.json()).then((json) => {
  catalog = json;
  buildSearchIndex();
  const params = new URLSearchParams(location.search);
  const companyId = params.get("company");
  const assetId = params.get("asset") || params.get("mine");
  if (companyId) {
    loadAllExtracts();
    selectCompany(companyId, assetId);
  } else {
    showAllClaims({ fit: true });
  }
}).catch(() => {
  setStatus("Could not load claims/companies.json");
});

window.qcSelectCompany = selectCompany;
window.qcShowAllClaims = showAllClaims;
window.qcDownloadClaims = downloadVisibleClaims;
