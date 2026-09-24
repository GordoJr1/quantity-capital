#!/usr/bin/env python3
"""Ontario MLAS claims database prototype.

The full title set stays in gitignored claims/.db/ontario.sqlite (stdlib
SQLite, geometry as GeoJSON). Published outputs are small JSON files under
claims/links, claims/search, and claims/around. The PMTiles archive is
gitignored under claims/.build/.

    python3 scripts/ontario_claims_db.py ingest
    python3 scripts/ontario_claims_db.py link
    python3 scripts/ontario_claims_db.py search
    python3 scripts/ontario_claims_db.py around
    python3 scripts/ontario_claims_db.py tiles
    python3 scripts/ontario_claims_db.py report

ingest uses the same MLAS keyset walk as build_on_bc_extracts.py
--full-ontario (OBJECTID order, 1,000-row pages, ~40 m generalization,
0.12 s between pages). No company filter.

Linking is deterministic first (catalog names, curated aliases, focus
holders already on a company extract, a single distinctive token). TypeSafe
Jev (jev-latest) runs only when two site companies remain tied. The key is
read from the environment or the store env file and is never printed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from build_on_bc_extracts import (  # noqa: E402
    DEFAULT_CACHE_DIR,
    OFFSET_DEG,
    ON_URL,
    PAGE,
    ArcGISError,
    cached_get_json,
    epoch_to_date,
    live_count,
)
from qc_io import atomic_write_json  # noqa: E402

CLAIMS = ROOT / "claims"
BETA = ROOT / "beta"
DB_PATH = CLAIMS / ".db" / "ontario.sqlite"
BUILD_DIR = CLAIMS / ".build"
LINKS_PATH = CLAIMS / "links" / "holders.json"
ALIASES_PATH = CLAIMS / "links" / "aliases.json"
SEARCH_HOLDERS = CLAIMS / "search" / "holders.json"
SEARCH_TITLES = CLAIMS / "search" / "titles"
AROUND_PATH = CLAIMS / "around" / "ontario-mines.json"
REPORT_PATH = CLAIMS / "links" / "build-report.json"
SCORE_PATH = CLAIMS / "links" / "jev-prototype-score.json"
PUBLICS_PATH = BETA / "claims-publics.json"
STORE_ENV = Path("/cursor/stores/bc-72d341b8-c108-4d41-b354-bacf0a3db316/internal/typesafe.env")

ON_FULL_FIELDS = (
    "OBJECTID,TENURE_NUMBER_ID,TITLE_TYPE_DESC,TENURE_STATUS_DESC,"
    "ISSUE_DATE,ANNIVERSARY_DATE,CLAIM_DUE_DATE,EXTENSION_DATE,HOLDER"
)
SLEEP_S = 0.12
M_PER_DEG_LAT = 111_320.0
GITHUB_FILE_LIMIT = 100 * 1024 * 1024
UNLINKED = "unlinked"

# 2026-09-24 returnCountOnly figures (claims review), except Quebec, which is
# the stated ~124k catalog floor rather than a live GESTIM count.
PROVINCE_COUNTS = {
    "ontario": None,  # filled from this ingest
    "yukon": 168_961,
    "quebec": 124_000,
    "nunavut": 34_551,
    "british_columbia": 10_000,  # MapServer view cap, not the full registry
    "manitoba": 6_297 + 3_825 + 180,
    "saskatchewan": 7_368,
    "newfoundland_and_labrador": 4_694,
    "nova_scotia": 2_183,
    "new_brunswick": 1_367,
    "northwest_territories": 1_102,
}

LEGAL_TOKENS = {
    "ltd", "limited", "limitee", "inc", "incorporated", "corp", "corporation",
    "company", "llc", "llp", "ulc", "plc", "gmbh", "the",
}
# "co", "lp", "nv", and "sa" stay in the name. Stripping them collapses
# "LP Gold" and "NV Gold" into the same generic token.
STOP_TOKENS = LEGAL_TOKENS | {
    "resources", "resource", "exploration", "explorations", "mining", "mines",
    "mine", "minerals", "mineral", "metals", "metal", "gold", "silver",
    "copper", "nickel", "cobalt", "uranium", "lithium", "platinum", "diamond",
    "diamonds", "energy", "energies", "holdings", "holding", "group",
    "international", "ventures", "venture", "capital", "royalty", "royalties",
    "canada", "canadian", "ontario", "quebec", "british", "columbia", "and",
    "for", "north", "south", "east", "west", "lake", "lakes", "river", "new",
    "first", "great", "bear", "american", "america", "global", "world",
    "pacific", "atlantic", "northern", "southern", "eastern", "western",
    "central", "royal", "red", "black", "white", "blue", "green", "big",
    "rock", "stone", "creek", "mountain", "mountains", "hill", "hills",
    "valley", "bay", "point", "island", "islands", "project", "projects",
    "partners", "partner", "development", "developments", "operations",
    "operation", "joint", "services", "service", "geological", "contracting",
    "construction", "materials", "advanced", "elements", "operating",
}
MIN_INTEREST = 20.0
CORP_HINT_RE = __import__("re").compile(
    r"\b(inc|corp|ltd|limited|llc|resources|mines|mining|gold|exploration|"
    r"corporation|minerals|metals|energy|holdings|company|ulc|plc|gmbh)\b",
    __import__("re").I,
)
ACCENT = str.maketrans({
    "é": "e", "è": "e", "ê": "e", "ë": "e", "à": "a", "â": "a", "ä": "a",
    "ô": "o", "ö": "o", "î": "i", "ï": "i", "ù": "u", "û": "u", "ç": "c",
    "É": "e", "È": "e", "À": "a", "Â": "a", "Ô": "o", "Î": "i", "Ç": "c",
    "á": "a", "í": "i", "ó": "o", "ú": "u", "ñ": "n",
})
PCT_HOLDER_RE = __import__("re").compile(r"\((\d+(?:\.\d+)?)\)\s*([^,]+?)(?=\s*,\s*\(|$)")
NON_ALNUM = __import__("re").compile(r"[^a-z0-9]+")

METHOD_RANK = {
    "alias": 0,
    "extract_holder": 1,
    "catalog_name": 2,
    "distinctive_token": 3,
    "jev": 4,
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS titles (
  objectid INTEGER PRIMARY KEY,
  title_id TEXT NOT NULL,
  holder TEXT,
  status TEXT,
  tenure_type TEXT,
  issue_date TEXT,
  anniversary_date TEXT,
  due_date TEXT,
  extension_date TEXT,
  area_ha REAL,
  minx REAL,
  miny REAL,
  maxx REAL,
  maxy REAL,
  geom_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_titles_holder ON titles(holder);
CREATE INDEX IF NOT EXISTS idx_titles_id ON titles(title_id);
CREATE INDEX IF NOT EXISTS idx_titles_bbox ON titles(minx, maxx, miny, maxy);
"""


