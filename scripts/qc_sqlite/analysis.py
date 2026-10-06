"""Signals book (analysis.json) — build_analysis.py rules + Jev ship/drop.

Deterministic scaffolding from the collector. Jev decides ship vs drop, action
when the setup is fuzzy, conflict framing, and borderline confidence.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jev
from paths import ANALYSIS_JSON, BIOS, DB_PATH, EXPORT_DIR, TRADES_LITE

if str(HERE.parent) not in sys.path:
    sys.path.insert(0, str(HERE.parent))
from qc_io import atomic_write_text  # noqa: E402
from tells import last_us_session_close, peek_collected, prices_dir

SKIP = {
    "LP", "SPCX", "GOOGM", "GOOGN", "SPY", "QQQ", "QQQM", "VOO", "VTI", "IWM", "DIA",
    "IVV", "VEA", "VWO", "ARKK", "TLT", "BND", "AGG", "XLF", "XLK", "XLE", "XLV",
    "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "SMH", "SOXX", "IJR", "IJH", "RSP",
    "VGT", "VOOG", "VUG", "VTV",
}
MEGA = {
    "AAPL", "MSFT", "NVDA", "AMZN", "GOOGL", "GOOG", "META", "TSLA", "BRK",
    "BRK.B", "BRKB", "AVGO", "JPM", "LLY", "UNH", "V", "MA",
}
BAD = {"LLC", "THE", "AND", "INC", "CORP", "CLASS", "NONE", "NA"}
HALF = 10
WIN = 90
BOOK_CAP = 6
AVOID_CAP = 5
JEV_BOOK_POOL = 10
JEV_AVOID_POOL = 8
# Refill: when Jev drops play names, review the next-ranked non-avoid names until
# JEV_BOOK_TARGET play names have passed review or the list runs out.
# JEV_REFILL_MAX caps the extra Jev calls per run, so the cost stays bounded.
JEV_BOOK_TARGET = 10
JEV_REFILL_MAX = 20

DISCLAIMER = (
    "Not a recommendation. Official ranges are not share counts. Weekly/monthly "
    "from Yahoo closes on file. Regenerated on every site update."
)

RULES = [
    {
        "seat": re.compile(r"environment and public works|clean air, climate, and nuclear|energy and natural resources|energy and commerce|natural resources|interior, environment|\benergy\b", re.I),
        "industry": re.compile(r"electric services|petroleum refining|crude petroleum|natural gas|coal mining|metal mining|gas transmission|oil.{0,12}gas|petroleum|pipeline|drilling oil", re.I),
        "name": re.compile(r"constellation energy|chevron|exxon|bloom energy|nextera|sempra|dominion|exelon|bwx technologies", re.I),
        "tickers": re.compile(r"^(BP|XOM|CVX|COP|OXY|VLO|NEE|DUK|SO|D|EXC|AEP|SRE|EQT|CEG|BE|BWXT)$"),
        "why": "Energy / environment",
    },
    {
        "seat": re.compile(r"labor, health|health and human|public health", re.I),
        "industry": re.compile(r"pharmaceutical|hospital & medical|biological product|x-ray", re.I),
        "name": re.compile(r"eli lilly|unitedhealth|pfizer|ge healthcare|johnson & johnson", re.I),
        "tickers": re.compile(r"^(UNH|LLY|JNJ|PFE|GEHC|ABT|AMGN)$"),
        "why": "Health",
    },
    {
        "seat": re.compile(r"telecommunication|consumer protection, technology|data privacy", re.I),
        "industry": re.compile(r"semiconductor|prepackaged software|cable & other pay|telephone|computer programming", re.I),
        "name": re.compile(r"alphabet|google|meta|apple|microsoft|nvidia|intel|broadcom", re.I),
        "tickers": re.compile(r"^(AAPL|MSFT|NVDA|INTC|GOOGL|GOOG|META|AVGO|T|AMD|MU|AMAT|CRWD)$"),
        "why": "Tech / telecom",
    },
    {
        "seat": re.compile(r"aviation, space|department of defense|armed services|intelligence", re.I),
        "industry": re.compile(r"aircraft|aerospace|guided missile|ordnance|search, detection", re.I),
        "name": re.compile(r"boeing|lockheed|transdigm|palantir|bwx technologies", re.I),
        "tickers": re.compile(r"^(BWXT|LMT|NOC|GD|RTX|BA|LHX|TDG|PLTR|HII)$"),
        "why": "Defense / aviation",
    },
    {
        "seat": re.compile(r"surface transportation|transportation and infrastructure", re.I),
        "industry": re.compile(r"railroad|trucking|air transportation|transportation services", re.I),
        "name": re.compile(r"union pacific|fedex|ups|c\.?h\.? robinson", re.I),
        "tickers": re.compile(r"^(UNP|CSX|FDX|UPS|CHRW|T|IBP)$"),
        "why": "Transportation / freight",
    },
    {
        "seat": re.compile(r"agriculture|nutrition|forestry", re.I),
        "industry": re.compile(r"agriculture|meat packing|grain mill|farm machinery", re.I),
        "name": re.compile(r"deere|tyson|general mills", re.I),
        "tickers": re.compile(r"^(DE|TSN|GIS|ADM)$"),
        "why": "Agriculture",
    },
]

ANALYSIS_DDL = """
CREATE TABLE IF NOT EXISTS analysis_candidates (
  ticker TEXT PRIMARY KEY,
  name TEXT,
  rule_action TEXT,
  action TEXT,
  score REAL,
  why TEXT,
  last_px REAL,
  px_asof TEXT,
  buy_px REAL,
  chg_since_buy REAL,
  last_buy TEXT,
  n_buyers INTEGER,
  buy_high INTEGER,
  heat_rank INTEGER,
  heat REAL,
  landed INTEGER,
  landed_at TEXT,
  lag_days INTEGER,
  conflict INTEGER,
  conflict_why_json TEXT,
  whale INTEGER,
  cluster INTEGER,
  buyers_json TEXT,
  weekly_json TEXT,
  monthly_json TEXT,
  invalidation REAL,
  flags_json TEXT,
  jev_ship TEXT,
  jev_action TEXT,
  jev_action_confidence REAL,
  jev_borderline REAL,
  jev_conflict_noul REAL,
  jev_model TEXT,
  shipped INTEGER NOT NULL DEFAULT 0,
  list TEXT
);
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def chart(code: str) -> bool:
    c = (code or "").upper()
    return bool(c and c != "—" and re.match(r"^[A-Z][A-Z0-9.]{0,6}$", c) and c not in BAD)


