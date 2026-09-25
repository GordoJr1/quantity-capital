// Split trades.json / trades-lite.json into the files the pages fetch.
// Enrichment uses qc.js so code, company, and industry match the site.
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const sandbox = {
  console,
  document: {
    readyState: "loading",
    addEventListener() {},
    querySelector() { return null; },
    documentElement: { style: {} }
  },
  location: { pathname: "/", search: "", hash: "" },
  navigator: { serviceWorker: null },
  localStorage: { getItem() { return null; }, setItem() {} }
};
sandbox.window = sandbox;
sandbox.global = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path.join(root, "qc.js"), "utf8"), sandbox, { filename: "qc.js" });
const QC = sandbox.QC;
if (!QC || !QC.enrich) throw new Error("qc.js did not export QC");

const MEGA = {
  AAPL: 1, MSFT: 1, NVDA: 1, AMZN: 1, GOOGL: 1, GOOG: 1, META: 1, TSLA: 1,
  BRK: 1, "BRK.B": 1, BRKB: 1, AVGO: 1, JPM: 1, LLY: 1, UNH: 1, V: 1, MA: 1
};
const SKIP = {
  LP: 1, SPCX: 1, GOOGM: 1, GOOGN: 1, SPY: 1, QQQ: 1, QQQM: 1, VOO: 1, VTI: 1,
  IWM: 1, DIA: 1, IVV: 1, VEA: 1, VWO: 1, ARKK: 1, TLT: 1, BND: 1, AGG: 1,
  XLF: 1, XLK: 1, XLE: 1, XLV: 1, XLI: 1, XLY: 1, XLP: 1, XLU: 1, XLB: 1,
  XLRE: 1, SMH: 1, SOXX: 1, IJR: 1, IJH: 1, RSP: 1, VGT: 1, VOOG: 1, VUG: 1, VTV: 1
};
const HALF = 10;
const PAGE = 120;
const DESK_MAX = 300;
const MIN_FILERS = 6;
const SINGLE_CAP = 16;

function readJson(name) {
  return JSON.parse(fs.readFileSync(path.join(root, name), "utf8"));
}

function writeJson(rel, obj) {
  const file = path.join(root, rel);
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(obj));
  return fs.statSync(file).size;
}

function stamp(t, file) {
  const row = QC.enrich(t, file);
  delete row.source;
  delete row.option;
  return row;
}

function kindAllows(kind, t) {
  const k = QC.assetKind(t);
  if (kind === "stock") return k === "stock" || k === "option";
  if (kind === "option") return k === "option";
  if (kind === "bond") return k === "bond";
  if (kind === "") return true;
  return k === kind;
}

function sliceOpts(desktop) {
  return {
    showAll: false,
    desktop,
    page: PAGE,
    deskMax: DESK_MAX,
    minFilers: MIN_FILERS,
    singleFilerCap: SINGLE_CAP,
    pinLandedHours: 72,
    sort: "filed"
  };
}

function sliceIds(rows, desktop) {
  return QC.sliceTapeRows(rows, sliceOpts(desktop)).rows.map((t) => t.id).join("\n");
}

function minimalPool(rows) {
  const wantPhone = sliceIds(rows, false);
  const wantDesk = sliceIds(rows, true);
  const landed = rows.filter((t) => QC.isLanded(t, 72));
  const landedIds = new Set(landed.map((t) => t.id));
  const rest = rows.filter((t) => !landedIds.has(t.id)).sort(QC.compareFiledDesc);
  let n = Math.min(rest.length, 250);
  while (n <= rest.length) {
    const pool = landed.concat(rest.slice(0, n));
    if (sliceIds(pool, false) === wantPhone && sliceIds(pool, true) === wantDesk) return pool;
    if (n === rest.length) break;
    n = Math.min(rest.length, Math.max(n + 250, Math.ceil(n * 1.5)));
  }
  return rows.slice();
}

