"""Live since-filing return vs SPY for each repeatable-board officer.

One pick per filer on insider-repeatable.json: their most recent qualifying
open-market buy (same rules as build-insider-repeatable.py). Entry is the
first prices/ close strictly after the filing date. Exit is the latest close
on file. Excess is that return minus SPY over the same two dates.

Reads qc.sqlite only for the filer list already ingested into
insider_repeatable_filers. Buy rows come from insider-trades-lite.json
because insider_trades does not store Form 4 code or SEDI nature. Prices are
local prices/<TICKER>.json via qc_common.load_prices. No network.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRIPTS = HERE.parent
for _p in (str(HERE), str(SCRIPTS), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from paths import EXPORT_DIR, QC_ROOT  # noqa: E402
from qc_common import (  # noqa: E402
    first_after,
    load_form4,
    load_prices,
    mean,
    median,
    round_ret,
    utc_stamp,
)
from qc_io import atomic_write_json  # noqa: E402

DEST_NAME = "insider-repeatable-scorecard.json"
TAPE_NAME = "insider-trades-lite.json"
FORM4_NAME = "insider-form4.json"
REPEATABLE_NAME = "insider-repeatable.json"

METHOD = (
    "One row per officer on the repeatable board. The buy is that officer's most recent "
    "qualifying open-market purchase inside the board's window: Form 4 code P or SEDI "
    "nature 10, scheduled 10b5-1 plan buys excluded, lots on the same officer / ticker / "
    "filing date collapsed, then the latest filing date (ticker breaks ties). Same test as "
    "build-insider-repeatable.py is_open_market_buy. "
    "Entry is the first prices/ close strictly after the filing date, at most "
    "10 calendar days later, because an after-hours print cannot be bought at that day's close. "
    "Exit is the latest close on file. Return is exit/entry - 1. "
    "SPY return is the SPY close on that same exit date divided by the SPY close on that "
    "same entry date, minus 1. Excess is the pick return minus the SPY return. "
    "A pick with no entry, a stale entry, or no SPY close on those dates is left blank. "
    "Prices are local prices/<TICKER>.json (qc.sqlite has no price history). No network fetch. "
    "Buys are read from insider-trades-lite.json; qc.sqlite insider_trades has no code or nature column."
)

DISCLAIMER = (
    "Past results, not a forecast. The mark is from the filing date to the latest close "
    "versus SPY over those same dates. Not investment advice."
)


def log(msg: str) -> None:
    print(msg, flush=True)


def load_builder():
    """The root repeatable builder, so the open-market test is not reimplemented."""
    name = "_qc_build_insider_repeatable"
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    path = QC_ROOT / "build-insider-repeatable.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def load_form4_trades(form4: dict) -> dict:
    trades = form4.get("trades") if isinstance(form4, dict) else None
    return trades if isinstance(trades, dict) else {}


def latest_open_market_buys(
    trades: list,
    form4_trades: dict,
    filer_ids: set[str],
    cutoff: str,
    is_open_market_buy: Callable[..., bool],
) -> dict[str, dict]:
    """Most recent clustered open-market buy per filer, matching the board's pick_firm key."""
    clustered: dict[tuple[str, str, str], dict] = {}
    for t in trades:
        if not isinstance(t, dict) or not is_open_market_buy(t, form4_trades):
            continue
        fid = t.get("filer_id") or ""
        if fid not in filer_ids:
            continue
        ticker = (t.get("ticker") or "").upper()
        filed = (t.get("filed_date") or "")[:10]
        if not ticker or len(filed) < 10:
            continue
        if cutoff and filed < cutoff:
            continue
        key = (fid, ticker, filed)
        if key not in clustered:
            clustered[key] = {"filer_id": fid, "ticker": ticker, "filed": filed}
    latest: dict[str, dict] = {}
    for buy in clustered.values():
        cur = latest.get(buy["filer_id"])
        if cur is None or (buy["filed"], buy["ticker"]) > (cur["filed"], cur["ticker"]):
            latest[buy["filer_id"]] = buy
    return latest


def _lag_days(target: str, bar_date: str) -> int | None:
    try:
        return (datetime.strptime(bar_date, "%Y-%m-%d") - datetime.strptime(target, "%Y-%m-%d")).days
    except ValueError:
        return None


def default_lag_ok(target: str, bar_date: str, min_lag: int = 0, max_lag: int = 10) -> bool:
    lag = _lag_days(target, bar_date)
    return lag is not None and min_lag <= lag <= max_lag


def score_window(
    closes: list | None,
    spy_by_date: dict[str, float],
    filed: str,
    *,
    max_lag: int = 10,
    lag_ok: Callable[..., bool] | None = None,
) -> dict | None:
    """Pick return and SPY return on the same entry and exit dates. None if a price is missing."""
    if not closes or not filed:
        return None
    ordered = sorted(closes, key=lambda row: row[0])
    entry = first_after(ordered, filed)
    if entry is None or entry[1] <= 0:
        return None
    ok = lag_ok or (lambda target, bar, min_lag=0: default_lag_ok(target, bar, min_lag, max_lag))
    if not ok(filed, entry[0], 1):
        return None
    exit_bar = ordered[-1]
    if exit_bar[0] < entry[0] or exit_bar[1] <= 0:
        return None
    spy_in = spy_by_date.get(entry[0])
    spy_out = spy_by_date.get(exit_bar[0])
    if spy_in is None or spy_out is None or spy_in <= 0 or spy_out <= 0:
        return None
    ret = exit_bar[1] / entry[1] - 1.0
    spy_ret = spy_out / spy_in - 1.0
    return {
        "filed": filed,
        "entry_date": entry[0],
        "entry_px": entry[1],
        "exit_date": exit_bar[0],
        "exit_px": exit_bar[1],
        "ret": round_ret(ret),
        "spy_ret": round_ret(spy_ret),
        "excess": round_ret(ret - spy_ret),
        "_excess": ret - spy_ret,
    }


