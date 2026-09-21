#!/usr/bin/env python3
"""Export cited Canadian mine production from qc.sqlite onto Pages JSON.

SQLite is the store (canada_mines / canada_mine_production /
canada_mine_sources). This module updates existing Newmont-shaped
beta/<issuer>.json files and writes the thin canada/producer-join.json
export. Never invent ounces. Owner-coverage shells are minted by
`build_canada_owner_beta_shells.py` (empty production). Never copy a complex
total onto a pit row. Match each file's units.gold (koz on Newmont).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

JOIN_SCHEMA = "qc-canada-producer-join-v1"
YEAR = 2025
FOOTNOTE = (
    "From company filings where disclosed; StatCan/NRCan do not publish "
    "mine-level output. Stored in qc.sqlite; Pages reads the thin export."
)
UNIT_NOTE = (
    "Canada table reads canada/producer-join.json commodities exported from "
    "qc.sqlite. Gold/silver/PGM are troy ounces. Other commodities keep the "
    "source unit. Existing beta/<issuer>.json pages still store gold in each "
    "file's units.gold (usually koz)."
)

# Map 900A mine_id → beta producer page + asset id.
# omit_figure: complex / FY-mismatch / not disclosed — join still points
# at the issuer, but the Canada cell stays blank and we do not write
# by_asset from that pit row.
ISSUER_MAP: dict[str, dict[str, Any]] = {
    "blackwater": {
        "beta_id": "artemis-gold", "asset_id": "blackwater",
        "ownership_pct": 100, "region": "British Columbia",
    },
    "brucejack": {
        "beta_id": "newmont", "asset_id": "brucejack",
        "ownership_pct": 100, "region": "British Columbia",
    },
    "copper-mountain": {
        "beta_id": "hudbay-minerals", "asset_id": "copper-mountain",
        "ownership_pct": 100, "region": "British Columbia",
        "commodity": "copper",
    },
    "dome-mountain": {
        "beta_id": "blue-lagoon-resources", "asset_id": "dome-mountain",
        "ownership_pct": 100, "region": "British Columbia",
    },
    "elk": {
        "beta_id": "gold-mountain-mining", "asset_id": "elk",
        "ownership_pct": 100, "region": "British Columbia",
    },
    "mount-milligan": {
        "beta_id": "centerra-gold", "asset_id": "mount-milligan",
        "ownership_pct": 100, "region": "British Columbia",
        "commodity": "gold",
    },
    "mount-polley": {
        "beta_id": "imperial-metals", "asset_id": "mount-polley",
        "ownership_pct": 100, "region": "British Columbia",
        "commodity": "copper",
    },
    "new-afton": {
        "beta_id": "new-gold", "asset_id": "new-afton",
        "ownership_pct": 100, "region": "British Columbia",
        "commodity": "copper",
    },
    "red-chris": {
        "beta_id": "newmont", "asset_id": "red-chris",
        "ownership_pct": 70, "region": "British Columbia",
        "commodity": "gold",
    },
    "valentine": {
        "beta_id": "equinox-gold", "asset_id": "valentine",
        "ownership_pct": 100, "region": "Newfoundland and Labrador",
    },
    "amaruq-meadowbank": {
        "beta_id": "agnico-eagle", "asset_id": "meadowbank",
        "ownership_pct": 100, "region": "Nunavut",
    },
    "goose-back-river": {
        "beta_id": "b2gold", "asset_id": "goose",
        "ownership_pct": 100, "region": "Nunavut",
    },
    "meliadine": {
        "beta_id": "agnico-eagle", "asset_id": "meliadine",
        "ownership_pct": 100, "region": "Nunavut",
    },
    "cote-gold": {
        "beta_id": "iamgold", "asset_id": "cote-gold",
        "ownership_pct": 70, "region": "Ontario",
    },
    "detour-lake": {
        "beta_id": "agnico-eagle", "asset_id": "detour-lake",
        "ownership_pct": 100, "region": "Ontario",
    },
    "eagle-river": {
        "beta_id": "wesdome-gold-mines", "asset_id": "eagle-river",
        "ownership_pct": 100, "region": "Ontario",
    },
    "greenstone": {
        "beta_id": "equinox-gold", "asset_id": "greenstone",
        "ownership_pct": 100, "region": "Ontario",
    },
    "hemlo-williams": {
        "beta_id": "hemlo-mining", "asset_id": "hemlo",
        "ownership_pct": 100, "region": "Ontario",
    },
    "macassa": {
        "beta_id": "agnico-eagle", "asset_id": "macassa",
        "ownership_pct": 100, "region": "Ontario",
    },
    "musselwhite": {
        "beta_id": "orla-mining", "asset_id": "musselwhite",
        "ownership_pct": 100, "region": "Ontario",
    },
    "rainy-river": {
        "beta_id": "new-gold", "asset_id": "rainy-river",
        "ownership_pct": 100, "region": "Ontario",
    },
    "young-davidson": {
        "beta_id": "alamos-gold", "asset_id": "young-davidson",
        "ownership_pct": 100, "region": "Ontario",
    },
    "casa-berardi": {
        "beta_id": "hecla-mining", "asset_id": "casa-berardi",
        "ownership_pct": 100, "region": "Quebec",
    },
    "goldex-goldex": {
        "beta_id": "agnico-eagle", "asset_id": "goldex",
        "ownership_pct": 100, "region": "Quebec",
    },
    "laronde": {
        "beta_id": "agnico-eagle", "asset_id": "laronde",
        "ownership_pct": 100, "region": "Quebec",
    },
    "lamaque": {
        "beta_id": "eldorado-gold", "asset_id": "lamaque",
        "ownership_pct": 100, "region": "Quebec",
    },
    "kiena": {
        "beta_id": "wesdome-gold-mines", "asset_id": "kiena",
        "ownership_pct": 100, "region": "Quebec",
    },
    "westwood-westwood": {
        "beta_id": "iamgold", "asset_id": "westwood",
        "ownership_pct": 100, "region": "Quebec",
    },
    "santoy-seabee": {
        "beta_id": "ssr-mining", "asset_id": "seabee",
        "ownership_pct": 100, "region": "Saskatchewan",
    },
    "island-gold-island-gold-district": {
        "beta_id": "alamos-gold", "asset_id": "island-gold-district",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "magino-island-gold-district": {
        "beta_id": "alamos-gold", "asset_id": "island-gold-district",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "barnat-canadian-malartic": {
        "beta_id": "agnico-eagle", "asset_id": "canadian-malartic",
        "ownership_pct": 100, "region": "Quebec", "omit_figure": True,
    },
    "odyssey-canadian-malartic": {
        "beta_id": "agnico-eagle", "asset_id": "canadian-malartic",
        "ownership_pct": 100, "region": "Quebec", "omit_figure": True,
    },
    "akasaba-west-goldex": {
        "beta_id": "agnico-eagle", "asset_id": "goldex",
        "ownership_pct": 100, "region": "Quebec", "omit_figure": True,
    },
    "grand-duc-westwood": {
        "beta_id": "iamgold", "asset_id": "westwood",
        "ownership_pct": 100, "region": "Quebec", "omit_figure": True,
    },
    "lalor-lake-snow-lake": {
        "beta_id": "hudbay-minerals", "asset_id": "snow-lake",
        "ownership_pct": 100, "region": "Manitoba", "omit_figure": True,
        "commodity": "gold",
    },
    "new-britannia-mill-snow-lake": {
        "beta_id": "hudbay-minerals", "asset_id": "snow-lake",
        "ownership_pct": 100, "region": "Manitoba", "omit_figure": True,
        "commodity": "gold",
    },
    "borden-porcupine": {
        "beta_id": "discovery-mining", "asset_id": "porcupine",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "dome-mill-porcupine": {
        "beta_id": "discovery-mining", "asset_id": "porcupine",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "hoyle-pond-porcupine": {
        "beta_id": "discovery-mining", "asset_id": "porcupine",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "bell-creek": {
        "beta_id": "pan-american-silver", "asset_id": "timmins",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "timmins-west": {
        "beta_id": "pan-american-silver", "asset_id": "timmins",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "red-lake": {
        "beta_id": "evolution", "asset_id": "red-lake",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "eleonore": {"omit_figure": True, "region": "Quebec"},
    "black-fox-fox-complex": {
        "beta_id": "mcewen", "asset_id": "fox-complex",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "lac-des-iles": {
        "beta_id": "impala-platinum-holdings", "asset_id": "lac-des-iles",
        "ownership_pct": 100, "region": "Ontario", "omit_figure": True,
    },
    "gibraltar": {
        "beta_id": "taseko-mines", "asset_id": "gibraltar",
        "ownership_pct": 100, "region": "British Columbia", "commodity": "copper",
    },
    "highland-valley-copper": {
        "beta_id": "teck-resources", "asset_id": "highland-valley-copper",
        "ownership_pct": 100, "region": "British Columbia", "commodity": "copper",
    },
    "cigar-lake": {
        "beta_id": "cameco", "asset_id": "cigar-lake",
        "ownership_pct": 54.547, "region": "Saskatchewan", "commodity": "uranium",
    },
    "mcarthur-river": {
        "beta_id": "cameco", "asset_id": "mcarthur-river",
        "ownership_pct": 69.805, "region": "Saskatchewan", "commodity": "uranium",
    },
    "key-lake-mill": {
        "beta_id": "cameco", "asset_id": "key-lake-mill",
        "ownership_pct": 83.33, "region": "Saskatchewan", "commodity": "uranium",
        "omit_figure": True,
    },
    "keno-hill-silver-district": {
        "beta_id": "hecla-mining", "asset_id": "keno-hill",
        "ownership_pct": 100, "region": "Yukon", "commodity": "silver",
    },
    "voisey-s-bay": {
        "beta_id": "vale", "asset_id": "voisey-s-bay",
        "ownership_pct": 100, "region": "Newfoundland and Labrador", "commodity": "nickel",
    },
    "thompson-t-1-and-t-3": {
        "beta_id": "vale", "asset_id": "thompson-t-1-and-t-3",
        "ownership_pct": 100, "region": "Manitoba", "commodity": "nickel",
    },
    "garson-sudbury-operations": {
        "beta_id": "vale", "asset_id": "garson-sudbury-operations",
        "ownership_pct": 100, "region": "Ontario", "commodity": "nickel", "omit_figure": True,
    },
    "stobie-sudbury-operations": {
        "beta_id": "vale", "asset_id": "stobie-sudbury-operations",
        "ownership_pct": 100, "region": "Ontario", "commodity": "nickel", "omit_figure": True,
    },
    "clarabelle-mill-sudbury-operations": {
        "beta_id": "vale", "asset_id": "clarabelle-mill-sudbury-operations",
        "ownership_pct": 100, "region": "Ontario", "commodity": "nickel", "omit_figure": True,
    },
    "copper-cliff-sudbury-operations": {
        "beta_id": "vale", "asset_id": "copper-cliff-sudbury-operations",
        "ownership_pct": 100, "region": "Ontario", "commodity": "nickel", "omit_figure": True,
    },
    "creighton-sudbury-operations": {
        "beta_id": "vale", "asset_id": "creighton-sudbury-operations",
        "ownership_pct": 100, "region": "Ontario", "commodity": "nickel", "omit_figure": True,
    },
    "coleman-sudbury-operations": {
        "beta_id": "vale", "asset_id": "coleman-sudbury-operations",
        "ownership_pct": 100, "region": "Ontario", "commodity": "nickel", "omit_figure": True,
    },
    "totten-sudbury-operations": {
        "beta_id": "vale", "asset_id": "totten-sudbury-operations",
        "ownership_pct": 100, "region": "Ontario", "commodity": "nickel", "omit_figure": True,
    },
    "gahcho-kue": {
        "beta_id": "anglo-american", "asset_id": "gahcho-kue",
        "ownership_pct": 51, "region": "Northwest Territories", "commodity": "diamonds",
    },
    "diavik": {
        "beta_id": "rio-tinto", "asset_id": "diavik",
        "ownership_pct": 100, "region": "Northwest Territories", "commodity": "diamonds",
    },
    "carol-lake": {
        "beta_id": "iron-ore-company-of-canada", "asset_id": "carol-lake",
        "ownership_pct": 100, "region": "Newfoundland and Labrador", "commodity": "iron-ore",
    },
    "lac-tio": {
        "beta_id": "rio-tinto", "asset_id": "lac-tio",
        "ownership_pct": 100, "region": "Quebec", "commodity": "titanium", "omit_figure": True,
    },
    "rocanville": {
        "beta_id": "nutrien", "asset_id": "rocanville",
        "ownership_pct": 100, "region": "Saskatchewan", "commodity": "potash",
    },
    "allan": {
        "beta_id": "nutrien", "asset_id": "allan",
        "ownership_pct": 100, "region": "Saskatchewan", "commodity": "potash",
    },
    "lanigan": {
        "beta_id": "nutrien", "asset_id": "lanigan",
        "ownership_pct": 100, "region": "Saskatchewan", "commodity": "potash",
    },
    "vanscoy": {
        "beta_id": "nutrien", "asset_id": "vanscoy",
        "ownership_pct": 100, "region": "Saskatchewan", "commodity": "potash",
    },
    "cory": {
        "beta_id": "nutrien", "asset_id": "cory",
        "ownership_pct": 100, "region": "Saskatchewan", "commodity": "potash",
    },
    "patience-lake": {
        "beta_id": "nutrien", "asset_id": "patience-lake",
        "ownership_pct": 100, "region": "Saskatchewan", "commodity": "potash",
    },
    "esterhazy-k-3": {
        "beta_id": "mosaic-company", "asset_id": "esterhazy-k-3",
        "ownership_pct": 100, "region": "Saskatchewan", "commodity": "potash",
    },
    "belle-plaine": {
        "beta_id": "mosaic-company", "asset_id": "belle-plaine",
        "ownership_pct": 100, "region": "Saskatchewan", "commodity": "potash",
    },
    "colonsay": {
        "beta_id": "mosaic-company", "asset_id": "colonsay",
        "ownership_pct": 100, "region": "Saskatchewan", "commodity": "potash",
    },
    "kidd-creek": {
        "beta_id": "glencore", "asset_id": "kidd-creek",
        "ownership_pct": 100, "region": "Ontario", "commodity": "zinc",
        "omit_figure": True,
    },
    "raglan": {
        "beta_id": "glencore", "asset_id": "raglan",
        "ownership_pct": 100, "region": "Quebec", "commodity": "nickel", "omit_figure": True,
    },
    "fraser-sudbury-ino": {
        "beta_id": "glencore", "asset_id": "fraser-sudbury-ino",
        "ownership_pct": 100, "region": "Ontario", "commodity": "nickel", "omit_figure": True,
    },
    "strathcona-mill-sudbury-ino": {
        "beta_id": "glencore", "asset_id": "strathcona-mill-sudbury-ino",
        "ownership_pct": 100, "region": "Ontario", "commodity": "nickel", "omit_figure": True,
    },
    "elkview": {
        "beta_id": "glencore", "asset_id": "elkview",
        "ownership_pct": 100, "region": "British Columbia", "commodity": "coal", "omit_figure": True,
    },
    "fording-river": {
        "beta_id": "glencore", "asset_id": "fording-river",
        "ownership_pct": 100, "region": "British Columbia", "commodity": "coal", "omit_figure": True,
    },
    "greenhills": {
        "beta_id": "glencore", "asset_id": "greenhills",
        "ownership_pct": 100, "region": "British Columbia", "commodity": "coal", "omit_figure": True,
    },
    "line-creek": {
        "beta_id": "glencore", "asset_id": "line-creek",
        "ownership_pct": 100, "region": "British Columbia", "commodity": "coal", "omit_figure": True,
    },
    "bloom-lake": {
        "beta_id": "champion-iron", "asset_id": "bloom-lake",
        "ownership_pct": 100, "region": "Quebec", "commodity": "iron-ore", "omit_figure": True,
    },
    "mont-wright": {
        "beta_id": "arcelormittal", "asset_id": "mont-wright",
        "ownership_pct": 100, "region": "Quebec", "commodity": "iron-ore", "omit_figure": True,
    },
    "fire-lake": {
        "beta_id": "arcelormittal", "asset_id": "fire-lake",
        "ownership_pct": 100, "region": "Quebec", "commodity": "iron-ore", "omit_figure": True,
    },
    "mccreedy-west": {
        "beta_id": "magna-mining", "asset_id": "mccreedy-west",
        "ownership_pct": 100, "region": "Ontario", "commodity": "copper", "omit_figure": True,
    },
    "ekati": {
        "beta_id": "burgundy-diamond", "asset_id": "ekati",
        "ownership_pct": 100, "region": "Northwest Territories", "commodity": "diamonds",
        "omit_figure": True,
    },
    "stall-concentrator-snow-lake": {
        "beta_id": "hudbay-minerals", "asset_id": "snow-lake",
        "ownership_pct": 100, "region": "Manitoba", "commodity": "zinc", "omit_figure": True,
    },
}

NEW_ISSUERS: dict[str, dict[str, Any]] = {
    "gold-mountain-mining": {
        "name": "Gold Mountain Mining Corp.",
        "short": "Gold Mountain",
        "tickers": [
            {"symbol": "GMTN.V", "exchange": "TSXV", "chart": "ticker.html?t=GMTN.V"},
        ],
        "hq": "Vancouver, British Columbia, Canada",
        "commodity": "gold",
        "website": "",
        "stage": "care-and-maintenance",
    },
    "imperial-metals": {
        "name": "Imperial Metals Corporation",
        "short": "Imperial Metals",
        "tickers": [
            {"symbol": "III.TO", "exchange": "TSX", "chart": "ticker.html?t=III.TO"},
        ],
        "hq": "Vancouver, British Columbia, Canada",
        "commodity": "copper",
        "website": "https://www.imperialmetals.com",
        "stage": "producing",
    },
}

PROD_KEYS = (
    "production_2025",
    "production_unit",
    "production_source",
    "production_source_title",
    "production_as_of",
    "production_quote",
    "production_blocker",
)


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def gold_unit(profile: dict[str, Any]) -> str:
    unit = ((profile.get("units") or {}).get("gold") or "koz").strip().lower()
    return "koz" if unit in {"koz", "000 oz", "thousand ounces"} else unit


def troy_to_profile_gold(troy_oz: float, profile: dict[str, Any]) -> float:
    if gold_unit(profile) == "koz":
        v = float(troy_oz) / 1000.0
        return int(v) if abs(v - round(v)) < 1e-9 else round(v, 3)
    v = float(troy_oz)
    return int(v) if abs(v - round(v)) < 1e-9 else v


def profile_gold_to_troy(value: float, profile: dict[str, Any]) -> float:
    if gold_unit(profile) == "koz":
        return float(value) * 1000.0
    return float(value)


def annual_2025(profile: dict[str, Any]) -> dict[str, Any] | None:
    for rec in profile.get("production") or []:
        if rec.get("period") == str(YEAR) and rec.get("kind") == "annual":
            return rec
    return None


def figure_for_table(
    profile: dict[str, Any],
    asset_id: str,
    commodity: str,
) -> dict[str, Any] | None:
    """Canada-table view of one by_asset row. Prefer 100% mine output."""
    rec = annual_2025(profile)
    row = ((rec or {}).get("by_asset") or {}).get(asset_id)
    if not row:
        return None
    if commodity == "gold":
        koz = row.get("koz_100pct")
        if koz is None:
            koz = row.get("attr_koz")
        if koz is None:
            return None
        return {
            "value": profile_gold_to_troy(koz, profile),
            "unit": "troy oz",
            "field": "koz_100pct" if row.get("koz_100pct") is not None else "attr_koz",
        }
    if commodity == "copper":
        if row.get("copper_t") is not None:
            return {"value": row["copper_t"], "unit": "t", "field": "copper_t"}
        if row.get("copper_mlb") is not None:
            return {"value": row["copper_mlb"], "unit": "Mlb", "field": "copper_mlb"}
        return None
    if commodity == "silver":
        if row.get("silver_koz") is not None:
            return {
                "value": float(row["silver_koz"]) * 1000.0,
                "unit": "troy oz",
                "field": "silver_koz",
            }
        if row.get("silver_oz") is not None:
            return {"value": row["silver_oz"], "unit": "troy oz", "field": "silver_oz"}
        return None
    extra = {
        "nickel": (("nickel_kt", "kt"), ("nickel_t", "t")),
        "uranium": (("uranium_mlb", "Mlb"),),
        "potash": (("potash_mt", "Mt"),),
        "iron-ore": (("iron_mt", "Mt"),),
        "diamonds": (("diamonds_kct", "kct"), ("diamonds_ct", "ct")),
        "molybdenum": (("molybdenum_mlb", "Mlb"), ("molybdenum_t", "t")),
        "zinc": (("zinc_kt", "kt"), ("zinc_t", "t")),
        "platinum": (("platinum_oz", "troy oz"),),
        "palladium": (("palladium_oz", "troy oz"),),
    }
    for field, unit in extra.get(commodity) or ():
        if row.get(field) is not None:
            return {"value": row[field], "unit": unit, "field": field}
    return None


def empty_profile(beta_id: str, spec: dict[str, Any]) -> dict[str, Any]:
    stage = spec.get("stage") or "producing"
    producing = stage == "producing"
    return {
        "schema": "qc-issuer-profile-v1",
        "id": beta_id,
        "kind": "producer" if producing else "explorer",
        "stage": stage,
        "generated": utc_today(),
        "disclaimer": (
            "Filing-backed Canadian mine production from public issuer reports. "
            "Do not invent ounces. Not S&P. Not investment advice."
        ),
        "units": {
            "mass": "kt",
            "gold": "koz",
            "grade": "g/t Au",
            "currency": "USD",
        },
        "issuer": {
            "name": spec["name"],
            "short": spec.get("short") or spec["name"],
            "tickers": spec.get("tickers") or [],
            "hq": spec.get("hq") or "Canada",
            "commodity": spec.get("commodity") or "gold",
            "website": spec.get("website") or "",
        },
        "method": {
            "production_basis": (
                "Company-disclosed mine actuals. Gold in koz (troy). "
                "Do not invent ounces."
            ),
            "resources_basis": "Not gathered. Do not invent ounces.",
        },
        "kpis": {
            "attr_koz_2025": None,
            "attr_pp_koz": None,
            "attr_mi_incl_koz": None,
            "attr_inf_koz": None,
        },
        "guidance_2026": {"basis": None, "total_koz": None},
        "production": [],
        "assets": [],
        "reserves_resources": {"as_of": None, "projects": []},
        "sources": [],
        "meta": {
            "layer": "canada-mine-production",
            "filing_backed": False,
        },
    }


def load_existing_profile(root: Path, beta_id: str) -> dict[str, Any] | None:
    path = root / "beta" / f"{beta_id}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_or_create_profile(root: Path, beta_id: str) -> dict[str, Any]:
    existing = load_existing_profile(root, beta_id)
    if existing is not None:
        return existing
    spec = NEW_ISSUERS.get(beta_id)
    if not spec:
        raise FileNotFoundError(f"no beta profile for {beta_id}")
    return empty_profile(beta_id, spec)


def upsert_asset(profile: dict[str, Any], asset: dict[str, Any]) -> bool:
    assets = profile.setdefault("assets", [])
    for existing in assets:
        if existing.get("id") == asset["id"]:
            return False
    assets.append(asset)
    return True


def merge_by_asset(existing: dict[str, Any] | None, incoming: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Fill missing keys only. Never overwrite a filed gold number.

    If the row already has gold ounces, only structured by-product fields
    may be filled. Notes/sources stay on the existing filing-backed page.
    """
    if not existing:
        return dict(incoming), True
    out = dict(existing)
    changed = False
    had_gold = existing.get("attr_koz") is not None or existing.get("koz_100pct") is not None
    allowed = (
        {
            "copper_t", "copper_mlb", "silver_koz", "silver_oz",
            "nickel_kt", "nickel_t", "uranium_mlb", "potash_mt", "iron_mt",
            "diamonds_kct", "diamonds_ct", "molybdenum_mlb", "molybdenum_t",
            "zinc_kt", "zinc_t", "platinum_oz", "palladium_oz",
        }
        if had_gold
        else set(incoming)
    )
    for key, val in incoming.items():
        if val is None or key not in allowed:
            continue
        if key not in out or out[key] is None:
            out[key] = val
            changed = True
    return out, changed


