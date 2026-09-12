/* Shared tape helpers for Quantity Capital pages. */
(function (global) {
  const BAD_TICKERS = { LLC: 1, THE: 1, AND: 1, INC: 1, CORP: 1, CLASS: 1, NONE: 1, NA: 1, CMN: 1, COM: 1, NPV: 1, ETF: 1, FUND: 1 };

  function esc(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function isBond(t) {
    const type = (t.asset_type || "").toLowerCase();
    const asset = (t.asset || "").toLowerCase();
    return type.includes("bond") || type.includes("municipal") || /rate\/coupon/.test(asset);
  }

  function isOptionLike(t) {
    const type = (t.asset_type || "").toLowerCase();
    const asset = (t.asset || "").toLowerCase();
    return type.includes("option") || /exercised|call option|put option|strike pric|flex euro|\bcall\/|\bput\/|@\s*\d/.test(asset);
  }

  function optionMeta(t) {
    const a = String(t.asset || "");
    let kind = "";
    if (/\bput\b|\bput\/|>ut\//i.test(a)) kind = "Put";
    else if (/\bcall\b|\bcall\/|sall\/|tall\/|=all\//i.test(a)) kind = "Call";
    const strike = (a.match(/strike\s*price:\s*\$?([\d,.]+)/i) || a.match(/@\s*\$?([\d,.]+)/) || [])[1];
    const exp = (a.match(/expires?:\s*([\d./-]+)/i) || a.match(/exp(?:ires)?\s*([\d./-]+)/i) || [])[1];
    let under = (t.ticker || "").toUpperCase();
    const m = a.match(/\b(?:call|put|sall|tall|=all|>ut)\s*\/\s*([A-Z]{1,5})\b/i);
    if (m) {
      under = m[1].toUpperCase();
      if (under === "INJ") under = "JNJ";
    }
    return { kind: kind, strike: strike || "", exp: exp || "", under: under };
  }

  function optionTag(t) {
    if (!isOptionLike(t)) return "";
    const o = optionMeta(t);
    const bits = [o.kind || "Option"];
    if (o.strike) bits.push("$" + o.strike.replace(/\.00$/, ""));
    if (o.exp) bits.push(o.exp);
    return "<span class=\"opt-tag\">" + bits.join(" ") + "</span>";
  }

  function isEtfLike(t) {
    const type = (t.asset_type || "").toLowerCase();
    const asset = (t.asset || "").toLowerCase();
    const ticker = (t.ticker || t.code || "").toUpperCase();
    if (type.includes("etf") || type.includes("etn")) return true;
    if (/\betf\b|\betn\b|exchange[- ]traded/.test(asset)) return true;
    if (/\bETF$/.test(ticker)) return true;
    return false;
  }

  function assetKind(t) {
    const type = (t.asset_type || "").toLowerCase();
    const asset = (t.asset || "").toLowerCase();
    if (isOptionLike(t)) return "option";
    if (type.includes("bond") || type.includes("municipal") || /rate\/coupon/.test(asset)) return "bond";
    if (type.includes("stock") || isEtfLike(t)) return "stock";
    return "other";
  }

  function isFilingError(t) {
    const td = t.trade_date || "";
    const fd = t.filed_date || "";
    return td.length >= 10 && fd.length >= 10 && td > fd;
  }

  function sideLabel(side) {
    if (side === "purchase") return "Buy";
    if (side === "sale") return "Sell";
    if (side === "sale_post") return "Sale Post-exercise";
    if (side === "exercise") return "Exercise";
    if (side === "award") return "Awarded";
    if (side === "exchange") return "Exch";
    return side || "";
  }

  function isAward(t) {
    if (!t || typeof t !== "object") return false;
    const code = String(t.code || "").toUpperCase();
    const nature = String(t.nature || "");
    if (t.side === "award") return true;
    if (code === "A") return true;
    if (code === "30" || code === "45" || code === "46") return true;
    return /^(30|45|46)\b/.test(nature);
  }

  function isExercise(t) {
    if (!t || typeof t !== "object") return false;
    const code = String(t.code || "").toUpperCase();
    const nature = String(t.nature || "");
    if (t.side === "exercise") return true;
    if (code === "M" || code === "X") return true;
    if (code === "51" || code === "54" || code === "57" || code === "59" || code === "71") return true;
    return /^(51|54|57|59|71)\b/.test(nature);
  }

  function isSalePost(t) {
    if (!t || typeof t !== "object") return false;
    const code = String(t.code || "").toUpperCase();
    if (t.side === "sale_post") return true;
    return code === "F";
  }

  function isMarketBuy(t) {
    return !!(t && t.side === "purchase" && !isAward(t) && !isExercise(t));
  }

  function isMarketSale(t) {
    return !!(t && (t.side === "sale" || t.side === "sale_post" || isSalePost(t)));
  }

  function isTapeTxn(t) {
    if (!t) return false;
    const side = t.side;
    return side === "purchase" || side === "sale" || side === "sale_post" ||
      side === "award" || side === "exercise" || side === "exchange" ||
      isAward(t) || isExercise(t) || isSalePost(t);
  }

  function txnLabel(t) {
    if (t == null) return "";
    if (typeof t === "string") return sideLabel(t);
    if (isSalePost(t)) return "Sale Post-exercise";
    if (isExercise(t)) return "Exercise";
    if (isAward(t)) return "Awarded";
    return sideLabel(t.side);
  }

  function txnClass(t) {
    if (isSalePost(t) || (t && t.side === "sale_post")) return "postex";
    if (t && t.side === "sale") return "sell";
    if (isExercise(t)) return "exercise";
    if (isAward(t)) return "award";
    if (t && t.side === "purchase") return "buy";
    if (t && t.side === "exchange") return "exch";
    return "";
  }

  function titleCaseName(s) {
    const raw = String(s || "").trim();
    if (!raw) return "";
    if (/[a-z]/.test(raw)) return raw;
    return raw.toLowerCase().replace(/(^|[\s',.\-/])([a-z])/g, (_, a, b) => a + b.toUpperCase());
  }

  function shortRole(t) {
    if (!t) return "";
    const title = String(t.title || "");
    if (t.ceo || /\bCEO\b|Chief Executive/i.test(title)) return "CEO";
    if (t.is_officer || /officer|president|cfo|coo|chief |\bvp\b|vice[ -]?pres|counsel|secretary|manager|founder|treasurer/i.test(title)) {
      return "Officer";
    }
    if (t.is_director || /director|chair/i.test(title)) return "Dir";
    return title ? "Officer" : "";
  }

  function filingLagDays(t) {
    if (!t || !t.filed_date || !t.trade_date) return null;
    const a = new Date(String(t.filed_date) + "T00:00:00");
    const b = new Date(String(t.trade_date) + "T00:00:00");
    if (isNaN(a.getTime()) || isNaN(b.getTime())) return null;
    return Math.max(0, Math.round((a.getTime() - b.getTime()) / 86400000));
  }

  function dotted(parts) {
    const out = [];
    parts.forEach((p, i) => {
      if (i) out.push("<span class=\"qc-txn-dot\" aria-hidden=\"true\">·</span>");
      out.push(p);
    });
    return out;
  }

  function formatHeldPct(n) {
    if (n == null || n === "") return "—";
    const x = Number(n);
    if (!isFinite(x)) return "—";
    return x.toFixed(4) + "%";
  }

  function isChartTicker(code) {
    const c = String(code || "").toUpperCase();
    return !!(c && c !== "—" && /^[A-Z][A-Z0-9.]{0,8}$/.test(c) && !BAD_TICKERS[c]);
  }

  function amountHigh(amount) {
    const nums = String(amount || "").match(/\$[\d,]+/g);
    if (!nums || !nums.length) return 0;
    return parseInt(nums[nums.length - 1].replace(/[$,]/g, ""), 10) || 0;
  }

  function formatAmountRange(amount) {
    const raw = String(amount || "").trim();
    if (!raw) return raw;
    const bits = raw.split(/\s*[\u2013\u2014\-]\s*/);
    if (bits.length === 2 && /\$/.test(bits[0]) && /\$/.test(bits[1])) {
      const a = parseInt(bits[0].replace(/[^\d]/g, ""), 10);
      const b = parseInt(bits[1].replace(/[^\d]/g, ""), 10);
      if (a && b) return formatMoney(a) + "\u2013" + formatMoney(b);
    }
    if (/^\$[\d,]+$/.test(raw)) {
      const n = parseInt(raw.replace(/[^\d]/g, ""), 10);
      if (n) return formatMoney(n);
    }
    return raw;
  }

  function formatMoney(n) {
    if (n >= 1000000) {
      const m = n / 1000000;
      const s = (m >= 10 ? m.toFixed(0) : m.toFixed(1)).replace(/\.0$/, "");
      return "$" + s + "M";
    }
    if (n >= 1000) return "$" + Math.round(n / 1000) + "K";
    return "$" + n.toLocaleString("en-US");
  }

  function formatQuote(n) {
    const x = Number(n);
    if (!isFinite(x)) return "—";
    const abs = Math.abs(x);
    const min = 2;
    const max = abs >= 1 ? 2 : 4;
    return "$" + x.toLocaleString("en-US", {
      minimumFractionDigits: min,
      maximumFractionDigits: max
    });
  }

  function lastCloseFromFile(data) {
    if (!data || data.missing) return null;
    const px = Number(data.px);
    if (isFinite(px) && px > 0) return px;
    const c = data.c;
    if (c && c.length) {
      const last = c[c.length - 1];
      const n = last && Number(last[1]);
      if (isFinite(n) && n > 0) return n;
    }
    return null;
  }

  function lastCloseLookup(map, t) {
    if (!map) return null;
    const code = String(tapeSymbol(t) || "").toUpperCase();
    if (!code) return null;
    if (map[code] != null) return map[code];
    return null;
  }

  function lastCloseHtml(n, code) {
    const text = (n != null && isFinite(Number(n))) ? formatQuote(n) : "—";
    const tk = String(code || "").toUpperCase();
    return "<span class=\"qc-txn-last\"" +
      (tk ? " data-tk=\"" + esc(tk) + "\"" : "") +
      " title=\"Last close\">" + esc(text) + "</span>";
  }

  function fillLastCloseMap(codes, map) {
    map = map || {};
    const want = [];
    (codes || []).forEach((c) => {
      const t = String(c || "").toUpperCase();
      if (!isChartTicker(t)) return;
      if (Object.prototype.hasOwnProperty.call(map, t)) return;
      want.push(t);
    });
    return Promise.all(want.map((t) =>
      fetch("prices/" + encodeURIComponent(t) + ".json")
        .then((r) => r.ok ? r.json() : null)
        .then((d) => { map[t] = lastCloseFromFile(d); })
        .catch(() => { map[t] = null; })
    )).then(() => map);
  }

  function applyLastCloses(root, map) {
    if (!root || !map) return;
    root.querySelectorAll(".qc-txn-last[data-tk]").forEach((el) => {
      const px = map[el.getAttribute("data-tk")];
      el.textContent = (px != null && isFinite(Number(px))) ? formatQuote(px) : "—";
    });
  }

  function signedMoney(n) {
    const abs = formatMoney(Math.abs(n));
    if (n > 0) return "+" + abs;
    if (n < 0) return "−" + abs;
    return abs;
  }

  function prettyDate(iso) {
    const d = new Date(String(iso || "") + "T00:00:00");
    if (isNaN(d.getTime())) return iso || "—";
    return d.toLocaleString("en-US", { month: "short", day: "numeric", year: "numeric" });
  }

  function threeYearCutoff() {
    const d = new Date();
    d.setFullYear(d.getFullYear() - 3);
    return d;
  }

  function cleanAsset(s) {
    return String(s || "").replace(/\s+(Common Stock.*|Class [A-Z].*)$/i, "").trim();
  }

  const BROKER_ISSUERS = {
    MS: /morgan stanley/i,
    GS: /goldman sachs/i,
    JPM: /jpmorgan|jp morgan/i,
    BAC: /bank of america|merrill/i,
    WFC: /wells fargo/i,
    SCHW: /schwab/i,
    C: /\bcitigroup\b|\bciti\b/i,
    BLK: /blackrock/i,
    UBS: /\bubs\b/i,
    PNC: /\bpnc\b/i,
    IBKR: /interactive brokers/i
  };

  const SHARE_TAIL = /\s+(?:Common Stock.*|Class [A-Z].*|Ordinary Shares?.*|American Depositary Shares?.*|\bADS\b.*|Registered Shares.*|Common Shares.*|New York Registry Shares.*|Common Units(?: Representing.*)?|\bVoting\b.*|Series [A-Z]\b.*|\bCMN\b.*)$/i;

  function issuerName(code, raw) {
    const c = String(code || "").toUpperCase();
    let s = String(raw || "").replace(/\s+/g, " ").trim();
    if (!s || s === "—") return c && c !== "—" ? c : "";
    // Glued PTR rows and option/note tails that leak into tickers.json names.
    s = s.replace(/^.*\([A-Z]{1,6}\)(?:\s*\[ST\])?\s+[PS]\s+\d{1,2}\/\d{1,2}\/\d{2,4}.*?(?:\$[\d,]+(?:\s*-\s*\$[\d,]+)?)\s+/i, "");
    s = s.replace(/^[PS]\s+\d{1,2}\/\d{1,2}\/\d{2,4}.*?(?:\$[\d,]+(?:\s*-\s*\$[\d,]+)?)\s+/i, "");
    s = s.replace(/\s+Option Type:.*$/i, "");
    s = s.replace(/\s*(?:Bond|Notes?|MTN)?\s*Rate\/Coupon:.*$/i, "");
    s = s.replace(/\s+\b(?:Bond|Notes?|MTN)\s*$/i, "");
    s = (s.split(/\s*>\s*/).pop() || s).trim();
    const brokerStock = BROKER_ISSUERS[c] && BROKER_ISSUERS[c].test(s)
      && !/\b(ira|roth|trust account|brokerage|select uma|unified management|joint tbe)\b/i.test(s);
    if (brokerStock) {
      return s.replace(SHARE_TAIL, "").replace(/\s*-\s*$/, "").trim() || c;
    }
    const brokers = "morgan stanley|goldman sachs|fidelity(?: investments)?|vanguard|charles schwab|\\bschwab\\b|bank of america|merrill lynch|\\bmerrill\\b|jpmorgan(?: chase)?|jp ?morgan|wells fargo|\\bubs\\b|raymond james|edward jones|ameriprise|e\\*?trade|td ameritrade|interactive brokers|\\bchase\\b|aperio group(?: llc)?";
    const account = "smith barney(?: llc)?|ira|roth ira|trust account|brokerage account|\\bbrokerage\\b|select uma(?: account)?|unified management account|joint tbe";
    if (!BROKER_ISSUERS[c]) {
      s = s.replace(new RegExp("^(?:" + brokers + ")\\b[\\s,:-]*", "i"), "");
    }
    s = s.replace(new RegExp("^(?:" + account + "|uma(?: account)?|select uma(?: account)?)\\b[\\s,:-]*", "i"), "");
    s = s.replace(/^(?:account(?:\s*#\s*\d+)?|uma account(?:\s*#\s*\d+)?)\b[\s,:-]*/i, "");
    s = s.replace(/^#\s*\d+\s+/, "");
    s = s.replace(/^\d{2,5}\s+/, "");
    s = s.replace(/^[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*)?\s+IRA\s+/i, "");
    s = s.replace(/^(?:tacs r3k)\s+/i, "");
    s = s.replace(/^\$[\d,]+(?:\.\d+)?\s+(?:F\s+S:\s*Amended\s+\S+\s+)?/i, "");
    s = s.replace(/^(?:CP\s*-?\s*INV|CRT\s*-?\s*Standard Unit Trust|Trust\s*-\s*\S+)\s+/i, "");
    s = s.replace(/^(?:D:\s*)?(?:Portfolio Rebalance|Account Closing|FULL LIQUIDATION\.?|Professionally managed account|D\/B\/A|Corporate bond|Municipal bond|Treasury bond)\s+/i, "");
    s = s.replace(/\b(?:D:\s*)?Portfolio Rebalance\s+/i, "");
    s = s.replace(/^D:\s+/i, "");
    s = s.replace(/^(?:investment account(?:\s*#\s*\d+)?)\b[\s,:-]*/i, "");
    s = s.replace(/^financial disclosure\.\s*/i, "");
    s = s.replace(/^active assets\s*\(\d+\)\s*/i, "");
    s = s.replace(/^.*\bD:\s*(?:professionally managed account\.?\s*|sold entire holding\.?\s*|own\/operate\s+(?:mobile home park\s+)?)/i, "");
    s = s.replace(/^C:\s*Sell to Open\s*[–—-]\s*(?:New\s+)?Covered Call Contract\s+/i, "");
    s = s.replace(/^.*\bFamily Partnership\s+/i, "");
    s = s.replace(SHARE_TAIL, "");
    if (c) {
      const escCode = c.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      s = s.replace(new RegExp("\\s*\\(" + escCode + "\\)\\s*$", "i"), "");
    }
    s = s.replace(/\s*-\s*Common\s+Sto.*$/i, "");
    s = s.replace(/\s*-\s*$/, "").trim();
    s = s.replace(/\s+CMN\b.*$/i, "").trim();
    s = s.replace(/\s*S\/ADR\s*$/i, "").trim();
    if (!s || /^(common stock|class [a-z]|llc|inc|corp|corporation|company|plc)$/i.test(s)) return c && c !== "—" ? c : "";
    if (/^[A-Z][A-Z0-9.]{0,6}$/.test(s) && s.toUpperCase() !== c) return c && c !== "—" ? c : s;
    if (/\$[\d,]|\d{2}\/\d{2}\/\d{4}|\[ST\]|rate\/coupon|matures:/i.test(s)) return c && c !== "—" ? c : "";
    return s;
  }

  const WEAK_ISSUER = /^(corporation|incorporated|company|companies|plc|inc\.?|corp\.?|llc|the|common stock|class [a-z])$/i;

  function isWeakIssuer(code, name) {
    const c = String(code || "").toUpperCase();
    const s = String(name || "").replace(/\s+/g, " ").trim();
    if (!s || s === "—" || s === "-") return true;
    if (c && s.toUpperCase() === c) return true;
    if (WEAK_ISSUER.test(s)) return true;
    if (/^D:\s*/i.test(s)) return true;
    if (/\bTrust\s*>/i.test(s) || /\b(?:grandchildren|family)\s+\d*\s*trust\b/i.test(s)) return true;
    if (/^[A-Z][A-Z0-9.]{0,6}$/.test(s) && s.toUpperCase() !== c) return true;
    return false;
  }

  function displayIssuer(code, raw, fallback) {
    const primary = issuerName(code, raw);
    if (!isWeakIssuer(code, primary)) return primary;
    if (fallback != null && fallback !== raw) {
      const second = issuerName(code, fallback);
      if (!isWeakIssuer(code, second)) return second;
    }
    return primary || String(code || "").toUpperCase() || "";
  }

  const OVERLAP_RULES = [
    {
      seat: /environment and public works|clean air, climate, and nuclear|energy and natural resources|energy and commerce|natural resources|interior, environment|\benergy\b/i,
      industry: /electric services|petroleum refining|crude petroleum|natural gas|coal mining|metal mining|gas transmission|gas distribution|oil.{0,12}gas|petroleum|pipeline|drilling oil/i,
      name: /constellation energy|chevron|exxon|conocophillips|occidental|valero|phillips 66|kinder morgan|cheniere|duke energy|nextera|sempra|dominion|exelon|firstenergy|freeport|bp p\.?l\.?c|british petroleum|championx|bwx technologies/i,
      tickers: /^(BP|XOM|CVX|COP|OXY|VLO|PSX|MPC|EOG|SLB|HAL|BKR|WMB|KMI|OKE|LNG|FANG|DVN|HES|MRO|CHX|NEE|DUK|SO|D|EXC|AEP|SRE)$/,
      why: "Energy / environment"
    },
    {
      seat: /chemical safety|waste management/i,
      industry: /industrial organic chemical|plastic material|paints|coatings|agricultural chemical/i,
      name: /sherwin-williams|dow inc|dow chemical|dupont|celanese|carlisle|air products|ppg industries|lyondell/i,
      why: "Chemical safety"
    },
    {
      seat: /labor, health|health and human|public health/i,
      industry: /pharmaceutical|hospital & medical|biological product|home health/i,
      name: /eli lilly|unitedhealth|pfizer|merck|abbvie|zoetis|johnson & johnson|cigna|elevance|humana|medtronic|abbott|amgen|bristol|gilead|moderna|regeneron|chemed/i,
      why: "Health funding (HHS)"
    },
    {
      seat: /telecommunication|telecommunications and media|consumer protection, technology|data privacy/i,
      industry: /semiconductor|prepackaged software|cable & other pay|telephone|radio & tv|computer communications|computer programming|data proc/i,
      name: /alphabet|google|meta platforms|facebook|cisco|comcast|broadcom|apple|microsoft|verizon|at&t|t-mobile|nvidia|intel|qualcomm|oracle/i,
      why: "Tech / telecom"
    },
    {
      seat: /aviation, space|department of defense|armed services|intelligence/i,
      industry: /aircraft|aerospace|guided missile|ordnance|ship & boat|search, detection/i,
      name: /boeing|lockheed|rtx\b|raytheon|northrop|general dynamics|ge aerospace|l3harris|transdigm|palantir|bwx technologies/i,
      tickers: /^(BWXT|LMT|NOC|GD|RTX|BA|LHX|TDG|PLTR|HII|TXT)$/,
      why: "Defense / aviation"
    },
    {
      seat: /surface transportation|freight, pipelines|transportation and infrastructure/i,
      industry: /railroad|trucking|air transportation, scheduled|transportation services/i,
      name: /union pacific|norfolk southern|csx|fedex|ups|prologis|delta air|southwest air|united airlines|kinder morgan|enbridge/i,
      why: "Transportation / freight"
    },
    {
      seat: /\bbanking\b|committee on finance|financial services/i,
      industry: /national commercial bank|state commercial bank|security brokers|dealers & flotation|investment advice|savings institution/i,
      name: /jpmorgan|bank of america|wells fargo|goldman sachs|morgan stanley|pnc financial|citigroup/i,
      tickers: /^(JPM|BAC|WFC|GS|MS|PNC|C|USB|TFC|COF|AXP|BLK|SCHW|BK|STT|CBU|CATY|CFG|FITB|HBAN|KEY|RF|MTB|NTRS)$/,
      why: "Financial services"
    },
    {
      seat: /agriculture|nutrition|forestry/i,
      industry: /agriculture|meat packing|grain mill|farm machinery/i,
      name: /archer-daniels|tyson foods|mondelez|general mills|deere|corteva/i,
      why: "Agriculture"
    }
  ];

  function overlapHit(rule, code, name, industry) {
    const c = String(code || "").toUpperCase();
    if (rule.tickers && rule.tickers.test(c)) return true;
    if (rule.industry && rule.industry.test(industry || "")) return true;
    const blob = (c + " " + issuerName(c, name)).toLowerCase();
    return !!(rule.name && rule.name.test(blob));
  }

  const SECTORS = [
    "Energy", "Materials", "Industrials", "Utilities", "Healthcare", "Financials",
    "Consumer Discretionary", "Consumer Staples", "Information Technology",
    "Communication Services", "Real Estate", "Metals & Mining"
  ];

  const SECTOR_RULES = [
    [/gold and silver|metal mining|metal ores|steel works|blast furnaces|primary production of aluminum|nonferrous metals|nonferrous wire|metals service/i, "Metals & Mining"],
    [/crude petroleum|petroleum refining|natural gas|oil & gas|oil and gas|drilling oil|pipe lines|oil royalty|bituminous coal|coal mining|oil & gas field|mineral royalty/i, "Energy"],
    [/electric services|electric & other services|water supply|gas & other services combined|cogeneration|municipal — water/i, "Utilities"],
    [/real estate investment|real estate agents|\breal estate\b|nonresidential buildings|real estate operators|municipal — housing/i, "Real Estate"],
    [/pharmaceutical|biological product|biological research|surgical & medical|orthopedic|medical laborator|hospital & medical|in vitro|electromedical|x-ray apparatus|dental equipment|home health|health & allied|medicinal chemical|drugs, proprietar|medical, dental & hospital|ophthalmic|laboratory analytical|diagnostic substance|municipal — health/i, "Healthcare"],
    [/commercial bank|national commercial|state commercial|investment advice|security brokers|life insurance|casualty insurance|finance services|insurance agents|commodity contracts|security & commodity|investment company|accident & health insurance|savings institution|surety insurance|title insurance|credit agencies|credit institution|insurance carriers|asset-backed|investors, nec|credit reporting|blank checks|personal credit|cryptocurrency|government-sponsored|^municipal$/i, "Financials"],
    [/telephone communications|cable & other pay|television broadcasting|radio broadcasting stations|radiotelephone|newspapers|advertising agencies|books: publishing|communications services, nec/i, "Communication Services"],
    [/prepackaged software|semiconductor|computer programming|computer processing|computer integrated|electronic computers|computer communications|computer peripheral|computer storage|electronic components|computer & office|printed circuit|electronic connectors|communications equipment|electronic coils|electronic parts|computer software stores|telephone & telegraph apparatus|optical instruments|photographic equipment|electronic & other electrical equipment|meas & testing of electricity|measuring & controlling devices|calculating & accounting machines/i, "Information Technology"],
    [/\bbeverages\b|soft drinks|food and kindred|perfumes, cosmetics|soap, detergents|grocery stores|fats & oils|cigarettes|groceries|grain mill|malt beverages|canned, frozen|canned, fruits|miscellaneous food|sugar & confectionery|poultry|meat packing|ice cream|beer, wine|drug stores|food stores|agricultural production|farm product raw|cleang preparations|specialty cleaning/i, "Consumer Staples"],
    [/motor vehicles & passenger|hotels & motels|eating  places|eating & drinking|variety stores|auto dealers|amusement|mail-order|family clothing|footwear|household furniture|apparel|auto & home supply|furnishgs|building materials, hardware|household audio|household appliances|building materials dealers|radio, tv & consumer|games, toys|furniture stores|mobile homes|motorcycles|shoe stores|department stores|miscellaneous retail|home furniture|video tape|personal services|membership sports|racing|educational services|sporting & athletic|motor homes|lawn & garden|office furniture|auto rental|jewelry|shopping goods|retail stores, nec|wholesale-motor vehicle|operative builders|residential bldgs|leather & leather|electric housewares/i, "Consumer Discretionary"],
    [/plastic materials|chemicals & allied|agricultural chemical|inorganic chemical|organic chemical|paints, varnishes|paper mills|paperboard|lumber & wood|cement|concrete|abrasive|plastics products|fabricated rubber|adhesives|chemical products|synth resin|plastics foam|converted paper|millwood|sawmills|nonmetallic mineral|metal cans|paperboard containers|^commodities$/i, "Materials"],
    [/aircraft|guided missile|ordnance|search, detection|heavy construction|trucking|railroad|air transportation|water transportation|transportation services|farm machinery|engines & turbines|construction machinery|electrical industrial|refuse systems|hazardous waste|engineering services|electrical work|construction|freight & cargo|air courier|airport|ship & boat|industrial trucks|metalworkg|ball & roller|switchgear|motors & generators|pumps & pumping|industrial machinery|special industry machinery|materials handling|cutlery|fabricated plate|fabricated metal|heating equip|air-cond|fans & blowers|refrigeration|help supply|equipment rental|detective, guard|management consulting|management services|testing laborator|industrial instruments|miscellaneous electrical|motor vehicle parts|truck & bus bodies|deep sea|water, sewer, pipeline|business services|miscellaneous manufacturing|misc industrial|municipal — transportation|municipal — education/i, "Industrials"]
  ];

  function sectorOf(industry) {
    const s = String(industry || "").replace(/\s+/g, " ").trim();
    if (!s) return "";
    for (let i = 0; i < SECTOR_RULES.length; i++) {
      if (SECTOR_RULES[i][0].test(s)) return SECTOR_RULES[i][1];
    }
    return "Industrials";
  }

  const NAME_STOP = {
    INC: 1, INCORPORATED: 1, CORP: 1, CORPORATION: 1, CO: 1, COMPANY: 1, COS: 1,
    LTD: 1, LLC: 1, LP: 1, LLP: 1, PLC: 1, SA: 1, AG: 1, NV: 1, THE: 1, AND: 1,
    GROUP: 1, HOLDINGS: 1, HOLDING: 1, HLDGS: 1, HLDG: 1, NEW: 1, DEL: 1, DE: 1,
    NOTE: 1, NOTES: 1, BOND: 1, BONDS: 1, MTN: 1, NTS: 1, BDS: 1, CLASS: 1,
    COMMON: 1, STOCK: 1, ADR: 1, SPONSORED: 1, UNSPONSORED: 1, CMN: 1, CMIN: 1,
    CIN: 1, CMY: 1, COM: 1, NPV: 1, CL: 1, SHS: 1, SHARES: 1, VOTING: 1,
    HYBRID: 1, PERPETUAL: 1, FUNDING: 1
  };
  const NAME_GENERIC = {
    GLOBAL: 1, NATIONAL: 1, AMERICAN: 1, FIRST: 1, BANK: 1, ENERGY: 1, CAPITAL: 1,
    FINANCIAL: 1, INTERNATIONAL: 1, UNITED: 1, STATE: 1, GENERAL: 1, POWER: 1,
    LIGHT: 1, TRUST: 1, FUND: 1, PARTNERS: 1, FINANCE: 1, SERVICES: 1, INDUSTRIES: 1
  };
  const NAME_ALIASES = {
    TESLA: "TSLA",
    NIKE: "NKE",
    "WELLS FARGO": "WFC",
    "WALLS FARGO": "WFC",
    "ELLS FARGO": "WFC",
    EXELON: "EXC",
    "BERKSHIRE HATHAWAY": "BRK.B",
    "BROWN FORMAN": "BF.B",
    APPLE: "AAPL",
    "META PLATFORMS": "META",
    MICROSOFT: "MSFT",
    FISERV: "FI",
    SOLVENTUM: "SOLV",
    CARVANA: "CVNA",
    APPLOVIN: "APP",
    WORKDAY: "WDAY",
    LENNAR: "LEN",
    ACCENTURE: "ACN",
    NVIDIA: "NVDA",
    ALPHABET: "GOOGL",
    MASTERCARD: "MA",
    WEYERHAEUSER: "WY",
    MCKESSON: "MCK",
    DATADOG: "DDOG",
    COSTAR: "CSGP",
    CINTAS: "CTAS",
    "EQUITY RESIDENTIAL": "EQR",
    KLA: "KLAC",
    PROGRESSIVE: "PGR",
    SYSCO: "SYY",
    STRYKER: "SYK",
    AMETEK: "AME",
    EQUIFAX: "EFX",
    NASDAQ: "NDAQ",
    TARGET: "TGT",
    AIRBNB: "ABNB",
    PAYCHEX: "PAYX",
    GARTNER: "IT",
    MOSAIC: "MOS",
    KROGER: "KR",
    "TIX COMPANIES": "TJX",
    "TJX COMPANIES": "TJX",
    INSULET: "PODD",
    DECKERS: "DECK",
    "DECKERS OUTDOOR": "DECK",
    AMRIZE: "AMRZ",
    EQUITABLE: "EQH",
    INTERCONTINENTAL: "ICE",
    "INTERCONTINENTAL EXCHANGE": "ICE",
    FLUTTER: "FLUT",
    "FLUTTER ENTERTAINMENT": "FLUT",
    "COOPER COMPANIES": "COO",
    KKR: "KKR",
    SHELL: "SHEL",
    WABTEC: "WAB",
    ZILLOW: "Z",
    HIVE: "HIVE",
    PERRIGO: "PRGO",
    BENTLEY: "BSY",
    "BENTLEY SYSTEMS": "BSY",
    GIBRALTAR: "ROCK",
    "CHESAPEAKE UTILITIES": "CPK",
    SHIFT4: "FOUR",
    "SHIFT4 PAYMENTS": "FOUR",
    "FRESH MONTE": "FDP",
    MARSH: "MMC",
    HOLOGIC: "HOLX",
    ZOETIS: "ZTS",
    "CROWN CASTLE": "CCI",
    "INTERNATIONAL PAPER": "IP",
    "KIMBERLY CLARK": "KMB",
    HORTON: "DHI",
    QUALCOMM: "QCOM",
    "ANALOG DEVICES": "ADI",
    BLACKROCK: "BLK",
    "JPMORGAN CHASE": "JPM",
    "UNITED AIRLINES": "UAL",
    "ILLINOIS TOOL": "ITW",
    "CAPITAL ONE": "COF",
    "TEXAS INSTRUMENTS": "TXN",
    BLOCK: "XYZ",
    "WARNER BROS": "WBD",
    "WARNER DISCOVERY": "WBD",
    HARTFORD: "HIG",
    "CARDINAL HEALTH": "CAH",
    MONSTER: "MNST",
    "MONSTER BEVERAGE": "MNST",
    COSTCO: "COST",
    "XCEL ENERGY": "XEL",
    "BANK AMERICA": "BAC",
    "TAKE TWO": "TTWO",
    "TWO INTERACTIVE": "TTWO",
    "JB HUNT": "JBHT",
    "ELECTRONIC ARTS": "EA",
    CROWDSTRIKE: "CRWD",
    LINDE: "LIN"
  };
  const OCR_PHRASES = [
    [/\bL[XI]ETON\b/g, "EXELON"],
    [/\bOWN\s+FORMAN\b/g, "BROWN FORMAN"],
    [/\b4T\s*&?\s*T\b/g, "AT T"],
    [/\bIPMORGAN\b/g, "JPMORGAN"],
    [/\bPMORGAN\b/g, "JPMORGAN"],
    [/\bIRNER\s+BROS\b/g, "WARNER BROS"],
    [/\bOISCOVERY\b/g, "DISCOVERY"],
    [/\bRTFORP\b/g, "HARTFORD"],
    [/\bDINAL\s+HEALTH\b/g, "CARDINAL HEALTH"],
    [/\bINSTER\s+BEVERAGE\b/g, "MONSTER BEVERAGE"],
    [/\bOSTCO\s+WHOLESALE\b/g, "COSTCO WHOLESALE"],
    [/\bPAYCHE\b/g, "PAYCHEX"],
    [/\bTWO\s+INTERACTIVE\b/g, "TAKE TWO INTERACTIVE"],
    [/\bVK\s+OF\s+AMERICA\b/g, "BANK OF AMERICA"],
    [/\bXCEL\s*,?\s*ENERGY\b/g, "XCEL ENERGY"],
    [/\bOSTEO\s+WHOLESALE\b/g, "COSTCO WHOLESALE"],
    [/\bJB\s+HUNT\b/g, "JB HUNT"],
    [/\bTECTROWIC\s+ARTS\b/g, "ELECTRONIC ARTS"],
    [/\bCLASS\s+8\b/g, "CLASS B"],
    [/\bCROW\s+DSTRIKE\b/g, "CROWDSTRIKE"],
    [/\bITED\s+AIRLINES\b/g, "UNITED AIRLINES"],
    [/\bNOIS\s+TOOL\b/g, "ILLINOIS TOOL"],
    [/\bCAPTRAT\s+ONE\b/g, "CAPITAL ONE"],
    [/\bEXAS\s+INSTRUMENTS\b/g, "TEXAS INSTRUMENTS"],
    [/^(?:WE|XE|KE|IKE)\s+CLASS[- ]B\b/i, "NIKE CLASS B"],
    [/\bCOOPER\s+COS\b/g, "COOPER COMPANIES"],
    [/\bTIX\s+COMPANIES\b/g, "TJX COMPANIES"],
    [/\bENTMT\b/g, "ENTERTAINMENT"],
    [/\bEXCHANG\b/g, "EXCHANGE"],
    [/\bOUTDOORS\b/g, "OUTDOOR"],
    [/\bUTILS\b/g, "UTILITIES"],
    [/\bPMTS\b/g, "PAYMENTS"],
    [/\bSYS\b/g, "SYSTEMS"],
    [/\bINDS\b/g, "INDUSTRIES"],
    [/\bINTERNTNL\b/g, "INTERNATIONAL"],
    [/\bJALCOMM\b/g, "QUALCOMM"],
    [/\bJALOG\b/g, "ANALOG"],
    [/\bMBERLY\b/g, "KIMBERLY"],
    [/\bD\s+RHORTON\b/g, "HORTON"],
    [/\bD\s+R\s+HORTON\b/g, "HORTON"],
    [/\bRHORTON\b/g, "HORTON"]
  ];
  const MUNI_NAME_RE = /\b(SCH(?:OOL)?|INDPT|CNTY|COUNTY|TWP|TOWNSHIP|CITY\s+OF|MUNI(?:CIPAL)?|TURNPIKE|AUTH(?:ORITY)?|UNIV(?:ERSITY)?|HOSP(?:ITAL)?|HOUSING|WTR|WATER|SWR|SEWER|\bGO\b|REV(?:ENUE)?|BLDG|LOC\s+BLDG)\b/i;
  const UNLINKED_NAME_RE = /preferred stock|perpetual preferred|structured note|linked note|\betf\b|index fund|dividend appreciation index|\bbdc\b|business development company|non-cumulative|cumulative redeemable|commodities plus|fund class|class y shares/i;
  const BROKER_PREFIX_RE = /^(?:morgan stanley|goldman sachs|fidelity(?: investments)?|vanguard|charles schwab|\bschwab\b|bank of america|merrill lynch|\bmerrill\b|jpmorgan(?: chase)?|jp ?morgan|wells fargo|\bubs\b|raymond james|edward jones|ameriprise|e\*?trade|td ameritrade|interactive brokers|\bchase\b|aperio group(?: llc)?)\b[\s,:-]*/i;
  const ACCOUNT_PREFIX_RE = /^(?:smith barney(?: llc)?|ira|roth ira|trust account|brokerage account|\bbrokerage\b|select uma(?: account)?|unified management account|joint tbe|uma(?: account)?|account(?:\s*#\s*\d+)?)\b[\s,:-]*/i;

  function dist1(a, b) {
    if (!a || !b || a === b) return false;
    const la = a.length;
    const lb = b.length;
    if (Math.abs(la - lb) > 1 || Math.min(la, lb) < 3) return false;
    if (la === lb) {
      let n = 0;
      for (let i = 0; i < la; i++) if (a[i] !== b[i] && ++n > 1) return false;
      return n === 1;
    }
    let short = a;
    let long = b;
    if (la > lb) { short = b; long = a; }
    let i = 0;
    let j = 0;
    let skip = 0;
    while (i < short.length && j < long.length) {
      if (short[i] === long[j]) { i++; j++; continue; }
      if (++skip > 1) return false;
      j++;
    }
    return true;
  }

  function nameTokens(s) {
    s = String(s || "").toUpperCase().replace(/&/g, " AND ").replace(/[-_.]/g, " ");
    s = s.replace(/[^A-Z0-9 ]+/g, " ");
    const out = [];
    s.split(/\s+/).forEach((t) => {
      if (!t || NAME_STOP[t] || /^\d+$/.test(t)) return;
      if (out.length && out[out.length - 1] === t) return;
      out.push(t);
    });
    if (out.length && out.every((t) => t.length === 1)) return [out.join("")];
    while (out.length && out[out.length - 1].length === 1 && "ABC".indexOf(out[out.length - 1]) >= 0) out.pop();
    return out;
  }

  function classHint(asset) {
    const m = String(asset || "").toUpperCase().match(/\b(?:CLASS|CL)[- ]?([ABC])\b/);
    return m ? m[1] : "";
  }

  function unglue(s) {
    let prev = "";
    while (prev !== s) {
      prev = s;
      s = s.replace(/([A-Z])(INCORPORATED|INTERNATIONAL|CORPORATION|COMPANY|CLASS|CMN|INC|CORP|PLC|INTL|LTD)\b/g, "$1 $2");
      s = s.replace(/\b(INCORPORATED|INTERNATIONAL|CORPORATION|COMPANY|INTL|INC|CORP|PLC|CMN)(INCORPORATED|CORPORATION|COMPANY|CLASS|CMN|INC|CORP|PLC|INTL)\b/g, "$1 $2");
      s = s.replace(/\bCMNCLASS\b/g, "CMN CLASS");
      s = s.replace(/\bCLASS([ABC])\b/g, "CLASS $1");
    }
    return s;
  }

  function normalizeIssuer(asset) {
    let s = String(asset || "").toUpperCase().replace(/_/g, " ").replace(/\./g, " ").replace(/,/g, " ");
    OCR_PHRASES.forEach((pair) => { s = s.replace(pair[0], pair[1]); });
    s = unglue(s);
    s = s.replace(/(INC|CORP|CO|PLC|COMPANY|CORPORATION)(CMN|COM|CLASS|INC)/g, "$1 $2");
    s = s.replace(/\b(?:CLASS|CL)[- ]?[ABC]\b/g, " ");
    s = s.replace(/^[^A-Z]+/, "");
    return s.replace(/\s+/g, " ").trim();
  }

  function skipNameResolve(t) {
    if (!t) return true;
    if (isBond(t) || isOptionLike(t)) return true;
    const type = String(t.asset_type || "").toLowerCase();
    if (type.indexOf("commodit") >= 0 || type.indexOf("crypto") >= 0 || type.indexOf("non-public") >= 0) return true;
    const asset = t.asset || "";
    if (MUNI_NAME_RE.test(asset)) return true;
    if (UNLINKED_NAME_RE.test(asset)) return true;
    if (/\d(?:\.\d+)?%\s/.test(asset)) return true;
    if (/\b(hybrid|perpetual|rate\/coupon|matures:)/i.test(asset) && !/\b(cmn|common|class\s*[a-z])/i.test(asset)) return true;
    if (/\b(?:CALL|PUT)\b/i.test(asset) && !/callaway/i.test(asset) && (/\b(?:EXP\b|STRIKE|FLEX|EURO\s+PM)\b/i.test(asset) || /\b(?:CALL|PUT)\s*$/i.test(asset))) return true;
    return false;
  }

  function cleanIndexName(name) {
    let s = String(name || "").replace(/\s+/g, " ").trim();
    s = (s.split(/\s*>\s*/).pop() || s).trim();
    s = s.replace(/^D:\s*/i, "");
    if (s.indexOf(". ") >= 0) {
      const last = s.split(". ").pop();
      if (/\b(?:Corporation|Incorporated|Inc\.?|Company|Companies|Corp\.?|PLC)\b/i.test(last)) s = last;
    }
    for (let i = 0; i < 4; i++) {
      const n = s.replace(BROKER_PREFIX_RE, "").replace(ACCOUNT_PREFIX_RE, "").replace(/^[\s:,-]+/, "");
      if (n === s) break;
      s = n;
    }
    s = s.replace(/^#\s*\d+\s+/, "");
    s = s.replace(/^[A-Z]?\d{2,5}\s+/, "");
    s = s.replace(/^[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*)?\s+IRA\s+/, "");
    s = s.replace(/^(?:tacs r3k)\s+/i, "");
    s = s.replace(/^\$[\d,]+(?:\.\d+)?\s+/, "");
    s = s.replace(/^.*\b(?:grandchildren|family)\s+\d*\s*trust\s+/i, "");
    s = s.replace(/\s+Option Type:.*$/i, "");
    return s.replace(/\s*-\s*$/, "").replace(/^[\s,-]+|[\s,-]+$/g, "");
  }

  function pickCodes(codes, hint) {
    const uniq = [];
    const seen = {};
    (codes || []).forEach((c) => {
      const u = String(c || "").toUpperCase();
      if (!u || seen[u] || BAD_TICKERS[u]) return;
      seen[u] = 1;
      uniq.push(u);
    });
    if (!uniq.length) return "";
    if (hint) {
      const tagged = uniq.filter((c) => c.slice(-2) === "." + hint || c.slice(-2) === "-" + hint);
      if (tagged.length === 1) return tagged[0];
      if (tagged.length) return tagged.sort((a, b) => a.length - b.length)[0];
    }
    if (uniq.length === 1) return uniq[0];
    const roots = {};
    uniq.forEach((c) => { roots[c.replace(/[.-][A-Z]$/, "")] = 1; });
    if (Object.keys(roots).length === 1 && hint) {
      const tagged = uniq.filter((c) => c.slice(-1) === hint);
      if (tagged.length === 1) return tagged[0];
    }
    return "";
  }

  let _nameIdx = null;
  function nameIndex(file) {
    if (_nameIdx && _nameIdx.file === file) return _nameIdx;
    const byKey = new Map();
    const byFirst = new Map();
    const tickers = (file && file.tickers) || {};
    Object.keys(tickers).forEach((code) => {
      const rec = tickers[code];
      const key = nameTokens(cleanIndexName(rec && rec.name));
      if (!key.length) return;
      const k = key.join(" ");
      if (!byKey.has(k)) byKey.set(k, []);
      byKey.get(k).push(code);
      const f = key[0];
      if (!byFirst.has(f)) byFirst.set(f, []);
      byFirst.get(f).push({ key: key, code: code });
    });
    _nameIdx = { file: file, byKey: byKey, byFirst: byFirst };
    return _nameIdx;
  }

  const _assetTicker = new Map();
  function resolveTickerFromName(asset, file, hint) {
    const cacheKey = String(asset || "") + "\0" + String(hint || "");
    if (_assetTicker.has(cacheKey)) return _assetTicker.get(cacheKey);
    const issuer = normalizeIssuer(asset);
    const key = nameTokens(issuer);
    let hit = "";
    if (key.length && key[0] === "ALPHABET") {
      hit = hint === "C" ? "GOOG" : "GOOGL";
    }
    if (!hit && key.length && key[0] === "ZILLOW") {
      hit = hint === "A" ? "ZG" : "Z";
    }
    if (!hit && key.length) {
      const alias = NAME_ALIASES[key.join(" ")];
      if (alias && isChartTicker(alias)) hit = alias;
      if (!hit) {
        Object.keys(NAME_ALIASES).forEach((k) => {
          if (hit) return;
          const parts = k.split(" ");
          if (parts.length >= 2 && parts.every((p) => key.indexOf(p) >= 0)) {
            const a = NAME_ALIASES[k];
            if (isChartTicker(a)) hit = a;
          }
        });
      }
    }
    if (!hit && key.length) {
      const idx = nameIndex(file);
      const exact = idx.byKey.get(key.join(" "));
      if (exact) hit = pickCodes(exact, hint);
      if (!hit && key[0] && !NAME_GENERIC[key[0]]) {
        const firstRows = idx.byFirst.get(key[0]) || [];
        const firstUniq = {};
        firstRows.forEach((r) => { firstUniq[String(r.code).toUpperCase()] = 1; });
        const firstCodes = Object.keys(firstUniq);
        if (firstCodes.length === 1) hit = firstCodes[0];
      }
      if (!hit) {
        key.forEach((tok) => {
          if (hit || NAME_GENERIC[tok] || tok.length < 5) return;
          const found = {};
          idx.byFirst.forEach((rows) => {
            rows.forEach((r) => {
              if (r.key.indexOf(tok) >= 0) found[String(r.code).toUpperCase()] = 1;
            });
          });
          const codes = Object.keys(found);
          if (codes.length === 1) hit = codes[0];
        });
      }
      if (!hit && key.length === 1) {
        const tok = key[0];
        if (!NAME_GENERIC[tok]) {
          if (isChartTicker(tok) && file && file.tickers && file.tickers[tok] && !/\b(INC|CORP|CORPORATION|COMPANY)\b/.test(issuer)) {
            hit = tok;
          }
          const firsts = idx.byFirst.get(tok) || [];
          if (!hit && firsts.length) hit = pickCodes(firsts.map((r) => r.code), hint);
          if (!hit && tok.length >= 3) {
            const cropped = [];
            idx.byFirst.forEach((rows, head) => {
              const extra = head.length - tok.length;
              if (extra >= 1 && extra <= 2 && (head.slice(-tok.length) === tok || head.slice(0, tok.length) === tok) && !NAME_GENERIC[head]) {
                rows.forEach((r) => { if (r.key.length === 1) cropped.push(r.code); });
              }
            });
            const uniq = {};
            cropped.forEach((c) => { uniq[String(c).toUpperCase()] = 1; });
            if (Object.keys(uniq).length === 1) hit = pickCodes(cropped, hint);
          }
          if (!hit && tok.length >= 5) {
            const d1 = [];
            idx.byFirst.forEach((rows, head) => {
              if (dist1(head, tok) && !NAME_GENERIC[head]) {
                rows.forEach((r) => { if (r.key.length === 1) d1.push(r.code); });
              }
            });
            const uniq = {};
            d1.forEach((c) => { uniq[String(c).toUpperCase()] = 1; });
            if (Object.keys(uniq).length === 1) hit = pickCodes(d1, hint);
          }
        }
      }
      if (!hit && key.length >= 2) {
        const rest = {};
        key.slice(1).forEach((t) => { rest[t] = 1; });
        const fuzzy = [];
        idx.byFirst.forEach((rows, head) => {
          if (!dist1(head, key[0])) return;
          rows.forEach((r) => {
            if (r.key.slice(1).some((t) => rest[t])) fuzzy.push(r.code);
          });
        });
        const uniq = {};
        fuzzy.forEach((c) => { uniq[String(c).toUpperCase()] = 1; });
        if (fuzzy.length) {
          hit = pickCodes(fuzzy, hint);
          if (!hit) {
            const codes = Object.keys(uniq).filter((c) => isChartTicker(c));
            codes.sort((a, b) => (a.length <= 2 && b.length > 2 ? 1 : 0) - (b.length <= 2 && a.length > 2 ? 1 : 0) || b.length - a.length);
            if (codes.length) hit = codes[0];
          }
        }
      }
    }
    if (hit && !isChartTicker(hit)) hit = "";
    _assetTicker.set(cacheKey, hit);
    return hit;
  }

  function resolvedCode(t, file) {
    const lookup = (file && file.tickers) || {};
    const extra = ((file && file.assets) || {})[t && t.asset] || {};
    const opt = optionMeta(t);
    let code = String(t && t.ticker || extra.ticker || opt.under || "").toUpperCase();
    if (isOptionLike(t) && (code === "ING" || code === "FOR" || code === "EXP" || code === "ETF")) code = "";
    if (isChartTicker(code)) return code;
    if (skipNameResolve(t)) return code;
    return resolveTickerFromName(t && t.asset, file, classHint(t && t.asset)) || code;
  }

  function enrich(t, file) {
    const lookup = (file && file.tickers) || {};
    const extra = ((file && file.assets) || {})[t.asset] || {};
    const opt = optionMeta(t);
    const code = resolvedCode(t, file);
    const meta = lookup[code] || {};
    const bondLike = isBond(t);
    const name = bondLike
      ? (t.asset || meta.name || "—")
      : (displayIssuer(code, meta.name, cleanAsset(t.asset)) || "—");
    return {
      ...t,
      code: code || "—",
      company: name,
      industry: meta.industry || extra.industry || "",
      option: isOptionLike(t) ? opt : null
    };
  }

  function tickerInfo(t, file) {
    const lookup = (file && file.tickers) || {};
    const extra = ((file && file.assets) || {})[t.asset] || {};
    const opt = optionMeta(t);
    const code = resolvedCode(t, file);
    const meta = lookup[code] || {};
    const bondLike = isBond(t);
    const name = bondLike ? (t.asset || meta.name || "—") : (displayIssuer(code, meta.name, cleanAsset(t.asset)) || "—");
    const industry = meta.industry || extra.industry || "—";
    return { code: code || "—", name: name, industry: industry };
  }

  function parseAdded(t) {
    const s = t && t.added;
    if (!s) return null;
    const d = new Date(s);
    return isNaN(d.getTime()) ? null : d;
  }

  function isLanded(t, hours) {
    const d = parseAdded(t);
    if (!d) return false;
    const h = hours == null ? 72 : hours;
    return (Date.now() - d.getTime()) <= h * 3600000;
  }

  function lagDays(t) {
    const a = parseAdded(t);
    if (!a || !t.trade_date) return null;
    const tr = new Date(String(t.trade_date) + "T00:00:00");
    if (isNaN(tr.getTime())) return null;
    return Math.max(0, Math.round((a.getTime() - tr.getTime()) / 86400000));
  }

  function asOfLabel(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (isNaN(d.getTime())) return "";
    const date = d.toLocaleDateString("en-US", {
      month: "short", day: "numeric", year: "numeric",
      timeZone: "America/New_York"
    });
    const time = d.toLocaleTimeString("en-US", {
      hour: "numeric", minute: "2-digit", hour12: true,
      timeZone: "America/New_York"
    });
    return "Updated " + date + ", " + time + " ET";
  }

  function tapeSymbol(t) {
    const ticker = String((t && t.ticker) || "");
    const code = String((t && t.code) || "");
    if (isChartTicker(ticker)) return ticker;
    if (isChartTicker(code)) return code;
    return ticker || code;
  }

  function isFiniteNum(n) {
    if (n == null || n === "") return false;
    return isFinite(Number(n));
  }

  function formatSharesQuiet(n) {
    if (!isFiniteNum(n)) return "";
    return Number(n).toLocaleString("en-US", { maximumFractionDigits: 0 });
  }

  function formatPriceQuiet(n) {
    if (!isFiniteNum(n) || Number(n) === 0) return "";
    return formatQuote(n);
  }

  function formatHeldQuiet(n) {
    if (!isFiniteNum(n)) return "";
    return Number(n).toFixed(4) + "%";
  }

  function firstNum(t, keys) {
    if (!t) return null;
    for (let i = 0; i < keys.length; i++) {
      if (isFiniteNum(t[keys[i]])) return Number(t[keys[i]]);
    }
    return null;
  }

  function tradeShareCount(t) {
    return firstNum(t, ["shares", "quantity", "shares_traded", "txn_shares"]);
  }

  function tradeSharesHeld(t) {
    return firstNum(t, ["shares_after", "sharesAfter", "shares_held", "sharesHeld", "shares_owned"]);
  }

  function tradeHeldPctValue(t) {
    return firstNum(t, ["held_pct", "pct_held", "percentOfClass", "pctHeld", "percent_of_class"]);
  }

  function formatSharesCell(n) {
    return formatSharesQuiet(n) || "—";
  }

  function formatHeldCell(n) {
    return formatHeldQuiet(n) || "—";
  }

  function shareColsHtml(t) {
    const delta = positionDelta(t);
    return "<span class=\"qc-txn-sh\" title=\"Shares in the trade\">" + esc(formatSharesCell(tradeShareCount(t))) + "</span>" +
      "<span class=\"qc-txn-after\" title=\"Shares held after the trade\">" + esc(formatSharesCell(tradeSharesHeld(t))) + "</span>" +
      "<span class=\"qc-txn-held " + delta + "\" title=\"Percent of class held\">" + esc(formatHeldCell(tradeHeldPctValue(t))) + "</span>";
  }

  function shareFactsHtml(t) {
    const parts = [];
    const sh = formatSharesQuiet(tradeShareCount(t));
    const after = formatSharesQuiet(tradeSharesHeld(t));
    const held = formatHeldQuiet(tradeHeldPctValue(t));
    if (sh) parts.push("<span class=\"qc-txn-sh\">" + esc(sh) + " sh</span>");
    if (after) parts.push("<span class=\"qc-txn-after\">" + esc(after) + " held</span>");
    if (held) parts.push("<span class=\"qc-txn-held " + positionDelta(t) + "\">" + esc(held) + "</span>");
    if (!parts.length) return "";
    return "<div class=\"qc-txn-facts\">" + dotted(parts).join("") + "</div>";
  }

  function txnChipLabel(t) {
    const label = txnLabel(t);
    if (label === "Sale Post-exercise") return "Post-ex";
    return label;
  }

  function tradeHero(t) {
    if (!t) return { text: "", kind: "" };
    const range = formatAmountRange(t.amount);
    if (range && amountHigh(t.amount) > 0) return { text: range, kind: "usd" };
    const val = Number(t.value);
    if (isFinite(val) && val > 0) return { text: formatMoney(val), kind: "usd" };
    const sh = formatSharesQuiet(t.shares);
    if (sh) return { text: sh, kind: "shares" };
    return { text: "", kind: "" };
  }

  function positionDelta(t) {
    if (!t) return "flat";
    const before = isFiniteNum(t.shares_before) ? Number(t.shares_before) : null;
    const after = isFiniteNum(t.shares_after) ? Number(t.shares_after) : null;
    if (before != null && after != null) {
      if (after > before) return "up";
      if (after < before) return "down";
      return "flat";
    }
    if (isMarketSale(t) || isSalePost(t)) return "down";
    if (isMarketBuy(t) || isAward(t) || isExercise(t)) return "up";
    return "flat";
  }

  function txnEndHtml(t) {
    const hero = tradeHero(t);
    const chip = txnChipLabel(t);
    const fullLabel = txnLabel(t);
    let heroHtml = "";
    if (hero.kind === "usd" && hero.text) {
      heroHtml = "<span class=\"qc-txn-hero\">" + esc(hero.text) + "</span>";
    } else {
      if (hero.kind === "shares" && hero.text) {
        heroHtml = "<span class=\"qc-txn-hero is-shares\">" + esc(hero.text) + "<small> sh</small></span>";
      }
      heroHtml += "<span class=\"qc-txn-hero is-empty\">—</span>";
    }
    const chipHtml = chip
      ? ("<span class=\"qc-txn-chip\"" + (chip !== fullLabel ? " title=\"" + esc(fullLabel) + "\"" : "") + ">" +
          esc(chip) +
        "</span>")
      : "";
    return (chipHtml || heroHtml) ? "<div class=\"qc-txn-end\">" + chipHtml + heroHtml + "</div>" : "";
  }

  function tickerHref(t) {
    if (!t || !t.ticker || !isChartTicker(t.ticker)) return "";
    return "insider-ticker.html?t=" + encodeURIComponent(t.ticker);
  }

  function tapeTickerHtml(t) {
    if (!t || !t.ticker) return "";
    const href = tickerHref(t);
    return href
      ? "<a class=\"qc-txn-tk\" href=\"" + esc(href) + "\">" + esc(t.ticker) + "</a>"
      : "<span class=\"qc-txn-tk\">" + esc(t.ticker) + "</span>";
  }

  function tapeRowHtml(t, opts) {
    opts = opts || {};
    const cls = txnClass(t);
    const name = titleCaseName(t.filer);
    const href = opts.nameHref || "";
    const nameHtml = name
      ? (href
          ? "<a class=\"qc-txn-name\" href=\"" + esc(href) + "\">" + esc(name) + "</a>"
          : (opts.nameHtml || "<span class=\"qc-txn-name\">" + esc(name) + "</span>"))
      : (opts.nameHtml || "");
    const tickerHtml = opts.tickerHtml || tapeTickerHtml(t);
    const role = shortRole(t);
    const roleHtml = role ? "<span class=\"qc-txn-role\">" + esc(role) + "</span>" : "";
    const company = String(t.company || "").trim();
    const coHref = tickerHref(t);

    const whoParts = [];
    if (tickerHtml) whoParts.push(tickerHtml);
    if (roleHtml) whoParts.push(roleHtml);

    const metaParts = [];
    if (t.trade_date) metaParts.push("<span>" + esc(prettyDate(t.trade_date)) + "</span>");
    if (company) {
      metaParts.push(coHref
        ? "<a class=\"qc-txn-co\" href=\"" + esc(coHref) + "\">" + esc(company) + "</a>"
        : "<span class=\"qc-txn-co\">" + esc(company) + "</span>");
    }

    const who = dotted(whoParts);
    const meta = dotted(metaParts);
    const sub = (who.length ? "<div class=\"qc-txn-who\">" + who.join("") + "</div>" : "") +
      (meta.length ? "<div class=\"qc-txn-meta\">" + meta.join("") + "</div>" : "");

    const nameLine = "<div class=\"qc-txn-id-line\">" + nameHtml + roleHtml + "</div>";
    const companyBits = [];
    if (t.ticker) companyBits.push("<span class=\"qc-txn-tk\">" + esc(t.ticker) + "</span>");
    if (company) companyBits.push("<span class=\"qc-txn-co-name\">" + esc(company) + "</span>");
    const companyInner = companyBits.join("");
    const companyCol = companyInner
      ? (coHref
          ? "<a class=\"qc-txn-company\" href=\"" + esc(coHref) + "\">" + companyInner + "</a>"
          : "<span class=\"qc-txn-company\">" + companyInner + "</span>")
      : "<span class=\"qc-txn-company\"></span>";

    const tblParts = [];
    if (t.trade_date) tblParts.push("<span class=\"qc-txn-date\">" + esc(prettyDate(t.trade_date)) + "</span>");
    tblParts.push(shareColsHtml(t));
    const px = formatPriceQuiet(t.price);
    if (px) tblParts.push("<span class=\"qc-txn-px\">" + esc(px) + "</span>");
    const tbl = tblParts.length ? "<div class=\"qc-txn-tbl\">" + tblParts.join("") + "</div>" : "";
    const facts = shareFactsHtml(t);

    return "<li class=\"qc-txn qc-txn-tape" + (cls ? " " + cls : "") + "\"" +
      (t.id ? " data-id=\"" + esc(t.id) + "\"" : "") + ">" +
      "<div class=\"qc-txn-id\">" + nameLine + "</div>" +
      txnEndHtml(t) +
      (sub || facts ? "<div class=\"qc-txn-sub\">" + sub + facts + "</div>" : "") +
      tbl +
      companyCol +
    "</li>";
  }

  function tradeColsHtml(opts) {
    opts = opts || {};
    const nameLabel = opts.nameLabel || "Name";
    if (opts.tape) {
      return "<li class=\"qc-txn-cols\" aria-hidden=\"true\">" +
        "<span class=\"qc-txn-date\">Date</span>" +
        "<span class=\"qc-txn-id\">" + esc(nameLabel) + "</span>" +
        "<span class=\"qc-txn-company\">" + esc(opts.companyLabel || "Company") + "</span>" +
        "<span class=\"qc-txn-chip\">Txn</span>" +
        "<span class=\"qc-txn-sh\">Shares</span>" +
        "<span class=\"qc-txn-px\">@ Price</span>" +
        "<span class=\"qc-txn-hero\">Value</span>" +
        "<span class=\"qc-txn-held\">% Held</span>" +
      "</li>";
    }
    return "<li class=\"qc-txn-cols\" aria-hidden=\"true\">" +
      "<span class=\"qc-txn-date\">Date</span>" +
      "<span class=\"qc-txn-id\">" + esc(nameLabel) + "</span>" +
      "<span class=\"qc-txn-chip\">Txn</span>" +
      "<span class=\"qc-txn-sh\">Shares</span>" +
      "<span class=\"qc-txn-px\">@ Price</span>" +
      "<span class=\"qc-txn-hero\">Value</span>" +
      "<span class=\"qc-txn-after\">Total Held</span>" +
      "<span class=\"qc-txn-held\">% Held</span>" +
    "</li>";
  }

  function tradeRowHtml(t, opts) {
    opts = opts || {};
    if (opts.variant === "tape") return tapeRowHtml(t, opts);

    const cls = txnClass(t);
    const delta = positionDelta(t);
    let nameHtml = opts.nameHtml || (t.filer ? "<span class=\"qc-txn-name\">" + esc(t.filer) + "</span>" : "");
    const tickerHtml = opts.tickerHtml || "";
    const tagsHtml = opts.tagsHtml || "";
    const extraMeta = (opts.extraMeta || []).filter(Boolean);
    if (opts.withRole) {
      const role = shortRole(t);
      if (role) {
        nameHtml = "<div class=\"qc-txn-id-line\">" + nameHtml +
          "<span class=\"qc-txn-role\">" + esc(role) + "</span></div>";
      }
    }

    const parts = [];
    if (t.trade_date) parts.push("<span class=\"qc-txn-date\">" + esc(prettyDate(t.trade_date)) + "</span>");
    const px = formatPriceQuiet(t.price);
    if (px) parts.push("<span class=\"qc-txn-px\"><span class=\"qc-txn-lbl\">@ </span>" + esc(px) + "</span>");
    const after = formatSharesQuiet(t.shares_after);
    if (after) parts.push("<span class=\"qc-txn-after\"><span class=\"qc-txn-lbl\">After </span>" + esc(after) + "</span>");
    const held = formatHeldQuiet(t.held_pct);
    if (held) parts.push("<span class=\"qc-txn-held " + delta + "\">" + esc(held) + "</span>");
    extraMeta.forEach((html) => parts.push("<span>" + html + "</span>"));

    const meta = dotted(parts);
    const sh = formatSharesQuiet(t.shares);
    const shHtml = sh ? "<span class=\"qc-txn-sh\">" + esc(sh) + "</span>" : "";

    return "<li class=\"qc-txn" + (cls ? " " + cls : "") + "\">" +
      "<div class=\"qc-txn-id\">" + nameHtml + tickerHtml + tagsHtml + "</div>" +
      txnEndHtml(t) +
      (meta.length ? "<div class=\"qc-txn-meta\">" + meta.join("") + "</div>" : "") +
      shHtml +
    "</li>";
  }

  function filedHeading(iso) {
    const d = new Date(String(iso || "") + "T00:00:00");
    if (isNaN(d.getTime())) return "Filed";
    const w = d.toLocaleString("en-US", { weekday: "short" });
    const rest = d.toLocaleString("en-US", { month: "short", day: "numeric" });
    return "Filed " + w + " " + rest;
  }

  function politicianTapeColsHtml(opts) {
    opts = opts || {};
    const last = opts.showLastClose
      ? "<span class=\"qc-txn-last\">Last</span>"
      : "";
    const shares = opts.showShareCols
      ? "<span class=\"qc-txn-sh\">Shares</span>" +
        "<span class=\"qc-txn-after\">Held</span>" +
        "<span class=\"qc-txn-held\">% Held</span>"
      : "";
    return "<li class=\"qc-txn-cols\" aria-hidden=\"true\">" +
      "<span class=\"qc-txn-date\">Date</span>" +
      "<span class=\"qc-txn-id\">Name</span>" +
      "<span class=\"qc-txn-company\">Ticker</span>" +
      "<span class=\"qc-txn-coname\">Company</span>" +
      last +
      "<span class=\"qc-txn-chip\">Trade</span>" +
      shares +
      "<span class=\"qc-txn-hero\">Value</span>" +
    "</li>";
  }

  function politicianTapeRowHtml(t, opts) {
    opts = opts || {};
    const cls = txnClass(t) || ((t && t.side) === "purchase" ? "buy" : (t && t.side) === "sale" ? "sell" : "");
    const code = tapeSymbol(t);
    const company = String((t && t.company) || "");
    const name = String((t && t.filer) || "");
    const nameHref = opts.nameHref || "";
    const tickerHref = opts.tickerHref || "";
    const nameHtml = name
      ? (nameHref
          ? "<a class=\"qc-txn-name\" href=\"" + esc(nameHref) + "\">" + esc(name) + "</a>"
          : "<span class=\"qc-txn-name\">" + esc(name) + "</span>")
      : "";
    const role = String((t && (t.chamber || t.role)) || shortRole(t) || "");
    const co = "<span class=\"qc-txn-tk\">" + esc(code) + "</span>" +
      "<span class=\"qc-txn-co-name\">" + esc(company) + "</span>";
    const companyHtml = tickerHref
      ? "<a class=\"qc-txn-company\" href=\"" + esc(tickerHref) + "\">" + co + "</a>"
      : "<span class=\"qc-txn-company\">" + co + "</span>";

    const lastHtml = opts.showLastClose ? lastCloseHtml(opts.lastClose, code) : "";
    const shareHtml = opts.showShareCols ? shareColsHtml(t) : "";
    const facts = opts.showShareCols ? shareFactsHtml(t) : "";
    const whoParts = [];
    if (code) {
      whoParts.push(tickerHref
        ? "<a class=\"qc-txn-tk\" href=\"" + esc(tickerHref) + "\">" + esc(code) + "</a>"
        : "<span class=\"qc-txn-tk\">" + esc(code) + "</span>");
    }
    if (lastHtml) whoParts.push(lastHtml);
    if (role) whoParts.push("<span class=\"qc-txn-role\">" + esc(role) + "</span>");
    const metaParts = [];
    if (t && t.trade_date) metaParts.push("<span>" + esc(prettyDate(t.trade_date)) + "</span>");
    if (company) {
      metaParts.push(tickerHref
        ? "<a class=\"qc-txn-co\" href=\"" + esc(tickerHref) + "\">" + esc(company) + "</a>"
        : "<span class=\"qc-txn-co\">" + esc(company) + "</span>");
    }
    const who = dotted(whoParts);
    const meta = dotted(metaParts);
    const sub = (who.length ? "<div class=\"qc-txn-who\">" + who.join("") + "</div>" : "") +
      (meta.length ? "<div class=\"qc-txn-meta\">" + meta.join("") + "</div>" : "");

    const extras = [];
    if (opts.selected) extras.push("on");
    if (opts.preview) extras.push("preview");
    if (opts.extraClass) extras.push(opts.extraClass);
    const extraCls = extras.length ? " " + extras.join(" ") : "";
    return "<li class=\"qc-txn qc-txn-tape" + (cls ? " " + cls : "") + extraCls + "\"" +
      (t && t.id ? " data-id=\"" + esc(t.id) + "\"" : "") +
      (opts.holdKey ? " data-key=\"" + esc(opts.holdKey) + "\"" : "") +
      " tabindex=\"0\" role=\"button\" aria-pressed=\"" + (opts.selected ? "true" : "false") + "\">" +
      "<div class=\"qc-txn-id\"><div class=\"qc-txn-id-line\">" +
        nameHtml +
        (role ? "<span class=\"qc-txn-role\">" + esc(role) + "</span>" : "") +
      "</div></div>" +
      "<div class=\"qc-txn-end\">" +
        "<span class=\"qc-txn-chip\">" + esc(txnLabel(t) || sideLabel(t && t.side)) + "</span>" +
        "<span class=\"qc-txn-hero\">" + esc(formatAmountRange(t && t.amount) || (Number(t && t.value) ? formatMoney(Number(t.value)) : "—")) + "</span>" +
      "</div>" +
      (sub || facts ? "<div class=\"qc-txn-sub\">" + sub + facts + "</div>" : "") +
      "<div class=\"qc-txn-tbl\"><span class=\"qc-txn-date\">" + esc(prettyDate(t && t.trade_date)) + "</span>" + lastHtml + shareHtml + "</div>" +
      companyHtml +
    "</li>";
  }

  function compareFiledDesc(a, b) {
    const fd = String(b.filed_date || "").localeCompare(String(a.filed_date || ""));
    if (fd) return fd;
    const td = String(b.trade_date || "").localeCompare(String(a.trade_date || ""));
    if (td) return td;
    return String(b.id || "").localeCompare(String(a.id || ""));
  }

  function compareAddedDesc(a, b) {
    const ad = String(b.added || "").localeCompare(String(a.added || ""));
    if (ad) return ad;
    return compareFiledDesc(a, b);
  }

  function takeFiledDayPage(sorted, opts) {
    opts = opts || {};
    const page = Number(opts.page) > 0 ? Number(opts.page) : 120;
    const deskMax = Number(opts.deskMax) > 0 ? Number(opts.deskMax) : 300;
    const minFilers = Number(opts.minFilers) > 0 ? Number(opts.minFilers) : 6;
    const singleCap = Number(opts.singleFilerCap) > 0 ? Number(opts.singleFilerCap) : 16;
    if (!opts.desktop) return sorted.slice(0, page);
    const out = [];
    let i = 0;
    while (i < sorted.length && out.length < deskMax) {
      const key = String(sorted[i].filed_date || "");
      const group = [];
      while (i < sorted.length && String(sorted[i].filed_date || "") === key) {
        group.push(sorted[i++]);
      }
      const room = deskMax - out.length;
      const dayFilers = new Set(group.map((t) => t.filer_id)).size;
      const cap = (dayFilers === 1 && group.length > singleCap) ? singleCap : group.length;
      out.push.apply(out, group.slice(0, Math.min(cap, room)));
      const filers = new Set(out.map((t) => t.filer_id)).size;
      const days = new Set(out.map((t) => t.filed_date)).size;
      if (out.length >= page && filers >= minFilers) break;
      if (out.length >= page && days >= 5 && filers >= 2) break;
    }
    return out;
  }

  function sliceTapeRows(rows, opts) {
    opts = opts || {};
    const list = rows || [];
    const page = Number(opts.page) > 0 ? Number(opts.page) : 120;
    const deskMax = Number(opts.deskMax) > 0 ? Number(opts.deskMax) : 300;
    const singleCap = Number(opts.singleFilerCap) > 0 ? Number(opts.singleFilerCap) : 16;
    const sortAdded = opts.sort === "added";
    if (opts.showAll) {
      const all = list.slice().sort(sortAdded ? compareAddedDesc : compareFiledDesc);
      return { rows: all, pinCount: sortAdded ? all.length : 0 };
    }
    if (sortAdded) {
      const sorted = list.slice().sort(compareAddedDesc);
      const cap = opts.desktop ? deskMax : page;
      const vis = sorted.slice(0, cap);
      return { rows: vis, pinCount: vis.length };
    }
    let pin = [];
    let skipIds = new Set();
    const pinHours = opts.pinLandedHours;
    if (pinHours) {
      const landed = list.filter((t) => isLanded(t, pinHours)).sort(compareAddedDesc);
      skipIds = new Set(landed.map((t) => t.id));
      const per = {};
      landed.forEach((t) => {
        const id = t.filer_id || t.filer || "";
        per[id] = (per[id] || 0) + 1;
        if (per[id] <= singleCap) pin.push(t);
      });
      if (pin.length > page) pin = pin.slice(0, page);
    }
    const rest = list.filter((t) => !skipIds.has(t.id)).sort(compareFiledDesc);
    if (pin.length >= page) {
      return { rows: pin, pinCount: pin.length };
    }
    if (!opts.desktop) {
      return { rows: pin.concat(rest.slice(0, page - pin.length)), pinCount: pin.length };
    }
    const restTake = takeFiledDayPage(rest, {
      desktop: true,
      page: Math.max(1, page - pin.length),
      deskMax: Math.max(0, deskMax - pin.length),
      minFilers: opts.minFilers,
      singleFilerCap: singleCap
    });
    return { rows: pin.concat(restTake), pinCount: pin.length };
  }

  function politicianTapeListHtml(rows, opts) {
    opts = opts || {};
    const empty = opts.empty || "No trades on the 3-year tape.";
    if (!rows || !rows.length) {
      return politicianTapeColsHtml(opts) + "<li class=\"muted\">" + esc(empty) + "</li>";
    }
    const sorted = rows.slice().sort((a, b) => {
      const fd = String(b.filed_date || "").localeCompare(String(a.filed_date || ""));
      if (fd) return fd;
      const td = String(b.trade_date || "").localeCompare(String(a.trade_date || ""));
      if (td) return td;
      return String(b.id || "").localeCompare(String(a.id || ""));
    });
    const page = Number(opts.limit) > 0 ? Number(opts.limit) : 0;
    let vis = sorted;
    if (page && sorted.length > page) {
      const idx = opts.selectedId ? sorted.findIndex((t) => t.id === opts.selectedId) : -1;
      vis = (idx >= page) ? sorted : sorted.slice(0, page);
    }
    const groups = [];
    vis.forEach((t) => {
      const key = t.filed_date || "";
      const last = groups[groups.length - 1];
      if (last && last.key === key) last.rows.push(t);
      else groups.push({ key: key, rows: [t] });
    });
    const namePage = opts.namePage || "politician.html";
    const tickerPage = opts.tickerPage || "ticker.html";
    const blocks = groups.map((g) => {
      const rowHtml = g.rows.map((t) => {
        const nameHref = opts.nameSelf
          ? ""
          : (t.filer_id ? namePage + "?id=" + encodeURIComponent(t.filer_id) : "");
        const code = tapeSymbol(t);
        const tickerHref = opts.tickerSelf
          ? ""
          : (isChartTicker(code) ? tickerPage + "?t=" + encodeURIComponent(code) : "");
        const holdKey = opts.holdKeyOf ? opts.holdKeyOf(t) : "";
        return politicianTapeRowHtml(t, {
          nameHref: nameHref,
          tickerHref: tickerHref,
          holdKey: holdKey,
          selected: !!(opts.selectedId && t.id === opts.selectedId),
          preview: !!(opts.previewId && t.id === opts.previewId && t.id !== opts.selectedId),
          showLastClose: !!opts.showLastClose,
          lastClose: opts.showLastClose ? lastCloseLookup(opts.lastCloseByCode, t) : null,
          showShareCols: !!opts.showShareCols
        });
      }).join("");
      return "<li class=\"day\"><h2>" + esc(filedHeading(g.key)) + "</h2></li>" + rowHtml;
    });
    return politicianTapeColsHtml(opts) + blocks.join("");
  }

  global.QC = {
    esc: esc,
    isBond: isBond,
    isOptionLike: isOptionLike,
    optionMeta: optionMeta,
    optionTag: optionTag,
    assetKind: assetKind,
    isFilingError: isFilingError,
    sideLabel: sideLabel,
    isAward: isAward,
    isExercise: isExercise,
    isSalePost: isSalePost,
    isMarketBuy: isMarketBuy,
    isMarketSale: isMarketSale,
    isTapeTxn: isTapeTxn,
    txnLabel: txnLabel,
    txnClass: txnClass,
    txnChipLabel: txnChipLabel,
    titleCaseName: titleCaseName,
    shortRole: shortRole,
    filingLagDays: filingLagDays,
    formatHeldPct: formatHeldPct,
    formatSharesQuiet: formatSharesQuiet,
    formatPriceQuiet: formatPriceQuiet,
    formatHeldQuiet: formatHeldQuiet,
    tradeHero: tradeHero,
    positionDelta: positionDelta,
    tradeRowHtml: tradeRowHtml,
    tradeColsHtml: tradeColsHtml,
    tapeRowHtml: tapeRowHtml,
    filedHeading: filedHeading,
    politicianTapeColsHtml: politicianTapeColsHtml,
    politicianTapeRowHtml: politicianTapeRowHtml,
    TAPE_PAGE: 120,
    politicianTapeListHtml: politicianTapeListHtml,
    compareFiledDesc: compareFiledDesc,
    compareAddedDesc: compareAddedDesc,
    sliceTapeRows: sliceTapeRows,
    isEtfLike: isEtfLike,
    isChartTicker: isChartTicker,
    amountHigh: amountHigh,
    formatAmountRange: formatAmountRange,
    formatMoney: formatMoney,
    formatQuote: formatQuote,
    lastCloseFromFile: lastCloseFromFile,
    lastCloseLookup: lastCloseLookup,
    lastCloseHtml: lastCloseHtml,
    fillLastCloseMap: fillLastCloseMap,
    applyLastCloses: applyLastCloses,
    signedMoney: signedMoney,
    prettyDate: prettyDate,
    tapeSymbol: tapeSymbol,
    threeYearCutoff: threeYearCutoff,
    cleanAsset: cleanAsset,
    issuerName: issuerName,
    isWeakIssuer: isWeakIssuer,
    displayIssuer: displayIssuer,
    OVERLAP_RULES: OVERLAP_RULES,
    overlapHit: overlapHit,
    SECTORS: SECTORS,
    sectorOf: sectorOf,
    enrich: enrich,
    tickerInfo: tickerInfo,
    resolveTickerFromName: resolveTickerFromName,
    asOfLabel: asOfLabel,
    parseAdded: parseAdded,
    isLanded: isLanded,
    lagDays: lagDays,
    BAD_TICKERS: BAD_TICKERS
  };
})(window);
