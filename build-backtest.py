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

ROOT = Path(__file__).resolve().parent
PRICES = ROOT / "prices"
BAD_TICKERS = {"LLC", "THE", "AND", "INC", "CORP", "CLASS", "NONE", "NA"}
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
    s = re.sub(r"\s*Bond\s+Rate/Coupon:.*$", "", s, flags=re.I)
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
    s = re.sub(
        r"^(?:D:\s*)?(?:Portfolio Rebalance|Account Closing|FULL LIQUIDATION\.?|"
        r"Professionally managed account|D/B/A)\s+",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"^(?:CP\s*-?\s*INV|CRT\s*-?\s*Standard Unit Trust|Trust\s*-\s*\S+)\s+",
        "",
        s,
        flags=re.I,
    )
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


def main() -> int:
    tape_path = ROOT / "trades-lite.json"
    if not tape_path.is_file():
        print("missing trades-lite.json", file=sys.stderr)
        return 1
    with tape_path.open() as f:
        tape = json.load(f)
    trades = tape.get("trades") or []

    names = {}
    name_cands: dict[str, list[str]] = {}
    tickers_path = ROOT / "tickers.json"
    if tickers_path.is_file():
        with tickers_path.open() as f:
            tickers_file = json.load(f)
        for code, meta in (tickers_file.get("tickers") or {}).items():
            c = str(code).upper()
            cleaned = issuer_name(c, (meta or {}).get("name") or "")
            names[c] = cleaned
            if cleaned:
                name_cands.setdefault(c, []).append(cleaned)

    for t in trades:
        code = (t.get("ticker") or "").upper()
        if not is_chart_ticker(code):
            continue
        asset_name = issuer_name(code, t.get("asset") or "")
        if asset_name:
            name_cands.setdefault(code, []).append(asset_name)

    price_cache: dict = {}
    stats = {
        "purchases": 0,
        "eligible": 0,
        "priced": 0,
        "skippedNoTicker": 0,
        "skippedBondOpt": 0,
        "skippedNoPrice": 0,
        "skippedStale": 0,
    }
    filers = {}
    price_asof = ""

    for t in trades:
        if t.get("side") != "purchase":
            continue
        stats["purchases"] += 1
        if is_bond(t) or is_option_like(t):
            stats["skippedBondOpt"] += 1
            continue
        code = (t.get("ticker") or "").upper()
        if not is_chart_ticker(code):
            stats["skippedNoTicker"] += 1
            continue
        stats["eligible"] += 1
        fid = t.get("filer_id") or t.get("filer") or ""
        if not fid:
            stats["skippedNoTicker"] += 1
            continue
        rec = filers.get(fid)
        if rec is None:
            rec = {
                "id": fid,
                "name": t.get("filer") or fid,
                "chamber": t.get("chamber") or "",
                "eligible": 0,
                "skip": 0,
                "legs": [],
            }
            filers[fid] = rec
        rec["eligible"] += 1
        filed = (t.get("filed_date") or "")[:10]
        if len(filed) < 10:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        closes = load_prices(code, price_cache)
        if not closes:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        if closes[-1][0] > price_asof:
            price_asof = closes[-1][0]
        bar = first_on_or_after(closes, filed)
        if bar is None:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        entry_d, entry_px = bar
        try:
            filed_dt = datetime.strptime(filed, "%Y-%m-%d")
            entry_dt = datetime.strptime(entry_d, "%Y-%m-%d")
        except ValueError:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        lag = (entry_dt - filed_dt).days
        # Weekend/holiday slack only. A first bar far after filed_date is the
        # start of a truncated price file (missing history), not a real print.
        if lag < 0 or lag > MAX_ENTRY_LAG_DAYS:
            rec["skip"] += 1
            stats["skippedStale"] += 1
            continue
        exit_d, exit_px = closes[-1]
        if exit_px <= 0 or entry_px <= 0:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        ret = exit_px / entry_px - 1.0
        rec["legs"].append({
            "t": code,
            "name": pick_name(code, name_cands.get(code) or [code]),
            "filed": filed,
            "trade": (t.get("trade_date") or "")[:10],
            "amt": t.get("amount") or "",
            "in": round_px(entry_px),
            "inD": entry_d,
            "out": round_px(exit_px),
            "outD": exit_d,
            "ret": round_ret(ret),
        })
        stats["priced"] += 1

    people = []
    ticker_acc = {}
    for rec in filers.values():
        legs = rec["legs"]
        if not legs:
            continue
        legs.sort(key=lambda x: (x["filed"], x["t"]), reverse=True)
        for leg in legs:
            leg.pop("name", None)
            if not leg.get("amt"):
                leg.pop("amt", None)
            if not leg.get("trade"):
                leg.pop("trade", None)
            if leg.get("inD") == leg.get("filed"):
                leg.pop("inD", None)
        rets = [leg["ret"] for leg in legs]
        n = len(legs)
        people.append({
            "id": rec["id"],
            "name": rec["name"],
            "chamber": rec["chamber"],
            "n": n,
            "skip": rec["skip"],
            "avg": round_ret(mean(rets)),
            "med": round_ret(median(rets)),
            "win": sum(1 for r in rets if r > 0),
            "legs": legs,
        })
        for leg in legs:
            acc = ticker_acc.get(leg["t"])
            if acc is None:
                acc = {"rets": [], "people": set()}
                ticker_acc[leg["t"]] = acc
            acc["rets"].append(leg["ret"])
            acc["people"].add(rec["id"])

    people.sort(key=lambda r: (-r["avg"], -r["n"], r["name"]))
    tickers = []
    for code, acc in ticker_acc.items():
        rets = acc["rets"]
        tickers.append({
            "t": code,
            "name": pick_name(code, name_cands.get(code) or [code]),
            "n": len(rets),
            "people": len(acc["people"]),
            "avg": round_ret(mean(rets)),
            "med": round_ret(median(rets)),
            "win": sum(1 for r in rets if r > 0),
        })
    tickers.sort(key=lambda r: (-r["avg"], -r["n"], r["t"]))

    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00",
        "tapeCollected": tape.get("collected") or "",
        "priceAsof": price_asof,
        "method": (
            "Purchases only. Equal-weight average of listed-stock legs. "
            "Entry is the first daily close on or after filed_date (max "
            + str(MAX_ENTRY_LAG_DAYS)
            + " calendar days). Exit is the latest close in prices/. "
            "Bonds, options, and legs with missing prices are skipped. "
            "Official amounts are ranges, so size is not used."
        ),
        "disclaimer": (
            "Not investment advice. Hypothetical paper returns from public filing dates, "
            "not the politician's actual trade date or fill. Amounts are official ranges, "
            "not share counts. Returns are not annualized. Past copies do not mean the next filing works."
        ),
        "stats": {
            "purchases": stats["purchases"],
            "eligible": stats["eligible"],
            "priced": stats["priced"],
            "skippedNoTicker": stats["skippedNoTicker"],
            "skippedBondOpt": stats["skippedBondOpt"],
            "skippedNoPrice": stats["skippedNoPrice"],
            "skippedStale": stats["skippedStale"],
            "filers": sum(1 for p in people if p["n"]),
            "tickers": len(tickers),
        },
        "filers": people,
        "tickers": tickers,
    }

    dest = ROOT / "backtest.json"
    with dest.open("w") as f:
        json.dump(out, f, separators=(",", ":"))
        f.write("\n")
    size = dest.stat().st_size
    print(
        "wrote {path} ({kb:.0f} KB)  priced={priced} filers={filers} tickers={tickers} asof={asof}".format(
            path=dest.name,
            kb=size / 1024,
            priced=stats["priced"],
            filers=out["stats"]["filers"],
            tickers=len(tickers),
            asof=price_asof,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
