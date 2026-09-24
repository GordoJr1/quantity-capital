"""Paper board Jev flags on top of the canonical backtest.json.

Root build-backtest.py is the only builder of backtest.json (paper.html).
This module reads that file, asks Jev to flag outlier legs (possible
truncated history / wrong ticker), and stores legs + flags in sqlite
(paper_legs / paper_filers / paper_tickers / jev_decisions). It never
writes backtest.json or any other site JSON.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jev
from paths import DB_PATH, QC_ROOT

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


def load_board(path: Path = PAPER_JSON) -> dict[str, Any] | None:
    if not path.is_file():
        log(f"paper: missing {path.name}; run build-backtest.py first")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        log(f"paper: {path.name} is not valid JSON ({exc.msg}); skipped")
        return None
    return data if isinstance(data, dict) else None


def board_legs(board: dict[str, Any]) -> list[dict]:
    legs = []
    for filer in board.get("filers") or []:
        fid = filer.get("id")
        if not fid:
            continue
        for leg in filer.get("legs") or []:
            row = dict(leg)
            row["filer_id"] = fid
            row.setdefault("inD", row.get("filed"))
            legs.append(row)
    return legs


def compute_paper(con: sqlite3.Connection, skip_jev: bool = False, path: Path = PAPER_JSON) -> dict[str, Any]:
    board = load_board(path)
    if board is None:
        return {"ran": False, "reason": "no_backtest_json"}
    people = [{k: v for k, v in f.items() if k != "legs"} for f in board.get("filers") or [] if f.get("id")]
    tickers = [t for t in board.get("tickers") or [] if t.get("t")]
    legs = board_legs(board)
    jev_stats = flag_outlier_legs(con, legs, skip_jev)
    persist(con, people, tickers, legs)
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("jev_paper", json.dumps({**jev_stats, "source": path.name, "generated": board.get("generated")})),
    )
    log(f"paper legs={len(legs)} filers={len(people)} tickers={len(tickers)} jev={jev_stats}")
    return jev_stats


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
                log(f"  paper Jev error: {type(exc).__name__}")
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
            if art is not None and art >= 0.7:
                stats["flagged"].append({"t": leg["t"], "filed": leg.get("filed"), "ret": leg.get("ret"), "artifact": art})
            stats["model"] = packed.get("model")
            stats["n"] += 1
    stats["ran"] = stats["n"] > 0
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
        [(p["id"], p.get("name"), p.get("chamber"), p.get("n"), p.get("skip"), p.get("avg"), p.get("med"), p.get("win")) for p in people],
    )
    con.executemany(
        """
        INSERT INTO paper_tickers(ticker, name, n, people, avg, med, win)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [(t["t"], t.get("name"), t.get("n"), t.get("people"), t.get("avg"), t.get("med"), t.get("win")) for t in tickers],
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


def run(con: sqlite3.Connection, skip_jev: bool = False) -> dict[str, Any]:
    return compute_paper(con, skip_jev=skip_jev)


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
