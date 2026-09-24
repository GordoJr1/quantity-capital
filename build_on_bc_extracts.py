#!/usr/bin/env python3
"""Fetch Ontario MLAS and BC MTA tenure for Beta producers (official REST, no HTML scrape).

Writes claims/<id>-ontario.geojson and claims/<id>-bc.geojson.
Focus titles are the matched holder. Ontario featured extracts also keep neighboring
MLAS titles in a ~3 km pad around those cells (role=neighbor), same idea as Quebec GESTIM.

    python3 build_on_bc_extracts.py                 # refetch focus ON/BC + rewrite catalog
    python3 build_on_bc_extracts.py --neighbors-only  # append Ontario neighbors onto existing extracts
    python3 build_on_bc_extracts.py --company vale  # Vale Canada Limited only (others untouched)
    python3 build_on_bc_extracts.py --full-ontario  # whole MLAS layer → gitignored cache tiles
    python3 build_on_bc_extracts.py --holders-index  # offline: cache tiles → committed claims/mlas/holders.json

Raw REST pages are cached under claims/.cache/mlas/ (gitignored) so later runs
reuse them instead of rereading pages. Cached pages expire after
--cache-max-age-hours (default 24) and ArcGIS {"error": ...} bodies are never
cached. The full-province MLAS file is never committed — only the script, the
documented cache path, and the company extracts the site serves. Pass
--refresh to force a network refetch.

A zero-feature answer only deletes a company extract after a live
returnCountOnly query confirms count 0. Any other failure keeps the old
extract and catalog row, and the run exits 1. Requests retry with backoff.

Vale holder strings are Jev-gated, not hand-matched: --sweep-holders collects
the distinct MLAS HOLDER values matching %VALE% (attributes only) and
--judge-holders asks TypeSafe Jev (Noul: is this holder Vale Canada Limited)
once, caching judgments beside the holder sweep so reruns spend no tokens.
When that judgments file exists, the Vale extract fetches exactly the
approved holder strings (HOLDER IN (...)); otherwise it falls back to the
ON_MATCH["vale"] substring. Jev only ever decides holder-name matches; it
never invents tenures.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

from qc_io import atomic_write_json, atomic_write_text  # noqa: E402

CLAIMS = ROOT / "claims"
AS_OF = datetime.now(timezone.utc).strftime("%Y-%m-%d")

ON_URL = "https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/MLAS/mlas_op/MapServer/1/query"
BC_URL = "https://delivery.maps.gov.bc.ca/arcgis/rest/services/whse/bcgw_pub_whse_mineral_tenure/MapServer/36/query"

ON_SOURCE = "Ontario MLAS operational claims (OGSEarth / LIO MapServer). Unofficial viewing data, not legal title."
BC_SOURCE = "BC MTA Mineral, Placer and Coal Tenure Spatial View (BCGW). Open Government Licence – British Columbia. Not legal title."

# Substrings against Ontario HOLDER / BC OWNER_NAME (uppercase LIKE).
# "vale" needles are Jev-gated: both live MLAS holder strings containing
# VALE CANADA LIMITED scored as Vale-held (100%-Vale 0.95; 50/50 Glencore JV
# 0.93; JV include-as-focus 0.7), so one needle covers the whole Vale set.
ON_MATCH = {
    "iamgold": ["IAMGOLD"],
    "agnico-eagle": ["AGNICO"],
    "alamos-gold": ["ALAMOS"],
    "wesdome-gold-mines": ["WESDOME"],
    "evolution": ["EVOLUTION"],
    "equinox-gold": ["GREENSTONE", "MUSSELWHITE"],
    "vale": ["VALE CANADA LIMITED"],
}
BC_MATCH = {
    "newmont": ["NEWMONT", "PRETIUM"],
    "centerra-gold": ["THOMPSON CREEK", "CENTERRA"],
    "artemis-gold": ["BW GOLD", "ARTEMIS"],
}

PAGE = 1000
OFFSET_DEG = 0.0004  # ~40 m generalize for Pages payload
ON_NEIGHBOR_PAD_DEG = 0.03  # ~3 km
ON_NEIGHBOR_GRID = 0.2
UA = "Quantity Capital gordojr@proton.me"
ON_FIELDS = "TENURE_NUMBER_ID,TITLE_TYPE_DESC,TENURE_STATUS_DESC,ISSUE_DATE,ANNIVERSARY_DATE,CLAIM_DUE_DATE,HOLDER"
BC_FIELDS = ("TENURE_NUMBER_ID,CLAIM_NAME,TENURE_TYPE_DESCRIPTION,TITLE_TYPE_DESCRIPTION,ISSUE_DATE,"
             "GOOD_TO_DATE,TERMINATION_DATE,AREA_IN_HECTARES,OWNER_NAME")
FULL_TILE_DIR = CLAIMS / "mlas"  # committed: holders.json only, never tiles
FULL_TILE_SIZE = 10000
HOLDERS_INDEX = FULL_TILE_DIR / "holders.json"


def full_cache_tiles(cache_dir: Path) -> Path:
    return cache_dir_ready(cache_dir) / "tiles"


def full_progress_path(cache_dir: Path) -> Path:
    return cache_dir_ready(cache_dir) / "full-progress.json"

# Gitignored raw-page + holder-judgment cache. The full MLAS download lives
# here in the same JSON form the builder consumes; only company extracts
# are committed. Later runs read the cache (no network, no Jev tokens)
# unless --refresh is passed.
DEFAULT_CACHE_DIR = CLAIMS / ".cache" / "mlas"
HOLDER_SWEEP_NEEDLE = "VALE"
JUDGE_MODEL = "jev-latest"
VALE_JUDGMENTS = "vale-holder-judgments.json"
CACHE_MAX_AGE_S = 24 * 3600
RETRIES = 3
RETRY_BASE_S = 2.0


class ArcGISError(RuntimeError):
    """The endpoint answered with an {"error": ...} body or never answered cleanly."""


def cache_dir_ready(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def page_cache_key(url: str, params: dict) -> str:
    blob = json.dumps({"u": url, "p": params}, sort_keys=True)
    return hashlib.sha1(blob.encode("utf-8")).hexdigest()


def cached_get_json(url: str, params: dict, cache_dir: Path | None, refresh: bool) -> dict:
    if cache_dir is not None:
        key = page_cache_key(url, {k: v for k, v in params.items()})
        slot = cache_dir / "pages" / (key + ".json")
        if slot.is_file() and not refresh and time.time() - slot.stat().st_mtime < CACHE_MAX_AGE_S:
            try:
                data = json.loads(slot.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                data = None
            if isinstance(data, dict) and not data.get("error"):
                return data
        data = get_json(url, params)
        atomic_write_text(slot, json.dumps(data, ensure_ascii=False))
        return data
    return get_json(url, params)


def get_json(url: str, params: dict) -> dict:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + q, headers={"User-Agent": UA})
    last: Exception | None = None
    for attempt in range(RETRIES):
        if attempt:
            time.sleep(RETRY_BASE_S * (2 ** (attempt - 1)))
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                data = json.loads(r.read().decode())
        except urllib.error.HTTPError as exc:
            last = exc
            if exc.code < 500 and exc.code != 429:
                break
            continue
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            last = exc
            continue
        if not isinstance(data, dict):
            last = ArcGISError("non-object response")
            continue
        if data.get("error"):
            err = data["error"] if isinstance(data["error"], dict) else {}
            last = ArcGISError(f"ArcGIS error code={err.get('code')} {err.get('message') or ''}".strip())
            continue
        return data
    raise ArcGISError(f"{type(last).__name__}: {last}") if not isinstance(last, ArcGISError) else last


def live_count(url: str, where: str) -> int:
    """Uncached returnCountOnly probe; raises ArcGISError unless the answer is clean."""
    data = get_json(url, {"where": where, "returnCountOnly": "true", "f": "json"})
    count = data.get("count")
    if not isinstance(count, int):
        raise ArcGISError("count missing from returnCountOnly response")
    return count


def color_for_id(cid: str) -> str:
    h = 0
    for ch in str(cid or ""):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return "hsl(%d, 68%%, 56%%)" % (h % 360)


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


def in_where(field: str, values: list[str]) -> str:
    quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in values)
    return f"{field} IN ({quoted})"


def approved_vale_holders(cache_dir: Path | None) -> list[str] | None:
    """Holder strings Jev approved as Vale, or None when no judgments were cached."""
    if cache_dir is None:
        return None
    slot = cache_dir / VALE_JUDGMENTS
    if not slot.is_file():
        return None
    try:
        rows = json.loads(slot.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(rows, dict):
        return None
    return sorted(h for h, row in rows.items() if isinstance(row, dict) and row.get("vale") is True)


def ontario_where(cid: str, needles: list[str], cache_dir: Path | None) -> str:
    if cid == "vale":
        approved = approved_vale_holders(cache_dir)
        if approved:
            return in_where("HOLDER", approved)
    return like_where("HOLDER", needles)


def fetch_layer(url: str, where: str, out_fields: str, page_size: int, envelope=None,
                 cache_dir: Path | None = None, refresh: bool = False) -> list[dict]:
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
            "orderByFields": "OBJECTID ASC",
            "maxAllowableOffset": OFFSET_DEG,
        }
        if envelope and len(envelope) == 4:
            params["geometry"] = "%s,%s,%s,%s" % (envelope[0], envelope[1], envelope[2], envelope[3])
            params["geometryType"] = "esriGeometryEnvelope"
            params["inSR"] = 4326
            params["spatialRel"] = "esriSpatialRelIntersects"
        data = cached_get_json(url, params, cache_dir, refresh)
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


def sweep_holders(needle: str = HOLDER_SWEEP_NEEDLE, cache_dir: Path | None = None,
                  refresh: bool = False) -> dict:
    """Attributes-only sweep of every MLAS title matching %needle%.

    Returns {holder: title_count} plus metadata, cached as JSON so reruns
    do not reread pages. This is the full downloaded set behind the Vale
    extract (geometry is fetched only for Jev-approved holders).
    """
    where = f"UPPER(HOLDER) LIKE '%{needle}%'"
    counts: dict[str, int] = {}
    offset = 0
    total = 0
    while True:
        params = {
            "where": where,
            "outFields": "HOLDER,TENURE_NUMBER_ID",
            "outSR": 4326,
            "f": "json",
            "resultRecordCount": PAGE,
            "resultOffset": offset,
            "orderByFields": "OBJECTID ASC",
            "returnGeometry": "false",
        }
        data = cached_get_json(ON_URL, params, cache_dir, refresh)
        batch = data.get("features") or []
        if not batch:
            break
        for feat in batch:
            holder = (feat.get("attributes") or {}).get("HOLDER")
            counts[str(holder)] = counts.get(str(holder), 0) + 1
        total += len(batch)
        offset += len(batch)
        if len(batch) < PAGE and not data.get("exceededTransferLimit"):
            break
        time.sleep(0.12)
    payload = {
        "needle": needle,
        "where": where,
        "swept_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "titles": total,
        "holders": counts,
    }
    if cache_dir is not None:
        atomic_write_json(cache_dir_ready(cache_dir) / "holders-vale.json", payload)
    return payload


def load_typesafe_key(typesafe_env: str | None = None) -> str | None:
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    candidates = []
    if typesafe_env:
        candidates.append(Path(typesafe_env))
    home_env = Path.home() / ".grok" / "typesafe.env"
    candidates.append(home_env)
    for path in candidates:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("TYPESAFE_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def judge_holders(holders: dict, cache_dir: Path | None = None,
                  typesafe_env: str | None = None) -> dict:
    """Jev Noul per holder: is this MLAS holder Vale Canada Limited?

    Judgments are cached (keyed by holder string) so later runs spend no
    tokens. Without a key or SDK, falls back to a recorded substring rule.
    Never prints the key.
    """
    slot = cache_dir_ready(cache_dir) / VALE_JUDGMENTS if cache_dir else None
    cached: dict = {}
    if slot and slot.is_file():
        try:
            cached = json.loads(slot.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            cached = {}
    out = dict(cached)
    missing = [h for h in holders if h not in out]
    if missing:
        key = load_typesafe_key(typesafe_env)
        use_jev = bool(key)
        client = None
        if use_jev:
            try:
                os.environ.setdefault("TYPESAFE_API_KEY", key or "")
                from typesafe_sdk import Noul  # type: ignore

                from typesafe_sdk import TypeSafeClient  # type: ignore

                client = TypeSafeClient(timeout=120.0)
                question = Noul(
                    instructions=(
                        "Is this Ontario MLAS HOLDER string a tenure held (in whole "
                        "or in part) by Vale Canada Limited / Vale Canada Limitee, "
                        "as opposed to a different company?"
                    ),
                )
            except Exception as exc:
                print(f"    jev unavailable ({type(exc).__name__}); substring fallback", flush=True)
                use_jev = False
        for holder in missing:
            if use_jev and client is not None:
                try:
                    resp = client.system_one(
                        state={"holder": holder,
                               "company": "Vale Canada Limited / Vale Canada Limitee"},
                        questions={"holder_is_vale": question},
                        model=JUDGE_MODEL,
                    )
                    ans = resp.answers["holder_is_vale"]
                    out[holder] = {"vale": bool(ans.noul and ans.noul >= 0.5),
                                   "noul": ans.noul, "method": "jev-noul",
                                   "model": getattr(resp, "model", JUDGE_MODEL)}
                except Exception as exc:
                    print(f"    jev failed for {holder!r}; substring fallback", flush=True)
                    out[holder] = {"vale": "VALE CANADA LIMITED" in holder.upper(),
                                   "noul": None, "method": f"substring-fallback ({type(exc).__name__})"}
            else:
                out[holder] = {"vale": "VALE CANADA LIMITED" in holder.upper(),
                               "noul": None, "method": "substring-fallback (no key/sdk)"}
        if slot:
            atomic_write_json(slot, out)
    for holder in holders:
        row = out.get(holder, {})
        print(f"    holder {holder!r} x{holders[holder]} -> vale={row.get('vale')} "
              f"noul={row.get('noul')} [{row.get('method')}]", flush=True)
    return out


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


def merge_envelopes(boxes: list[list[float]]) -> list[list[float]]:
    boxes = [list(b) for b in boxes if b and len(b) == 4]
    changed = True
    while changed:
        changed = False
        out = []
        used = [False] * len(boxes)
        for i, a in enumerate(boxes):
            if used[i]:
                continue
            cur = a[:]
            used[i] = True
            for j, b in enumerate(boxes):
                if used[j]:
                    continue
                if cur[0] <= b[2] and cur[2] >= b[0] and cur[1] <= b[3] and cur[3] >= b[1]:
                    cur = [min(cur[0], b[0]), min(cur[1], b[1]), max(cur[2], b[2]), max(cur[3], b[3])]
                    used[j] = True
                    changed = True
            out.append(cur)
        boxes = out
    return boxes


def focus_cell_envelopes(features: list[dict], pad: float = ON_NEIGHBOR_PAD_DEG) -> list[list[float]]:
    cells = defaultdict(lambda: [1e9, 1e9, -1e9, -1e9])
    for feat in features:
        props = feat.get("properties") or {}
        if props.get("role") == "neighbor":
            continue
        box = bbox_of([feat])
        if not box:
            continue
        lon = (box[0] + box[2]) / 2
        lat = (box[1] + box[3]) / 2
        key = (round(lon / ON_NEIGHBOR_GRID), round(lat / ON_NEIGHBOR_GRID))
        slot = cells[key]
        slot[0] = min(slot[0], box[0])
        slot[1] = min(slot[1], box[1])
        slot[2] = max(slot[2], box[2])
        slot[3] = max(slot[3], box[3])
    raw = [[a - pad, b - pad, c + pad, d + pad] for a, b, c, d in cells.values()]
    return merge_envelopes(raw)


def fetch_ontario_neighbors(cid: str, focus_feats: list[dict], cache_dir: Path | None = None,
                            refresh: bool = False) -> list[dict]:
    focus_ids = set()
    for feat in focus_feats:
        claim_id = (feat.get("properties") or {}).get("claim_id")
        if claim_id is not None and claim_id != "":
            focus_ids.add(str(claim_id))
    envelopes = focus_cell_envelopes(focus_feats)
    print(f"    {cid} neighbor envelopes {len(envelopes)}", flush=True)
    seen = set(focus_ids)
    neighbors = []
    for env in envelopes:
        raw = fetch_layer(ON_URL, "1=1", ON_FIELDS, PAGE, envelope=env,
                          cache_dir=cache_dir, refresh=refresh)
        for feat in raw:
            if not feat.get("geometry"):
                continue
            mapped = map_on(feat)
            claim_id = str((mapped.get("properties") or {}).get("claim_id") or "")
            if not claim_id or claim_id in seen:
                continue
            seen.add(claim_id)
            holder = (mapped["properties"].get("holder") or claim_id)
            mapped["properties"]["role"] = "neighbor"
            mapped["properties"]["company_id"] = cid
            mapped["properties"]["color"] = color_for_id(holder)
            neighbors.append(mapped)
    return neighbors


def write_ontario_with_neighbors(cid: str, focus_feats: list[dict], dest: Path,
                                  cache_dir: Path | None = None, refresh: bool = False) -> list[dict]:
    neighbors = fetch_ontario_neighbors(cid, focus_feats, cache_dir, refresh)
    for feat in focus_feats:
        props = feat.setdefault("properties", {})
        props.setdefault("company_id", cid)
        props.setdefault("role", "focus")
    all_feats = focus_feats + neighbors
    write_fc(dest, all_feats, f"{cid}-ontario")
    print(
        f"    wrote {len(focus_feats)} focus + {len(neighbors)} nearby -> {dest.relative_to(ROOT)} "
        f"({dest.stat().st_size / 1e6:.1f} MB)",
        flush=True,
    )
    return all_feats


def fetch_full_ontario(tile_size: int = FULL_TILE_SIZE, cache_dir: Path | None = None,
                       refresh: bool = False, out_dir: Path | None = None) -> dict:
    """Download the whole MLAS operational-claims layer, tiled by OBJECTID.

    Tiles live in the gitignored cache (`claims/.cache/mlas/tiles/`), never
    in git. Progress is tracked in `full-progress.json`; a rerun with a
    complete cache downloads nothing. The committed holder summary is built
    offline from the cache with build_holder_index(). Every tenure comes
    from the REST endpoint; nothing is invented.
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR
    tiles_dir = full_cache_tiles(cache_dir)
    tiles_dir.mkdir(parents=True, exist_ok=True)
    if out_dir is None:
        out_dir = tiles_dir
    prog_path = full_progress_path(cache_dir)
    prog: dict = {}
    if prog_path.is_file() and not refresh:
        try:
            prog = json.loads(prog_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prog = {}
    if prog.get("done") and not refresh:
        tiles = prog.get("tiles") or []
        if tiles and all((tiles_dir / Path(t["file"]).name).is_file() for t in tiles):
            total = sum(t["n"] for t in tiles)
            print(f"  full MLAS cache complete: {total} titles in {len(tiles)} tiles — no download",
                  flush=True)
            return {"titles": total, "tiles": tiles,
                    "bytes_total": sum(t.get("bytes", 0) for t in tiles),
                    "cached": True}
        print("  cache progress stale — resuming download", flush=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    after = 0
    tiles = []
    tile_idx = 0
    if not refresh:
        # Resume past complete cache tiles, in order.
        while True:
            slot = out_dir / ("tile-%04d.geojson" % tile_idx)
            if not slot.is_file():
                break
            try:
                data = json.loads(slot.read_text(encoding="utf-8"))
                feats = data.get("features") or []
                props = data.get("properties") or {}
            except (json.JSONDecodeError, OSError, ValueError, KeyError):
                break
            if len(feats) >= tile_size and props.get("max_objectid"):
                after = int(props["max_objectid"])
                tiles.append({"file": "tiles/" + slot.name, "n": len(feats),
                              "min_objectid": props.get("min_objectid"),
                              "max_objectid": props.get("max_objectid"),
                              "bytes": slot.stat().st_size})
                print(f"  resume: keep {slot.name} n={len(feats)} after={after}", flush=True)
                tile_idx += 1
                continue
            break
    print(f"  full MLAS download from OBJECTID>{after} tile_size={tile_size}", flush=True)
    buf: list[dict] = []
    total = sum(t["n"] for t in tiles)
    pages = 0
    while True:
        where = "1=1" if after <= 0 else f"OBJECTID>{after}"
        params = {
            "where": where,
            "outFields": "OBJECTID," + ON_FIELDS,
            "outSR": 4326,
            "f": "geojson",
            "resultRecordCount": PAGE,
            "orderByFields": "OBJECTID ASC",
            "maxAllowableOffset": OFFSET_DEG,
        }
        data = cached_get_json(ON_URL, params, cache_dir, refresh)
        batch = data.get("features") or []
        if not batch:
            break
        pages += 1
        oids = []
        for feat in batch:
            mapped = map_on(feat)
            props = mapped.get("properties") or {}
            oid = (feat.get("properties") or {}).get("OBJECTID")
            if oid is not None:
                try:
                    oids.append(int(oid))
                    props["objectid"] = int(oid)
                except (TypeError, ValueError):
                    pass
            if mapped.get("geometry") and props.get("claim_id") not in (None, ""):
                buf.append(mapped)
        if oids:
            after = max(oids)
        total += len(batch)
        while len(buf) >= tile_size:
            chunk, buf = buf[:tile_size], buf[tile_size:]
            tile_idx = write_full_tile(out_dir, tile_idx, chunk, tiles)
        if pages % 20 == 0:
            print(f"  pages={pages} titles={total} after={after} tiles={len(tiles)}", flush=True)
        if len(batch) < PAGE and not data.get("exceededTransferLimit"):
            break
        time.sleep(0.12)
    if buf:
        tile_idx = write_full_tile(out_dir, tile_idx, buf, tiles)
    total = sum(t["n"] for t in tiles)
    index = {
        "dataset": "Ontario MLAS operational claims (OGSEarth / LIO MapServer layer 1). "
                   "Unofficial viewing data, not legal title.",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "titles": total,
        "tiles": tiles,
        "bytes_total": sum(t["bytes"] for t in tiles),
        "done": True,
        "after": after,
    }
    atomic_write_json(prog_path, index)
    print(f"  full MLAS: {total} titles in {len(tiles)} tiles "
          f"({index['bytes_total'] / 1e6:.1f} MB) — cache only, not committed", flush=True)
    return index


def iter_holder_cells(path: Path):
    """Yield (holder, minx, miny, maxx, maxy) per feature in a cache tile."""
    data = json.loads(path.read_text(encoding="utf-8"))
    for feat in data.get("features") or []:
        props = feat.get("properties") or {}
        holder = props.get("holder") or "(blank)"
        box = bbox_of([feat])
        if box:
            yield holder, box[0], box[1], box[2], box[3]
        else:
            yield holder, None, None, None, None


def build_holder_index(cache_dir: Path | None = None, out: Path = HOLDERS_INDEX) -> dict:
    """Offline holder summary from the cache tiles: holder, count, bbox.

    No network. Committed as claims/mlas/holders.json — the small index
    behind the per-company extracts.
    """
    if cache_dir is None:
        cache_dir = DEFAULT_CACHE_DIR
    tiles_dir = cache_dir / "tiles"
    agg: dict[str, list] = {}
    n = 0
    for slot in sorted(tiles_dir.glob("tile-*.geojson")):
        for holder, x0, y0, x1, y1 in iter_holder_cells(slot):
            row = agg.get(holder)
            if row is None:
                row = agg[holder] = [0, 1e9, 1e9, -1e9, -1e9]
            row[0] += 1
            n += 1
            if x0 is not None:
                row[1] = min(row[1], x0)
                row[2] = min(row[2], y0)
                row[3] = max(row[3], x1)
                row[4] = max(row[4], y1)
    rows = [
        {"holder": h, "count": v[0],
         "bbox": [round(v[1], 4), round(v[2], 4), round(v[3], 4), round(v[4], 4)]}
        for h, v in sorted(agg.items(), key=lambda kv: -kv[1][0])
    ]
    payload = {
        "dataset": "Ontario MLAS operational claims (OGSEarth / LIO MapServer layer 1). "
                   "Unofficial viewing data, not legal title. Holder, tenure count, "
                   "and bounding box per holder from the full cached download.",
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "titles": n,
        "holders": rows,
    }
    atomic_write_json(out, payload, indent=1)
    print(f"  holder index: {n} titles, {len(rows)} holders -> {out} "
          f"({out.stat().st_size / 1e3:.0f} KB)", flush=True)
    return payload


def write_full_tile(out_dir: Path, tile_idx: int, chunk: list[dict], tiles: list) -> int:
    oids = [int((f.get("properties") or {})["objectid"]) for f in chunk
            if (f.get("properties") or {}).get("objectid") is not None]
    name = "tile-%04d.geojson" % tile_idx
    payload = {
        "type": "FeatureCollection",
        "name": "mlas-" + name.replace(".geojson", ""),
        "properties": {
            "min_objectid": min(oids) if oids else None,
            "max_objectid": max(oids) if oids else None,
        },
        "features": chunk,
    }
    slot = out_dir / name
    atomic_write_json(slot, payload, compact=True)
    tiles.append({"file": "claims/mlas/" + name, "n": len(chunk),
                  "min_objectid": min(oids) if oids else None,
                  "max_objectid": max(oids) if oids else None,
                  "bytes": slot.stat().st_size})
    print(f"  wrote {name} n={len(chunk)} ({slot.stat().st_size / 1e6:.1f} MB)", flush=True)
    return tile_idx + 1


def write_fc(path: Path, features: list[dict], name: str):
    atomic_write_text(
        path,
        json.dumps(
            {"type": "FeatureCollection", "name": name, "features": features},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    )


def ensure_company_row(catalog: dict, by_id: dict, cid: str, holder: str, names: list[str],
                        mines: list[dict] | None = None) -> dict:
    """Insert a catalog row for a newly covered company (e.g. vale). Never rewrites Quebec/BC rows."""
    row = by_id.get(cid)
    if row is not None:
        return row
    row = {
        "id": cid,
        "names": names,
        "holder": holder,
        "claim_count": 0,
        "neighbor_count": 0,
        "bbox": None,
        "color": "#e8b040",
        "extract": None,
        "mines": mines or [],
        "neighbors": [],
        "quebec_count": 0,
        "ontario_count": 0,
        "bc_count": 0,
        "ontario_extract": None,
        "bc_extract": None,
        "ontario_bbox": None,
        "bc_bbox": None,
        "quebec_bbox": None,
    }
    catalog["companies"].append(row)
    by_id[cid] = row
    return row


VALE_MINES: list[dict] = []  # No mine pins: no committed source for Vale shaft coords on this branch. Honest blank.


def main(args=None):
    catalog = json.loads((CLAIMS / "companies.json").read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in catalog["companies"]}
    only = set(args.company or []) if args else set()
    cache_dir = Path(args.cache_dir) if args and args.cache_dir else None
    if cache_dir is not None:
        cache_dir_ready(cache_dir)
    refresh = bool(args and args.refresh)

    if not only or "vale" in only:
        ensure_company_row(catalog, by_id, "vale", "Vale Canada Limited",
                           ["Vale Canada Limited", "Vale Canada Limitée", "VALE CANADA LIMITED"],
                           mines=[dict(m) for m in VALE_MINES])

    if args and (args.sweep_holders or args.judge_holders):
        if not only or "vale" in only:
            print("Ontario MLAS holder sweep (%VALE%)…")
            sweep = sweep_holders(HOLDER_SWEEP_NEEDLE, cache_dir, refresh)
            print(f"  {sweep['titles']} titles, {len(sweep['holders'])} distinct holders", flush=True)
            if args.judge_holders:
                print("Jev holder judgments (cached; Jev decides holder matches only)…")
                judge_holders(sweep["holders"], cache_dir, args.typesafe_env)

    for row in catalog["companies"]:
        if only and row["id"] not in only:
            continue
        if "quebec_count" not in row:
            row["quebec_count"] = row.get("claim_count") or 0

    failures: list[str] = []
    try:
        print("Ontario MLAS…")
        for cid, needles in ON_MATCH.items():
            if only and cid not in only:
                continue
            where = ontario_where(cid, needles, cache_dir)
            print(f"  {cid} {where}")

            def write_on(dest: Path, feats: list[dict], cid: str = cid) -> None:
                write_ontario_with_neighbors(cid, feats, dest, cache_dir, refresh)

            refresh_extract(by_id, cid, "ontario", ON_URL, where, ON_FIELDS, map_on, write_on,
                            cache_dir, refresh, failures)

        print("BC MTA…")
        for cid, needles in BC_MATCH.items():
            if only and cid not in only:
                continue
            where = like_where("OWNER_NAME", needles)
            print(f"  {cid} {where}")

            def write_bc(dest: Path, feats: list[dict], cid: str = cid) -> None:
                write_fc(dest, feats, f"{cid}-bc")
                print(f"    wrote {len(feats)} -> {dest.relative_to(ROOT)} ({dest.stat().st_size/1e6:.1f} MB)")

            refresh_extract(by_id, cid, "bc", BC_URL, where, BC_FIELDS, map_bc, write_bc,
                            cache_dir, refresh, failures)
    finally:
        write_catalog(catalog, only)
    if failures:
        print("Kept previous extract/catalog row for: " + ", ".join(failures), file=sys.stderr)
        return 1
    return 0


def refresh_extract(by_id: dict, cid: str, kind: str, url: str, where: str, fields: str, mapper,
                    writer, cache_dir: Path | None, refresh: bool, failures: list[str]) -> None:
    """Refetch one company extract. Delete it only when a clean live count says 0."""
    rel = f"claims/{cid}-{'ontario' if kind == 'ontario' else 'bc'}.geojson"
    dest = ROOT / rel
    try:
        raw = fetch_layer(url, where, fields, PAGE, cache_dir=cache_dir, refresh=refresh)
        feats = [mapper(f) for f in raw if f.get("geometry")]
        if feats:
            writer(dest, feats)
        else:
            count = live_count(url, where)
            if count != 0:
                raise ArcGISError(f"0 features parsed but live count is {count}")
            if dest.exists():
                dest.unlink()
                print(f"    live count 0 — removed {rel}", flush=True)
    except (ArcGISError, OSError, ValueError) as exc:
        print(f"    {cid} {kind} FAILED ({type(exc).__name__}: {exc}); keeping {rel}", flush=True)
        failures.append(f"{cid}:{kind}")
        return
    row = by_id.get(cid)
    if row:
        row[f"{kind}_count"] = len(feats)
        row[f"{kind}_extract"] = rel if feats else None
        row[f"{kind}_bbox"] = bbox_of(feats)


def write_catalog(catalog: dict, only: set[str]) -> None:
    for row in catalog["companies"]:
        if only and row["id"] not in only:
            continue
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
        "Ontario featured extracts also include neighboring MLAS titles within ~3 km. "
        "BC layers are the selected company's tenures only."
    )
    catalog["jurisdiction"] = "Quebec, Ontario, British Columbia"
    catalog["sources"] = {
        "quebec": "GESTIM active titles",
        "ontario": ON_SOURCE,
        "british_columbia": BC_SOURCE,
        "as_of_on_bc": AS_OF,
    }
    catalog["on_bc_built_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    atomic_write_json(CLAIMS / "companies.json", catalog)
    print("Updated claims/companies.json")


def neighbors_only(only_ids: list[str] | None = None, cache_dir: Path | None = None,
                   refresh: bool = False) -> None:
    want = set(only_ids or [])
    for cid in ON_MATCH:
        if want and cid not in want:
            continue
        rel = f"claims/{cid}-ontario.geojson"
        dest = ROOT / rel
        if not dest.is_file():
            print(f"  skip {cid}: no {rel}", flush=True)
            continue
        data = json.loads(dest.read_text(encoding="utf-8"))
        focus = [
            f for f in (data.get("features") or [])
            if (f.get("properties") or {}).get("role") != "neighbor"
        ]
        print(f"  {cid} {len(focus)} focus titles", flush=True)
        write_ontario_with_neighbors(cid, focus, dest, cache_dir, refresh)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--neighbors-only",
        action="store_true",
        help="Keep existing Ontario focus polygons and append MLAS neighbors around them.",
    )
    parser.add_argument("--company", action="append", default=[], help="Limit run to these ids (others untouched).")
    parser.add_argument("--cache-dir", default=str(DEFAULT_CACHE_DIR),
                        help="Gitignored raw-page cache (default claims/.cache/mlas).")
    parser.add_argument("--refresh", action="store_true", help="Ignore cache and refetch from the REST endpoints.")
    parser.add_argument("--cache-max-age-hours", type=float, default=CACHE_MAX_AGE_S / 3600,
                        help="Refetch cached REST pages older than this (default %(default)s).")
    parser.add_argument("--sweep-holders", action="store_true",
                        help="Attributes-only sweep of all MLAS titles matching %%VALE%% into the cache.")
    parser.add_argument("--judge-holders", action="store_true",
                        help="Jev-gate the swept holder strings (cached; Jev decides holder matches only). Implies --sweep-holders.")
    parser.add_argument("--typesafe-env", default=None,
                        help="Path to a TYPESAFE_API_KEY env file (else $TYPESAFE_API_KEY, else ~/.grok/typesafe.env).")
    parser.add_argument("--full-ontario", action="store_true",
                        help="Download the whole MLAS layer into tiled claims/mlas/ files + index.json.")
    parser.add_argument("--tile-size", type=int, default=FULL_TILE_SIZE,
                        help="Titles per full-download cache tile (default %(default)s).")
    parser.add_argument("--holders-index", action="store_true",
                        help="Build committed claims/mlas/holders.json offline from the cache tiles (no network).")
    args = parser.parse_args()
    CACHE_MAX_AGE_S = args.cache_max_age_hours * 3600
    if args.judge_holders:
        args.sweep_holders = True
    if args.holders_index:
        cache = Path(args.cache_dir) if args.cache_dir else None
        build_holder_index(cache)
    elif args.full_ontario:
        cache = Path(args.cache_dir) if args.cache_dir else None
        if cache is not None:
            cache_dir_ready(cache)
        fetch_full_ontario(args.tile_size, cache, args.refresh)
    elif args.neighbors_only:
        cache = Path(args.cache_dir) if args.cache_dir else None
        neighbors_only(args.company, cache, args.refresh)
    else:
        raise SystemExit(main(args))
