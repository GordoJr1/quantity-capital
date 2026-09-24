#!/usr/bin/env python3
"""Append new Yahoo daily closes into prices/*.json, and back-adjust stock splits.

Stdlib only. Run by hand (or after the off-repo morning collector, which also
appends to prices/). The daily-update Action no longer runs it.

New bars are appended; existing bars are only rewritten to undo a confirmed
split. Yahoo leaves many OTC / ADR / TSX-V splits unadjusted, and append-only
history never picks up Yahoo's later adjustments, so a split shows up as a fake
-50% .. -90% (or +100% .. +900% for a consolidation) step. A step is adjusted
only when Yahoo's current history has already rescaled the pre-split closes, or
when the volume signature says split (no jump-day spike, volume shifts by the
ratio). Crashes that happen to land near 1/2 keep their prices and are reported.
`--repair-splits` scans the whole history without appending bars.

Files marked missing:true with no bars are skipped.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from qc_common import atomic_write_json

ROOT = Path(__file__).resolve().parent
PRICES = ROOT / "prices"
YAHOO = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
UA = (
    "Mozilla/5.0 (compatible; QuantityCapital/1.0; "
    "+https://gordojr1.github.io/quantity-capital/)"
)
EXCHANGE_SUFFIXES = {
    ".TO",
    ".V",
    ".CN",
    ".NE",
    ".AX",
    ".L",
    ".PA",
    ".HK",
    ".SW",
    ".DE",
    ".MI",
    ".AS",
    ".BR",
    ".OL",
    ".ST",
    ".HE",
    ".CO",
    ".IC",
    ".IR",
    ".MC",
    ".LS",
}
# Price ratios treated as a possible split / consolidation step (100x is align_px's pence case).
SPLIT_FACTORS = (2, 3, 4, 5, 8, 10, 15, 20, 25, 30, 40, 50)
SPLIT_TOL = 0.04
# A step must be clean: this many bars each side stay within STEP_BAND of their level.
STEP_BARS = 10
STEP_MIN_POST = 5
STEP_BAND = 0.20
# Tick-size noise on sub-20c names flips between price levels; never treat it as a split.
SPLIT_MIN_PX = 0.2
# Normal runs only re-check recent steps; --repair-splits checks the whole file.
RECENT_SPLIT_DAYS = 60
OVERLAP_DAYS = 14


def compact_px(px: float) -> float:
    x = float(px)
    nd = 4 if abs(x) < 0.05 else 2
    r = round(x, nd)
    if r == int(r):
        return float(int(r))
    return r


def align_px(px: float, last_px: float | None) -> float:
    """Undo LSE pence-vs-pounds (and similar) 100x unit mismatches."""
    px = compact_px(px)
    if not last_px or last_px <= 0 or px <= 0:
        return px
    ratio = px / last_px
    if 50 <= ratio <= 200:
        return compact_px(px / 100.0)
    if 0.005 <= ratio <= 0.02:
        return compact_px(px * 100.0)
    return px


def yahoo_symbol(code: str) -> str:
    """Map a prices/ filename ticker onto Yahoo's symbol."""
    c = (code or "").strip()
    if not c:
        return c
    upper = c.upper()
    for suf in EXCHANGE_SUFFIXES:
        if upper.endswith(suf):
            return c
    if "." in c:
        left, right = c.rsplit(".", 1)
        # Share-class suffixes (.A / .B). .C is usually CSE, not a class share.
        if right.upper() in {"A", "B"} and left.replace(".", "").isalnum():
            return f"{left}-{right}"
    return c


def symbol_candidates(code: str, src: str | None = None) -> list[str]:
    out: list[str] = []

    def add(s: str | None) -> None:
        s = (s or "").strip()
        if s and s not in out:
            out.append(s)

    add(yahoo_symbol(code))
    add(code)
    if src:
        add(yahoo_symbol(src))
        add(src)
    if code.upper().endswith(".C"):
        add(code[:-2] + ".CN")
    return out


