"""Paper board (backtest.json) — same rules as build-backtest.py.

Jev flags outlier legs (possible truncated history / wrong ticker) into
jev_decisions. Legs stay in the JSON contract; flags live in sqlite.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jev
from paths import DB_PATH, EXPORT_DIR, QC_ROOT, TICKERS
from tells import peek_collected
from _paper_lib import (
    MAX_ENTRY_LAG_DAYS,
    first_on_or_after,
    is_bond,
    is_chart_ticker,
    is_option_like,
    issuer_name,
    load_prices,
    mean,
    median,
    pick_name,
    round_px,
    round_ret,
)

PAPER_JSON = QC_ROOT / "backtest.json"

DDL = """
CREATE TABLE IF NOT EXISTS paper_legs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  filer_id TEXT NOT NULL,
  ticker TEXT NOT NULL,
  filed TEXT,
  trade TEXT,
  amt TEXT,
  entry_px REAL,
  entry_date TEXT,
  exit_px REAL,
  exit_date TEXT,
  ret REAL,
  jev_artifact_noul REAL,
  jev_quality REAL,
  jev_model TEXT
);
CREATE TABLE IF NOT EXISTS paper_filers (
  filer_id TEXT PRIMARY KEY,
  name TEXT,
  chamber TEXT,
  n INTEGER,
  skip INTEGER,
  avg REAL,
  med REAL,
  win INTEGER
);
CREATE TABLE IF NOT EXISTS paper_tickers (
  ticker TEXT PRIMARY KEY,
  name TEXT,
  n INTEGER,
  people INTEGER,
  avg REAL,
  med REAL,
  win INTEGER
);
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def paper_questions():
    from typesafe_sdk import Noul, Score

    return {
        "artifact": Noul(
            instructions=(
                "Is this paper-copy return likely a data artifact (wrong ticker, "
                "truncated price history, or a stub quote) rather than a real listed-stock move?"
            ),
            criteria={
                "true": "The return is implausible for a listed stock over this window.",
                "false": "The return is a plausible listed-stock move.",
            },
        ),
        "quality": Score(
            instructions="How usable is this leg as a STOCK Act copy-trade observation?",
            criteria=[
                "Broken or unusable (artifact, stub, or truncated history).",
                "Usable but unusual.",
                "Ordinary listed-stock copy leg.",
            ],
        ),
    }


def load_name_cands(con: sqlite3.Connection) -> dict[str, list[str]]:
    cands: dict[str, list[str]] = defaultdict(list)
    for code, name in con.execute("SELECT ticker, name FROM tickers"):
        c = (code or "").upper()
        cleaned = issuer_name(c, name or "")
        if cleaned:
            cands[c].append(cleaned)
    if TICKERS.exists():
        tfile = json.loads(TICKERS.read_text(encoding="utf-8-sig"))
        for code, meta in (tfile.get("tickers") or {}).items():
            c = str(code).upper()
            cleaned = issuer_name(c, (meta or {}).get("name") or "")
            if cleaned:
                cands[c].append(cleaned)
    for ticker, asset in con.execute(
        "SELECT ticker, asset FROM politician_trades WHERE ticker IS NOT NULL AND ticker != ''"
    ):
        c = (ticker or "").upper()
        if not is_chart_ticker(c):
            continue
        asset_name = issuer_name(c, asset or "")
        if asset_name:
            cands[c].append(asset_name)
    return cands


def compute_paper(con: sqlite3.Connection, skip_jev: bool = False) -> dict[str, Any]:
    name_cands = load_name_cands(con)
    trades = con.execute(
        """
        SELECT trade_id, filer, filer_id, chamber, ticker, asset, asset_type, side,
               amount_raw, trade_date, filed_date
        FROM politician_trades
        WHERE side = 'purchase'
        """
    )
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
    filers: dict[str, dict] = {}
    price_asof = ""

    for r in trades:
        t = {
            "id": r[0],
            "filer": r[1],
            "filer_id": r[2],
            "chamber": r[3],
            "ticker": r[4],
            "asset": r[5],
            "asset_type": r[6],
            "side": r[7],
            "amount": r[8],
            "trade_date": r[9],
            "filed_date": r[10],
        }
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
        rec["legs"].append(
            {
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
                "filer_id": fid,
            }
        )
        stats["priced"] += 1

    people = []
    ticker_acc: dict[str, dict] = {}
    all_legs = []
    for rec in filers.values():
        legs = rec["legs"]
        if not legs:
            continue
        legs.sort(key=lambda x: (x["filed"], x["t"]), reverse=True)
        all_legs.extend(legs)
        export_legs = []
        for leg in legs:
            row = dict(leg)
            row.pop("name", None)
            row.pop("filer_id", None)
            if not row.get("amt"):
                row.pop("amt", None)
            if not row.get("trade"):
                row.pop("trade", None)
            if row.get("inD") == row.get("filed"):
                row.pop("inD", None)
            export_legs.append(row)
        rets = [leg["ret"] for leg in legs]
        n = len(legs)
        people.append(
            {
                "id": rec["id"],
                "name": rec["name"],
                "chamber": rec["chamber"],
                "n": n,
                "skip": rec["skip"],
                "avg": round_ret(mean(rets)),
                "med": round_ret(median(rets)),
                "win": sum(1 for r in rets if r > 0),
                "legs": export_legs,
            }
        )
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
        tickers.append(
            {
                "t": code,
                "name": pick_name(code, name_cands.get(code) or [code]),
                "n": len(rets),
                "people": len(acc["people"]),
                "avg": round_ret(mean(rets)),
                "med": round_ret(median(rets)),
                "win": sum(1 for r in rets if r > 0),
            }
        )
    tickers.sort(key=lambda r: (-r["avg"], -r["n"], r["t"]))

    jev_stats = flag_outlier_legs(con, all_legs, skip_jev)

    tape = None
    try:
        tape = con.execute("SELECT value FROM meta WHERE key='tape_collected'").fetchone()
    except sqlite3.Error:
        tape = None

    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00",
        "tapeCollected": (tape[0] if tape else None) or peek_collected() or "",
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
    persist(con, people, tickers, all_legs)
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("jev_paper", json.dumps(jev_stats)),
    )
    log(
        f"paper priced={stats['priced']} filers={out['stats']['filers']} "
        f"tickers={len(tickers)} jev={jev_stats}"
    )
    return out


