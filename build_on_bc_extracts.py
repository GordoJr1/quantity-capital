#!/usr/bin/env python3
"""Fetch Ontario MLAS and BC MTA tenure for Beta producers (official REST, no HTML scrape).

Writes claims/<id>-ontario.geojson and claims/<id>-bc.geojson (focus holders only).
Updates claims/companies.json with per-province extracts and counts.
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CLAIMS = ROOT / "claims"
AS_OF = datetime.now(timezone.utc).strftime("%Y-%m-%d")

ON_URL = "https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/MLAS/mlas_op/MapServer/1/query"
BC_URL = "https://delivery.maps.gov.bc.ca/arcgis/rest/services/whse/bcgw_pub_whse_mineral_tenure/MapServer/36/query"

ON_SOURCE = "Ontario MLAS operational claims (OGSEarth / LIO MapServer). Unofficial viewing data, not legal title."
BC_SOURCE = "BC MTA Mineral, Placer and Coal Tenure Spatial View (BCGW). Open Government Licence – British Columbia. Not legal title."

# Substrings against Ontario HOLDER / BC OWNER_NAME (uppercase LIKE).
ON_MATCH = {
    "iamgold": ["IAMGOLD"],
    "agnico-eagle": ["AGNICO"],
    "alamos-gold": ["ALAMOS"],
    "wesdome-gold-mines": ["WESDOME"],
    "evolution": ["EVOLUTION"],
    "equinox-gold": ["GREENSTONE", "MUSSELWHITE"],
}
BC_MATCH = {
    "newmont": ["NEWMONT", "PRETIUM"],
    "centerra-gold": ["THOMPSON CREEK", "CENTERRA"],
    "artemis-gold": ["BW GOLD", "ARTEMIS"],
}

PAGE = 1000
OFFSET_DEG = 0.0004  # ~40 m generalize for Pages payload


def get_json(url: str, params: dict) -> dict:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + q, headers={"User-Agent": "quantity-capital-claims/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read().decode())


def epoch_to_date(v):
    if v is None or v == "":
        return None
    if isinstance(v, str) and len(v) >= 10 and v[4] == "-":
        return v[:10]
    try:
        ms = int(v)
        if ms > 1e12:
            ms //= 1000
        return datetime.fromtimestamp(ms, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(v)[:10] if v else None


def like_where(field: str, needles: list[str]) -> str:
    parts = [f"UPPER({field}) LIKE '%{n.replace(chr(39), '')}%'" for n in needles]
    return "(" + " OR ".join(parts) + ")"


def fetch_layer(url: str, where: str, out_fields: str, page_size: int) -> list[dict]:
    features = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "outSR": 4326,
            "f": "geojson",
            "resultRecordCount": page_size,
            "resultOffset": offset,
            "maxAllowableOffset": OFFSET_DEG,
        }
        data = get_json(url, params)
        batch = data.get("features") or []
        features.extend(batch)
        print(f"    {offset}+{len(batch)} (total {len(features)})", flush=True)
        if len(batch) < page_size and not data.get("exceededTransferLimit"):
            break
        if not batch:
            break
        offset += len(batch)
        time.sleep(0.15)
    return features


def map_on(feat: dict) -> dict:
    p = feat.get("properties") or feat.get("attributes") or {}
    return {
        "type": "Feature",
        "geometry": feat.get("geometry"),
        "properties": {
            "jurisdiction": "Ontario",
            "claim_id": p.get("TENURE_NUMBER_ID"),
            "claim_name": None,
            "holder": p.get("HOLDER"),
            "status": p.get("TENURE_STATUS_DESC"),
            "recorded_date": epoch_to_date(p.get("ISSUE_DATE")),
            "anniversary_or_expiry": epoch_to_date(p.get("ANNIVERSARY_DATE") or p.get("CLAIM_DUE_DATE")),
            "area_ha": None,
            "tenure_type": p.get("TITLE_TYPE_DESC"),
            "source": ON_SOURCE,
            "as_of": AS_OF,
            "role": "focus",
            "color": "#e8b040",
        },
    }


def map_bc(feat: dict) -> dict:
    p = feat.get("properties") or feat.get("attributes") or {}
    return {
        "type": "Feature",
        "geometry": feat.get("geometry"),
        "properties": {
            "jurisdiction": "British Columbia",
            "claim_id": p.get("TENURE_NUMBER_ID"),
            "claim_name": p.get("CLAIM_NAME"),
            "holder": p.get("OWNER_NAME"),
            "status": p.get("TENURE_TYPE_DESCRIPTION") or p.get("TITLE_TYPE_DESCRIPTION"),
            "recorded_date": epoch_to_date(p.get("ISSUE_DATE")),
            "anniversary_or_expiry": epoch_to_date(p.get("GOOD_TO_DATE") or p.get("TERMINATION_DATE")),
            "area_ha": p.get("AREA_IN_HECTARES"),
            "tenure_type": p.get("TENURE_TYPE_DESCRIPTION") or p.get("TENURE_SUB_TYPE_DESCRIPTION"),
            "source": BC_SOURCE,
            "as_of": AS_OF,
            "role": "focus",
            "color": "#e8b040",
        },
    }


def bbox_of(features: list[dict]):
    xs, ys = [], []
    for f in features:
        g = f.get("geometry") or {}
        coords = g.get("coordinates")
        if not coords:
            continue

        def walk(c):
            if isinstance(c, (int, float)):
                return
            if c and isinstance(c[0], (int, float)) and len(c) >= 2:
                xs.append(c[0])
                ys.append(c[1])
                return
            for x in c:
                walk(x)

        walk(coords)
    if not xs:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]


def write_fc(path: Path, features: list[dict], name: str):
    path.write_text(
        json.dumps(
            {"type": "FeatureCollection", "name": name, "features": features},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def main():
    catalog = json.loads((CLAIMS / "companies.json").read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in catalog["companies"]}
    for row in catalog["companies"]:
        if "quebec_count" not in row:
            row["quebec_count"] = row.get("claim_count") or 0
        row["ontario_count"] = 0
        row["bc_count"] = 0
        row["ontario_extract"] = None
        row["bc_extract"] = None

    print("Ontario MLAS…")
    for cid, needles in ON_MATCH.items():
        where = like_where("HOLDER", needles)
        print(f"  {cid} {where}")
        raw = fetch_layer(
            ON_URL,
            where,
            "TENURE_NUMBER_ID,TITLE_TYPE_DESC,TENURE_STATUS_DESC,ISSUE_DATE,ANNIVERSARY_DATE,CLAIM_DUE_DATE,HOLDER",
            PAGE,
        )
        feats = [map_on(f) for f in raw if f.get("geometry")]
        rel = f"claims/{cid}-ontario.geojson"
        dest = ROOT / rel
        if feats:
            write_fc(dest, feats, f"{cid}-ontario")
            print(f"    wrote {len(feats)} -> {rel} ({dest.stat().st_size/1e6:.1f} MB)")
        elif dest.exists():
            dest.unlink()
        row = by_id.get(cid)
        if row:
            row["ontario_count"] = len(feats)
            row["ontario_extract"] = rel if feats else None
            row["ontario_bbox"] = bbox_of(feats)

    print("BC MTA…")
    for cid, needles in BC_MATCH.items():
        where = like_where("OWNER_NAME", needles)
        print(f"  {cid} {where}")
        raw = fetch_layer(
            BC_URL,
            where,
            "TENURE_NUMBER_ID,CLAIM_NAME,TENURE_TYPE_DESCRIPTION,TITLE_TYPE_DESCRIPTION,ISSUE_DATE,GOOD_TO_DATE,TERMINATION_DATE,AREA_IN_HECTARES,OWNER_NAME",
            PAGE,
        )
        feats = [map_bc(f) for f in raw if f.get("geometry")]
        rel = f"claims/{cid}-bc.geojson"
        dest = ROOT / rel
        if feats:
            write_fc(dest, feats, f"{cid}-bc")
            print(f"    wrote {len(feats)} -> {rel} ({dest.stat().st_size/1e6:.1f} MB)")
        elif dest.exists():
            dest.unlink()
        row = by_id.get(cid)
        if row:
            row["bc_count"] = len(feats)
            row["bc_extract"] = rel if feats else None
            row["bc_bbox"] = bbox_of(feats)

    for row in catalog["companies"]:
        row["claim_count"] = (row.get("quebec_count") or 0) + (row.get("ontario_count") or 0) + (row.get("bc_count") or 0)
        for mine in row.get("mines") or []:
            region = (mine.get("region") or "").lower()
            if "ontario" in region and row.get("ontario_count"):
                mine["note"] = "Ontario MLAS claims in this extract. Not legal title."
            elif "british columbia" in region and row.get("bc_count"):
                mine["note"] = "BC MTA tenures in this extract. Not legal title."

    catalog["disclaimer"] = (
        "Not legal title. Quebec GESTIM, Ontario MLAS operational claims (unofficial viewing data), "
        "and BC MTA tenure. Neighbors on Quebec extracts are titles within ~2 km of the company cells. "
        "Ontario/BC layers are the selected company's tenures only."
    )
    catalog["jurisdiction"] = "Quebec, Ontario, British Columbia"
    catalog["sources"] = {
        "quebec": "GESTIM active titles",
        "ontario": ON_SOURCE,
        "british_columbia": BC_SOURCE,
        "as_of_on_bc": AS_OF,
    }
    catalog["on_bc_built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    (CLAIMS / "companies.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Updated claims/companies.json")


if __name__ == "__main__":
    main()