def paren_ticker(asset: str) -> str:
    m = re.search(r"\(([A-Z]{1,5})\)", asset or "")
    return m.group(1) if m else ""


def is_bond(t: dict) -> bool:
    typ = (t.get("asset_type") or "").lower()
    asset = (t.get("asset") or "").lower()
    return "bond" in typ or "municipal" in typ or bool(re.search(r"rate/coupon", asset))


def is_opt(t: dict) -> bool:
    typ = (t.get("asset_type") or "").lower()
    asset = (t.get("asset") or "").lower()
    return "option" in typ or bool(re.search(r"exercised|call option|put option|strike pric|flex euro|call/|put/|@\s*\d", asset))


def amount_high(amount: str) -> int:
    nums = re.findall(r"\$[\d,]+", amount or "")
    if not nums:
        return 0
    return int(nums[-1].replace("$", "").replace(",", ""))


def code_of(t: dict) -> str:
    c = (t.get("ticker") or "").upper()
    if not chart(c):
        p = paren_ticker(t.get("asset") or "")
        if p:
            c = p
    return c


# ---- Filing-name cleanup. Self-contained on purpose: no build-backtest.py import. ----
_WS = re.compile(r"\s+")
_NOTE_MARK = re.compile(r"(?<!\S)([DCL]):(?:\s+|$)")
# Lead-ins that open a "D:" note and end right where the company name starts.
_NOTE_LEADIN = re.compile(
    r"^(?:account closing|(?:part of )?(?:advisor-driven )?(?:quarterly )?(?:portfolio )?rebalanc(?:e|ing)|"
    r"reinvestment of distributions?|(?:call|put) options?|expires?\s+[\d/]+|corporate bond|municipal bond|treasury bond|"
    r"mutual fund|dividend reinvestment|automatic reinvestment|reinvest(?:ed)? shares|full liquidation|"
    r"partial liquidation|closed position|existing holding|adr stock|eft|(?:sell|buy) to (?:open|close)|"
    r"asset (?:inherited|acquired)(?:\s+[A-Z][a-z]{2}\.?\s+\d{4})?|"
    r"ticker\s+[\w.]+(?:\s+[A-Z]{2})?|[\d.,]+\s+shares?(?:/\w+)?)[.:,]?\s+",
    re.I,
)
# Sentence end inside a note: "FULL LIQUIDATION. Lazard, Inc." -> split after the period,
# but not after an initial or a corporate abbreviation (D.R. Horton, Inc., Corp.).
_ABBR = {"inc", "corp", "co", "cos", "ltd", "jr", "sr", "st", "no", "mr", "mrs", "dr", "bros", "intl", "hldgs", "plc", "llc", "lp", "sa", "nv", "ag", "u.s", "s.a", "n.v",
         "sub", "vot", "svs", "ser", "pfd", "dep", "shs", "ord", "cl",
         "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
_SENT = re.compile(r"(?<=[\w)%/])\.\s+(?=[A-Z0-9]|i[A-Z])")
# Words a company name practically never has; their presence means the note is still there.
_NOTE_WORDS = re.compile(
    r"\b(?:shares?|options?|strike|expir\w*|sold|purchased|bought|reinvest\w*|rebalanc\w*|inherited|"
    r"contribution|received|surrendered|exercised|liquidation|professionally|position|terms|called|"
    r"account|ira|401\(?k\)?|brokerage|cusip|ticker|managed|pooled|convertible|due|operates?|engaged)\b|own/operate|"
    r"[;$%+]|\bmore\b|\d{1,3}(?:,\d{3})+|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b|\b\d{4}-\d{2}-\d{2}\b",
    re.I,
)
# "Palo Alto, CA" / "Caldwell, ID, US": a place, not a company.
_PLACE = re.compile(
    r"\b[A-Z][a-z]+,\s*(?:A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[ADEINOST]|N[CDEHJMVY]|O[HKR]|PA|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b(?!\.)"
)
_SUFFIX = re.compile(
    r"(?:\s*-\s*|\s+)(?:(?:new )?common stock|common shares?(?: of beneficial interest)?|ordinary shares?|registered ordinary shares|"
    r"registered|american depositary shares?|(?:un)?sponsored(?: adr)?|unsp/adr|adr|ads|cmn(?:\s+class\s?\w)?|class [a-c](?: sub\.? ?vot\.?(?: common stock)?| common stock| ordinary shares)?|"
    r"see endnote|\[\w{1,3}\]|\((?!the\))(?:[A-Z][A-Z.\-]{0,6}|\$?[A-Z]{1,5}\$?\w?)\)|[;_]+)\s*$",
    re.I,
)
_TRAILING_TERMS = re.compile(r"\s+(?:option type|rate/coupon|matures|strike price|expires):.*$", re.I)
# Account labels in front of the company. Each needs an account word after the
# broker, so "Fidelity National ...", "Morgan Stanley", "UBS Group AG" stay whole.
_BROKER = r"(?:charles schwab|schwab|chase|stephens|fidelity(?: investments)?|vanguard|merrill lynch|merrill|edward jones|jp ?morgan|j\.p\. morgan|wells fargo|morgan stanley|ubs|td ameritrade|e\*?trade|pershing llc|cetera|lpl|northwestern mutual|rockefeller capital management)"
_ACCT_WORD = r"(?:select uma|uma|active assets?|unified management|advantage|portfolio management|trust|brokerage|investment|investments|managed|advisors?|rollover|traditional|roth|sep|simple|inherited|joint(?: tbe)?|ira|401\(?k\)?|one|jm|account|tax efficient core|-)"
_ACCOUNT_PREFIX = re.compile(
    rf"^{_BROKER}(?:\s*-\s*|\s+)(?:{_ACCT_WORD}(?:\s+|\s*-\s*))*(?:{_ACCT_WORD})(?:\s*#\s*\d+|\s+\d{{1,4}}|\s*\(\d+\))?"
    rf"(?:\s*\([^)]{{1,20}}\))?(?:\s*-)?\s+(?=\S)",
    re.I,
)
_OTHER_PREFIX = re.compile(
    r"^(?:(?:joint ownership\s+)?lpl account|morgan stanley ira(?:\s*-\s*\S+)?|tacs r3k|aperio group llc|"
    r"(?:select uma account|uma account|account)\s*#\s*\d+|joint brokerage|ubs financial services(?:,? inc\.?)?(?:\s+\d{1,4})?|"
    r"[A-Z][\w&.']*(?:\s+[A-Z][\w&.']*){0,3}\s*-\s*(?:brokerage|gst exempt trust)(?:\s*#\s*\d+)?|"
    r"\d{4}\s+(?:family\s+)?trust|rollover|"
    r"(?:charles\s+)?schwab\s+\d{3,4}|cetera|pershing llc|rockefeller capital management(?:\s*\(\d+\))?|tlh(?:\s+adr)?|livtr(?:\s+\d{6,}[A-Z]{0,3})?|"
    r"(?:[A-Z][\w.'&\u2019-]*\s+(?:-\s+)?){1,6}account(?:\s*#?\s*\d+|\s+[A-Z](?=\s))?)\s+(?=\S)",
    re.I,
)
# Person's retirement account: "Chris IRA ...", "Kevin Hern Traditional IRA ...".
_PERSON_IRA = re.compile(
    r"^(?:[A-Z][\w.'\u2019-]*\s+){1,4}(?:(?:traditional|roth|rollover|sep|simple|inherited|spousal)\s+)*(?:IRA|401\(?[kK]\)?)\s+(?=\S)"
    r"|^[A-Z][a-z]+\s+(?:SEP|Roth)\s+(?=[A-Z])"
)
# Holder trust in front of the company: "John A. James Children's Trust ICON plc", "Trust One NVIDIA ...".
_HOLDER_TRUST = re.compile(
    r"^(?:trust\s+(?:one|two|three|#?\d+)|trust\s*-\s*\w+|"
    r"[^>]{0,60}?\b(?:family|revocable|living|irrevocable|insurance|children\u2019?'?s?|grandchildren|gst|exempt|unit|charitable|crt|\w+[\u2019']s|\d{4})\b[^>]{0,30}?\b(?:trust|foundation|partnership)(?:\s*#\s*\d+)?)"
    r"(?:\s*[-:]\s*|\s+)(?![,&]|(?:corp|corporation|inc|co|company|series|fund|etf|units?|shares|of|reit|realty)\b)(?=[A-Z0-9]|i[A-Z])",
    re.I,
)
# Account/holder words that must never be left in a display name.
_HOLDER_LEFT = re.compile(
    r"\b(?:ira|401\(?k\)?|brokerage|account|revocable|rollover|living trust|family trust|unit trust|insurance trust|"
    r"trust one|tbe|jtwros|ttee|family foundation|family partnership|grandchildren|children\u2019?'?s trust|fbo|u/a|dtd)\b",
    re.I,
)
_PTR_AMOUNT = re.compile(r"\$[\d,]+\s*-\s*\$[\d,]+(?:\s|$)")
_BANK_PREFIX = re.compile(r"^(?:bank of america|morgan stanley|ubs)\s+(?=\S)", re.I)
# What follows the bank word when the bank itself is the issuer ("UBS Group AG", "Bank of America Corp").
_BANK_OWN = re.compile(
    r"^(?:group|ag|corp|corporation|inc|co|company|financial|finance|funding|&|bank|n\.?a\.?|securities|preferred|pfd|dep|"
    r"depositary|wealth|private|smart|ser|series|london|bonds?|notes?|\d{1,2}(?:\.\d+)?%)(?:\b|$)",
    re.I,
)


def _sentence_tail(t: str) -> str:
    """Text after the last real sentence end ("FULL LIQUIDATION. Lazard, Inc." -> "Lazard, Inc.")."""
    cut = 0
    for m in _SENT.finditer(t):
        word = re.findall(r"[\w.]+$", t[: m.start()])
        w = (word[0] if word else "").lower().rstrip(".")
        if len(w) < 2 or w in _ABBR or re.fullmatch(r"(?:[a-z]\.)+[a-z]?", w):
            continue
        cut = m.end()
    return t[cut:]


def _strip_suffixes(s: str) -> str:
    s = _TRAILING_TERMS.sub("", s)
    for _ in range(6):
        new = re.sub(r"(?:(?<=\.)\s*cmn)?[\s_\-\u2013]*$", "", s, flags=re.I)  # "INC.CMN", "CORP CMN_", "Inc. -"
        new = _SUFFIX.sub("", new).strip()
        if new == s or not new:
            break
        s = new
    return s.strip()


def _company_after_note(t: str) -> str | None:
    """The company that ends a free-text note, or None when it can't be told apart."""
    t = t.split(";")[-1].strip()  # "Call options; Strike price $220; Expires 06/20/2025 Moderna, Inc."
    for _ in range(3):
        new = _NOTE_LEADIN.sub("", t)
        if new == t:
            break
        t = new
    t = _sentence_tail(t)
    t = _NOTE_LEADIN.sub("", t)
    t = _strip_suffixes(t)
    if not _SUFFIX.sub("", " " + t).strip():  # only "Common Stock" / "ADR" left
        return None
    if not t or len(t) > 80 or len(t.split()) > 9 or _NOTE_WORDS.search(t) or _PLACE.search(t):
        return None
    t = re.sub(r"^(?=[0-9A-Z]{9}\s)(?=\S*\d)\S{9}\s+", "", t)  # leading CUSIP
    if not re.match(r"[A-Z]|[a-z][A-Z]|\d+[A-Za-z]", t) or not re.search(r"[A-Za-z]{2}", t):
        return None
    return t


_COMPANY_END = re.compile(
    r"(?:common stock|ordinary shares?|common shares?|class [a-c]|\b(?:inc|corp|corporation|incorporated|plc|ltd|limited|"
    r"company|co|n\.?v|s\.?a|ag|se|holdings?|group))\.?\s*-?\s*$",
    re.I,
)
_HOLDER_WORDS = re.compile(r"\b(?:ira|trust|account|brokerage|foundation|partnership|401\(?k\)?|portfolio)\b", re.I)


def _head_company(head: str) -> str | None:
    """The name in front of a trailing note ("Intel Corporation - Common Stock D: Purchased ...")."""
    head = _strip_prefixes(head.strip())  # "Trust 1 Accenture plc ..." -> "Accenture plc ..."
    if not head or not _COMPANY_END.search(head) or _HOLDER_WORDS.search(head):
        return None
    return head


def _strip_prefixes(s: str) -> str:
    for _ in range(3):
        before = s
        for rx in (_ACCOUNT_PREFIX, _OTHER_PREFIX, _PERSON_IRA, _HOLDER_TRUST):
            rest = rx.sub("", s, count=1)
            if rest != s and re.search(r"[A-Za-z]{2}", rest):
                s = rest
        rest = _BANK_PREFIX.sub("", s, count=1)
        if rest != s and not _BANK_OWN.match(rest) and _SUFFIX.sub("", " " + rest).strip():
            s = rest
        s = re.sub(r"^[\s\-\u2013\u2014]+", "", s)
        s = re.sub(r"^[A-Z]{1,4}\d{2,5}\s+(?=\S)", "", s)
        s = re.sub(r"^\d{6,}\s+(?=[A-Za-z])", "", s)  # account number
        if s == before:
            break
    return s


def clean_name(name: str, code: str) -> str:
    """Company name for display, with filing junk removed.

    House/Senate asset strings put the owner, account and free-text notes
    ("D: ...", "C: ...", "L: ...") in front of the company. The company is the
    tail. When the tail can't be told apart from the note, use the ticker:
    never show an account holder or a note as the name.
    """
    code = code or ""
    s = _WS.sub(" ", name or "").strip()
    amount = _PTR_AMOUNT.search(s)
    if amount and _NOTE_MARK.search(s[: amount.start()]):
        # Several PTR rows glued together behind a note: only the leading name is trustworthy.
        head = _head_company(s[: _NOTE_MARK.search(s).start()])
        if head is None:
            return code
        s = head
    s = (s.split(">")[-1] or s).strip()
    # PTR table row: "P 01/02/2025 01/03/2025 $1,001 - $15,000 Apple Inc." -> text after the amount range
    tail = _PTR_AMOUNT.split(s)[-1].strip()
    if tail != s and len(re.findall(r"[A-Za-z]", tail)) >= 2:
        s = tail
    elif tail != s:  # nothing after the amount: drop the "[GS] P 04/16/2025 ... $1,001 - $15,000" trailer
        s = re.sub(r"\s*(?:\[[A-Z]{2}\]\s*)?\b[PSE]\s+\d{2}/\d{2}/\d{4}.*$", "", s).strip()
    ptr_codes = re.findall(r"\(([A-Z][A-Z.]{0,5})\)\s*\[[A-Z]{2}\]", s)
    if ptr_codes and code.upper() not in ptr_codes:
        # "Shares (TME) [ST] Tesla, Inc.": the end of another row, then ours
        after = re.split(r"\[[A-Z]{2}\]\s*", s)[-1].strip()
        if not re.search(r"[A-Za-z]{2}", after) or re.match(r"[PSE](?:\s|$)", after):
            return code
        s = after
    s = re.sub(r"\s*\[[A-Z]{2}\]\s+[PSE]$", "", s)  # cut-off "(PLTR) [ST] P"
    s = re.sub(r"^[\s\-\u2013\u2014\u2022*.|;,'\"\u2018\u2019\u201c\u201d]+", "", s)
    marks = list(_NOTE_MARK.finditer(s))
    if marks:
        last = marks[-1]
        tail = None if last.group(1) == "L" else _company_after_note(s[last.end():])  # a location is never the company
        if tail is None:
            tail = _head_company(s[: marks[0].start()])
        if tail is None:
            return code
        s = tail
    elif _NOTE_WORDS.search(s) or _HOLDER_LEFT.search(s) or len(s) > 80 or re.match(r"[a-z]{2,}[\s,.]", s):
        tail = _company_after_note(_sentence_tail(s))
        if tail and tail != s and len(tail) >= 5:
            s = tail  # "... this is an Inherited IRA. Apple Inc." -> "Apple Inc."
    s = _strip_prefixes(s)
    s = _strip_suffixes(s)
    s = re.sub(r"^[\s\-\u2013\u2014]+", "", s)
    if not re.search(r"[A-Za-z]{2}", s):
        return code
    if _HOLDER_LEFT.search(s):
        return code  # an account/holder label we could not peel off
    if s.upper() == "TLH" and code.upper() != "TLH":  # tax-loss-harvest sleeve, not a company
        return code
    return s


def public_stock(t: dict, code: str) -> bool:
    if code in SKIP or not chart(code):
        return False
    typ = (t.get("asset_type") or "").lower()
    if re.search(r"non-public|municipal|corporate bond|\bbond\b|other", typ):
        return False
    if is_bond(t) or is_opt(t):
        return False
    blob = ((t.get("asset") or "") + " " + (t.get("ticker") or "")).lower()
    if re.search(r"exchange traded|\betf\b|mandatory convertible|private equity", blob):
        return False
    return True


def seats_of(bio: dict) -> list[str]:
    out = []
    for s in (bio.get("committees") or []) + (bio.get("subcommittees") or []):
        out.append(s.get("name") or "")
    return out


def overlap_why(code: str, name: str, industry: str, seats: list[str]) -> list[str]:
    hits = []
    blob = f"{code} {name} {industry}".lower()
    for rule in RULES:
        if not any(rule["seat"].search(s or "") for s in seats):
            continue
        ok = False
        if rule.get("tickers") and rule["tickers"].search(code):
            ok = True
        if rule.get("industry") and rule["industry"].search(industry or ""):
            ok = True
        if rule.get("name") and rule["name"].search(blob):
            ok = True
        if ok and rule["why"] not in hits:
            hits.append(rule["why"])
    return hits


def load_prices(code: str) -> list[tuple[str, float]]:
    path = prices_dir() / f"{code}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if data.get("missing") or not data.get("c"):
        return []
    return [(d, float(p)) for d, p in data["c"]]


def nearest(closes: list[tuple[str, float]], day: str) -> float | None:
    pick = None
    for d, p in closes:
        if d <= day:
            pick = p
        else:
            break
    return pick


def rsi(vals: list[float], n: int = 14) -> float | None:
    if len(vals) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = vals[i] - vals[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    ag, al = gains / n, losses / n
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 1)


def sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return round(sum(vals[-n:]) / n, 2)


def ret_pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new / old - 1) * 100, 1)