def upsert_prod_2025(
    profile: dict[str, Any],
    asset_id: str,
    row: dict[str, Any],
) -> bool:
    prods = profile.setdefault("production", [])
    rec = annual_2025(profile)
    created = False
    if rec is None:
        rec = {"period": str(YEAR), "kind": "annual", "by_asset": {}}
        prods.insert(0, rec)
        created = True
    ba = rec.setdefault("by_asset", {})
    merged, changed = merge_by_asset(ba.get(asset_id), row)
    if changed or asset_id not in ba:
        ba[asset_id] = merged
        changed = True
    return changed or created


def finalize_shell_total(profile: dict[str, Any], *, was_empty: bool) -> bool:
    """Set company 2025 attr_koz only when every producing asset is cited.

    A one-mine fill (Copper Mountain) must not become Hudbay's company total.
    """
    if not was_empty:
        return False
    rec = annual_2025(profile)
    if not rec:
        return False
    ba = rec.get("by_asset") or {}
    producing = [
        a for a in (profile.get("assets") or [])
        if a.get("in_production") and a.get("id")
    ]
    if not producing:
        return False
    vals = []
    for asset in producing:
        row = ba.get(asset["id"]) or {}
        if row.get("attr_koz") is None:
            if rec.get("attr_koz") is not None:
                rec.pop("attr_koz", None)
                kpis = profile.setdefault("kpis", {})
                if kpis.get("attr_koz_2025") is not None:
                    kpis["attr_koz_2025"] = None
                return True
            return False
        vals.append(row["attr_koz"])
    total = round(sum(vals), 3)
    changed = rec.get("attr_koz") != total
    rec["attr_koz"] = total
    kpis = profile.setdefault("kpis", {})
    if kpis.get("attr_koz_2025") != total:
        kpis["attr_koz_2025"] = total
        changed = True
    return changed


