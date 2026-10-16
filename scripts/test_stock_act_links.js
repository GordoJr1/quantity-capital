#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const root = path.join(__dirname, "..");
let failed = 0;

function check(ok, message) {
  if (ok) return;
  failed += 1;
  console.error("FAIL " + message);
}

const document = {
  readyState: "loading",
  addEventListener() {},
  querySelectorAll() { return []; },
  querySelector() { return null }
};
const location = {
  href: "https://gordojr1.github.io/quantity-capital/insiders.html",
  pathname: "/quantity-capital/insiders.html",
  search: "",
  hash: ""
};
const window = {
  document: document,
  location: location,
  addEventListener() {},
  matchMedia() { return { matches: false }; }
};
window.window = window;
const context = {
  window: window,
  document: document,
  location: location,
  history: {},
  URL: URL,
  console: console
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(root, "qc.js"), "utf8"), context);
const QC = context.window.QC;
check(!!QC && typeof QC.stockActPage === "function", "QC.stockActPage missing");
check(!!QC && typeof QC.rewriteRedirectHref === "function", "QC.rewriteRedirectHref missing");

const origin = "https://gordojr1.github.io/stock-act/";
const base = "https://gordojr1.github.io/quantity-capital/insiders.html";
check(QC.STOCK_ACT === origin, "STOCK_ACT is " + QC.STOCK_ACT);
check(QC.POLITICIAN_TAPE_URL === origin + "trades-lite.json", "tape url " + QC.POLITICIAN_TAPE_URL);
check(QC.stockActPage("politician.html") === origin + "politician.html", "politician page");
check(QC.stockActPage("ticker.html") === origin + "ticker.html", "ticker page");

const tape = QC.politicianTapeListHtml([{
  id: "t1",
  filer_id: "rep-1",
  filer: "Jane Doe",
  ticker: "AAPL",
  asset: "Apple",
  filed_date: "2026-01-02",
  trade_date: "2026-01-01",
  side: "purchase",
  amount: "$1,001 - $15,000"
}], {});
check(tape.indexOf(origin + "politician.html?id=rep-1") >= 0, "tape name link is Stock Act politician.html");
check(tape.indexOf(origin + "ticker.html?t=AAPL") >= 0, "tape ticker link is Stock Act ticker.html");
check(tape.indexOf("insider.html?id=") < 0, "tape name link is not insider.html");
check(tape.indexOf("insider-ticker.html?t=") < 0, "tape ticker link is not insider-ticker.html");

const insiderTape = QC.politicianTapeListHtml([{
  id: "t2",
  filer_id: "off-1",
  filer: "Pat Officer",
  ticker: "NEM",
  asset: "Newmont",
  filed_date: "2026-01-02",
  trade_date: "2026-01-01",
  side: "purchase",
  amount: "$1,001 - $15,000"
}], { namePage: "insider.html", tickerPage: "insider-ticker.html" });
check(insiderTape.indexOf("insider.html?id=off-1") >= 0, "explicit insider name page stays");
check(insiderTape.indexOf("insider-ticker.html?t=NEM") >= 0, "explicit insider ticker page stays");

const rewrite = QC.rewriteRedirectHref;
check(rewrite("ticker.html", base) === "insider-ticker.html", "QC ticker.html rewrites");
check(rewrite("ticker.html?t=PLTR", base) === "insider-ticker.html?t=PLTR", "QC ticker query rewrites");
check(rewrite("signals.html", base) === "insider-signals.html", "QC signals.html rewrites");
check(rewrite("landed.html?x=1", base) === "insider-landed.html?x=1", "QC landed.html rewrites");
check(rewrite("politician.html?id=rep-1", base) === "politician.html?id=rep-1", "QC politician.html is not rewritten");

const pol = origin + "politician.html?id=rep-1";
const tick = origin + "ticker.html?t=PLTR";
check(rewrite(pol, base) === pol, "Stock Act politician.html stays");
check(rewrite(tick, base) === tick, "Stock Act ticker.html stays");
check(rewrite("/stock-act/politician.html?id=rep-1", base) === "/stock-act/politician.html?id=rep-1", "stock-act path stays");
check(
  rewrite("https://example.com/quantity-capital/ticker.html", base) === "https://example.com/quantity-capital/ticker.html",
  "other origin stays"
);
check(
  rewrite("ticker.html", "http://127.0.0.1:8000/insiders.html") === "ticker.html",
  "localhost path outside /quantity-capital/ stays"
);

const qc = fs.readFileSync(path.join(root, "qc.js"), "utf8");
const overlap = fs.readFileSync(path.join(root, "overlap.html"), "utf8");
const research = fs.readFileSync(path.join(root, "research.html"), "utf8");
check(qc.includes('stockActPage("politician.html")'), "qc.js name page uses stockActPage");
check(qc.includes('stockActPage("ticker.html")'), "qc.js ticker page uses stockActPage");
check(!/["']politician\.html["']:\s*"insider\.html"/.test(qc), "INSIDER_FOR no longer maps politician.html");
check(overlap.includes('QCAPI.stockActPage("politician.html")'), "overlap polLine uses stockActPage");
check(overlap.includes('QCAPI.stockActPage("ticker.html")'), "overlap tape ticker uses stockActPage");
check(overlap.includes("insider-ticker.html?t="), "overlap keeps the insider tape link");
check(research.includes('QC.stockActPage("ticker.html")'), "research builds Stock Act ticker links");
check(research.indexOf("insider-ticker.html?t=PLTR") < 0, "research no longer points PLTR at insider-ticker");

if (failed) {
  console.error(failed + " failed");
  process.exit(1);
}
console.log("stock act links ok");
