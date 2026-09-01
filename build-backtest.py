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


def issuer_name(code: str, raw: str) -> str:
    s = re.sub(r"\s+", " ", raw or "").strip()
    s = re.sub(r"\s+(Common Stock.*|Class [A-Z].*|Ordinary Shares.*)$", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*$", "", s).strip()
    if not s or s == "—":
        return code
    return s


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
    tickers_path = ROOT / "tickers.json"
    if tickers_path.is_file():
        with tickers_path.open() as f:
            tickers_file = json.load(f)
        for code, meta in (tickers_file.get("tickers") or {}).items():
            names[str(code).upper()] = issuer_name(str(code).upper(), (meta or {}).get("name") or "")

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
        # Weekend/holiday slack only. A first bar far after filed_date is missing history, not a real print.
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
        name = names.get(code) or issuer_name(code, t.get("asset") or code)
        rec["legs"].append({
            "t": code,
            "name": name,
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
            "name": names.get(code) or code,
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
            "not share counts. Past copies do not mean the next filing works."
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