def flag_outlier_legs(con: sqlite3.Connection, legs: list[dict], skip_jev: bool) -> dict:
    stats = {"ran": False, "n": 0, "errors": 0, "model": None, "flagged": []}
    if skip_jev or not legs:
        return stats
    if not jev.key_present():
        log("paper Jev skipped (no TYPESAFE_API_KEY)")
        stats["reason"] = "no_key"
        return stats
    ranked = sorted(legs, key=lambda x: abs(x.get("ret") or 0), reverse=True)[:12]
    log(f"Jev paper outlier legs: {len(ranked)}")
    flags: dict[tuple, dict] = {}

    def one(leg: dict) -> dict:
        packed = jev.ask(
            {
                "ticker": leg["t"],
                "filed": leg.get("filed"),
                "entry": leg.get("in"),
                "entryDate": leg.get("inD") or leg.get("filed"),
                "exit": leg.get("out"),
                "exitDate": leg.get("outD"),
                "ret": leg.get("ret"),
                "filer_id": leg.get("filer_id"),
            },
            paper_questions(),
            label=f"paper_leg_v1:{leg.get('filer_id')}:{leg['t']}:{leg.get('filed')}",
        )
        return {"leg": leg, "packed": packed}

    errors = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(one, leg) for leg in ranked]
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as exc:
                errors += 1
                log(f"  paper Jev error: {type(exc).__name__}: {exc}")
                continue
            packed = r["packed"]
            leg = r["leg"]
            jev.store_decision(con, "paper_leg", f"{leg.get('filer_id')}:{leg['t']}:{leg.get('filed')}", packed)
            ans = packed.get("answers") or {}
            art = (ans.get("artifact") or {}).get("noul")
            qual = (ans.get("quality") or {}).get("score")
            leg["jev_artifact_noul"] = art
            leg["jev_quality"] = qual
            leg["jev_model"] = packed.get("model")
            flags[(leg.get("filer_id"), leg["t"], leg.get("filed"))] = {
                "artifact": art,
                "quality": qual,
                "model": packed.get("model"),
            }
            if art is not None and art >= 0.7:
                stats["flagged"].append({"t": leg["t"], "filed": leg.get("filed"), "ret": leg.get("ret"), "artifact": art})
            stats["model"] = packed.get("model")
            stats["n"] += 1
    stats["ran"] = True
    stats["errors"] = errors
    return stats


def persist(con: sqlite3.Connection, people: list[dict], tickers: list[dict], legs: list[dict]) -> None:
    con.executescript(DDL)
    con.execute("DELETE FROM paper_legs")
    con.execute("DELETE FROM paper_filers")
    con.execute("DELETE FROM paper_tickers")
    con.executemany(
        """
        INSERT INTO paper_filers(filer_id, name, chamber, n, skip, avg, med, win)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(p["id"], p["name"], p["chamber"], p["n"], p["skip"], p["avg"], p["med"], p["win"]) for p in people],
    )
    con.executemany(
        """
        INSERT INTO paper_tickers(ticker, name, n, people, avg, med, win)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(t["t"], t["name"], t["n"], t["people"], t["avg"], t["med"], t["win"]) for t in tickers],
    )
    con.executemany(
        """
        INSERT INTO paper_legs(
          filer_id, ticker, filed, trade, amt, entry_px, entry_date, exit_px, exit_date, ret,
          jev_artifact_noul, jev_quality, jev_model
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                lg.get("filer_id"),
                lg["t"],
                lg.get("filed"),
                lg.get("trade"),
                lg.get("amt"),
                lg.get("in"),
                lg.get("inD") or lg.get("filed"),
                lg.get("out"),
                lg.get("outD"),
                lg.get("ret"),
                lg.get("jev_artifact_noul"),
                lg.get("jev_quality"),
                lg.get("jev_model"),
            )
            for lg in legs
        ],
    )


def write_json(payload: dict) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":")) + "\n"
    PAPER_JSON.write_text(text, encoding="utf-8")
    (EXPORT_DIR / "backtest.json").write_text(text, encoding="utf-8")
    log(f"Wrote {PAPER_JSON} ({PAPER_JSON.stat().st_size / 1024:.0f} KB)")


def run(con: sqlite3.Connection, skip_jev: bool = False) -> dict[str, Any]:
    payload = compute_paper(con, skip_jev=skip_jev)
    write_json(payload)
    return payload


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--skip-jev", action="store_true")
    args = p.parse_args()
    if not DB_PATH.exists():
        log(f"missing {DB_PATH}")
        return 1
    con = sqlite3.connect(str(DB_PATH))
    try:
        run(con, skip_jev=args.skip_jev)
        con.commit()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