def resample(closes: list[tuple[str, float]], kind: str) -> list[float]:
    buckets: dict[str, float] = {}
    for d, p in closes:
        dt = datetime.strptime(d, "%Y-%m-%d")
        key = dt.strftime("%Y-%W") if kind == "W" else dt.strftime("%Y-%m")
        buckets[key] = p
    return [buckets[k] for k in sorted(buckets)]


def ta_block(closes: list[tuple[str, float]]) -> dict:
    if len(closes) < 30:
        return {}
    last_d, last = closes[-1]
    w = resample(closes, "W")
    m = resample(closes, "M")
    w20, w40 = sma(w, 20), sma(w, 40)
    m10 = sma(m, 10)
    wr, mr = rsi(w), rsi(m)
    r4 = ret_pct(w[-1], w[-5]) if len(w) >= 5 else None
    r13 = ret_pct(w[-1], w[-14]) if len(w) >= 14 else None
    hi20 = max(w[-20:]) if len(w) >= 20 else max(w)
    lo8 = min(w[-8:]) if len(w) >= 8 else min(w)
    from_hi = ret_pct(last, hi20)
    vs20 = ret_pct(last, w20)
    vs10m = ret_pct(last, m10)
    if w20 and w40 and last > w20 > w40:
        wtrend = "UP"
    elif w20 and last < w20:
        wtrend = "DOWN"
    else:
        wtrend = "MIX"
    mtrend = "UP" if (m10 and last > m10) else ("DOWN" if m10 and last < m10 else "MIX")
    return {
        "last": round(last, 2),
        "asof": last_d,
        "weekly": {
            "rsi": wr,
            "sma20": w20,
            "sma40": w40,
            "vs20": vs20,
            "ret4w": r4,
            "ret13w": r13,
            "fromHigh": from_hi,
            "trend": wtrend,
            "recentLow": round(lo8, 2),
        },
        "monthly": {
            "rsi": mr,
            "sma10": m10,
            "vs10": vs10m,
            "trend": mtrend,
        },
        "invalidation": round(lo8, 2),
    }