function garbageCompany(s) {
  const t = String(s || "");
  if (!t) return false;
  if (/[\uFFFD]|Ã.|Â.|â€.|ï¿½|ðŸ/.test(t)) return true;
  let weird = 0;
  let letters = 0;
  for (let i = 0; i < t.length; i++) {
    const c = t.charCodeAt(i);
    if ((c >= 65 && c <= 90) || (c >= 97 && c <= 122)) letters++;
    else if (c < 32 || c === 127 || c > 255) weird++;
    else if (c > 127 && c < 160) weird++;
  }
  if (t.length >= 4 && letters === 0) return true;
  if (t.length >= 8 && weird / t.length > 0.18) return true;
  if (!/\s/.test(t) && t.length > 18 && (t.match(/[a-z][A-Z]/g) || []).length > 4) return true;
  return false;
}

function displayCompany(name, ticker) {
  const s = String(name || "").replace(/\s+/g, " ").trim();
  const tk = String(ticker || "").trim();
  if (!s || s === "—") return tk && tk !== "—" ? tk : "";
  if (garbageCompany(s)) return tk && tk !== "—" ? tk : "";
  return s;
}

function canonFilerId(fid, bios) {
  const aliases = (bios && bios.aliases) || {};
  return aliases[fid] || fid;
}

function buildBoards(trades, bios, tradersFile) {
  const cutoff = new Date();
  cutoff.setFullYear(cutoff.getFullYear() - 3);
  const byId = new Map();
  trades.forEach((t) => {
    const fid = canonFilerId(t.filer_id, bios);
    if (!fid || new Date(t.trade_date) < cutoff) return;
    if (!byId.has(fid)) byId.set(fid, []);
    byId.get(fid).push(t);
  });
  const people = (bios && bios.people) || {};
  const pnlPeople = (tradersFile && tradersFile.people) || {};
  const rows = [];
  byId.forEach((list, id) => {
    const overlaps = findOverlaps(people[id], list);
    const whys = [];
    overlaps.forEach((o) => {
      String(o.why || "").split("; ").forEach((w) => {
        if (w && whys.indexOf(w) === -1) whys.push(w);
      });
    });
    const rec = pnlPeople[id] || { pnl: 0, tickers: {} };
    rows.push({
      id,
      name: (people[id] && people[id].name) || list[0].filer,
      trades: list.length,
      conflicts: overlaps.length,
      whys,
      pnl: rec.pnl || 0,
      sectors: traderSectors(list, overlaps, rec.tickers)
    });
  });
  const byTicker = new Map();
  trades.forEach((t) => {
    const fid = canonFilerId(t.filer_id, bios);
    if (!fid || new Date(t.trade_date) < cutoff) return;
    if ((t.side !== "purchase" && t.side !== "sale") || QC.isBond(t) || QC.isOptionLike(t) || !QC.isChartTicker(t.code)) return;
    const code = t.code.toUpperCase();
    const prev = byTicker.get(code) || { code, name: t.company || code, buys: 0, sells: 0, people: new Set() };
    if (t.side === "purchase") prev.buys += 1;
    else prev.sells += 1;
    prev.people.add(fid);
    if (t.company && t.company !== "—") {
      const cleaned = displayCompany(String(t.company).replace(/\s+-\s*$/, ""), code);
      if (cleaned) prev.name = cleaned;
    }
    byTicker.set(code, prev);
  });
  const stocks = Array.from(byTicker.values()).map((s) => ({
    code: s.code,
    name: s.name,
    buys: s.buys,
    sells: s.sells,
    trades: s.buys + s.sells,
    people: s.people.size
  })).sort((a, b) => b.trades - a.trades || b.buys - a.buys || a.code.localeCompare(b.code));
  return {
    activity: rows.slice().sort((a, b) => b.trades - a.trades || a.name.localeCompare(b.name)),
    traders: rows.filter((r) => r.pnl !== 0 || (r.sectors && r.sectors.length)).sort((a, b) => b.pnl - a.pnl || a.name.localeCompare(b.name)),
    conflicts: rows.filter((r) => r.conflicts > 0).sort((a, b) => b.conflicts - a.conflicts || b.trades - a.trades),
    stocks
  };
}

function findOverlaps(bio, rows) {
  const seats = [].concat((bio && bio.committees) || []).concat((bio && bio.subcommittees) || []);
  if (!seats.length) return [];
  const byCode = new Map();
  rows.filter((t) => t.side === "purchase" && !QC.isBond(t) && !QC.isOptionLike(t)).forEach((t) => {
    const code = t.code || "—";
    const industry = t.industry || "";
    seats.forEach((seat) => {
      QC.OVERLAP_RULES.forEach((rule) => {
        if (!rule.seat.test(seat.name || "")) return;
        if (!QC.overlapHit(rule, code, t.company || t.asset || "", industry)) return;
        const prev = byCode.get(code);
        if (!prev) {
          byCode.set(code, { code, why: rule.why });
          return;
        }
        if (prev.why.indexOf(rule.why) === -1) prev.why += "; " + rule.why;
      });
    });
  });
  return Array.from(byCode.values());
}

