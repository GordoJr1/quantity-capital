const CATALOG_URL = "claims/companies.json";
const QUEBEC_CENTER = [-72.5, 51.5];
const CANADA_CENTER = [-96, 56];

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
let hiddenHolders = new Set();
let extractCache = {};
let selectGen = 0;
let currentCompany = null;
let currentAssetId = null;

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

function setStatus(extra) {
  const el = document.getElementById("status");
  const asOf = catalog && (catalog.sources && catalog.sources.as_of_on_bc || catalog.as_of) ? (catalog.sources && catalog.sources.as_of_on_bc || catalog.as_of) : "";
  const base = "Quebec GESTIM · Ontario MLAS (unofficial viewing) · BC MTA" +
    (asOf ? " · " + asOf : "") +
    " · <span style=\"color:#d97a6c\">Not legal title.</span> Confirm on GESTIM / MLAS / Mineral Titles.";
  el.innerHTML = extra ? extra + " · " + base : base;
}

function matchCompanies(q) {
  const needle = q.trim().toLowerCase();
  if (!needle || !catalog) return [];
  return (catalog.companies || []).filter((c) =>
    (c.names || []).some((n) => String(n).toLowerCase().includes(needle)) ||
    String(c.holder || "").toLowerCase().includes(needle) ||
    String(c.id || "").toLowerCase().includes(needle)
  );
}

function titleBits(c) {
  const bits = [];
  if ((c.quebec_count || 0) > 0) bits.push((c.quebec_count || 0).toLocaleString("en-CA") + " QC");
  if ((c.ontario_count || 0) > 0) bits.push((c.ontario_count || 0).toLocaleString("en-CA") + " ON");
  if ((c.bc_count || 0) > 0) bits.push((c.bc_count || 0).toLocaleString("en-CA") + " BC");
  if (!bits.length) return "0 titles in QC/ON/BC extracts";
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
  const neighborFilter = hide.length
    ? ["!", ["in", ["get", "holder"], ["literal", hide]]]
    : true;
  map.setFilter("neighbor-fill", ["all", ["==", ["get", "role"], "neighbor"], neighborFilter]);
  map.setFilter("neighbor-line", ["all", ["==", ["get", "role"], "neighbor"], neighborFilter]);
}

function paintLegend(company) {
  const box = document.getElementById("legend");
  const hint = document.getElementById("company-hint");
  if (!company) {
    box.hidden = true;
    hint.hidden = false;
    hint.textContent = "No claims drawn until you select a producer.";
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
  map.addSource("company", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
  map.addLayer({
    id: "neighbor-fill",
    type: "fill",
    source: "company",
    filter: ["==", ["get", "role"], "neighbor"],
    paint: { "fill-color": ["coalesce", ["get", "color"], "#90a4ae"], "fill-opacity": 0.38 },
  });
  map.addLayer({
    id: "neighbor-line",
    type: "line",
    source: "company",
    filter: ["==", ["get", "role"], "neighbor"],
    paint: { "line-color": ["coalesce", ["get", "color"], "#90a4ae"], "line-width": 0.4 },
  });
  map.addLayer({
    id: "focus-fill",
    type: "fill",
    source: "company",
    filter: ["==", ["get", "role"], "focus"],
    paint: { "fill-color": ["coalesce", ["get", "color"], "#e8b040"], "fill-opacity": 0.55 },
  });
  map.addLayer({
    id: "focus-line",
    type: "line",
    source: "company",
    filter: ["==", ["get", "role"], "focus"],
    paint: { "line-color": "#f3d48a", "line-width": 0.8 },
  });
  ["focus-fill", "neighbor-fill"].forEach((id) => {
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
  const res = await fetch(assetUrl(url));
  if (!res.ok) throw new Error("Could not load " + url);
  return res.json();
}

async function loadExtract(company) {
  if (extractCache[company.id]) return extractCache[company.id];
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
  extractCache[company.id] = { type: "FeatureCollection", features };
  return extractCache[company.id];
}

function hasAnyExtract(company) {
  return !!(company.extract || company.ontario_extract || company.bc_extract);
}

function paintCompanyData(company, asset) {
  ensureCompanyLayers();
  const cached = extractCache[company.id];
  const data = cached ? filterByProvince(cached) : { type: "FeatureCollection", features: [] };
  map.resize();
  map.getSource("company").setData(data);
  fitCompany(company, asset);
  paintLegend(company);
  const vis = visibleCounts(company);
  const bits = [];
  if (vis.qc) bits.push(vis.qc.toLocaleString("en-CA") + " QC");
  if (vis.on) bits.push(vis.on.toLocaleString("en-CA") + " ON");
  if (vis.bc) bits.push(vis.bc.toLocaleString("en-CA") + " BC");
  let extra = "<strong>" + (company.holder || company.names[0]) + "</strong>";
  extra += bits.length ? " · " + bits.join(" · ") : " · no titles in on provinces";
  if ((company.neighbor_count || 0) && provinceOn("ly-quebec")) {
    extra += " · " + (company.neighbor_count || 0).toLocaleString("en-CA") + " QC neighbors";
  }
  if (asset) {
    extra += " · around " + asset.name;
    extra += " · " + (asset.note || "");
  }
  setStatus(extra);
}

function selectCompany(id, assetId) {
  const company = (catalog.companies || []).find((c) => c.id === id);
  if (!company) return;
  currentCompany = company;
  currentAssetId = assetId || null;
  document.getElementById("search-results").hidden = true;
  document.getElementById("search").value = company.names[0] || company.holder;
  hiddenHolders = new Set();
  paintLegend(company);
  const gen = ++selectGen;
  const asset = (company.mines || []).find((m) => m.id === assetId);
  whenMapReady(() => {
    if (gen !== selectGen) return;
    ensureCompanyLayers();
    if (!hasAnyExtract(company)) {
      map.resize();
      map.getSource("company").setData({ type: "FeatureCollection", features: [] });
      fitCompany(company, asset);
      let extra = "<strong>" + (company.holder || company.names[0]) + "</strong> · no QC/ON/BC titles in extracts";
      if (asset) extra += " · around " + asset.name + " · " + (asset.note || "");
      setStatus(extra);
      return;
    }
    setStatus("Loading <strong>" + company.holder + "</strong> claims…");
    loadExtract(company).then(() => {
      if (gen !== selectGen) return;
      paintCompanyData(company, asset);
    }).catch(() => {
      if (gen !== selectGen) return;
      setStatus("Could not load company extract");
    });
  });
}

["ly-quebec", "ly-ontario", "ly-bc"].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("change", () => {
    if (currentCompany) selectCompany(currentCompany.id, currentAssetId);
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
  if (q.trim().length < 2) { renderHits([]); return; }
  renderHits(matchCompanies(q));
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
  setStatus("Search a <strong>producer</strong> to draw the company footprint. No polygons until then.");
  const params = new URLSearchParams(location.search);
  const companyId = params.get("company");
  const assetId = params.get("asset") || params.get("mine");
  if (companyId) selectCompany(companyId, assetId);
}).catch(() => {
  setStatus("Could not load claims/companies.json");
});

window.qcSelectCompany = selectCompany;
