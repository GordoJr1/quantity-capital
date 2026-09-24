/* Draft Ontario MLAS viewer. Does not change claims.html.
   Tiles: claims/.build/ontario.pmtiles (gitignored) or ?tiles=<url>. */
(function () {
  const params = new URLSearchParams(location.search);
  const tilesRel = params.get("tiles") || "claims/.build/ontario.pmtiles";
  const tilesUrl = new URL(tilesRel, location.href).href;
  const statusEl = document.getElementById("status");
  const searchEl = document.getElementById("search");
  const resultsEl = document.getElementById("search-results");
  const legendEl = document.getElementById("legend");
  const linkedOnlyEl = document.getElementById("linked-only");
  const subEl = document.getElementById("hud-sub");

  const protocol = new pmtiles.Protocol();
  maplibregl.addProtocol("pmtiles", protocol.tile);

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
          attribution: "Tiles &copy; Esri",
          maxzoom: 16,
        },
      },
      layers: [
        { id: "background", type: "background", paint: { "background-color": "#0b1016" } },
        { id: "basemap", type: "raster", source: "esri" },
      ],
    },
    center: [-84.5, 49.5],
    zoom: 4.6,
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 140, unit: "metric" }), "bottom-right");

  let holders = [];
  let active = -1;
  let holderFilter = null;
  let companyFilter = null;
  let popup = null;
  let tileNote = "";

  function setStatus(text) {
    statusEl.textContent = text;
  }

  function colorFor(companyId) {
    if (!companyId) return "#5c6b7a";
    let h = 0;
    for (let i = 0; i < companyId.length; i++) h = (h * 31 + companyId.charCodeAt(i)) >>> 0;
    const hue = h % 360;
    return "hsl(" + hue + ", 62%, 58%)";
  }

  function applyFilter() {
    if (!map.getLayer("claims-fill")) return;
    const parts = ["all"];
    if (linkedOnlyEl.checked) parts.push(["!=", ["get", "company"], "unlinked"]);
    if (holderFilter) parts.push(["==", ["get", "holder"], holderFilter]);
    if (companyFilter) parts.push(["==", ["get", "company"], companyFilter]);
    const filter = parts.length === 1 ? null : parts;
    map.setFilter("claims-fill", filter);
    map.setFilter("claims-line", filter);
  }

  function showPopup(feature, lngLat) {
    const p = feature.properties || {};
    const company = !p.company || p.company === "unlinked" ? "—" : p.company;
    const ticker = p.ticker || "—";
    const html =
      '<div class="pop"><div class="holder"></div><dl>' +
      "<dt>Holder</dt><dd class=\"v-holder\"></dd>" +
      "<dt>Company</dt><dd class=\"v-company\"></dd>" +
      "<dt>Ticker</dt><dd class=\"v-ticker\"></dd>" +
      "<dt>Title</dt><dd class=\"v-title\"></dd>" +
      "</dl></div>";
    if (popup) popup.remove();
    popup = new maplibregl.Popup({ closeButton: true, maxWidth: "320px" })
      .setLngLat(lngLat)
      .setHTML(html)
      .addTo(map);
    const root = popup.getElement();
    root.querySelector(".holder").textContent = p.holder || "Holder";
    root.querySelector(".v-holder").textContent = p.holder || "—";
    root.querySelector(".v-company").textContent = company;
    root.querySelector(".v-ticker").textContent = ticker;
    root.querySelector(".v-title").textContent = p.id || "—";
  }

  function fitHolder(row) {
    holderFilter = row.name;
    companyFilter = null;
    applyFilter();
    if (row.bbox && row.bbox.length === 4) {
      map.fitBounds([[row.bbox[0], row.bbox[1]], [row.bbox[2], row.bbox[3]]], { padding: 48, maxZoom: 11, duration: 600 });
    }
    setStatus(row.name + " · " + row.count.toLocaleString() + " titles" + (row.ticker ? " · " + row.ticker : ""));
  }

  async function lookupTitle(id) {
    const shard = id.slice(0, 2);
    const res = await fetch("claims/search/titles/" + shard + ".json");
    if (!res.ok) {
      setStatus("No title shard for " + id);
      return;
    }
    const table = await res.json();
    const idx = table[id];
    if (idx == null || !holders[idx]) {
      setStatus("Title " + id + " is not in the Ontario index");
      return;
    }
    searchEl.value = holders[idx].name;
    fitHolder(holders[idx]);
  }

  function renderResults(query) {
    const q = query.trim().toLowerCase();
    resultsEl.innerHTML = "";
    if (!q) {
      resultsEl.hidden = true;
      return;
    }
    if (/^\d{4,}$/.test(q)) {
      resultsEl.hidden = true;
      lookupTitle(q).catch(() => setStatus("Title lookup failed"));
      return;
    }
    const hits = [];
    for (let i = 0; i < holders.length && hits.length < 12; i++) {
      const row = holders[i];
      const blob = (row.name + " " + (row.company || "") + " " + (row.company_id || "") + " " + (row.ticker || "")).toLowerCase();
      if (blob.includes(q)) hits.push(row);
    }
    if (!hits.length) {
      resultsEl.hidden = true;
      setStatus("No holder match");
      return;
    }
    hits.forEach((row) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "hit";
      btn.innerHTML = '<div class="who"></div><div class="meta"></div>';
      btn.querySelector(".who").textContent = row.name;
      const bits = [row.count.toLocaleString() + " titles"];
      if (row.company_id) bits.push(row.company_id);
      if (row.ticker) bits.push(row.ticker);
      btn.querySelector(".meta").textContent = bits.join(" · ");
      btn.addEventListener("click", () => {
        resultsEl.hidden = true;
        fitHolder(row);
      });
      resultsEl.appendChild(btn);
    });
    resultsEl.hidden = false;
  }

  function renderLegend() {
    const totals = new Map();
    holders.forEach((row) => {
      if (!row.company_id) return;
      const prev = totals.get(row.company_id) || { id: row.company_id, ticker: row.ticker, count: 0 };
      prev.count += row.count;
      if (!prev.ticker && row.ticker) prev.ticker = row.ticker;
      totals.set(row.company_id, prev);
    });
    const top = Array.from(totals.values()).sort((a, b) => b.count - a.count).slice(0, 14);
    legendEl.innerHTML = "";
    top.forEach((row) => {
      const el = document.createElement("div");
      el.className = "swatch";
      el.innerHTML = "<i></i><span></span>";
      el.querySelector("i").style.background = colorFor(row.id);
      el.querySelector("span").textContent = row.id + (row.ticker ? " · " + row.ticker : "") + " · " + row.count.toLocaleString();
      el.addEventListener("click", () => {
        companyFilter = companyFilter === row.id ? null : row.id;
        holderFilter = null;
        applyFilter();
        setStatus(companyFilter ? "Company " + row.id : "All holders");
      });
      legendEl.appendChild(el);
    });
  }

  map.on("load", () => {
    map.addSource("on-claims", { type: "vector", url: "pmtiles://" + tilesUrl });
    map.addLayer({
      id: "claims-fill",
      type: "fill",
      source: "on-claims",
      "source-layer": "claims",
      paint: {
        "fill-color": ["coalesce", ["get", "color"], "#5c6b7a"],
        "fill-opacity": 0.62,
      },
    });
    map.addLayer({
      id: "claims-line",
      type: "line",
      source: "on-claims",
      "source-layer": "claims",
      paint: {
        "line-color": ["coalesce", ["get", "color"], "#c5d0dc"],
        "line-width": 0.4,
      },
    });
    map.on("click", "claims-fill", (ev) => {
      const feature = ev.features && ev.features[0];
      if (feature) showPopup(feature, ev.lngLat);
    });
    map.on("mouseenter", "claims-fill", () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", "claims-fill", () => { map.getCanvas().style.cursor = ""; });
  });

  map.on("error", (ev) => {
    const msg = (ev && ev.error && ev.error.message) || "tile error";
    if (!tileNote && /pmtiles|fetch|ajax|404|Failed/i.test(msg)) {
      tileNote = " Tile archive not loaded (" + tilesRel + "). It is gitignored; pass ?tiles= to a hosted PMTiles URL.";
      setStatus((statusEl.textContent || "") + tileNote);
    }
  });

  linkedOnlyEl.addEventListener("change", applyFilter);
  searchEl.addEventListener("input", () => renderResults(searchEl.value));
  document.getElementById("search-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const q = searchEl.value.trim();
    if (/^\d{4,}$/.test(q)) lookupTitle(q).catch(() => setStatus("Title lookup failed"));
  });

  const t0 = performance.now();
  fetch("claims/search/holders.json")
    .then((res) => {
      if (!res.ok) throw new Error("holders index " + res.status);
      return res.json();
    })
    .then((data) => {
      holders = data.holders || [];
      const ms = Math.round(performance.now() - t0);
      const cov = data.coverage != null ? Math.round(data.coverage * 1000) / 10 + "%" : "—";
      subEl.textContent = "Draft · " + (data.titles || 0).toLocaleString() + " titles · " + cov + " linked · index " + ms + " ms";
      setStatus("Ontario MLAS · " + holders.length.toLocaleString() + " holders · " + cov + " of titles linked to a site company. Not legal title.");
      renderLegend();
    })
    .catch((err) => setStatus("Index failed: " + err.message));
})();
