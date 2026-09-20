"""Rebuild qc.sqlite from pinned JSON. Does not rewrite collectors.

Usage (from the quantity-capital repo root):
  python scripts/qc_sqlite/rebuild.py
  python scripts/qc_sqlite/rebuild.py --skip-jev
  python scripts/qc_sqlite/rebuild.py --skip-tells
  python scripts/qc_sqlite/rebuild.py --skip-analysis
  python scripts/qc_sqlite/tells.py
  python scripts/qc_sqlite/analysis.py
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
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
from paths import (
    BIOS,
    CLAIMS_COMPANIES,
    CLAIMS_DIR,
    DB_MIRROR,
    DB_PATH,
    EXPORT_DIR,
    INSIDER_COMPANIES,
    INSIDER_TRADES,
    MARKET_CAPS,
    MINES_EXPLORERS,
    MINES_MCAP,
    MINES_REGISTRY,
    QC_ROOT,
    SCHEMA_SQL,
    TICKERS,
    TRADES,
)

LEGAL_RE = re.compile(
    r"\b(inc\.?|incorporated|ltd\.?|limited|llc|l\.l\.c\.?|corp\.?|corporation|"
    r"plc|s\.a\.?|n\.v\.?|co\.?|company)\b",
    re.I,
)
ACCENT = str.maketrans(
    {
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "à": "a",
        "â": "a",
        "ä": "a",
        "ô": "o",
        "ö": "o",
        "î": "i",
        "ï": "i",
        "ù": "u",
        "û": "u",
        "ç": "c",
        "É": "e",
        "È": "e",
        "À": "a",
    }
)
CORP_HINT_RE = re.compile(
    r"\b(inc|corp|ltd|limited|resources|mines|mining|gold|exploration|corporation|"
    r"plc|llc|minerals|metals|royalt|ventures|energy)\b",
    re.I,
)
PERSON_LIKE_RE = re.compile(r"^[A-Za-zÀ-ÿ' .\-]{3,60}$")
AMOUNT_RE = re.compile(r"[\d,]+")
PCT_HOLDER_RE = re.compile(r"\((\d+(?:\.\d+)?)\)\s*([^,]+?)(?=\s*,\s*\(|$)")

# Needles used by build_on_bc_extracts.py to pull each producer's layer.
EXTRACT_NEEDLES = {
    "iamgold": ["iamgold"],
    "agnico-eagle": ["agnico"],
    "alamos-gold": ["alamos"],
    "wesdome-gold-mines": ["wesdome"],
    "evolution": ["evolution"],
    "equinox-gold": ["equinox", "greenstone gold", "musselwhite"],
    "newmont": ["newmont", "pretium"],
    "centerra-gold": ["centerra", "thompson creek"],
    "artemis-gold": ["artemis", "bw gold"],
    "gold-fields": ["gold fields", "windfall"],
    "barrick": ["barrick"],
    "eldorado-gold": ["eldorado"],
}


def log(msg: str) -> None:
    print(msg, flush=True)


def load_json(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open("r", encoding="utf-8-sig") as f:
        return json.load(f)


def slugify(name: str) -> str:
    s = (name or "").translate(ACCENT).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unknown"


def norm_name(value: str) -> str:
    s = (value or "").translate(ACCENT).lower()
    s = LEGAL_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def looks_like_person(holder: str) -> bool:
    if not holder or CORP_HINT_RE.search(holder):
        return False
    if re.search(r"\d{3,}", holder):
        return False
    if not PERSON_LIKE_RE.match(holder.strip()):
        return False
    parts = re.findall(r"[A-Za-zÀ-ÿ']+", holder)
    return 2 <= len(parts) <= 4


def parse_holder_parties(raw: str | None) -> list[tuple[str, float | None]]:
    if not raw or not str(raw).strip():
        return []
    text = str(raw).strip()
    parts = PCT_HOLDER_RE.findall(text)
    if parts:
        return [(name.strip(), float(pct)) for pct, name in parts if name.strip()]
    return [(text, None)]


def clean_date(value: Any) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}", text):
        year = int(text[:4])
        return text[:10] if 1900 <= year <= 2100 else None
    try:
        n = int(float(text))
        if abs(n) > 10**12:
            n //= 1000
        if abs(n) < 10**8:
            return None
        dt = datetime.fromtimestamp(n, tz=timezone.utc)
        if 1900 <= dt.year <= 2100:
            return dt.strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return None
    return None


def name_index_for(book: CompanyBook) -> dict[str, list[str]]:
    index: dict[str, list[str]] = defaultdict(list)
    for cid, row in book.rows.items():
        for n in [row.get("name"), row.get("holder"), *(row.get("names") or [])]:
            nn = norm_name(n or "")
            if nn:
                index[nn].append(cid)
    return {k: list(dict.fromkeys(v)) for k, v in index.items()}


def fuzzy_company_ids(nn: str, name_index: dict[str, list[str]]) -> list[str]:
    tokens = set(nn.split())
    if not tokens:
        return []
    hits: list[str] = []
    for other_n, cids in name_index.items():
        ot = set(other_n.split())
        if not ot:
            continue
        inter = tokens & ot
        if len(inter) >= 2 and (len(inter) / max(len(tokens), len(ot))) >= 0.6:
            hits.extend(cids)
    return list(dict.fromkeys(hits))


def extract_alias_hit(holder_name: str, extract_cid: str) -> bool:
    nn = norm_name(holder_name)
    for needle in EXTRACT_NEEDLES.get(extract_cid, []):
        if needle in nn:
            return True
    return False


def parse_amount_band(raw: str | None) -> tuple[float | None, float | None, float | None]:
    if not raw:
        return None, None, None
    nums = [int(x.replace(",", "")) for x in AMOUNT_RE.findall(raw)]
    if not nums:
        return None, None, None
    if len(nums) == 1:
        n = float(nums[0])
        return n, n, n
    lo, hi = float(nums[0]), float(nums[1])
    return lo, hi, (lo + hi) / 2.0


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    con.execute("PRAGMA synchronous = NORMAL")
    return con


def apply_schema(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))


def meta_set(con: sqlite3.Connection, key: str, value: Any) -> None:
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value) if not isinstance(value, str) else value),
    )


def record_jev(
    con: sqlite3.Connection,
    subject_type: str,
    subject_id: str,
    packed: dict[str, Any],
) -> None:
    created = now_iso()
    model = packed.get("model")
    inn = packed.get("input_tokens")
    out = packed.get("output_tokens")
    for qid, ans in (packed.get("answers") or {}).items():
        con.execute(
            """
            INSERT INTO jev_decisions(
              created_at, subject_type, subject_id, question_id, primitive,
              model, answer_json, input_tokens, output_tokens
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                created,
                subject_type,
                subject_id,
                qid,
                ans.get("type"),
                model,
                json.dumps(ans),
                inn,
                out,
            ),
        )