def decay(days: float) -> float:
    return 2 ** (-days / HALF)


def ensure_added_column(con: sqlite3.Connection) -> None:
    cols = {r[1] for r in con.execute("PRAGMA table_info(politician_trades)")}
    if "added" not in cols:
        con.execute("ALTER TABLE politician_trades ADD COLUMN added TEXT")
    if not TRADES_LITE.exists():
        return
    n_null = con.execute(
        "SELECT COUNT(*) FROM politician_trades WHERE added IS NULL OR added = ''"
    ).fetchone()[0]
    if n_null == 0:
        return
    lite = json.loads(TRADES_LITE.read_text(encoding="utf-8-sig"))
    rows = []
    for t in lite.get("trades") or []:
        tid, added = t.get("id"), t.get("added")
        if tid and added:
            rows.append((added, tid))
    if rows:
        con.executemany("UPDATE politician_trades SET added=? WHERE trade_id=? AND (added IS NULL OR added='')", rows)
        log(f"analysis overlay added on {len(rows)} lite rows")


def load_people() -> dict[str, dict]:
    if not BIOS.exists():
        return {}
    data = json.loads(BIOS.read_text(encoding="utf-8-sig"))
    return data.get("people") or {}


def load_trades(con: sqlite3.Connection) -> list[dict[str, Any]]:
    cols = {r[1] for r in con.execute("PRAGMA table_info(politician_trades)")}
    added_sel = "added" if "added" in cols else "NULL AS added"
    rows = con.execute(
        f"""
        SELECT trade_id, filer, filer_id, chamber, ticker, asset, asset_type, side,
               amount_raw, trade_date, {added_sel}
        FROM politician_trades
        WHERE side IN ('purchase', 'sale')
        """
    )
    out = []
    for r in rows:
        out.append(
            {
                "id": r[0],
                "filer": r[1],
                "filer_id": r[2],
                "chamber": r[3],
                "ticker": r[4] or "",
                "asset": r[5] or "",
                "asset_type": r[6] or "",
                "side": r[7],
                "amount": r[8] or "",
                "trade_date": r[9] or "",
                "added": r[10] or "",
            }
        )
    return out