function tickerSector(list, code) {
  const hit = list.find((t) => (t.code || "").toUpperCase() === code);
  return QC.sectorOf(hit && hit.industry) || "Industrials";
}

function traderSectors(list, overlaps, tickers) {
  const pnlBy = {};
  Object.keys(tickers || {}).forEach((code) => {
    const sec = tickerSector(list, code);
    pnlBy[sec] = (pnlBy[sec] || 0) + (tickers[code] || 0);
  });
  const conflictBy = {};
  (overlaps || []).forEach((o) => {
    const sec = tickerSector(list, String(o.code || "").toUpperCase());
    if (!conflictBy[sec]) conflictBy[sec] = [];
    String(o.why || "").split("; ").forEach((w) => {
      if (w && conflictBy[sec].indexOf(w) === -1) conflictBy[sec].push(w);
    });
  });
  const names = QC.SECTORS.filter((s) => pnlBy[s] || conflictBy[s]);
  names.sort((a, b) => Math.abs(pnlBy[b] || 0) - Math.abs(pnlBy[a] || 0) || a.localeCompare(b));
  return names.map((name) => ({
    name,
    pnl: pnlBy[name] || 0,
    conflict: !!(conflictBy[name] && conflictBy[name].length),
    why: (conflictBy[name] || []).join(" · ")
  }));
}

function uniqueFilers(trades, bios) {
  const map = new Map();
  const people = (bios && bios.people) || {};
  trades.forEach((t) => {
    const id = canonFilerId(t.filer_id, bios);
    if (!id || map.has(id)) return;
    const person = people[id] || {};
    map.set(id, person.name || person.display || t.filer);
  });
  return Array.from(map, ([id, name]) => ({ id, name })).sort((a, b) => a.name.localeCompare(b.name));
}

function isoDay(ms) {
  const d = new Date(ms);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return y + "-" + m + "-" + day;
}

function daysBetween(iso, now) {
  const a = new Date(iso + "T00:00:00").getTime();
  return Math.max(0, (now - a) / 86400000);
}

function decay(days) {
  return Math.pow(2, -days / HALF);
}

function parenTicker(asset) {
  const m = String(asset || "").match(/\(([A-Z]{1,5})\)/);
  return m ? m[1] : "";
}

function resolveInfo(t, tickerFile) {
  const fake = Object.assign({}, t);
  if (!QC.isChartTicker(fake.ticker)) {
    const p = parenTicker(fake.asset);
    if (p) fake.ticker = p;
  }
  return QC.tickerInfo(fake, tickerFile);
}

function isPublicStock(t, info) {
  if (SKIP[info.code]) return false;
  const type = (t.asset_type || "").toLowerCase();
  if (/non-public|municipal|corporate bond|\bbond\b|other/.test(type)) return false;
  if (QC.isBond(t) || QC.isOptionLike(t) || QC.isFilingError(t)) return false;
  const blob = ((info.name || "") + " " + (info.industry || "") + " " + (t.asset || "")).toLowerCase();
  if (/exchange traded|\betf\b|space exploration technologies|mandatory convertible|private equity|pooled investment|successor agency/.test(blob)) return false;
  return QC.isChartTicker(info.code);
}

