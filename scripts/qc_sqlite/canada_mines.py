"""Canada Map 900A mine production — SQLite store + thin Pages export.

Pattern (same as boards cutover):
  1. Ingest cited filings into qc.sqlite (never committed).
  2. Export only what Pages needs: canada/producer-join.json and
     existing beta/<issuer>.json production/by_asset.
  3. Do not mint new issuer JSON. Do not invent ounces.

    python3 scripts/ingest_canada_mine_production.py --sqlite qc.sqlite --apply
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import canada_beta_production as beta

YEAR = 2025

DDL = """
CREATE TABLE IF NOT EXISTS canada_mines (
  mine_id TEXT PRIMARY KEY,
  name TEXT,
  company_id TEXT,
  asset_id TEXT,
  region TEXT,
  country TEXT NOT NULL DEFAULT 'Canada',
  ownership_pct REAL,
  commodity TEXT,
  omit_figure INTEGER NOT NULL DEFAULT 0,
  map_900a INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS canada_mine_sources (
  mine_id TEXT NOT NULL,
  year INTEGER NOT NULL,
  url TEXT,
  title TEXT,
  as_of TEXT,
  kind TEXT,
  blocker TEXT,
  fetch_ok INTEGER,
  fetch_error TEXT,
  PRIMARY KEY (mine_id, year)
);

CREATE TABLE IF NOT EXISTS canada_mine_production (
  mine_id TEXT NOT NULL,
  year INTEGER NOT NULL,
  commodity TEXT NOT NULL,
  value REAL NOT NULL,
  unit TEXT NOT NULL,
  source_value REAL,
  source_unit TEXT,
  quote TEXT,
  PRIMARY KEY (mine_id, year, commodity)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = OFF")
    return con


def apply_schema(con: sqlite3.Connection) -> None:
    con.executescript(DDL)


def _meta(mid: str, src: dict[str, Any]) -> dict[str, Any]:
    meta = dict(beta.ISSUER_MAP.get(mid) or {})
    if src.get("beta_id"):
        meta["company_id"] = src["beta_id"]
        meta["beta_id"] = src["beta_id"]
    elif meta.get("beta_id"):
        meta["company_id"] = meta["beta_id"]
    if src.get("asset_id"):
        meta["asset_id"] = src["asset_id"]
    if src.get("ownership_pct") is not None:
        meta["ownership_pct"] = src["ownership_pct"]
    if src.get("omit_figure"):
        meta["omit_figure"] = True
    if src.get("region"):
        meta["region"] = src["region"]
    return meta


def store_book(
    con: sqlite3.Connection,
    book: dict[str, Any],
    sources_book: dict[str, Any],
) -> dict[str, int]:
    """Replace Canada mine production rows from a verified ingest book."""
    apply_schema(con)
    by_src = {row["mine_id"]: row for row in (sources_book.get("mines") or [])}
    con.execute("DELETE FROM canada_mine_production")
    con.execute("DELETE FROM canada_mine_sources")
    con.execute("DELETE FROM canada_mines")
    n_mines = n_prod = n_src = 0
    for mid, rec in (book.get("mines") or {}).items():
        src = by_src.get(mid) or {}
        meta = _meta(mid, src)
        con.execute(
            """
            INSERT INTO canada_mines(
              mine_id, name, company_id, asset_id, region, country,
              ownership_pct, commodity, omit_figure, map_900a
            ) VALUES (?,?,?,?,?,?,?,?,?,1)
            """,
            (
                mid,
                rec.get("mine_name") or src.get("mine_name") or mid,
                meta.get("company_id") or meta.get("beta_id"),
                meta.get("asset_id"),
                meta.get("region") or src.get("region"),
                "Canada",
                meta.get("ownership_pct"),
                meta.get("commodity") or "gold",
                1 if meta.get("omit_figure") else 0,
            ),
        )
        n_mines += 1
        con.execute(
            """
            INSERT INTO canada_mine_sources(
              mine_id, year, url, title, as_of, kind, blocker, fetch_ok, fetch_error
            ) VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                mid,
                int(rec.get("year") or YEAR),
                rec.get("production_source") or src.get("url"),
                rec.get("production_source_title") or src.get("title"),
                rec.get("production_as_of") or src.get("as_of"),
                rec.get("kind") or src.get("kind"),
                rec.get("blocker") or src.get("blocker"),
                1 if rec.get("fetch_ok") else 0,
                rec.get("fetch_blocker") or rec.get("fetch_error"),
            ),
        )
        n_src += 1
        if meta.get("omit_figure"):
            continue
        for cid, row in (rec.get("commodities") or {}).items():
            if row.get("value") is None:
                continue
            con.execute(
                """
                INSERT INTO canada_mine_production(
                  mine_id, year, commodity, value, unit,
                  source_value, source_unit, quote
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    mid,
                    int(rec.get("year") or YEAR),
                    cid,
                    float(row["value"]),
                    row.get("unit") or "",
                    row.get("source_value"),
                    row.get("source_unit"),
                    row.get("quote"),
                ),
            )
            n_prod += 1
    con.commit()
    return {"n_mines": n_mines, "n_production": n_prod, "n_sources": n_src}


def book_from_db(con: sqlite3.Connection, year: int = YEAR) -> dict[str, Any]:
    """Rebuild the ingest-shaped book from sqlite (export input)."""
    mines: dict[str, Any] = {}
    for row in con.execute("SELECT * FROM canada_mines ORDER BY mine_id"):
        mid = row["mine_id"]
        src = con.execute(
            "SELECT * FROM canada_mine_sources WHERE mine_id=? AND year=?",
            (mid, year),
        ).fetchone()
        comms: dict[str, Any] = {}
        if not row["omit_figure"]:
            for prod in con.execute(
                "SELECT * FROM canada_mine_production WHERE mine_id=? AND year=?",
                (mid, year),
            ):
                comms[prod["commodity"]] = {
                    "value": prod["value"] if float(prod["value"]).is_integer() else prod["value"],
                    "unit": prod["unit"],
                    "source_value": prod["source_value"],
                    "source_unit": prod["source_unit"],
                    "quote": prod["quote"],
                }
                val = comms[prod["commodity"]]["value"]
                if isinstance(val, float) and val.is_integer():
                    comms[prod["commodity"]]["value"] = int(val)
        mines[mid] = {
            "mine_id": mid,
            "mine_name": row["name"],
            "year": year,
            "kind": (src["kind"] if src else None),
            "production_source": (src["url"] if src else None),
            "production_source_title": (src["title"] if src else None),
            "production_as_of": (src["as_of"] if src else None),
            "commodities": comms,
            "blocker": (src["blocker"] if src else None),
            "fetch_ok": bool(src["fetch_ok"]) if src else False,
            "company_id": row["company_id"],
            "asset_id": row["asset_id"],
            "omit_figure": bool(row["omit_figure"]),
            "ownership_pct": row["ownership_pct"],
            "region": row["region"],
        }
    n_fig = sum(1 for r in mines.values() if r.get("commodities"))
    return {
        "schema": "qc-canada-mine-production-v1",
        "year": year,
        "n_with_figure": n_fig,
        "n_blank": len(mines) - n_fig,
        "n_sources": len(mines),
        "mines": mines,
        "built_from": "qc.sqlite",
    }


def sources_from_db(con: sqlite3.Connection, year: int = YEAR) -> dict[str, Any]:
    rows = []
    for mine in con.execute("SELECT * FROM canada_mines ORDER BY mine_id"):
        src = con.execute(
            "SELECT * FROM canada_mine_sources WHERE mine_id=? AND year=?",
            (mine["mine_id"], year),
        ).fetchone()
        rec = {
            "mine_id": mine["mine_id"],
            "mine_name": mine["name"],
            "beta_id": mine["company_id"],
            "asset_id": mine["asset_id"],
            "ownership_pct": mine["ownership_pct"],
            "omit_figure": bool(mine["omit_figure"]),
            "region": mine["region"],
        }
        if src:
            rec.update({
                "url": src["url"],
                "title": src["title"],
                "as_of": src["as_of"],
                "kind": src["kind"],
                "blocker": src["blocker"],
            })
        rows.append(rec)
    return {"year": year, "mines": rows}


def export_pages(
    con: sqlite3.Connection,
    *,
    root: Path,
    join_path: Path | None = None,
) -> dict[str, Any]:
    """Write the thin Pages JSON from sqlite. Existing beta files only."""
    book = book_from_db(con)
    sources_book = sources_from_db(con)
    stats = beta.write_beta_profiles(book, sources_book, root=root, create=False)
    join = beta.build_join(book, sources_book, include_figures=True)
    join["built_from"] = "qc.sqlite"
    dest = join_path or (root / "canada" / "producer-join.json")
    beta.write_json(dest, join)
    return {"stats": stats, "join": join, "book": book}


def validate_db(con: sqlite3.Connection) -> list[str]:
    errors: list[str] = []
    n = con.execute("SELECT COUNT(*) FROM canada_mines").fetchone()[0]
    if n < 10:
        errors.append(f"canada_mines too small: {n}")
    n_fig = con.execute("SELECT COUNT(DISTINCT mine_id) FROM canada_mine_production").fetchone()[0]
    if n_fig < 5:
        errors.append(f"canada_mine_production need several cited mines, have {n_fig}")
    for row in con.execute(
        "SELECT mine_id, commodity, value, unit FROM canada_mine_production"
    ):
        if row["commodity"] in {"aueq", "gold-equivalent", "geo"}:
            errors.append(f"{row['mine_id']}: AuEq stored")
        if row["commodity"] == "gold" and row["unit"] != "troy oz":
            errors.append(f"{row['mine_id']}: gold must be troy oz in sqlite")
        if row["value"] is None or float(row["value"]) <= 0:
            errors.append(f"{row['mine_id']}: non-positive {row['commodity']}")
        src = con.execute(
            "SELECT url FROM canada_mine_sources WHERE mine_id=?",
            (row["mine_id"],),
        ).fetchone()
        if not src or not (src["url"] or "").startswith("http"):
            errors.append(f"{row['mine_id']}: figure without URL")
    return errors