def append_source(profile: dict[str, Any], src: dict[str, Any]) -> bool:
    title = (src.get("production_source_title") or src.get("title") or "").strip()
    url = (src.get("production_source") or src.get("url") or "").strip()
    if not (title or url):
        return False
    sources = profile.setdefault("sources", [])
    for row in sources:
        if url and row.get("url") == url:
            return False
        if title and row.get("title") == title:
            return False
    entry: dict[str, Any] = {}
    if title:
        entry["title"] = title
    if src.get("production_as_of") or src.get("as_of"):
        entry["date"] = src.get("production_as_of") or src.get("as_of")
    if url:
        entry["url"] = url
    quote = None
    comms = src.get("commodities") or {}
    for row in comms.values():
        if row.get("quote"):
            quote = row["quote"]
            break
    if quote:
        entry["what"] = quote
    sources.append(entry)
    return True


def mark_filing_backed(profile: dict[str, Any], wrote_figure: bool) -> bool:
    changed = False
    meta = profile.setdefault("meta", {})
    if wrote_figure and meta.get("filing_backed") is False:
        meta["filing_backed"] = True
        changed = True
    method = profile.setdefault("method", {})
    if wrote_figure and method.get("production_basis") == "Not gathered. Do not invent ounces.":
        method["production_basis"] = (
            "Company-disclosed mine actuals (Canada ingest). Gold in koz (troy)."
        )
        changed = True
    if wrote_figure:
        if profile.get("kind") == "explorer":
            profile["kind"] = "producer"
            changed = True
        if profile.get("stage") == "exploration":
            profile["stage"] = "producing"
            changed = True
        disc = profile.get("disclaimer") or ""
        if "No production or reserves until a filing pass" in disc:
            profile["disclaimer"] = (
                "Filing-backed Canadian mine production from public issuer "
                "reports. Do not invent ounces. Not S&P. Not investment advice."
            )
            changed = True
    return changed


