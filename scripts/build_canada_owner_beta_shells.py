#!/usr/bin/env python3
"""Mint Vale-style beta producer shells for Map 900A Principal Mines owners.

Coverage + owner linking only. Does not invent ounces or tonnes. Does not
call Grok Build. Does not write qc.sqlite.

    python3 scripts/build_canada_owner_beta_shells.py
    python3 scripts/build_canada_owner_beta_shells.py --check

Reads canada/commodities.json Map 900A owners, maps subsidiaries onto
existing beta/<id>.json pages, and writes missing qc-issuer-profile-v1
shells (public mcap shape when a ticker is in insider-companies /
market-caps; otherwise a private stub). Also writes:

  scripts/canada-owner-aliases.json
  beta/canada-owners.json

and attaches beta_id / beta_href on the committed commodities book.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_canada_commodities as b

ALIASES_OUT = HERE / "canada-owner-aliases.json"
INDEX_OUT = ROOT / "beta" / "canada-owners.json"
COMMODITIES = ROOT / "canada" / "commodities.json"
INDEX_SCHEMA = "qc-beta-canada-owners-v1"
SKIP_BETA = {"issuers.json", "explorers.json", "mcap.json", "claims-publics.json", "canada-owners.json"}

# Parent / subsidiary strings → existing or minted beta id. Deterministic.
CURATED_ALIASES: dict[str, str] = {
    "Vale Canada Limited": "vale",
    "Glencore Canada Corporation": "glencore",
    "Agnico Eagle Mines Limited": "agnico-eagle",
    "Rio Tinto Canada Inc.": "rio-tinto",
    "Rio Tinto Iron and Titanium Canada Inc.": "rio-tinto",
    "Heidelberg Materials Canada Limited": "heidelberg-materials",
    "Compass Minerals Canada Corporation": "compass-minerals",
    "Impala Canada Ltd.": "impala-platinum-holdings",
    "De Beers Canada Inc.": "anglo-american",
    "Texada Quarrying Ltd. (Amrize AG)": "amrize",
    "Amrize Ltd.": "amrize",
    "Discovery Silver Corp.": "discovery-mining",
    "Hecla Mining Company": "hecla-mining",
    "McEwen Mining Inc.": "mcewen",
    "Hudbay Minerals Inc.": "hudbay-minerals",
    "Cameco Corporation": "cameco",
    "Nutrien Ltd.": "nutrien",
    "Orla Mining Ltd.": "orla-mining",
    "Magna Mining Inc.": "magna-mining",
    "Imperial Metals Corporation": "imperial-metals",
    "Gold Mountain Mining Corp.": "gold-mountain-mining",
    "Northern Graphite Corporation": "northern-graphite",
    "Progressive Planet Solutions Inc.": "progressive-planet",
    "Burgundy Diamond Mines": "burgundy-diamond",
    "ArcelorMittal S.A.": "arcelormittal",
    "CRH Canada Group Inc.": "crh",
    "Orano Canada Inc.": "orano",
    "Westmoreland Mining Holdings LLC": "westmoreland",
    "Tata Steel Minerals Canada Limited": "tata-steel-minerals",
    "Tacora Resources Inc.": "tacora",
    "Elevra Lithium Limited": "elevra-lithium",
    "Canadian Royalties Inc.": "canadian-royalties",
    "Iron Ore Company of Canada Inc.": "iron-ore-company-of-canada",
    "St. Marys Cement Inc.": "st-marys-cement",
    "St Marys CBM (Canada) Inc.": "st-marys-cement",
    "E.C. King Contracting Ltd. (Miller Paving Co.)": "miller-paving",
    "K+S Potash Canada GP": "k-s-potash-canada",
    "CertainTeed Gypsum Canada Inc.": "certainteed-gypsum",
    "Gold Bond Canada Ltd.": "gold-bond-canada",
    "Windsor Salt Ltd.": "windsor-salt",
    "Stone Canyon Industries Holding": "stone-canyon-industries",
    "Graymont Inc.": "graymont",
    "Carmeuse Lime (Canada) Limited": "carmeuse",
    "BURNCO Rock Products Ltd.": "burnco",
    "OMYA (Canada) Inc.": "omya",
    "Covia Canada Ltd.": "covia",
    "CGC Inc.": "cgc",
    "Magris Performance Materials Inc.": "magris",
    "Baffinland Iron Mines Corporation": "baffinland",
    "Sinomine Resource Grp Co. Ltd.": "sinomine",
    "Coalspur Mines Limited": "coalspur",
    "Conuma Coal Resources Ltd.": "conuma-coal",
    "CST Canada Coal Limited": "cst-canada-coal",
    "Dhilmar Ltd.": "dhilmar",
    "Trinity Performance Minerals": "trinity-performance-minerals",
    "Nova Construction Ltd.": "nova-construction",
    "Hammond River Holdings Ltd.": "hammond-river-holdings",
    "Brookville Manufacturing Company": "brookville-manufacturing",
    "Elmtree Resources Ltd.": "elmtree-resources",
    "Ciment Québec inc.": "ciment-quebec",
    "Carrière d’Acton Vale Ltée": "carriere-d-acton-vale",
    "Carrière d'Acton Vale Ltée": "carriere-d-acton-vale",
    "Béton Provincial Ltée": "beton-provincial",
    "Demix Agrégats": "demix-agregats",
    "Canadian Wollastonite": "canadian-wollastonite",
    "Owen Sound Ledgerock Ltd.": "owen-sound-ledgerock",
    "Boreal Agrominerals Inc.": "boreal-agrominerals",
    "ERCO Worldwide LP": "erco-worldwide",
    "Saskatchewan Mining and Minerals Inc.": "saskatchewan-mining-and-minerals",
    "Ward Chemical Incorporated": "ward-chemical",
    "Tiger Calcium Services Inc.": "tiger-calcium",
    "Baymag Inc.": "baymag",
    "Imperial Limestone Co. Ltd.": "imperial-limestone",
    "Fireside Minerals Ltd.": "fireside-minerals",
}

# Public tickers we are willing to attach (must exist in market-caps to fill cap).
CURATED_TICKERS: dict[str, list[str]] = {
    "amrize": ["AMRZ"],
    "arcelormittal": ["MT"],
    "compass-minerals": ["CMP"],
    "crh": ["CRH"],
    "imperial-metals": ["III.TO"],
    "gold-mountain-mining": ["GMTN.V"],
    "northern-graphite": ["NGC.V"],
    "progressive-planet": ["PLAN.V"],
    "burgundy-diamond": ["BDM.AX"],
    "heidelberg-materials": ["HDELY"],
}

# Single-token cores that must not auto-bind an unrelated issuer.
GENERIC_CORES = {
    "ROYALTIES", "GOLD", "SILVER", "ENERGY", "METALS", "MINERALS", "RESOURCES",
    "LITHIUM", "GRAPHITE", "POTASH", "CEMENT", "LIME", "SALT", "COAL", "IRON",
    "STEEL", "QUARRY", "MINING", "MINES", "COMPANY", "GROUP", "HOLDINGS",
    "CANADA", "CANADIAN", "IMPERIAL", "COMPASS", "NOVA", "TIGER", "DISCOVERY",
    "MOUNTAIN", "KING", "ORANGE",
}

LEGAL_STRIP = re.compile(
    r"\b(inc\.?|incorporated|ltd\.?|limited|llc|l\.l\.c\.?|corp\.?|corporation|"
    r"plc|s\.a\.?|n\.v\.?|co\.?|company|ulc|gp|lp|llp|ltee|limit[eé]e)\b",
    re.I,
)
CANADA_STRIP = re.compile(r"\b(canada|canadian|canadien(?:ne)?)\b", re.I)
PAREN = re.compile(r"\(([^)]+)\)")

BANNED_PROD = (
    "koz", "tonnes", "quantity", "production_t", "mine_tonnes",
    "attr_koz", "koz_100pct",
)


def utc_today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def existing_beta_ids(root: Path) -> set[str]:
    out = set()
    for path in (root / "beta").glob("*.json"):
        if path.name in SKIP_BETA:
            continue
        out.add(path.stem)
    return out


def owner_slug(name: str) -> str:
    cleaned = LEGAL_STRIP.sub(" ", name or "")
    cleaned = CANADA_STRIP.sub(" ", cleaned)
    cleaned = PAREN.sub(" ", cleaned)
    return b.slugify(cleaned) or b.slugify(name)


def unique_owner_parts(mines: list[dict[str, Any]]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for mine in mines:
        raw = mine.get("owners") or ""
        parts = b.split_owners(raw) or ([raw] if raw else [])
        for part in parts:
            if part and part not in seen:
                seen.add(part)
                out.append(part)
    return out


def mines_for_owner(mines: list[dict[str, Any]], owner: str, beta_id: str) -> list[dict[str, Any]]:
    hits = []
    for mine in mines:
        raw = mine.get("owners") or ""
        parts = b.split_owners(raw) or ([raw] if raw else [])
        if owner in parts or raw == owner:
            hits.append(mine)
            continue
        # parenthetical / alias already resolved to this id
        if any(CURATED_ALIASES.get(p) == beta_id for p in parts):
            hits.append(mine)
    return hits


def load_beta_catalog(root: Path) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}

    def ensure(cid: str) -> dict[str, Any]:
        return rows.setdefault(
            cid,
            {"id": cid, "names": set(), "tickers": set(), "file": f"beta/{cid}.json", "name": cid},
        )

    for rel in (
        "beta/issuers.json",
        "beta/explorers.json",
        "beta/mcap.json",
        "beta/claims-publics.json",
        "beta/canada-owners.json",
    ):
        path = root / rel
        if not path.exists():
            continue
        payload = load_json(path)
        for row in payload.get("issuers") or []:
            cid = row.get("id") or row.get("beta_id") or row.get("alias_of")
            if not cid:
                continue
            rec = ensure(cid)
            if row.get("name"):
                rec["names"].add(row["name"])
                rec["name"] = rec.get("name") if rec.get("name") != cid else row["name"]
            rec["names"].add(cid.replace("-", " "))
            rec["names"].update(row.get("names") or [])
            if row.get("file"):
                rec["file"] = row["file"]
            for tk in row.get("tickers") or []:
                rec["tickers"].add(str(tk))

    for path in (root / "beta").glob("*.json"):
        if path.name in SKIP_BETA:
            continue
        try:
            prof = load_json(path)
        except json.JSONDecodeError:
            continue
        cid = prof.get("id") or path.stem
        rec = ensure(cid)
        iss = prof.get("issuer") or {}
        if iss.get("name"):
            rec["names"].add(iss["name"])
            rec["name"] = iss["name"]
        if iss.get("short"):
            rec["names"].add(iss["short"])
        rec["file"] = f"beta/{path.name}"
        rec["profile"] = prof
        for tk in iss.get("tickers") or []:
            if isinstance(tk, dict) and tk.get("symbol"):
                rec["tickers"].add(tk["symbol"])
            elif tk:
                rec["tickers"].add(str(tk))
    return rows


def build_name_indexes(catalog: dict[str, dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    fold_idx: dict[str, str] = {}
    core_idx: dict[str, str] = {}
    core_ambig: set[str] = set()
    for cid, rec in catalog.items():
        names = set(rec.get("names") or [])
        names.add(cid.replace("-", " "))
        for name in names:
            if not name:
                continue
            fold_idx.setdefault(b.fold(name), cid)
            ck = b.core_key(name)
            if not ck or ck in GENERIC_CORES:
                continue
            if ck in core_idx and core_idx[ck] != cid:
                core_ambig.add(ck)
            else:
                core_idx.setdefault(ck, cid)
    for ck in core_ambig:
        core_idx.pop(ck, None)
    return fold_idx, core_idx


def load_insider_index(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "insider-companies.json"
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return out
    for row in load_json(path).get("companies") or []:
        name = row.get("name") or ""
        if not name:
            continue
        out.setdefault(b.fold(name), row)
        ck = b.core_key(name)
        if ck and ck not in GENERIC_CORES:
            out.setdefault(ck, row)
    return out


def load_market_caps(root: Path) -> dict[str, dict[str, Any]]:
    path = root / "market-caps.json"
    if not path.exists():
        return {}
    data = load_json(path)
    return data.get("tickers") or {}


def ticker_exchange(symbol: str) -> str:
    if symbol.endswith(".TO"):
        return "TSX"
    if symbol.endswith(".V"):
        return "TSXV"
    if symbol.endswith(".AX"):
        return "ASX"
    if symbol.endswith(".L"):
        return "LSE"
    if symbol.endswith(".DE") or symbol.endswith(".PA"):
        return "EU"
    return "US"


def ticker_rows(symbols: list[str], caps: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for sym in symbols:
        rows.append(
            {
                "symbol": sym,
                "exchange": ticker_exchange(sym),
                "chart": f"insider-ticker.html?t={sym}",
            }
        )
        _ = caps.get(sym)
    return rows


def pick_cap(symbols: list[str], caps: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    for sym in symbols:
        row = caps.get(sym)
        if row and (row.get("cap") or row.get("px")):
            return {"symbol": sym, **row}
    return None


def map900a_assets(mines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    assets = []
    seen: set[str] = set()
    for mine in mines:
        mid = mine.get("id") or b.slugify(mine.get("name") or "")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        products = mine.get("products") or []
        commodity = products[0] if products else (mine.get("products_raw") or mine.get("layer") or "other")
        assets.append(
            {
                "id": mid,
                "name": mine.get("name") or mid,
                "stage": "producing",
                "in_production": True,
                "commodity": commodity,
                "country": "Canada",
                "region": mine.get("province") or None,
                "nearest": mine.get("city") or None,
                "location_note": mine.get("location") or "",
                "lat": mine.get("lat"),
                "lon": mine.get("lon"),
                "operator": mine.get("owners") or None,
                "mine_type": mine.get("layer") or None,
                "note": "Map 900A principal operation. No mine-level tonnes published.",
            }
        )
    return assets


def display_name(owner: str, beta_id: str, catalog: dict[str, dict[str, Any]]) -> str:
    rec = catalog.get(beta_id) or {}
    iss = ((rec.get("profile") or {}).get("issuer") or {})
    name = iss.get("name") or ""
    if name and " ." not in name and name != beta_id:
        return name
    return owner


def new_shell_payload(
    *,
    beta_id: str,
    owner: str,
    name: str,
    mines: list[dict[str, Any]],
    symbols: list[str],
    caps: dict[str, dict[str, Any]],
    insider: dict[str, Any] | None,
) -> dict[str, Any]:
    cap = pick_cap(symbols, caps)
    public = bool(symbols and (cap or insider))
    tickers = ticker_rows(symbols, caps) if symbols else []
    commodity = "other"
    country = "Canada"
    watch_type = None
    if insider:
        commodity = (insider.get("commodity") or commodity).lower()
        country = insider.get("country") or country
        watch_type = insider.get("type")
        if not tickers:
            tickers = ticker_rows(list(insider.get("all") or []), caps)
            cap = cap or pick_cap(list(insider.get("all") or []), caps)
    assets = map900a_assets(mines)
    products = []
    for mine in mines:
        products.extend(mine.get("products") or [])
    if products:
        commodity = products[0]
    disclaimer = (
        "Insiders-watchlist shell from insider-companies.json + market-caps.json. "
        "No production or reserves until a filing pass. Do not invent ounces. "
        "Not S&P. Not investment advice."
        if public
        else (
            "Map 900A owner stub. Private or no ticker in the QC watchlist / "
            "market-caps book. Assets are Map 900A names and locations only. "
            "No production or reserves. Do not invent ounces. Not S&P. "
            "Not investment advice."
        )
    )
    kpis: dict[str, Any] = {
        "attr_koz_2025": None,
        "attr_pp_koz": None,
        "attr_mi_incl_koz": None,
        "attr_inf_koz": None,
    }
    if cap:
        kpis.update(
            {
                "market_cap": cap.get("cap"),
                "market_cap_usd": cap.get("cap") if cap.get("cur") == "USD" else None,
                "market_cap_cur": cap.get("cur"),
                "shares": cap.get("shares"),
                "px": cap.get("px"),
                "price_asof": cap.get("asof"),
            }
        )
    layer = "mcap-watchlist" if public else "canada-owner"
    return {
        "schema": "qc-issuer-profile-v1",
        "id": beta_id,
        "kind": "producer",
        "stage": "producing",
        "generated": utc_today(),
        "disclaimer": disclaimer,
        "units": {"mass": "kt", "gold": "koz", "grade": "g/t Au", "currency": "USD"},
        "issuer": {
            "name": name,
            "short": name,
            "tickers": tickers,
            "hq": country,
            "commodity": commodity,
            "country": country,
            "watchlist_type": watch_type,
        },
        "method": {
            "production_basis": "Not gathered. Do not invent ounces.",
            "resources_basis": "Not gathered. Do not invent ounces.",
        },
        "kpis": kpis,
        "guidance_2026": {
            "basis": "none — canada owner shell",
            "total_koz": None,
        },
        "production": [],
        "assets": assets,
        "reserves_resources": {"as_of": None, "projects": []},
        "sources": [
            {
                "title": "NRCan Map 900A principal producing sites",
                "date": utc_today(),
                "what": "Canadian operation name, location, commodity. No mine-level tonnes.",
            }
        ],
        "meta": {
            "layer": layer,
            "watchlist": "canada-map-900a" if not public else "insiders-mcap",
            "filing_backed": False,
            "map_900a_assets": True,
            "alerts": [],
        },
    }


def can_patch_assets(profile: dict[str, Any]) -> bool:
    meta = profile.get("meta") or {}
    if meta.get("filing_backed") is True:
        return False
    if profile.get("production"):
        return False
    assets = profile.get("assets") or []
    if not assets:
        return True
    return bool(meta.get("map_900a_assets")) and all(
        (a.get("note") or "").startswith("Map 900A") for a in assets
    )


def resolve_owner(
    owner: str,
    *,
    curated: dict[str, str],
    fold_idx: dict[str, str],
    core_idx: dict[str, str],
    catalog: dict[str, dict[str, Any]],
    insider_idx: dict[str, dict[str, Any]],
) -> tuple[str | None, str]:
    folded = b.fold(owner)
    ck = b.core_key(owner)
    if folded in curated:
        return curated[folded], "alias-fold"
    if ck and ck in curated:
        return curated[ck], "alias-core"
    if folded in fold_idx:
        return fold_idx[folded], "catalog-fold"
    if ck and ck not in GENERIC_CORES and ck in core_idx:
        return core_idx[ck], "catalog-core"
    insider = insider_idx.get(folded) or (insider_idx.get(ck) if ck and ck not in GENERIC_CORES else None)
    if insider:
        tks = set(insider.get("all") or [])
        for cid, rec in catalog.items():
            if rec.get("tickers") and rec["tickers"] & tks:
                return cid, "insider-ticker"
    return None, ""


def plan_owners(root: Path, mines: list[dict[str, Any]]) -> dict[str, Any]:
    catalog = load_beta_catalog(root)
    fold_idx, core_idx = build_name_indexes(catalog)
    insider_idx = load_insider_index(root)
    caps = load_market_caps(root)
    curated_fold = {}
    for owner, cid in CURATED_ALIASES.items():
        curated_fold[b.fold(owner)] = cid
        ck = b.core_key(owner)
        if ck:
            curated_fold[ck] = cid

    taken = existing_beta_ids(root)
    owners = unique_owner_parts(mines)
    rows: list[dict[str, Any]] = []
    owner_to_id: dict[str, str] = {}

    for owner in owners:
        cid, how = resolve_owner(
            owner,
            curated=curated_fold,
            fold_idx=fold_idx,
            core_idx=core_idx,
            catalog=catalog,
            insider_idx=insider_idx,
        )
        mint = False
        if not cid:
            cid = owner_slug(owner)
            if cid in taken:
                # Existing page is a different company — keep a longer slug.
                cid = b.slugify(LEGAL_STRIP.sub(" ", owner))
            if cid in taken and cid not in {CURATED_ALIASES.get(owner)}:
                cid = b.slugify(owner)
            how = "mint-stub"
            mint = cid not in taken
        elif cid not in taken and not (root / "beta" / f"{cid}.json").exists():
            mint = True
            how = how + "+mint"
        owner_to_id[owner] = cid
        insider = insider_idx.get(b.fold(owner)) or insider_idx.get(b.core_key(owner) or "")
        symbols = list(CURATED_TICKERS.get(cid) or [])
        if insider and not symbols:
            symbols = list(insider.get("all") or [])
        rec = catalog.get(cid) or {}
        if rec.get("tickers") and not symbols:
            symbols = sorted(rec["tickers"])
        rows.append(
            {
                "owner": owner,
                "beta_id": cid,
                "how": how,
                "mint": mint,
                "exists": (root / "beta" / f"{cid}.json").exists(),
                "name": display_name(owner, cid, catalog),
                "symbols": symbols,
                "insider": insider,
            }
        )
        taken.add(cid)

    return {
        "rows": rows,
        "owner_to_id": owner_to_id,
        "catalog": catalog,
        "caps": caps,
        "mines": mines,
    }


def alias_payload(owner_to_id: dict[str, str]) -> dict[str, Any]:
    aliases = [
        {"owner": owner, "company_id": cid}
        for owner, cid in sorted(owner_to_id.items(), key=lambda kv: kv[0].lower())
    ]
    return {
        "schema": "qc-canada-owner-aliases-v1",
        "note": (
            "Deterministic Map 900A operator → beta id. Includes Canada "
            "subsidiaries (Vale Canada Limited → vale) and minted stubs. "
            "Do not put secrets here."
        ),
        "aliases": aliases,
    }


def index_payload(rows: list[dict[str, Any]], minted: list[str]) -> dict[str, Any]:
    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        cid = row["beta_id"]
        rec = by_id.setdefault(
            cid,
            {
                "id": cid,
                "name": row["name"],
                "file": f"beta/{cid}.json",
                "kind": "producer",
                "stage": "producing",
                "names": [],
                "tickers": list(row.get("symbols") or []),
                "shell": cid in minted or (row.get("mint") and not row.get("exists")),
            },
        )
        if row["owner"] not in rec["names"]:
            rec["names"].append(row["owner"])
        if row["name"] and row["name"] not in rec["names"]:
            rec["names"].append(row["name"])
    issuers = [by_id[k] for k in sorted(by_id)]
    return {
        "schema": INDEX_SCHEMA,
        "generated": utc_now(),
        "disclaimer": (
            "Canada Principal Mines owner index for beta.html?id= deep links. "
            "Shells have Map 900A names/locations only — no invented ounces."
        ),
        "n": len(issuers),
        "shells": sum(1 for r in issuers if r.get("shell")),
        "issuers": issuers,
    }


def apply_beta_fields(payload: dict[str, Any], owner_to_id: dict[str, str]) -> int:
    hits = 0

    def one(mine: dict[str, Any]) -> None:
        nonlocal hits
        raw = mine.get("owners") or ""
        parts = b.split_owners(raw) or ([raw] if raw else [])
        links = []
        first = mine.get("beta_id")
        for part in parts:
            cid = owner_to_id.get(part)
            href = f"beta.html?id={cid}" if cid else None
            links.append({"name": part, "beta_id": cid, "href": href})
            if cid and not first:
                first = cid
        if first:
            if not mine.get("beta_id"):
                mine["beta_id"] = first
            if not mine.get("beta_href"):
                mine["beta_href"] = f"beta.html?id={first}"
            hits += 1
        if links:
            mine["owner_links"] = links

    for mine in payload.get("mines") or []:
        one(mine)
    for comm in payload.get("commodities") or []:
        for mine in comm.get("mines") or []:
            one(mine)
    payload["n_beta_linked"] = sum(1 for m in (payload.get("mines") or []) if m.get("beta_href"))
    return hits


def patch_join_pages(root: Path, minted_ids: set[str]) -> int:
    path = root / "canada" / "producer-join.json"
    if not path.exists():
        return 0
    book = load_json(path)
    n = 0
    for rec in (book.get("mines") or {}).values():
        cid = rec.get("beta_id")
        if not cid:
            continue
        if not (root / "beta" / f"{cid}.json").exists():
            continue
        want_file = f"beta/{cid}.json"
        want_page = f"beta.html?id={cid}"
        if rec.get("file") != want_file or rec.get("page") != want_page:
            rec["file"] = want_file
            rec["page"] = want_page
            n += 1
    if n:
        write_json(path, book)
    return n


def write_shells(root: Path, plan: dict[str, Any]) -> list[str]:
    minted: list[str] = []
    mines = plan["mines"]
    caps = plan["caps"]
    by_id_mines: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in plan["rows"]:
        for mine in mines:
            raw = mine.get("owners") or ""
            parts = b.split_owners(raw) or ([raw] if raw else [])
            if row["owner"] in parts or raw == row["owner"]:
                by_id_mines[row["beta_id"]].append(mine)

    for row in plan["rows"]:
        cid = row["beta_id"]
        path = root / "beta" / f"{cid}.json"
        owner_mines = by_id_mines.get(cid) or []
        if row["mint"] and not path.exists():
            payload = new_shell_payload(
                beta_id=cid,
                owner=row["owner"],
                name=row["name"],
                mines=owner_mines,
                symbols=row.get("symbols") or [],
                caps=caps,
                insider=row.get("insider"),
            )
            write_json(path, payload)
            minted.append(cid)
            continue
        if not path.exists():
            continue
        try:
            prof = load_json(path)
        except json.JSONDecodeError:
            continue
        if can_patch_assets(prof) and owner_mines:
            assets = map900a_assets(owner_mines)
            if assets and (not prof.get("assets") or (prof.get("meta") or {}).get("map_900a_assets")):
                existing_ids = {a.get("id") for a in (prof.get("assets") or [])}
                merged = list(prof.get("assets") or [])
                for asset in assets:
                    if asset["id"] not in existing_ids:
                        merged.append(asset)
                        existing_ids.add(asset["id"])
                if merged != (prof.get("assets") or []):
                    prof["assets"] = merged
                    meta = prof.setdefault("meta", {})
                    meta["map_900a_assets"] = True
                    write_json(path, prof)
    return minted


def unlinked_owners(mines: list[dict[str, Any]], owner_to_id: dict[str, str], root: Path) -> list[str]:
    missing = []
    for owner in unique_owner_parts(mines):
        cid = owner_to_id.get(owner)
        if not cid or not (root / "beta" / f"{cid}.json").exists():
            missing.append(owner)
    return missing


def check(root: Path) -> list[str]:
    errors: list[str] = []
    comm_path = root / "canada" / "commodities.json"
    if not comm_path.exists():
        return ["canada/commodities.json missing"]
    payload = load_json(comm_path)
    mines = payload.get("mines") or []
    if not mines:
        return ["no Map 900A mines"]
    aliases = load_json(root / "scripts" / "canada-owner-aliases.json")
    if aliases.get("schema") != "qc-canada-owner-aliases-v1":
        errors.append("bad aliases schema")
    alias_map = {
        (row.get("owner") or ""): (row.get("company_id") or "")
        for row in aliases.get("aliases") or []
    }
    if alias_map.get("Vale Canada Limited") != "vale":
        errors.append("Vale Canada Limited must alias to vale")
    index_path = root / "beta" / "canada-owners.json"
    if not index_path.exists():
        errors.append("beta/canada-owners.json missing")
    else:
        index = load_json(index_path)
        if index.get("schema") != INDEX_SCHEMA:
            errors.append("bad canada-owners schema")
    vale_mines = [m for m in mines if m.get("owners") == "Vale Canada Limited"]
    if not vale_mines:
        errors.append("Vale Canada Limited missing from commodities")
    for mine in vale_mines:
        if mine.get("beta_id") != "vale" or mine.get("beta_href") != "beta.html?id=vale":
            errors.append("Vale Canada Limited must link to beta.html?id=vale")
            break
        if mine.get("claims_company") == "canada-nickel":
            errors.append("Vale Canada must not link to canada-nickel")
    missing = []
    for owner in unique_owner_parts(mines):
        cid = alias_map.get(owner)
        if not cid or not (root / "beta" / f"{cid}.json").exists():
            missing.append(owner)
            continue
        if cid == "canada-nickel" and "Vale" in owner:
            errors.append("Vale aliased to canada-nickel")
    if missing:
        errors.append("unlinked owners: " + ", ".join(missing[:12]))
    # minted shells must not invent ounces
    for path in (root / "beta").glob("*.json"):
        if path.name in SKIP_BETA:
            continue
        try:
            prof = load_json(path)
        except json.JSONDecodeError:
            errors.append(f"bad json {path.name}")
            continue
        meta = prof.get("meta") or {}
        if meta.get("layer") not in {"canada-owner", "mcap-watchlist", "mcap-top200"}:
            continue
        if meta.get("map_900a_assets") or meta.get("watchlist") == "canada-map-900a":
            if prof.get("production"):
                errors.append(f"{path.stem}: canada owner shell has production")
            for key in BANNED_PROD:
                blob = json.dumps(prof.get("assets") or [])
                if f'"{key}"' in blob and "note" not in key:
                    # allow nothing — assets must not carry koz
                    if any(key in (a or {}) for a in (prof.get("assets") or [])):
                        errors.append(f"{path.stem}: invented {key} on assets")
    return errors


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--check", action="store_true")
    args = p.parse_args(argv)
    root = args.root
    if args.check:
        errors = check(root)
        if errors:
            print("canada owner beta shells FAIL: " + "; ".join(errors), file=sys.stderr)
            return 1
        payload = load_json(root / "canada" / "commodities.json")
        aliases = load_json(root / "scripts" / "canada-owner-aliases.json")
        print(
            f"canada owner beta shells ok aliases={len(aliases.get('aliases') or [])} "
            f"n_beta_linked={payload.get('n_beta_linked')} mines={payload.get('n_mines')}"
        )
        return 0

    comm = load_json(root / "canada" / "commodities.json")
    mines = comm.get("mines") or []
    plan = plan_owners(root, mines)
    minted = write_shells(root, plan)
    write_json(root / "scripts" / "canada-owner-aliases.json", alias_payload(plan["owner_to_id"]))
    write_json(root / "beta" / "canada-owners.json", index_payload(plan["rows"], minted))
    apply_beta_fields(comm, plan["owner_to_id"])
    write_json(root / "canada" / "commodities.json", comm)
    join_n = patch_join_pages(root, set(minted))
    leftover = unlinked_owners(mines, plan["owner_to_id"], root)
    print(
        f"canada owner shells aliases={len(plan['owner_to_id'])} "
        f"minted={len(minted)} patched_join={join_n} "
        f"n_beta_linked={comm.get('n_beta_linked')} leftover={len(leftover)}"
    )
    if minted:
        print("minted: " + ", ".join(sorted(set(minted))))
    if leftover:
        print("unlinked: " + ", ".join(leftover), file=sys.stderr)
        return 1
    errors = check(root)
    if errors:
        print("validate: " + "; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
