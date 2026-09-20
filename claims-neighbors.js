/* Neighbor-company sheet for claims.html. Browser + node. No map / no extracts. */
(function (root) {
  const NAME_DROPS = {
    inc: 1, ltd: 1, ltee: 1, limited: 1, limitee: 1, corp: 1, corporation: 1,
    co: 1, company: 1, the: 1, llc: 1, ulc: 1, plc: 1, lp: 1, llp: 1,
    quebec: 1, ontario: 1, canada: 1, canadien: 1, canadienne: 1, les: 1,
  };
  const GENERIC = Object.assign({
    gold: 1, mines: 1, mine: 1, mining: 1, miniere: 1, minieres: 1,
    resources: 1, ressource: 1, ressources: 1, exploration: 1, explorations: 1,
    minerals: 1, mineral: 1, entreprises: 1, entreprise: 1, groupe: 1, group: 1,
    holdings: 1, holding: 1, aurifere: 1, auriferes: 1, societe: 1,
    north: 1, america: 1, american: 1, house: 1, metals: 1, metal: 1,
    properties: 1, property: 1, projects: 1, project: 1, ventures: 1,
    energy: 1, royalty: 1, royalties: 1, silver: 1, copper: 1,
  }, NAME_DROPS);

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

  const CAD_SUFFIX = /\.(TO|V|CN|NE|TSX|TSXV)$/i;
  const PAD_DEG = 0.35;

  const FIELDS = [
    "holder",
    "company_id",
    "company",
    "ticker_cad",
    "ticker_us",
    "tickers",
    "match_confidence",
    "match_source",
    "relation",
    "neighbor_title_count",
    "claim_count",
    "provinces",
    "shared_provinces",
    "distance_km",
    "lat",
    "lon",
    "nearest_focus_mine",
    "region_notes",
  ];

  function normName(s) {
    let t = String(s || "").toLowerCase().normalize("NFD").replace(/[\u0300-\u036f]/g, "");
    t = t.replace(/\([^)]*\)/g, " ");
    t = t.replace(/&/g, " and ");
    t = t.replace(/[^a-z0-9]+/g, " ").trim();
    return t.split(/\s+/).filter((p) => p && !NAME_DROPS[p]).join(" ");
  }

  function tokensOf(s) {
    return normName(s).split(/\s+/).filter(Boolean);
  }

  function distinctiveTokens(toks) {
    return toks.filter((t) => t && !GENERIC[t] && t.length >= 4);
  }

  function featuredIdForHolder(name) {
    const n = String(name || "").toLowerCase();
    if (!n) return null;
    const keys = Object.keys(FEATURED_NEEDLES);
    for (let i = 0; i < keys.length; i++) {
      const needles = FEATURED_NEEDLES[keys[i]];
      for (let j = 0; j < needles.length; j++) {
        if (n.indexOf(needles[j]) !== -1) return keys[i];
      }
    }
    return null;
  }

  function classifyTicker(sym) {
    const s = String(sym || "").trim().toUpperCase();
    if (!s) return null;
    if (CAD_SUFFIX.test(s)) return "cad";
    return "us";
  }

  function uniq(list) {
    const out = [];
    const seen = {};
    (list || []).forEach((v) => {
      const s = String(v || "").trim();
      if (!s) return;
      const k = s.toUpperCase();
      if (seen[k]) return;
      seen[k] = 1;
      out.push(s);
    });
    return out;
  }

  function splitTickers(symbols) {
    const cad = [];
    const us = [];
    uniq(symbols).forEach((s) => {
      if (classifyTicker(s) === "cad") cad.push(s);
      else us.push(s);
    });
    return { cad: uniq(cad), us: uniq(us), all: uniq(cad.concat(us)) };
  }

  function emptyTickers() {
    return { cad: [], us: [], all: [] };
  }

  function tickerRecord(cad, us, source) {
    const split = splitTickers((cad || []).concat(us || []));
    return { cad: split.cad, us: split.us, all: split.all, source: source || "" };
  }

  function buildTickerIndex(lookups) {
    const byId = {};
    const byNorm = {};
    const tokenHits = {};
    function putNorm(name, rec) {
      const k = normName(name);
      if (!k) return;
      const prev = byNorm[k];
      if (!prev) {
        byNorm[k] = rec;
        return;
      }
      const merged = tickerRecord(
        (prev.cad || []).concat(rec.cad || []),
        (prev.us || []).concat(rec.us || []),
        [prev.source, rec.source].filter(Boolean).join("+")
      );
      merged.name = prev.name || rec.name;
      merged.id = prev.id || rec.id;
      byNorm[k] = merged;
    }
    function putTokens(name, rec) {
      distinctiveTokens(tokensOf(name)).forEach((t) => {
        if (!tokenHits[t]) tokenHits[t] = [];
        if (tokenHits[t].indexOf(rec) === -1) tokenHits[t].push(rec);
      });
    }
    ((lookups && lookups.betaIssuers) || []).forEach((row) => {
      if (!row || !row.id) return;
      const rec = tickerRecord(row.tickers || [], [], "beta");
      rec.name = row.name || row.id;
      rec.id = row.id;
      if (!rec.all.length) return;
      byId[row.id] = rec;
      putNorm(row.name, rec);
      putNorm(row.id.replace(/-/g, " "), rec);
      putTokens(row.name, rec);
      putTokens(row.id.replace(/-/g, " "), rec);
    });
    ((lookups && lookups.insiderCompanies) || []).forEach((row) => {
      if (!row || !row.name) return;
      const rec = tickerRecord(row.cad || [], row.us || [], "insider-companies");
      rec.name = row.name;
      if (!rec.all.length) return;
      putNorm(row.name, rec);
      putTokens(row.name, rec);
    });
    const byToken = {};
    Object.keys(tokenHits).forEach((t) => {
      if (tokenHits[t].length === 1) byToken[t] = tokenHits[t][0];
    });
    return { byId: byId, byNorm: byNorm, byToken: byToken };
  }

  function catalogById(catalog) {
    const map = {};
    ((catalog && catalog.companies) || []).forEach((c) => {
      if (c && c.id) map[c.id] = c;
    });
    return map;
  }

  function buildCatalogIndex(catalog) {
    const byId = catalogById(catalog);
    const byNorm = {};
    const idToken = {};
    Object.keys(byId).forEach((id) => {
      const c = byId[id];
      const names = [c.holder].concat(c.names || []).concat([id.replace(/-/g, " ")]);
      names.forEach((n) => {
        const k = normName(n);
        if (!k) return;
        if (!byNorm[k]) byNorm[k] = [];
        if (byNorm[k].indexOf(c) === -1) byNorm[k].push(c);
      });
      distinctiveTokens(id.split("-")).forEach((t) => {
        if (!idToken[t]) idToken[t] = [];
        if (idToken[t].indexOf(c) === -1) idToken[t].push(c);
      });
    });
    Object.keys(idToken).forEach((t) => {
      if (idToken[t].length !== 1) delete idToken[t];
    });
    return { byId: byId, byNorm: byNorm, idToken: idToken };
  }

  function onlyOne(list) {
    if (!list || list.length !== 1) return null;
    return list[0];
  }

  function uniqueCompanies(lists) {
    const out = [];
    const seen = {};
    (lists || []).forEach((arr) => {
      (arr || []).forEach((c) => {
        if (!c || !c.id || seen[c.id]) return;
        seen[c.id] = 1;
        out.push(c);
      });
    });
    return out;
  }

  function matchHolder(holder, catalogIndex, tickerIndex) {
    const raw = String(holder || "").trim();
    if (!raw) return { company: null, company_id: "", company_name: "", confidence: "unmatched", source: "", tickers: emptyTickers() };
    const idx = catalogIndex || { byId: {}, byNorm: {}, idToken: {} };
    const tix = tickerIndex || { byId: {}, byNorm: {} };
    const n = normName(raw);
    const toks = tokensOf(raw);

    const fid = featuredIdForHolder(raw);
    if (fid && idx.byId[fid]) {
      const c = idx.byId[fid];
      return finishMatch(c, "exact", "featured", tix);
    }

    const exact = onlyOne(idx.byNorm[n]);
    if (exact) return finishMatch(exact, "exact", "catalog", tix);

    const tset = toks.slice().sort().join(" ");
    const aliasHits = [];
    Object.keys(idx.byNorm).forEach((k) => {
      if (k.split(/\s+/).sort().join(" ") === tset) {
        idx.byNorm[k].forEach((c) => aliasHits.push(c));
      }
    });
    const alias = onlyOne(uniqueCompanies([aliasHits]));
    if (alias) return finishMatch(alias, "alias", "catalog-tokens", tix);

    const dist = distinctiveTokens(toks);
    const idHits = uniqueCompanies(dist.map((t) => idx.idToken[t]));
    if (idHits.length === 1) return finishMatch(idHits[0], "fuzzy", "catalog-id", tix);

    const insiderExact = tix.byNorm[n];
    if (insiderExact && insiderExact.all && insiderExact.all.length) {
      return {
        company: null,
        company_id: "",
        company_name: insiderExact.name || raw,
        confidence: "exact",
        source: "insider-companies",
        tickers: insiderExact,
      };
    }

    const tokenRecs = [];
    distinctiveTokens(toks).forEach((t) => {
      if (tix.byToken && tix.byToken[t]) tokenRecs.push(tix.byToken[t]);
    });
    const uniqRec = [];
    tokenRecs.forEach((rec) => {
      if (uniqRec.indexOf(rec) === -1) uniqRec.push(rec);
    });
    if (uniqRec.length === 1 && uniqRec[0].all && uniqRec[0].all.length) {
      const rec = uniqRec[0];
      return {
        company: rec.id && idx.byId[rec.id] ? idx.byId[rec.id] : null,
        company_id: rec.id && idx.byId[rec.id] ? rec.id : "",
        company_name: rec.name || raw,
        confidence: "fuzzy",
        source: rec.source || "insider-companies",
        tickers: rec,
      };
    }

    return { company: null, company_id: "", company_name: "", confidence: "unmatched", source: "", tickers: emptyTickers() };
  }

  function finishMatch(company, confidence, source, tickerIndex) {
    const tickers = tickersForCompany(company, tickerIndex);
    return {
      company: company,
      company_id: company.id,
      company_name: company.holder || (company.names && company.names[0]) || company.id,
      confidence: confidence,
      source: source,
      tickers: tickers,
    };
  }

  function tickersForCompany(company, tickerIndex) {
    const tix = tickerIndex || { byId: {}, byNorm: {} };
    const cad = [];
    const us = [];
    const sources = [];
    function take(rec) {
      if (!rec || !rec.all || !rec.all.length) return;
      (rec.cad || []).forEach((t) => cad.push(t));
      (rec.us || []).forEach((t) => us.push(t));
      if (rec.source && sources.indexOf(rec.source) === -1) sources.push(rec.source);
    }
    if (company && company.id) take(tix.byId[company.id]);
    const names = company ? [company.holder].concat(company.names || []).concat([String(company.id || "").replace(/-/g, " ")]) : [];
    names.forEach((n) => take(tix.byNorm[normName(n)]));
    const out = tickerRecord(cad, us, sources.join("+"));
    return out;
  }

  function provincesOf(company) {
    const bits = [];
    if (!company) return bits;
    if ((company.quebec_count || 0) > 0) bits.push("QC");
    if ((company.ontario_count || 0) > 0) bits.push("ON");
    if ((company.bc_count || 0) > 0) bits.push("BC");
    return bits;
  }

  function sharedProvinces(a, b) {
    const sb = {};
    (b || []).forEach((p) => { sb[p] = 1; });
    return (a || []).filter((p) => sb[p]);
  }

  function canadaMines(company) {
    return ((company && company.mines) || []).filter((m) => {
      if (!Number.isFinite(m.lat) || !Number.isFinite(m.lon)) return false;
      const country = String(m.country || "");
      const region = String(m.region || "");
      return country === "Canada" || !!m.gestim || /Quebec|Ontario|British Columbia|\bBC\b|Nunavut/i.test(region);
    });
  }

  function bboxCentroid(b) {
    if (!b || b.length !== 4) return null;
    return {
      lon: Math.round(((b[0] + b[2]) / 2) * 1e5) / 1e5,
      lat: Math.round(((b[1] + b[3]) / 2) * 1e5) / 1e5,
    };
  }

  function expandBbox(b, pad) {
    if (!b || b.length !== 4) return b;
    return [b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad];
  }

  function bboxIntersects(a, b) {
    if (!a || !b) return false;
    return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
  }

  function featureBbox(f) {
    const g = f && f.geometry;
    if (!g) return null;
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity, n = 0;
    function walk(xy) {
      if (!xy || xy.length < 2) return;
      n += 1;
      minx = Math.min(minx, xy[0]);
      miny = Math.min(miny, xy[1]);
      maxx = Math.max(maxx, xy[0]);
      maxy = Math.max(maxy, xy[1]);
    }
    function rings(coords, depth) {
      if (!coords) return;
      if (typeof coords[0] === "number") {
        walk(coords);
        return;
      }
      coords.forEach((c) => rings(c, (depth || 0) + 1));
    }
    rings(g.coordinates, 0);
    return n ? [minx, miny, maxx, maxy] : null;
  }

  function companyBboxes(company) {
    const boxes = [];
    if (!company) return boxes;
    [company.quebec_bbox, company.ontario_bbox, company.bc_bbox, company.bbox].forEach((b) => {
      if (b && b.length === 4) boxes.push(b);
    });
    return boxes;
  }

  function companyPoints(company) {
    const pts = [];
    canadaMines(company).forEach((m) => pts.push({ lon: m.lon, lat: m.lat, name: m.name, region: m.region, note: m.note }));
    companyBboxes(company).forEach((b) => {
      const c = bboxCentroid(b);
      if (c) pts.push({ lon: c.lon, lat: c.lat, name: "", region: "", note: "" });
    });
    return pts;
  }

  function haversineKm(a, b) {
    if (!a || !b) return null;
    const R = 6371;
    const toR = (d) => d * Math.PI / 180;
    const dLat = toR(b.lat - a.lat);
    const dLon = toR(b.lon - a.lon);
    const lat1 = toR(a.lat);
    const lat2 = toR(b.lat);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLon / 2) ** 2;
    return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
  }

  function overviewCellsFor(overviewFeatures, companyId) {
    return (overviewFeatures || []).filter((f) => f && f.properties && f.properties.company_id === companyId);
  }

  function padsForFocus(focus, overviewFeatures, padDeg) {
    const pad = padDeg == null ? PAD_DEG : padDeg;
    const pads = [];
    overviewCellsFor(overviewFeatures, focus.id).forEach((f) => {
      const b = featureBbox(f);
      if (b) pads.push(expandBbox(b, pad));
    });
    canadaMines(focus).forEach((m) => {
      pads.push([m.lon - pad, m.lat - pad * 0.7, m.lon + pad, m.lat + pad * 0.7]);
    });
    if (!pads.length) {
      companyBboxes(focus).forEach((b) => pads.push(expandBbox(b, pad)));
    }
    return pads;
  }

  function hitsPads(boxes, pads) {
    if (!boxes || !boxes.length || !pads || !pads.length) return false;
    for (let i = 0; i < boxes.length; i++) {
      for (let j = 0; j < pads.length; j++) {
        if (bboxIntersects(boxes[i], pads[j])) return true;
      }
    }
    return false;
  }

  function locationForCompany(company, overviewFeatures) {
    const cells = overviewCellsFor(overviewFeatures, company && company.id);
    if (cells.length) {
      let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
      cells.forEach((f) => {
        const b = featureBbox(f);
        if (!b) return;
        minx = Math.min(minx, b[0]);
        miny = Math.min(miny, b[1]);
        maxx = Math.max(maxx, b[2]);
        maxy = Math.max(maxy, b[3]);
      });
      if (minx !== Infinity) return bboxCentroid([minx, miny, maxx, maxy]);
    }
    const mines = canadaMines(company);
    if (mines.length) {
      let lon = 0, lat = 0;
      mines.forEach((m) => { lon += m.lon; lat += m.lat; });
      return {
        lon: Math.round((lon / mines.length) * 1e5) / 1e5,
        lat: Math.round((lat / mines.length) * 1e5) / 1e5,
      };
    }
    const boxes = companyBboxes(company);
    if (boxes.length) return bboxCentroid(boxes[0]);
    return null;
  }

  function nearestMine(focus, pt) {
    const mines = canadaMines(focus);
    if (!pt || !mines.length) return null;
    let best = null;
    let bestD = Infinity;
    mines.forEach((m) => {
      const d = haversineKm(pt, { lon: m.lon, lat: m.lat });
      if (d != null && d < bestD) {
        bestD = d;
        best = m;
      }
    });
    return best;
  }

  function regionNotes(company, focusMine) {
    const bits = [];
    canadaMines(company).forEach((m) => {
      const label = [m.name, m.region].filter(Boolean).join(" · ");
      if (label && bits.indexOf(label) === -1) bits.push(label);
    });
    if (!bits.length && focusMine) {
      const label = [focusMine.name, focusMine.region, focusMine.note].filter(Boolean).join(" · ");
      if (label) bits.push("near " + label);
    }
    return bits.slice(0, 4).join("; ");
  }

  function csvEscape(v) {
    const s = v == null ? "" : String(v);
    if (/[",\n\r]/.test(s)) return "\"" + s.replace(/"/g, "\"\"") + "\"";
    return s;
  }

  function toCsv(rows) {
    const lines = [FIELDS.join(",")];
    (rows || []).forEach((row) => {
      lines.push(FIELDS.map((k) => csvEscape(row[k])).join(","));
    });
    return "\uFEFF" + lines.join("\n") + "\n";
  }

  function filenameFor(company) {
    const id = (company && company.id) || "company";
    return "qc-claims-neighbors-" + id + ".csv";
  }

  function isFocusHolder(holder, focus, catalogIndex) {
    if (!holder || !focus) return false;
    if (featuredIdForHolder(holder) === focus.id) return true;
    const n = normName(holder);
    if (!n) return false;
    if (n === normName(focus.holder) || n === normName(focus.id.replace(/-/g, " "))) return true;
    return (focus.names || []).some((nm) => normName(nm) === n);
  }

  function emptyRow() {
    return {
      holder: "",
      company_id: "",
      company: "",
      ticker_cad: "",
      ticker_us: "",
      tickers: "",
      match_confidence: "unmatched",
      match_source: "",
      relation: "",
      neighbor_title_count: "",
      claim_count: "",
      provinces: "",
      shared_provinces: "",
      distance_km: "",
      lat: "",
      lon: "",
      nearest_focus_mine: "",
      region_notes: "",
    };
  }

  function applyTickers(row, tickers) {
    const t = tickers || emptyTickers();
    row.ticker_cad = (t.cad || []).join(" ");
    row.ticker_us = (t.us || []).join(" ");
    row.tickers = (t.all || []).join(" ");
  }

  function pointsForDistance(company, overviewFeatures) {
    const pts = [];
    overviewCellsFor(overviewFeatures, company && company.id).forEach((f) => {
      const c = bboxCentroid(featureBbox(f));
      if (c) pts.push(c);
    });
    companyPoints(company).forEach((p) => pts.push(p));
    return pts;
  }

  function closestPair(company, focus, overviewFeatures) {
    const a = pointsForDistance(company, overviewFeatures);
    const b = pointsForDistance(focus, overviewFeatures);
    let best = null;
    let bestPt = null;
    a.forEach((p) => {
      b.forEach((q) => {
        const d = haversineKm(p, q);
        if (d == null) return;
        if (best == null || d < best) {
          best = d;
          bestPt = p;
        }
      });
    });
    return { km: best, pt: bestPt };
  }

  function applyLocation(row, company, focus, overviewFeatures) {
    const pair = closestPair(company, focus, overviewFeatures);
    const loc = pair.pt || locationForCompany(company, overviewFeatures);
    if (loc) {
      row.lat = loc.lat;
      row.lon = loc.lon;
    }
    if (pair.km != null) row.distance_km = Math.round(pair.km * 10) / 10;
    const mine = nearestMine(focus, loc);
    if (mine) row.nearest_focus_mine = mine.name || "";
    row.region_notes = regionNotes(company, mine);
    const prov = provincesOf(company);
    const focusProv = provincesOf(focus);
    row.provinces = prov.join(" ");
    row.shared_provinces = sharedProvinces(prov, focusProv).join(" ");
    if (company && company.claim_count) row.claim_count = company.claim_count;
  }

  function mergeRelation(a, b) {
    const hasAdj = /adjacent/.test(a) || /adjacent/.test(b);
    const hasNear = /nearby/.test(a) || /nearby/.test(b);
    if (hasAdj && hasNear) return "adjacent claims + nearby";
    if (hasAdj) return "adjacent claims";
    if (hasNear) return "nearby";
    return a || b || "";
  }

  function rowKey(row) {
    if (row.company_id) return "id:" + row.company_id;
    return "h:" + normName(row.holder);
  }

  function buildNeighborRows(opts) {
    const focus = opts && opts.focus;
    const catalog = opts && opts.catalog;
    if (!focus || !catalog) return [];
    const overviewFeatures = (opts && opts.overviewFeatures) || [];
    const padDeg = opts && opts.padDeg;
    const catalogIndex = buildCatalogIndex(catalog);
    const tickerIndex = (opts && opts.tickerIndex) || buildTickerIndex(opts && opts.lookups);
    const pads = padsForFocus(focus, overviewFeatures, padDeg);
    const merged = {};

    function upsert(partial) {
      const key = rowKey(partial);
      if (!key || key === "h:") return;
      const prev = merged[key];
      if (!prev) {
        merged[key] = partial;
        return;
      }
      prev.relation = mergeRelation(prev.relation, partial.relation);
      if (partial.neighbor_title_count && (!prev.neighbor_title_count || Number(partial.neighbor_title_count) > Number(prev.neighbor_title_count))) {
        prev.neighbor_title_count = partial.neighbor_title_count;
        if (partial.holder) prev.holder = partial.holder;
      }
      if (!prev.company_id && partial.company_id) {
        prev.company_id = partial.company_id;
        prev.company = partial.company;
        prev.match_confidence = partial.match_confidence;
        prev.match_source = partial.match_source;
        prev.ticker_cad = partial.ticker_cad;
        prev.ticker_us = partial.ticker_us;
        prev.tickers = partial.tickers;
      }
      if (prev.distance_km === "" && partial.distance_km !== "") prev.distance_km = partial.distance_km;
      if (!prev.lat && partial.lat) {
        prev.lat = partial.lat;
        prev.lon = partial.lon;
      }
      if (!prev.nearest_focus_mine && partial.nearest_focus_mine) prev.nearest_focus_mine = partial.nearest_focus_mine;
      if (!prev.region_notes && partial.region_notes) prev.region_notes = partial.region_notes;
      if (!prev.provinces && partial.provinces) prev.provinces = partial.provinces;
      if (!prev.shared_provinces && partial.shared_provinces) prev.shared_provinces = partial.shared_provinces;
      if (!prev.claim_count && partial.claim_count) prev.claim_count = partial.claim_count;
    }

    ((focus.neighbors || [])).forEach((nb) => {
      const holder = nb && nb.holder;
      if (!holder || isFocusHolder(holder, focus, catalogIndex)) return;
      const matched = matchHolder(holder, catalogIndex, tickerIndex);
      if (matched.company_id === focus.id) return;
      const row = emptyRow();
      row.holder = holder;
      row.company_id = matched.company_id || "";
      row.company = matched.company_name || "";
      row.match_confidence = matched.confidence;
      row.match_source = matched.source;
      row.relation = "adjacent claims";
      row.neighbor_title_count = nb.count || "";
      applyTickers(row, matched.tickers);
      if (matched.company) applyLocation(row, matched.company, focus, overviewFeatures);
      else if (matched.tickers && matched.tickers.all && matched.tickers.all.length) {
        row.company = matched.company_name || holder;
      }
      upsert(row);
    });

    ((catalog.companies || [])).forEach((c) => {
      if (!c || c.id === focus.id) return;
      const boxes = [];
      overviewCellsFor(overviewFeatures, c.id).forEach((f) => {
        const b = featureBbox(f);
        if (b) boxes.push(b);
      });
      if (!boxes.length) {
        companyBboxes(c).forEach((b) => boxes.push(b));
        canadaMines(c).forEach((m) => {
          boxes.push([m.lon - 0.05, m.lat - 0.05, m.lon + 0.05, m.lat + 0.05]);
        });
      }
      if (!hitsPads(boxes, pads)) return;
      const tickers = tickersForCompany(c, tickerIndex);
      const row = emptyRow();
      row.holder = c.holder || (c.names && c.names[0]) || c.id;
      row.company_id = c.id;
      row.company = c.holder || (c.names && c.names[0]) || c.id;
      row.match_confidence = "exact";
      row.match_source = "catalog";
      row.relation = "nearby";
      applyTickers(row, tickers);
      applyLocation(row, c, focus, overviewFeatures);
      upsert(row);
    });

    const rows = Object.keys(merged).map((k) => merged[k]);
    rows.sort((a, b) => {
      const ar = /adjacent/.test(a.relation) ? 0 : 1;
      const br = /adjacent/.test(b.relation) ? 0 : 1;
      if (ar !== br) return ar - br;
      const ac = Number(a.neighbor_title_count) || 0;
      const bc = Number(b.neighbor_title_count) || 0;
      if (bc !== ac) return bc - ac;
      const ad = a.distance_km === "" ? 1e9 : Number(a.distance_km);
      const bd = b.distance_km === "" ? 1e9 : Number(b.distance_km);
      if (ad !== bd) return ad - bd;
      return String(a.holder).localeCompare(String(b.holder));
    });
    return rows;
  }

  const api = {
    FIELDS: FIELDS,
    PAD_DEG: PAD_DEG,
    FEATURED_NEEDLES: FEATURED_NEEDLES,
    normName: normName,
    featuredIdForHolder: featuredIdForHolder,
    classifyTicker: classifyTicker,
    buildTickerIndex: buildTickerIndex,
    buildCatalogIndex: buildCatalogIndex,
    matchHolder: matchHolder,
    tickersForCompany: tickersForCompany,
    buildNeighborRows: buildNeighborRows,
    toCsv: toCsv,
    filenameFor: filenameFor,
    provincesOf: provincesOf,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = api;
  root.qcClaimsNeighbors = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