def bar_date(ts: int, gmtoffset: int) -> str:
    dt = datetime.fromtimestamp(int(ts) + int(gmtoffset or 0), tz=timezone.utc)
    return dt.strftime("%Y-%m-%d")


def utc_ts(day: str) -> int:
    d = datetime.strptime(day, "%Y-%m-%d")
    return int(d.replace(tzinfo=timezone.utc).timestamp())


def fetch_chart(symbol: str, period1: int, timeout: float = 20.0, period2: int | None = None) -> dict:
    if period2 is None:
        period2 = int(time.time()) + 120
    if period2 <= period1:
        period2 = period1 + 86400
    url = (
        YAHOO.format(symbol=urllib.parse.quote(symbol, safe=".-"))
        + f"?interval=1d&period1={period1}&period2={period2}&events=split"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/json"},
    )
    last_err: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            last_err = e
            if e.code in (404, 400):
                raise
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last_err = e
            if attempt < 3:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise
    raise last_err or RuntimeError("fetch failed")


def parse_chart(payload: dict, now: float | None = None) -> dict:
    """Daily closes, volumes, and split events. Drops a still-trading session's live bar."""
    chart = payload.get("chart") or {}
    err = chart.get("error")
    if err:
        raise RuntimeError(err.get("description") or str(err))
    out = {"bars": [], "vol": {}, "currency": "", "splits": []}
    results = chart.get("result") or []
    if not results:
        return out
    row = results[0]
    meta = row.get("meta") or {}
    gmtoffset = int(meta.get("gmtoffset") or 0)
    out["currency"] = (meta.get("currency") or "").upper()
    live_date = ""
    regular = (meta.get("currentTradingPeriod") or {}).get("regular") or {}
    try:
        start, end = int(regular["start"]), int(regular["end"])
    except (KeyError, TypeError, ValueError):
        start = end = 0
    now = time.time() if now is None else now
    if start and start <= now < end:
        live_date = bar_date(start, int(regular.get("gmtoffset", gmtoffset) or 0))
    ts = row.get("timestamp") or []
    quote = ((row.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    for idx, (t, c) in enumerate(zip(ts, closes)):
        if t is None or c is None:
            continue
        d = bar_date(int(t), gmtoffset)
        if d == live_date:
            continue
        px = compact_px(c)
        if px <= 0:
            continue
        out["bars"].append((d, px))
        v = volumes[idx] if idx < len(volumes) else None
        if v is not None:
            out["vol"][d] = int(v)
    for ev in ((row.get("events") or {}).get("splits") or {}).values():
        try:
            ratio = float(ev["numerator"]) / float(ev["denominator"])
            out["splits"].append((bar_date(int(ev["date"]), gmtoffset), ratio))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue
    return out


def parse_bars(payload: dict) -> tuple[list[tuple[str, float]], str]:
    got = parse_chart(payload)
    return got["bars"], got["currency"]


def nominal_factor(ratio: float) -> float | None:
    """Split factor (old / new price) near `ratio`: 2 for a 2-for-1, 0.1 for a 1-for-10 consolidation."""
    for f in SPLIT_FACTORS:
        for ff in (float(f), 1.0 / f):
            if abs(ratio / ff - 1.0) <= SPLIT_TOL:
                return ff
    return None


def split_steps(series: list, since: str = "") -> list[tuple[int, float]]:
    """Indexes where the series steps cleanly by a split ratio and holds the new level."""
    out: list[tuple[int, float]] = []
    for i in range(1, len(series)):
        if str(series[i][0])[:10] < since:
            continue
        a, b = float(series[i - 1][1]), float(series[i][1])
        if a <= 0 or b <= 0 or min(a, b) < SPLIT_MIN_PX:
            continue
        f = nominal_factor(a / b)
        if f is None:
            continue
        pre = [float(x[1]) for x in series[max(0, i - STEP_BARS):i]]
        post = [float(x[1]) for x in series[i:i + STEP_BARS]]
        if len(post) < STEP_MIN_POST:
            continue
        if any(abs(x / a - 1.0) > STEP_BAND for x in pre):
            continue
        if any(abs(x / b - 1.0) > STEP_BAND for x in post):
            continue
        out.append((i, f))
    return out


def split_evidence(series: list, i: int, f: float, yahoo: dict) -> str:
    """Why the step at i is a split, or "" when it may be a real move."""
    closes = dict(yahoo.get("bars") or [])
    vols = yahoo.get("vol") or {}
    p_day, d_day = str(series[i - 1][0])[:10], str(series[i][0])[:10]
    sp, sd = float(series[i - 1][1]), float(series[i][1])
    yp, yd = closes.get(p_day), closes.get(d_day)
    if not yp or not yd:
        return ""
    k_pre, k_post = sp / yp, sd / yd
    change = k_pre / k_post
    if abs(change / f - 1.0) <= SPLIT_TOL:
        return "yahoo-adjusted"
    if abs(change - 1.0) > SPLIT_TOL or abs(k_post - 1.0) > 0.10:
        return ""
    step_dt = datetime.strptime(d_day, "%Y-%m-%d")
    for ev_day, ratio in yahoo.get("splits") or []:
        try:
            near = abs((datetime.strptime(ev_day, "%Y-%m-%d") - step_dt).days) <= 3
        except ValueError:
            continue
        if near and abs(ratio / f - 1.0) <= SPLIT_TOL:
            return "yahoo-event"
    days = sorted(vols)
    before = [vols[x] for x in days if x < d_day and vols[x] > 0][-STEP_BARS:]
    after = [vols[x] for x in days if x > d_day and vols[x] > 0][:STEP_BARS]
    v_day = vols.get(d_day) or 0
    if v_day <= 0 or len(before) < 5 or len(after) < 5:
        return ""
    pre_m, post_m = statistics.median(before), statistics.median(after)
    # A crash trades a multiple of normal volume on the day; a split day looks like the new normal.
    if v_day > post_m:
        return ""
    shift = post_m / pre_m
    if f > 1 and shift >= max(1.5, f / 2.0):
        return "volume"
    if f < 1 and shift <= min(0.67, 2.0 * f):
        return "volume"
    return ""


def apply_splits(series: list, steps: list[tuple[int, float]]) -> None:
    """Divide every bar before each step by its factor (cumulative for several steps)."""
    at = {i: f for i, f in steps}
    cum = 1.0
    for k in range(len(series) - 1, -1, -1):
        if k + 1 in at:
            cum *= at[k + 1]
        if cum != 1.0:
            series[k] = [series[k][0], compact_px(float(series[k][1]) / cum)]


def yahoo_window(syms: list[str], lo: str, hi: str) -> dict | None:
    p1 = utc_ts(lo) - 5 * 86400
    p2 = utc_ts(hi) + 6 * 86400
    for sym in syms:
        try:
            got = parse_chart(fetch_chart(sym, p1, period2=p2))
        except Exception:
            continue
        if got["bars"]:
            return got
    return None


def check_splits(code: str, src: str | None, series: list, since: str, prefer: str = "") -> tuple[list[dict], list[dict]]:
    """Confirm split-shaped steps against Yahoo and back-adjust the confirmed ones in place."""
    steps = split_steps(series, since)
    if not steps:
        return [], []
    syms = ([prefer] if prefer else []) + [s for s in symbol_candidates(code, src) if s != prefer]
    accepted: list[tuple[int, float]] = []
    fixed: list[dict] = []
    flagged: list[dict] = []
    for i, f in steps:
        lo = str(series[max(0, i - STEP_BARS - 2)][0])[:10]
        hi = str(series[min(len(series) - 1, i + STEP_BARS + 1)][0])[:10]
        yahoo = yahoo_window(syms, lo, hi)
        why = split_evidence(series, i, f, yahoo) if yahoo else ""
        row = {"date": str(series[i][0])[:10], "factor": f, "from": series[i - 1][1], "to": series[i][1]}
        if why:
            row["why"] = why
            accepted.append((i, f))
            fixed.append(row)
        else:
            row["why"] = "no Yahoo data" if yahoo is None else "not confirmed"
            flagged.append(row)
    if accepted:
        apply_splits(series, accepted)
    return fixed, flagged


def load_price_file(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def write_price_file(path: Path, payload: dict) -> None:
    atomic_write_json(path, payload)


def last_bar_date(payload: dict) -> str:
    bars = payload.get("c") or []
    if not bars:
        return ""
    return str(bars[-1][0])[:10]


def period1_for(last: str) -> int:
    if not last:
        start = date.today() - timedelta(days=400)
    else:
        try:
            start = datetime.strptime(last, "%Y-%m-%d").date() + timedelta(days=1)
        except ValueError:
            start = date.today() - timedelta(days=40)
    dt = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    return int(dt.timestamp())


def fetch_new_bars(code: str, src: str | None, last: str) -> dict:
    p1 = period1_for(last)
    errors: list[str] = []
    missing = 0
    empty: dict | None = None
    for sym in symbol_candidates(code, src):
        try:
            got = parse_chart(fetch_chart(sym, p1))
        except urllib.error.HTTPError as e:
            errors.append(f"{sym}: HTTP {e.code}")
            if e.code in (404, 400):
                missing += 1
            continue
        except Exception as e:
            errors.append(f"{sym}: {type(e).__name__}")
            continue
        got["via"] = sym
        if got["bars"]:
            return got
        empty = empty or got
    if empty is not None:
        return empty
    return {"bars": [], "errors": errors, "all_missing": bool(errors) and missing == len(errors)}


def refresh_one(path: Path, dry_run: bool, repair_only: bool = False, check_all: bool = False) -> dict:
    payload = load_price_file(path)
    code = payload.get("t") or path.stem
    if payload.get("missing") and not (payload.get("c") or []):
        return {"code": code, "status": "skip-missing"}
    series = [list(b) for b in (payload.get("c") or [])]
    last = last_bar_date(payload)
    today = date.today().isoformat()
    status = "current"
    added: list[tuple[str, float]] = []
    used = ""
    currency = ""
    rec: dict = {"code": code, "last": last}

    if not repair_only and last < today:
        got = fetch_new_bars(code, payload.get("src"), last)
        used = got.get("via") or ""
        currency = got.get("currency") or ""
        if got.get("errors") is not None and not got["bars"]:
            rec["error"] = "; ".join(got["errors"][:3])
            rec["status"] = "not-found" if got.get("all_missing") else "error"
            return rec
        known = {str(b[0])[:10] for b in series}
        last_px = float(series[-1][1]) if series else None
        for d, px in got["bars"]:
            if d in known or d <= last:
                continue
            px = align_px(px, last_px)
            series.append([d, px])
            known.add(d)
            added.append((d, px))
            last_px = px
        status = "updated" if added else "no-new"

    since = "" if (repair_only or check_all) else (date.today() - timedelta(days=RECENT_SPLIT_DAYS)).isoformat()
    fixed, flagged = check_splits(code, payload.get("src"), series, since, prefer=used)
    if fixed and not added:
        status = "split-fixed"
    rec.update({"status": status, "via": used})
    if fixed:
        rec["splits"] = fixed
    if flagged:
        rec["flagged"] = flagged
    if not (added or fixed):
        return rec

    payload["c"] = series
    if "px" in payload:
        payload["px"] = series[-1][1]
    if "cur" in payload and currency:
        payload["cur"] = currency
    if not dry_run:
        write_price_file(path, payload)
    rec["last"] = series[-1][0]
    if added:
        rec["added"] = len(added)
        rec["new"] = added[-1]
    return rec


def self_test() -> int:
    assert yahoo_symbol("AAPL") == "AAPL"
    assert yahoo_symbol("BRK.A") == "BRK-A"
    assert yahoo_symbol("BF.B") == "BF-B"
    assert yahoo_symbol("ABX.TO") == "ABX.TO"
    assert yahoo_symbol("AAG.V") == "AAG.V"
    assert yahoo_symbol("ACDX.CN") == "ACDX.CN"
    assert yahoo_symbol("GAL.L") == "GAL.L"
    assert yahoo_symbol("NVA.AX") == "NVA.AX"
    assert yahoo_symbol("NINE.C") == "NINE.C"
    assert compact_px(326.57000732421875) == 326.57
    assert compact_px(761245.0) == 761245.0
    assert compact_px(0.7099999785423279) == 0.71
    assert compact_px(0.00951) == 0.0095
    assert abs(align_px(32.0, 0.32) - 0.32) < 1e-9
    assert abs(align_px(31.5, 0.32) - 0.32) < 1e-9 or abs(align_px(31.5, 0.32) - 0.31) < 1e-9
    assert align_px(326.57, 315.34) == 326.57
    assert symbol_candidates("NINE.C")[0] == "NINE.C"
    assert "NINE.CN" in symbol_candidates("NINE.C")

    assert nominal_factor(113.83 / 28.74) == 4.0
    assert nominal_factor(0.66 / 6.7) == 0.1
    assert nominal_factor(1.3) is None

    days = [(date(2026, 1, 1) + timedelta(days=k)).isoformat() for k in range(30)]
    pre = [100.0 + k * 0.1 for k in range(15)]
    post = [25.0 + k * 0.1 for k in range(15)]
    series = [[d, px] for d, px in zip(days, pre + post)]
    assert split_steps(series) == [(15, 4.0)]
    assert split_steps(series, since=days[20]) == []
    noisy = [[d, 1.0 if k % 3 else 2.0] for k, d in enumerate(days)]
    assert split_steps(noisy) == []

    def yahoo(closes, vols, splits=()):
        return {"bars": list(zip(days, closes)), "vol": dict(zip(days, vols)), "splits": list(splits)}

    raw = [px for _, px in series]
    split_vol = [1000] * 15 + [4200] * 15
    assert split_evidence(series, 15, 4.0, yahoo(raw, split_vol)) == "volume"
    crash_vol = [1000] * 15 + [30000] + [4200] * 14
    assert split_evidence(series, 15, 4.0, yahoo(raw, crash_vol)) == ""
    flat_vol = [1000] * 30
    assert split_evidence(series, 15, 4.0, yahoo(raw, flat_vol)) == ""
    assert split_evidence(series, 15, 4.0, yahoo(raw, flat_vol, [(days[15], 4.0)])) == "yahoo-event"
    adjusted = [px / 4.0 for px in pre] + post
    assert split_evidence(series, 15, 4.0, yahoo(adjusted, crash_vol)) == "yahoo-adjusted"

    fixed = [list(b) for b in series]
    apply_splits(fixed, [(15, 4.0)])
    assert fixed[14][1] == compact_px(pre[14] / 4.0) and fixed[15][1] == post[0]
    assert split_steps(fixed) == []
    two = [[d, px] for d, px in zip(days, [400.0] * 10 + [200.0] * 10 + [100.0] * 10)]
    apply_splits(two, [(10, 2.0), (20, 2.0)])
    assert {px for _, px in two} == {100.0}

    t0 = utc_ts("2026-03-02") + 14 * 3600 + 30 * 60
    live = {"chart": {"result": [{
        "meta": {"gmtoffset": -18000, "currency": "usd", "currentTradingPeriod": {
            "regular": {"start": t0, "end": t0 + 23400, "gmtoffset": -18000}}},
        "timestamp": [t0 - 86400, t0 + 3600],
        "indicators": {"quote": [{"close": [10.0, 10.5], "volume": [100, 50]}]},
        "events": {"splits": {"x": {"date": t0 - 86400, "numerator": 2, "denominator": 1}}},
    }]}}
    got = parse_chart(live, now=t0 + 7200)
    assert [d for d, _ in got["bars"]] == ["2026-03-01"], got["bars"]
    assert got["splits"] == [("2026-03-01", 2.0)] and got["currency"] == "USD"
    assert len(parse_chart(live, now=t0 + 30000)["bars"]) == 2
    print("self-test ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=0, help="Max files to consider")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--only", action="append", default=[], help="Ticker(s) to refresh")
    ap.add_argument("--repair-splits", action="store_true", help="Only back-adjust split steps, whole history; no new bars")
    ap.add_argument("--check-all-splits", action="store_true", help=f"Also re-check steps older than {RECENT_SPLIT_DAYS} days")
    args = ap.parse_args()
    if args.self_test:
        return self_test()

    files = sorted(PRICES.glob("*.json"))
    if args.only:
        want = {t.upper() for t in args.only}
        files = [p for p in files if p.stem.upper() in want]
    if args.limit:
        files = files[: args.limit]
    if not files:
        print("no price files", file=sys.stderr)
        return 1

    stats = {"updated": 0, "split-fixed": 0, "current": 0, "no-new": 0, "not-found": 0, "skip-missing": 0, "error": 0}
    added_bars = 0
    samples: list[str] = []
    split_lines: list[str] = []
    flag_lines: list[str] = []
    workers = max(1, min(args.workers, 32))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {
            pool.submit(refresh_one, path, args.dry_run, args.repair_splits, args.check_all_splits): path
            for path in files
        }
        for fut in as_completed(futs):
            path = futs[fut]
            try:
                rec = fut.result()
            except Exception as e:
                stats["error"] += 1
                print(f"error {path.stem}: {type(e).__name__}: {e}", file=sys.stderr)
                continue
            status = rec.get("status") or "error"
            stats[status] = stats.get(status, 0) + 1
            if status == "error" and len(samples) < 12:
                samples.append(f"{rec['code']} error {rec.get('error', '')}")
            for s in rec.get("splits") or []:
                split_lines.append(f"{rec['code']} {s['date']} {s['from']} -> {s['to']} /{s['factor']:g} ({s['why']})")
            for s in rec.get("flagged") or []:
                flag_lines.append(f"{rec['code']} {s['date']} {s['from']} -> {s['to']} x{1 / s['factor']:g} ({s['why']})")
            if rec.get("added"):
                added_bars += int(rec["added"])
                if len(samples) < 12:
                    new = rec.get("new") or ("", "")
                    samples.append(f"{rec['code']} +{rec['added']} last={new[0]} {new[1]}")

    print(
        "prices {mode} files={n} updated={u} bars+={b} splitFixed={sf} current={c} none={nn} "
        "notFound={nf} missing={m} error={e}".format(
            mode="dry-run" if args.dry_run else "wrote",
            n=len(files),
            u=stats["updated"],
            b=added_bars,
            sf=len(split_lines),
            c=stats["current"],
            nn=stats["no-new"],
            nf=stats["not-found"],
            m=stats["skip-missing"],
            e=stats["error"],
        )
    )
    for line in samples:
        print(" ", line)
    for line in sorted(split_lines):
        print("  split", line)
    for line in sorted(flag_lines):
        print("  kept (not a confirmed split)", line)
    attempted = stats["updated"] + stats["no-new"] + stats["error"] + stats["not-found"]
    if attempted >= 50 and stats["error"] / attempted > 0.3:
        print("too many Yahoo failures", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
