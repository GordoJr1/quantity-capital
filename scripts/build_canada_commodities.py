#!/usr/bin/env python3
"""Build static Canadian commodity production + Map 900A mine roster JSON.

National (and optional provincial) aggregates only. Mine-level tonnes are
not published and are never invented.

Sources (no secrets):
  - StatCan table 16-10-0022 quantity CSV (2019–present)
  - NRCan annual mineral production HTML (FileT=YYYY; fills earlier years)
  - Map 900A principal operations (layers 3–6)

Monthly refresh (not a morning/evening bat):

    python3 scripts/build_canada_commodities.py
    python3 scripts/build_canada_commodities.py --sqlite /path/to/qc.sqlite
    python3 scripts/build_canada_commodities.py --jev          # optional TypeSafe
    python3 scripts/build_canada_commodities.py --offline      # fixtures only
    python3 scripts/build_canada_commodities.py --check

Jev/TypeSafe is optional and one-shot (cached). CI and --offline use the
deterministic linker. Key sources if present: TYPESAFE_API_KEY,
~/.grok/typesafe.env, C:\\Users\\gordo\\.grok\\typesafe.env, or
source /home/box/shared/typesafe/env. Never commit the key.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sqlite3
import sys
import time
import unicodedata
import urllib.error
import urllib.request
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures" / "canada"
OUT = ROOT / "canada" / "commodities.json"

UA = "Quantity Capital gordojr@proton.me"
STATCAN_ZIP = "https://www150.statcan.gc.ca/n1/tbl/csv/16100022-eng.zip"
NRCAN_YEAR = "https://mmsd.nrcan-rncan.gc.ca/prod-prod/ann-ann-eng.aspx?FileT={year}&Lang=en"
MAP_QUERY = (
    "https://maps-cartes.services.geo.ca/server_serveur/rest/services/NRCan/"
    "900A_and_top_100_en/MapServer/{layer}/query"
    "?where=1%3D1&outFields=operation_name_en,operator_owners_en,province_en,"
    "city_en,product_en_spelt,product_group_en_spelt,latitude,longitude,"
    "website,OBJECTID&returnGeometry=false&f=json&resultRecordCount=1000"
)
MAP_LAYERS = {3: "metals", 4: "nonmetals", 5: "coal", 6: "oil-sands"}

SCHEMA = "qc-canada-commodities-v1"
SLEEP_S = 0.15
REQUESTED_YEARS = 20
FALLBACK_YEARS = 10

STOP = {
    "INC", "INCORPORATED", "CORP", "CORPORATION", "LTD", "LIMITED", "LLC",
    "LLP", "LP", "PLC", "CO", "COMPANY", "THE", "AND", "OF", "MINES", "MINE",
    "MINING", "ULC", "SA", "NV", "RESOURCES", "RESOURCE", "GOLD", "LTEE",
    "LIMITEE", "SOCIETE", "MINIERE", "CANADA", "CANADIAN", "QUEBEC",
    "ONTARIO", "HOLDINGS", "GROUP", "INTERNATIONAL", "MINERALS", "LIMITED",
    "PRODUCTS", "MATERIALS", "SERVICES", "LIME", "GYPSUM",
}
OWNER_ALIASES = HERE / "canada-owner-aliases.json"
JEV_JUDGMENTS = HERE / "canada-commodities-jev-judgments.json"
MINE_STRIP = re.compile(
    r"\b(complex|mine|mines|project|operation|pit|site|mill|deposit|camp)\b",
    re.I,
)
FOOTNOTE = re.compile(r"\s*\d+\s*$")
LEGAL = re.compile(
    r"\b(inc\.?|incorporated|ltd\.?|limited|llc|l\.l\.c\.?|corp\.?|corporation|"
    r"plc|s\.a\.?|n\.v\.?|co\.?|company|ulc)\b",
    re.I,
)
UNIT_RE = re.compile(
    r"^(kilograms?|kg|metric tonnes?|tonnes?|tons?|carats?|grams?|g|kt)$",
    re.I,
)
SKIP_CATEGORY = re.compile(
    r"quantity shipped|value of shipments|closing inventor|\$000|thousands of dollars",
    re.I,
)
SKIP_PRODUCT = re.compile(
    r"thousands of dollars|total metals|total non-metals|total aggregates",
    re.I,
)
PROVINCES = {
    "newfoundland and labrador": "NL",
    "prince edward island": "PE",
    "nova scotia": "NS",
    "new brunswick": "NB",
    "quebec": "QC",
    "ontario": "ON",
    "manitoba": "MB",
    "saskatchewan": "SK",
    "alberta": "AB",
    "british columbia": "BC",
    "yukon": "YT",
    "northwest territories": "NT",
    "nunavut": "NU",
}

# Owner fragments that are the same listed issuer as a claims/beta id.
BUILTIN_ALIASES = {
    "AGNICO EAGLE": "agnico-eagle",
    "AGNICO EAGLE MINES": "agnico-eagle",
    "EQUINOX GOLD": "equinox-gold",
    "NEWMONT": "newmont",
    "BARRICK": "barrick",
    "BARRICK GOLD": "barrick",
    "BARRICK MINING": "barrick",
    "IAMGOLD": "iamgold",
    "ALAMOS": "alamos-gold",
    "ALAMOS GOLD": "alamos-gold",
    "KINROSS": "kinross",
    "KINROSS GOLD": "kinross",
    "GLENCORE": "glencore",
    "GLENCORE CANADA": "glencore",
    "RIO TINTO": "rio-tinto",
    "CHAMPION IRON": "champion-iron",
    "TECK": "teck-resources",
    "TECK RESOURCES": "teck-resources",
    "ELDORADO": "eldorado-gold",
    "ELDORADO GOLD": "eldorado-gold",
    "CENTERRA": "centerra-gold",
    "CENTERRA GOLD": "centerra-gold",
    "ARTEMIS GOLD": "artemis-gold",
    "WESDOME": "wesdome-gold-mines",
    "WESDOME GOLD": "wesdome-gold-mines",
    "NEW GOLD": "new-gold",
    "SSR MINING": "ssr-mining",
    "PAN AMERICAN": "pan-american-silver",
    "PAN AMERICAN SILVER": "pan-american-silver",
    "YAMANA": "agnico-eagle",
    "YAMANA GOLD": "agnico-eagle",
}

COMMODITY_ALIASES = {
    "gold": "gold",
    "gold recoverable": "gold",
    "silver": "silver",
    "silver recoverable": "silver",
    "copper": "copper",
    "copper recoverable": "copper",
    "nickel": "nickel",
    "nickel recoverable": "nickel",
    "cobalt": "cobalt",
    "zinc": "zinc",
    "lead": "lead",
    "molybdenum": "molybdenum",
    "uranium": "uranium",
    "lithium": "lithium",
    "iron": "iron-ore",
    "iron concentrates": "iron-ore",
    "iron ore": "iron-ore",
    "iron agglomerates": "iron-agglomerates",
    "platinum group": "platinum-group",
    "platinum group metals": "platinum-group",
    "diamonds": "diamonds",
    "potash": "potash",
    "potash potassium chloride": "potash",
    "potassium chloride": "potash",
    "coal": "coal",
    "coal metallurgical": "coal",
    "coal thermal": "coal",
    "oil sands": "oil-sands",
    "upgraded crude oil": "oil-sands",
    "graphite": "graphite",
    "salt": "salt",
    "gypsum": "gypsum",
    "limestone": "limestone",
    "niobium": "niobium",
    "niobium columbium": "niobium",
}

KIND_HINT = {
    "gold": "metal", "silver": "metal", "copper": "metal", "nickel": "metal",
    "cobalt": "metal", "zinc": "metal", "lead": "metal", "molybdenum": "metal",
    "uranium": "metal", "lithium": "metal", "iron-ore": "metal",
    "iron-agglomerates": "metal", "platinum-group": "metal", "niobium": "metal",
    "diamonds": "nonmetal", "potash": "nonmetal", "graphite": "nonmetal",
    "salt": "nonmetal", "gypsum": "nonmetal", "limestone": "nonmetal",
    "coal": "coal", "oil-sands": "oil-sands",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper()
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def tokens(s: str) -> list[str]:
    return [t for t in fold(s).split() if t and t not in STOP and not t.isdigit()]


def core_key(s: str) -> str:
    toks = tokens(s)
    return " ".join(toks) if toks else fold(s)


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unknown"


def parse_number(raw: str) -> tuple[float | None, str | None]:
    if raw is None:
        return None, None
    text = unicodedata.normalize("NFKD", str(raw))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace(",", "").replace("\xa0", " ").strip()
    status = None
    if re.search(r"\bp\b", text, re.I) or "ᵖ" in str(raw):
        status = "p"
    low = text.lower()
    if low in {"", "..", "...", "-", "—", "x", "f", "na", "n/a"}:
        if low == "x":
            return None, "x"
        return None, status
    text = re.sub(r"[^0-9.+-]", "", text)
    if text in {"", "+", "-", ".", "+.", "-."}:
        return None, status or ("x" if "x" in low else None)
    try:
        return float(text), status
    except ValueError:
        return None, status


def commodity_key(name: str) -> str:
    """Fold a product label without company-name stoplist (keeps gold/iron)."""
    cleaned = FOOTNOTE.sub("", name or "")
    cleaned = re.sub(r"\([^)]*\)", " ", cleaned)
    cleaned = fold(cleaned).lower()
    cleaned = re.sub(r"\b(metric|tonnes?|tons?|kilograms?|kg|grams?|carats?|dollars?)\b", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def commodity_id(name: str) -> str:
    key = commodity_key(name)
    if key in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[key]
    # "gold recoverable" / "copper concentrates"
    if key.endswith(" recoverable"):
        base = key[: -len(" recoverable")]
        if base in COMMODITY_ALIASES:
            return COMMODITY_ALIASES[base]
    if key.endswith(" concentrates"):
        base = key[: -len(" concentrates")]
        if base in COMMODITY_ALIASES:
            return COMMODITY_ALIASES[base] + "-concentrates"
    parts = key.split()
    if parts and parts[0] in COMMODITY_ALIASES:
        return COMMODITY_ALIASES[parts[0]]
    return slugify(key or name)


def display_name(cid: str, raw: str) -> str:
    names = {
        "gold": "Gold",
        "silver": "Silver",
        "copper": "Copper",
        "nickel": "Nickel",
        "cobalt": "Cobalt",
        "zinc": "Zinc",
        "lead": "Lead",
        "molybdenum": "Molybdenum",
        "uranium": "Uranium",
        "lithium": "Lithium",
        "iron-ore": "Iron ore",
        "iron-agglomerates": "Iron agglomerates",
        "platinum-group": "Platinum group",
        "diamonds": "Diamonds",
        "potash": "Potash",
        "coal": "Coal",
        "oil-sands": "Oil sands",
        "graphite": "Graphite",
        "salt": "Salt",
        "gypsum": "Gypsum",
        "limestone": "Limestone",
        "niobium": "Niobium",
        "all": "All operations",
        "other": "Other",
    }
    if cid in names:
        return names[cid]
    cleaned = FOOTNOTE.sub("", raw or "")
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned)
    cleaned = re.sub(r",\s*recoverable.*$", "", cleaned, flags=re.I)
    return cleaned.strip() or cid.replace("-", " ").title()


def unit_norm(uom: str) -> tuple[str, str]:
    t = (uom or "").strip().lower()
    if t.startswith("kilogram"):
        return "kg", "kilograms"
    if "carat" in t:
        return "ct", "carats"
    if t in {"gram", "grams", "g"}:
        return "g", "grams"
    if "tonne" in t or t in {"tonnes", "tons", "t"}:
        return "t", "tonnes"
    return (t or "t"), (uom or "tonnes")


def product_tokens(products: str) -> list[str]:
    parts = re.split(r"[,;/]| and ", products or "", flags=re.I)
    out: list[str] = []
    seen: set[str] = set()
    for part in parts:
        cid = commodity_id(part.strip())
        if cid and cid not in seen and cid != "unknown":
            seen.add(cid)
            out.append(cid)
    return out


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch(url: str, timeout: int = 90) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "*/*"},
    )
    last: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            code = getattr(exc, "code", None)
            if code in {403, 404} and attempt == 0:
                time.sleep(0.4)
                continue
            if code in {429, 503} or isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
                time.sleep(0.4 * (2 ** attempt))
                continue
            raise
    raise last  # type: ignore[misc]


# ---------------------------------------------------------------------------
# StatCan
# ---------------------------------------------------------------------------

def parse_statcan_csv(text: str) -> list[dict[str, Any]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    out: list[dict[str, Any]] = []
    for row in rows:
        product = (row.get("Products") or "").strip()
        variable = (row.get("Variables") or "").strip()
        geo = (row.get("GEO") or "").strip()
        if variable != "Quantity produced":
            continue
        if SKIP_PRODUCT.search(product):
            continue
        if "dollar" in (row.get("UOM") or "").lower() or "dollar" in product.lower():
            continue
        value, status = parse_number(row.get("VALUE") or "")
        if value is None and not status:
            status = (row.get("STATUS") or "").strip() or None
        out.append(
            {
                "year": int(row["REF_DATE"]),
                "geo": geo,
                "product": product,
                "commodity_id": commodity_id(product),
                "unit": row.get("UOM") or "",
                "value": value,
                "status": status or (row.get("STATUS") or "").strip() or None,
                "source": "statcan",
            }
        )
    return out


def fetch_statcan() -> list[dict[str, Any]]:
    blob = fetch(STATCAN_ZIP)
    zf = zipfile.ZipFile(io.BytesIO(blob))
    name = next(n for n in zf.namelist() if n.endswith(".csv") and "Meta" not in n)
    text = zf.read(name).decode("utf-8-sig")
    return parse_statcan_csv(text)


# ---------------------------------------------------------------------------
# NRCan HTML tables
# ---------------------------------------------------------------------------

class _TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []
        elif tag == "br" and self._cell is not None:
            self._cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_nrcan_html(html: str, year: int) -> list[dict[str, Any]]:
    parser = _TableParser()
    parser.feed(html)
    rows_out: list[dict[str, Any]] = []
    for table in parser.tables:
        if not table:
            continue
        header = [fold(c).title() for c in table[0]]
        header_l = [c.lower() for c in table[0]]
        canada_idx = None
        for i, cell in enumerate(header_l):
            if cell.strip() == "canada":
                canada_idx = i
        geo_idx: dict[int, str] = {}
        for i, cell in enumerate(header_l):
            key = cell.strip()
            if key in PROVINCES:
                geo_idx[i] = cell.strip().title() if cell[:1].isupper() else key.title()
                # keep original-ish
                geo_idx[i] = {
                    "newfoundland and labrador": "Newfoundland and Labrador",
                    "prince edward island": "Prince Edward Island",
                    "nova scotia": "Nova Scotia",
                    "new brunswick": "New Brunswick",
                    "quebec": "Quebec",
                    "ontario": "Ontario",
                    "manitoba": "Manitoba",
                    "saskatchewan": "Saskatchewan",
                    "alberta": "Alberta",
                    "british columbia": "British Columbia",
                    "yukon": "Yukon",
                    "northwest territories": "Northwest Territories",
                    "nunavut": "Nunavut",
                }[key]
        commodity = ""
        for raw in table[1:]:
            cells = list(raw)
            if not cells:
                continue
            # Current commodity from a leading name cell (not a unit/category).
            first = cells[0]
            if first and not UNIT_RE.match(first) and not SKIP_CATEGORY.search(first) and first.lower() not in {
                "units", "commodity", "category",
            }:
                if not re.match(r"^[\d,x.]+", first, re.I):
                    commodity = FOOTNOTE.sub("", first).strip()
            category = ""
            unit = ""
            for cell in cells[1:4]:
                if SKIP_CATEGORY.search(cell) and "quantity produced" not in cell.lower():
                    if re.search(r"quantity shipped|value of shipments", cell, re.I):
                        category = cell
                if cell.lower() == "quantity produced":
                    category = "Quantity produced"
                if UNIT_RE.match(cell) or cell.lower() in {
                    "metric tonnes", "kilograms", "grams", "carats", "tonnes",
                }:
                    unit = cell
            joined = " ".join(cells).lower()
            if SKIP_CATEGORY.search(joined) and "quantity produced" not in joined:
                continue
            if "$000" in joined or "thousands of dollars" in joined:
                continue
            if not commodity:
                continue
            if not unit:
                for cell in cells:
                    if UNIT_RE.match(cell) or cell.lower() in {"metric tonnes", "kilograms", "grams", "carats"}:
                        unit = cell
                        break
            if not unit:
                continue
            if canada_idx is not None and canada_idx < len(cells):
                canada_raw = cells[canada_idx]
            else:
                canada_raw = cells[-1]
            canada, status = parse_number(canada_raw)
            provinces: dict[str, float] = {}
            for i, name in geo_idx.items():
                if i < len(cells):
                    val, _st = parse_number(cells[i])
                    if val is not None:
                        provinces[name] = val
            rows_out.append(
                {
                    "year": year,
                    "geo": "Canada",
                    "product": commodity,
                    "commodity_id": commodity_id(commodity),
                    "unit": unit,
                    "value": canada,
                    "status": status,
                    "provinces": provinces,
                    "source": "nrcan",
                }
            )
    return rows_out


def fetch_nrcan_year(year: int) -> list[dict[str, Any]]:
    html = fetch(NRCAN_YEAR.format(year=year)).decode("utf-8", "replace")
    return parse_nrcan_html(html, year)


# ---------------------------------------------------------------------------
# Map 900A
# ---------------------------------------------------------------------------

def parse_map_layer(payload: dict[str, Any], layer_id: int) -> list[dict[str, Any]]:
    layer = MAP_LAYERS[layer_id]
    mines: list[dict[str, Any]] = []
    for feat in payload.get("features") or []:
        a = feat.get("attributes") or feat
        name = (a.get("operation_name_en") or "").strip()
        if not name:
            continue
        products = (a.get("product_en_spelt") or "").strip()
        mines.append(
            {
                "objectid": a.get("OBJECTID"),
                "name": name,
                "owners": (a.get("operator_owners_en") or "").replace("\xa0", " ").strip(),
                "province": (a.get("province_en") or "").strip(),
                "city": (a.get("city_en") or "").strip(),
                "products_raw": products,
                "product_ids": product_tokens(products),
                "group": (a.get("product_group_en_spelt") or "").strip(),
                "lat": a.get("latitude"),
                "lon": a.get("longitude"),
                "website": (a.get("website") or "").strip() or None,
                "layer": layer,
            }
        )
    return mines


def fetch_map900a() -> list[dict[str, Any]]:
    mines: list[dict[str, Any]] = []
    for layer_id in MAP_LAYERS:
        payload = json.loads(fetch(MAP_QUERY.format(layer=layer_id)))
        mines.extend(parse_map_layer(payload, layer_id))
        time.sleep(SLEEP_S)
    return mines


def load_map_fixture(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    mines: list[dict[str, Any]] = []
    for key, feats in raw.items():
        layer_id = int(key)
        mines.extend(parse_map_layer({"features": feats}, layer_id))
    return mines


# ---------------------------------------------------------------------------
# Company catalog + linker
# ---------------------------------------------------------------------------

def mine_keys(name: str) -> set[str]:
    keys = {fold(name), core_key(name), slugify(name)}
    stripped = MINE_STRIP.sub(" ", name or "")
    keys.update({fold(stripped), core_key(stripped), slugify(stripped)})
    return {k for k in keys if k}


def split_owners(raw: str) -> list[str]:
    parts = re.split(r"\s*/\s*|\s*;\s*|\s+\+\s+", raw or "")
    return [p.strip() for p in parts if p.strip()]


def load_company_catalog(
    root: Path,
    sqlite_path: Path | None = None,
) -> dict[str, dict[str, Any]]:
    catalog: dict[str, dict[str, Any]] = {}

    def ensure(cid: str, name: str | None = None, *, in_claims: bool = False) -> dict[str, Any]:
        rec = catalog.setdefault(
            cid,
            {"id": cid, "names": set(), "mines": [], "in_claims": False},
        )
        if in_claims:
            rec["in_claims"] = True
        if name:
            rec["names"].add(name)
        return rec

    claims = root / "claims" / "companies.json"
    if claims.exists():
        data = json.loads(claims.read_text(encoding="utf-8-sig"))
        for row in data.get("companies") or []:
            cid = row.get("id")
            if not cid:
                continue
            rec = ensure(cid, row.get("holder"), in_claims=True)
            rec["names"].update(row.get("names") or [])
            rec["names"].add(cid.replace("-", " "))
            for mine in row.get("mines") or []:
                if mine.get("name"):
                    rec["mines"].append(
                        {"id": mine.get("id") or slugify(mine["name"]), "name": mine["name"]}
                    )

    for rel in (
        "beta/issuers.json",
        "beta/explorers.json",
        "beta/claims-publics.json",
        "beta/mcap.json",
    ):
        path = root / rel
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        for row in data.get("issuers") or []:
            cid = row.get("id") or row.get("beta_id")
            if not cid:
                continue
            rec = ensure(cid, row.get("name"), in_claims=cid in catalog and catalog[cid]["in_claims"])
            rec["names"].update(row.get("names") or [])
            if row.get("alias_of"):
                rec["alias_of"] = row["alias_of"]

    if sqlite_path and sqlite_path.exists():
        con = sqlite3.connect(str(sqlite_path))
        try:
            for cid, name, holder, names_json in con.execute(
                "SELECT company_id, name, holder, names_json FROM companies"
            ):
                rec = ensure(cid, name, in_claims=cid in catalog and catalog[cid]["in_claims"])
                if holder:
                    rec["names"].add(holder)
                try:
                    rec["names"].update(json.loads(names_json or "[]"))
                except json.JSONDecodeError:
                    pass
            try:
                for cid, name in con.execute("SELECT company_id, name FROM company_names"):
                    if cid and name:
                        ensure(cid, name)
            except sqlite3.OperationalError:
                pass
        finally:
            con.close()

    for rec in catalog.values():
        rec["names"] = sorted({n for n in rec["names"] if n})
    return catalog


def build_indexes(catalog: dict[str, dict[str, Any]]) -> tuple[dict[str, str], dict[str, tuple[str, str]]]:
    name_idx: dict[str, str] = load_owner_aliases()
    mine_idx: dict[str, tuple[str, str]] = {}
    claims_ids = {cid for cid, rec in catalog.items() if rec.get("in_claims")}
    for cid, rec in catalog.items():
        target = rec.get("alias_of") or cid
        if rec.get("alias_of") and rec["alias_of"] in catalog:
            target = rec["alias_of"]
        if rec.get("in_claims") is False:
            # Beta/mcap-only names must not mint a claims.html?company= href.
            continue
        for name in rec["names"] + [cid.replace("-", " ")]:
            name_idx.setdefault(fold(name), target)
            ck = core_key(name)
            if ck:
                name_idx.setdefault(ck, target)
        for mine in rec["mines"]:
            for key in mine_keys(mine["name"]):
                mine_idx.setdefault(key, (target, mine["id"]))
    return name_idx, mine_idx


def load_owner_aliases() -> dict[str, str]:
    """Deterministic extras + one-shot Jev judgments. Never requires a key."""
    out = dict(BUILTIN_ALIASES)
    if OWNER_ALIASES.exists():
        try:
            payload = json.loads(OWNER_ALIASES.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            payload = {}
        for row in payload.get("aliases") or []:
            key = fold(row.get("owner") or "") or core_key(row.get("owner") or "")
            cid = (row.get("company_id") or "").strip()
            if key and cid:
                out[key] = cid
                ck = core_key(row.get("owner") or "")
                if ck:
                    out[ck] = cid
    if JEV_JUDGMENTS.exists():
        try:
            judged = json.loads(JEV_JUDGMENTS.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            judged = {}
        if judged.get("ran"):
            for call in judged.get("calls") or []:
                if not str(call.get("id") or "").startswith("owner:"):
                    continue
                ans = (call.get("answers") or {}).get("link_state") or {}
                score = ans.get("score")
                if score is None:
                    continue
                # Same bands as scripts/qc_sqlite/jev.py LINK_OUTCOME (no import).
                band = min(int(float(score) + 0.5), 2)
                outcome = ("leave_unlinked", "curator", "same_entity")[band]
                state = call.get("state") or {}
                cid = state.get("catalog_id")
                owner = state.get("owner")
                if outcome == "same_entity" and cid and owner and state.get("in_claims"):
                    out[fold(owner)] = cid
                    ck = core_key(owner)
                    if ck:
                        out[ck] = cid
    return out


def match_owner(owner: str, name_idx: dict[str, str], catalog: dict[str, dict[str, Any]]) -> tuple[str | None, str]:
    """Exact / core-key only. No unique-token guesses (those invented claims ids)."""
    folded = fold(owner)
    ck = core_key(owner)
    if folded in name_idx:
        return name_idx[folded], "owner-exact"
    if ck and ck in name_idx:
        return name_idx[ck], "owner-core"
    return None, ""


def match_mine(name: str, mine_idx: dict[str, tuple[str, str]]) -> tuple[str | None, str | None, str]:
    for key in mine_keys(name):
        if key in mine_idx:
            cid, mid = mine_idx[key]
            return cid, mid, "mine-name"
    return None, None, ""


def maybe_jev_link(owner: str, candidates: list[dict[str, str]]) -> str | None:
    """Optional TypeSafe call. Returns a company id or None. Never required."""
    if not candidates or len(candidates) > 4:
        return None
    try:
        sqlite_dir = HERE / "qc_sqlite"
        if str(sqlite_dir) not in sys.path:
            sys.path.insert(0, str(sqlite_dir))
        import jev  # type: ignore

        if not jev.key_present():
            return None
        from typesafe_sdk import Score

        questions = {
            "link_state": Score(
                instructions="How do the Map 900A owner and the catalog issuer relate as companies?",
                criteria=jev.LINK_LEVELS,
            )
        }
        # Score the best-named candidate only — one call, token-cheap.
        cand = candidates[0]
        packed = jev.ask(
            {
                "owner": owner,
                "catalog_id": cand["id"],
                "catalog_name": cand["name"],
            },
            questions,
            label=f"canada_owner_v1:{fold(owner)}:{cand['id']}",
        )
        ans = (packed.get("answers") or {}).get("link_state") or {}
        score = ans.get("score")
        if score is None:
            return None
        outcome = jev.round_link_outcome(float(score))
        if outcome == "same_entity":
            return cand["id"]
    except Exception:
        return None
    return None


def link_mine(
    mine: dict[str, Any],
    catalog: dict[str, dict[str, Any]],
    name_idx: dict[str, str],
    mine_idx: dict[str, tuple[str, str]],
    use_jev: bool = False,
) -> dict[str, Any]:
    company_id, asset_id, how = match_mine(mine["name"], mine_idx)
    owner_id = None
    owner_how = ""
    for part in split_owners(mine.get("owners") or "") or [mine.get("owners") or ""]:
        oid, oh = match_owner(part, name_idx, catalog)
        if oid:
            owner_id, owner_how = oid, oh
            break
    if not owner_id and use_jev and mine.get("owners"):
        # Only ask Jev when the deterministic linker is empty.
        cands = []
        own = fold(mine["owners"])
        for cid, rec in catalog.items():
            if any(core_key(n) and core_key(n)[:6] == core_key(mine["owners"])[:6] for n in rec["names"] if n):
                cands.append({"id": cid, "name": rec["names"][0] if rec["names"] else cid})
            if own[:8] and fold(cid.replace("-", " "))[:8] == own[:8]:
                cands.append({"id": cid, "name": rec["names"][0] if rec["names"] else cid})
        # unique
        seen: set[str] = set()
        uniq = []
        for c in cands:
            if c["id"] not in seen:
                seen.add(c["id"])
                uniq.append(c)
        jev_id = maybe_jev_link(mine["owners"], uniq[:3])
        if jev_id:
            owner_id, owner_how = jev_id, "jev"

    if company_id and owner_id and company_id != owner_id:
        # Mine name wins for the asset; keep that company so the deep link
        # lands on the claims extract that actually has the pad.
        owner_id = company_id
        owner_how = how + "+owner-conflict"
    if not company_id:
        company_id = owner_id
        if owner_how:
            how = owner_how
    href = None
    in_claims = catalog.get(company_id or "", {}).get("in_claims")
    if company_id and in_claims is False:
        company_id = None
        asset_id = None
        how = None
    if company_id:
        href = "claims.html?company=" + company_id
        if asset_id:
            href += "&asset=" + asset_id
    location = ", ".join(p for p in (mine.get("city"), mine.get("province")) if p)
    return {
        "id": slugify(mine["name"]),
        "name": mine["name"],
        "location": location,
        "city": mine.get("city") or None,
        "province": mine.get("province") or None,
        "owners": mine.get("owners") or None,
        "products": mine.get("product_ids") or [],
        "products_raw": mine.get("products_raw") or None,
        "lat": mine.get("lat"),
        "lon": mine.get("lon"),
        "website": mine.get("website"),
        "layer": mine.get("layer"),
        "claims_company": company_id,
        "claims_asset": asset_id,
        "claims_href": href,
        "link": how or None,
    }


# ---------------------------------------------------------------------------
# Merge
# ---------------------------------------------------------------------------

def prefer_statcan(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One point per year; StatCan beats NRCan when both exist."""
    by_year: dict[int, dict[str, Any]] = {}
    for row in sorted(series, key=lambda r: (r["year"], 0 if r["source"] == "nrcan" else 1)):
        by_year[row["year"]] = row
    return [by_year[y] for y in sorted(by_year)]


