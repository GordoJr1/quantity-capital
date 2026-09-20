"""Insider boards: analysis / repeatable / follow + form4 sidecar ingest.

Runs the upstream builders against the site root, then stores JSON in sqlite
and optionally Jev-gates the follow list.
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
from paths import DB_PATH, EXPORT_DIR, GROKS, QC_ROOT

UP = HERE / "_upstream"
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


def exec_upstream(filename: str, root_assign: str) -> None:
    path = UP / filename
    text = path.read_text(encoding="utf-8")
    text = text.replace(
        "ROOT = Path(__file__).resolve().parent",
        f"ROOT = Path(r'{QC_ROOT}')",
    )
    text = text.replace(
        "SITE = Path(__file__).resolve().parent.parent",
        f"SITE = Path(r'{QC_ROOT}')",
    )
    # form4_enrich uses collect parent; do not run it here (network scraper).
    collect = GROKS / "collect"
    if str(collect) not in sys.path:
        sys.path.insert(0, str(collect))
    ns: dict[str, Any] = {"__name__": "__qc_sqlite_upstream__", "__file__": str(path)}
    exec(compile(text, str(path), "exec"), ns)
    if "main" in ns:
        rc = ns["main"]()
        if rc not in (0, None):
            raise SystemExit(rc)


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
        (EXPORT_DIR / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
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
    (EXPORT_DIR / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
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
                log(f"  follow Jev error: {type(exc).__name__}: {exc}")
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
    text = json.dumps(data, indent=2) + "\n"
    path.write_text(text, encoding="utf-8")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    (EXPORT_DIR / "insider-follow.json").write_text(text, encoding="utf-8")
    stats["ran"] = True
    stats["errors"] = errors
    log(f"insider follow shipped {len(kept)} dropped {stats['dropped']}")
    return stats


def run(con: sqlite3.Connection, skip_jev: bool = False) -> dict[str, Any]:
    con.executescript(DDL)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    n_form4 = ingest_form4(con)
    # Repeatable + analysis write JSON into QC_ROOT; follow depends on repeatable.
    if (UP / "build_insider_analysis.py").exists():
        log("insider analysis builder…")
        try:
            exec_upstream("build_insider_analysis.py", "SITE")
        except SystemExit as e:
            if e.code not in (0, None):
                log(f"insider analysis builder exit {e.code}")
        except Exception as exc:
            log(f"insider analysis builder failed: {type(exc).__name__}: {exc}")
    if (UP / "build-insider-repeatable.py").exists() and (QC_ROOT / "insider-trades-lite.json").exists():
        log("insider repeatable builder…")
        try:
            exec_upstream("build-insider-repeatable.py", "ROOT")
        except SystemExit as e:
            if e.code not in (0, None):
                log(f"repeatable exit {e.code}")
        except Exception as exc:
            log(f"repeatable failed: {type(exc).__name__}: {exc}")
    if (UP / "build-insider-follow.py").exists() and (QC_ROOT / "insider-repeatable.json").exists():
        log("insider follow builder…")
        try:
            exec_upstream("build-insider-follow.py", "ROOT")
        except SystemExit as e:
            if e.code not in (0, None):
                log(f"follow exit {e.code}")
        except Exception as exc:
            log(f"follow failed: {type(exc).__name__}: {exc}")

    ingest_json_list(con, QC_ROOT / "insider-analysis.json", "insider_analysis_book", "code")
    ingest_json_list(con, QC_ROOT / "insider-repeatable.json", "insider_repeatable_filers", "id")
    jev_stats = gate_follow(con, skip_jev)
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("jev_insider_follow", json.dumps(jev_stats)),
    )
    return {"form4": n_form4, "follow_jev": jev_stats}


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
