#!/usr/bin/env python3
"""Build insider-repeatable.json: most repeatable open-market insider copy trades.

Copy as-of the public filing date (Form 4 / SEDI print), using prices/ closes.
Open-market buys only. Equal-weight by ticker so one name cannot dominate.
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PRICES = ROOT / "prices"
TAPE = ROOT / "insider-trades-lite.json"
DEST = ROOT / "insider-repeatable.json"

HORIZONS = (30, 90, 180)
DEFAULT_HORIZON = 90
WINDOW_DAYS = 548  # 18 months
# Weekend / holiday slack. A first bar far after the target date is truncated history.
MAX_BAR_LAG_DAYS = 10
# Floor on clustered open-market buys in the 18-month window that also
# complete the ranking horizon. 5 sits in the requested 4–6 band and
# still leaves a few hundred 90-day filers after pricing.
MIN_BUYS = 5

AWARD_CODES = {"A", "30", "45", "46"}
EXERCISE_CODES = {"M", "X", "51", "54", "57", "59", "71"}
AWARD_NATURE = re.compile(r"^(30|45|46)\b")
EXERCISE_NATURE = re.compile(r"^(51|54|57|59|71)\b")


def is_award(t: dict) -> bool:
    if t.get("side") == "award":
        return True
    code = str(t.get("code") or "").upper()
    nature = str(t.get("nature") or "")
    return code in AWARD_CODES or bool(AWARD_NATURE.match(nature))


def is_exercise(t: dict) -> bool:
    if t.get("side") == "exercise":
        return True
    code = str(t.get("code") or "").upper()
    nature = str(t.get("nature") or "")
    return code in EXERCISE_CODES or bool(EXERCISE_NATURE.match(nature))


def is_open_market_buy(t: dict) -> bool:
    """Form 4 code P, or SEDI public-market nature 10. No awards, exercises, gifts, sales."""
    if not t or t.get("side") != "purchase":
        return False
    if is_award(t) or is_exercise(t):
        return False
    code = str(t.get("code") or "").upper()
    if code in {"G", "W", "D", "J", "U"}:
        return False
    origin = str(t.get("origin") or "").lower()
    nature = str(t.get("nature") or "")
    if origin == "sedi":
        return nature == "10" or nature.startswith("10")
    return code == "P"


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


def add_days(date: str, n: int) -> str | None:
    try:
        return (datetime.strptime(date, "%Y-%m-%d") + timedelta(days=n)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def bar_lag_ok(target: str, bar_date: str) -> bool:
    try:
        lag = (datetime.strptime(bar_date, "%Y-%m-%d") - datetime.strptime(target, "%Y-%m-%d")).days
    except ValueError:
        return False
    return 0 <= lag <= MAX_BAR_LAG_DAYS


def mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


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


def horizon_return(closes: list, start: str, days: int):
    """First close on/after start → first close on/after start+days. None if stale/missing."""
    entry = first_on_or_after(closes, start)
    if entry is None or not bar_lag_ok(start, entry[0]) or entry[1] <= 0:
        return None
    end = add_days(start, days)
    if not end:
        return None
    exit_bar = first_on_or_after(closes, end)
    if exit_bar is None or not bar_lag_ok(end, exit_bar[0]) or exit_bar[1] <= 0:
        return None
    return {
        "inD": entry[0],
        "in": entry[1],
        "outD": exit_bar[0],
        "out": exit_bar[1],
        "ret": exit_bar[1] / entry[1] - 1.0,
    }


def vs_ticker(closes: list, trade: str, copy_out_px: float, copy_ret: float):
    """Copy return minus same ticker from the trade-date close to the same exit close."""
    if not trade or copy_out_px <= 0:
        return None
    entry = first_on_or_after(closes, trade)
    if entry is None or not bar_lag_ok(trade, entry[0]) or entry[1] <= 0:
        return None
    ticker_ret = copy_out_px / entry[1] - 1.0
    return copy_ret - ticker_ret


def pick_firm(buys: list[dict]) -> tuple[str, str, str]:
    """Most recent clustered buy supplies firm, ticker, title."""
    latest = max(buys, key=lambda b: (b["filed"], b["ticker"]))
    return latest.get("firm") or "", latest.get("ticker") or "", latest.get("title") or ""


def score_horizon(buys: list[dict], days: int) -> dict | None:
    by_ticker: dict[str, list[float]] = defaultdict(list)
    hits = 0
    n = 0
    vs_vals: list[float] = []
    for b in buys:
        scored = b.get("h", {}).get(days)
        if not scored:
            continue
        ret = scored["ret"]
        by_ticker[b["ticker"]].append(ret)
        n += 1
        if ret > 0:
            hits += 1
        if scored.get("vs") is not None:
            vs_vals.append(scored["vs"])
    if n < MIN_BUYS:
        return None
    ticker_avgs = [mean(rs) for rs in by_ticker.values()]
    avg = mean([x for x in ticker_avgs if x is not None])
    if avg is None:
        return None
    vs = mean(vs_vals)
    out = {
        "n": n,
        "hit": round_ret(hits / n),
        "avg": round_ret(avg),
        "names": len(by_ticker),
    }
    if vs is not None:
        out["vs"] = round_ret(vs)
    return out


def main() -> int:
    if not TAPE.is_file():
        print("missing insider-trades-lite.json", file=sys.stderr)
        return 1
    with TAPE.open() as f:
        tape = json.load(f)
    trades = tape.get("trades") or []

    raw_buys = 0
    clustered: dict[tuple[str, str, str], dict] = {}
    for t in trades:
        if not is_open_market_buy(t):
            continue
        raw_buys += 1
        fid = t.get("filer_id") or ""
        code = (t.get("ticker") or "").upper()
        filed = (t.get("filed_date") or "")[:10]
        if not fid or not code or len(filed) < 10:
            continue
        key = (fid, code, filed)
        prev = clustered.get(key)
        if prev is None:
            clustered[key] = {
                "id": fid,
                "name": t.get("filer") or fid,
                "title": t.get("title") or "",
                "firm": t.get("company") or "",
                "ticker": code,
                "filed": filed,
                "trade": (t.get("trade_date") or "")[:10],
            }
        else:
            # Prefer a filled firm/title if a later lot has one.
            if t.get("company") and not prev.get("firm"):
                prev["firm"] = t.get("company")
            if t.get("title") and not prev.get("title"):
                prev["title"] = t.get("title")

    # As-of = latest filed print (tape is the clock for the 18-month floor).
    filed_dates = [b["filed"] for b in clustered.values()]
    tape_asof = max(filed_dates) if filed_dates else (tape.get("collected") or "")[:10]
    try:
        cutoff = (datetime.strptime(tape_asof, "%Y-%m-%d") - timedelta(days=WINDOW_DAYS)).strftime(
            "%Y-%m-%d"
        )
    except ValueError:
        cutoff = "2025-01-01"

    by_filer: dict[str, list[dict]] = defaultdict(list)
    for b in clustered.values():
        if b["filed"] >= cutoff:
            by_filer[b["id"]].append(b)

    price_cache: dict = {}
    price_asof = ""
    priced_buys = 0
    for buys in by_filer.values():
        for b in buys:
            closes = load_prices(b["ticker"], price_cache)
            if not closes:
                continue
            if closes[-1][0] > price_asof:
                price_asof = closes[-1][0]
            scored = {}
            for days in HORIZONS:
                h = horizon_return(closes, b["filed"], days)
                if not h:
                    continue
                vs = vs_ticker(closes, b.get("trade") or "", h["out"], h["ret"])
                if vs is not None:
                    h["vs"] = vs
                scored[days] = h
            if scored:
                b["h"] = scored
                priced_buys += 1

    filers = []
    horizon_counts = {d: 0 for d in HORIZONS}
    for fid, buys in by_filer.items():
        if len(buys) < MIN_BUYS:
            continue
        windows = {}
        for days in HORIZONS:
            scored = score_horizon(buys, days)
            if scored:
                windows[str(days)] = scored
                horizon_counts[days] += 1
        if not windows:
            continue
        firm, ticker, title = pick_firm(buys)
        sample = max(buys, key=lambda b: b["filed"])
        filers.append({
            "id": fid,
            "name": sample.get("name") or fid,
            "title": title,
            "firm": firm,
            "ticker": ticker,
            "n18": len(buys),
            **windows,
        })

    def sort_key(row: dict):
        w = row.get(str(DEFAULT_HORIZON)) or {}
        return (-(w.get("avg") or -99), -(w.get("hit") or 0), -row.get("n18", 0), row.get("name") or "")

    filers.sort(key=sort_key)
    for i, row in enumerate(filers, 1):
        row["rank"] = i

    method = (
        "Open-market buys only: Form 4 code P and SEDI nature 10. "
        "Awards, option exercises, gifts, private/prospectus prints, and sales are excluded. "
        "One copy trade per officer / ticker / filing date (lots on the same print collapse). "
        "Entry is the first prices/ close on or after the public filing date "
        f"(max {MAX_BAR_LAG_DAYS} calendar days). Exit is the first close on or after "
        "filing date + 30 / 90 / 180 calendar days (same lag cap). "
        "Average return is the equal-weight mean of per-ticker means, so one name cannot "
        "dominate. Hit rate is the share of copy trades with a positive return at that horizon. "
        f"Floor is {MIN_BUYS} clustered open-market buys in the last {WINDOW_DAYS} days "
        f"(~18 months) that also complete the horizon. "
        "Vs ticker is the copy return minus the same ticker from the insider’s trade-date "
        "close to the same exit close."
    )
    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00",
        "tapeCollected": tape.get("collected") or "",
        "priceAsof": price_asof,
        "asof": tape_asof,
        "cutoff": cutoff,
        "windowDays": WINDOW_DAYS,
        "minBuys": MIN_BUYS,
        "defaultHorizon": DEFAULT_HORIZON,
        "horizons": list(HORIZONS),
        "method": method,
        "disclaimer": (
            "Not investment advice. Hypothetical paper copies from the public print date, "
            "not the officer’s fill. Past hit rates do not mean the next filing works."
        ),
        "stats": {
            "rawBuys": raw_buys,
            "clustered18": sum(len(v) for v in by_filer.values()),
            "priced18": priced_buys,
            "filers": len(filers),
            "filers30": horizon_counts[30],
            "filers90": horizon_counts[90],
            "filers180": horizon_counts[180],
        },
        "filers": filers,
    }

    with DEST.open("w") as f:
        json.dump(out, f, separators=(",", ":"))
        f.write("\n")
    print(
        "wrote {path} ({kb:.0f} KB)  filers={n}  30/90/180={a}/{b}/{c}  "
        "minBuys={m}  asof={asof}  cutoff={cut}".format(
            path=DEST.name,
            kb=DEST.stat().st_size / 1024,
            n=len(filers),
            a=horizon_counts[30],
            b=horizon_counts[90],
            c=horizon_counts[180],
            m=MIN_BUYS,
            asof=tape_asof,
            cut=cutoff,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