def log(msg: str) -> None:
    print(msg, flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect(path: Path = DB_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def holder_key(value: str | None) -> str:
    return (value or "").strip() or "(blank)"


def normalize_name(value: str | None) -> str:
    text = (value or "").translate(ACCENT).lower().replace("&", " and ")
    text = NON_ALNUM.sub(" ", text)
    tokens = [tok for tok in text.split() if tok not in LEGAL_TOKENS]
    return " ".join(tokens)


def distinctive_tokens(norm: str) -> set[str]:
    return {tok for tok in norm.split() if len(tok) >= 6 and tok not in STOP_TOKENS}


def parse_parties(raw: str | None) -> list[tuple[str, float | None]]:
    text = (raw or "").strip()
    if not text:
        return []
    found = PCT_HOLDER_RE.findall(text)
    if found:
        return [(name.strip(" ."), float(pct)) for pct, name in found if name.strip()]
    return [(text.strip(" ."), None)]


def phrase_in(phrase: str, norm: str) -> bool:
    if not phrase or not norm:
        return False
    return f" {phrase} " in f" {norm} "


def shard_key(title_id: str) -> str:
    text = str(title_id).strip()
    if len(text) >= 2:
        return text[:2]
    return text.zfill(2)


def company_color(company_id: str | None) -> str:
    if not company_id or company_id == UNLINKED:
        return "#5c6b7a"
    digest = hashlib.sha1(company_id.encode("utf-8")).hexdigest()
    red = 70 + (int(digest[0:2], 16) % 170)
    green = 70 + (int(digest[2:4], 16) % 170)
    blue = 70 + (int(digest[4:6], 16) % 170)
    return f"#{red:02x}{green:02x}{blue:02x}"


def _ring_area_m2(ring: list) -> float:
    if not ring or len(ring) < 4:
        return 0.0
    lat0 = sum(point[1] for point in ring) / len(ring)
    lon0 = sum(point[0] for point in ring) / len(ring)
    mx = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    area = 0.0
    for i in range(len(ring) - 1):
        x1 = (ring[i][0] - lon0) * mx
        y1 = (ring[i][1] - lat0) * M_PER_DEG_LAT
        x2 = (ring[i + 1][0] - lon0) * mx
        y2 = (ring[i + 1][1] - lat0) * M_PER_DEG_LAT
        area += x1 * y2 - x2 * y1
    return abs(area) / 2.0


def geometry_area_ha(geom: dict | None) -> float | None:
    if not geom:
        return None
    kind = geom.get("type")
    coords = geom.get("coordinates") or []
    if kind == "Polygon":
        if not coords:
            return None
        area = _ring_area_m2(coords[0])
        for hole in coords[1:]:
            area -= _ring_area_m2(hole)
        return round(max(area, 0.0) / 10_000.0, 2)
    if kind == "MultiPolygon":
        total = 0.0
        for poly in coords:
            if not poly:
                continue
            area = _ring_area_m2(poly[0])
            for hole in poly[1:]:
                area -= _ring_area_m2(hole)
            total += max(area, 0.0)
        return round(total / 10_000.0, 2)
    return None


def geometry_bbox(geom: dict | None) -> tuple[float, float, float, float] | None:
    if not geom:
        return None
    xs: list[float] = []
    ys: list[float] = []

    def walk(node) -> None:
        if not node:
            return
        if isinstance(node[0], (int, float)):
            xs.append(float(node[0]))
            ys.append(float(node[1]))
            return
        for part in node:
            walk(part)

    walk(geom.get("coordinates"))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _xy(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    mx = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    return (lon - lon0) * mx, (lat - lat0) * M_PER_DEG_LAT


def _point_in_ring(x: float, y: float, ring_xy: list[tuple[float, float]]) -> bool:
    inside = False
    j = len(ring_xy) - 1
    for i in range(len(ring_xy)):
        xi, yi = ring_xy[i]
        xj, yj = ring_xy[j]
        if (yi > y) != (yj > y):
            denom = (yj - yi) or 1e-12
            if x < (xj - xi) * (y - yi) / denom + xi:
                inside = not inside
        j = i
    return inside


def _seg_dist(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _ring_distance_m(x: float, y: float, ring_xy: list[tuple[float, float]]) -> float:
    best = float("inf")
    for i in range(len(ring_xy) - 1):
        ax, ay = ring_xy[i]
        bx, by = ring_xy[i + 1]
        best = min(best, _seg_dist(x, y, ax, ay, bx, by))
    return best


def geometry_distance_km(lon: float, lat: float, geom: dict | None) -> float | None:
    """Distance from a point to a polygon, in kilometres. Inside is 0."""
    if not geom or geom.get("type") not in ("Polygon", "MultiPolygon"):
        return None
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    best = float("inf")
    for poly in polys:
        if not poly or not poly[0]:
            continue
        outer = poly[0]
        lon0 = sum(p[0] for p in outer) / len(outer)
        lat0 = sum(p[1] for p in outer) / len(outer)
        rings = [[_xy(p[0], p[1], lon0, lat0) for p in ring] for ring in poly if ring]
        if not rings:
            continue
        px, py = _xy(lon, lat, lon0, lat0)
        inside = _point_in_ring(px, py, rings[0])
        if inside:
            for hole in rings[1:]:
                if _point_in_ring(px, py, hole):
                    inside = False
                    break
        if inside:
            return 0.0
        for ring in rings:
            best = min(best, _ring_distance_m(px, py, ring))
    if best == float("inf"):
        return None
    return best / 1000.0


def load_aliases(path: Path = ALIASES_PATH) -> list[dict]:
    if not path.is_file():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for row in data.get("aliases") or []:
        phrase = normalize_name(row.get("phrase"))
        cid = (row.get("company_id") or "").strip()
        if phrase and cid:
            rows.append({"phrase": phrase, "company_id": cid, "note": row.get("note") or ""})
    return rows


def _beta_assets(payload: dict) -> list[dict]:
    assets = []
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict) or not asset.get("id"):
            continue
        lat, lon = asset.get("lat"), asset.get("lon")
        try:
            lat_f = float(lat) if lat is not None else None
            lon_f = float(lon) if lon is not None else None
        except (TypeError, ValueError):
            lat_f, lon_f = None, None
        assets.append({
            "id": str(asset["id"]),
            "name": asset.get("name") or str(asset["id"]),
            "region": asset.get("region") or "",
            "country": asset.get("country") or "",
            "nearest": asset.get("nearest") or "",
            "lat": lat_f,
            "lon": lon_f,
        })
    return assets


def _beta_tickers(payload: dict) -> list[str]:
    issuer = payload.get("issuer") or {}
    out = []
    for row in issuer.get("tickers") or []:
        if isinstance(row, dict) and row.get("symbol"):
            out.append(str(row["symbol"]))
        elif isinstance(row, str) and row.strip():
            out.append(row.strip())
    return out


def load_company_book() -> dict[str, dict]:
    """Site companies: claims catalog merged with beta profiles and publics tickers."""
    book: dict[str, dict] = {}
    catalog = json.loads((CLAIMS / "companies.json").read_text(encoding="utf-8"))
    for row in catalog.get("companies") or []:
        cid = row.get("id")
        if not cid:
            continue
        names = [row.get("holder") or "", *(row.get("names") or []), cid.replace("-", " ")]
        book[cid] = {
            "company_id": cid,
            "name": row.get("holder") or (row.get("names") or [cid])[0],
            "names": [n for n in names if n],
            "ticker": None,
            "tickers": [],
            "assets": [],
            "asset_rows": [],
        }
    publics = json.loads(PUBLICS_PATH.read_text(encoding="utf-8")) if PUBLICS_PATH.is_file() else {}
    alias_of = {k: v for k, v in (publics.get("aliases") or {}).items()}
    for row in publics.get("issuers") or []:
        cid = row.get("id")
        if not cid:
            continue
        slot = book.setdefault(cid, {
            "company_id": cid,
            "name": row.get("name") or cid,
            "names": [],
            "ticker": None,
            "tickers": [],
            "assets": [],
            "asset_rows": [],
        })
        if row.get("name"):
            slot["name"] = row["name"]
            slot["names"].append(row["name"])
        for ticker in row.get("tickers") or []:
            if ticker and ticker not in slot["tickers"]:
                slot["tickers"].append(ticker)
        if slot["tickers"] and not slot["ticker"]:
            slot["ticker"] = slot["tickers"][0]
        if row.get("alias_of"):
            alias_of[cid] = row["alias_of"]
    skip = {"mcap.json", "issuers.json", "explorers.json", "claims-publics.json"}
    for path in sorted(BETA.glob("*.json")):
        if path.name in skip:
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        cid = payload.get("id") or path.stem
        if payload.get("schema") not in (None, "qc-issuer-profile-v1") and "issuer" not in payload and "assets" not in payload:
            continue
        if "issuer" not in payload and "assets" not in payload:
            continue
        issuer = payload.get("issuer") or {}
        slot = book.setdefault(cid, {
            "company_id": cid,
            "name": issuer.get("short") or issuer.get("name") or cid,
            "names": [],
            "ticker": None,
            "tickers": [],
            "assets": [],
            "asset_rows": [],
        })
        for label in (issuer.get("name"), issuer.get("short"), cid.replace("-", " ")):
            if label:
                slot["names"].append(label)
        if issuer.get("short") or issuer.get("name"):
            slot["name"] = issuer.get("short") or issuer.get("name")
        for ticker in _beta_tickers(payload):
            if ticker not in slot["tickers"]:
                slot["tickers"].append(ticker)
        if slot["tickers"] and not slot["ticker"]:
            slot["ticker"] = slot["tickers"][0]
        slot["asset_rows"] = _beta_assets(payload)
        slot["assets"] = [a["id"] for a in slot["asset_rows"]]
    for cid, target in alias_of.items():
        if cid in book and target in book:
            if not book[cid]["assets"]:
                book[cid]["assets"] = list(book[target]["assets"])
                book[cid]["asset_rows"] = list(book[target]["asset_rows"])
            if not book[cid]["ticker"] and book[target]["ticker"]:
                book[cid]["ticker"] = book[target]["ticker"]
                book[cid]["tickers"] = list(book[target]["tickers"])
    for slot in book.values():
        norms = []
        seen = set()
        for name in slot["names"]:
            norm = normalize_name(name)
            if norm and norm not in seen:
                seen.add(norm)
                norms.append(norm)
        slot["norms"] = norms
        tokens: set[str] = set()
        for norm in norms:
            tokens |= distinctive_tokens(norm)
        slot["tokens"] = tokens
    return book


def name_indexes(book: dict[str, dict]) -> tuple[dict[str, list[str]], list[tuple[str, str]], dict[str, set[str]]]:
    exact: dict[str, list[str]] = defaultdict(list)
    phrases: list[tuple[str, str]] = []
    token_index: dict[str, set[str]] = defaultdict(set)
    for cid, slot in book.items():
        for norm in slot["norms"]:
            if cid not in exact[norm]:
                exact[norm].append(cid)
            if len(norm) >= 8 and len(norm.split()) >= 2:
                phrases.append((norm, cid))
        for tok in slot["tokens"]:
            token_index[tok].add(cid)
    phrases.sort(key=lambda item: len(item[0]), reverse=True)
    return exact, phrases, token_index


def harvest_extract_holders(claims_dir: Path = CLAIMS) -> dict[str, str]:
    """Focus holder string → company id from committed extracts. Conflicts are dropped."""
    found: dict[str, set[str]] = defaultdict(set)
    for path in sorted(claims_dir.glob("*.geojson")):
        if path.name == "overview.geojson":
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for feat in data.get("features") or []:
            props = feat.get("properties") or {}
            if props.get("role") == "neighbor":
                continue
            holder = (props.get("holder") or "").strip()
            cid = (props.get("company_id") or "").strip()
            if holder and cid:
                found[holder].add(cid)
    return {holder: next(iter(cids)) for holder, cids in found.items() if len(cids) == 1}


def first_token(norm: str) -> str:
    parts = (norm or "").split()
    return parts[0] if parts else ""


def exact_ok(norm: str) -> bool:
    """A one-word leftover such as 'gold' is not an exact company match."""
    parts = (norm or "").split()
    if len(parts) >= 2:
        return True
    if len(parts) == 1 and len(parts[0]) >= 6 and parts[0] not in STOP_TOKENS:
        return True
    return False


def phrase_prefix(phrase: str, norm: str) -> bool:
    return bool(phrase) and (norm == phrase or norm.startswith(phrase + " "))


def looks_like_person(party: str) -> bool:
    text = (party or "").strip()
    if not text or CORP_HINT_RE.search(text) or __import__("re").search(r"\d", text):
        return False
    parts = __import__("re").findall(r"[A-Za-zÀ-ÿ']+", text)
    return 2 <= len(parts) <= 4


def _company_first_tokens(slot: dict) -> set[str]:
    return {first_token(norm) for norm in slot.get("norms") or [] if norm}


def _judge_candidates(party: str, norm: str, owners: list[str], book: dict, judge, interest, add, ambiguous) -> None:
    ambiguous.extend(owners)
    if judge is None or not (1 <= len(owners) <= 2):
        return
    judged = judge(party, norm, [book[cid] for cid in owners if cid in book])
    for row in judged:
        add(row["company_id"], "jev", interest, party, {
            "jev_score": row.get("jev_score"),
            "jev_confidence": row.get("jev_confidence"),
            "jev_outcome": row.get("jev_outcome"),
        })


def match_holder(
    raw: str | None,
    book: dict[str, dict],
    aliases: list[dict],
    extract_map: dict[str, str],
    exact: dict[str, list[str]],
    phrases: list[tuple[str, str]],
    token_index: dict[str, set[str]],
    judge=None,
) -> dict:
    """Link one registry holder string. Jev runs only for a two-company tie or a short first-token near miss."""
    holder = holder_key(raw)
    links: list[dict] = []
    ambiguous: list[str] = []

    def add(company_id: str, method: str, interest: float | None, party: str, extra: dict | None = None) -> None:
        if company_id not in book and method != "alias":
            return
        if company_id not in book:
            return
        row = {
            "company_id": company_id,
            "method": method,
            "interest_pct": interest,
            "party": party,
        }
        if extra:
            row.update(extra)
        links.append(row)

    mapped = extract_map.get(holder)
    if mapped and mapped in book:
        add(mapped, "extract_holder", None, holder)

    for party, interest in parse_parties(holder) or [("(blank)", None)]:
        if interest is not None and interest < MIN_INTEREST:
            continue
        if looks_like_person(party):
            continue
        norm = normalize_name(party)
        if not norm:
            continue
        alias_ids = [row["company_id"] for row in aliases if phrase_in(row["phrase"], norm)]
        alias_ids = [cid for cid in dict.fromkeys(alias_ids) if cid in book]
        if len(alias_ids) == 1:
            add(alias_ids[0], "alias", interest, party)
            continue
        if len(alias_ids) > 1:
            _judge_candidates(party, norm, alias_ids[:2], book, judge, interest, add, ambiguous)
            continue
        if exact_ok(norm):
            exact_ids = [cid for cid in exact.get(norm, []) if cid in book]
            if len(exact_ids) == 1:
                add(exact_ids[0], "catalog_name", interest, party)
                continue
            if len(exact_ids) == 2:
                _judge_candidates(party, norm, exact_ids, book, judge, interest, add, ambiguous)
                continue
            if len(exact_ids) > 2:
                continue
        prefixed = [(phrase, cid) for phrase, cid in phrases if cid in book and phrase_prefix(phrase, norm)]
        if prefixed:
            best_len = max(len(phrase) for phrase, _cid in prefixed)
            best = list(dict.fromkeys(cid for phrase, cid in prefixed if len(phrase) == best_len))
            if len(best) == 1:
                add(best[0], "catalog_name", interest, party)
                continue
            if len(best) == 2:
                _judge_candidates(party, norm, best, book, judge, interest, add, ambiguous)
                continue
            continue
        first = first_token(norm)
        if len(first) < 6 or first in STOP_TOKENS:
            continue
        # The company's whole normalized name is this holder's first word
        # ("glencore" inside "glencore canada"). One token, one company.
        if exact_ok(first):
            whole = [cid for cid in exact.get(first, []) if cid in book]
            if len(whole) == 1:
                add(whole[0], "catalog_name", interest, party)
                continue
        owners = []
        for cid in token_index.get(first, ()):
            if cid in book and first in _company_first_tokens(book[cid]):
                owners.append(cid)
        owners = sorted(dict.fromkeys(owners))
        # A shared first token is not enough on its own (Endurance Gold vs
        # Endurance Elements, Champion Bear vs Champion Iron). Aliases cover
        # the known coined-name cases. One or two candidates go to Jev.
        if len(owners) == 1 or len(owners) == 2:
            _judge_candidates(party, norm, owners, book, judge, interest, add, ambiguous)

    dedup: dict[str, dict] = {}
    for row in links:
        prev = dedup.get(row["company_id"])
        if prev is None or METHOD_RANK.get(row["method"], 9) < METHOD_RANK.get(prev["method"], 9):
            dedup[row["company_id"]] = row
    ordered = sorted(
        dedup.values(),
        key=lambda row: (METHOD_RANK.get(row["method"], 9), -(row.get("interest_pct") or 0), row["company_id"]),
    )
    primary = ordered[0] if ordered else None
    companies = []
    for row in ordered:
        slot = book.get(row["company_id"]) or {}
        companies.append({
            "company_id": row["company_id"],
            "company": slot.get("name"),
            "ticker": slot.get("ticker"),
            "tickers": slot.get("tickers") or [],
            "assets": slot.get("assets") or [],
            "method": row["method"],
            "party": row.get("party"),
            "interest_pct": row.get("interest_pct"),
            "jev_score": row.get("jev_score"),
            "jev_confidence": row.get("jev_confidence"),
            "jev_outcome": row.get("jev_outcome"),
        })
    return {
        "holder": holder,
        "company_id": primary["company_id"] if primary else None,
        "method": primary["method"] if primary else None,
        "ambiguous": sorted(set(ambiguous)) if not ordered and ambiguous else [],
        "companies": companies,
    }


def ensure_typesafe_key() -> bool:
    if os.environ.get("TYPESAFE_API_KEY"):
        return True
    if not STORE_ENV.is_file():
        return False
    for line in STORE_ENV.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        if key.strip() == "TYPESAFE_API_KEY" and value.strip():
            os.environ["TYPESAFE_API_KEY"] = value.strip().strip('"').strip("'")
            return True
    return False


def jev_judge(party: str, norm: str, candidates: list[dict]) -> list[dict]:
    """Ask Jev whether this holder party is each candidate. Same-entity only."""
    qc = SCRIPTS / "qc_sqlite"
    if str(qc) not in sys.path:
        sys.path.insert(0, str(qc))
    import jev  # noqa: WPS433

    if not ensure_typesafe_key():
        log("  jev skipped: no API key")
        return []
    accepted = []
    shortlist = candidates[:4]
    for cand in shortlist:
        state = {
            "holder": party,
            "holder_normalized": norm,
            "catalog_issuer": {
                "id": cand["company_id"],
                "name": cand.get("name"),
                "names": (cand.get("names") or [])[:8],
                "ticker": cand.get("ticker"),
            },
            "other_candidate_ids": [c["company_id"] for c in shortlist if c["company_id"] != cand["company_id"]],
        }
        try:
            packed = jev.ask(state, jev.link_questions(), label="ontario-holder-link")
        except Exception as exc:
            log(f"  jev failed ({type(exc).__name__}) for {cand['company_id']}")
            continue
        answers = packed.get("answers") or {}
        score_row = answers.get("link_state") or {}
        score = score_row.get("score")
        conf = score_row.get("confidence") or 0
        if score is None:
            continue
        outcome = jev.round_link_outcome(float(score))
        if outcome == "same_entity" and float(conf) >= 0.55:
            accepted.append({
                "company_id": cand["company_id"],
                "jev_score": score,
                "jev_confidence": conf,
                "jev_outcome": outcome,
                "same_name_noul": (answers.get("same_name") or {}).get("noul"),
                "holder_is_vehicle_noul": (answers.get("holder_is_vehicle") or {}).get("noul"),
            })
    jev.flush()
    return accepted


def _holder_rows_from_db(con: sqlite3.Connection) -> list[dict]:
    merged: dict[str, dict] = {}
    for row in con.execute(
        """
        SELECT holder AS holder, COUNT(*) AS n,
               MIN(minx) AS minx, MIN(miny) AS miny, MAX(maxx) AS maxx, MAX(maxy) AS maxy
        FROM titles
        GROUP BY holder
        """
    ):
        key = holder_key(row["holder"])
        slot = merged.get(key)
        if slot is None:
            slot = merged[key] = {"holder": key, "count": 0, "bbox": None}
        slot["count"] += row["n"]
        if row["minx"] is not None:
            box = [row["minx"], row["miny"], row["maxx"], row["maxy"]]
            prev = slot["bbox"]
            if prev is None:
                slot["bbox"] = box
            else:
                slot["bbox"] = [min(prev[0], box[0]), min(prev[1], box[1]), max(prev[2], box[2]), max(prev[3], box[3])]
    rows = []
    for slot in merged.values():
        bbox = slot["bbox"]
        if bbox is not None:
            bbox = [round(v, 4) for v in bbox]
        rows.append({"holder": slot["holder"], "count": slot["count"], "bbox": bbox})
    rows.sort(key=lambda row: (-row["count"], row["holder"]))
    return rows


def _holder_rows_from_index(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [
        {"holder": row.get("holder") or "(blank)", "count": row.get("count") or 0, "bbox": row.get("bbox")}
        for row in data.get("holders") or []
    ]


def link_holders(rows: list[dict], use_jev: bool, harvest: bool) -> dict:
    t0 = time.time()
    book = load_company_book()
    aliases = load_aliases()
    exact, phrases, token_index = name_indexes(book)
    extract_map = harvest_extract_holders() if harvest else {}
    log(f"  companies {len(book)} aliases {len(aliases)} extract holders {len(extract_map)}")
    judge = jev_judge if use_jev else None
    linked_rows = []
    jev_calls = 0
    for i, src in enumerate(rows):
        probe = match_holder(
            src["holder"], book, aliases, extract_map, exact, phrases, token_index, judge=None,
        )
        if use_jev and probe["ambiguous"]:
            jev_calls += 1
            if jev_calls <= 5 or jev_calls % 25 == 0:
                log(f"  jev {jev_calls}: {src['holder'][:80]}")
            matched = match_holder(
                src["holder"], book, aliases, extract_map, exact, phrases, token_index, judge=judge,
            )
        else:
            matched = probe
        matched["count"] = src["count"]
        matched["bbox"] = src["bbox"]
        linked_rows.append(matched)
        if (i + 1) % 400 == 0:
            log(f"  matched {i + 1}/{len(rows)}")
    titles = sum(row["count"] for row in linked_rows)
    linked_titles = sum(row["count"] for row in linked_rows if row.get("company_id"))
    by_method: dict[str, int] = defaultdict(int)
    for row in linked_rows:
        if row.get("method"):
            by_method[row["method"]] += row["count"]
    great = [
        row for row in linked_rows
        if "great bear resources" in normalize_name(row["holder"])
    ]
    payload = {
        "dataset": "Ontario MLAS holder → site company. Not legal title.",
        "generated_at": now_iso(),
        "province": "Ontario",
        "titles": titles,
        "linked_titles": linked_titles,
        "coverage": round(linked_titles / titles, 6) if titles else 0,
        "holders": len(linked_rows),
        "linked_holders": sum(1 for row in linked_rows if row.get("company_id")),
        "by_method_titles": dict(sorted(by_method.items())),
        "jev": use_jev,
        "jev_ambiguous_holders": jev_calls if use_jev else sum(1 for row in linked_rows if row.get("ambiguous")),
        "great_bear": [
            {
                "holder": row["holder"],
                "count": row["count"],
                "company_id": row.get("company_id"),
                "method": row.get("method"),
                "ticker": (row["companies"][0]["ticker"] if row.get("companies") else None),
            }
            for row in great
        ],
        "seconds": round(time.time() - t0, 1),
        "rows": linked_rows,
    }
    log(
        f"  coverage {linked_titles}/{titles} ({payload['coverage']:.1%}) "
        f"holders {payload['linked_holders']}/{payload['holders']} "
        f"methods {payload['by_method_titles']}"
    )
    for row in great:
        log(f"  great bear: {row['holder']!r} → {row.get('company_id')} via {row.get('method')} n={row['count']}")
    return payload


def write_links(payload: dict, path: Path = LINKS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Drop bulky per-row ambiguity lists once a company was chosen.
    rows = []
    for row in payload["rows"]:
        companies = []
        for company in row.get("companies") or []:
            companies.append({
                "company_id": company["company_id"],
                "company": company.get("company"),
                "ticker": company.get("ticker"),
                "assets": company.get("assets") or [],
                "method": company.get("method"),
                "party": company.get("party"),
                "interest_pct": company.get("interest_pct"),
                "jev_score": company.get("jev_score"),
                "jev_confidence": company.get("jev_confidence"),
            })
        rows.append({
            "holder": row["holder"],
            "count": row["count"],
            "bbox": row.get("bbox"),
            "company_id": row.get("company_id"),
            "method": row.get("method"),
            "companies": companies,
            "ambiguous": row.get("ambiguous") or [],
        })
    out = {k: v for k, v in payload.items() if k != "rows"}
    out["rows"] = rows
    atomic_write_json(path, out, indent=1)
    log(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e6:.2f} MB)")


def write_search(payload: dict) -> dict:
    holders = []
    for row in payload["rows"]:
        primary = (row.get("companies") or [None])[0]
        holders.append({
            "name": row["holder"],
            "count": row["count"],
            "bbox": row.get("bbox"),
            "company_id": row.get("company_id"),
            "company": primary.get("company") if primary else None,
            "ticker": primary.get("ticker") if primary else None,
        })
    body = {
        "province": "Ontario",
        "generated_at": payload["generated_at"],
        "titles": payload["titles"],
        "linked_titles": payload["linked_titles"],
        "coverage": payload["coverage"],
        "disclaimer": "Not legal title. Ontario MLAS operational claims, generalized for viewing.",
        "holders": holders,
    }
    SEARCH_HOLDERS.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(SEARCH_HOLDERS, body, indent=1)
    index = {row["name"]: i for i, row in enumerate(holders)}
    return index


def write_title_shards(con: sqlite3.Connection, holder_index: dict[str, int]) -> dict:
    if SEARCH_TITLES.exists():
        for old in SEARCH_TITLES.glob("*.json"):
            old.unlink()
    SEARCH_TITLES.mkdir(parents=True, exist_ok=True)
    shards: dict[str, dict[str, int]] = defaultdict(dict)
    duplicates = 0
    missing = 0
    seen = set()
    for title_id, holder in con.execute("SELECT title_id, holder FROM titles"):
        key = holder_key(holder)
        idx = holder_index.get(key)
        if idx is None:
            missing += 1
            continue
        sid = shard_key(str(title_id))
        if title_id in seen:
            duplicates += 1
        seen.add(title_id)
        shards[sid][str(title_id)] = idx
    manifest_rows = []
    total_bytes = 0
    for sid in sorted(shards):
        slot = SEARCH_TITLES / f"{sid}.json"
        slot.write_text(json.dumps(shards[sid], separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
        size = slot.stat().st_size
        total_bytes += size
        manifest_rows.append({
            "id": sid,
            "file": f"claims/search/titles/{sid}.json",
            "titles": len(shards[sid]),
            "bytes": size,
        })
    manifest = {
        "province": "Ontario",
        "generated_at": now_iso(),
        "shard_by": "first two characters of the title id",
        "holder_index": "claims/search/holders.json holders array",
        "titles": sum(row["titles"] for row in manifest_rows),
        "duplicate_title_ids": duplicates,
        "missing_holder": missing,
        "bytes": total_bytes,
        "shards": manifest_rows,
    }
    atomic_write_json(SEARCH_TITLES / "index.json", manifest, indent=1)
    log(
        f"  search holders {SEARCH_HOLDERS.stat().st_size / 1e3:.0f} KB, "
        f"title shards {len(manifest_rows)} files {total_bytes / 1e6:.2f} MB"
    )
    return manifest


def _ontario_assets(book: dict[str, dict]) -> list[dict]:
    mines = []
    seen = set()
    for cid, slot in book.items():
        for asset in slot.get("asset_rows") or []:
            region = (asset.get("region") or "").lower()
            if "ontario" not in region:
                continue
            if asset.get("lat") is None or asset.get("lon") is None:
                continue
            key = (cid, asset["id"])
            if key in seen:
                continue
            seen.add(key)
            mines.append({
                "mine_id": asset["id"],
                "company_id": cid,
                "name": asset.get("name") or asset["id"],
                "nearest": asset.get("nearest") or "",
                "region": asset.get("region") or "",
                "lat": asset["lat"],
                "lon": asset["lon"],
                "ticker": slot.get("ticker"),
            })

    def sort_key(mine: dict) -> tuple:
        blob = f"{mine['mine_id']} {mine['name']} {mine['nearest']}".lower()
        return (0 if "sudbury" in blob else 1, mine["name"].lower())

    mines.sort(key=sort_key)
    return mines


def _bbox_hits(lon: float, lat: float, km: float) -> tuple[float, float, float, float]:
    dlat = km / (M_PER_DEG_LAT / 1000.0)
    dlon = km / ((M_PER_DEG_LAT * math.cos(math.radians(lat))) / 1000.0)
    return lon - dlon, lat - dlat, lon + dlon, lat + dlat


def claims_near_mine(con: sqlite3.Connection, lon: float, lat: float, km: float, holder_company: dict[str, dict]) -> dict:
    minx, miny, maxx, maxy = _bbox_hits(lon, lat, km)
    by_holder: dict[str, int] = defaultdict(int)
    titles = 0
    for row in con.execute(
        """
        SELECT holder, geom_json FROM titles
        WHERE geom_json IS NOT NULL
          AND minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ?
        """,
        (maxx, minx, maxy, miny),
    ):
        try:
            geom = json.loads(row["geom_json"])
        except json.JSONDecodeError:
            continue
        dist = geometry_distance_km(lon, lat, geom)
        if dist is None or dist > km:
            continue
        titles += 1
        by_holder[holder_key(row["holder"])] += 1
    holders = []
    by_company: dict[str, int] = defaultdict(int)
    for holder, count in sorted(by_holder.items(), key=lambda item: -item[1]):
        link = holder_company.get(holder) or {}
        cid = link.get("company_id")
        holders.append({
            "holder": holder,
            "count": count,
            "company_id": cid,
            "ticker": link.get("ticker"),
        })
        by_company[cid or UNLINKED] += count
    companies = [
        {"company_id": None if cid == UNLINKED else cid, "count": count}
        for cid, count in sorted(by_company.items(), key=lambda item: -item[1])
    ]
    return {"titles": titles, "by_holder": holders, "by_company": companies}


def write_around(con: sqlite3.Connection, payload: dict) -> dict:
    t0 = time.time()
    book = load_company_book()
    mines = _ontario_assets(book)
    holder_company = {}
    for row in payload["rows"]:
        primary = (row.get("companies") or [{}])[0]
        holder_company[row["holder"]] = {
            "company_id": row.get("company_id"),
            "ticker": primary.get("ticker"),
        }
    log(f"  ontario mines with coordinates: {len(mines)}")
    out_mines = []
    for i, mine in enumerate(mines):
        near = {}
        for km in (5, 10):
            near[str(km)] = claims_near_mine(con, mine["lon"], mine["lat"], km, holder_company)
        out_mines.append({
            "mine_id": mine["mine_id"],
            "company_id": mine["company_id"],
            "name": mine["name"],
            "ticker": mine["ticker"],
            "region": mine["region"],
            "lat": mine["lat"],
            "lon": mine["lon"],
            "km": near,
        })
        if i < 8 or (i + 1) % 10 == 0:
            k5 = near["5"]["titles"]
            k10 = near["10"]["titles"]
            log(f"  {mine['mine_id']}: {k5} within 5 km, {k10} within 10 km")
    body = {
        "dataset": "Ontario MLAS titles within 5 km and 10 km of beta mine coordinates. Grouped by holder. Not legal title. Does not modify beta JSON.",
        "generated_at": now_iso(),
        "province": "Ontario",
        "radii_km": [5, 10],
        "mines": len(out_mines),
        "sudbury_first": True,
        "seconds": round(time.time() - t0, 1),
        "results": out_mines,
    }
    AROUND_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(AROUND_PATH, body, indent=1)
    log(f"  wrote {AROUND_PATH.relative_to(ROOT)} ({AROUND_PATH.stat().st_size / 1e3:.0f} KB) in {body['seconds']}s")
    return body


def export_geojsonl(con: sqlite3.Connection, holder_link: dict[str, dict], dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with dest.open("w", encoding="utf-8") as handle:
        for row in con.execute("SELECT title_id, holder, geom_json FROM titles WHERE geom_json IS NOT NULL"):
            key = holder_key(row["holder"])
            link = holder_link.get(key) or {}
            cid = link.get("company_id") or UNLINKED
            props = {
                "id": row["title_id"],
                "holder": key if key != "(blank)" else "",
                "company": cid,
                "ticker": link.get("ticker") or "",
                "color": company_color(None if cid == UNLINKED else cid),
            }
            handle.write(
                '{"type":"Feature","geometry":'
                + row["geom_json"]
                + ',"properties":'
                + json.dumps(props, ensure_ascii=False, separators=(",", ":"))
                + "}\n"
            )
            n += 1
    log(f"  geojsonl {n} features ({dest.stat().st_size / 1e6:.1f} MB)")
    return n


def build_tiles(geojsonl: Path, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "tippecanoe",
        "-o", str(dest),
        "-l", "claims",
        "--force",
        "--read-parallel",
        "--no-feature-limit",
        "--drop-densest-as-needed",
        "--extend-zooms-if-still-dropping",
        "--minimum-zoom=4",
        "--maximum-zoom=12",
        "--full-detail=12",
        str(geojsonl),
    ]
    log("  " + " ".join(cmd))
    t0 = time.time()
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr[-4000:])
        raise SystemExit(f"tippecanoe failed ({proc.returncode})")
    elapsed = time.time() - t0
    size = dest.stat().st_size
    log(f"  pmtiles {size / 1e6:.2f} MB in {elapsed:.1f}s")
    return {"bytes": size, "seconds": round(elapsed, 1), "path": str(dest)}


def project_hosting(ontario_bytes: int, ontario_titles: int) -> dict:
    per = ontario_bytes / ontario_titles if ontario_titles else 0
    counts = dict(PROVINCE_COUNTS)
    counts["ontario"] = ontario_titles
    projected = {name: int(round(count * per)) for name, count in counts.items() if count}
    total = sum(projected.values())
    largest = max(projected.items(), key=lambda item: item[1])
    fits = {name: size <= GITHUB_FILE_LIMIT for name, size in projected.items()}
    return {
        "github_file_limit_bytes": GITHUB_FILE_LIMIT,
        "ontario_bytes": ontario_bytes,
        "ontario_titles": ontario_titles,
        "bytes_per_title": round(per, 2),
        "projected_bytes": projected,
        "projected_total_bytes": total,
        "largest_province": {"name": largest[0], "bytes": largest[1]},
        "each_province_under_github_limit": all(fits.values()),
        "ontario_under_github_limit": ontario_bytes <= GITHUB_FILE_LIMIT,
        "note": (
            "Projection scales Ontario bytes per title onto the 2026-09-24 "
            "registry counts. Yukon and Quebec polygons may be larger or smaller "
            "than an Ontario cell. British Columbia uses the 10,000-row MapServer "
            "view, not a full registry census. Quebec uses the stated 124k floor."
        ),
    }


def ingest(refresh: bool = False, limit_pages: int | None = None) -> dict:
    con = connect()
    if refresh:
        con.execute("DELETE FROM titles")
        con.commit()
    done = con.execute("SELECT value FROM meta WHERE key='done'").fetchone()
    if done and done["value"] == "1" and not refresh and not limit_pages:
        n = con.execute("SELECT COUNT(*) AS n FROM titles").fetchone()["n"]
        log(f"  ontario sqlite already complete: {n} titles")
        return {"titles": n, "cached": True}
    after_row = con.execute("SELECT MAX(objectid) AS m FROM titles").fetchone()
    after = int(after_row["m"] or 0)
    log(f"  MLAS download from OBJECTID>{after} page={PAGE} offset={OFFSET_DEG}")
    expected = live_count(ON_URL, "1=1")
    log(f"  live count {expected}")
    pages = 0
    inserted = 0
    complete = False
    t0 = time.time()
    cache = DEFAULT_CACHE_DIR
    cache.mkdir(parents=True, exist_ok=True)
    while True:
        if limit_pages is not None and pages >= limit_pages:
            break
        where = "1=1" if after <= 0 else f"OBJECTID>{after}"
        params = {
            "where": where,
            "outFields": ON_FULL_FIELDS,
            "outSR": 4326,
            "f": "geojson",
            "resultRecordCount": PAGE,
            "orderByFields": "OBJECTID ASC",
            "maxAllowableOffset": OFFSET_DEG,
        }
        data = cached_get_json(ON_URL, params, cache, refresh)
        batch = data.get("features") or []
        if not batch:
            complete = True
            break
        rows = []
        oids = []
        for feat in batch:
            props = feat.get("properties") or {}
            try:
                oid = int(props.get("OBJECTID"))
            except (TypeError, ValueError):
                continue
            oids.append(oid)
            title_id = props.get("TENURE_NUMBER_ID")
            if title_id in (None, ""):
                continue
            geom = feat.get("geometry")
            box = geometry_bbox(geom)
            rows.append((
                oid,
                str(title_id),
                props.get("HOLDER") or None,
                props.get("TENURE_STATUS_DESC"),
                props.get("TITLE_TYPE_DESC"),
                epoch_to_date(props.get("ISSUE_DATE")),
                epoch_to_date(props.get("ANNIVERSARY_DATE")),
                epoch_to_date(props.get("CLAIM_DUE_DATE")),
                epoch_to_date(props.get("EXTENSION_DATE")),
                geometry_area_ha(geom),
                box[0] if box else None,
                box[1] if box else None,
                box[2] if box else None,
                box[3] if box else None,
                json.dumps(geom, separators=(",", ":")) if geom else None,
            ))
        con.executemany(
            """
            INSERT OR REPLACE INTO titles (
              objectid, title_id, holder, status, tenure_type,
              issue_date, anniversary_date, due_date, extension_date,
              area_ha, minx, miny, maxx, maxy, geom_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        con.commit()
        inserted += len(rows)
        pages += 1
        if oids:
            after = max(oids)
        if pages % 20 == 0:
            log(f"  pages={pages} inserted={inserted} after={after}")
        if len(batch) < PAGE and not data.get("exceededTransferLimit"):
            complete = True
            break
        time.sleep(SLEEP_S)
    total = con.execute("SELECT COUNT(*) AS n FROM titles").fetchone()["n"]
    holders = con.execute("SELECT COUNT(DISTINCT holder) AS n FROM titles").fetchone()["n"]
    finished = complete and limit_pages is None
    con.execute(
        "INSERT INTO meta(key, value) VALUES('done', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        ("1" if finished else "0",),
    )
    con.execute(
        "INSERT INTO meta(key, value) VALUES('ingested_at', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (now_iso(),),
    )
    con.execute(
        "INSERT INTO meta(key, value) VALUES('live_count', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(expected),),
    )
    con.commit()
    elapsed = round(time.time() - t0, 1)
    log(f"  sqlite {total} titles, {holders} holders, db {DB_PATH.stat().st_size / 1e6:.1f} MB in {elapsed}s")
    if finished and total + 50 < expected:
        raise SystemExit(f"ingest short: sqlite {total} live {expected}")
    return {"titles": total, "holders": holders, "live_count": expected, "seconds": elapsed, "pages": pages}


def cmd_link(args: argparse.Namespace) -> dict:
    if args.from_holders:
        rows = _holder_rows_from_index(Path(args.from_holders))
    else:
        con = connect()
        rows = _holder_rows_from_db(con)
        con.close()
    if not rows:
        raise SystemExit("no holders. Run ingest first.")
    payload = link_holders(rows, use_jev=not args.no_jev, harvest=not args.no_extracts)
    write_links(payload)
    return payload


def cmd_search(args: argparse.Namespace) -> None:
    payload = json.loads(LINKS_PATH.read_text(encoding="utf-8"))
    payload["rows"] = payload.get("rows") or []
    index = write_search(payload)
    con = connect()
    try:
        write_title_shards(con, index)
    finally:
        con.close()


def cmd_around(_args: argparse.Namespace) -> None:
    payload = json.loads(LINKS_PATH.read_text(encoding="utf-8"))
    payload["rows"] = payload.get("rows") or []
    con = connect()
    try:
        write_around(con, payload)
    finally:
        con.close()


def cmd_tiles(_args: argparse.Namespace) -> dict:
    payload = json.loads(LINKS_PATH.read_text(encoding="utf-8"))
    holder_link = {}
    for row in payload.get("rows") or []:
        primary = (row.get("companies") or [{}])[0]
        holder_link[row["holder"]] = {
            "company_id": row.get("company_id"),
            "ticker": primary.get("ticker"),
        }
    geojsonl = BUILD_DIR / "ontario.jsonl"
    dest = BUILD_DIR / "ontario.pmtiles"
    con = connect()
    try:
        export_geojsonl(con, holder_link, geojsonl)
    finally:
        con.close()
    stats = build_tiles(geojsonl, dest)
    geojsonl.unlink(missing_ok=True)
    return stats


def dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def cmd_report(tiles_stats: dict | None = None) -> dict:
    links = json.loads(LINKS_PATH.read_text(encoding="utf-8")) if LINKS_PATH.is_file() else {}
    search_bytes = SEARCH_HOLDERS.stat().st_size if SEARCH_HOLDERS.is_file() else 0
    shard_bytes = dir_bytes(SEARCH_TITLES)
    around_bytes = AROUND_PATH.stat().st_size if AROUND_PATH.is_file() else 0
    links_bytes = LINKS_PATH.stat().st_size if LINKS_PATH.is_file() else 0
    db_bytes = dir_bytes(DB_PATH.parent)
    pmtiles = BUILD_DIR / "ontario.pmtiles"
    pm_bytes = pmtiles.stat().st_size if pmtiles.is_file() else (tiles_stats or {}).get("bytes") or 0
    titles = links.get("titles") or 0
    hosting = project_hosting(pm_bytes, titles) if pm_bytes and titles else {}
    report = {
        "generated_at": now_iso(),
        "titles": titles,
        "linked_titles": links.get("linked_titles"),
        "coverage": links.get("coverage"),
        "linked_holders": links.get("linked_holders"),
        "holders": links.get("holders"),
        "by_method_titles": links.get("by_method_titles"),
        "great_bear": links.get("great_bear"),
        "sizes": {
            "sqlite_bytes": db_bytes,
            "pmtiles_bytes": pm_bytes,
            "links_bytes": links_bytes,
            "search_holders_bytes": search_bytes,
            "search_titles_bytes": shard_bytes,
            "around_bytes": around_bytes,
            "published_json_bytes": links_bytes + search_bytes + shard_bytes + around_bytes,
        },
        "hosting": hosting,
        "pmtiles": str(pmtiles),
        "sqlite": str(DB_PATH),
    }
    atomic_write_json(REPORT_PATH, report, indent=1)
    log(f"  report {REPORT_PATH}")
    log(json.dumps({"coverage": report["coverage"], "sizes": report["sizes"], "hosting_fits": hosting.get("each_province_under_github_limit")}, indent=2))
    return report


def score_prototype(report: dict) -> dict:
    """One Jev score of the finished Ontario prototype. No key in the output."""
    qc = SCRIPTS / "qc_sqlite"
    if str(qc) not in sys.path:
        sys.path.insert(0, str(qc))
    import jev  # noqa: WPS433
    from typesafe_sdk import Score

    if not ensure_typesafe_key():
        raise SystemExit("TYPESAFE_API_KEY missing; cannot score the prototype")
    sizes = report.get("sizes") or {}
    hosting = report.get("hosting") or {}
    state = {
        "province": "Ontario",
        "titles": report.get("titles"),
        "coverage": report.get("coverage"),
        "linked_titles": report.get("linked_titles"),
        "great_bear": report.get("great_bear"),
        "sqlite_mb": round((sizes.get("sqlite_bytes") or 0) / 1e6, 1),
        "pmtiles_mb": round((sizes.get("pmtiles_bytes") or 0) / 1e6, 1),
        "published_json_mb": round((sizes.get("published_json_bytes") or 0) / 1e6, 2),
        "ontario_under_100mb": hosting.get("ontario_under_github_limit"),
        "projected_canada_mb": round((hosting.get("projected_total_bytes") or 0) / 1e6, 1),
        "design": (
            "Full MLAS ingest in gitignored SQLite on the desktop. "
            "Deterministic holder links plus Jev only for ambiguous ties. "
            "Site publishes a holder index, sharded title lookup, mine-radius JSON, "
            "and a draft MapLibre page. PMTiles stay out of git."
        ),
    }
    questions = {
        "prototype": Score(
            instructions=(
                "Score this Ontario-only prototype as the base for a Canada-wide "
                "claims database on a static GitHub Pages site. The full geometry "
                "database stays on a desktop. The site receives small JSON plus, "
                "separately, a hosted PMTiles file."
            ),
            criteria=[
                "Do not build the Canada-wide database this way.",
                "Usable Ontario slice, but the published files or the hosting choice still block a Canada rollout.",
                "Sound base for Canada: local ingest, company links, small site files, and a clear place to host the tiles.",
            ],
        )
    }
    packed = jev.ask(state, questions, label="ontario-claims-prototype")
    jev.flush()
    answer = (packed.get("answers") or {}).get("prototype") or {}
    out = {
        "generated_at": now_iso(),
        "model": packed.get("model"),
        "score": answer.get("score"),
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities"),
        "input_tokens": packed.get("input_tokens"),
        "output_tokens": packed.get("output_tokens"),
        "state": state,
    }
    atomic_write_json(SCORE_PATH, out, indent=1)
    log(f"  jev prototype score {out['score']} confidence {out['confidence']} model {out['model']}")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ontario claims database prototype")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_ing = sub.add_parser("ingest", help="Download all Ontario MLAS titles into claims/.db/")
    p_ing.add_argument("--refresh", action="store_true")
    p_ing.add_argument("--limit-pages", type=int, default=None)
    p_ing.set_defaults(func=lambda args: ingest(args.refresh, args.limit_pages))

    p_link = sub.add_parser("link", help="Write claims/links/holders.json")
    p_link.add_argument("--no-jev", action="store_true")
    p_link.add_argument("--no-extracts", action="store_true", help="Skip scanning committed GeoJSON for holder aliases")
    p_link.add_argument("--from-holders", default=None, help="Holder index JSON instead of the sqlite database")
    p_link.set_defaults(func=cmd_link)

    p_search = sub.add_parser("search", help="Write the holder index and title-id shards")
    p_search.set_defaults(func=cmd_search)

    p_around = sub.add_parser("around", help="Write claims/around/ontario-mines.json")
    p_around.set_defaults(func=cmd_around)

    p_tiles = sub.add_parser("tiles", help="Build gitignored claims/.build/ontario.pmtiles")
    p_tiles.set_defaults(func=cmd_tiles)

    p_report = sub.add_parser("report", help="Write claims/links/build-report.json")
    p_report.set_defaults(func=lambda args: cmd_report())

    p_score = sub.add_parser("score", help="Ask Jev to score the finished prototype")
    p_score.set_defaults(func=lambda args: score_prototype(json.loads(REPORT_PATH.read_text(encoding="utf-8"))))

    p_all = sub.add_parser("publish", help="link + search + around + tiles + report")
    p_all.add_argument("--no-jev", action="store_true")
    p_all.set_defaults(func=None)

    args = parser.parse_args(argv)
    if args.cmd == "publish":
        con = connect()
        try:
            holder_rows = _holder_rows_from_db(con)
        finally:
            con.close()
        payload = link_holders(holder_rows, use_jev=not args.no_jev, harvest=True)
        write_links(payload)
        index = write_search(payload)
        con = connect()
        try:
            write_title_shards(con, index)
            write_around(con, payload)
        finally:
            con.close()
        stats = cmd_tiles(args)
        cmd_report(stats)
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
