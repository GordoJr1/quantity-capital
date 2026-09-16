const CATALOG_URL = "claims/iamgold-meta.json";
const QUEBEC_CENTER = [-72.5, 51.5];

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
  center: QUEBEC_CENTER,
  zoom: 5,
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

function assetUrl(name) {
  return new URL(name, window.location.href).href;
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
  const asOf = catalog && catalog.as_of ? catalog.as_of : "";
  const base = "Quebec GESTIM" + (asOf ? " · " + asOf : "") +
    " · <span style=\"color:#d97a6c\">Not legal title.</span> Confirm on GESTIM.";
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
      "<div class=\"meta\">" + (c.claim_count || 0).toLocaleString("en-CA") + " Quebec titles · whole company</div>" +
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
    return;
  }
  hint.hidden = true;
  box.hidden = false;
  const extra = (company.neighbors || []).length > 10
    ? "<p class=\"hint\">" + ((company.neighbors || []).length - 10) + " more neighbor holders share a gray.</p>"
    : "";
  const rows = [
    { holder: company.holder, count: company.claim_count, color: company.color, focus: true },
  ].concat((company.neighbors || []).slice(0, 10));
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

function fitCompany(company, asset) {
  if (asset && Number.isFinite(asset.lat) && Number.isFinite(asset.lon)) {
    map.fitBounds(
      [[asset.lon - 0.35, asset.lat - 0.22], [asset.lon + 0.35, asset.lat + 0.22]],
      { padding: 48, duration: 900, maxZoom: 11 }
    );
    return;
  }
  const b = company.bbox;
  if (!b || b.length !== 4) return;
  map.fitBounds([[b[0], b[1]], [b[2], b[3]]], { padding: 56, duration: 1100, maxZoom: 9 });
}

function whenMapReady(fn) {
  if (map.loaded()) fn();
  else map.once("load", fn);
}

async function loadExtract(company) {
  if (extractCache[company.id]) return extractCache[company.id];
  const url = assetUrl(company.extract);
  const res = await fetch(url);
  if (!res.ok) throw new Error("Could not load " + company.extract);
  const data = await res.json();
  extractCache[company.id] = data;
  return data;
}

function selectCompany(id, assetId) {
  const company = (catalog.companies || []).find((c) => c.id === id);
  if (!company) return;
  document.getElementById("search-results").hidden = true;
  document.getElementById("search").value = company.names[0] || company.holder;
  hiddenHolders = new Set();
  paintLegend(company);
  const gen = ++selectGen;
  whenMapReady(() => {
    if (gen !== selectGen) return;
    ensureCompanyLayers();
    setStatus("Loading <strong>" + company.holder + "</strong> claims…");
    loadExtract(company).then((data) => {
      if (gen !== selectGen) return;
      map.resize();
      map.getSource("company").setData(data);
      const asset = (company.mines || []).find((m) => m.id === assetId);
      fitCompany(company, asset);
      const n = (company.claim_count || 0).toLocaleString("en-CA");
      const nb = (company.neighbor_count || 0).toLocaleString("en-CA");
      let extra = "<strong>" + company.holder + "</strong> · " + n + " titles · " + nb + " neighbors";
      if (asset) {
        extra += " · around " + asset.name;
        if (asset.gestim === false) extra += " · " + (asset.note || "Outside Quebec GESTIM");
      }
      setStatus(extra);
    }).catch(() => {
      if (gen !== selectGen) return;
      setStatus("Could not load company extract");
    });
  });
}

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
  setStatus("Search <strong>IAMGOLD</strong> to draw the company footprint. No polygons until then.");
  const params = new URLSearchParams(location.search);
  const companyId = params.get("company");
  const assetId = params.get("asset") || params.get("mine");
  if (companyId) selectCompany(companyId, assetId);
}).catch(() => {
  setStatus("Could not load claims/iamgold-meta.json");
});

window.qcSelectCompany = selectCompany;