function scoreRows(trades, tickerFile, bios, now, win) {
  const cut = isoDay(now - win * 86400000);
  const baseCut = isoDay(QC.threeYearCutoff().getTime());
  const by = new Map();
  trades.forEach((t) => {
    if (t.side !== "purchase" && t.side !== "sale") return;
    const info = resolveInfo(t, tickerFile);
    if (!isPublicStock(t, info)) return;
    const rec = by.get(info.code) || {
      code: info.code,
      name: info.name,
      industry: info.industry,
      sector: (info.industry && info.industry !== "—") ? QC.sectorOf(info.industry) : "",
      buys: [],
      sells: [],
      histBuys: 0
    };
    if (info.name && info.name !== "—") rec.name = info.name;
    if (info.industry && info.industry !== "—") {
      rec.industry = info.industry;
      rec.sector = QC.sectorOf(info.industry);
    }
    const when = t.trade_date || "";
    if (when >= baseCut && t.side === "purchase") rec.histBuys += 1;
    if (when >= cut) {
      if (t.side === "purchase") rec.buys.push(t);
      else rec.sells.push(t);
    }
    by.set(info.code, rec);
  });

  const out = [];
  by.forEach((rec) => {
    if (!rec.buys.length) return;
    const filers = new Map();
    function touch(t, side) {
      const hi = QC.amountHigh(t.amount);
      const prev = filers.get(t.filer_id) || {
        name: t.filer, id: t.filer_id, buyHigh: 0, sellHigh: 0,
        lastBuy: "", lastSell: "", filed: "", nBuys: 0, conflict: false
      };
      if (side === "buy") {
        prev.buyHigh += hi;
        prev.nBuys += 1;
        if (t.trade_date > prev.lastBuy) prev.lastBuy = t.trade_date;
        if ((t.filed_date || "") > prev.filed) prev.filed = t.filed_date || "";
      } else {
        prev.sellHigh += hi;
        if (t.trade_date > prev.lastSell) prev.lastSell = t.trade_date;
      }
      filers.set(t.filer_id, prev);
    }
    rec.buys.forEach((t) => touch(t, "buy"));
    rec.sells.forEach((t) => touch(t, "sell"));

    const buyers = [];
    let buyHigh = 0;
    let sellHigh = 0;
    filers.forEach((f) => {
      buyHigh += f.buyHigh;
      sellHigh += f.sellHigh;
      const netHigh = f.buyHigh - f.sellHigh;
      if (netHigh > 0 && f.lastBuy) {
        buyers.push({
          name: f.name, id: f.id, high: netHigh, last: f.lastBuy,
          filed: f.filed, nBuys: f.nBuys, conflict: false
        });
      }
    });
    if (!buyers.length) return;

    let recency = 0;
    buyers.forEach((b) => {
      recency += Math.log10(1 + b.high / 1000) * decay(daysBetween(b.last, now));
    });
    recency += 0.1 * Math.max(0, buyers.reduce((n, b) => n + b.nBuys, 0) - buyers.length);

    let conflict = false;
    buyers.forEach((b) => {
      const bio = (bios.people || {})[b.id];
      const seats = [].concat((bio && bio.committees) || []).concat((bio && bio.subcommittees) || []);
      seats.forEach((seat) => {
        QC.OVERLAP_RULES.forEach((rule) => {
          if (!rule.seat.test(seat.name || "")) return;
          if (QC.overlapHit(rule, rec.code, rec.name, rec.industry)) {
            b.conflict = true;
            conflict = true;
          }
        });
      });
    });

    const lastBuy = buyers.reduce((m, b) => b.last > m ? b.last : m, buyers[0].last);
    const lastFiled = buyers.reduce((m, b) => (b.filed || "") > m ? b.filed : m, buyers[0].filed || "");
    const nBuyers = buyers.length;
    const nPrints = buyers.reduce((n, b) => n + b.nBuys, 0);
    const months = Math.max(1, (now - QC.threeYearCutoff().getTime()) / (86400000 * 30));
    const burst = 1 + Math.min(2, nBuyers / Math.max(0.35, rec.histBuys / months));
    const mixed = sellHigh > 0 ? 0.82 : 1;
    const mega = MEGA[rec.code] ? 0.8 : 1;
    const heat = recency * (1 + Math.log(1 + nBuyers)) * mixed * (conflict ? 1.32 : 1) * burst * mega;
    buyers.sort((a, b) => b.high - a.high || b.last.localeCompare(a.last));
    out.push({
      code: rec.code,
      name: rec.name,
      sector: rec.sector,
      heat,
      histBuys: rec.histBuys,
      buyers,
      nBuyers,
      nPrints,
      buyHigh,
      sellHigh,
      lastBuy,
      lastFiled,
      conflict,
      whale: buyers.some((b) => b.high >= 500000),
      fresh: daysBetween(lastBuy, now) <= 14,
      cluster: nBuyers >= 3,
      mega: !!MEGA[rec.code]
    });
  });
  out.sort((a, b) => b.heat - a.heat || b.lastBuy.localeCompare(a.lastBuy));
  return out;
}