def ticker_meta(con: sqlite3.Connection) -> dict[str, dict]:
    return {
        r[0]: {"name": r[1] or r[0], "industry": r[2] or ""}
        for r in con.execute("SELECT ticker, name, industry FROM tickers")
    }


def buy_dip_why(landed: bool, monthly_ok: bool, monthly_trend: str | None) -> str | None:
    """Landed buy-dip line. 'Still up' is an UP monthly trend, not monthly_ok."""
    if not landed:
        return None
    parts = ["Just landed, weekly washed out"]
    if monthly_trend == "UP":
        parts.append("monthly still up")
    elif monthly_ok:
        parts.append("monthly intact")
    return ", ".join(parts)


def score_universe(con: sqlite3.Connection) -> list[dict[str, Any]]:
    people = load_people()
    lookup = ticker_meta(con)
    trades = load_trades(con)
    now = datetime.now(timezone.utc)
    today = now.date()
    cut = (today - timedelta(days=WIN)).isoformat()
    base_cut = (today - timedelta(days=365 * 3)).isoformat()

    by: dict[str, dict] = {}
    for t in trades:
        if t.get("side") not in ("purchase", "sale"):
            continue
        code = code_of(t)
        if not public_stock(t, code) and not (chart(code) and code not in SKIP and not is_bond(t)):
            if not (chart(code) and not is_bond(t) and code not in SKIP):
                continue
        meta = lookup.get(code) or {}
        rec = by.get(code) or {
            "code": code,
            "name": meta.get("name") or t.get("asset") or code,
            "industry": meta.get("industry") or "",
            "buys": [],
            "sells": [],
            "hist": 0,
            "landed_buys": [],
            "conflict_why": set(),
            "conflict_filers": set(),
        }
        if meta.get("name"):
            rec["name"] = meta["name"]
        td = t.get("trade_date") or ""
        if td >= base_cut and t.get("side") == "purchase" and public_stock(t, code):
            rec["hist"] += 1
        if td >= cut:
            if t.get("side") == "purchase":
                rec["buys"].append(t)
            else:
                rec["sells"].append(t)
        if t.get("added") and t.get("side") == "purchase" and public_stock(t, code):
            rec["landed_buys"].append(t)
        if t.get("side") == "purchase" and td >= cut and public_stock(t, code):
            bio = people.get(t.get("filer_id") or "") or {}
            whys = overlap_why(code, rec["name"], rec["industry"], seats_of(bio))
            if whys:
                rec["conflict_why"].update(whys)
                rec["conflict_filers"].add(t.get("filer") or "")
        by[code] = rec

    heat_rows = []
    for code, rec in by.items():
        try:
            stock_buys = [t for t in rec["buys"] if public_stock(t, code)]
            if not stock_buys:
                continue
            filers: dict[str, dict] = {}
            buy_high = sell_high = 0
            for t in rec["sells"]:
                sell_high += amount_high(t.get("amount") or "")
            for t in stock_buys:
                hi = amount_high(t.get("amount") or "")
                buy_high += hi
                f = filers.get(t.get("filer_id") or "") or {
                    "id": t.get("filer_id"), "name": t.get("filer"), "high": 0, "last": "", "n": 0
                }
                f["high"] += hi
                f["n"] += 1
                if (t.get("trade_date") or "") > f["last"]:
                    f["last"] = t.get("trade_date") or ""
                filers[t.get("filer_id") or ""] = f
            buyers = [f for f in filers.values() if f["high"] > 0]
            if not buyers:
                continue
            recency = 0.0
            for b in buyers:
                last = b["last"]
                days = max(0, (today - datetime.fromisoformat(last).date()).days)
                recency += math.log10(1 + b["high"] / 1000) * decay(days)
            n_buyers = len(buyers)
            last_buy = max(b["last"] for b in buyers)
            months = max(1, (now.timestamp() - datetime.fromisoformat(base_cut).timestamp()) / (86400 * 30))
            burst = 1 + min(2, n_buyers / max(0.35, rec["hist"] / months))
            mixed = 0.82 if sell_high > 0 else 1
            mega = 0.8 if code in MEGA else 1
            conflict = 1.32 if rec["conflict_why"] else 1
            heat = recency * (1 + math.log(1 + n_buyers)) * mixed * conflict * burst * mega
            heat_rows.append({
                "code": code,
                "name": rec["name"],
                "industry": rec["industry"],
                "heat": heat,
                "nBuyers": n_buyers,
                "buyHigh": buy_high,
                "lastBuy": last_buy,
                "buyers": sorted(buyers, key=lambda x: -x["high"]),
                "conflict": bool(rec["conflict_why"]),
                "conflictWhy": sorted(rec["conflict_why"]),
                "whale": any(b["high"] >= 500000 for b in buyers),
                "landed": bool(rec["landed_buys"]),
                "landedAt": max((t.get("added") or "") for t in rec["landed_buys"]) if rec["landed_buys"] else "",
                "cluster": n_buyers >= 3,
            })
        except Exception as exc:
            log(f"analysis skip heat {code}: {exc}")
    heat_rows.sort(key=lambda r: (-r["heat"], r["lastBuy"]))
    rank_of = {r["code"]: i + 1 for i, r in enumerate(heat_rows)}

    book = []
    today = datetime.now(timezone.utc).date()
    for row in heat_rows:
        try:
            code = row["code"]
            closes = load_prices(code)
            tech = ta_block(closes) if closes else {}
            if not tech:
                continue
            last = tech["last"]
            buy_px = nearest(closes, row["lastBuy"])
            chg = round((last - buy_px) / buy_px, 4) if buy_px else None
            w, mth = tech["weekly"], tech["monthly"]
            wr, mr = w.get("rsi"), mth.get("rsi")
            vs20, vs10 = w.get("vs20"), mth.get("vs10")
            lag = None
            if row["landedAt"] and row["lastBuy"]:
                try:
                    ad = datetime.fromisoformat(row["landedAt"].replace("Z", "+00:00"))
                    tr = datetime.fromisoformat(row["lastBuy"])
                    lag = max(0, (ad.date() - tr.date()).days)
                except ValueError:
                    lag = None

            score = 0.0
            bits = []
            if row["landed"]:
                score += 2.4
                bits.append("Landed")
            if row["whale"]:
                score += 0.8
                bits.append("Whale")
            if row["conflict"]:
                score += 1.5
                bits.append("Conflict")
            if row["cluster"]:
                score += 1.1
                bits.append("Cluster")
            score += min(2.2, (row["heat"] / max(heat_rows[0]["heat"], 0.001)) * 2.2)
            days_ago = max(0, (today - datetime.fromisoformat(row["lastBuy"]).date()).days)
            score += max(0, 1.4 - days_ago / 40)

            monthly_ok = (mr or 0) >= 45 or (vs10 is not None and vs10 >= -8)
            oversold = wr is not None and 20 <= wr <= 48 and monthly_ok
            deep = wr is not None and wr < 32 and monthly_ok
            extended = wr is not None and wr >= 72
            chase = chg is not None and chg > 0.12 and w.get("trend") == "DOWN"
            if oversold and monthly_ok:
                score += 2.0
                bits.append("Weekly washout")
            if deep:
                score += 1.2
                bits.append("Deep washout")
            if w.get("trend") == "UP" and not extended:
                score += 0.6
            if monthly_ok:
                score += 0.5
                bits.append("Monthly intact")
            if chase:
                score -= 2.2
                bits.append("Already ran")
            if extended:
                score -= 2.4
                bits.append("Weekly extended")
            if code in MEGA:
                score *= 0.85

            if chase or extended:
                action = "avoid"
            elif oversold and monthly_ok:
                action = "buy-dip"
            elif w.get("trend") == "UP" and not extended:
                action = "watch"
            else:
                action = "watch"

            why = " · ".join(bits[:5]) if bits else "Tape print in window"
            if action == "buy-dip":
                dipped = buy_dip_why(bool(row["landed"]), bool(monthly_ok), mth.get("trend"))
                if dipped:
                    why = dipped
            elif action == "avoid" and chase:
                why = "Politician already in; price ran and weekly is still down — do not chase the open"
            elif action == "avoid" and extended:
                why = "Signals heat, but weekly RSI is stretched into the highs"

            inv = tech.get("invalidation")
            if inv is not None and last and inv >= last * 0.995:
                inv = round(min(last * 0.94, (w.get("sma20") or last) * 0.92), 2)

            book.append({
                "code": code,
                "name": clean_name(row["name"] or code, code),
                "action": action,
                "rule_action": action,
                "score": round(score, 3),
                "why": why,
                "last": last,
                "pxAsof": tech["asof"],
                "buyPx": None if buy_px is None else round(buy_px, 2),
                "chgSinceBuy": chg,
                "lastBuy": row["lastBuy"],
                "nBuyers": row["nBuyers"],
                "buyHigh": row["buyHigh"],
                "heatRank": rank_of.get(code),
                "heat": round(row["heat"], 3),
                "landed": row["landed"],
                "landedAt": row["landedAt"],
                "lagDays": lag,
                "conflict": row["conflict"],
                "conflictWhy": row["conflictWhy"],
                "whale": row["whale"],
                "cluster": row["cluster"],
                "buyers": [{"name": b["name"], "id": b["id"]} for b in row["buyers"][:6]],
                "weekly": w,
                "monthly": mth,
                "invalidation": inv,
                "flags": bits,
                "daysAgo": days_ago,
                "fuzzy": action == "watch" and not (chase or extended or (oversold and monthly_ok)),
            })
        except Exception as exc:
            log(f"analysis skip book {row.get('code')}: {exc}")
    book.sort(key=lambda r: -r["score"])
    return book


