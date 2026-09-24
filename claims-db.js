/* Draft claims viewer for the provincial PMTiles files.
   Does not change claims.html.
   Tiles: claims/tiles/<code>.pmtiles.png, listed in claims/tiles/index.json.
   The .png suffix is only so GitHub Pages will not gzip-slice Range requests.
   ?tiles=<url> loads one archive instead. */
(function () {
  const params = new URLSearchParams(location.search);
  const tilesOverride = params.get("tiles");
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
    center: [-100, 58],
    zoom: 3,
  });
  map.addControl(new maplibregl.NavigationControl({ visualizePitch: false }), "bottom-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth: 140, unit: "metric" }), "bottom-right");

  let holders = [];
  let holderFilter = null;
  let companyFilter = null;
  let popup = null;
  let fillLayers = [];
  const rangeModes = [];

  function setStatus(text) {
    statusEl.textContent = text;
  }

  function colorFor(companyId) {
    if (!companyId) return "#5c6b7a";
    let h = 0;
    for (let i = 0; i < companyId.length; i++) h = (h * 31 + companyId.charCodeAt(i)) >>> 0;
    return "hsl(" + (h % 360) + ", 62%, 58%)";
  }

  function applyFilter() {
    const parts = ["all"];
    if (linkedOnlyEl.checked) parts.push(["!=", ["get", "company"], "unlinked"]);
    if (holderFilter) parts.push(["==", ["get", "holder"], holderFilter]);
    if (companyFilter) parts.push(["==", ["get", "company"], companyFilter]);
    const filter = parts.length === 1 ? null : parts;
    fillLayers.forEach((id) => {
      if (!map.getLayer(id)) return;
      map.setFilter(id, filter);
      const line = id.replace("claims-fill-", "claims-line-");
      if (map.getLayer(line)) map.setFilter(line, filter);
    });
  }

  function showPopup(feature, lngLat) {
    const p = feature.properties || {};
    const company = p.name || (!p.company || p.company === "unlinked" ? "—" : p.company);
    const ticker = p.ticker || "—";
    const html =
      '<div class="pop"><div class="holder"></div><dl>' +
      "<dt>Holder</dt><dd class=\"v-holder\"></dd>" +
      "<dt>Company</dt><dd class=\"v-company\"></dd>" +
      "<dt>Ticker</dt><dd class=\"v-ticker\"></dd>" +
      "<dt>Title</dt><dd class=\"v-title\"></dd>" +
      "<dt>Province</dt><dd class=\"v-prov\"></dd>" +
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
    root.querySelector(".v-prov").textContent = p.province || "—";
  }

  function fitHolder(row) {
    holderFilter = row.name;
    companyFilter = null;
    applyFilter();
    if (row.bbox && row.bbox.length === 4) {
      map.fitBounds([[row.bbox[0], row.bbox[1]], [row.bbox[2], row.bbox[3]]], { padding: 48, maxZoom: 11, duration: 600 });
    }
    const where = row.code ? row.code.toUpperCase() + " · " : "";
    setStatus(where + row.name + " · " + row.count.toLocaleString() + " titles" + (row.ticker ? " · " + row.ticker : ""));
  }

  async function lookupTitle(id) {
    const shard = (id.length >= 2 ? id.slice(0, 2) : id.padStart(2, "0")).replace(/ /g, "_");
    const codes = Array.from(new Set(holders.map((row) => row.code).filter(Boolean)));
    for (const code of codes) {
      const res = await fetch("claims/search/titles/" + code + "/" + shard + ".json");
      if (!res.ok) continue;
      const table = await res.json();
      const idx = table[id];
      if (idx == null || !holders[idx]) continue;
      searchEl.value = holders[idx].name;
      fitHolder(holders[idx]);
      return;
    }
    setStatus("Title " + id + " is not in the index");
  }

  function renderResults(query) {
    const q = query.trim().toLowerCase();
    resultsEl.innerHTML = "";
    if (!q) {
      resultsEl.hidden = true;
      return;
    }
    const hits = [];
    for (let i = 0; i < holders.length && hits.length < 12; i++) {
      const row = holders[i];
      const blob = (row.name + " " + (row.company || "") + " " + (row.company_id || "") + " " + (row.ticker || "") + " " + (row.code || "")).toLowerCase();
      if (blob.includes(q)) hits.push(row);
    }
    if (!hits.length) {
      resultsEl.hidden = true;
      if (/\d/.test(q) && !q.includes(" ")) {
        lookupTitle(q).catch(() => setStatus("Title lookup failed"));
        return;
      }
      setStatus("No holder match");
      return;
    }
    hits.forEach((row) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "hit";
      btn.innerHTML = '<div class="who"></div><div class="meta"></div>';
      btn.querySelector(".who").textContent = row.name;
      const bits = [];
      if (row.code) bits.push(row.code.toUpperCase());
      bits.push(row.count.toLocaleString() + " titles");
      if (row.company) bits.push(row.company);
      else if (row.company_id) bits.push(row.company_id);
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
      const prev = totals.get(row.company_id) || { id: row.company_id, name: row.company || "", ticker: row.ticker, count: 0 };
      prev.count += row.count;
      if (!prev.name && row.company) prev.name = row.company;
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
      el.querySelector("span").textContent = (row.name || row.id) + (row.ticker ? " · " + row.ticker : "") + " · " + row.count.toLocaleString();
      el.addEventListener("click", () => {
        companyFilter = companyFilter === row.id ? null : row.id;
        holderFilter = null;
        applyFilter();
        setStatus(companyFilter ? "Company " + row.id : "All holders");
      });
      legendEl.appendChild(el);
    });
  }

  function addLayers(sourceId) {
    const fillId = "claims-fill-" + sourceId;
    const lineId = "claims-line-" + sourceId;
    map.addLayer({
      id: fillId,
      type: "fill",
      source: sourceId,
      "source-layer": "claims",
      paint: {
        "fill-color": ["coalesce", ["get", "color"], "#5c6b7a"],
        "fill-opacity": 0.62,
      },
    });
    map.addLayer({
      id: lineId,
      type: "line",
      source: sourceId,
      "source-layer": "claims",
      paint: {
        "line-color": ["coalesce", ["get", "color"], "#c5d0dc"],
        "line-width": 0.4,
      },
    });
    fillLayers.push(fillId);
    map.on("click", fillId, (ev) => {
      const feature = ev.features && ev.features[0];
      if (feature) showPopup(feature, ev.lngLat);
    });
    map.on("mouseenter", fillId, () => { map.getCanvas().style.cursor = "pointer"; });
    map.on("mouseleave", fillId, () => { map.getCanvas().style.cursor = ""; });
  }

  function looksLikePmtiles(buf) {
    return new TextDecoder().decode(new Uint8Array(buf).subarray(0, 7)) === "PMTiles";
  }

  function looksLikeGzip(buf) {
    const bytes = new Uint8Array(buf);
    return bytes.length >= 2 && bytes[0] === 0x1f && bytes[1] === 0x8b;
  }

  async function openArchive(sourceId, url) {
    const absolute = new URL(url, location.href).href;
    let rangeOk = false;
    try {
      const probe = await fetch(absolute, { headers: { Range: "bytes=0-15" } });
      const headBuf = await probe.arrayBuffer();
      rangeOk = probe.status === 206 && looksLikePmtiles(headBuf) && !looksLikeGzip(headBuf);
    } catch (err) {
      rangeOk = false;
    }
    if (!rangeOk) {
      const full = await fetch(absolute);
      if (!full.ok) throw new Error(full.status + " " + url);
      const archiveBytes = await full.arrayBuffer();
      if (!looksLikePmtiles(archiveBytes)) throw new Error("not a PMTiles archive " + url);
      protocol.add(new pmtiles.PMTiles({
        getKey() { return absolute; },
        getBytes(offset, length) {
          return Promise.resolve({ data: archiveBytes.slice(offset, offset + length) });
        },
      }));
      rangeModes.push("buffer");
    } else {
      rangeModes.push("range");
    }
    map.addSource(sourceId, { type: "vector", url: "pmtiles://" + absolute });
  }

  async function loadTiles() {
    let files = [];
    if (tilesOverride) {
      files = [{ code: "one", file: tilesOverride }];
    } else {
      const res = await fetch("claims/tiles/index.json");
      if (!res.ok) throw new Error("claims/tiles/index.json " + res.status);
      const index = await res.json();
      files = index.provinces || [];
    }
    for (const row of files) {
      await openArchive(row.code || row.file, row.file);
      addLayers(row.code || row.file);
    }
    applyFilter();
    const mode = rangeModes.every((item) => item === "range") ? "range requests" : "full-file read";
    return files.length + " tile archives via " + mode;
  }

  map.on("load", () => {
    loadTiles().then((note) => {
      setStatus((statusEl.textContent || "Index") + " " + note + ".");
    }).catch((err) => {
      setStatus((statusEl.textContent || "") + " Tiles not loaded (" + err.message + ").");
    });
  });

  linkedOnlyEl.addEventListener("change", applyFilter);
  searchEl.addEventListener("input", () => renderResults(searchEl.value));
  document.getElementById("search-form").addEventListener("submit", (ev) => {
    ev.preventDefault();
    const q = searchEl.value.trim();
    if (/^[a-z0-9]{4,}$/i.test(q) && /\d/.test(q) && !q.includes(" ")) {
      lookupTitle(q).catch(() => setStatus("Title lookup failed"));
    }
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
      const provinces = data.provinces || {};
      const bits = Object.keys(provinces).map((key) => {
        const slot = provinces[key];
        const pct = slot.coverage != null ? Math.round(slot.coverage * 1000) / 10 + "%" : "—";
        return (slot.name || key) + " " + pct;
      });
      subEl.textContent = "Draft · " + (data.titles || 0).toLocaleString() + " titles · " + cov + " linked · index " + ms + " ms";
      setStatus((bits.join(" · ") || "Claims") + ". Not legal title.");
      renderLegend();
    })
    .catch((err) => setStatus("Index failed: " + err.message));
})();
