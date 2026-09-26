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

  var WARN_LABEL = {
    financing: "Financing",
    insider_sale: "Insider sale",
    sell_after_buy: "Sold after buy"
  };

  function warnClass(strength) {
    if (strength === "strong") return "is-strong";
    if (strength === "weak") return "is-weak";
    return "is-mod";
  }

  function warnButton(tag, ticker) {
    var label = WARN_LABEL[tag && tag.t];
    if (!label) return null;
    var n = Number(tag.n);
    if (!isFinite(n) || n < 1) n = 1;
    var filed = tag.d ? dateText(tag.d) : "";
    var btn = document.createElement("button");
    btn.type = "button";
    btn.className = "qc-txn-tag qc-mini-tag qc-warn-chip " + warnClass(tag.s);
    btn.setAttribute("data-warn", ticker);
    btn.setAttribute("data-n", String(n));
    btn.setAttribute("aria-expanded", "false");
    btn.setAttribute("aria-label", label + (filed && filed !== "\u2014" ? ", filed " + filed : ""));
    var text = document.createElement("span");
    text.className = "qc-warn-label";
    text.textContent = label + (n > 1 ? " +" + (n - 1) : "");
    btn.appendChild(text);
    return btn;
  }

  function applyWarnTags(rows, data, root) {
    if (!root) return;
    var tags = (data && data.tags) || null;
    root.querySelectorAll(".qc-warn-chip").forEach(function (el) {
      if (el.parentNode) el.parentNode.removeChild(el);
    });
    if (!tags) return;
    var byId = {};
    (rows || []).forEach(function (t) {
      if (t && t.id != null) byId[String(t.id)] = t;
    });
    root.querySelectorAll(".qc-txn-tape").forEach(function (el) {
      var t = byId[el.getAttribute("data-id") || ""];
      if (!t) return;
      var tk = String(t.ticker || "").toUpperCase();
      var tag = tags[tk];
      if (!tag) return;
      var cell = el.querySelector(".qc-txn-company .qc-txn-tk") || el.querySelector(".qc-txn-tk");
      if (!cell) return;
      var btn = warnButton(tag, tk);
      if (btn) cell.appendChild(btn);
    });
  }

  function warnClipRight(btn) {
    var row = btn.closest ? btn.closest("li") : null;
    var narrow = (global.innerWidth || 0) <= 899;
    if (narrow && row) {
      var limit = null;
      var stops = [row.querySelector(".qc-txn-chip"), row.querySelector(".qc-txn-hero")];
      stops.forEach(function (el) {
        if (!el) return;
        var rect = el.getBoundingClientRect();
        if (rect.width < 1 || rect.height < 1) return;
        var edge = rect.left - 6;
        if (limit == null || edge < limit) limit = edge;
      });
      if (limit != null) return limit;
    }
    var node = btn.parentElement;
    while (node && node !== row) {
      var ox = global.getComputedStyle(node).overflowX;
      if (ox && ox !== "visible") return node.getBoundingClientRect().right;
      node = node.parentElement;
    }
    return btn.parentElement ? btn.parentElement.getBoundingClientRect().right : null;
  }

  function fitWarnChips(root) {
    if (!root || !root.querySelectorAll) return;
    var chips = root.querySelectorAll("button.qc-warn-chip");
    var i;
    for (i = 0; i < chips.length; i++) chips[i].classList.remove("is-dot");
    for (i = 0; i < chips.length; i++) {
      var btn = chips[i];
      var limit = warnClipRight(btn);
      if (limit == null) continue;
      if (btn.getBoundingClientRect().right > limit) btn.classList.add("is-dot");
    }
  }

  function stampTape(rows, root) {
    var token = ++stampToken;
    var list = rows || [];
    var host = root || document.getElementById("rows");
    Promise.all([
      loadJson("insider-basket-tags.json"),
      loadJson("insider-warning-tags.json")
    ]).then(function (pair) {
      if (token !== stampToken) return;
      if (pair[0]) applyTags(list, pair[0], host);
      if (pair[1]) applyWarnTags(list, pair[1], host);
      var fit = function () { fitWarnChips(host); };
      if (global.requestAnimationFrame) global.requestAnimationFrame(fit);
      else fit();
      if (document.fonts && document.fonts.ready) document.fonts.ready.then(fit);
    });
  }

  function tickerList(tickers) {
    var list = [];
    var seen = {};
    (tickers || []).forEach(function (t) {
      var code = String(t || "").trim();
      if (!code || seen[code.toUpperCase()]) return;
      seen[code.toUpperCase()] = true;
      list.push(code);
    });
    return list;
  }

  function mountWarnChip(host, tickers) {
    if (!host) return;
    var list = tickerList(tickers);
    var key = list.join("|");
    if (host.getAttribute("data-key") === key && host.getAttribute("data-set") === "1") return;
    host.setAttribute("data-key", key);
    host.setAttribute("data-set", "1");
    var gen = (host._g = (host._g || 0) + 1);
    host.hidden = true;
    host.innerHTML = "";
    loadJson("insider-warning-tags.json").then(function (data) {
      if (host._g !== gen) return;
      var tags = data && data.tags;
      if (!tags) return;
      var hit = null;
      var code = "";
      list.forEach(function (t) {
        if (hit) return;
        var up = t.toUpperCase();
        if (tags[up]) { hit = tags[up]; code = up; }
        else if (tags[t]) { hit = tags[t]; code = up || t; }
      });
      if (!hit) return;
      var btn = warnButton(hit, code);
      if (!btn) return;
      host.appendChild(btn);
      host.hidden = false;
    });
  }

  function whenIdle(fn) {
    if (typeof global.requestIdleCallback === "function") {
      global.requestIdleCallback(fn, { timeout: 2500 });
    } else {
      global.setTimeout(fn, 400);
    }
  }

  function afterLoadIdle(fn) {
    if (document.readyState === "complete") {
      whenIdle(fn);
      return;
    }
    global.addEventListener("load", function () { whenIdle(fn); }, { once: true });
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

  function filerHasFile(fid) {
    var id = String(fid || "");
    if (!id) return Promise.resolve(false);
    return indexByFiler().then(function (map) {
      return !!(map && map[id]);
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

  function shortBlock(title, items, render, cap) {
    var all = items || [];
    if (!all.length) return "";
    var limit = cap > 0 ? cap : 8;
    var html = "<h3>" + esc(title) + "</h3><ul class=\"qc-flow\">" +
      all.slice(0, limit).map(render).join("") + "</ul>";
    var rest = all.slice(limit);
    if (rest.length) {
      html += "<div data-track-rest hidden><ul class=\"qc-flow\">" + rest.map(render).join("") + "</ul></div>" +
        "<button type=\"button\" class=\"qc-track-more\" data-track-more>Show the rest</button>";
    }
    return html;
  }

  function cappedRows(headHtml, rows, cap) {
    if (!rows || !rows.length) return "";
    var limit = cap > 0 ? cap : rows.length;
    var html = "<div class=\"qc-track-scroll\"><table>" + headHtml + "<tbody>" +
      rows.slice(0, limit).join("") + "</tbody></table></div>";
    var rest = rows.slice(limit);
    if (rest.length) {
      html += "<div data-track-rest hidden><div class=\"qc-track-scroll\"><table>" + headHtml + "<tbody>" +
        rest.join("") + "</tbody></table></div></div>" +
        "<button type=\"button\" class=\"qc-track-more\" data-track-more>Show the rest</button>";
    }
    return html;
  }

  function cappedOl(items, render, cap) {
    if (!items || !items.length) return "";
    var limit = cap > 0 ? cap : items.length;
    var html = "<ol class=\"qc-pos-list\">" + items.slice(0, limit).map(render).join("") + "</ol>";
    var rest = items.slice(limit);
    if (rest.length) {
      html += "<div data-track-rest hidden><ol class=\"qc-pos-list\">" + rest.map(render).join("") + "</ol></div>" +
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
    var buyHead = "<thead><tr><th>Who</th><th>Role</th><th>Date</th><th class=\"num\">Amount</th><th class=\"num\">63d vs peers</th></tr></thead>";
    var buyRows = ((data && data.buys) || []).map(function (b) {
      var who = String((b && b.who) || "");
      var fid = String((b && b.fid) || "");
      var whoHtml = fid
        ? "<a href=\"insider.html?id=" + encodeURIComponent(fid) + "\">" + esc(who || fid) + "</a>"
        : esc(who || "\u2014");
      return "<tr><td>" + whoHtml + "</td><td>" + esc((b && b.role) || "\u2014") + "</td><td>" +
        esc(dateText(b && b.td)) + "</td><td class=\"num\">" + esc(moneyText(b && b.usd)) + "</td>" +
        xpButton(b && b.xp63, b && b.r63) + "</tr>";
    });
    var buys = buyRows.length ? "<h3>Insider buys</h3>" + cappedRows(buyHead, buyRows, 25) : "";
    var flowBlock = shortBlock("Sales and financings", data && data.sales_and_financings, function (s) {
      var words = exitWords(s && s.type);
      var who = String((s && s.who) || "");
      var label = words || "Filing";
      return "<li><span class=\"qc-pos-tk\">" + esc(label) + "</span> <span class=\"qc-pos-meta\">" +
        esc(who) + " \u00b7 " + esc(dateText(s && (s.td || s.fd))) + "</span></li>";
    });
    var paperBlock = shortBlock("Paper positions", data && data.paper_positions, function (p) {
      var tag = (p && p.backfill) ? "<span class=\"qc-txn-tag qc-backfill-tag\">backfill</span>" : "";
      var pending = p && p.status === "pending_entry" ? "<span class=\"qc-txn-tag qc-mini-tag\">Pending entry</span>" : "";
      if (p && p.status === "pending_entry") return "<li>" + tag + pending + "</li>";
      var reason = exitWords(p && p.exit_reason);
      var when = esc(dateText(p && p.entry));
      if (p && p.exit) when += " \u2192 " + esc(dateText(p.exit));
      if (reason) when += " \u00b7 " + esc(reason);
      return "<li><span class=\"qc-pos-meta\">entered " + when + "</span> " + tag + pending + "</li>";
    });
    return head + nameHtml +
      "<p class=\"qc-track-caveat\">" + esc(CAVEAT) + "</p>" +
      countHtml + buys + flowBlock + paperBlock;
  }

  function historyIndex() {
    return loadJson("insider-history/index.json").then(function (idx) {
      var set = {};
      if (!idx || !idx.rows) return set;
      idx.rows.forEach(function (row) {
        if (row && row[0]) set[String(row[0]).toUpperCase()] = true;
      });
      return set;
    });
  }

  function firstHistory(tickers) {
    var list = tickerList(tickers);
    return historyIndex().then(function (set) {
      var listed = list.filter(function (t) { return set[t.toUpperCase()]; });
      var i = 0;
      function next() {
        if (i >= listed.length) return Promise.resolve(null);
        var url = historyUrl(listed[i++]);
        if (!url) return next();
        return loadJson(url).then(function (data) {
          if (data && (data.buys || data.sales_and_financings || data.paper_positions || data.summary)) return data;
          return next();
        });
      }
      return next();
    });
  }

  function sourceHref(w) {
    var url = w && w.source_url ? String(w.source_url) : "";
    if (url) return url;
    var src = String((w && w.source) || "");
    if (src.indexOf("https://") === 0) return src;
    return "";
  }

  function warnEntry(pack, list) {
    var book = pack && pack.tickers;
    if (!book) return null;
    for (var i = 0; i < list.length; i++) {
      var code = String(list[i] || "").toUpperCase();
      if (book[code]) return book[code];
      if (book[list[i]]) return book[list[i]];
    }
    return null;
  }

  function warnItems(items, render, cap) {
    var all = items || [];
    if (!all.length) return "";
    var limit = cap > 0 ? cap : 5;
    var html = "<ul class=\"qc-flow\">" + all.slice(0, limit).map(render).join("") + "</ul>";
    var rest = all.slice(limit);
    if (rest.length) {
      html += "<div data-track-rest hidden><ul class=\"qc-flow\">" + rest.map(render).join("") + "</ul></div>" +
        "<button type=\"button\" class=\"qc-track-more\" data-track-more>Show the rest</button>";
    }
    return html;
  }

  function warningsBlock(pack, entry) {
    var all = (entry && entry.warnings) || [];
    var main = [];
    var info = [];
    all.forEach(function (w) {
      if (!w) return;
      if (w.strength === "info" || w.type === "placement") info.push(w);
      else main.push(w);
    });
    if (!main.length && !info.length) return "";
    var html = "<h3>Warnings (last 180 days)</h3>";
    if (pack && pack.disclaimer) html += "<p class=\"qc-track-note\">" + esc(pack.disclaimer) + "</p>";
    html += warnItems(main, function (w) {
      var label = WARN_LABEL[w.type] || "";
      var chip = label
        ? "<span class=\"qc-txn-tag qc-mini-tag qc-warn-chip " + warnClass(w.strength) + "\">" + esc(label) + "</span> "
        : "";
      var href = sourceHref(w);
      var link = "";
      if (href) {
        var text = w.source && String(w.source).indexOf("https://") !== 0 ? String(w.source) : "Source";
        link = "<p class=\"qc-pos-meta\"><a href=\"" + esc(href) + "\" rel=\"noopener\">" + esc(text) + "</a></p>";
      }
      return "<li class=\"qc-warn-item\">" + chip +
        "<span class=\"qc-pos-meta\">" + esc(dateText(w.date)) + "</span>" +
        (w.detail ? "<p class=\"qc-pos-meta\">" + esc(w.detail) + "</p>" : "") +
        (w.explain ? "<p class=\"qc-warn-explain\">" + esc(w.explain) + "</p>" : "") +
        link + "</li>";
    }, 5);
    if (info.length) {
      html += "<p class=\"qc-track-hint\">For information</p>" + warnItems(info, function (w) {
        return "<li class=\"qc-warn-item\">" +
          (w.detail ? "<p class=\"qc-pos-meta\">" + esc(w.detail) + "</p>" : "") +
          (w.explain ? "<p class=\"qc-warn-explain\">" + esc(w.explain) + "</p>" : "") +
          "</li>";
      }, 5);
    }
    return html;
  }

  function idleJson(url) {
    return new Promise(function (resolve) {
      afterLoadIdle(function () { loadJson(url).then(resolve); });
    });
  }

  function companyHasSignal(list) {
    return Promise.all([
      historyIndex(),
      loadJson("insider-warning-tags.json")
    ]).then(function (pair) {
      var listed = pair[0] || {};
      var tags = (pair[1] && pair[1].tags) || {};
      for (var i = 0; i < list.length; i++) {
        var code = String(list[i] || "").toUpperCase();
        if (listed[code] || tags[code]) return true;
      }
      return false;
    });
  }

  function fillCompany(el, list, gen, heading) {
    var body = el.querySelector("[data-track-body]");
    var target = body || el;
    return Promise.all([
      firstHistory(list),
      idleJson("insider-warnings.json")
    ]).then(function (pair) {
      if (el._g !== gen) return;
      var hist = pair[0];
      var entry = warnEntry(pair[1], list);
      if (!hist && !entry) {
        target.innerHTML = "";
        el.hidden = true;
        return;
      }
      var html = heading ? "<h2>Insider activity</h2>" : "";
      if (entry) html += warningsBlock(pair[1], entry);
      if (hist) html += companyHtml(hist, { heading: false });
      target.innerHTML = html;
      el.hidden = false;
      el.setAttribute("data-filled", el.getAttribute("data-key") || "");
    });
  }

  function mountCompany(el, tickers, opts) {
    if (!el) return;
    opts = opts || {};
    var list = tickerList(tickers);
    var key = list.join("|");
    if (el.getAttribute("data-key") === key) return;
    el.setAttribute("data-key", key);
    el.removeAttribute("data-filled");
    var gen = (el._g = (el._g || 0) + 1);
    var body = el.querySelector("[data-track-body]");
    var target = body || el;
    el.hidden = true;
    target.innerHTML = "";
    var heading = el.tagName === "DETAILS" ? false : opts.heading !== false;
    if (el.tagName === "DETAILS") {
      if (!el._warnBound) {
        el._warnBound = true;
        el.addEventListener("toggle", function () {
          if (!el.open) return;
          if (el.getAttribute("data-filled") === el.getAttribute("data-key")) return;
          fillCompany(el, tickerList((el.getAttribute("data-key") || "").split("|")), el._g, false);
        });
      }
      companyHasSignal(list).then(function (show) {
        if (el._g !== gen) return;
        el.hidden = !show;
        if (show && el.open) fillCompany(el, list, gen, false);
      });
      return;
    }
    fillCompany(el, list, gen, heading);
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
    if (!row || row.status !== "pending_entry") meta.push("entered " + esc(dateText(row && row.entry_date)));
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
      ? cappedOl(open, posHtml, 25)
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
      var notes = data.research_notes || [];
      if (notes.length) {
        backHtml += "<p class=\"qc-track-line\">Research notes</p><ul class=\"qc-limits\">" +
          notes.map(function (line) { return "<li>" + esc(line) + "</li>"; }).join("") + "</ul>";
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

  var warnPop = null;
  var warnHideTimer = 0;

  function ensureWarnPop() {
    if (warnPop) return warnPop;
    warnPop = document.createElement("div");
    warnPop.className = "qc-warn-pop";
    warnPop.hidden = true;
    warnPop.setAttribute("role", "tooltip");
    document.body.appendChild(warnPop);
    warnPop.addEventListener("mouseenter", function () { warnPop._hold = true; });
    warnPop.addEventListener("mouseleave", function () {
      warnPop._hold = false;
      scheduleWarnHide();
    });
    return warnPop;
  }

  function placeWarnPop(btn) {
    var pop = ensureWarnPop();
    var rect = btn.getBoundingClientRect();
    var width = Math.min(352, global.innerWidth - 16);
    var left = Math.max(8, Math.min(rect.left, global.innerWidth - width - 8));
    pop.style.width = width + "px";
    pop.style.left = left + "px";
    pop.style.top = Math.max(8, rect.bottom + 6) + "px";
  }

  function hideWarnPop() {
    if (!warnPop) return;
    warnPop.hidden = true;
    warnPop._hold = false;
    if (warnPop._btn) {
      warnPop._btn.setAttribute("aria-expanded", "false");
      warnPop._btn = null;
    }
  }

  function scheduleWarnHide() {
    global.clearTimeout(warnHideTimer);
    warnHideTimer = global.setTimeout(function () {
      if (warnPop && warnPop._hold) return;
      hideWarnPop();
    }, 160);
  }

  function warnNarrow() {
    return global.matchMedia && global.matchMedia("(max-width: 899px)").matches;
  }

  function fillWarnPop(pop, btn, data) {
    var ticker = btn.getAttribute("data-warn") || "";
    var entry = data && data.tickers && (data.tickers[ticker] || data.tickers[ticker.toUpperCase()]);
    var list = (entry && entry.warnings) || [];
    var want = entry && entry.strongest;
    var pick = null;
    for (var i = 0; i < list.length; i++) {
      if (!list[i] || list[i].strength === "info" || list[i].type === "placement") continue;
      if (!pick) pick = list[i];
      if (want && list[i].strength === want) { pick = list[i]; break; }
    }
    if (!pick) {
      hideWarnPop();
      return;
    }
    pop.innerHTML = "";
    var explain = document.createElement("p");
    explain.className = "qc-warn-explain";
    explain.textContent = pick.explain || pick.detail || "";
    pop.appendChild(explain);
    var filed = document.createElement("p");
    filed.className = "qc-pos-meta";
    filed.textContent = "Filed " + dateText(pick.date);
    pop.appendChild(filed);
    var href = sourceHref(pick);
    if (href) {
      var link = document.createElement("a");
      link.href = href;
      link.rel = "noopener";
      link.textContent = pick.source && String(pick.source).indexOf("https://") !== 0 ? String(pick.source) : "Source";
      var line = document.createElement("p");
      line.className = "qc-pos-meta";
      line.appendChild(link);
      pop.appendChild(line);
    }
    var extra = (Number(btn.getAttribute("data-n")) || 1) - 1;
    if (extra > 0) {
      var more = document.createElement("a");
      more.href = "insider-ticker.html?t=" + encodeURIComponent(ticker);
      more.textContent = extra + " more in the company panel";
      var moreLine = document.createElement("p");
      moreLine.className = "qc-pos-meta";
      moreLine.appendChild(more);
      pop.appendChild(moreLine);
    }
  }

  function showWarnPop(btn) {
    var pop = ensureWarnPop();
    global.clearTimeout(warnHideTimer);
    if (pop._btn && pop._btn !== btn) pop._btn.setAttribute("aria-expanded", "false");
    pop._btn = btn;
    pop._hold = false;
    btn.setAttribute("aria-expanded", "true");
    pop.hidden = false;
    pop.innerHTML = "<p class=\"qc-warn-explain\">Loading\u2026</p>";
    placeWarnPop(btn);
    var gen = (pop._g = (pop._g || 0) + 1);
    loadJson("insider-warnings.json").then(function (data) {
      if (!pop || pop._g !== gen || pop._btn !== btn) return;
      if (!data) {
        hideWarnPop();
        return;
      }
      fillWarnPop(pop, btn, data);
      if (!pop.hidden) placeWarnPop(btn);
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
    var warn = ev.target.closest && ev.target.closest("button.qc-warn-chip");
    if (warn) {
      ev.preventDefault();
      ev.stopPropagation();
      if (warnNarrow()) {
        if (warnPop && !warnPop.hidden && warnPop._btn === warn) hideWarnPop();
        else showWarnPop(warn);
      } else {
        showWarnPop(warn);
      }
      return;
    }
    if (warnPop && !warnPop.hidden && !(ev.target.closest && ev.target.closest(".qc-warn-pop"))) hideWarnPop();
    var btn = ev.target.closest && ev.target.closest("button.qc-xp");
    if (!btn) return;
    if (global.matchMedia && global.matchMedia("(min-width: 900px)").matches) return;
    var open = btn.getAttribute("aria-expanded") === "true";
    btn.setAttribute("aria-expanded", open ? "false" : "true");
  });

  document.addEventListener("mouseover", function (ev) {
    if (warnNarrow()) return;
    var warn = ev.target.closest && ev.target.closest("button.qc-warn-chip");
    if (!warn) return;
    showWarnPop(warn);
  });
  document.addEventListener("mouseout", function (ev) {
    if (warnNarrow()) return;
    var warn = ev.target.closest && ev.target.closest("button.qc-warn-chip");
    if (!warn) return;
    var next = ev.relatedTarget;
    if (next && warnPop && warnPop.contains(next)) return;
    scheduleWarnHide();
  });
  document.addEventListener("focusin", function (ev) {
    var warn = ev.target.closest && ev.target.closest("button.qc-warn-chip");
    if (warn && !warnNarrow()) showWarnPop(warn);
  });
  document.addEventListener("focusout", function (ev) {
    var warn = ev.target.closest && ev.target.closest("button.qc-warn-chip");
    if (!warn || warnNarrow()) return;
    var next = ev.relatedTarget;
    if (next && warnPop && warnPop.contains(next)) return;
    scheduleWarnHide();
  });

  if (global.addEventListener) {
    global.addEventListener("resize", function () {
      var rows = document.getElementById("rows");
      if (rows) fitWarnChips(rows);
      if (warnPop && !warnPop.hidden && warnPop._btn) placeWarnPop(warnPop._btn);
    });
  }

  global.QCTrack = {
    stampTape: stampTape,
    mountPerson: mountPerson,
    mountCompany: mountCompany,
    mountBasket: mountBasket,
    mountWarnChip: mountWarnChip,
    filerHasFile: filerHasFile,
    whenIdle: whenIdle
  };
})(window);
