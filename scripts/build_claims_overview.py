#!/usr/bin/env python3
"""Build claims/overview.geojson — claim-block footprints for the default map.

The claims map default view must not fetch every per-company extract (~260 files,
~250 MB). This overview rasterizes focus titles onto a fine grid and merges
touching cells into claim-block polygons (not 0.2° squares). Rebuild after
changing claims/*.geojson or claims/companies.json:

    python3 scripts/build_claims_overview.py

Stdlib only. Does not talk to GESTIM / MLAS / MTA. Does not write qc.sqlite.
Refuses to write (exit 1) when an extract named in the catalog is missing or
the result has no blocks; pass --allow-missing to build around missing files.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from qc_io import atomic_write_json, atomic_write_text  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "claims"
CATALOG = CLAIMS / "companies.json"
OUT = CLAIMS / "overview.geojson"
BYTES_OUT = CLAIMS / "extract-bytes.json"
# ~2.2 km. Fine enough that blocks follow real title clusters; coarse enough
# that the file stays a few MB. 0.2° squares were ~22 km and looked like pixels.
DEFAULT_GRID = 0.02


def color_for_id(cid: str) -> str:
    h = 0
    for ch in cid:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return "hsl(%d, 68%%, 56%%)" % (h % 360)


def walk_xy(geom, fn) -> None:
    if not geom:
        return
    t = geom.get("type")
    if t == "GeometryCollection":
        for g in geom.get("geometries") or []:
            walk_xy(g, fn)
        return
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


def occupy_cells(geom, grid: float, into: set) -> None:
    box = geom_bbox(geom)
    if not box:
        return
    ix0 = math.floor(box[0] / grid)
    iy0 = math.floor(box[1] / grid)
    ix1 = math.floor(box[2] / grid)
    iy1 = math.floor(box[3] / grid)
    if (ix1 - ix0 + 1) * (iy1 - iy0 + 1) > 40:
        def take(xy):
            if not xy or len(xy) < 2:
                return
            into.add((math.floor(xy[0] / grid), math.floor(xy[1] / grid)))
        walk_xy(geom, take)
        into.add((math.floor(((box[0] + box[2]) / 2) / grid), math.floor(((box[1] + box[3]) / 2) / grid)))
        return
    for ix in range(ix0, ix1 + 1):
        for iy in range(iy0, iy1 + 1):
            into.add((ix, iy))


def connected_components(cells: set[tuple[int, int]]) -> list[list[tuple[int, int]]]:
    seen = set()
    out = []
    for start in cells:
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        comp = []
        while stack:
            x, y = stack.pop()
            comp.append((x, y))
            for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                nb = (x + dx, y + dy)
                if nb in cells and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        out.append(comp)
    return out


def boundary_rings(cells) -> list[list[tuple[int, int]]]:
    """Directed boundary of occupied cells, chained into closed rings (cell corners)."""
    cells = set(cells)
    edges: dict[tuple[tuple[int, int], tuple[int, int]], int] = {}

    def add(a, b):
        rev = (b, a)
        if edges.get(rev):
            edges[rev] -= 1
            if edges[rev] <= 0:
                del edges[rev]
            return
        edges[(a, b)] = edges.get((a, b), 0) + 1

    for ix, iy in cells:
        add((ix, iy), (ix + 1, iy))
        add((ix + 1, iy), (ix + 1, iy + 1))
        add((ix + 1, iy + 1), (ix, iy + 1))
        add((ix, iy + 1), (ix, iy))

    nxt: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for (a, b), n in edges.items():
        for _ in range(n):
            nxt[a].append(b)

    rings = []
    while True:
        start = None
        for k, vs in nxt.items():
            if vs:
                start = k
                break
        if start is None:
            break
        ring = [start]
        cur = start
        for _ in range(200000):
            opts = nxt.get(cur) or []
            if not opts:
                break
            nxtp = opts.pop()
            ring.append(nxtp)
            cur = nxtp
            if cur == start:
                break
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def rings_to_multipolygon(rings, grid: float) -> dict | None:
    coords = []
    for ring in rings:
        path = [[pt[0] * grid, pt[1] * grid] for pt in ring]
        if path[0] != path[-1]:
            path.append(path[0])
        if len(path) >= 4:
            coords.append([path])
    if not coords:
        return None
    if len(coords) == 1:
        return {"type": "Polygon", "coordinates": coords[0]}
    return {"type": "MultiPolygon", "coordinates": coords}


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
    key = (cid, jurisdiction)
    slot = buckets.get(key)
    if slot is None:
        slot = {
            "company_id": cid,
            "holder": holder,
            "color": color,
            "jurisdiction": jurisdiction,
            "cells": set(),
            "n": 0,
        }
        buckets[key] = slot
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        if not is_focus(props):
            continue
        occupy_cells(feat.get("geometry"), grid, slot["cells"])
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
    company_bytes = {}
    missing = []
    for company in companies:
        extracts = company_extracts(company)
        if not extracts:
            continue
        used += 1
        total = 0
        for jurisdiction, rel in extracts:
            path = ROOT / rel
            files += 1
            if path.is_file():
                total += path.stat().st_size
            else:
                missing.append(rel)
            titles += accumulate_file(path, company, jurisdiction, grid, buckets)
        company_bytes[company["id"]] = total
    features = []
    for (cid, jurisdiction), slot in sorted(buckets.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        comps = connected_components(slot["cells"])
        # Share this jurisdiction's title count across blocks by cell occupancy.
        cell_n = len(slot["cells"]) or 1
        for idx, comp in enumerate(comps):
            geom = rings_to_multipolygon(boundary_rings(comp), grid)
            if not geom:
                continue
            share = max(1, int(round(slot["n"] * (len(comp) / cell_n))))
            claim_id = "%s:%s:%d" % (cid, jurisdiction.replace(" ", "-").lower(), idx)
            features.append({
                "type": "Feature",
                "properties": {
                    "company_id": slot["company_id"],
                    "company": slot["holder"],
                    "holder": slot["holder"],
                    "role": "focus",
                    "color": slot["color"],
                    "claim_id": claim_id,
                    "claim_name": "Claim footprint",
                    "status": "overview",
                    "tenure_type": "overview",
                    "jurisdiction": jurisdiction,
                    "source": "claims/overview.geojson (claim blocks from committed extracts)",
                    "as_of": catalog.get("as_of") or "",
                    "claim_count": share,
                    "overview": True,
                },
                "geometry": geom,
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
        "company_bytes": company_bytes,
        "missing_extracts": missing,
        "features": features,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Build claims/overview.geojson from committed extracts")
    ap.add_argument("--grid", type=float, default=DEFAULT_GRID, help="raster cell size in degrees (default 0.02)")
    ap.add_argument("-o", "--out", type=Path, default=OUT)
    ap.add_argument("--allow-missing", action="store_true",
                    help="Write even when catalog extracts are missing (they are listed in missing_extracts)")
    args = ap.parse_args()
    if args.grid <= 0:
        raise SystemExit("--grid must be > 0")
    fc = build(args.grid)
    missing = fc.get("missing_extracts") or []
    if missing:
        print("%d catalog extracts missing: %s" % (len(missing), ", ".join(missing[:12])), file=sys.stderr)
        if not args.allow_missing:
            print("refusing to write %s (use --allow-missing)" % args.out.name, file=sys.stderr)
            return 1
    else:
        fc.pop("missing_extracts", None)
    if not fc["features"]:
        print("refusing to write %s: 0 claim blocks" % args.out.name, file=sys.stderr)
        return 1
    atomic_write_text(args.out, json.dumps(fc, ensure_ascii=False, separators=(",", ":")) + "\n")
    bytes_payload = {
        "generated_at": fc["generated_at"],
        "company_bytes": fc.get("company_bytes") or {},
    }
    prev = {}
    if BYTES_OUT.is_file():
        try:
            prev = json.loads(BYTES_OUT.read_text(encoding="utf-8")).get("company_bytes") or {}
        except json.JSONDecodeError:
            prev = {}
    if prev != bytes_payload["company_bytes"]:
        atomic_write_json(BYTES_OUT, bytes_payload)
        print("Wrote %s · %d company byte totals" % (BYTES_OUT.relative_to(ROOT), len(bytes_payload["company_bytes"])))
    else:
        print("Unchanged %s" % BYTES_OUT.relative_to(ROOT))
    kb = args.out.stat().st_size / 1024
    print(
        "Wrote %s · %d blocks · %d companies · %d source titles · %.1f KB · grid %s°"
        % (args.out, len(fc["features"]), fc["companies"], fc["source_titles"], kb, args.grid)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
