#!/usr/bin/env node
/* Static check: neighbor sheet columns + ticker matching. No network. */
const fs = require("fs");
const path = require("path");
const assert = require("assert");

const root = path.resolve(__dirname, "..");
const api = require(path.join(root, "claims-neighbors.js"));

function load(rel) {
  return JSON.parse(fs.readFileSync(path.join(root, rel), "utf8"));
}

const catalog = load("claims/companies.json");
const insider = load("insider-companies.json");
const issuers = load("beta/issuers.json");
const explorers = load("beta/explorers.json");
const overview = load("claims/overview.geojson");

const tickerIndex = api.buildTickerIndex({
  insiderCompanies: insider.companies || [],
  betaIssuers: (issuers.issuers || []).concat(explorers.issuers || []),
});
const catalogIndex = api.buildCatalogIndex(catalog);

function byId(id) {
  return (catalog.companies || []).find((c) => c.id === id);
}

function rowsFor(id) {
  return api.buildNeighborRows({
    catalog: catalog,
    focus: byId(id),
    tickerIndex: tickerIndex,
    overviewFeatures: overview.features || [],
    padDeg: api.PAD_DEG,
  });
}

function findRow(rows, pred) {
  return rows.find(pred);
}

const agnico = rowsFor("agnico-eagle");
assert.ok(agnico.length >= 20, "Agnico neighbor sheet should list surrounding holders, got " + agnico.length);

const probe = findRow(agnico, (r) => r.company_id === "probe-gold" || /probe gold/i.test(r.holder));
assert.ok(probe, "Agnico sheet includes Probe Gold");
assert.strictEqual(probe.company_id, "probe-gold");
assert.ok(/\bPRB\.TO\b/.test(probe.ticker_cad + " " + probe.tickers), "Probe ticker from beta/explorers: " + probe.tickers);
assert.ok(/adjacent/.test(probe.relation), "Probe is an adjacent-claims neighbor");

const eldo = findRow(agnico, (r) => r.company_id === "eldorado-gold");
assert.ok(eldo, "Agnico sheet includes Eldorado");
assert.ok(/\bELD\.TO\b/.test(eldo.ticker_cad + " " + eldo.tickers), "Eldorado CAD ticker: " + eldo.tickers);
assert.ok(/\bEGO\b/.test(eldo.ticker_us + " " + eldo.tickers), "Eldorado US ticker: " + eldo.tickers);

const wes = findRow(agnico, (r) => r.company_id === "wesdome-gold-mines");
assert.ok(wes, "Agnico sheet includes Wesdome");
assert.ok(/\bWDO\.TO\b/.test(wes.ticker_cad + " " + wes.tickers), "Wesdome CAD ticker: " + wes.tickers);

const windfall = findRow(agnico, (r) => r.company_id === "gold-fields" || /windfall/i.test(r.holder));
assert.ok(windfall && windfall.company_id === "gold-fields", "Windfall holder maps to gold-fields");

const jean = findRow(agnico, (r) => /jean robert/i.test(r.holder));
assert.ok(jean, "Private holder Jean Robert stays on the sheet");
assert.strictEqual(jean.tickers, "", "Do not invent a ticker for Jean Robert");
assert.strictEqual(jean.company_id, "", "Jean Robert is not a catalog company");
assert.strictEqual(jean.match_confidence, "unmatched");

const carat = findRow(agnico, (r) => /carat/i.test(r.holder));
if (carat) {
  assert.notStrictEqual(carat.company_id, "thor-explorations", "Carat must not fuzzy-match Thor Explorations");
  assert.strictEqual(carat.tickers, "", "Do not invent a ticker for Explorations Carat");
}

const known = {};
(insider.companies || []).forEach((c) => (c.all || []).forEach((t) => { known[String(t).toUpperCase()] = 1; }));
(issuers.issuers || []).concat(explorers.issuers || []).forEach((c) => (c.tickers || []).forEach((t) => { known[String(t).toUpperCase()] = 1; }));
agnico.forEach((r) => {
  String(r.tickers || "").split(/\s+/).filter(Boolean).forEach((t) => {
    assert.ok(known[t.toUpperCase()], "invented ticker " + t + " for " + r.holder);
  });
});

const withTk = agnico.filter((r) => r.tickers);
assert.ok(withTk.length >= 8, "Agnico sheet should resolve several public tickers, got " + withTk.length);
assert.ok(agnico.some((r) => r.lat !== "" && r.lon !== ""), "Agnico sheet includes lat/lon where catalog location exists");
assert.ok(agnico.some((r) => r.provinces), "Agnico sheet includes province columns");
assert.ok(agnico.some((r) => r.nearest_focus_mine), "Agnico sheet includes nearest focus mine when mines exist");

const csv = api.toCsv(agnico);
api.FIELDS.forEach((col) => {
  assert.ok(csv.indexOf(col) !== -1, "CSV header missing " + col);
});
assert.strictEqual(api.filenameFor(byId("agnico-eagle")), "qc-claims-neighbors-agnico-eagle.csv");

const match = api.matchHolder("Mines Abcourt Inc.", catalogIndex, tickerIndex);
assert.strictEqual(match.company_id, "abcourt-mines");
assert.ok(match.tickers.all.length, "Abcourt should resolve insider tickers");

const equinox = rowsFor("equinox-gold");
assert.ok(equinox.length >= 3, "Equinox has no catalog.neighbors but should still list nearby catalog peers, got " + equinox.length);
assert.ok(equinox.every((r) => r.company_id !== "equinox-gold"), "Sheet excludes the focus company");
assert.ok(equinox.some((r) => r.company_id), "Equinox spatial peers include catalog ids");

const iam = rowsFor("iamgold");
assert.ok(iam.length >= 5, "IAMGOLD neighbor sheet should not be empty, got " + iam.length);

console.log("check_claims_neighbors ok");
console.log("  agnico rows", agnico.length, "with tickers", withTk.length);
console.log("  equinox rows", equinox.length);
console.log("  iamgold rows", iam.length);
console.log("  probe", probe.tickers, probe.relation, "dist", probe.distance_km);
console.log("  eldorado", eldo.tickers, eldo.provinces);