class CompanyBook:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}
        self.tickers: dict[str, list[tuple[str, str, int]]] = defaultdict(list)
        # (company_id, ticker, source, is_primary)

    def add(self, company_id: str, *, source: str, **fields: Any) -> str:
        cid = company_id.strip()
        row = self.rows.setdefault(
            cid,
            {
                "company_id": cid,
                "names": [],
                "sources": [],
            },
        )
        if source and source not in row["sources"]:
            row["sources"].append(source)
        names = fields.pop("names", None)
        if names:
            for n in names:
                if n and n not in row["names"]:
                    row["names"].append(n)
        tickers = fields.pop("tickers", None)
        if tickers:
            for i, t in enumerate(tickers):
                t = (t or "").strip().upper()
                if not t:
                    continue
                self.tickers[cid].append((t, source, 1 if i == 0 else 0))
        for key, value in fields.items():
            if value is None or value == "":
                continue
            if key in {"claim_count", "neighbor_count", "quebec_count", "ontario_count", "bc_count", "holder", "extract_path", "bbox_json", "color", "ontario_extract", "bc_extract"}:
                row[key] = value
            elif row.get(key) in (None, ""):
                row[key] = value
        if row.get("name") and row["name"] not in row["names"]:
            row["names"].insert(0, row["name"])
        return cid


def ingest_company_sources(book: CompanyBook) -> dict[str, Any]:
    mines = load_json(MINES_REGISTRY)
    for iss in mines.get("issuers") or []:
        book.add(
            iss["id"],
            source="mines_registry",
            name=iss.get("name"),
            universe=mines.get("universe"),
            rank=iss.get("rank"),
            enabled=1 if iss.get("enabled") else 0,
            fiscal_year_end=iss.get("fiscal_year_end"),
            ir_news=iss.get("ir_news"),
            profile_file=iss.get("file"),
            tickers=iss.get("tickers") or [],
            names=[iss.get("name")] if iss.get("name") else [],
        )

    explorers = load_json(MINES_EXPLORERS)
    for iss in explorers.get("issuers") or []:
        book.add(
            iss["id"],
            source="mines_explorers",
            name=iss.get("name"),
            universe=explorers.get("universe"),
            enabled=1 if iss.get("enabled") else 0,
            ir_news=iss.get("ir_news"),
            tickers=iss.get("tickers") or [],
            names=[iss.get("name")] if iss.get("name") else [],
        )

    mcap = load_json(MINES_MCAP)
    for iss in mcap.get("issuers") or []:
        book.add(
            iss["id"],
            source="mines_mcap",
            name=iss.get("name"),
            universe=mcap.get("universe") if iss["id"] not in book.rows else None,
            rank=iss.get("rank") if book.rows.get(iss["id"], {}).get("rank") is None else None,
            enabled=1 if iss.get("enabled") else 0,
            filing_backed=1 if iss.get("filing_backed") else 0,
            profile_file=iss.get("file"),
            tickers=iss.get("tickers") or [],
            names=[iss.get("name")] if iss.get("name") else [],
        )

    insider = load_json(INSIDER_COMPANIES)
    by_norm: dict[str, str] = {}
    for cid, row in book.rows.items():
        for n in row.get("names") or []:
            nn = norm_name(n)
            if nn:
                by_norm.setdefault(nn, cid)
        nn = norm_name(row.get("name") or cid)
        if nn:
            by_norm.setdefault(nn, cid)

    for rec in insider.get("companies") or []:
        name = rec.get("name") or ""
        nn = norm_name(name)
        cid = by_norm.get(nn) or slugify(name)
        tickers = rec.get("all") or []
        if not tickers:
            tickers = (rec.get("us") or []) + (rec.get("cad") or []) + (rec.get("other") or [])
        book.add(
            cid,
            source="insider_companies",
            name=name,
            commodity=rec.get("commodity"),
            company_type=rec.get("type"),
            country=rec.get("country"),
            tickers=tickers,
            names=[name] if name else [],
        )
        if nn:
            by_norm.setdefault(nn, cid)

    claims = load_json(CLAIMS_COMPANIES)
    for rec in claims.get("companies") or []:
        cid = rec["id"]
        bbox = rec.get("bbox")
        book.add(
            cid,
            source="claims_map",
            name=rec.get("holder") or rec.get("names", [None])[0],
            holder=rec.get("holder"),
            claim_count=rec.get("claim_count"),
            neighbor_count=rec.get("neighbor_count"),
            quebec_count=rec.get("quebec_count"),
            ontario_count=rec.get("ontario_count"),
            bc_count=rec.get("bc_count"),
            extract_path=rec.get("extract"),
            ontario_extract=rec.get("ontario_extract"),
            bc_extract=rec.get("bc_extract"),
            bbox_json=json.dumps(bbox) if bbox else None,
            color=rec.get("color"),
            names=rec.get("names") or [],
        )
    return claims