def _extra_by_asset(row: dict[str, Any], comms: dict[str, Any]) -> None:
    """Copy non-Au/Ag/Cu commodities onto by_asset using source units."""
    mapping = {
        "nickel": (("kt", "nickel_kt"), ("t", "nickel_t")),
        "uranium": (("mlb", "uranium_mlb"),),
        "potash": (("mt", "potash_mt"),),
        "iron-ore": (("mt", "iron_mt"),),
        "diamonds": (("kct", "diamonds_kct"), ("ct", "diamonds_ct")),
        "molybdenum": (("mlb", "molybdenum_mlb"), ("t", "molybdenum_t")),
        "zinc": (("kt", "zinc_kt"), ("t", "zinc_t")),
        "platinum": (("troy oz", "platinum_oz"), ("oz", "platinum_oz")),
        "palladium": (("troy oz", "palladium_oz"), ("oz", "palladium_oz")),
    }
    for cid, choices in mapping.items():
        rec = comms.get(cid)
        if not rec or rec.get("value") is None:
            continue
        unit = (rec.get("unit") or rec.get("source_unit") or "").strip().lower()
        field = None
        for want, dest in choices:
            if unit == want:
                field = dest
                break
        if field is None:
            field = choices[0][1]
        row[field] = rec["value"]


