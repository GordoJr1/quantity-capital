#!/usr/bin/env python3
"""Build claims/overview.geojson — one grid cell per company × province × occupied cell.

The claims map default view must not fetch every per-company extract (~260 files,
~250 MB). This overview is a small FeatureCollection of 0.2° cells derived from
those extracts (focus titles only). Rebuild after changing claims/*.geojson or
claims/companies.json:

    python3 scripts/build_claims_overview.py

Stdlib only. Does not talk to GESTIM / MLAS / MTA. Does not write qc.sqlite.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "claims"
CATALOG = CLAIMS / "companies.json"
OUT = CLAIMS / "overview.geojson"
DEFAULT_GRID = 0.2


def color_for_id(cid: str) -> str:
    h = 0
    for ch in cid:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return "hsl(%d, 68%%, 56%%)" % (h % 360)


def walk_xy(geom, fn) -> None:
    if not geom:
        return
    t = geom.get("type")
    c = geom.get("coordinates")
    if not c:
        return
    if t == "Polygon":
        for ring in c:
            for xy in ring:
                fn(xy)
    elif t == "MultiPolygon":
        for poly in c:
            for ring in poly:
                for xy in ring:
                    fn(xy)
    elif t == "Point":
        fn(c)
    elif t in ("MultiPoint", "LineString"):
        for xy in c:
            fn(xy)
    elif t == "MultiLineString":
        for line in c:
            for xy in line:
                fn(xy)


def geom_bbox(geom):
    minx = miny = math.inf
    maxx = maxy = -math.inf
    n = 0

    def take(xy):
        nonlocal minx, miny, maxx, maxy, n
        if not xy or len(xy) < 2:
            return
        n += 1
        x, y = xy[0], xy[1]
        if x < minx:
            minx = x
        if y < miny:
            miny = y
        if x > maxx:
            maxx = x
        if y > maxy:
            maxy = y

    walk_xy(geom, take)
    return (minx, miny, maxx, maxy) if n else None


def is_focus(props: dict) -> bool:
    return (props or {}).get("role") != "neighbor"


def cell_key(lon: float, lat: float, grid: float) -> tuple[int, int]:
    return (math.floor(lon / grid), math.floor(lat / grid))


def cell_polygon(ix: int, iy: int, grid: float) -> dict:
    x0, y0 = ix * grid, iy * grid
    x1, y1 = x0 + grid, y0 + grid
    return {
        "type": "Polygon",
        "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]],
    }


def load_catalog() -> dict:
    return json.loads(CATALOG.read_text(encoding="utf-8"))


def company_extracts(company: dict) -> list[tuple[str, str]]:
    rows = []
    if company.get("extract"):
        rows.append(("Quebec", company["extract"]))
    if company.get("ontario_extract"):
        rows.append(("Ontario", company["ontario_extract"]))
    if company.get("bc_extract"):
        rows.append(("British Columbia", company["bc_extract"]))
    return rows


def accumulate_file(path: Path, company: dict, jurisdiction: str, grid: float, buckets: dict) -> int:
    if not path.is_file():
        return 0
    data = json.loads(path.read_text(encoding="utf-8"))
    n = 0
    cid = company["id"]
    holder = company.get("holder") or (company.get("names") or [cid])[0]
    color = color_for_id(cid)
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        if not is_focus(props):
            continue
        box = geom_bbox(feat.get("geometry"))
        if not box:
            continue
        lon = (box[0] + box[2]) / 2
        lat = (box[1] + box[3]) / 2
        ix, iy = cell_key(lon, lat, grid)
        key = (cid, jurisdiction, ix, iy)
        slot = buckets.get(key)
        if slot is None:
            buckets[key] = {
                "n": 1,
                "company_id": cid,
                "holder": holder,
                "color": color,
                "jurisdiction": jurisdiction,
                "ix": ix,
                "iy": iy,
            }
        else:
            slot["n"] += 1
        n += 1
    return n


def build(grid: float) -> dict:
    catalog = load_catalog()
    companies = catalog.get("companies") or []
    buckets = {}
    files = 0
    titles = 0
    used = 0
    for company in companies:
        extracts = company_extracts(company)
        if not extracts:
            continue
        used += 1
        for jurisdiction, rel in extracts:
            path = ROOT / rel
            files += 1
            titles += accumulate_file(path, company, jurisdiction, grid, buckets)
    features = []
    for (cid, jurisdiction, ix, iy), slot in sorted(
        buckets.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], kv[0][3])
    ):
        claim_id = "%s:%s:%d:%d" % (cid, jurisdiction.replace(" ", "-").lower(), ix, iy)
        features.append({
            "type": "Feature",
            "properties": {
                "company_id": slot["company_id"],
                "company": slot["holder"],
                "holder": slot["holder"],
                "role": "focus",
                "color": slot["color"],
                "claim_id": claim_id,
                "claim_name": "Overview cell",
                "status": "overview",
                "tenure_type": "overview",
                "jurisdiction": jurisdiction,
                "source": "claims/overview.geojson (grid from committed extracts)",
                "as_of": catalog.get("as_of") or "",
                "claim_count": slot["n"],
                "overview": True,
            },
            "geometry": cell_polygon(ix, iy, grid),
        })
    return {
        "type": "FeatureCollection",
        "name": "claims-overview",
        "grid_deg": grid,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "as_of": catalog.get("as_of"),
        "companies": used,
        "extract_files": files,
        "source_titles": titles,
        "features": features,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build claims/overview.geojson from committed extracts")
    ap.add_argument("--grid", type=float, default=DEFAULT_GRID, help="cell size in degrees (default 0.2)")
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    args = ap.parse_args()
    if args.grid <= 0:
        raise SystemExit("--grid must be > 0")
    fc = build(args.grid)
    args.out.write_text(json.dumps(fc, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    kb = args.out.stat().st_size / 1024
    print(
        "Wrote %s · %d cells · %d companies · %d source titles · %.1f KB · grid %s°"
        % (args.out.relative_to(ROOT), len(fc["features"]), fc["companies"], fc["source_titles"], kb, args.grid)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