def jev_state(row: dict) -> dict:
    w, m = row.get("weekly") or {}, row.get("monthly") or {}
    return {
        "ticker": row["code"],
        "name": row["name"],
        "rule_action": row["rule_action"],
        "score": row["score"],
        "why": row["why"],
        "flags": row.get("flags") or [],
        "weekly": {"rsi": w.get("rsi"), "trend": w.get("trend"), "vs20": w.get("vs20")},
        "monthly": {"rsi": m.get("rsi"), "trend": m.get("trend"), "vs10": m.get("vs10")},
        "chgSinceBuy": row.get("chgSinceBuy"),
        "landed": row.get("landed"),
        "whale": row.get("whale"),
        "cluster": row.get("cluster"),
        "conflict": row.get("conflict"),
        "conflictWhy": row.get("conflictWhy") or [],
        "nBuyers": row.get("nBuyers"),
        "lastBuy": row.get("lastBuy"),
        "daysAgo": row.get("daysAgo"),
        "buyers": [b.get("name") for b in (row.get("buyers") or [])[:4]],
        "fuzzy_watch": bool(row.get("fuzzy")),
    }


def apply_jev_row(row: dict) -> dict:
    packed = jev.ask(jev_state(row), jev.analysis_questions(), label=f"analysis_ship_v1:{row['code']}")
    return {"code": row["code"], "packed": packed}