def by_asset_from_record(
    rec: dict[str, Any],
    profile: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any] | None:
    comms = rec.get("commodities") or {}
    if not comms:
        return None
    row: dict[str, Any] = {}
    own = meta.get("ownership_pct")
    if own is not None:
        row["ownership_pct"] = own
    gold = comms.get("gold")
    if gold and gold.get("value") is not None:
        koz = troy_to_profile_gold(gold["value"], profile)
        row["koz_100pct"] = koz
        if own in (None, 100):
            row["attr_koz"] = koz
        else:
            # Do not invent attributable ounces from a 100% disclosure
            # when ownership is a known JV (Red Chris 70%, Côté 70%).
            # Leave attr_koz for an existing filing-backed row to keep.
            pass
    copper = comms.get("copper")
    if copper and copper.get("value") is not None and own in (None, 100):
        unit = (copper.get("unit") or copper.get("source_unit") or "").lower()
        if unit in {"mlb", "mlbs"}:
            row["copper_mlb"] = copper["value"]
        elif unit in {"kt"}:
            row["copper_t"] = float(copper["value"]) * 1000.0
        else:
            row["copper_t"] = copper["value"]
    silver = comms.get("silver")
    if silver and silver.get("value") is not None and own in (None, 100):
        troy = silver["value"]
        if gold_unit(profile) == "koz":
            row["silver_koz"] = troy_to_profile_gold(troy, profile)
        else:
            row["silver_oz"] = troy
    # 100% mine output as disclosed (Cigar Lake 19.1 Mlb; Gahcho Kué 2,210 kct is already 51%).
    _extra_by_asset(row, comms)
    quote = None
    for c in comms.values():
        if c.get("quote"):
            quote = c["quote"]
            break
    if quote and len(quote.strip()) >= 24:
        row["note"] = quote.strip()
    return row or None