function heatOf(row, now) {
  const buyers = row.buyers || [];
  let recency = 0;
  buyers.forEach((b) => {
    recency += Math.log10(1 + b.high / 1000) * decay(daysBetween(b.last, now));
  });
  recency += 0.1 * Math.max(0, buyers.reduce((n, b) => n + b.nBuys, 0) - buyers.length);
  const nBuyers = buyers.length;
  const months = Math.max(1, (now - QC.threeYearCutoff().getTime()) / (86400000 * 30));
  const burst = 1 + Math.min(2, nBuyers / Math.max(0.35, (row.histBuys || 0) / months));
  const mixed = row.sellHigh > 0 ? 0.82 : 1;
  const conflict = buyers.some((b) => b.conflict);
  const mega = row.mega ? 0.8 : 1;
  return recency * (1 + Math.log(1 + nBuyers)) * mixed * (conflict ? 1.32 : 1) * burst * mega;
}

function tradeCode(t, tickerFile) {
  const extra = ((tickerFile && tickerFile.assets) || {})[t.asset] || {};
  const opt = QC.optionMeta(t);
  return String(t.ticker || extra.ticker || opt.under || "").toUpperCase();
}

const RESERVED_WIN = new Set([
  "CON", "PRN", "AUX", "NUL",
  "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
  "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
]);

function safeFile(id) {
  const s = String(id || "");
  if (!/^[A-Za-z0-9._-]{1,180}$/.test(s) || s === "." || s === "..") return "";
  return s;
}

function tickerFilename(code) {
  const safe = safeFile(code);
  if (!safe) return "";
  // CON.json is a Windows device, so git cannot index it.
  if (RESERVED_WIN.has(safe.toUpperCase())) return safe + "_.json";
  return safe + ".json";
}

function rollingCutoff() {
  const d = new Date();
  d.setFullYear(d.getFullYear() - 3);
  d.setDate(d.getDate() - 2);
  return d;
}

function onOrAfter(tradeDate, cutoff) {
  const d = new Date(tradeDate);
  return !isNaN(d.getTime()) && d >= cutoff;
}

function assertNoSource(rows, label) {
  for (let i = 0; i < rows.length; i++) {
    if (rows[i] && Object.prototype.hasOwnProperty.call(rows[i], "source")) {
      throw new Error(label + " still has source on row " + i);
    }
  }
}

function replaceDir(rel, entries) {
  const dir = path.join(root, rel);
  fs.rmSync(dir, { recursive: true, force: true });
  fs.mkdirSync(dir, { recursive: true });
  let bytes = 0;
  entries.forEach(([name, body]) => {
    const file = path.join(dir, name);
    fs.writeFileSync(file, JSON.stringify(body));
    bytes += fs.statSync(file).size;
  });
  return bytes;
}

const lite = readJson("trades-lite.json");
const full = readJson("trades.json");
const tickerFile = readJson("tickers.json");
const bios = readJson("bios.json");
const traders = readJson("traders.json");
const now = Date.now();
const generated = new Date(now).toISOString();
const cutoff = rollingCutoff();

const liteRows = (lite.trades || []).map((t) => stamp(t, tickerFile));
const fullRows = (full.trades || []).map((t) => stamp(t, tickerFile));
assertNoSource(liteRows, "lite");
assertNoSource(fullRows, "full");

const totals = {
  stock: liteRows.filter((t) => kindAllows("stock", t)).length,
  option: liteRows.filter((t) => kindAllows("option", t)).length,
  bond: liteRows.filter((t) => kindAllows("bond", t)).length,
  all: liteRows.length
};

const pools = {};
for (const kind of ["stock", "option", "bond", ""]) {
  const key = kind === "" ? "all" : kind;
  const subset = kind === "" ? liteRows : liteRows.filter((t) => kindAllows(kind, t));
  const pool = minimalPool(subset);
  if (sliceIds(pool, false) !== sliceIds(subset, false) || sliceIds(pool, true) !== sliceIds(subset, true)) {
    throw new Error("pool mismatch for kind " + key);
  }
  pools[key] = pool;
  console.error("pool", key, pool.length, "of", subset.length);
}

