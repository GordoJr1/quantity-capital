#!/usr/bin/env python3
"""Build backtest.json: paper returns from STOCK Act filing dates.

Entry = first daily close on/after filed_date (when the public can copy).
Exit  = latest close in prices/. Purchases only, equal-weight per leg.
Skip bonds, options, junk tickers, and legs with no usable price.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from paths import QC_ROOT, PRICES as QC_PRICES

ROOT = QC_ROOT
PRICES = QC_PRICES if QC_PRICES.exists() else (QC_ROOT / "prices")
BAD_TICKERS = {"LLC", "THE", "AND", "INC", "CORP", "CLASS", "NONE", "NA", "CMN", "COM", "NPV", "ETF", "FUND"}
OPT_RE = re.compile(
    r"exercised|call option|put option|strike pric|flex euro|\bcall/|\bput/|@\s*\d",
    re.I,
)
BOND_RE = re.compile(r"rate/coupon", re.I)
CHART_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,6}$")
# Weekend + holiday slack. A first bar far after filed_date is truncated history, not a real print.
MAX_ENTRY_LAG_DAYS = 10


def is_bond(t: dict) -> bool:
    typ = (t.get("asset_type") or "").lower()
    asset = (t.get("asset") or "").lower()
    return "bond" in typ or "municipal" in typ or bool(BOND_RE.search(asset))


def is_option_like(t: dict) -> bool:
    typ = (t.get("asset_type") or "").lower()
    asset = (t.get("asset") or "").lower()
    return "option" in typ or bool(OPT_RE.search(asset))


def is_chart_ticker(code: str) -> bool:
    c = (code or "").upper()
    return bool(c and c != "—" and CHART_RE.match(c) and c not in BAD_TICKERS)


# Port of qc.js issuerName, plus PTR-row / account / share-class junk that
# pollutes tickers.json names. Prefer a real issuer over a glued filing line.
BROKER_ISSUERS = {
    "MS": re.compile(r"morgan stanley", re.I),
    "GS": re.compile(r"goldman sachs", re.I),
    "JPM": re.compile(r"jpmorgan|jp morgan", re.I),
    "BAC": re.compile(r"bank of america|merrill", re.I),
    "WFC": re.compile(r"wells fargo", re.I),
    "SCHW": re.compile(r"schwab", re.I),
    "C": re.compile(r"\bcitigroup\b|\bciti\b", re.I),
    "BLK": re.compile(r"blackrock", re.I),
    "UBS": re.compile(r"\bubs\b", re.I),
    "PNC": re.compile(r"\bpnc\b", re.I),
    "IBKR": re.compile(r"interactive brokers", re.I),
}
BROKERS = (
    r"morgan stanley|goldman sachs|fidelity(?: investments)?|vanguard|"
    r"charles schwab|\bschwab\b|bank of america|merrill lynch|\bmerrill\b|"
    r"jpmorgan(?: chase)?|jp ?morgan|wells fargo|\bubs\b|raymond james|"
    r"edward jones|ameriprise|e\*?trade|td ameritrade|interactive brokers|"
    r"\bchase\b|aperio group(?: llc)?"
)
ACCOUNT = (
    r"smith barney(?: llc)?|ira|roth ira|trust account|brokerage account|"
    r"\bbrokerage\b|select uma(?: account)?|unified management account|joint tbe"
)
SHARE_TAIL = re.compile(
    r"\s+(?:Common Stock.*|Class [A-Z].*|Ordinary Shares?.*|"
    r"American Depositary Shares?.*|\bADS\b.*|Registered Shares.*|"
    r"Common Shares.*|New York Registry Shares.*|"
    r"Common Units(?: Representing.*)?|\bVoting\b.*|Series [A-Z]\b.*|"
    r"\bCMN\b.*)$",
    re.I,
)
PTR_OTHER = re.compile(
    r"^.*\([A-Z]{1,6}\)(?:\s*\[ST\])?\s+[PS]\s+\d{1,2}/\d{1,2}/\d{2,4}"
    r".*?(?:\$[\d,]+(?:\s*-\s*\$[\d,]+)?)\s+"
)
PTR_ROW = re.compile(
    r"^[PS]\s+\d{1,2}/\d{1,2}/\d{2,4}.*?(?:\$[\d,]+(?:\s*-\s*\$[\d,]+)?)\s+"
)
JUNK_NAME = re.compile(r"\$[\d,]|\d{2}/\d{2}/\d{4}|\[ST\]|rate/coupon|matures:", re.I)


def issuer_name(code: str, raw: str) -> str:
    c = (code or "").upper()
    s = re.sub(r"\s+", " ", raw or "").strip()
    if not s or s == "—":
        return c
    s = PTR_OTHER.sub("", s)
    s = PTR_ROW.sub("", s)
    s = re.sub(r"\s+Option Type:.*$", "", s, flags=re.I)
    s = re.sub(r"\s*(?:Bond|Notes?|MTN)?\s*Rate/Coupon:.*$", "", s, flags=re.I)
    s = re.sub(r"\s+\b(?:Bond|Notes?|MTN)\s*$", "", s, flags=re.I)
    s = (s.split(">")[-1] if ">" in s else s).strip()
    broker_re = BROKER_ISSUERS.get(c)
    broker_stock = bool(
        broker_re
        and broker_re.search(s)
        and not re.search(
            r"\b(ira|roth|trust account|brokerage|select uma|unified management|joint tbe)\b",
            s,
            re.I,
        )
    )
    if broker_stock:
        s = SHARE_TAIL.sub("", s)
        s = re.sub(r"\s*-\s*$", "", s).strip()
        return s or c
    if c not in BROKER_ISSUERS:
        s = re.sub(rf"^(?:{BROKERS})\b[\s,:-]*", "", s, flags=re.I)
    s = re.sub(
        rf"^(?:{ACCOUNT}|uma(?: account)?|select uma(?: account)?)\b[\s,:-]*",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"^(?:account(?:\s*#\s*\d+)?|uma account(?:\s*#\s*\d+)?)\b[\s,:-]*",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"^#\s*\d+\s+", "", s)
    s = re.sub(r"^\d{2,5}\s+", "", s)
    s = re.sub(
        r"^[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*)?\s+IRA\s+",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"^(?:tacs r3k)\s+", "", s, flags=re.I)
    s = re.sub(r"^\$[\d,]+(?:\.\d+)?\s+(?:F\s+S:\s*Amended\s+\S+\s+)?", "", s, flags=re.I)
    s = re.sub(
        r"^(?:CP\s*-?\s*INV|CRT\s*-?\s*Standard Unit Trust|Trust\s*-\s*\S+)\s+",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"^(?:D:\s*)?(?:Portfolio Rebalance|Account Closing|FULL LIQUIDATION\.?|"
        r"Professionally managed account|D/B/A)\s+",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"\b(?:D:\s*)?Portfolio Rebalance\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:investment account(?:\s*#\s*\d+)?)\b[\s,:-]*", "", s, flags=re.I)
    s = re.sub(r"^financial disclosure\.\s*", "", s, flags=re.I)
    s = re.sub(r"^active assets\s*\(\d+\)\s*", "", s, flags=re.I)
    s = re.sub(
        r"^.*\bD:\s*(?:professionally managed account\.?\s*|sold entire holding\.?\s*|own/operate\s+(?:mobile home park\s+)?)",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"^C:\s*Sell to Open\s*[–—-]\s*(?:New\s+)?Covered Call Contract\s+", "", s)
    s = re.sub(r"^.*\bFamily Partnership\s+", "", s, flags=re.I)
    s = SHARE_TAIL.sub("", s)
    if c:
        s = re.sub(rf"\s*\({re.escape(c)}\)\s*$", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*Common\s+Sto.*$", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*$", "", s).strip()
    s = re.sub(r"\s+CMN\b.*$", "", s, flags=re.I).strip()
    s = re.sub(r"\s*S/ADR\s*$", "", s, flags=re.I).strip()
    if not s or re.match(r"^(common stock|class [a-z]|llc|inc|corp)$", s, re.I):
        return c
    if re.match(r"^[A-Z][A-Z0-9.]{0,6}$", s) and s.upper() != c:
        return c
    if JUNK_NAME.search(s):
        return c
    return s


def name_quality(code: str, s: str) -> int:
    if not s or s == code:
        return 0
    sl = s.lower()
    if len(s) > 90:
        return 0
    if JUNK_NAME.search(s):
        return 0
    if re.search(r"\b(uma account|brokerage account|select uma|investment account|financial disclosure|sell to open|professionally managed)\b", sl):
        return 0
    if re.search(r"\bD:\s|\bC:\s|\bL:\s", s):
        return 0
    q = 5
    if re.search(
        r"\b(inc|incorp|corp|corporation|ltd|limited|plc|llc|co|company|"
        r"group|holdings?|etf|n\.?v\.?)\b",
        sl,
    ):
        q += 6
    if " " in s:
        q += 3
    if re.search(r"\b(ira|roth|trust account|partnership|grandchildren)\b", sl):
        q -= 6
    if s.isupper() and len(s) > 24:
        q -= 2
    return q


def pick_name(code: str, cands: list[str]) -> str:
    best, best_q = code, -1
    seen = set()
    for s in cands:
        if not s or s in seen:
            continue
        seen.add(s)
        q = name_quality(code, s)
        if q > best_q or (q == best_q and q > 0 and len(s) < len(best)):
            best, best_q = s, q
    return best if best_q > 0 else code



def first_on_or_after(closes: list, date: str):
    lo, hi = 0, len(closes)
    while lo < hi:
        mid = (lo + hi) // 2
        if closes[mid][0] < date:
            lo = mid + 1
        else:
            hi = mid
    if lo >= len(closes):
        return None
    return closes[lo]


def mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    ys = sorted(xs)
    n = len(ys)
    mid = n // 2
    if n % 2:
        return ys[mid]
    return (ys[mid - 1] + ys[mid]) / 2.0


def round_px(n) -> float:
    return round(float(n), 2)


def round_ret(n) -> float:
    return round(float(n), 4)


def load_prices(code: str, cache: dict):
    if code in cache:
        return cache[code]
    path = PRICES / (code + ".json")
    if not path.is_file():
        cache[code] = None
        return None
    try:
        with path.open() as f:
            data = json.load(f)
        closes = data.get("c") or []
        cleaned = []
        for row in closes:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            d, px = row[0], row[1]
            if not d or px is None:
                continue
            try:
                px = float(px)
            except (TypeError, ValueError):
                continue
            if px <= 0:
                continue
            cleaned.append([str(d)[:10], px])
        cache[code] = cleaned or None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        cache[code] = None
    return cache[code]


