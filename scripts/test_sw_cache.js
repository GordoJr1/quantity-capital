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

function count(hay, needle) {
  let n = 0;
  let i = 0;
  while (true) {
    const at = hay.indexOf(needle, i);
    if (at < 0) return n;
    n += 1;
    i = at + needle.length;
  }
}

const sw = fs.readFileSync(path.join(root, "sw.js"), "utf8");
const context = {
  self: {
    addEventListener() {},
    skipWaiting() {},
    clients: {
      claim() {},
      matchAll() { return Promise.resolve([]); },
      openWindow() {}
    }
  },
  caches: {},
  URL: URL,
  fetch() {},
  console: console
};
context.globalThis = context;
vm.createContext(context);
vm.runInContext(
  sw + "\nglobalThis.__drop = qcShellCachesToDelete;\nglobalThis.__SHELL = SHELL;\n",
  context
);

const drop = context.__drop;
const current = "qc-shell-v265";
const gone = drop([
  "qc-shell-v263",
  "qc-shell-v264",
  current,
  "stock-act-v9",
  "other",
  "qc-shell",
  "x-qc-shell-v1"
], current);
check(
  JSON.stringify(gone) === JSON.stringify(["qc-shell-v263", "qc-shell-v264"]),
  "filter keeps Stock Act and the live cache, got " + JSON.stringify(gone)
);
check(drop([], current).length === 0, "empty key list");
check(drop(["stock-act-v9"], "qc-shell-v1").length === 0, "stock-act-v9 stays when it is the only cache");
check(drop(["qc-shell-v1"], "qc-shell-v1").length === 0, "current qc-shell cache stays");
check(drop(["qc-shell-v9"], "qc-shell-v1").join() === "qc-shell-v9", "older qc-shell cache goes");

check(sw.includes("qcShellCachesToDelete(keys, CACHE)"), "activate calls qcShellCachesToDelete");
check(!/keys\.filter\(\(key\) => key !== CACHE\)/.test(sw), "activate still deletes every other cache name");

const shell = context.__SHELL;
["./politician.html", "./ticker.html", "./signals.html", "./landed.html", "./tells.html", "./paper.html"].forEach((name) => {
  check(shell.indexOf(name) < 0, "precache still has " + name);
});

function copies(name) {
  return shell.filter((entry) => {
    const base = entry.replace(/^\.\//, "");
    return base === name || base.startsWith(name + "?");
  });
}

["shell.css", "qc.js", "chart.js", "claims-map.js", "claims-db.js", "insider-track.css", "insider-track.js", "claims-neighbors.js", "city.jpg"].forEach((name) => {
  const found = copies(name);
  check(found.length === 1, name + " precached once, got " + JSON.stringify(found));
});

const shellQc = shell.filter((entry) => entry.indexOf("qc.js?v=") >= 0)[0] || "";
const version = (shellQc.match(/v=(\d+)/) || [])[1];
check(!!version, "shell lists a qc.js version");
fs.readdirSync(root).filter((name) => name.endsWith(".html")).forEach((name) => {
  const text = fs.readFileSync(path.join(root, name), "utf8");
  const tags = text.match(/qc\.js\?v=\d+/g) || [];
  tags.forEach((tag) => {
    check(tag === "qc.js?v=" + version, name + " loads " + tag + ", shell has v=" + version);
  });
});

const pagesUrl = "https://gordojr1.github.io/stock-act/trades-lite.json";
const rawUrl = "https://raw.githubusercontent.com/GordoJr1/stock-act/main/trades-lite.json";
const qc = fs.readFileSync(path.join(root, "qc.js"), "utf8");
const beta = fs.readFileSync(path.join(root, "beta.html"), "utf8");
const morning = fs.readFileSync(path.join(root, "check-morning-tapes.py"), "utf8");
check(count(qc, pagesUrl) === 1, "pages tape URL once in qc.js");
check(count(beta, pagesUrl) === 0, "beta.html no longer inlines the pages tape URL");
check(beta.includes("QC.POLITICIAN_TAPE_URL"), "beta.html uses QC.POLITICIAN_TAPE_URL");
check(count(morning, rawUrl) === 1, "raw tape URL once in check-morning-tapes.py");
check(morning.includes('POLITICIAN_URL = "' + rawUrl + '"'), "morning check keeps one Python constant");

function walk(dir, acc) {
  fs.readdirSync(dir).forEach((name) => {
    if (name === ".git" || name === "node_modules" || name === "prices" || name === "test_sw_cache.js") return;
    const full = path.join(dir, name);
    if (fs.statSync(full).isDirectory()) walk(full, acc);
    else if (/\.(js|html|py)$/.test(name)) acc.push(full);
  });
  return acc;
}

walk(root, []).forEach((file) => {
  const rel = path.relative(root, file);
  const text = fs.readFileSync(file, "utf8");
  if (rel !== "qc.js" && count(text, pagesUrl)) console.error("extra pages URL in " + rel);
  if (rel !== "qc.js") check(count(text, pagesUrl) === 0, "pages URL also in " + rel);
  if (rel !== "check-morning-tapes.py") check(count(text, rawUrl) === 0, "raw URL also in " + rel);
});

const manifest = JSON.parse(fs.readFileSync(path.join(root, "manifest.webmanifest"), "utf8"));
check(manifest.start_url === "./insiders.html", "start_url is " + manifest.start_url);
check(fs.existsSync(path.join(root, "insiders.html")), "insiders.html missing");
check(!/STOCK Act|Senate|White House|OGE/i.test(manifest.description), "description still describes Stock Act");

if (failed) {
  console.error(failed + " failed");
  process.exit(1);
}
console.log("sw cache filter ok");