def _apply_answer(row: dict, packed: dict, stats: dict) -> None:
    """Fold one Jev answer into the row (ship/drop, action flip, conflict, borderline)."""
    ans = packed.get("answers") or {}
    ship = (ans.get("ship") or {}).get("choice")
    action = (ans.get("action") or {}).get("choice")
    aconf = (ans.get("action") or {}).get("confidence")
    borderline = (ans.get("borderline") or {}).get("score")
    conflict_noul = (ans.get("conflict_real") or {}).get("noul")
    row["jev_ship"] = ship
    row["jev_action"] = action
    row["jev_action_confidence"] = aconf
    row["jev_borderline"] = borderline
    row["jev_conflict_noul"] = conflict_noul
    row["jev_model"] = packed.get("model")
    stats["model"] = packed.get("model")
    stats["n"] += 1
    if ship == "drop":
        row["shipped"] = False
        stats["dropped"].append(row["code"])
        return
    row["shipped"] = True
    if action in ("buy-dip", "watch", "avoid") and aconf is not None and aconf >= 0.55:
        if action != row["action"]:
            stats["action_flips"].append((row["code"], row["action"], action))
        row["action"] = action
    if row.get("conflict") and conflict_noul is not None and conflict_noul < 0.45:
        row["conflict"] = False
        row["conflictWhy"] = []
        row["flags"] = [f for f in (row.get("flags") or []) if f != "Conflict"]
    if borderline is not None and borderline < 0.75 and row["action"] == "buy-dip":
        # close call: keep buy-dip only if weekly washout is still in flags
        if "Weekly washout" not in (row.get("flags") or []) and "Deep washout" not in (row.get("flags") or []):
            row["action"] = "watch"


def _review(con: sqlite3.Connection, rows: list[dict], stats: dict) -> int:
    """Ask Jev about each row in parallel. Returns the number of errored calls."""
    by_code = {r["code"]: r for r in rows}
    errors = 0
    with ThreadPoolExecutor(max_workers=6) as pool_ex:
        futs = [pool_ex.submit(apply_jev_row, row) for row in rows]
        for fut in as_completed(futs):
            try:
                result = fut.result()
            except Exception as exc:
                errors += 1
                log(f"  analysis Jev error: {type(exc).__name__}")
                continue
            packed = result["packed"]
            row = by_code[result["code"]]
            jev.store_decision(con, "analysis", row["code"], packed)
            _apply_answer(row, packed, stats)
    for row in rows:
        if "shipped" not in row:
            # Jev errored on this row: keep the rules verdict rather than silently dropping it.
            row["shipped"] = True
            row["jev_error"] = True
    return errors


def _kept_play(rows: list[dict]) -> int:
    return sum(1 for r in rows if r.get("shipped") and r["action"] != "avoid")


def gate_with_jev(con: sqlite3.Connection, book: list[dict], skip_jev: bool) -> tuple[list[dict], list[dict], dict]:
    non_avoid = [r for r in book if r["action"] != "avoid"]
    play_pool = non_avoid[:JEV_BOOK_POOL]
    avoid_pool = [r for r in book if r["action"] == "avoid"][:JEV_AVOID_POOL]
    pool = play_pool + avoid_pool
    stats = {"ran": False, "n": 0, "errors": 0, "model": None, "dropped": [], "action_flips": [],
             "refill": 0, "refill_codes": []}
    if skip_jev:
        log("analysis Jev skipped (--skip-jev)")
        play = [r for r in book if r["action"] != "avoid"][:BOOK_CAP]
        avoid = [r for r in book if r["action"] == "avoid"][:AVOID_CAP]
        return play, avoid, stats
    if not jev.key_present():
        log("BLOCKER: TYPESAFE_API_KEY missing; analysis export is rules-only (no Jev gate).")
        play = [r for r in book if r["action"] != "avoid"][:BOOK_CAP]
        avoid = [r for r in book if r["action"] == "avoid"][:AVOID_CAP]
        stats["reason"] = "no_key"
        return play, avoid, stats

    log(f"Jev analysis gate: {len(pool)} candidates")
    errors = _review(con, pool, stats)

    # Refill: the next-ranked non-avoid names, a batch at a time, until JEV_BOOK_TARGET
    # play names have passed review, the list runs out, or JEV_REFILL_MAX extra calls are spent.
    reviewed = {r["code"] for r in pool}
    queue = [r for r in non_avoid if r["code"] not in reviewed]
    while stats["n"] > 0 and queue and stats["refill"] < JEV_REFILL_MAX:
        need = JEV_BOOK_TARGET - _kept_play(pool)
        if need <= 0:
            break
        batch = queue[:min(need, JEV_REFILL_MAX - stats["refill"])]
        queue = queue[len(batch):]
        stats["refill"] += len(batch)
        stats["refill_codes"].extend(r["code"] for r in batch)
        errors += _review(con, batch, stats)
        pool.extend(batch)
    if stats["refill"]:
        log(f"analysis Jev refill {stats['refill']} (cap {JEV_REFILL_MAX}) kept play {_kept_play(pool)}/{JEV_BOOK_TARGET}")

    stats["ran"] = True
    stats["errors"] = errors
    if stats["n"] == 0:
        log("BLOCKER: analysis Jev returned 0 answers; export is rules-only.")
        play = [r for r in book if r["action"] != "avoid"][:BOOK_CAP]
        avoid = [r for r in book if r["action"] == "avoid"][:AVOID_CAP]
        return play, avoid, stats

    shipped = [r for r in pool if r.get("shipped")]
    play = [r for r in shipped if r["action"] != "avoid"]
    play.sort(key=lambda r: -r["score"])
    play = play[:BOOK_CAP]
    avoid = [r for r in shipped if r["action"] == "avoid"]
    avoid.sort(key=lambda r: -r["score"])
    avoid = avoid[:AVOID_CAP]
    return play, avoid, stats