def rows_to_commodities(
    production_rows: list[dict[str, Any]],
    mines: list[dict[str, Any]],
    year_min: int,
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    raw_name: dict[str, str] = {}
    unit_for: dict[str, str] = {}
    for row in production_rows:
        if row["year"] < year_min:
            continue
        cid = row["commodity_id"]
        grouped[cid].append(row)
        raw_name.setdefault(cid, row["product"])
        unit_for.setdefault(cid, row.get("unit") or "")

    mines_by_c: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for mine in mines:
        attached = False
        for cid in mine.get("products") or []:
            mines_by_c[cid].append(mine)
            attached = True
        if mine.get("layer") in {"coal", "oil-sands"}:
            mines_by_c[mine["layer"]].append(mine)
            attached = True
        # Keep every Map 900A row even with no catalog company and no
        # commodity token — they still belong on the All operations roster.
        if not attached:
            mines_by_c["other"].append(mine)

    commodities: list[dict[str, Any]] = []
    extra_ids = set(mines_by_c) | set(grouped)
    for cid in extra_ids:
        series_src = prefer_statcan(grouped.get(cid) or [])
        series = []
        for row in series_src:
            point = {
                "year": row["year"],
                "canada": row["value"],
                "source": row["source"],
            }
            if row.get("status"):
                point["status"] = row["status"]
            if row.get("provinces"):
                point["provinces"] = row["provinces"]
            series.append(point)
        # attach StatCan provinces onto matching years when NRCan won the point
        # (already on the chosen row).
        unit, unit_label = unit_norm(unit_for.get(cid) or "")
        mine_rows = []
        seen_m: set[str] = set()
        for mine in mines_by_c.get(cid) or []:
            key = (mine.get("name") or "") + "|" + (mine.get("owners") or "")
            if key in seen_m:
                continue
            seen_m.add(key)
            mine_rows.append(mine)
        mine_rows.sort(key=lambda m: (m.get("province") or "", m.get("name") or ""))
        commodities.append(
            {
                "id": cid,
                "name": display_name(cid, raw_name.get(cid, cid)),
                "kind": KIND_HINT.get(cid, "other"),
                "unit": unit,
                "unit_label": unit_label,
                "series": series,
                "mines": mine_rows,
            }
        )

    def sort_key(c: dict[str, Any]) -> tuple:
        if c["id"] == "all":
            pref = -1
        elif c["id"] == "gold":
            pref = 0
        elif c["id"] in {
            "copper", "nickel", "silver", "iron-ore", "uranium", "potash", "coal",
        }:
            pref = 1
        else:
            pref = 2
        return (pref, c["name"].lower())

    all_mines = sorted(
        mines,
        key=lambda m: (m.get("province") or "", m.get("name") or ""),
    )
    commodities.append(
        {
            "id": "all",
            "name": "All operations",
            "kind": "all",
            "unit": "",
            "unit_label": "",
            "series": [],
            "mines": all_mines,
        }
    )
    commodities.sort(key=sort_key)
    return commodities


def attach_statcan_provinces(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fold province GEO rows onto Canada quantity rows for the same year/product."""
    provinces: dict[tuple[int, str], dict[str, float]] = defaultdict(dict)
    for row in rows:
        geo = row["geo"]
        if geo != "Canada" and geo.lower() in PROVINCES and row["value"] is not None:
            provinces[(row["year"], row["commodity_id"])][geo] = row["value"]
    out: list[dict[str, Any]] = []
    for row in rows:
        if row["geo"] != "Canada":
            continue
        rec = dict(row)
        extra = provinces.get((row["year"], row["commodity_id"]))
        if extra:
            merged = dict(rec.get("provinces") or {})
            merged.update(extra)
            rec["provinces"] = merged
        out.append(rec)
    return out


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------

def load_offline_production() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stat = (FIXTURES / "statcan_sample.csv").read_text(encoding="utf-8")
    rows.extend(parse_statcan_csv(stat))
    for year, name in ((2006, "nrcan_2006.html"), (2025, "nrcan_2025.html")):
        html = (FIXTURES / name).read_text(encoding="utf-8")
        rows.extend(parse_nrcan_html(html, year))
    return attach_statcan_provinces(rows)


def build_payload(
    *,
    root: Path = ROOT,
    sqlite_path: Path | None = None,
    offline: bool = False,
    use_jev: bool = False,
    requested_years: int = REQUESTED_YEARS,
) -> dict[str, Any]:
    blockers: list[str] = []
    sources: list[dict[str, Any]] = []
    production_rows: list[dict[str, Any]] = []
    mines_raw: list[dict[str, Any]] = []

    this_year = datetime.now(timezone.utc).year
    year_min_20 = this_year - requested_years + 1

    if offline:
        production_rows = load_offline_production()
        mines_raw = load_map_fixture(FIXTURES / "map900a_sample.json")
        sources.append({"id": "fixtures", "title": "Offline fixtures in scripts/fixtures/canada/"})
    else:
        try:
            st_rows = fetch_statcan()
            production_rows.extend(st_rows)
            years = sorted({r["year"] for r in st_rows if r["geo"] == "Canada"})
            sources.append(
                {
                    "id": "statcan-16100022",
                    "title": "StatCan table 16-10-0022 (quantities)",
                    "url": "https://www150.statcan.gc.ca/t1/tbl1/en/tv.action?pid=1610002201",
                    "years": [years[0], years[-1]] if years else [],
                }
            )
        except Exception as exc:
            blockers.append(f"StatCan 16-10-0022: {type(exc).__name__}: {exc}")

        statcan_years = {r["year"] for r in production_rows if r.get("geo") == "Canada"}
        latest = max(statcan_years) if statcan_years else this_year
        need_from = latest - requested_years + 1
        nrcan_ok: list[int] = []
        nrcan_fail: list[str] = []
        # Fill years StatCan does not cover. If StatCan failed, pull the full window.
        years_to_pull = [
            y for y in range(need_from, latest + 1) if y not in statcan_years
        ]
        if not years_to_pull and not statcan_years:
            years_to_pull = list(range(latest - requested_years + 1, latest + 1))
        for year in years_to_pull:
            try:
                rows = fetch_nrcan_year(year)
                if rows:
                    production_rows.extend(rows)
                    nrcan_ok.append(year)
                time.sleep(SLEEP_S)
            except Exception as exc:
                nrcan_fail.append(f"{year}: {type(exc).__name__}")
        if nrcan_ok:
            sources.append(
                {
                    "id": "nrcan-annual",
                    "title": "NRCan annual mineral production",
                    "url": "https://mmsd.nrcan-rncan.gc.ca/prod-prod/ann-ann-eng.aspx",
                    "years": [min(nrcan_ok), max(nrcan_ok)],
                }
            )
        if nrcan_fail and not nrcan_ok and not statcan_years:
            blockers.append("NRCan annual tables: " + "; ".join(nrcan_fail[:4]))

        try:
            mines_raw = fetch_map900a()
            sources.append(
                {
                    "id": "map-900a",
                    "title": "NRCan Map 900A principal producing sites",
                    "url": "https://open.canada.ca/data/en/dataset/000183ed-8864-42f0-ae43-c4313a860720",
                    "rest": "https://maps-cartes.services.geo.ca/server_serveur/rest/services/NRCan/900A_and_top_100_en/MapServer",
                    "n": len(mines_raw),
                }
            )
        except Exception as exc:
            blockers.append(f"Map 900A: {type(exc).__name__}: {exc}")
            mines_raw = load_map_fixture(FIXTURES / "map900a_sample.json")
            sources.append({"id": "map-900a-fixture", "title": "Map 900A fixture (live fetch failed)"})

        if blockers and not any(r.get("geo") == "Canada" and r.get("value") is not None for r in production_rows):
            production_rows = load_offline_production()
            sources.append({"id": "fixtures-fallback", "title": "Production fixtures; desktop can refill"})

    production_rows = attach_statcan_provinces(production_rows)
    catalog = load_company_catalog(root, sqlite_path)
    name_idx, mine_idx = build_indexes(catalog)
    linked = [
        link_mine(m, catalog, name_idx, mine_idx, use_jev=use_jev) for m in mines_raw
    ]

    years_have = sorted({r["year"] for r in production_rows if r.get("geo") == "Canada"})
    if years_have:
        span = years_have[-1] - years_have[0] + 1
        year_min = years_have[-1] - requested_years + 1
        if span < FALLBACK_YEARS:
            year_min = years_have[0]
        elif span < requested_years:
            year_min = years_have[0]
    else:
        year_min = year_min_20

    commodities = rows_to_commodities(production_rows, linked, year_min)
    latest_year = None
    for c in commodities:
        nums = [p["year"] for p in c["series"] if p.get("canada") is not None]
        if nums:
            latest_year = max(latest_year or 0, max(nums))

    return {
        "schema": SCHEMA,
        "generated": utc_now(),
        "latest_year": latest_year,
        "years_requested": requested_years,
        "years_available": (max(years_have) - min(years_have) + 1) if years_have else 0,
        "sources": sources,
        "disclaimer": (
            "Not legal title. Not investment advice. Production figures are "
            "national (and provincial) aggregates from StatCan table 16-10-0022 "
            "and NRCan annual mineral production tables. Mine-level tonnes are "
            "not published and are not shown. Map 900A lists significant "
            "producing operations, not every quarry or exploration project. "
            "Claims links open the existing Quebec / Ontario / BC overview-first map."
        ),
        "blockers": blockers,
        "n_mines": len(linked),
        "n_linked": sum(1 for m in linked if m.get("claims_company")),
        "mines": linked,
        "commodities": commodities,
    }


def validate_payload(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if payload.get("schema") != SCHEMA:
        errors.append("bad schema")
    comms = payload.get("commodities") or []
    if not comms:
        errors.append("no commodities")
    gold = next((c for c in comms if c["id"] == "gold"), None)
    all_ops = next((c for c in comms if c["id"] == "all"), None)
    if not gold:
        errors.append("gold missing")
    else:
        nums = [p for p in gold.get("series") or [] if p.get("canada") is not None]
        if len(nums) < 1:
            errors.append("gold series empty")
        if not gold.get("mines"):
            errors.append("gold mines empty")
        for mine in gold.get("mines") or []:
            for banned in ("tonnes", "koz", "quantity", "production_t", "mine_tonnes"):
                if banned in mine:
                    errors.append(f"invented mine field {banned}")
            href = mine.get("claims_href")
            if href and not href.startswith("claims.html?company="):
                errors.append(f"bad claims href {href}")
    if not all_ops or not all_ops.get("mines"):
        errors.append("all operations roster missing")
    elif payload.get("n_mines") and len(all_ops["mines"]) < payload["n_mines"]:
        errors.append("all operations dropped Map 900A mines")
    claims_ids = set()
    claims_path = ROOT / "claims" / "companies.json"
    if claims_path.exists():
        claims_ids = {
            r.get("id")
            for r in (json.loads(claims_path.read_text(encoding="utf-8-sig")).get("companies") or [])
            if r.get("id")
        }
    for comm in comms:
        for mine in comm.get("mines") or []:
            cid = mine.get("claims_company")
            if cid and claims_ids and cid not in claims_ids:
                errors.append(f"claims href for non-catalog id {cid}")
            if mine.get("owners") == "Vale Canada Limited" and mine.get("claims_company") == "canada-nickel":
                errors.append("Vale Canada must not link to canada-nickel")
    return errors


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--sqlite", type=Path, default=None, help="Optional qc.sqlite path (not committed)")
    p.add_argument("--jev", action="store_true", help="Optional TypeSafe owner linker")
    p.add_argument("--offline", action="store_true", help="Rebuild from scripts/fixtures/canada/")
    p.add_argument("--check", action="store_true", help="Validate the committed JSON and exit")
    p.add_argument("--years", type=int, default=REQUESTED_YEARS)
    args = p.parse_args(argv)

    out = args.out or (args.root / "canada" / "commodities.json")
    if args.check:
        payload = json.loads(out.read_text(encoding="utf-8"))
        errors = validate_payload(payload)
        if errors:
            print("canada commodities check FAIL: " + "; ".join(errors), file=sys.stderr)
            return 1
        print(
            f"canada commodities ok schema={payload.get('schema')} "
            f"n={len(payload.get('commodities') or [])} "
            f"latest={payload.get('latest_year')} mines={payload.get('n_mines')}"
        )
        return 0

    payload = build_payload(
        root=args.root,
        sqlite_path=args.sqlite,
        offline=args.offline,
        use_jev=args.jev,
        requested_years=args.years,
    )
    errors = validate_payload(payload)
    write_json(out, payload)
    print(
        f"wrote {out} commodities={len(payload['commodities'])} "
        f"mines={payload['n_mines']} linked={payload['n_linked']} "
        f"years={payload['years_available']} latest={payload['latest_year']}"
    )
    if payload.get("blockers"):
        print("blockers: " + " | ".join(payload["blockers"]))
    if errors:
        print("validate: " + "; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
