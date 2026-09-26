/* Lazy insider track, company history, and paper-basket panels.
   Shards stay out of first paint except the tape tag file, which is
   requested after rows render. A missing file leaves the panel hidden. */
(function (global) {
  var QC = global.QC;
  var CAVEAT = "How these buys did afterwards, compared with similar companies. In our research, an insider's past results did not predict their next buys.";
  var mem = {};
  var stampToken = 0;

  function esc(s) {
    return QC.esc(s);
  }

  function loadJson(url) {
    if (Object.prototype.hasOwnProperty.call(mem, url)) return mem[url];
    mem[url] = fetch(url).then(function (r) {
      if (!r || !r.ok) return null;
      return r.json().catch(function () { return null; });
    }).catch(function () { return null; });
    return mem[url];
  }

  function pctParts(v) {
    if (v == null || v === "") return null;
    var n = Number(v);
    if (!isFinite(n)) return null;
    var pct = n * 100;
    if (Math.abs(pct) < 0.05) return { text: "0.0%", cls: "" };
    var abs = Math.abs(pct);
    var digits = abs >= 100 ? 0 : 1;
    var sign = pct > 0 ? "+" : "\u2212";
    return { text: sign + abs.toFixed(digits) + "%", cls: pct > 0 ? "up" : "down" };
  }

  function pctText(v) {
    var p = pctParts(v);
    return p ? p.text : "\u2014";
  }

  function hitText(v) {
    if (v == null || v === "" || !isFinite(Number(v))) return "\u2014";
    return Math.round(Number(v) * 100) + "%";
  }

  function moneyText(n) {
    if (n == null || n === "" || !isFinite(Number(n)) || Number(n) <= 0) return "\u2014";
    return QC.formatMoney(Number(n));
  }

  function signedDollars(n) {
    if (n == null || n === "" || !isFinite(Number(n))) return "\u2014";
    return QC.signedMoney(Number(n));
  }

  function dateText(iso) {
    if (!iso) return "\u2014";
    return QC.prettyDate(String(iso).slice(0, 10));
  }

  function daysText(n) {
    if (n == null || n === "" || !isFinite(Number(n))) return "";
    var d = Math.round(Number(n));
    return d + (d === 1 ? " day held" : " days held");
  }

  function exitWords(reason) {
    if (reason === "sale") return "insider sale";
    if (reason === "financing") return "financing";
    if (reason === "252d") return "252-day limit";
    return "";
  }

  function trackUrl(file) {
    var raw = String(file || "");
    if (!raw || raw.indexOf("..") >= 0 || raw.charAt(0) === "/" || raw.indexOf("\\") >= 0) return "";
    var parts = raw.split("/").filter(Boolean);
    if (!parts.length) return "";
    return "insider-track/" + parts.map(function (p) { return encodeURIComponent(p); }).join("/");
  }

  function historyUrl(ticker) {
    var t = String(ticker || "").trim();
    if (!t || t.indexOf("/") >= 0 || t.indexOf("\\") >= 0) return "";
    return "insider-history/" + encodeURIComponent(t) + ".json";
  }

  function xpButton(vs, raw) {
    var peer = pctParts(vs);
    if (!peer) return "<td class=\"num\">\u2014</td>";
    var rawP = pctParts(raw);
    var tip = rawP ? ("Raw return " + rawP.text) : "Raw return \u2014";
    return "<td class=\"num\"><button type=\"button\" class=\"qc-xp " + peer.cls + "\" title=\"" + esc(tip) + "\" aria-expanded=\"false\">" +
      esc(peer.text) + "<span class=\"qc-raw\">" + esc(tip) + "</span></button></td>";
  }

  function tagMap(tags) {
    var out = {};
    if (!tags) return out;
    Object.keys(tags).forEach(function (key) {
      var parts = String(key).split("|");
      if (parts.length < 3) return;
      var id = parts[0];
      var tk = String(parts[1] || "").toUpperCase();
      var td = parts.slice(2).join("|").slice(0, 10);
      if (!id || !tk || !td) return;
      out[id + "|" + tk + "|" + td] = tags[key];
    });
    return out;
  }

  function tradeKey(t) {
    var id = String((t && t.filer_id) || "");
    var tk = String((t && t.ticker) || "").toUpperCase();
    var td = String((t && t.trade_date) || "").slice(0, 10);
    if (!id || !tk || !td) return "";
    return id + "|" + tk + "|" + td;
  }

  function applyTags(rows, data, root) {
    if (!root) return;
    var map = tagMap(data && data.tags);
    var byId = {};
    (rows || []).forEach(function (t) {
      if (t && t.id != null) byId[String(t.id)] = t;
    });
    root.querySelectorAll(".qc-txn-tape").forEach(function (el) {
      var old = el.querySelector(".qc-basket-chip");
      if (old) old.parentNode.removeChild(old);
      var t = byId[el.getAttribute("data-id") || ""];
      if (!t || !QC.isMarketBuy(t)) return;
      var tag = map[tradeKey(t)];
      if (tag !== "basket" && tag !== "exited") return;
      var tk = el.querySelector(".qc-txn-company .qc-txn-tk") || el.querySelector(".qc-txn-tk");
      if (!tk) return;
      var chip = document.createElement("span");
      chip.className = "qc-txn-tag qc-basket-chip" + (tag === "exited" ? " is-exit" : "");
      chip.textContent = tag === "exited" ? "Exited" : "Basket";
      tk.appendChild(chip);
    });
  }

  function stampTape(rows, root) {
    var token = ++stampToken;
    var list = rows || [];
    var host = root || document.getElementById("rows");
    loadJson("insider-basket-tags.json").then(function (data) {
      if (token !== stampToken) return;
      if (!data) return;
      applyTags(list, data, host);
    });
  }

  function indexByFiler() {
    return loadJson("insider-track/index.json").then(function (idx) {
      if (!idx || !idx.rows) return null;
      var map = {};
      idx.rows.forEach(function (row) {
        if (row && row[0] && row[2]) map[String(row[0])] = row[2];
      });
      return map;
    });
  }

  function personHeadline(summary) {
    var n = summary && summary.n != null ? Number(summary.n) : 0;
    if (!isFinite(n) || n < 0) n = 0;
    var h = (summary && summary.h63) || {};
    var measured = Number(h.n);
    var few = !(measured >= 3);
    var hit = few ? "\u2014" : hitText(h.hit_rate_vs_peer);
    var avg = few ? "\u2014" : pctText(h.shrunk_mean_vs_peer);
    var count = n + " insider " + (n === 1 ? "buy" : "buys");
    return count + " \u00b7 hit rate vs peers " + hit + " at 63 trading days \u00b7 avg vs peers (adjusted for few buys) " + avg;
  }

  function personHtml(data, opts) {
    opts = opts || {};
    var buys = (data && data.buys) || [];
    var head = opts.heading === false ? "" : "<h2>Past buys</h2>";
    var rows = buys.map(function (b) {
      var tk = String((b && b.t) || "");
      var co = String((b && b.co) || "");
      var href = tk ? ("insider-ticker.html?t=" + encodeURIComponent(tk)) : "";
      var tkHtml = href
        ? "<a href=\"" + esc(href) + "\">" + esc(tk) + "</a>"
        : esc(tk || "\u2014");
      var coHtml = co ? "<span class=\"qc-co\">" + esc(co) + "</span>" : "";
      return "<tr><td class=\"tk\">" + tkHtml + coHtml + "</td><td>" + esc(dateText(b && b.td)) + "</td><td class=\"num\">" +
        esc(moneyText(b && b.usd)) + "</td>" +
        xpButton(b && b.xp21, b && b.r21) +
        xpButton(b && b.xp63, b && b.r63) +
        xpButton(b && b.xp126, b && b.r126) +
        xpButton(b && b.xp252, b && b.r252) + "</tr>";
    }).join("");
    var table = rows
      ? "<div class=\"qc-track-scroll\"><table><thead><tr><th>Ticker</th><th>Trade date</th><th class=\"num\">Amount</th><th class=\"num\">21d</th><th class=\"num\">63d</th><th class=\"num\">126d</th><th class=\"num\">252d</th></tr></thead><tbody>" +
        rows + "</tbody></table></div><p class=\"qc-track-hint\">21d / 63d / 126d / 252d are vs peers. A dash is not measurable yet.</p>"
      : "";
    return head +
      "<p class=\"qc-track-caveat\">" + esc(CAVEAT) + "</p>" +
      "<p class=\"qc-track-line\">" + esc(personHeadline(data && data.summary)) + "</p>" +
      table;
  }

  function mountPerson(el, filerId, opts) {
    if (!el) return;
    opts = opts || {};
    var gen = (el._g = (el._g || 0) + 1);
    var fid = String(filerId || "");
    if (el.tagName !== "DETAILS") el.hidden = true;
    el.innerHTML = "";
    if (!fid) {
      if (opts.onEmpty) opts.onEmpty();
      return;
    }
    indexByFiler().then(function (map) {
      if (!el._g || el._g !== gen) return null;
      var file = map && map[fid];
      var url = trackUrl(file);
      if (!url) return null;
      return loadJson(url);
    }).then(function (data) {
      if (el._g !== gen) return;
      var has = data && (data.summary || (data.buys && data.buys.length));
      if (!has) {
        if (opts.onEmpty) opts.onEmpty();
        return;
      }
      el.innerHTML = personHtml(data, opts);
      if (el.tagName !== "DETAILS") el.hidden = false;
    }).catch(function () {
      if (el._g === gen && opts.onEmpty) opts.onEmpty();
    });
  }

  function shortBlock(title, items, render) {
    var all = items || [];
    if (!all.length) return "";
    var cap = 8;
    var html = "<h3>" + esc(title) + "</h3><ul class=\"qc-flow\">" +
      all.slice(0, cap).map(render).join("") + "</ul>";
    var rest = all.slice(cap);
    if (rest.length) {
      html += "<div data-track-rest hidden><ul class=\"qc-flow\">" + rest.map(render).join("") + "</ul></div>" +
        "<button type=\"button\" class=\"qc-track-more\" data-track-more>Show the rest</button>";
    }
    return html;
  }

  function companyHtml(data, opts) {
    opts = opts || {};
    var head = opts.heading === false ? "" : "<h2>Insider activity</h2>";
    var name = String((data && data.company) || "");
    var nameHtml = name ? "<p class=\"qc-track-co\">" + esc(name) + "</p>" : "";
    var n = data && data.summary && data.summary.n != null ? Number(data.summary.n) : ((data && data.buys) || []).length;
    var count = isFinite(n) ? (n + " insider " + (n === 1 ? "buy" : "buys")) : "";
    var countHtml = count ? "<p class=\"qc-track-line\">" + esc(count) + "</p>" : "";
    var buyRows = ((data && data.buys) || []).map(function (b) {
      var who = String((b && b.who) || "");
      var fid = String((b && b.fid) || "");
      var whoHtml = fid
        ? "<a href=\"insider.html?id=" + encodeURIComponent(fid) + "\">" + esc(who || fid) + "</a>"
        : esc(who || "\u2014");
      return "<tr><td>" + whoHtml + "</td><td>" + esc((b && b.role) || "\u2014") + "</td><td>" +
        esc(dateText(b && b.td)) + "</td><td class=\"num\">" + esc(moneyText(b && b.usd)) + "</td>" +
        xpButton(b && b.xp63, b && b.r63) + "</tr>";
    }).join("");
    var buys = buyRows
      ? "<h3>Insider buys</h3><div class=\"qc-track-scroll\"><table><thead><tr><th>Who</th><th>Role</th><th>Date</th><th class=\"num\">Amount</th><th class=\"num\">63d vs peers</th></tr></thead><tbody>" +
        buyRows + "</tbody></table></div>"
      : "";
    var flowBlock = shortBlock("Sales and financings", data && data.sales_and_financings, function (s) {
      var words = exitWords(s && s.type);
      var who = String((s && s.who) || "");
      var label = words || "Filing";
      return "<li><span class=\"qc-pos-tk\">" + esc(label) + "</span> <span class=\"qc-pos-meta\">" +
        esc(who) + " \u00b7 " + esc(dateText(s && (s.td || s.fd))) + "</span></li>";
    });
    var paperBlock = shortBlock("Paper positions", data && data.paper_positions, function (p) {
      var reason = exitWords(p && p.exit_reason);
      var when = esc(dateText(p && p.entry));
      if (p && p.exit) when += " \u2192 " + esc(dateText(p.exit));
      if (reason) when += " \u00b7 " + esc(reason);
      var tag = (p && p.backfill) ? "<span class=\"qc-txn-tag qc-backfill-tag\">backfill</span>" : "";
      var pending = p && p.status === "pending_entry" ? "<span class=\"qc-txn-tag qc-mini-tag\">Pending entry</span>" : "";
      return "<li><span class=\"qc-pos-meta\">entered " + when + "</span> " + tag + pending + "</li>";
    });
    return head + nameHtml +
      "<p class=\"qc-track-caveat\">" + esc(CAVEAT) + "</p>" +
      countHtml + buys + flowBlock + paperBlock;
  }

  function firstHistory(tickers) {
    var list = [];
    var seen = {};
    (tickers || []).forEach(function (t) {
      var code = String(t || "").trim();
      if (!code || seen[code]) return;
      seen[code] = true;
      list.push(code);
    });
    var i = 0;
    function next() {
      if (i >= list.length) return Promise.resolve(null);
      var url = historyUrl(list[i++]);
      if (!url) return next();
      return loadJson(url).then(function (data) {
        if (data && (data.buys || data.sales_and_financings || data.paper_positions || data.summary)) return data;
        return next();
      });
    }
    return next();
  }

  function mountCompany(el, tickers, opts) {
    if (!el) return;
    opts = opts || {};
    var list = [];
    var seen = {};
    (tickers || []).forEach(function (t) {
      var code = String(t || "").trim();
      if (!code || seen[code]) return;
      seen[code] = true;
      list.push(code);
    });
    var key = list.join("|");
    if (el.getAttribute("data-key") === key) return;
    el.setAttribute("data-key", key);
    var gen = (el._g = (el._g || 0) + 1);
    var body = el.querySelector("[data-track-body]");
    var target = body || el;
    el.hidden = true;
    target.innerHTML = "";
    firstHistory(list).then(function (data) {
      if (el._g !== gen) return;
      if (!data) return;
      var heading = el.tagName === "DETAILS" ? false : opts.heading !== false;
      target.innerHTML = companyHtml(data, { heading: heading });
      el.hidden = false;
    });
  }

  function stat(label, value) {
    return "<div><dt>" + esc(label) + "</dt><dd>" + esc(value) + "</dd></div>";
  }

  function recordStats(block, withReturns) {
    block = block || {};
    var stats = block.closed_stats || {};
    var pl = block.paper_pl || {};
    var html = stat("Positions", block.count != null ? String(block.count) : "\u2014") +
      stat("Open", block.open != null ? String(block.open) : "\u2014") +
      stat("Closed", block.closed != null ? String(block.closed) : "\u2014");
    if (block.pending_entry) html += stat("Pending entry", String(block.pending_entry));
    if (withReturns) {
      html += stat("Average return", pctText(stats.mean_ret)) +
        stat("Median return", pctText(stats.median_ret)) +
        stat("Average vs peers", pctText(stats.mean_vs_peer)) +
        stat("Paper P/L", signedDollars(pl.pl_usd)) +
        stat("Paper P/L vs peers", signedDollars(pl.pl_usd_vs_peer));
    }
    return "<dl class=\"qc-stats\">" + html + "</dl>";
  }

  function rowTags(row) {
    var tags = [];
    if (row && row.backfill) tags.push("backfill");
    if (row && row.tier_a) tags.push("At/below insider price");
    if (row && row.caution) tags.push("Recent financing");
    if (row && row.exit_pending) tags.push("Exit pending");
    if (row && row.price_stale) tags.push("Stale price");
    if (row && row.status === "pending_entry") tags.push("Pending entry");
    if (!tags.length) return "";
    return "<p class=\"qc-pos-tags\">" + tags.map(function (label) {
      var cls = label === "backfill" ? "qc-txn-tag qc-backfill-tag" : "qc-txn-tag qc-mini-tag";
      return "<span class=\"" + cls + "\">" + esc(label) + "</span>";
    }).join("") + "</p>";
  }

  function firstRole(row) {
    var ins = row && row.insiders;
    if (ins && ins.length && ins[0] && ins[0].role) return String(ins[0].role);
    return row && row.role ? String(row.role) : "";
  }

  function posHtml(row) {
    var tk = String((row && row.ticker) || "\u2014");
    var co = String((row && row.company) || "");
    var href = tk && tk !== "\u2014" ? ("insider-ticker.html?t=" + encodeURIComponent(tk)) : "";
    var tkHtml = href ? "<a href=\"" + esc(href) + "\">" + esc(tk) + "</a>" : esc(tk);
    var coHtml = co ? "<span class=\"qc-co\">" + esc(co) + "</span>" : "";
    var ret = pctParts(row && row.ret);
    var vs = pctParts(row && row.ret_vs_peer);
    var retHtml = ret ? "<span class=\"" + ret.cls + "\">" + esc(ret.text) + "</span>" : "\u2014";
    var vsHtml = vs ? "<span class=\"" + vs.cls + "\">" + esc(vs.text) + "</span>" : "\u2014";
    var role = firstRole(row);
    var meta = [];
    if (role) meta.push(esc(role));
    meta.push("entered " + esc(dateText(row && row.entry_date)));
    var held = daysText(row && row.days_held);
    if (held) meta.push(esc(held));
    return "<li class=\"qc-pos\"><div class=\"qc-pos-main\"><span class=\"qc-pos-tk\">" + tkHtml + coHtml + "</span>" +
      "<span class=\"qc-pos-nums\">" + retHtml + " <span class=\"qc-vs\">vs peers " + vsHtml + "</span></span></div>" +
      "<p class=\"qc-pos-meta\">" + meta.join(" \u00b7 ") + "</p>" + rowTags(row) + "</li>";
  }

  function exitHtml(row) {
    var tk = String((row && row.ticker) || "\u2014");
    var co = String((row && row.company) || "");
    var href = tk && tk !== "\u2014" ? ("insider-ticker.html?t=" + encodeURIComponent(tk)) : "";
    var tkHtml = href ? "<a href=\"" + esc(href) + "\">" + esc(tk) + "</a>" : esc(tk);
    var coHtml = co ? "<span class=\"qc-co\">" + esc(co) + "</span>" : "";
    var ret = pctParts(row && row.ret);
    var vs = pctParts(row && row.ret_vs_peer);
    var retHtml = ret ? "<span class=\"" + ret.cls + "\">" + esc(ret.text) + "</span>" : "\u2014";
    var vsHtml = vs ? "<span class=\"" + vs.cls + "\">" + esc(vs.text) + "</span>" : "\u2014";
    var reason = exitWords(row && row.exit_reason);
    var span = esc(dateText(row && row.entry_date)) + " \u2192 " + esc(dateText(row && row.exit_date));
    if (reason) span += " \u00b7 " + esc(reason);
    return "<li class=\"qc-pos\"><div class=\"qc-pos-main\"><span class=\"qc-pos-tk\">" + tkHtml + coHtml + "</span>" +
      "<span class=\"qc-pos-nums\">" + retHtml + " <span class=\"qc-vs\">vs peers " + vsHtml + "</span></span></div>" +
      "<p class=\"qc-pos-meta\">" + span + "</p>" + rowTags(row) + "</li>";
  }

  function regimeLabel(regime) {
    if (!regime) return "";
    var stress = regime.stress === true || String(regime.label || "").toUpperCase() === "STRESS";
    return stress ? "Market: stressed" : "Market: normal";
  }

  function renderBasket(el, data) {
    el.innerHTML = "";
    if (data.disclaimer) {
      var disc = document.createElement("p");
      disc.className = "qc-track-note";
      disc.textContent = String(data.disclaimer);
      el.appendChild(disc);
    }
    var meta = document.createElement("p");
    meta.className = "qc-track-line";
    var bits = [];
    if (data.price_asof) bits.push("Prices as of " + dateText(data.price_asof));
    meta.textContent = bits.join(" ");
    var chip = document.createElement("span");
    var regime = regimeLabel(data.regime);
    if (regime) {
      chip.className = "qc-txn-tag qc-mini-tag" + (regime === "Market: stressed" ? " hot" : "");
      chip.textContent = regime;
      meta.appendChild(document.createTextNode(" "));
      meta.appendChild(chip);
    }
    el.appendChild(meta);

    var live = (data.ledger_stats && data.ledger_stats.live) || {};
    var since = String(data.live_start || "2026-09-26");
    var liveWrap = document.createElement("div");
    liveWrap.className = "qc-live";
    var closedN = Number(live.closed) || 0;
    var liveHtml = "<h3>Live record since " + esc(since) + "</h3>";
    if (closedN <= 0) {
      liveHtml += "<p class=\"qc-track-line\">Live record started 2026-09-26 \u2014 no closed positions yet.</p>";
      liveHtml += recordStats(live, false);
    } else {
      liveHtml += "<p class=\"qc-track-hint\">US$1,000 per position. Live figures only.</p>";
      liveHtml += recordStats(live, true);
    }
    liveWrap.innerHTML = liveHtml;
    el.appendChild(liveWrap);

    var open = data.basket || [];
    var openWrap = document.createElement("div");
    openWrap.innerHTML = "<h3>Open positions</h3>" + (open.length
      ? "<ol class=\"qc-pos-list\">" + open.map(posHtml).join("") + "</ol>"
      : "<p class=\"qc-track-line\">No open positions.</p>");
    el.appendChild(openWrap);

    var exits = data.exits || [];
    var exitWrap = document.createElement("div");
    exitWrap.innerHTML = "<h3>Recent exits</h3>" + (exits.length
      ? "<ol class=\"qc-pos-list\">" + exits.map(exitHtml).join("") + "</ol>"
      : "<p class=\"qc-track-line\">No recent exits.</p>");
    el.appendChild(exitWrap);

    var back = (data.ledger_stats && data.ledger_stats.backfill) || null;
    var limits = data.limitations || [];
    if (back || limits.length) {
      var fold = document.createElement("details");
      fold.className = "qc-track-fold";
      var summary = document.createElement("summary");
      summary.textContent = "Backfill (in-sample, not a track record)";
      fold.appendChild(summary);
      var inner = document.createElement("div");
      inner.className = "qc-track-body";
      var backHtml = "<p class=\"qc-track-hint\">Backfill only. Not added to the live record.</p>";
      if (back) {
        var backClosed = Number(back.closed) || 0;
        backHtml += recordStats(back, backClosed > 0);
      }
      if (limits.length) {
        backHtml += "<p class=\"qc-track-line\">Limitations</p><ul class=\"qc-limits\">" +
          limits.map(function (line) { return "<li>" + esc(line) + "</li>"; }).join("") + "</ul>";
      }
      inner.innerHTML = backHtml;
      fold.appendChild(inner);
      el.appendChild(fold);
    }
  }

  var basketGen = 0;
  function mountBasket(el) {
    if (!el) return;
    if (el.getAttribute("data-ready") === "1") return;
    var gen = ++basketGen;
    el.innerHTML = "";
    loadJson("insider-signals.json").then(function (data) {
      if (gen !== basketGen) return;
      el.setAttribute("data-ready", "1");
      if (!data) return;
      renderBasket(el, data);
    });
  }

  document.addEventListener("click", function (ev) {
    var more = ev.target.closest && ev.target.closest("[data-track-more]");
    if (more) {
      var box = more.previousElementSibling;
      if (!box || !box.hasAttribute("data-track-rest")) return;
      var opening = box.hasAttribute("hidden");
      if (opening) box.removeAttribute("hidden");
      else box.setAttribute("hidden", "");
      more.textContent = opening ? "Show fewer" : "Show the rest";
      return;
    }
    var btn = ev.target.closest && ev.target.closest("button.qc-xp");
    if (!btn) return;
    if (global.matchMedia && global.matchMedia("(min-width: 900px)").matches) return;
    var open = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", open ? "false" : "true");
  });

  global.QCTrack = {
    stampTape: stampTape,
    mountPerson: mountPerson,
    mountCompany: mountCompany,
    mountBasket: mountBasket
  };
})(window);
