/* Shared tape helpers for Quantity Capital pages. */
(function (global) {
  const BAD_TICKERS = { LLC: 1, THE: 1, AND: 1, INC: 1, CORP: 1, CLASS: 1, NONE: 1, NA: 1 };

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

  function assetKind(t) {
    const type = (t.asset_type || "").toLowerCase();
    const asset = (t.asset || "").toLowerCase();
    if (isOptionLike(t)) return "option";
    if (type.includes("bond") || type.includes("municipal") || /rate\/coupon/.test(asset)) return "bond";
    if (type.includes("stock")) return "stock";
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
    if (side === "exchange") return "Exch";
    return side || "";
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

  function issuerName(code, raw) {
    const c = String(code || "").toUpperCase();
    let s = String(raw || "").replace(/\s+/g, " ").trim();
    if (!s || s === "—") return c && c !== "—" ? c : "";
    s = (s.split(/\s*>\s*/).pop() || s).trim();
    const brokerStock = BROKER_ISSUERS[c] && BROKER_ISSUERS[c].test(s)
      && !/\b(ira|roth|trust account|brokerage|select uma|unified management|joint tbe)\b/i.test(s);
    if (brokerStock) {
      return s.replace(/\s+(Common Stock.*|Class [A-Z].*|Ordinary Shares.*)$/i, "").replace(/\s*-\s*$/, "").trim() || c;
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
    s = s.replace(/\s+(Common Stock.*|Class [A-Z].*|Ordinary Shares.*)$/i, "");
    s = s.replace(/\s*-\s*$/, "").trim();
    if (!s || /^(common stock|class [a-z]|llc|inc|corp)$/i.test(s)) return c && c !== "—" ? c : "";
    if (/^[A-Z][A-Z0-9.]{0,6}$/.test(s) && s.toUpperCase() !== c) return c && c !== "—" ? c : s;
    return s;
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

  function enrich(t, file) {
    const lookup = (file && file.tickers) || {};
    const extra = ((file && file.assets) || {})[t.asset] || {};
    const opt = optionMeta(t);
    const code = (t.ticker || extra.ticker || opt.under || "").toUpperCase();
    const meta = lookup[code] || {};
    const bondLike = isBond(t);
    const name = bondLike
      ? (t.asset || meta.name || "—")
      : (issuerName(code, meta.name || t.asset) || "—");
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
    const code = (t.ticker || extra.ticker || opt.under || "").toUpperCase();
    const meta = lookup[code] || {};
    const bondLike = isBond(t);
    const name = bondLike ? (t.asset || meta.name || "—") : (issuerName(code, meta.name || cleanAsset(t.asset)) || "—");
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
    return "Data of " + date + ", " + time + " ET";
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
    isChartTicker: isChartTicker,
    amountHigh: amountHigh,
    formatAmountRange: formatAmountRange,
    formatMoney: formatMoney,
    formatQuote: formatQuote,
    signedMoney: signedMoney,
    prettyDate: prettyDate,
    threeYearCutoff: threeYearCutoff,
    cleanAsset: cleanAsset,
    issuerName: issuerName,
    OVERLAP_RULES: OVERLAP_RULES,
    overlapHit: overlapHit,
    SECTORS: SECTORS,
    sectorOf: sectorOf,
    enrich: enrich,
    tickerInfo: tickerInfo,
    asOfLabel: asOfLabel,
    parseAdded: parseAdded,
    isLanded: isLanded,
    lagDays: lagDays,
    BAD_TICKERS: BAD_TICKERS
  };
})(window);