def apply_record_to_profile(
    profile: dict[str, Any],
    rec: dict[str, Any],
    meta: dict[str, Any],
    src: dict[str, Any],
) -> bool:
    asset_id = meta.get("asset_id")
    if not asset_id:
        return False
    name = rec.get("mine_name") or src.get("mine_name") or asset_id
    # Prefer the canonical asset name (Meadowbank, not Amaruq (Meadowbank)).
    if asset_id in {"meadowbank", "goose", "seabee", "hemlo", "porcupine",
                    "timmins", "snow-lake", "island-gold-district",
                    "canadian-malartic", "fox-complex"}:
        name = {
            "meadowbank": "Meadowbank",
            "goose": "Goose",
            "seabee": "Seabee",
            "hemlo": "Hemlo",
            "porcupine": "Porcupine",
            "timmins": "Timmins",
            "snow-lake": "Snow Lake",
            "island-gold-district": "Island Gold District",
            "canadian-malartic": "Canadian Malartic",
            "fox-complex": "Fox Complex",
        }[asset_id]
    commodity = meta.get("commodity") or "gold"
    has_figure = bool(rec.get("commodities")) and not meta.get("omit_figure")
    asset = {
        "id": asset_id,
        "name": name,
        "stage": "producing",
        "in_production": True,
        "commodity": commodity,
        "country": "Canada",
        "ownership_pct": meta.get("ownership_pct") if meta.get("ownership_pct") is not None else 100,
        "region": meta.get("region"),
    }
    if asset_id == "elk":
        asset["stage"] = "care-and-maintenance"
        asset["in_production"] = False
        asset["location_note"] = rec.get("blocker")
    elif asset_id == "dome-mountain":
        asset["stage"] = "ramp-up"
        asset["in_production"] = True
        asset["location_note"] = rec.get("blocker")
    elif rec.get("blocker") and not has_figure:
        asset["location_note"] = rec.get("blocker")
    changed = upsert_asset(profile, {k: v for k, v in asset.items() if v is not None})
    wrote_figure = False
    if not meta.get("omit_figure"):
        row = by_asset_from_record(rec, profile, meta)
        if row:
            rec2025 = annual_2025(profile)
            had_slot = bool(((rec2025 or {}).get("by_asset") or {}).get(asset_id))
            if upsert_prod_2025(profile, asset_id, row):
                changed = True
                wrote_figure = not had_slot
    if wrote_figure and append_source(profile, {**src, **rec}):
        changed = True
    if mark_filing_backed(profile, wrote_figure):
        changed = True
    return changed


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def write_beta_profiles(
    book: dict[str, Any],
    sources_book: dict[str, Any],
    *,
    root: Path = ROOT,
    create: bool = False,
) -> dict[str, Any]:
    """Update existing beta producer pages from the sqlite book. No new files."""
    by_mine_src = {row["mine_id"]: row for row in (sources_book.get("mines") or [])}
    written: list[str] = []
    skipped: list[str] = []
    created: list[str] = []
    dirty: dict[str, dict[str, Any]] = {}

    for mid, rec in (book.get("mines") or {}).items():
        meta = dict(ISSUER_MAP.get(mid) or {})
        src = by_mine_src.get(mid) or {}
        if src.get("beta_id"):
            meta["beta_id"] = src["beta_id"]
        if rec.get("company_id"):
            meta["beta_id"] = rec["company_id"]
        if src.get("asset_id") or rec.get("asset_id"):
            meta["asset_id"] = src.get("asset_id") or rec.get("asset_id")
        if src.get("ownership_pct") is not None:
            meta["ownership_pct"] = src["ownership_pct"]
        elif rec.get("ownership_pct") is not None:
            meta["ownership_pct"] = rec["ownership_pct"]
        if rec.get("omit_figure"):
            meta["omit_figure"] = True
        beta_id = meta.get("beta_id")
        if not beta_id:
            skipped.append(mid)
            continue
        path = root / "beta" / f"{beta_id}.json"
        if not path.exists() and not create:
            skipped.append(mid)
            continue
        if beta_id not in dirty:
            existed = path.exists()
            profile = load_or_create_profile(root, beta_id) if create else load_existing_profile(root, beta_id)
            if profile is None:
                skipped.append(mid)
                continue
            dirty[beta_id] = {
                "profile": profile,
                "was_empty": (not existed) or (
                    not annual_2025(profile)
                    and (profile.get("kpis") or {}).get("attr_koz_2025") is None
                ),
            }
            if not existed:
                created.append(beta_id)
        if apply_record_to_profile(dirty[beta_id]["profile"], rec, meta, src):
            if beta_id not in written:
                written.append(beta_id)

    for beta_id, state in dirty.items():
        profile = state["profile"]
        if finalize_shell_total(profile, was_empty=state["was_empty"]):
            if beta_id not in written:
                written.append(beta_id)
        if beta_id in written or beta_id in created:
            write_json(root / "beta" / f"{beta_id}.json", profile)
            if beta_id not in written:
                written.append(beta_id)

    return {
        "written": sorted(written),
        "created": sorted(set(created)),
        "skipped": skipped,
        "n_written": len(set(written)),
    }