const filers = uniqueFilers(fullRows, bios);
const rawIds = new Set(Object.keys((bios && bios.people) || {}));
fullRows.forEach((t) => { if (t.filer_id) rawIds.add(t.filer_id); });
const boards = buildBoards(liteRows, bios, traders);

const pageBytes = writeJson("tape/page.json", {
  collected: lite.collected || "",
  generated,
  totals,
  filers,
  boards,
  trades: pools.stock
});
let kindBytes = 0;
for (const key of ["option", "bond", "all"]) {
  kindBytes += writeJson("tape/kind/" + key + ".json", { trades: pools[key] });
}
const allBytes = writeJson("tape/all.json", {
  collected: lite.collected || "",
  generated,
  trades: liteRows
});
const filerBytes = writeJson("tape/filers.json", {
  collected: full.collected || lite.collected || "",
  generated,
  filers,
  ids: Array.from(rawIds).sort()
});

const byFiler = new Map();
fullRows.forEach((t) => {
  const id = safeFile(t.filer_id);
  if (!id) return;
  if (!onOrAfter(t.trade_date, cutoff)) return;
  if (!byFiler.has(id)) byFiler.set(id, []);
  byFiler.get(id).push(t);
});
const politicianEntries = [];
byFiler.forEach((trades, id) => {
  trades.sort((a, b) => String(b.trade_date || "").localeCompare(String(a.trade_date || "")) || String(b.id || "").localeCompare(String(a.id || "")));
  politicianEntries.push([id + ".json", {
    collected: full.collected || "",
    generated,
    filer_id: id,
    trades
  }]);
});
const politicianBytes = replaceDir("tape/politicians", politicianEntries);

const byTicker = new Map();
fullRows.forEach((t) => {
  const code = safeFile(tradeCode(t, tickerFile));
  if (!code) return;
  if (!onOrAfter(t.trade_date, cutoff)) return;
  if (!byTicker.has(code)) byTicker.set(code, []);
  byTicker.get(code).push(t);
});
const tickerEntries = [];
byTicker.forEach((trades, code) => {
  trades.sort((a, b) => String(b.trade_date || "").localeCompare(String(a.trade_date || "")) || String(b.id || "").localeCompare(String(a.id || "")));
  tickerEntries.push([tickerFilename(code), {
    collected: full.collected || "",
    generated,
    ticker: code,
    trades
  }]);
});
const tickerBytes = replaceDir("tape/tickers", tickerEntries);

const landedRows = liteRows.filter((t) => QC.isLanded(t, 336));
const landedCheck = (lite.trades || []).filter((t) => QC.isLanded(t, 336)).map((t) => t.id).sort().join("\n");
const landedGot = landedRows.map((t) => t.id).sort().join("\n");
if (landedCheck !== landedGot) throw new Error("landed id mismatch");
const landedBytes = writeJson("landed.json", {
  collected: lite.collected || "",
  generated,
  hours: 336,
  trades: landedRows
});

const heatWindows = {};
for (const win of [14, 30, 90]) {
  const ranked = scoreRows(lite.trades || [], tickerFile, bios, now, win);
  ranked.forEach((row) => {
    const again = heatOf(row, now);
    if (Math.abs(again - row.heat) > 1e-6) {
      throw new Error("heat recompute mismatch " + row.code + " " + win + " " + again + " vs " + row.heat);
    }
  });
  heatWindows[String(win)] = ranked;
}
const issuers = {};
liteRows.forEach((t) => {
  const code = String(t.code || "");
  if (!QC.isChartTicker(code)) return;
  const name = t.company || "";
  if (!name || name === "—") return;
  if (!issuers[code] || QC.isWeakIssuer(code, issuers[code])) {
    if (!QC.isWeakIssuer(code, name)) issuers[code] = name;
  }
});
const heatBytes = writeJson("heat.json", {
  collected: lite.collected || "",
  generated,
  issuers,
  windows: heatWindows
});

console.error(JSON.stringify({
  pageBytes,
  kindBytes,
  allBytes,
  filerBytes,
  politicianBytes,
  politicianFiles: politicianEntries.length,
  tickerBytes,
  tickerFiles: tickerEntries.length,
  landedBytes,
  landedRows: landedRows.length,
  heatBytes,
  heat90: heatWindows["90"].length
}));
