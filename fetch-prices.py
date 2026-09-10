#!/usr/bin/env python3
"""Append new Yahoo daily closes into prices/*.json.

Stdlib only. Used by .github/workflows/daily-update.yml so ticker charts
and paper.html stay current even when the off-repo tape collector is idle.

Existing bars are never rewritten (matches the collector's append-only diffs).
Files marked missing:true with no bars are skipped.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

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


def fetch_chart(symbol: str, period1: int, timeout: float = 20.0) -> dict:
    period2 = int(time.time()) + 120
    if period2 <= period1:
        period2 = period1 + 86400
    url = (
        YAHOO.format(symbol=urllib.parse.quote(symbol, safe=".-"))
        + f"?interval=1d&period1={period1}&period2={period2}&events=history"
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


def parse_bars(payload: dict) -> tuple[list[tuple[str, float]], str]:
    chart = payload.get("chart") or {}
    err = chart.get("error")
    if err:
        raise RuntimeError(err.get("description") or str(err))
    results = chart.get("result") or []
    if not results:
        return [], ""
    row = results[0]
    meta = row.get("meta") or {}
    gmtoffset = int(meta.get("gmtoffset") or 0)
    currency = (meta.get("currency") or "").upper()
    ts = row.get("timestamp") or []
    quote = ((row.get("indicators") or {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    bars: list[tuple[str, float]] = []
    for t, c in zip(ts, closes):
        if t is None or c is None:
            continue
        px = compact_px(c)
        if px <= 0:
            continue
        bars.append((bar_date(int(t), gmtoffset), px))
    return bars, currency


def load_price_file(path: Path) -> dict:
    with path.open() as f:
        return json.load(f)


def write_price_file(path: Path, payload: dict) -> None:
    with path.open("w") as f:
        json.dump(payload, f, separators=(",", ":"))
        f.write("\n")


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


def refresh_one(path: Path, dry_run: bool) -> dict:
    payload = load_price_file(path)
    code = payload.get("t") or path.stem
    if payload.get("missing") and not (payload.get("c") or []):
        return {"code": code, "status": "skip-missing"}
    last = last_bar_date(payload)
    today = date.today().isoformat()
    if last >= today:
        return {"code": code, "status": "current", "last": last}

    known = {str(b[0])[:10] for b in (payload.get("c") or [])}
    p1 = period1_for(last)
    bars: list[tuple[str, float]] = []
    currency = ""
    used = ""
    errors: list[str] = []
    for sym in symbol_candidates(code, payload.get("src")):
        try:
            raw = fetch_chart(sym, p1)
            got, currency = parse_bars(raw)
            used = sym
            bars = got
            if got:
                break
        except Exception as e:
            errors.append(f"{sym}: {type(e).__name__}")
            continue
    if not bars:
        return {
            "code": code,
            "status": "no-new",
            "last": last,
            "error": "; ".join(errors[:3]),
        }

    added: list[tuple[str, float]] = []
    series = list(payload.get("c") or [])
    last_px = float(series[-1][1]) if series else None
    for d, px in bars:
        if d in known or d <= last:
            continue
        px = align_px(px, last_px)
        series.append([d, px])
        known.add(d)
        added.append((d, px))
        last_px = px
    if not added:
        return {"code": code, "status": "no-new", "last": last, "via": used}

    payload["c"] = series
    if "px" in payload:
        payload["px"] = series[-1][1]
    if "cur" in payload and currency:
        payload["cur"] = currency
    if not dry_run:
        write_price_file(path, payload)
    return {
        "code": code,
        "status": "updated",
        "last": series[-1][0],
        "added": len(added),
        "via": used,
        "new": added[-1],
    }


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
    print("self-test ok")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--limit", type=int, default=0, help="Max files to consider")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--self-test", action="store_true")
    ap.add_argument("--only", action="append", default=[], help="Ticker(s) to refresh")
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

    stats = {"updated": 0, "current": 0, "no-new": 0, "skip-missing": 0, "error": 0}
    added_bars = 0
    samples: list[str] = []
    workers = max(1, min(args.workers, 32))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(refresh_one, path, args.dry_run): path for path in files}
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
            if status == "updated":
                added_bars += int(rec.get("added") or 0)
                if len(samples) < 12:
                    new = rec.get("new") or ("", "")
                    samples.append(f"{rec['code']} +{rec['added']} last={new[0]} {new[1]}")

    print(
        "prices {mode} files={n} updated={u} bars+={b} current={c} none={nn} "
        "missing={m} error={e}".format(
            mode="dry-run" if args.dry_run else "wrote",
            n=len(files),
            u=stats["updated"],
            b=added_bars,
            c=stats["current"],
            nn=stats["no-new"],
            m=stats["skip-missing"],
            e=stats["error"],
        )
    )
    for line in samples:
        print(" ", line)
    attempted = stats["updated"] + stats["no-new"] + stats["error"]
    if attempted >= 50 and stats["error"] / attempted > 0.3:
        print("too many Yahoo failures", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