def build_join(
    book: dict[str, Any],
    sources_book: dict[str, Any],
    *,
    include_figures: bool = False,
    root: Path | None = None,
) -> dict[str, Any]:
    mines: dict[str, Any] = {}
    by_src = {row["mine_id"]: row for row in (sources_book.get("mines") or [])}
    site = root or ROOT
    for mid, rec in (book.get("mines") or {}).items():
        meta = dict(ISSUER_MAP.get(mid) or {})
        src = by_src.get(mid) or {}
        beta_id = rec.get("company_id") or src.get("beta_id") or meta.get("beta_id")
        asset_id = rec.get("asset_id") or src.get("asset_id") or meta.get("asset_id")
        omit = bool(rec.get("omit_figure") or meta.get("omit_figure") or src.get("omit_figure"))
        has_file = bool(beta_id and (site / "beta" / f"{beta_id}.json").exists())
        row: dict[str, Any] = {
            "mine_id": mid,
            "mine_name": rec.get("mine_name") or src.get("mine_name") or mid,
            "beta_id": beta_id,
            "asset_id": asset_id,
            "file": f"beta/{beta_id}.json" if has_file else None,
            "page": f"beta.html?id={beta_id}" if has_file else None,
            "omit_figure": omit,
            "source": rec.get("production_source") or src.get("url"),
            "source_title": rec.get("production_source_title") or src.get("title"),
            "as_of": rec.get("production_as_of") or src.get("as_of"),
            "blocker": rec.get("blocker") or src.get("blocker"),
        }
        if include_figures and not omit:
            comms = rec.get("commodities") or {}
            exported = {}
            for cid, c in comms.items():
                if c.get("value") is None:
                    continue
                exported[cid] = {"value": c["value"], "unit": c.get("unit") or ""}
                if c.get("quote"):
                    exported[cid]["quote"] = c["quote"]
            if exported:
                row["commodities"] = exported
        mines[mid] = {k: v for k, v in row.items() if v is not None or k in {"beta_id", "asset_id"}}
    n_fig = sum(1 for r in mines.values() if (r.get("commodities") or {}) and not r.get("omit_figure"))
    n_linked = sum(1 for r in mines.values() if r.get("beta_id") and not r.get("omit_figure"))
    n_blank = sum(1 for r in mines.values() if r.get("omit_figure") or r.get("blocker") or not (r.get("commodities") or {}))
    return {
        "schema": JOIN_SCHEMA,
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "year": YEAR,
        "disclaimer": FOOTNOTE,
        "unit_note": UNIT_NOTE,
        "built_from": book.get("built_from") or "qc.sqlite",
        "sources_file": "scripts/canada-mine-production-sources.json",
        "n_mines": len(mines),
        "n_linked": n_linked,
        "n_with_figure": n_fig,
        "n_blank": n_blank,
        "mines": mines,
    }


