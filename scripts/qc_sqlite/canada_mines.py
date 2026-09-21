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
YTD_YEAR = 2026
YTD_PERIOD = "2026-YTD"

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
  period TEXT,
  through TEXT,
  period_label TEXT,
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
    cols = {r[1] for r in con.execute("PRAGMA table_info(canada_mine_sources)")}
    for col, typ in (("period", "TEXT"), ("through", "TEXT"), ("period_label", "TEXT")):
        if col not in cols:
            con.execute(f"ALTER TABLE canada_mine_sources ADD COLUMN {col} {typ}")


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


def _insert_source(
    con: sqlite3.Connection,
    mid: str,
    *,
    year: int,
    rec: dict[str, Any],
    src: dict[str, Any],
) -> None:
    con.execute(
        """
        INSERT INTO canada_mine_sources(
          mine_id, year, url, title, as_of, kind, blocker, fetch_ok, fetch_error,
          period, through, period_label
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            mid,
            year,
            rec.get("production_source") or src.get("url"),
            rec.get("production_source_title") or src.get("title"),
            rec.get("production_as_of") or src.get("as_of"),
            rec.get("kind") or src.get("kind"),
            rec.get("blocker") or src.get("blocker"),
            1 if rec.get("fetch_ok") else 0,
            rec.get("fetch_blocker") or rec.get("fetch_error"),
            rec.get("period") or src.get("period"),
            rec.get("through") or src.get("through"),
            rec.get("period_label") or src.get("period_label"),
        ),
    )


def _insert_commodities(
    con: sqlite3.Connection,
    mid: str,
    year: int,
    comms: dict[str, Any],
) -> int:
    n = 0
    for cid, row in (comms or {}).items():
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
                year,
                cid,
                float(row["value"]),
                row.get("unit") or "",
                row.get("source_value"),
                row.get("source_unit"),
                row.get("quote"),
            ),
        )
        n += 1
    return n


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
        _insert_source(con, mid, year=int(rec.get("year") or YEAR), rec=rec, src=src)
        n_src += 1
        if not meta.get("omit_figure"):
            n_prod += _insert_commodities(
                con, mid, int(rec.get("year") or YEAR), rec.get("commodities") or {}
            )
        ytd = rec.get("ytd") or {}
        ytd_src = src.get("ytd") if isinstance(src.get("ytd"), dict) else {}
        ytd_comms = ytd.get("commodities") or {}
        if ytd_comms or ytd.get("production_source") or ytd_src.get("url"):
            ytd_year = int(ytd.get("year") or ytd_src.get("year") or YTD_YEAR)
            if ytd_year == YEAR:
                continue
            ytd_rec = {
                **ytd,
                "kind": "ytd",
                "period": ytd.get("period") or YTD_PERIOD,
                "through": ytd.get("through") or ytd_src.get("through"),
                "period_label": ytd.get("period_label") or ytd_src.get("period_label"),
            }
            _insert_source(con, mid, year=ytd_year, rec=ytd_rec, src=ytd_src)
            n_src += 1
            if not meta.get("omit_figure"):
                n_prod += _insert_commodities(con, mid, ytd_year, ytd_comms)
    con.commit()
    return {"n_mines": n_mines, "n_production": n_prod, "n_sources": n_src}


def _comms_from_rows(rows: list[sqlite3.Row]) -> dict[str, Any]:
    comms: dict[str, Any] = {}
    for prod in rows:
        val = prod["value"]
        if isinstance(val, float) and val.is_integer():
            val = int(val)
        comms[prod["commodity"]] = {
            "value": val,
            "unit": prod["unit"],
            "source_value": prod["source_value"],
            "source_unit": prod["source_unit"],
            "quote": prod["quote"],
        }
    return comms


def _ytd_from_db(con: sqlite3.Connection, mid: str, omit: bool) -> dict[str, Any] | None:
    src = con.execute(
        "SELECT * FROM canada_mine_sources WHERE mine_id=? AND year=?",
        (mid, YTD_YEAR),
    ).fetchone()
    if not src:
        return None
    comms: dict[str, Any] = {}
    if not omit:
        comms = _comms_from_rows(
            list(
                con.execute(
                    "SELECT * FROM canada_mine_production WHERE mine_id=? AND year=?",
                    (mid, YTD_YEAR),
                )
            )
        )
    if not comms and not src["url"]:
        return None
    side: dict[str, Any] = {
        "year": YTD_YEAR,
        "period": src["period"] or YTD_PERIOD,
        "kind": src["kind"] or "ytd",
        "through": src["through"],
        "period_label": src["period_label"],
        "production_source": src["url"],
        "production_source_title": src["title"],
        "production_as_of": src["as_of"],
        "commodities": comms,
        "blocker": src["blocker"],
        "fetch_ok": bool(src["fetch_ok"]),
    }
    return {k: v for k, v in side.items() if v is not None or k in {"commodities"}}


def book_from_db(con: sqlite3.Connection, year: int = YEAR) -> dict[str, Any]:
    """Rebuild the ingest-shaped book from sqlite (export input).

    2025 annual rows stay on rec.commodities. A labeled 2026 YTD snapshot,
    when present, is rec.ytd — never mixed into the 2025 figure.
    """
    mines: dict[str, Any] = {}
    for row in con.execute("SELECT * FROM canada_mines ORDER BY mine_id"):
        mid = row["mine_id"]
        src = con.execute(
            "SELECT * FROM canada_mine_sources WHERE mine_id=? AND year=?",
            (mid, year),
        ).fetchone()
        comms: dict[str, Any] = {}
        if not row["omit_figure"]:
            comms = _comms_from_rows(
                list(
                    con.execute(
                        "SELECT * FROM canada_mine_production WHERE mine_id=? AND year=?",
                        (mid, year),
                    )
                )
            )
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
        ytd = _ytd_from_db(con, mid, bool(row["omit_figure"]))
        if ytd:
            mines[mid]["ytd"] = ytd
    n_fig = sum(1 for r in mines.values() if r.get("commodities"))
    n_ytd = sum(1 for r in mines.values() if (r.get("ytd") or {}).get("commodities"))
    return {
        "schema": "qc-canada-mine-production-v1",
        "year": year,
        "n_with_figure": n_fig,
        "n_with_ytd": n_ytd,
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
        ytd_src = con.execute(
            "SELECT * FROM canada_mine_sources WHERE mine_id=? AND year=?",
            (mine["mine_id"], YTD_YEAR),
        ).fetchone()
        if ytd_src:
            rec["ytd"] = {
                "year": YTD_YEAR,
                "period": ytd_src["period"] or YTD_PERIOD,
                "kind": ytd_src["kind"] or "ytd",
                "through": ytd_src["through"],
                "period_label": ytd_src["period_label"],
                "url": ytd_src["url"],
                "title": ytd_src["title"],
                "as_of": ytd_src["as_of"],
                "blocker": ytd_src["blocker"],
            }
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
    n_fig = con.execute(
        "SELECT COUNT(DISTINCT mine_id) FROM canada_mine_production WHERE year=?",
        (YEAR,),
    ).fetchone()[0]
    if n_fig < 5:
        errors.append(f"canada_mine_production need several cited mines, have {n_fig}")
    for row in con.execute(
        "SELECT mine_id, year, commodity, value, unit FROM canada_mine_production"
    ):
        if row["commodity"] in {"aueq", "gold-equivalent", "geo"}:
            errors.append(f"{row['mine_id']}: AuEq stored")
        if row["commodity"] == "gold" and row["unit"] != "troy oz":
            errors.append(f"{row['mine_id']}: gold must be troy oz in sqlite")
        if row["value"] is None or float(row["value"]) <= 0:
            errors.append(f"{row['mine_id']}: non-positive {row['commodity']}")
        src = con.execute(
            "SELECT url, kind, through, period_label, period FROM canada_mine_sources WHERE mine_id=? AND year=?",
            (row["mine_id"], row["year"]),
        ).fetchone()
        if not src or not (src["url"] or "").startswith("http"):
            errors.append(f"{row['mine_id']} {row['year']}: figure without URL")
        if row["year"] == YTD_YEAR:
            if (src["kind"] if src else None) != "ytd":
                errors.append(f"{row['mine_id']}: 2026 production must be kind=ytd")
            if not (src and src["through"]):
                errors.append(f"{row['mine_id']}: 2026 YTD missing through")
            if not (src and src["period_label"]):
                errors.append(f"{row['mine_id']}: 2026 YTD missing period_label")
            if src and (src["period"] in {"2025", "annual"} or src["kind"] == "annual"):
                errors.append(f"{row['mine_id']}: partial 2026 stored as a full year")
        if row["year"] == YEAR and src and src["kind"] == "ytd":
            errors.append(f"{row['mine_id']}: YTD mixed into 2025")
    return errors