def summarize(excesses: list[float], missing: int) -> dict[str, Any]:
    n = len(excesses)
    beat = sum(1 for x in excesses if x > 0)
    avg = mean(excesses)
    med = median(excesses)
    return {
        "n": n,
        "avg": round_ret(avg) if avg is not None else None,
        "median": round_ret(med) if med is not None else None,
        "hit": round_ret(beat / n) if n else None,
        "beat": beat,
        "missing": missing,
    }


def _load_filers(con: sqlite3.Connection) -> tuple[list[dict], str]:
    filers: list[dict] = []
    try:
        rows = con.execute(
            "SELECT filer_id, payload_json FROM insider_repeatable_filers"
        ).fetchall()
    except sqlite3.Error:
        rows = []
    for fid, payload in rows:
        try:
            rec = json.loads(payload) if payload else None
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rec.setdefault("id", fid)
            if rec.get("id"):
                filers.append(rec)
    cutoff = ""
    path = QC_ROOT / REPEATABLE_NAME
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            cutoff = str(data.get("cutoff") or "")[:10]
            if not filers:
                for rec in data.get("filers") or []:
                    if isinstance(rec, dict) and rec.get("id"):
                        filers.append(rec)
    return filers, cutoff


def _spy_index(cache: dict) -> dict[str, float]:
    closes = load_prices("SPY", cache)
    if not closes:
        return {}
    return {row[0]: row[1] for row in closes}


def _public_pick(ticker: str, scored: dict) -> dict:
    return {
        "ticker": ticker,
        "filed": scored["filed"],
        "entry_date": scored["entry_date"],
        "entry_px": scored["entry_px"],
        "exit_date": scored["exit_date"],
        "exit_px": scored["exit_px"],
        "ret": scored["ret"],
        "spy_ret": scored["spy_ret"],
        "excess": scored["excess"],
    }


def build(con: sqlite3.Connection) -> dict[str, Any]:
    filers, cutoff = _load_filers(con)
    if not filers:
        raise RuntimeError("no repeatable filers; keeping the previous scorecard")
    tape_path = QC_ROOT / TAPE_NAME
    if not tape_path.is_file():
        raise FileNotFoundError(f"missing {TAPE_NAME}")
    with tape_path.open(encoding="utf-8-sig") as f:
        tape = json.load(f)
    trades = tape.get("trades") if isinstance(tape, dict) else None
    if not isinstance(trades, list):
        raise ValueError(f"{TAPE_NAME} has no trades list")
    form4, form4_warn = load_form4(QC_ROOT / FORM4_NAME)
    if form4_warn:
        log("repeatable scorecard: " + form4_warn)
    builder = load_builder()
    filer_ids = {rec["id"] for rec in filers if rec.get("id")}
    latest = latest_open_market_buys(
        trades,
        load_form4_trades(form4),
        filer_ids,
        cutoff,
        builder.is_open_market_buy,
    )
    cache: dict = {}
    spy_by_date = _spy_index(cache)
    if not spy_by_date:
        raise RuntimeError("prices/SPY.json missing or empty; keeping the previous scorecard")
    lag_ok = builder.bar_lag_ok
    max_lag = int(builder.MAX_BAR_LAG_DAYS)

    filers.sort(key=lambda rec: (rec.get("rank") if isinstance(rec.get("rank"), int) else 10**9, rec.get("id") or ""))
    picks: dict[str, dict | None] = {}
    excesses: list[float] = []
    missing = 0
    ticker_mismatch = 0
    for rec in filers:
        fid = rec.get("id")
        if not fid or fid in picks:
            continue
        buy = latest.get(fid)
        scored = None
        if buy:
            if rec.get("ticker") and rec.get("ticker") != buy["ticker"]:
                ticker_mismatch += 1
            closes = load_prices(buy["ticker"], cache)
            scored = score_window(closes, spy_by_date, buy["filed"], max_lag=max_lag, lag_ok=lag_ok)
        if scored is None:
            picks[fid] = None
            missing += 1
            continue
        picks[fid] = _public_pick(buy["ticker"], scored)
        excesses.append(scored["_excess"])

    summary = summarize(excesses, missing)
    asof = ""
    for pick in picks.values():
        if pick and pick["exit_date"] > asof:
            asof = pick["exit_date"]
    if ticker_mismatch:
        log(f"repeatable scorecard: {ticker_mismatch} filers whose board ticker differs from the latest open-market buy")
    return {
        "generated": utc_stamp(),
        "asof": asof,
        "method": METHOD,
        "disclaimer": DISCLAIMER,
        "summary": summary,
        "picks": picks,
    }


def write_payload(payload: dict) -> None:
    text_path = QC_ROOT / DEST_NAME
    atomic_write_json(text_path, payload, compact=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    atomic_write_json(EXPORT_DIR / DEST_NAME, payload, compact=True)


def run(con: sqlite3.Connection) -> dict[str, Any]:
    t0 = time.perf_counter()
    try:
        payload = build(con)
    except Exception:
        log(f"repeatable scorecard failed after {time.perf_counter() - t0:.2f}s")
        raise
    write_payload(payload)
    summary = payload["summary"]
    log(
        "repeatable scorecard {sec:.2f}s  priced={n} missing={missing} "
        "avg={avg} median={median} hit={hit} asof={asof}".format(
            sec=time.perf_counter() - t0,
            n=summary["n"],
            missing=summary["missing"],
            avg=summary["avg"],
            median=summary["median"],
            hit=summary["hit"],
            asof=payload["asof"],
        )
    )
    return payload
