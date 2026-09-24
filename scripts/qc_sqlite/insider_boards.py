"""Insider boards: analysis / repeatable / follow + form4 sidecar ingest.

Runs the root build-insider-repeatable.py and build-insider-follow.py (the
same builders CI runs), ingests the collector's insider-analysis.json as-is,
stores everything in sqlite, and optionally Jev-gates the follow list. Jev
ship/drop lives in sqlite and the gitignored export/ copy only; the committed
insider-follow.json is left exactly as the root builder wrote it.
"""
from __future__ import annotations

import importlib.util
import inspect
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
from paths import DB_PATH, EXPORT_DIR, QC_ROOT

sys.path.insert(0, str(HERE.parent))
from qc_io import atomic_write_json, atomic_write_text  # noqa: E402

DDL = """
CREATE TABLE IF NOT EXISTS insider_form4 (
  trade_id TEXT PRIMARY KEY,
  plan INTEGER,
  payload_json TEXT
);
CREATE TABLE IF NOT EXISTS insider_repeatable_filers (
  filer_id TEXT PRIMARY KEY,
  payload_json TEXT
);
CREATE TABLE IF NOT EXISTS insider_follow (
  filer_id TEXT PRIMARY KEY,
  shipped INTEGER,
  jev_ship TEXT,
  jev_confidence REAL,
  payload_json TEXT
);
CREATE TABLE IF NOT EXISTS insider_analysis_book (
  ticker TEXT PRIMARY KEY,
  list TEXT,
  payload_json TEXT
);
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def load_root_builder(filename: str):
    path = QC_ROOT / filename
    name = "_qc_root_" + path.stem.replace("-", "_")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_root_builder(filename: str) -> int:
    mod = load_root_builder(filename)
    params = inspect.signature(mod.main).parameters
    rc = mod.main([]) if params else mod.main()
    return int(rc or 0)


def ingest_form4(con: sqlite3.Connection) -> int:
    p = QC_ROOT / "insider-form4.json"
    if not p.exists():
        log("no insider-form4.json")
        return 0
    data = json.loads(p.read_text(encoding="utf-8-sig"))
    trades = data.get("trades") if isinstance(data, dict) else {}
    if not isinstance(trades, dict):
        trades = {}
    con.execute("DELETE FROM insider_form4")
    rows = []
    for tid, ov in trades.items():
        if not isinstance(ov, dict):
            continue
        rows.append((tid, 1 if ov.get("plan") else 0, json.dumps(ov)))
    con.executemany("INSERT OR REPLACE INTO insider_form4(trade_id, plan, payload_json) VALUES (?,?,?)", rows)
    log(f"insider-form4 rows {len(rows)}")
    return len(rows)


def ingest_json_list(con: sqlite3.Connection, path: Path, table: str, id_key: str) -> list[dict]:
    if not path.exists():
        log(f"missing {path.name}")
        return []
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    items = data.get("filers") or data.get("follow") or data.get("book") or []
    con.execute(f"DELETE FROM {table}")
    rows = []
    out = []
    if table == "insider_analysis_book":
        for lst, key in (("book", "book"), ("heat", "heat")):
            for rec in data.get(key) or []:
                code = rec.get("code")
                if not code:
                    continue
                rows.append((code, lst, json.dumps(rec)))
                out.append(rec)
        con.executemany(
            "INSERT OR REPLACE INTO insider_analysis_book(ticker, list, payload_json) VALUES (?,?,?)",
            rows,
        )
        atomic_write_text(EXPORT_DIR / path.name, path.read_text(encoding="utf-8"))
        log(f"{path.name} stored {len(rows)}")
        return out
    for rec in items:
        fid = rec.get(id_key) or rec.get("id")
        if not fid:
            continue
        rows.append((fid, json.dumps(rec)))
        out.append(rec)
    if table == "insider_repeatable_filers":
        con.executemany(
            "INSERT OR REPLACE INTO insider_repeatable_filers(filer_id, payload_json) VALUES (?,?)",
            rows,
        )
    log(f"{path.name} stored {len(rows)}")
    atomic_write_text(EXPORT_DIR / path.name, path.read_text(encoding="utf-8"))
    return out


def follow_questions():
    from typesafe_sdk import Choice, Noul

    return {
        "ship": Choice(
            instructions="Should this officer stay on the public insider follow list?",
            criteria={
                "ship": "Keep them. Repeatable open-market buys look copyable.",
                "drop": "Drop them. Too scheduled, too thin, or the edge looks like noise.",
            },
        ),
        "discretionary": Noul(
            instructions="Are this officer's remaining market prints mostly discretionary (not 10b5-1 / plan)?",
        ),
    }


def gate_follow(con: sqlite3.Connection, skip_jev: bool) -> dict:
    path = QC_ROOT / "insider-follow.json"
    stats = {"ran": False, "n": 0, "errors": 0, "dropped": [], "model": None}
    if not path.exists():
        return stats
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    follow = data.get("follow") or []
    con.execute("DELETE FROM insider_follow")
    if skip_jev or not jev.key_present() or not follow:
        for rec in follow:
            fid = rec.get("id")
            if fid:
                con.execute(
                    "INSERT INTO insider_follow(filer_id, shipped, payload_json) VALUES (?,?,?)",
                    (fid, 1, json.dumps(rec)),
                )
        if not skip_jev and not jev.key_present():
            log("insider follow Jev skipped (no key)")
            stats["reason"] = "no_key"
        return stats

    pool = follow[:15]
    log(f"Jev insider follow: {len(pool)}")
    by = {r.get("id"): r for r in pool if r.get("id")}

    def one(rec: dict) -> dict:
        packed = jev.ask(
            {
                "id": rec.get("id"),
                "name": rec.get("name"),
                "title": rec.get("title"),
                "hit": rec.get("hit") or rec.get("hitRate") or rec.get("win"),
                "avg": rec.get("avg") or rec.get("avg90") or rec.get("ret"),
                "n": rec.get("n") or rec.get("buys") or rec.get("nBuys"),
                "planShare": rec.get("planShare") or rec.get("plan"),
                "ticker": rec.get("ticker") or rec.get("code"),
            },
            follow_questions(),
            label=f"insider_follow_v1:{rec.get('id')}",
        )
        return {"id": rec.get("id"), "packed": packed}

    errors = 0
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(one, r) for r in pool]
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as exc:
                errors += 1
                log(f"  follow Jev error: {type(exc).__name__}")
                continue
            packed = r["packed"]
            rec = by.get(r["id"]) or {}
            jev.store_decision(con, "insider_follow", r["id"], packed)
            ship = ((packed.get("answers") or {}).get("ship") or {}).get("choice")
            conf = ((packed.get("answers") or {}).get("ship") or {}).get("confidence")
            rec["_jev_ship"] = ship
            rec["_jev_conf"] = conf
            stats["model"] = packed.get("model")
            stats["n"] += 1
            if ship == "drop":
                stats["dropped"].append(r["id"])

    kept = []
    for rec in follow:
        fid = rec.get("id")
        if not fid:
            continue
        ship = rec.get("_jev_ship")
        shipped = 0 if ship == "drop" else 1
        if shipped:
            kept.append({k: v for k, v in rec.items() if not str(k).startswith("_")})
        con.execute(
            """
            INSERT OR REPLACE INTO insider_follow(filer_id, shipped, jev_ship, jev_confidence, payload_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (fid, shipped, rec.get("_jev_ship"), rec.get("_jev_conf"), json.dumps({k: v for k, v in rec.items() if not str(k).startswith("_")})),
        )
    data["follow"] = kept
    atomic_write_json(EXPORT_DIR / "insider-follow.json", data)
    stats["ran"] = stats["n"] > 0
    stats["errors"] = errors
    log(f"insider follow shipped {len(kept)} dropped {stats['dropped']}")
    return stats


def run(con: sqlite3.Connection, skip_jev: bool = False) -> dict[str, Any]:
    con.executescript(DDL)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    n_form4 = ingest_form4(con)
    builders = {}
    if (QC_ROOT / "insider-trades-lite.json").exists():
        for filename in ("build-insider-repeatable.py", "build-insider-follow.py"):
            log(f"{filename}…")
            try:
                rc = run_root_builder(filename)
            except SystemExit as e:
                rc = e.code if isinstance(e.code, int) else 1
            except Exception as exc:
                log(f"{filename} failed: {type(exc).__name__}: {exc}")
                rc = 1
            builders[filename] = rc
            if rc:
                log(f"{filename} exit {rc}; later boards use the previous JSON")
                break
    ingest_json_list(con, QC_ROOT / "insider-analysis.json", "insider_analysis_book", "code")
    ingest_json_list(con, QC_ROOT / "insider-repeatable.json", "insider_repeatable_filers", "id")
    jev_stats = gate_follow(con, skip_jev)
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("jev_insider_follow", json.dumps(jev_stats)),
    )
    return {"form4": n_form4, "builders": builders, "follow_jev": jev_stats}


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
