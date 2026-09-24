#!/usr/bin/env python3
"""Index publicly traded claims-map companies onto beta.html deep links.

Reads claims/companies.json plus existing beta catalogs / shells. Writes
beta/claims-publics.json so `beta.html?id=<claims-id>` resolves without a
parallel id scheme.

Does **not** regenerate filing-backed or mcap shells. The only gap on the
live catalog is `troilus-mining` → existing `beta/troilus.json`.

Does not crawl provinces, does not touch qc.sqlite, does not fetch extracts.

    python3 scripts/build_claims_beta_profiles.py
    python3 scripts/build_claims_beta_profiles.py --check
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from qc_io import atomic_write_json  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "claims" / "companies.json"
OVERVIEW = ROOT / "claims" / "overview.geojson"
OUT = ROOT / "beta" / "claims-publics.json"
BETA = ROOT / "beta"

# Claims catalog id → existing beta profile id. Do not invent a second ounce book.
ALIASES = {
    "troilus-mining": "troilus",
}

SKIP_BETA = {"issuers.json", "explorers.json", "mcap.json", "claims-publics.json"}
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
SUFFIX_STRIP = re.compile(r"-(mining|mines|gold|resources|minerals)$")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def slugify(name: str) -> str:
    s = (name or "").translate(ACCENT).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unknown"


def write_json(path: Path, payload: Any) -> None:
    atomic_write_json(path, payload)


def extract_symbols(rec: dict) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in rec.get("tickers") or []:
        if isinstance(item, dict):
            sym = item.get("symbol") or item.get("ticker")
        else:
            sym = item
        if not sym:
            continue
        s = str(sym).strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    issuer = rec.get("issuer") or {}
    for item in issuer.get("tickers") or []:
        if isinstance(item, dict):
            sym = item.get("symbol")
        else:
            sym = item
        if not sym:
            continue
        s = str(sym).strip().upper()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def beta_file_index(root: Path) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for rel in ("beta/issuers.json", "beta/explorers.json", "beta/mcap.json"):
        path = root / rel
        if not path.exists():
            continue
        data = load_json(path)
        source = Path(rel).stem
        for rec in data.get("issuers") or []:
            cid = rec.get("id")
            if not cid:
                continue
            row = by_id.setdefault(
                cid,
                {
                    "id": cid,
                    "name": rec.get("name"),
                    "file": rec.get("file") or f"beta/{cid}.json",
                    "tickers": [],
                    "kind": rec.get("kind"),
                    "stage": rec.get("stage"),
                    "filing_backed": rec.get("filing_backed"),
                    "in_issuers": False,
                    "in_explorers": False,
                    "in_mcap": False,
                },
            )
            if source == "issuers":
                row["in_issuers"] = True
                row["kind"] = row["kind"] or "producer"
            elif source == "explorers":
                row["in_explorers"] = True
                row["kind"] = row["kind"] or rec.get("kind") or "explorer"
            elif source == "mcap":
                row["in_mcap"] = True
            if rec.get("name"):
                row["name"] = rec.get("name")
            if rec.get("file"):
                row["file"] = rec["file"]
            for t in rec.get("tickers") or []:
                t = str(t).upper()
                if t not in row["tickers"]:
                    row["tickers"].append(t)
            if rec.get("filing_backed") is True:
                row["filing_backed"] = True
            elif rec.get("filing_backed") is False and row["filing_backed"] is None:
                row["filing_backed"] = False
    for prof in (root / "beta").glob("*.json"):
        if prof.name in SKIP_BETA:
            continue
        try:
            data = load_json(prof)
        except (OSError, json.JSONDecodeError):
            continue
        cid = data.get("id") or prof.stem
        issuer = data.get("issuer") or {}
        tks = extract_symbols(data)
        row = by_id.setdefault(
            cid,
            {
                "id": cid,
                "name": issuer.get("short") or issuer.get("name") or cid,
                "file": f"beta/{prof.name}",
                "tickers": [],
                "kind": data.get("kind"),
                "stage": data.get("stage"),
                "filing_backed": None,
                "in_issuers": False,
                "in_explorers": False,
                "in_mcap": False,
            },
        )
        row["file"] = f"beta/{prof.name}"
        if issuer.get("short") or issuer.get("name"):
            row["name"] = issuer.get("short") or issuer.get("name")
        if data.get("kind"):
            row["kind"] = data["kind"]
        if data.get("stage"):
            row["stage"] = data["stage"]
        for t in tks:
            if t not in row["tickers"]:
                row["tickers"].append(t)
        meta = data.get("meta") or {}
        if meta.get("filing_backed") is True or (data.get("assets") and data.get("reserves_resources", {}).get("projects")):
            if meta.get("layer") in {"mcap-watchlist", "claims-public"}:
                row["filing_backed"] = bool(meta.get("filing_backed"))
            else:
                row["filing_backed"] = True if meta.get("filing_backed") is not False else False
        elif meta.get("filing_backed") is False:
            row["filing_backed"] = False
    return by_id


def overview_ids(root: Path) -> set[str]:
    path = root / "claims" / "overview.geojson"
    if not path.exists():
        return set()
    data = load_json(path)
    out = set()
    for feat in data.get("features") or []:
        cid = (feat.get("properties") or {}).get("company_id")
        if cid:
            out.add(cid)
    return out


def resolve_beta_id(cid: str, beta: dict[str, dict]) -> tuple[str | None, str | None]:
    """Return (beta_id, alias_note)."""
    if cid in beta:
        return cid, None
    if cid in ALIASES and ALIASES[cid] in beta:
        return ALIASES[cid], f"alias {cid} → {ALIASES[cid]}"
    stripped = SUFFIX_STRIP.sub("", cid)
    if stripped and stripped != cid and stripped in beta:
        return stripped, f"alias {cid} → {stripped}"
    return None, None


def catalog_bbox(rec: dict) -> list[float] | None:
    for key in ("bbox", "quebec_bbox", "ontario_bbox", "bc_bbox"):
        box = rec.get(key)
        if isinstance(box, list) and len(box) == 4 and all(isinstance(n, (int, float)) for n in box):
            return [float(n) for n in box]
    return None


def merge_boxes(boxes: list[list[float]]) -> list[float] | None:
    if not boxes:
        return None
    minx = min(b[0] for b in boxes)
    miny = min(b[1] for b in boxes)
    maxx = max(b[2] for b in boxes)
    maxy = max(b[3] for b in boxes)
    return [minx, miny, maxx, maxy]


def classify_company(rec: dict, beta: dict[str, dict], ov: set[str]) -> dict:
    cid = rec["id"]
    display = rec.get("holder") or (rec.get("names") or [cid])[0]
    beta_id, alias_note = resolve_beta_id(cid, beta)
    hit = beta.get(beta_id) if beta_id else None
    tickers = list((hit or {}).get("tickers") or [])
    file = (hit or {}).get("file") if hit else None
    kind = (hit or {}).get("kind")
    if not kind:
        kind = "producer" if (hit and hit.get("in_issuers")) else "explorer"
    filing = (hit or {}).get("filing_backed")
    if filing is None:
        filing = bool(hit and (hit.get("in_issuers") or hit.get("in_explorers")))
    boxes = []
    for key in ("bbox", "quebec_bbox", "ontario_bbox", "bc_bbox"):
        box = rec.get(key)
        if isinstance(box, list) and len(box) == 4:
            boxes.append([float(n) for n in box])
    status = "matched" if hit and tickers and file else ("missing-ticker" if not tickers else "missing-shell")
    if hit and tickers and file:
        status = "matched"
    elif hit and file and not tickers:
        status = "missing-ticker"
    elif not hit:
        status = "missing-shell"
    layer = "producers" if (hit and hit.get("in_issuers")) else ("explorers" if (hit and hit.get("in_explorers")) else "claims")
    return {
        "status": status,
        "id": cid,
        "beta_id": beta_id,
        "alias_of": beta_id if beta_id and beta_id != cid else None,
        "name": (hit or {}).get("name") or display,
        "file": file,
        "tickers": tickers,
        "kind": kind,
        "stage": (hit or {}).get("stage"),
        "layer": layer,
        "in_issuers": bool(hit and hit.get("in_issuers")),
        "in_explorers": bool(hit and hit.get("in_explorers")),
        "filing_backed": bool(filing),
        "claim_count": rec.get("claim_count") or 0,
        "quebec_count": rec.get("quebec_count"),
        "ontario_count": rec.get("ontario_count"),
        "bc_count": rec.get("bc_count"),
        "has_overview": cid in ov,
        "has_mines": bool(rec.get("mines")),
        "bbox": merge_boxes(boxes),
        "color": rec.get("color") or "#e8b040",
        "note": alias_note or "",
    }


def build_index(root: Path) -> dict[str, Any]:
    claims = load_json(root / "claims" / "companies.json")
    beta = beta_file_index(root)
    ov = overview_ids(root)
    rows = [classify_company(rec, beta, ov) for rec in claims.get("companies") or []]
    rows.sort(key=lambda r: r["id"])
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    issuers = []
    for row in rows:
        issuers.append(
            {
                "id": row["id"],
                "beta_id": row["beta_id"] or row["id"],
                "alias_of": row["alias_of"],
                "name": row["name"],
                "file": row["file"] or f"beta/{row['id']}.json",
                "tickers": row["tickers"],
                "kind": row["kind"],
                "stage": row["stage"],
                "layer": row["layer"],
                "in_issuers": row["in_issuers"],
                "in_explorers": row["in_explorers"],
                "filing_backed": row["filing_backed"],
                "claim_count": row["claim_count"],
                "has_overview": row["has_overview"],
                "has_mines": row["has_mines"],
                "bbox": row["bbox"],
                "color": row["color"],
                "note": row["note"] or None,
            }
        )
    return {
        "schema": "qc-beta-claims-publics-v1",
        "generated": utc_now(),
        "disclaimer": (
            "Claims-map publics using claims/companies.json ids. Deep link "
            "beta.html?id=<claims-id>. Map preview uses claims/overview.geojson "
            "cells + catalog mines — not per-company extracts. Shells already on "
            "disk are reused. Not legal title. Not investment advice."
        ),
        "universe_note": (
            "Every claims/companies.json issuer that resolves to a beta ticker "
            "and profile file. troilus-mining aliases to the filing-backed "
            "troilus shell. Do not invent ounces."
        ),
        "n": len(issuers),
        "counts": counts,
        "aliases": dict(ALIASES),
        "issuers": issuers,
    }


def new_shell_payload(row: dict, rec: dict) -> dict:
    """Minimal claims-public shell. No invented ounces."""
    tks = []
    for sym in row.get("tickers") or []:
        exch = "TSX" if str(sym).endswith(".TO") else ("TSXV" if str(sym).endswith(".V") else "US")
        chart_sym = str(sym).split(".")[0]
        tks.append(
            {
                "symbol": sym,
                "exchange": exch,
                "chart": f"ticker.html?t={chart_sym}",
            }
        )
    assets = []
    for mine in rec.get("mines") or []:
        if not mine.get("id"):
            continue
        assets.append(
            {
                "id": mine.get("id"),
                "name": mine.get("name") or mine.get("id"),
                "stage": "unknown",
                "in_production": False,
                "country": mine.get("country"),
                "region": mine.get("region"),
                "lat": mine.get("lat"),
                "lon": mine.get("lon"),
                "location_note": mine.get("note") or "",
            }
        )
    return {
        "schema": "qc-issuer-profile-v1",
        "id": row["id"],
        "kind": row.get("kind") or "explorer",
        "stage": row.get("stage") or "exploration",
        "generated": utc_now()[:10],
        "disclaimer": (
            "Claims-map shell from claims/companies.json. No production or "
            "reserves until a filing pass. Do not invent ounces. Not S&P. "
            "Not investment advice."
        ),
        "units": {"mass": "kt", "gold": "koz", "grade": "g/t Au", "currency": "USD"},
        "issuer": {
            "name": row.get("name") or row["id"],
            "short": row.get("name") or row["id"],
            "tickers": tks,
            "commodity": "gold",
        },
        "method": {
            "production_basis": "Not gathered. Do not invent ounces.",
            "resources_basis": "Not gathered. Do not invent ounces.",
        },
        "kpis": {
            "attr_koz_2025": None,
            "attr_pp_koz": None,
            "attr_mi_incl_koz": None,
            "attr_inf_koz": None,
        },
        "guidance_2026": {"basis": "none — claims-public shell", "total_koz": None},
        "production": [],
        "assets": assets,
        "reserves_resources": {"as_of": None, "projects": []},
        "sources": [
            {
                "title": "Quantity Capital claims/companies.json",
                "date": utc_now()[:10],
                "what": "Claims-map catalog id, ticker from beta catalogs, optional catalog mines",
            }
        ],
        "meta": {
            "layer": "claims-public",
            "watchlist": "claims-map",
            "filing_backed": False,
            "alerts": [],
        },
    }


def write_missing_shells(root: Path, rows: list[dict], claims_recs: dict[str, dict]) -> list[str]:
    written = []
    for row in rows:
        if row["status"] != "missing-shell":
            continue
        if not row.get("tickers"):
            continue
        path = root / "beta" / f"{row['id']}.json"
        if path.exists():
            continue
        rec = claims_recs.get(row["id"]) or {}
        write_json(path, new_shell_payload(row, rec))
        written.append(row["id"])
    return written


def check_index(root: Path, payload: dict) -> list[str]:
    errors = []
    path = root / "beta" / "claims-publics.json"
    if not path.exists():
        return ["beta/claims-publics.json missing"]
    committed = load_json(path)
    if committed.get("schema") != "qc-beta-claims-publics-v1":
        errors.append("bad schema")
    claims = load_json(root / "claims" / "companies.json")
    catalog_ids = [c["id"] for c in claims.get("companies") or []]
    got = [r["id"] for r in committed.get("issuers") or []]
    if got != sorted(catalog_ids):
        errors.append(f"index ids != claims catalog ({len(got)} vs {len(catalog_ids)})")
    by_id = {r["id"]: r for r in committed.get("issuers") or []}
    for cid in ("iamgold", "probe-gold", "g2-goldfields", "troilus-mining", "kenorland-minerals"):
        if cid not in by_id:
            errors.append(f"missing {cid}")
    tro = by_id.get("troilus-mining") or {}
    if tro.get("alias_of") != "troilus" or tro.get("file") != "beta/troilus.json":
        errors.append("troilus-mining must alias to beta/troilus.json")
    if not (by_id.get("probe-gold") or {}).get("file", "").endswith("probe-gold.json"):
        errors.append("probe-gold must reuse beta/probe-gold.json")
    if not (by_id.get("iamgold") or {}).get("file", "").endswith("iamgold.json"):
        errors.append("iamgold must reuse beta/iamgold.json")
    missing_file = []
    for row in committed.get("issuers") or []:
        rel = row.get("file") or ""
        if not rel or not (root / rel).exists():
            missing_file.append(row["id"])
        if not row.get("tickers"):
            errors.append(f"{row['id']}: no tickers")
    if missing_file:
        errors.append("missing profile files: " + ", ".join(missing_file[:12]))
    rebuilt = build_index(root)
    rebuilt_ids = [(r["id"], r.get("file"), r.get("alias_of")) for r in rebuilt["issuers"]]
    committed_ids = [(r["id"], r.get("file"), r.get("alias_of")) for r in committed.get("issuers") or []]
    if rebuilt_ids != committed_ids:
        errors.append("committed claims-publics.json stale — rerun build_claims_beta_profiles.py")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Index claims publics for beta.html deep links")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Validate committed index; do not write")
    parser.add_argument("--write-shells", action="store_true", help="Write missing shells (none expected)")
    args = parser.parse_args()
    root = args.root
    payload = build_index(root)
    claims = load_json(root / "claims" / "companies.json")
    recs = {c["id"]: c for c in claims.get("companies") or []}
    rows = []
    beta = beta_file_index(root)
    ov = overview_ids(root)
    for rec in claims.get("companies") or []:
        rows.append(classify_company(rec, beta, ov))

    summary = {
        "claims": payload["n"],
        "counts": payload["counts"],
        "aliases": payload["aliases"],
        "samples": {
            "iamgold": next(r["file"] for r in payload["issuers"] if r["id"] == "iamgold"),
            "probe-gold": next(r["file"] for r in payload["issuers"] if r["id"] == "probe-gold"),
            "troilus-mining": next(
                (r["alias_of"], r["file"]) for r in payload["issuers"] if r["id"] == "troilus-mining"
            ),
        },
    }
    print(json.dumps(summary, indent=2))

    if args.check:
        errors = check_index(root, payload)
        if errors:
            print("check failed:", file=sys.stderr)
            for e in errors:
                print(" ", e, file=sys.stderr)
            return 1
        print("claims-publics check ok")
        return 0

    if not payload.get("issuers") or len(payload["issuers"]) < len(claims.get("companies") or []):
        print(
            f"refusing to write beta/claims-publics.json: {len(payload.get('issuers') or [])} issuers "
            f"for {len(claims.get('companies') or [])} catalog companies",
            file=sys.stderr,
        )
        return 1
    write_json(root / "beta" / "claims-publics.json", payload)
    written = []
    if args.write_shells:
        written = write_missing_shells(root, rows, recs)
    print(f"wrote beta/claims-publics.json n={payload['n']} new_shells={written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