def persist(con: sqlite3.Connection, book: list[dict], play: list[dict], avoid: list[dict]) -> None:
    con.execute(ANALYSIS_DDL)
    con.execute("DELETE FROM analysis_candidates")
    shipped_codes = {r["code"] for r in play} | {r["code"] for r in avoid}
    play_codes = {r["code"] for r in play}
    rows = []
    for r in book:
        lst = None
        if r["code"] in play_codes:
            lst = "book"
        elif r["code"] in {a["code"] for a in avoid}:
            lst = "avoid"
        rows.append(
            (
                r["code"],
                r["name"],
                r.get("rule_action"),
                r["action"],
                r["score"],
                r["why"],
                r.get("last"),
                r.get("pxAsof"),
                r.get("buyPx"),
                r.get("chgSinceBuy"),
                r.get("lastBuy"),
                r.get("nBuyers"),
                r.get("buyHigh"),
                r.get("heatRank"),
                r.get("heat"),
                1 if r.get("landed") else 0,
                r.get("landedAt"),
                r.get("lagDays"),
                1 if r.get("conflict") else 0,
                json.dumps(r.get("conflictWhy") or []),
                1 if r.get("whale") else 0,
                1 if r.get("cluster") else 0,
                json.dumps(r.get("buyers") or []),
                json.dumps(r.get("weekly") or {}),
                json.dumps(r.get("monthly") or {}),
                r.get("invalidation"),
                json.dumps(r.get("flags") or []),
                r.get("jev_ship"),
                r.get("jev_action"),
                r.get("jev_action_confidence"),
                r.get("jev_borderline"),
                r.get("jev_conflict_noul"),
                r.get("jev_model"),
                1 if r["code"] in shipped_codes else 0,
                lst,
            )
        )
    con.executemany(
        """
        INSERT OR REPLACE INTO analysis_candidates(
          ticker, name, rule_action, action, score, why, last_px, px_asof, buy_px,
          chg_since_buy, last_buy, n_buyers, buy_high, heat_rank, heat, landed,
          landed_at, lag_days, conflict, conflict_why_json, whale, cluster,
          buyers_json, weekly_json, monthly_json, invalidation, flags_json,
          jev_ship, jev_action, jev_action_confidence, jev_borderline,
          jev_conflict_noul, jev_model, shipped, list
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        rows,
    )


def export_row(r: dict, rank: int) -> dict:
    return {
        "code": r["code"],
        "name": r["name"],
        "action": r["action"],
        "score": r["score"],
        "why": r["why"],
        "last": r["last"],
        "pxAsof": r["pxAsof"],
        "buyPx": r["buyPx"],
        "chgSinceBuy": r["chgSinceBuy"],
        "lastBuy": r["lastBuy"],
        "nBuyers": r["nBuyers"],
        "buyHigh": r["buyHigh"],
        "heatRank": r["heatRank"],
        "heat": r["heat"],
        "landed": r["landed"],
        "landedAt": r["landedAt"],
        "lagDays": r["lagDays"],
        "conflict": r["conflict"],
        "conflictWhy": r["conflictWhy"],
        "whale": r["whale"],
        "cluster": r["cluster"],
        "buyers": r["buyers"],
        "weekly": r["weekly"],
        "monthly": r["monthly"],
        "invalidation": r["invalidation"],
        "flags": r["flags"],
        "rank": rank,
    }


def compute_analysis(con: sqlite3.Connection, skip_jev: bool = False) -> dict[str, Any] | None:
    root = prices_dir()
    if not root.exists():
        log("No prices/; skip analysis.")
        return None
    ensure_added_column(con)
    con.execute(ANALYSIS_DDL)
    log("analysis scoring universe…")
    book = score_universe(con)
    log(f"analysis universe {len(book)}")
    play, avoid, stats = gate_with_jev(con, book, skip_jev)
    for i, r in enumerate(play, 1):
        r["rank"] = i
    for i, r in enumerate(avoid, 1):
        r["rank"] = i
    persist(con, book, play, avoid)
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("jev_analysis", json.dumps(stats)),
    )
    asofs = [r.get("pxAsof") for r in book if r.get("pxAsof")]
    tape = None
    try:
        tape = con.execute("SELECT value FROM meta WHERE key='tape_collected'").fetchone()
    except sqlite3.Error:
        tape = None
    out = {
        "generated": iso_now(),
        "tapeCollected": (tape[0] if tape else None) or peek_collected(),
        "priceAsof": last_us_session_close(asofs),
        "windowDays": WIN,
        "disclaimer": DISCLAIMER,
        "book": [export_row(r, r["rank"]) for r in play],
        "avoid": [export_row(r, r["rank"]) for r in avoid],
    }
    log(
        f"analysis book {len(play)} avoid {len(avoid)} jev n={stats.get('n')} "
        f"errors={stats.get('errors')} dropped={stats.get('dropped')} flips={stats.get('action_flips')}"
    )
    return out


def write_analysis_json(payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2) + "\n"
    atomic_write_text(ANALYSIS_JSON, text)
    atomic_write_text(EXPORT_DIR / "analysis.json", text)
    log(f"Wrote {ANALYSIS_JSON}")


def run(con: sqlite3.Connection, skip_jev: bool = False) -> dict[str, Any] | None:
    payload = compute_analysis(con, skip_jev=skip_jev)
    if payload is None:
        return None
    write_analysis_json(payload)
    return payload


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Rebuild analysis.json from qc.sqlite")
    parser.add_argument("--skip-jev", action="store_true")
    args = parser.parse_args()
    if not DB_PATH.exists():
        log(f"missing {DB_PATH}; run rebuild.py first")
        return 1
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        payload = run(con, skip_jev=args.skip_jev)
        con.commit()
        if payload is None:
            return 1
        if not args.skip_jev and not jev.key_present():
            return 2
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