def validate_join(join: dict[str, Any], *, root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    if join.get("schema") != JOIN_SCHEMA:
        errors.append("bad join schema")
    if join.get("year") != YEAR:
        errors.append("join year must be 2025")
    mines = join.get("mines") or {}
    if len(mines) < 10:
        errors.append("join missing mines")
    banned = ("attr_koz", "production_2025", "koz", "tonnes")
    n_fig = 0
    for mid, row in mines.items():
        for key in banned:
            if key in row and row[key] not in (None, False):
                errors.append(f"{mid}: join must not store {key}")
        comms = row.get("commodities") or {}
        if "aueq" in comms or "gold-equivalent" in comms:
            errors.append(f"{mid}: AuEq on join export")
        gold = comms.get("gold")
        if gold and not row.get("omit_figure"):
            n_fig += 1
            if gold.get("unit") != "troy oz":
                errors.append(f"{mid}: gold export unit {gold.get('unit')}")
            if gold.get("value") is None or float(gold["value"]) <= 0:
                errors.append(f"{mid}: non-positive gold")
            if not (row.get("source") or "").startswith("http"):
                errors.append(f"{mid}: figure without URL")
        elif row.get("omit_figure") or row.get("blocker"):
            continue
        elif row.get("file"):
            path = root / row["file"]
            if path.exists() and row.get("asset_id"):
                profile = json.loads(path.read_text(encoding="utf-8"))
                fig = figure_for_table(profile, row["asset_id"], "gold")
                if fig:
                    n_fig += 1
    if n_fig < 5:
        errors.append(f"need several cited gold figures, have {n_fig}")
    # Guard existing Newmont shape.
    nem = root / "beta" / "newmont.json"
    if nem.exists():
        profile = json.loads(nem.read_text(encoding="utf-8"))
        rec = annual_2025(profile)
        ba = (rec or {}).get("by_asset") or {}
        if "brucejack" not in ba:
            errors.append("newmont 2025 missing brucejack")
        if "lihir" not in ba:
            errors.append("newmont 2025 lost non-Canada assets")
        bj = ba.get("brucejack") or {}
        if bj.get("attr_koz") not in (231, 231.0):
            errors.append("newmont brucejack attr_koz changed unexpectedly")
    return errors


def strip_commodities_overlay(payload: dict[str, Any]) -> int:
    """Remove parallel canada-only figures. National series stay."""
    n = 0
    groups = [payload.get("mines") or []]
    for comm in payload.get("commodities") or []:
        groups.append(comm.get("mines") or [])
    seen: set[int] = set()
    for group in groups:
        for mine in group:
            ident = id(mine)
            if ident in seen:
                continue
            seen.add(ident)
            for key in PROD_KEYS:
                if key in mine:
                    mine.pop(key, None)
                    n += 1
    payload.pop("mine_production", None)
    disc = payload.get("disclaimer") or ""
    extra = (
        " Mine-level 2025 production is stored in qc.sqlite and exported to "
        "canada/producer-join.json (and existing beta/<issuer>.json) from "
        "company filings where disclosed; StatCan/NRCan do not publish "
        "mine-level output. Blank is not zero."
    )
    # Collapse duplicated footnotes from earlier overlays.
    markers = (
        "Mine-level 2025 production is from company filings",
        "Mine-level 2025 production is from beta producer pages",
        "Mine-level 2025 production is stored in qc.sqlite",
    )
    changed = True
    while changed:
        changed = False
        for marker in markers:
            start = disc.find(" " + marker)
            if start < 0:
                start = disc.find(marker)
            if start < 0:
                continue
            rest = disc[start + (1 if disc[start] == " " else 0):]
            end = rest.find("Blank is not zero.")
            if end < 0:
                disc = disc[:start].rstrip()
            else:
                disc = (disc[:start] + rest[end + len("Blank is not zero."):]).strip()
            changed = True
            break
    payload["disclaimer"] = (disc.rstrip() + extra).strip()
    note = payload.get("unit_note") or ""
    for marker in (
        " Company reports of gold",
        " Beta gold follows",
        " Canada table reads",
    ):
        cut = note.find(marker)
        if cut > 0:
            note = note[:cut].rstrip()
    payload["unit_note"] = (note + " " + UNIT_NOTE).strip()
    return n


def attach_join_pointers(mines: list[dict[str, Any]], join: dict[str, Any]) -> int:
    by_id = join.get("mines") or {}
    hits = 0
    for mine in mines:
        rec = by_id.get(mine.get("id") or "")
        if not rec:
            continue
        if rec.get("beta_id"):
            mine["beta_id"] = rec["beta_id"]
            mine["beta_asset_id"] = rec.get("asset_id")
            if rec.get("page"):
                mine["beta_href"] = rec["page"]
            else:
                mine.pop("beta_href", None)
            hits += 1
        if rec.get("blocker"):
            mine["production_blocker"] = rec["blocker"]
        if rec.get("source"):
            mine["production_source"] = rec["source"]
        if rec.get("source_title"):
            mine["production_source_title"] = rec["source_title"]
    return hits