def write_companies(con: sqlite3.Connection, book: CompanyBook, ticker_set: set[str]) -> None:
    for cid, row in book.rows.items():
        con.execute(
            """
            INSERT INTO companies(
              company_id, name, holder, universe, rank, enabled, filing_backed,
              fiscal_year_end, ir_news, profile_file, commodity, company_type,
              country, claim_count, neighbor_count, quebec_count, ontario_count,
              bc_count, extract_path, ontario_extract, bc_extract, bbox_json,
              color, sources_json, names_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                cid,
                row.get("name"),
                row.get("holder"),
                row.get("universe"),
                row.get("rank"),
                row.get("enabled"),
                row.get("filing_backed"),
                row.get("fiscal_year_end"),
                row.get("ir_news"),
                row.get("profile_file"),
                row.get("commodity"),
                row.get("company_type"),
                row.get("country"),
                row.get("claim_count"),
                row.get("neighbor_count"),
                row.get("quebec_count"),
                row.get("ontario_count"),
                row.get("bc_count"),
                row.get("extract_path"),
                row.get("ontario_extract"),
                row.get("bc_extract"),
                row.get("bbox_json"),
                row.get("color"),
                json.dumps(row.get("sources") or []),
                json.dumps(row.get("names") or []),
            ),
        )
        for n in row.get("names") or []:
            con.execute(
                "INSERT OR IGNORE INTO company_names(company_id, name, kind) VALUES (?, ?, ?)",
                (cid, n, "alias"),
            )

    seen: set[tuple[str, str]] = set()
    for cid, pairs in book.tickers.items():
        primary_set = False
        for ticker, source, is_primary in pairs:
            if ticker not in ticker_set:
                con.execute(
                    "INSERT OR IGNORE INTO tickers(ticker, name) VALUES (?, ?)",
                    (ticker, None),
                )
                ticker_set.add(ticker)
            key = (cid, ticker)
            if key in seen:
                continue
            seen.add(key)
            pri = is_primary if not primary_set else 0
            if pri:
                primary_set = True
            con.execute(
                """
                INSERT OR IGNORE INTO company_tickers(company_id, ticker, source, is_primary)
                VALUES (?, ?, ?, ?)
                """,
                (cid, ticker, source, pri),
            )
        if not primary_set and pairs:
            con.execute(
                "UPDATE company_tickers SET is_primary=1 WHERE company_id=? AND ticker=?",
                (cid, pairs[0][0]),
            )


def ingest_tickers(con: sqlite3.Connection) -> set[str]:
    catalog = load_json(TICKERS)
    caps = load_json(MARKET_CAPS)
    cap_map = caps.get("tickers") or {}
    ticker_set: set[str] = set()
    rows = []
    for ticker, rec in (catalog.get("tickers") or {}).items():
        ticker = ticker.upper()
        cap = cap_map.get(ticker) or {}
        rows.append(
            (
                ticker,
                rec.get("name"),
                rec.get("industry") or None,
                rec.get("sic"),
                cap.get("cap"),
                cap.get("shares"),
                cap.get("px"),
                cap.get("asof"),
                cap.get("cur"),
                cap.get("src"),
            )
        )
        ticker_set.add(ticker)
    extra = []
    for ticker, cap in cap_map.items():
        ticker = ticker.upper()
        if ticker in ticker_set:
            continue
        extra.append(
            (
                ticker,
                None,
                None,
                None,
                cap.get("cap"),
                cap.get("shares"),
                cap.get("px"),
                cap.get("asof"),
                cap.get("cur"),
                cap.get("src"),
            )
        )
        ticker_set.add(ticker)
    con.executemany(
        """
        INSERT INTO tickers(
          ticker, name, industry, sic, market_cap, shares, px, cap_asof, cap_currency, cap_src
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows + extra,
    )
    return ticker_set


def ingest_people(con: sqlite3.Connection) -> None:
    bios = load_json(BIOS)
    rows = []
    for filer_id, rec in (bios.get("people") or {}).items():
        rows.append(
            (
                filer_id,
                rec.get("name"),
                rec.get("display"),
                rec.get("chamber"),
                rec.get("state"),
                rec.get("party"),
                rec.get("district"),
                rec.get("bioguide"),
                rec.get("opensecrets"),
            )
        )
    con.executemany(
        """
        INSERT INTO people(filer_id, name, display, chamber, state, party, district, bioguide, opensecrets)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def ingest_mines(con: sqlite3.Connection, claims: dict[str, Any]) -> None:
    rows = []
    for rec in claims.get("companies") or []:
        cid = rec["id"]
        for mine in rec.get("mines") or []:
            mid = mine.get("id") or slugify(mine.get("name") or "mine")
            rows.append(
                (
                    mid,
                    cid,
                    mine.get("name"),
                    mine.get("lat"),
                    mine.get("lon"),
                    mine.get("region"),
                    mine.get("country"),
                    1 if mine.get("gestim") else 0,
                    mine.get("note"),
                )
            )
    con.executemany(
        """
        INSERT OR REPLACE INTO mines(mine_id, company_id, name, lat, lon, region, country, gestim, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def ingest_claim_packs(con: sqlite3.Connection, claims: dict[str, Any], book: CompanyBook) -> list[dict[str, Any]]:
    as_of = claims.get("as_of")
    source = claims.get("source")
    jurisdiction = claims.get("jurisdiction")
    name_index = name_index_for(book)

    packs = []
    auto_links = []
    pending_jev: list[dict[str, Any]] = []

    for rec in claims.get("companies") or []:
        cid = rec["id"]
        pack_id = f"{cid}:focus"
        packs.append(
            (
                pack_id,
                cid,
                "focus",
                rec.get("holder"),
                cid,
                rec.get("claim_count") or 0,
                jurisdiction,
                source,
                as_of,
                rec.get("extract"),
                json.dumps(rec.get("bbox")) if rec.get("bbox") else None,
                rec.get("color"),
            )
        )
        auto_links.append((pack_id, cid, "owner", "catalog", None, None, "same_entity", None, None))
        pending_jev.append(
            {
                "kind": "catalog",
                "pack_id": pack_id,
                "company_id": cid,
                "claims_entity": {
                    "id": cid,
                    "names": rec.get("names") or [],
                    "holder": rec.get("holder"),
                    "claim_count": rec.get("claim_count") or 0,
                },
                "catalog_issuer": {
                    "id": cid,
                    "name": book.rows.get(cid, {}).get("name"),
                    "tickers": [t for t, _, _ in book.tickers.get(cid, [])][:6],
                    "sources": book.rows.get(cid, {}).get("sources"),
                },
            }
        )
        for neigh in rec.get("neighbors") or []:
            holder = (neigh.get("holder") or "").strip()
            if not holder:
                continue
            nid = slugify(holder)
            pack_n = f"{cid}:neighbor:{nid}"
            nn = norm_name(holder)
            matches = list(dict.fromkeys(name_index.get(nn) or []))
            holder_cid = matches[0] if len(matches) == 1 else None
            packs.append(
                (
                    pack_n,
                    cid,
                    "neighbor",
                    holder,
                    holder_cid,
                    neigh.get("count") or 0,
                    jurisdiction,
                    source,
                    as_of,
                    None,
                    None,
                    neigh.get("color"),
                )
            )
            auto_links.append((pack_n, cid, "neighbor_of", "catalog", None, None, None, None, None))
            if holder_cid:
                auto_links.append(
                    (pack_n, holder_cid, "neighbor_holder", "name_match", None, None, "same_entity", None, None)
                )
            elif not looks_like_person(holder) and nn:
                fuzzy = fuzzy_company_ids(nn, name_index)
                if len(fuzzy) == 1:
                    pending_jev.append(
                        {
                            "kind": "neighbor",
                            "pack_id": pack_n,
                            "company_id": fuzzy[0],
                            "claims_entity": {
                                "holder": holder,
                                "neighbor_of": cid,
                                "claim_count": neigh.get("count") or 0,
                            },
                            "catalog_issuer": {
                                "id": fuzzy[0],
                                "name": book.rows.get(fuzzy[0], {}).get("name"),
                                "tickers": [t for t, _, _ in book.tickers.get(fuzzy[0], [])][:6],
                            },
                        }
                    )

    con.executemany(
        """
        INSERT OR REPLACE INTO claim_packs(
          pack_id, company_id, role, holder, holder_company_id, claim_count,
          jurisdiction, source, as_of, extract_path, bbox_json, color
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        packs,
    )
    con.executemany(
        """
        INSERT INTO claim_company_links(
          pack_id, company_id, link_role, source, jev_score, jev_confidence,
          jev_outcome, same_name_noul, holder_is_vehicle_noul
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        auto_links,
    )
    return pending_jev


def discover_title_extracts() -> list[tuple[str, str, str, str, str, Path]]:
    """(company_id, jurisdiction, pack_key, count_col, extract_col, path)."""
    found: list[tuple[str, str, str, str, str, Path]] = []
    if not CLAIMS_DIR.exists():
        return found
    for p in sorted(CLAIMS_DIR.glob("*-ontario.geojson")):
        found.append(
            (p.name[: -len("-ontario.geojson")], "Ontario", "ontario", "ontario_count", "ontario_extract", p)
        )
    for p in sorted(CLAIMS_DIR.glob("*-bc.geojson")):
        found.append(
            (p.name[: -len("-bc.geojson")], "British Columbia", "bc", "bc_count", "bc_extract", p)
        )
    for p in sorted(CLAIMS_DIR.glob("*.geojson")):
        if p.name.endswith("-ontario.geojson") or p.name.endswith("-bc.geojson"):
            continue
        found.append((p.stem, "Quebec", "quebec", "quebec_count", "extract_path", p))
    return found


def _title_link_role(resolved: str, extract_cid: str, title_role: str) -> str:
    if resolved == extract_cid:
        return "title_vehicle"
    if title_role == "neighbor":
        return "neighbor_holder"
    return "jv_holder"


def ingest_claim_titles(
    con: sqlite3.Connection, book: CompanyBook
) -> list[dict[str, Any]]:
    """Per-title attributes from ON/BC/Quebec GeoJSON. Geometry is read and dropped."""
    extracts = discover_title_extracts()
    if not extracts:
        log("No claim extracts in claims/; skip claim_titles.")
        return []

    con.execute(
        """
        UPDATE companies
        SET quebec_count = COALESCE(quebec_count, claim_count, 0)
        WHERE quebec_count IS NULL
        """
    )

    name_index = name_index_for(book)
    pending: list[dict[str, Any]] = []
    seen_jev: set[tuple[str, str, str]] = set()
    pack_rows = []
    title_rows = []
    party_rows = []
    owner_links = []
    holder_links: dict[tuple[str, str], tuple] = {}
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    role_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    for cid, jurisdiction, pack_key, count_col, extract_col, path in extracts:
        if cid not in book.rows:
            log(f"  skip {path.name}: company {cid} not in catalog")
            continue
        rel = f"claims/{path.name}"
        pack_id = f"{cid}:{pack_key}"
        fc = load_json(path)
        features = fc.get("features") or []
        n_titles = 0
        n_focus = 0
        n_neighbor = 0
        unique_holders: dict[str, dict[str, Any]] = {}
        as_of = None
        source = None
        for feat in features:
            props = feat.get("properties") or {}
            feat["geometry"] = None
            claim_id = props.get("claim_id")
            if claim_id is None or claim_id == "":
                continue
            holder_raw = (props.get("holder") or "").strip() or None
            title_role = (props.get("role") or "focus").strip() or "focus"
            title_pk = f"{pack_id}:{title_role}:{claim_id}"
            parties = parse_holder_parties(holder_raw)
            majority_cid = None
            majority_pct = -1.0
            for holder_name, pct in parties:
                rec = unique_holders.setdefault(holder_name, {"n": 0, "roles": set()})
                rec["n"] += 1
                rec["roles"].add(title_role)
                resolved = None
                src = None
                nn = norm_name(holder_name)
                exact = name_index.get(nn) or []
                if len(exact) == 1:
                    resolved, src = exact[0], "name_match"
                elif title_role != "neighbor" and extract_alias_hit(holder_name, cid):
                    resolved, src = cid, "extract_alias"
                party_rows.append(
                    (title_pk, pack_id, holder_name, pct, resolved, src, None, None, None, None, None)
                )
                if resolved:
                    link_role = _title_link_role(resolved, cid, title_role)
                    holder_links[(pack_id, resolved)] = (
                        pack_id,
                        resolved,
                        link_role,
                        src,
                        None,
                        None,
                        "same_entity",
                        None,
                        None,
                    )
                if pct is not None and pct > majority_pct and resolved:
                    majority_pct = pct
                    majority_cid = resolved
                elif pct is None and resolved and majority_cid is None:
                    majority_cid = resolved
            title_rows.append(
                (
                    title_pk,
                    pack_id,
                    cid,
                    majority_cid,
                    jurisdiction,
                    str(claim_id),
                    props.get("claim_name"),
                    holder_raw,
                    props.get("status"),
                    clean_date(props.get("recorded_date")),
                    clean_date(props.get("anniversary_or_expiry")),
                    props.get("area_ha"),
                    props.get("tenure_type"),
                    props.get("source"),
                    props.get("as_of"),
                    title_role,
                    rel,
                )
            )
            as_of = as_of or props.get("as_of")
            source = source or props.get("source")
            n_titles += 1
            if title_role == "neighbor":
                n_neighbor += 1
            else:
                n_focus += 1
        counts[cid][jurisdiction] = n_titles
        role_counts[jurisdiction]["focus"] += n_focus
        role_counts[jurisdiction]["neighbor"] += n_neighbor
        pack_rows.append(
            (
                pack_id,
                cid,
                "focus",
                book.rows.get(cid, {}).get("holder") or book.rows.get(cid, {}).get("name"),
                cid,
                n_titles,
                jurisdiction,
                source,
                as_of,
                rel,
                None,
                book.rows.get(cid, {}).get("color"),
            )
        )
        owner_links.append(
            (pack_id, cid, "owner", "catalog", None, None, "same_entity", None, None)
        )
        log(
            f"  {path.name}: {n_titles} titles ({n_focus} focus / {n_neighbor} neighbor), "
            f"{len(unique_holders)} holders"
        )

        for holder_name, rec in unique_holders.items():
            nn = norm_name(holder_name)
            exact = name_index.get(nn) or []
            if len(exact) == 1:
                continue
            if extract_alias_hit(holder_name, cid) and "neighbor" not in rec["roles"]:
                continue
            if looks_like_person(holder_name):
                continue
            fuzzy = fuzzy_company_ids(nn, name_index)
            candidate = fuzzy[0] if len(fuzzy) == 1 else None
            if candidate is None:
                if "neighbor" in rec["roles"]:
                    continue
                generic = {"gold", "mines", "mining", "resources", "minerals", "metals", "exploration"}
                extract_tokens = set(norm_name(book.rows.get(cid, {}).get("name") or cid).split()) - generic
                holder_tokens = set(nn.split()) - generic
                if holder_tokens & extract_tokens:
                    candidate = cid
                else:
                    continue
            key = (pack_id, holder_name, candidate)
            if key in seen_jev:
                continue
            seen_jev.add(key)
            roles = rec["roles"]
            if candidate == cid:
                link_role = "title_vehicle"
            elif "neighbor" in roles and "focus" not in roles:
                link_role = "neighbor_holder"
            else:
                link_role = "jv_holder"
            pending.append(
                {
                    "kind": "title_holder",
                    "pack_id": pack_id,
                    "company_id": candidate,
                    "holder_name": holder_name,
                    "link_role": link_role,
                    "claims_entity": {
                        "holder": holder_name,
                        "jurisdiction": jurisdiction,
                        "extract_company_id": cid,
                        "extract_company": book.rows.get(cid, {}).get("name"),
                        "title_count": rec["n"],
                        "roles": sorted(roles),
                    },
                    "catalog_issuer": {
                        "id": candidate,
                        "name": book.rows.get(candidate, {}).get("name"),
                        "tickers": [t for t, _, _ in book.tickers.get(candidate, [])][:6],
                    },
                }
            )

        overlay_n = n_focus if jurisdiction == "Quebec" else n_titles
        con.execute(
            f"UPDATE companies SET {count_col}=?, {extract_col}=? WHERE company_id=?",
            (overlay_n, rel, cid),
        )
        if jurisdiction == "Quebec":
            con.execute(
                "UPDATE companies SET neighbor_count=? WHERE company_id=?",
                (n_neighbor, cid),
            )
        con.execute(
            """
            UPDATE companies SET claim_count =
              COALESCE(quebec_count, 0) + COALESCE(ontario_count, 0) + COALESCE(bc_count, 0)
            WHERE company_id=?
            """,
            (cid,),
        )

    if pack_rows:
        con.executemany(
            """
            INSERT OR REPLACE INTO claim_packs(
              pack_id, company_id, role, holder, holder_company_id, claim_count,
              jurisdiction, source, as_of, extract_path, bbox_json, color
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            pack_rows,
        )
    if owner_links:
        con.executemany(
            """
            INSERT INTO claim_company_links(
              pack_id, company_id, link_role, source, jev_score, jev_confidence,
              jev_outcome, same_name_noul, holder_is_vehicle_noul
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            owner_links + list(holder_links.values()),
        )
    if title_rows:
        con.executemany(
            """
            INSERT OR REPLACE INTO claim_titles(
              title_pk, pack_id, company_id, holder_company_id, jurisdiction, claim_id,
              claim_name, holder_raw, status, recorded_date, anniversary_or_expiry,
              area_ha, tenure_type, source, as_of, role, extract_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            title_rows,
        )
    if party_rows:
        con.executemany(
            """
            INSERT INTO claim_title_parties(
              title_pk, pack_id, holder_name, interest_pct, holder_company_id, source,
              jev_score, jev_confidence, jev_outcome, same_name_noul, holder_is_vehicle_noul
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            party_rows,
        )

    meta_set(
        con,
        "claim_titles",
        {
            "files": [str(p.name) for *_, p in extracts],
            "counts": {cid: dict(v) for cid, v in counts.items()},
            "roles": {j: dict(v) for j, v in role_counts.items()},
        },
    )
    return pending


def ingest_politician_trades(con: sqlite3.Connection) -> None:
    payload = load_json(TRADES)
    if payload.get("collected"):
        meta_set(con, "tape_collected", payload.get("collected"))
    rows = []
    for t in payload.get("trades") or []:
        lo, hi, mid = parse_amount_band(t.get("amount"))
        ticker = (t.get("ticker") or "").strip().upper() or None
        rows.append(
            (
                t.get("id"),
                t.get("filer"),
                t.get("filer_id"),
                t.get("chamber"),
                ticker,
                t.get("asset"),
                t.get("asset_type"),
                t.get("side"),
                t.get("amount"),
                lo,
                hi,
                mid,
                t.get("trade_date"),
                t.get("filed_date"),
                t.get("owner"),
                t.get("source"),
                t.get("added"),
            )
        )
    con.executemany(
        """
        INSERT OR REPLACE INTO politician_trades(
          trade_id, filer, filer_id, chamber, ticker, asset, asset_type, side,
          amount_raw, amount_low, amount_high, amount_mid, trade_date, filed_date,
          owner, source, added
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def ingest_insider_trades(con: sqlite3.Connection) -> None:
    payload = load_json(INSIDER_TRADES)
    rows = []
    for t in payload.get("trades") or []:
        ticker = (t.get("ticker") or "").strip().upper() or None
        rows.append(
            (
                t.get("id"),
                t.get("filer"),
                t.get("filer_id"),
                t.get("title"),
                ticker,
                t.get("company"),
                t.get("company_type"),
                t.get("commodity"),
                t.get("side"),
                t.get("shares"),
                t.get("price"),
                t.get("value"),
                t.get("amount"),
                t.get("trade_date"),
                t.get("filed_date"),
                t.get("origin"),
                t.get("exchange"),
            )
        )
    con.executemany(
        """
        INSERT OR REPLACE INTO insider_trades(
          trade_id, filer, filer_id, title, ticker, company, company_type, commodity,
          side, shares, price, value, amount_raw, trade_date, filed_date, origin, exchange
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def materialize_calc(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM trade_size_vs_cap")
    con.execute(
        """
        INSERT INTO trade_size_vs_cap(
          trade_id, ticker, company_id, filer, filer_id, side, trade_date,
          amount_raw, amount_mid, market_cap, size_bps, has_claims
        )
        SELECT
          trade_id, ticker, company_id, filer, filer_id, side, trade_date,
          amount_raw, amount_mid, market_cap, size_bps, has_claims
        FROM v_trade_size_vs_cap
        WHERE ticker IS NOT NULL AND ticker != '' AND amount_mid IS NOT NULL
        """
    )


def run_schema_jev(con: sqlite3.Connection, skip: bool) -> dict[str, Any]:
    fallback = {
        "claims_grain": "company_extract_summary",
        "company_key": "slug_union",
        "pilot_calc": "size_vs_cap",
        "source": "fallback",
    }
    if skip or not jev.key_present():
        meta_set(con, "jev_schema", fallback)
        return fallback
    try:
        packed = jev.ask(
            {
                "goal": "SQLite middle layer for Quantity Capital: claims linked to companies and tickers, then one politician trade calc. GeoJSON polygons must stay out of the DB. Inputs are GESTIM claim catalog summaries, mines registries, tickers, market caps, politician PTR trades, insider trades.",
                "geojson_local": False,
                "claim_producers": 33,
                "politician_trades": 27000,
            },
            jev.schema_questions(),
            label="schema_v1",
        )
        record_jev(con, "schema", "v1", packed)
        answers = packed["answers"]
        decided = {
            "claims_grain": answers["claims_grain"]["choice"],
            "company_key": answers["company_key"]["choice"],
            "pilot_calc": answers["pilot_calc"]["choice"],
            "source": "jev",
            "confidence": {
                "claims_grain": answers["claims_grain"].get("confidence"),
                "company_key": answers["company_key"].get("confidence"),
                "pilot_calc": answers["pilot_calc"].get("confidence"),
            },
        }
        meta_set(con, "jev_schema", decided)
        log(
            "Jev schema: grain={claims_grain} key={company_key} calc={pilot_calc}".format(
                **decided
            )
        )
        return decided
    except Exception as exc:
        log(f"Jev schema call failed ({type(exc).__name__}); using fallback. See README.")
        fallback["error"] = type(exc).__name__
        meta_set(con, "jev_schema", fallback)
        return fallback


def apply_link_judgment(item: dict[str, Any]) -> dict[str, Any]:
    packed = jev.ask(
        {
            "claims_entity": item["claims_entity"],
            "catalog_issuer": item["catalog_issuer"],
        },
        jev.link_questions(),
        label=f"link:{item['kind']}:{item['pack_id']}:{item['company_id']}",
    )
    link = packed["answers"]["link_state"]
    score = float(link["score"])
    return {
        "item": item,
        "packed": packed,
        "score": score,
        "confidence": link.get("confidence"),
        "outcome": jev.round_link_outcome(score),
        "same_name": packed["answers"]["same_name"]["noul"],
        "holder_is_vehicle": packed["answers"]["holder_is_vehicle"]["noul"],
    }


def run_link_jev(con: sqlite3.Connection, pending: list[dict[str, Any]], skip: bool) -> None:
    if skip or not pending:
        meta_set(con, "jev_links", {"ran": False, "pending": len(pending)})
        return
    if not jev.key_present():
        log("TYPESAFE_API_KEY missing; claim links keep catalog/name_match only.")
        meta_set(con, "jev_links", {"ran": False, "reason": "no_key", "pending": len(pending)})
        return
    log(f"Jev claim↔company links: {len(pending)} pairs")
    results = []
    errors = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(apply_link_judgment, item) for item in pending]
        for fut in as_completed(futs):
            try:
                results.append(fut.result())
            except Exception as exc:
                errors += 1
                log(f"  link Jev error: {type(exc).__name__}: {exc}")
    for r in results:
        item = r["item"]
        record_jev(con, "claim_link", item["pack_id"], r["packed"])
        if item["kind"] == "catalog":
            con.execute(
                """
                UPDATE claim_company_links
                SET jev_score=?, jev_confidence=?, jev_outcome=?,
                    same_name_noul=?, holder_is_vehicle_noul=?
                WHERE pack_id=? AND company_id=? AND link_role='owner'
                """,
                (
                    r["score"],
                    r["confidence"],
                    r["outcome"],
                    r["same_name"],
                    r["holder_is_vehicle"],
                    item["pack_id"],
                    item["company_id"],
                ),
            )
        else:
            if item["kind"] == "title_holder":
                con.execute(
                    """
                    UPDATE claim_title_parties
                    SET holder_company_id=?, source='jev', jev_score=?, jev_confidence=?,
                        jev_outcome=?, same_name_noul=?, holder_is_vehicle_noul=?
                    WHERE pack_id=? AND holder_name=? AND holder_company_id IS NULL
                    """,
                    (
                        item["company_id"] if r["outcome"] == "same_entity" else None,
                        r["score"],
                        r["confidence"],
                        r["outcome"],
                        r["same_name"],
                        r["holder_is_vehicle"],
                        item["pack_id"],
                        item["holder_name"],
                    ),
                )
                if r["outcome"] == "same_entity":
                    con.execute(
                        """
                        UPDATE claim_titles
                        SET holder_company_id=?
                        WHERE pack_id=? AND holder_company_id IS NULL
                          AND title_pk IN (
                            SELECT title_pk FROM claim_title_parties
                            WHERE pack_id=? AND holder_name=? AND holder_company_id=?
                          )
                        """,
                        (
                            item["company_id"],
                            item["pack_id"],
                            item["pack_id"],
                            item["holder_name"],
                            item["company_id"],
                        ),
                    )
            if r["outcome"] == "leave_unlinked":
                continue
            link_role = item.get("link_role") or (
                "jv_holder" if item["kind"] == "title_holder" else "neighbor_holder"
            )
            con.execute(
                """
                INSERT INTO claim_company_links(
                  pack_id, company_id, link_role, source, jev_score, jev_confidence,
                  jev_outcome, same_name_noul, holder_is_vehicle_noul
                ) VALUES (?, ?, ?, 'jev', ?, ?, ?, ?, ?)
                """,
                (
                    item["pack_id"],
                    item["company_id"],
                    link_role,
                    r["score"],
                    r["confidence"],
                    r["outcome"],
                    r["same_name"],
                    r["holder_is_vehicle"],
                ),
            )
            if r["outcome"] == "same_entity":
                con.execute(
                    "UPDATE claim_packs SET holder_company_id=? WHERE pack_id=? AND holder_company_id IS NULL",
                    (item["company_id"], item["pack_id"]),
                )
    meta_set(
        con,
        "jev_links",
        {"ran": True, "n": len(results), "errors": errors, "pending": len(pending)},
    )


def apply_calc_judgment(row: sqlite3.Row) -> dict[str, Any]:
    state = {
        "trade": {
            "id": row["trade_id"],
            "ticker": row["ticker"],
            "company_id": row["company_id"],
            "filer": row["filer"],
            "side": row["side"],
            "amount_raw": row["amount_raw"],
            "amount_mid": row["amount_mid"],
            "market_cap": row["market_cap"],
            "size_bps": row["size_bps"],
            "has_claims": row["has_claims"],
            "trade_date": row["trade_date"],
        },
        "note": (
            "size_bps is 10,000 * amount_mid / market_cap. STOCK Act amounts are bands, "
            "so midpoint is an estimate. Typical politician prints are a few bps or less "
            "on mega-caps and larger on small issuers."
        ),
    }
    packed = jev.ask(state, jev.calc_questions(), label=f"calc:{row['trade_id']}")
    return {"trade_id": row["trade_id"], "packed": packed}


def run_calc_jev(con: sqlite3.Connection, skip: bool) -> None:
    if skip:
        meta_set(con, "jev_calc", {"ran": False})
        return
    if not jev.key_present():
        log("TYPESAFE_API_KEY missing; calc table has no flags.")
        meta_set(con, "jev_calc", {"ran": False, "reason": "no_key"})
        return
    rows = list(
        con.execute(
            """
            SELECT * FROM trade_size_vs_cap
            WHERE size_bps IS NOT NULL
            ORDER BY size_bps DESC
            LIMIT 20
            """
        )
    )
    log(f"Jev calc flags: top {len(rows)} size_bps trades")
    errors = 0
    n = 0
    with ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(apply_calc_judgment, row) for row in rows]
        for fut in as_completed(futs):
            try:
                r = fut.result()
            except Exception as exc:
                errors += 1
                log(f"  calc Jev error: {type(exc).__name__}: {exc}")
                continue
            packed = r["packed"]
            record_jev(con, "trade_calc", r["trade_id"], packed)
            flag = packed["answers"]["flag"]
            con.execute(
                """
                UPDATE trade_size_vs_cap
                SET jev_flag=?, jev_confidence=?, jev_anomaly_noul=?, jev_model=?
                WHERE trade_id=?
                """,
                (
                    flag["choice"],
                    flag.get("confidence"),
                    packed["answers"]["is_anomaly"]["noul"],
                    packed.get("model"),
                    r["trade_id"],
                ),
            )
            n += 1
    meta_set(con, "jev_calc", {"ran": True, "n": n, "errors": errors})


def export_stubs(con: sqlite3.Connection) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    companies = [
        dict(r)
        for r in con.execute(
            """
            SELECT company_id, name, holder, claim_count, neighbor_count, extract_path,
                   commodity, company_type, sources_json
            FROM companies
            WHERE claim_count IS NOT NULL
            ORDER BY COALESCE(claim_count,0) DESC, company_id
            """
        )
    ]
    links = [
        dict(r)
        for r in con.execute(
            """
            SELECT l.pack_id, p.role, p.holder, l.company_id, c.name AS company_name,
                   l.link_role, l.source, l.jev_outcome, l.jev_score, l.jev_confidence,
                   p.claim_count
            FROM claim_company_links l
            JOIN claim_packs p ON p.pack_id = l.pack_id
            JOIN companies c ON c.company_id = l.company_id
            ORDER BY p.claim_count DESC, l.pack_id
            """
        )
    ]
    calc = [
        dict(r)
        for r in con.execute(
            """
            SELECT trade_id, ticker, company_id, filer, side, trade_date, amount_raw,
                   amount_mid, market_cap, size_bps, has_claims, jev_flag,
                   jev_confidence, jev_anomaly_noul
            FROM trade_size_vs_cap
            WHERE size_bps IS NOT NULL
            ORDER BY size_bps DESC
            LIMIT 200
            """
        )
    ]
    (EXPORT_DIR / "claims_companies.json").write_text(
        json.dumps({"n": len(companies), "companies": companies}, indent=2), encoding="utf-8"
    )
    (EXPORT_DIR / "claim_company_links.json").write_text(
        json.dumps({"n": len(links), "links": links}, indent=2), encoding="utf-8"
    )
    titles = [
        dict(r)
        for r in con.execute(
            """
            SELECT jurisdiction, COALESCE(role,'focus') AS role, company_id, COUNT(*) AS n
            FROM claim_titles
            GROUP BY jurisdiction, role, company_id
            ORDER BY jurisdiction, role, n DESC
            """
        )
    ]
    (EXPORT_DIR / "claim_titles_summary.json").write_text(
        json.dumps({"by_company": titles}, indent=2), encoding="utf-8"
    )
    (EXPORT_DIR / "trade_size_vs_cap_top.json").write_text(
        json.dumps(
            {
                "calc": "size_vs_cap",
                "formula": "size_bps = 10000 * amount_mid / market_cap",
                "n": len(calc),
                "rows": calc,
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def print_report(con: sqlite3.Connection) -> None:
    def n(sql: str) -> int:
        return con.execute(sql).fetchone()[0]

    log("---- qc.sqlite ----")
    have = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for table in (
        "companies",
        "tickers",
        "company_tickers",
        "claim_packs",
        "claim_company_links",
        "claim_titles",
        "claim_title_parties",
        "mines",
        "people",
        "politician_trades",
        "insider_trades",
        "trade_size_vs_cap",
        "tell_events",
        "tell_hands",
        "tell_now",
        "analysis_candidates",
        "paper_filers",
        "paper_legs",
        "insider_form4",
        "insider_follow",
        "jev_decisions",
    ):
        if table not in have:
            continue
        log(f"  {table:22} {n(f'SELECT COUNT(*) FROM {table}'):>8}")
    focus_n = n("SELECT COUNT(*) FROM claim_packs WHERE role='focus'")
    neigh_n = n("SELECT COUNT(*) FROM claim_packs WHERE role='neighbor'")
    log(f"  claim packs focus/neighbor {focus_n} / {neigh_n}")
    for r in con.execute(
        """
        SELECT jurisdiction, COALESCE(role,''), COUNT(*)
        FROM claim_titles GROUP BY 1, 2 ORDER BY 1, 2
        """
    ):
        log(f"  claim titles {r[0]} {r[1] or 'focus'}: {r[2]}")
    for r in con.execute(
        """
        SELECT source, jev_outcome, COUNT(*) FROM claim_company_links
        GROUP BY 1, 2 ORDER BY 3 DESC
        """
    ):
        log(f"  links {r[0]}/{r[1]}: {r[2]}")
    log(
        "  calc rows with bps "
        f"{n('SELECT COUNT(*) FROM trade_size_vs_cap WHERE size_bps IS NOT NULL')}"
    )
    log(
        "  calc Jev-flagged "
        f"{n('SELECT COUNT(*) FROM trade_size_vs_cap WHERE jev_flag IS NOT NULL')}"
    )
    try:
        ab = n("SELECT COUNT(*) FROM analysis_candidates WHERE shipped=1 AND list='book'")
        aa = n("SELECT COUNT(*) FROM analysis_candidates WHERE shipped=1 AND list='avoid'")
        log(f"  analysis shipped book/avoid {ab} / {aa}")
    except sqlite3.Error:
        pass
    top = con.execute(
        """
        SELECT ticker, filer, side, amount_raw, ROUND(size_bps, 4), jev_flag
        FROM trade_size_vs_cap
        WHERE size_bps IS NOT NULL
        ORDER BY size_bps DESC
        LIMIT 5
        """
    ).fetchall()
    log("  top size_bps:")
    for r in top:
        log(f"    {r[0]:8} {r[1][:22]:22} {r[2]:8} {r[3]:22} bps={r[4]} flag={r[5]}")


def refresh_tapes(con: sqlite3.Connection) -> None:
    con.execute("DELETE FROM trade_size_vs_cap")
    con.execute("DELETE FROM politician_trades")
    ingest_politician_trades(con)
    con.execute("DELETE FROM insider_trades")
    ingest_insider_trades(con)
    materialize_calc(con)


def run_boards(
    con: sqlite3.Connection,
    *,
    skip_jev: bool,
    skip_tells: bool,
    skip_analysis: bool,
    skip_paper: bool,
    skip_insider: bool,
    excel: bool,
) -> None:
    if not skip_tells:
        log("Tells board…")
        import tells as tells_mod

        tells_mod.run(con)
    if not skip_analysis:
        log("Analysis / signals book…")
        import analysis as analysis_mod

        analysis_mod.run(con, skip_jev=skip_jev)
    if not skip_paper:
        log("Paper / backtest…")
        import paper as paper_mod

        paper_mod.run(con, skip_jev=skip_jev)
    if not skip_insider:
        log("Insider boards…")
        import insider_boards as insider_mod

        insider_mod.run(con, skip_jev=skip_jev)
    if excel:
        log("Excel export…")
        import export_excel as excel_mod

        excel_mod.run(con)


def rebuild_boards(
    skip_jev: bool = False,
    skip_tells: bool = False,
    skip_analysis: bool = False,
    skip_paper: bool = False,
    skip_insider: bool = False,
    excel: bool = False,
) -> int:
    if not DB_PATH.exists():
        log("no qc.sqlite; running full rebuild")
        rebuild(
            skip_jev=skip_jev,
            skip_tells=skip_tells,
            skip_analysis=skip_analysis,
            skip_paper=skip_paper,
            skip_insider=skip_insider,
            excel=excel,
        )
        return 0
    con = connect(DB_PATH)
    try:
        log("Boards-only refresh of tapes + exports")
        refresh_tapes(con)
        con.commit()
        run_boards(
            con,
            skip_jev=skip_jev,
            skip_tells=skip_tells,
            skip_analysis=skip_analysis,
            skip_paper=skip_paper,
            skip_insider=skip_insider,
            excel=excel,
        )
        meta_set(con, "boards_refreshed_at", now_iso())
        con.commit()
        print_report(con)
    finally:
        con.close()
    try:
        shutil.copy2(DB_PATH, DB_MIRROR)
        log(f"Mirrored {DB_MIRROR}")
    except OSError as exc:
        log(f"Mirror skipped: {exc}")
    return 0


def rebuild(skip_jev: bool, skip_tells: bool = False, skip_analysis: bool = False, skip_paper: bool = False, skip_insider: bool = False, excel: bool = False) -> None:
    tmp = DB_PATH.with_suffix(".sqlite.tmp")
    if tmp.exists():
        tmp.unlink()
    log(f"Building {tmp}")
    con = connect(tmp)
    try:
        apply_schema(con)
        meta_set(con, "schema_version", "qc-sqlite-pilot-v5")
        meta_set(con, "built_at", now_iso())
        meta_set(con, "pilot_calc", "size_vs_cap")
        meta_set(
            con,
            "pilot_calc_doc",
            "Politician STOCK Act amount-band midpoint as basis points of issuer market cap: 10000 * amount_mid / market_cap. Joins trades.ticker to tickers and primary company_tickers. Jev flags the 20 largest relative prints.",
        )

        schema_decision = run_schema_jev(con, skip_jev)
        if schema_decision.get("claims_grain") == "defer_claims":
            log("Jev asked to defer claims; still ingesting attribute packs (scope lock).")
        if schema_decision.get("pilot_calc") and schema_decision["pilot_calc"] != "size_vs_cap":
            log(
                f"Jev preferred {schema_decision['pilot_calc']}; shipping size_vs_cap as the implemented pilot (others later)."
            )

        log("Tickers + market caps…")
        ticker_set = ingest_tickers(con)
        log("Companies…")
        book = CompanyBook()
        claims = ingest_company_sources(book)
        write_companies(con, book, ticker_set)
        log("People…")
        ingest_people(con)
        log("Mine pins…")
        ingest_mines(con, claims)
        log("Claim packs + catalog links…")
        pending = ingest_claim_packs(con, claims, book)
        log("Claim titles (Quebec / Ontario / BC)…")
        pending.extend(ingest_claim_titles(con, book))
        log("Politician trades…")
        ingest_politician_trades(con)
        log("Insider trades…")
        ingest_insider_trades(con)
        log("Pilot calc trade_size_vs_cap…")
        materialize_calc(con)
        con.commit()

        run_link_jev(con, pending, skip_jev)
        run_calc_jev(con, skip_jev)
        run_boards(
            con,
            skip_jev=skip_jev,
            skip_tells=skip_tells,
            skip_analysis=skip_analysis,
            skip_paper=skip_paper,
            skip_insider=skip_insider,
            excel=excel,
        )
        meta_set(con, "built_finished_at", now_iso())
        con.commit()
        export_stubs(con)
        print_report(con)
    finally:
        con.close()

    if DB_PATH.exists():
        DB_PATH.unlink()
    tmp.replace(DB_PATH)
    log(f"Wrote {DB_PATH} ({DB_PATH.stat().st_size:,} bytes)")
    try:
        shutil.copy2(DB_PATH, DB_MIRROR)
        log(f"Mirrored {DB_MIRROR}")
    except OSError as exc:
        log(f"Mirror skipped: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild Quantity Capital qc.sqlite")
    parser.add_argument("--skip-jev", action="store_true", help="Ingest only; leave Jev fields null")
    parser.add_argument("--skip-tells", action="store_true", help="Skip Tells board calc / tells.json export")
    parser.add_argument("--skip-analysis", action="store_true", help="Skip analysis.json / signals book")
    parser.add_argument("--skip-paper", action="store_true", help="Skip paper / backtest.json")
    parser.add_argument("--skip-insider", action="store_true", help="Skip insider boards")
    parser.add_argument("--excel", action="store_true", help="Write Desktop xlsx exports")
    parser.add_argument(
        "--boards-only",
        action="store_true",
        help="Refresh tapes + board JSON on existing qc.sqlite (skip claims ingest)",
    )
    args = parser.parse_args()
    os.chdir(QC_ROOT)
    if args.boards_only:
        return rebuild_boards(
            skip_jev=args.skip_jev,
            skip_tells=args.skip_tells,
            skip_analysis=args.skip_analysis,
            skip_paper=args.skip_paper,
            skip_insider=args.skip_insider,
            excel=args.excel,
        )
    rebuild(
        skip_jev=args.skip_jev,
        skip_tells=args.skip_tells,
        skip_analysis=args.skip_analysis,
        skip_paper=args.skip_paper,
        skip_insider=args.skip_insider,
        excel=args.excel,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
