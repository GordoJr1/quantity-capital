#!/usr/bin/env python3
"""Monthly claims database for Quebec, British Columbia, Ontario, Yukon, Newfoundland and Labrador, and Nunavut.

One gitignored SQLite file (claims/.db/claims.sqlite) holds every title, with a
province column. Published files are the holder links, the search index, the
mine-radius JSON, and one PMTiles archive per province under claims/tiles/.
Each archive must stay under GitHub's 100 MiB file limit.

    py -3 scripts/claims_db.py --all --publish

On Windows use `py -3` or `python`. `python3` is the Microsoft Store stub and
does not run this script. The command ingests, links, builds tiles, writes
search and mine files, then stages only claims/links, claims/search,
claims/around, and claims/tiles. The database, the page cache, and the tape
files are not staged.

Tiles call `tippecanoe` on PATH. If that is missing, they call `wsl tippecanoe`
and translate the archive paths with `wslpath`. If neither is available the
build stops. go-pmtiles does not build these archives from GeoJSON.

Ontario keeps working through scripts/ontario_claims_db.py. This script copies
an existing claims/.db/ontario.sqlite instead of downloading MLAS again.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
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
    OFFSET_DEG,
    ON_URL,
    ArcGISError,
    cached_get_json,
    epoch_to_date,
    live_count,
)
import ontario_claims_db as ondb  # noqa: E402

CLAIMS = ROOT / "claims"
DB_PATH = CLAIMS / ".db" / "claims.sqlite"
LEGACY_ON = CLAIMS / ".db" / "ontario.sqlite"
TILES = CLAIMS / "tiles"
# GitHub Pages gzip-slices Range responses for .pmtiles (application/octet-stream).
# A name ending in .png is served as image/png, which Pages does not gzip, so
# byte ranges stay the raw archive. The bytes are still a PMTiles file.
TILE_PUBLIC_SUFFIX = ".pmtiles.png"
LINKS_PATH = CLAIMS / "links" / "holders.json"
SEARCH_HOLDERS = CLAIMS / "search" / "holders.json"
SEARCH_TITLES = CLAIMS / "search" / "titles"
AROUND_DIR = CLAIMS / "around"
REPORT_PATH = CLAIMS / "links" / "build-report.json"
SCORE_PATH = CLAIMS / "links" / "jev-prototype-score.json"
GITHUB_FILE_LIMIT = 100 * 1024 * 1024
SLEEP_S = 0.12
USER_AGENT = "Quantity Capital gordojr@proton.me"
STAGED = ("claims/links", "claims/search", "claims/around", "claims/tiles")
BC_WFS = "https://openmaps.gov.bc.ca/geo/pub/WHSE_MINERAL_TENURE.MTA_ACQUIRED_TENURE_SVW/wfs"
BC_TYPENAME = "pub:WHSE_MINERAL_TENURE.MTA_ACQUIRED_TENURE_SVW"
# Mineral claims and leases only. Placer and coal stay out of this database.
BC_CQL = (
    "TENURE_TYPE_DESCRIPTION='Mineral' AND "
    "(TENURE_SUB_TYPE_DESCRIPTION='CLAIM' OR TENURE_SUB_TYPE_DESCRIPTION='LEASE')"
)
QC_ZIP_URL = (
    "https://diffusion.mern.gouv.qc.ca/public/GESTIM/telechargements/"
    "Province_shape/TITRES_ACTIFS_ACTIVE_TITLES.zip"
)
QC_ZIP_NAME = "TITRES_ACTIFS_ACTIVE_TITLES.zip"
INSERT_TITLE = """
INSERT OR REPLACE INTO titles (
  province, objectid, title_id, holder, status, tenure_type,
  issue_date, anniversary_date, due_date, extension_date,
  area_ha, minx, miny, maxx, maxy, geom_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# Registry counts checked 2026-09-24. These layers are the ones in the claims review.
PROVINCES = {
    "ontario": {
        "code": "on",
        "name": "Ontario",
        "url": ON_URL,
        "fields": ondb.ON_FULL_FIELDS,
        "page": 1000,
        "cache": CLAIMS / ".cache" / "mlas",
        "id_field": "TENURE_NUMBER_ID",
        "holder_field": "HOLDER",
        "status_field": "TENURE_STATUS_DESC",
        "type_field": "TITLE_TYPE_DESC",
        "issue_field": "ISSUE_DATE",
        "anniversary_field": "ANNIVERSARY_DATE",
        "due_field": "CLAIM_DUE_DATE",
        "extension_field": "EXTENSION_DATE",
        "area_field": None,
        "region_needle": "ontario",
    },
    "yukon": {
        "code": "yt",
        "name": "Yukon",
        "url": "https://deptweb.gov.yk.ca/prod/rest/services/Mining_Class1/Class1Layers_WM/MapServer/2/query",
        "fields": "OBJECTID,GRANT_NUMBER,OWNER_NAME,TENURE_STATUS,STAKING_DATE,RECORDED_DATE,EXPIRY_DATE",
        "page": 2000,
        "cache": CLAIMS / ".cache" / "yt",
        "id_field": "GRANT_NUMBER",
        "holder_field": "OWNER_NAME",
        "status_field": "TENURE_STATUS",
        "type_field": None,
        "issue_field": "STAKING_DATE",
        "anniversary_field": "RECORDED_DATE",
        "due_field": "EXPIRY_DATE",
        "extension_field": None,
        "area_field": None,
        "region_needle": "yukon",
    },
    "newfoundland": {
        "code": "nl",
        "name": "Newfoundland and Labrador",
        "url": "https://dnrmaps.gov.nl.ca/arcgis/rest/services/GeoAtlas/Mineral_Lands/MapServer/0/query",
        "fields": "OBJECTID,LICENSE_NBR,CLIENT_NAME,STATUS,STAKEDATE,ISSDATE,EXPIRYDATE",
        "page": 1000,
        "cache": CLAIMS / ".cache" / "nl",
        "id_field": "LICENSE_NBR",
        "holder_field": "CLIENT_NAME",
        "status_field": "STATUS",
        "type_field": None,
        "issue_field": "ISSDATE",
        "anniversary_field": "STAKEDATE",
        "due_field": "EXPIRYDATE",
        "extension_field": None,
        "area_field": None,
        "region_needle": "newfoundland",
    },
    "nunavut": {
        "code": "nu",
        "name": "Nunavut",
        "url": "https://geo.sac-isc.gc.ca/geomatics/rest/services/Donnees_Ouvertes-Open_Data/Claim_minier_NU_Mineral_Claim/MapServer/0/query",
        "fields": "OBJECTID,CLAIM_NUM,OWNERS,CLAIM_STAT,ISSUE_DATE,ANNIV_DT,AREA_HA",
        "page": 2000,
        "cache": CLAIMS / ".cache" / "nu",
        "id_field": "CLAIM_NUM",
        "holder_field": "OWNERS",
        "status_field": "CLAIM_STAT",
        "type_field": None,
        "issue_field": "ISSUE_DATE",
        "anniversary_field": "ANNIV_DT",
        "due_field": None,
        "extension_field": None,
        "area_field": "AREA_HA",
        "region_needle": "nunavut",
    },
    "quebec": {
        "code": "qc",
        "name": "Quebec",
        "source": "gestim-shp",
        "url": QC_ZIP_URL,
        "page": 2000,
        "cache": CLAIMS / ".cache" / "qc",
        "region_needle": "quebec",
    },
    "british-columbia": {
        "code": "bc",
        "name": "British Columbia",
        "source": "bc-wfs",
        "url": BC_WFS,
        "page": 1000,
        "cache": CLAIMS / ".cache" / "bc",
        "region_needle": "british columbia",
    },
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY,
  value TEXT
);
CREATE TABLE IF NOT EXISTS titles (
  province TEXT NOT NULL,
  objectid INTEGER NOT NULL,
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
  geom_json TEXT,
  PRIMARY KEY (province, objectid)
);
CREATE INDEX IF NOT EXISTS idx_titles_holder ON titles(province, holder);
CREATE INDEX IF NOT EXISTS idx_titles_id ON titles(province, title_id);
CREATE INDEX IF NOT EXISTS idx_titles_bbox ON titles(province, minx, maxx, miny, maxy);
"""

SLASH_PCT = __import__("re").compile(
    r"([^/]+?)\s*\(\s*(\d+(?:\.\d+)?)\s*%\s*\)",
)
DASH_PCT = __import__("re").compile(
    r"^(.*?)\s+-\s+(\d+(?:\.\d+)?)\s*%?\s*$",
)
ONTARIO_PCT = __import__("re").compile(r"\(\d+(?:\.\d+)?\)")


def log(msg: str) -> None:
    print(msg, flush=True)


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(SCHEMA)
    return con


def province_ids(selected: list[str] | None, all_provinces: bool) -> list[str]:
    if all_provinces or not selected:
        return list(PROVINCES)
    unknown = [name for name in selected if name not in PROVINCES]
    if unknown:
        raise SystemExit(f"unknown province {unknown[0]}. Choose from {', '.join(PROVINCES)}")
    return selected


def registry_holder_to_ontario(raw: str | None) -> str:
    """Turn Yukon/Nunavut/NL owner strings into the '(pct) NAME' form the linker already parses."""
    text = (raw or "").strip()
    if not text or ONTARIO_PCT.search(text):
        return text
    parts = SLASH_PCT.findall(text)
    if parts and sum(float(pct) for _name, pct in parts) >= 50:
        return ", ".join(f"({_trim_pct(pct)}) {name.strip(' ,/')}" for name, pct in parts)
    dashed = DASH_PCT.match(text)
    if dashed:
        return f"({_trim_pct(dashed.group(2))}) {dashed.group(1).strip()}"
    return text


def _trim_pct(value: str) -> str:
    number = float(value)
    if number == int(number):
        return str(int(number))
    return str(number)


def _prop(props: dict, field: str | None):
    if not field:
        return None
    return props.get(field)


def _date(value):
    if value is None or value == "":
        return None
    return epoch_to_date(value)


def _area(spec: dict, props: dict, geom) -> float | None:
    if spec.get("area_field"):
        raw = props.get(spec["area_field"])
        try:
            if raw not in (None, ""):
                return round(float(raw), 2)
        except (TypeError, ValueError):
            pass
    return ondb.geometry_area_ha(geom)


def copy_legacy_ontario(con: sqlite3.Connection) -> int:
    have = con.execute("SELECT COUNT(*) AS n FROM titles WHERE province='ontario'").fetchone()["n"]
    if have:
        log(f"  ontario already in {DB_PATH.name}: {have} titles")
        return have
    if not LEGACY_ON.is_file():
        return 0
    log(f"  copying {LEGACY_ON.name} into {DB_PATH.name}")
    legacy = sqlite3.connect(LEGACY_ON)
    try:
        legacy.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        legacy.close()
    con.execute("ATTACH DATABASE ? AS legacy", (str(LEGACY_ON),))
    con.execute(
        """
        INSERT OR REPLACE INTO titles (
          province, objectid, title_id, holder, status, tenure_type,
          issue_date, anniversary_date, due_date, extension_date,
          area_ha, minx, miny, maxx, maxy, geom_json
        )
        SELECT 'ontario', objectid, title_id, holder, status, tenure_type,
               issue_date, anniversary_date, due_date, extension_date,
               area_ha, minx, miny, maxx, maxy, geom_json
        FROM legacy.titles
        """
    )
    con.execute(
        "INSERT INTO meta(key, value) VALUES('done:ontario', '1') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    con.commit()
    con.execute("DETACH DATABASE legacy")
    n = con.execute("SELECT COUNT(*) AS n FROM titles WHERE province='ontario'").fetchone()["n"]
    log(f"  copied {n} Ontario titles")
    return n


def _mark(con: sqlite3.Connection, key: str, value: str) -> None:
    con.execute(
        "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def format_registry_holder(name: str | None, percent) -> str | None:
    """'(pct) NAME' so the linker sees the interest. A blank name stays blank."""
    text = (name or "").strip()
    if not text:
        return None
    if percent in (None, ""):
        return text
    try:
        number = float(percent)
    except (TypeError, ValueError):
        return text
    return f"({_trim_pct(str(number))}) {text}"


def _ymd(value) -> str | None:
    if value is None or value == "":
        return None
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return f"{text[0:4]}-{text[4:6]}-{text[6:8]}"
    return _date(text)


def _title_tuple(province, oid, title_id, holder, status, tenure_type, issue, due, area, geom):
    box = ondb.geometry_bbox(geom)
    return (
        province,
        int(oid),
        str(title_id),
        holder,
        status,
        tenure_type,
        issue,
        None,
        due,
        None,
        area,
        box[0] if box else None,
        box[1] if box else None,
        box[2] if box else None,
        box[3] if box else None,
        json.dumps(geom, separators=(",", ":")) if geom else None,
    )


def _finish_ingest(con: sqlite3.Connection, name: str, expected: int | None, started: float, pages: int, extra: dict | None = None) -> dict:
    total = con.execute("SELECT COUNT(*) AS n FROM titles WHERE province=?", (name,)).fetchone()["n"]
    holders = con.execute(
        "SELECT COUNT(DISTINCT holder) AS n FROM titles WHERE province=?", (name,)
    ).fetchone()["n"]
    _mark(con, f"done:{name}", "1")
    _mark(con, f"ingested_at:{name}", ondb.now_iso())
    if expected is not None:
        _mark(con, f"live_count:{name}", str(expected))
    con.commit()
    elapsed = round(time.time() - started, 1)
    log(f"  {name} {total} titles, {holders} holders in {elapsed}s")
    if expected is not None and total != expected:
        raise SystemExit(f"{name} ingest mismatch: sqlite {total} expected {expected}")
    out = {
        "province": name,
        "titles": total,
        "holders": holders,
        "live_count": expected,
        "seconds": elapsed,
        "pages": pages,
    }
    if extra:
        out.update(extra)
    return out


def http_bytes(url: str, params: dict | None = None, timeout: int = 180) -> bytes:
    if params:
        joiner = "&" if "?" in url else "?"
        url = url + joiner + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "application/json, application/xml, */*",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read(400).decode("utf-8", "replace")
        raise SystemExit(f"HTTP {exc.code} for {url[:160]}: {detail}") from exc


def wfs_number_matched(body: str) -> int:
    match = re.search(r'numberMatched="(\d+)"', body)
    if not match:
        raise SystemExit("WFS hits response has no numberMatched")
    return int(match.group(1))


def shp_to_geojson(content: bytes) -> dict | None:
    """ESRI polygon (type 5) to a GeoJSON Polygon. Null shapes return None."""
    if len(content) < 4:
        return None
    shape_type = struct.unpack_from("<i", content, 0)[0]
    if shape_type == 0:
        return None
    if shape_type != 5:
        raise SystemExit(f"unsupported shapefile type {shape_type}")
    if len(content) < 44:
        raise SystemExit("polygon record is short")
    nparts, npts = struct.unpack_from("<2i", content, 36)
    if nparts < 1 or npts < 1:
        return None
    parts = struct.unpack_from(f"<{nparts}i", content, 44)
    offset = 44 + 4 * nparts
    rings = []
    for index, start in enumerate(parts):
        end = parts[index + 1] if index + 1 < nparts else npts
        ring = []
        for point in range(start, end):
            lon, lat = struct.unpack_from("<2d", content, offset + point * 16)
            ring.append([round(lon, 6), round(lat, 6)])
        if len(ring) < 3:
            continue
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        rings.append(ring)
    if not rings:
        return None
    return {"type": "Polygon", "coordinates": rings}


def _dbf_layout(header: bytes) -> tuple[int, int, dict[str, tuple[int, int]]]:
    if len(header) < 32:
        raise SystemExit("DBF header is short")
    count, hlen, rlen = struct.unpack_from("<4xIHH", header, 0)
    if len(header) < hlen:
        raise SystemExit("DBF header was cut off")
    body = header[32:hlen]
    if not body or body[-1] != 0x0D:
        raise SystemExit("DBF header is missing its terminator")
    fields: dict[str, tuple[int, int]] = {}
    cursor = 1
    for start in range(0, len(body) - 1, 32):
        desc = body[start:start + 32]
        if len(desc) < 32:
            break
        fname = desc[:11].split(b"\x00")[0].decode("ascii", "replace")
        fields[fname] = (cursor, desc[16])
        cursor += desc[16]
    if cursor != rlen:
        raise SystemExit(f"DBF field widths sum to {cursor}, record length is {rlen}")
    return count, rlen, fields


def iter_gestim_titles(dbf_stream, shp_stream):
    """Yield one dict per shapefile record. Active cells set keep=True."""
    prefix = dbf_stream.read(32)
    if len(prefix) < 32:
        raise SystemExit("GESTIM DBF is short")
    hlen = struct.unpack_from("<H", prefix, 8)[0]
    header = prefix + dbf_stream.read(hlen - 32)
    count, rlen, fields = _dbf_layout(header)
    wanted = (
        "TIT_NO", "STI_CODE", "STI_DES_AN", "DET_NOM", "DET_POURC",
        "TPO_DES_AN", "TIT_DAT_EM", "TIT_DAT_EX", "TIT_SUPRF",
    )
    missing = [name for name in wanted if name not in fields]
    if missing:
        raise SystemExit("GESTIM DBF is missing " + ", ".join(missing))
    shp_header = shp_stream.read(100)
    if len(shp_header) < 100:
        raise SystemExit("GESTIM shapefile header is short")
    for index in range(count):
        record = dbf_stream.read(rlen)
        rec_head = shp_stream.read(8)
        if len(record) < rlen or len(rec_head) < 8:
            raise SystemExit(f"GESTIM file ended at record {index}")
        recno, words = struct.unpack(">2i", rec_head)
        content = shp_stream.read(words * 2)
        if len(content) < words * 2:
            raise SystemExit(f"GESTIM shape ended at record {recno}")
        if record[:1] == b"*":
            continue

        def field(name: str) -> str:
            start, length = fields[name]
            return record[start:start + length].decode("cp1252", "replace").strip()

        status_code = field("STI_CODE")
        area_raw = field("TIT_SUPRF")
        try:
            area = round(float(area_raw), 2) if area_raw else None
        except ValueError:
            area = None
        yield {
            "objectid": recno,
            "title_id": field("TIT_NO"),
            "holder": format_registry_holder(field("DET_NOM"), field("DET_POURC")),
            "status": field("STI_DES_AN") or status_code,
            "status_code": status_code,
            "tenure_type": field("TPO_DES_AN"),
            "issue_date": _ymd(field("TIT_DAT_EM")),
            "due_date": _ymd(field("TIT_DAT_EX")),
            "area_ha": area,
            "geom": shp_to_geojson(content),
            "keep": status_code.upper() == "A",
        }


def _ensure_qc_zip(cache: Path, refresh: bool) -> Path:
    cache.mkdir(parents=True, exist_ok=True)
    dest = cache / QC_ZIP_NAME
    if dest.is_file() and dest.stat().st_size > 1_000_000 and not refresh:
        return dest
    staged = Path("/tmp/qc-gestim/active.zip")
    if staged.is_file() and staged.stat().st_size > 1_000_000 and not refresh:
        shutil.copyfile(staged, dest)
        log(f"  quebec zip copied from {staged}")
        return dest
    log(f"  quebec downloading {QC_ZIP_URL}")
    dest.write_bytes(http_bytes(QC_ZIP_URL, timeout=300))
    return dest


def ingest_gestim(con: sqlite3.Connection, name: str, refresh: bool, limit_pages: int | None) -> dict:
    spec = PROVINCES[name]
    started = time.time()
    con.execute("DELETE FROM titles WHERE province=?", (name,))
    con.commit()
    archive = _ensure_qc_zip(spec["cache"], refresh)
    dropped: dict[str, int] = defaultdict(int)
    unique_ids: set[str] = set()
    active = 0
    inserted = 0
    batch: list[tuple] = []
    cap = None if limit_pages is None else limit_pages * spec["page"]
    log(f"  quebec reading {archive.name}")
    with zipfile.ZipFile(archive) as bundle:
        dbf_name = next(info.filename for info in bundle.infolist() if info.filename.lower().endswith(".dbf"))
        shp_name = next(info.filename for info in bundle.infolist() if info.filename.lower().endswith(".shp"))
        member = bundle.getinfo(shp_name)
        source_date = "%04d-%02d-%02d" % member.date_time[:3]
        with bundle.open(dbf_name) as dbf_stream, bundle.open(shp_name) as shp_stream:
            for row in iter_gestim_titles(dbf_stream, shp_stream):
                if not row["keep"]:
                    dropped[row["status_code"] or "?"] += 1
                    continue
                active += 1
                if cap is not None and active > cap:
                    active -= 1
                    break
                if not row["title_id"] or not row["geom"]:
                    dropped["no-geometry"] += 1
                    active -= 1
                    continue
                unique_ids.add(row["title_id"])
                batch.append(_title_tuple(
                    name, row["objectid"], row["title_id"], row["holder"], row["status"],
                    row["tenure_type"], row["issue_date"], row["due_date"], row["area_ha"], row["geom"],
                ))
                if len(batch) >= 2000:
                    con.executemany(INSERT_TITLE, batch)
                    con.commit()
                    inserted += len(batch)
                    batch.clear()
                    if inserted % 20000 == 0:
                        log(f"  quebec inserted {inserted}")
    if batch:
        con.executemany(INSERT_TITLE, batch)
        con.commit()
        inserted += len(batch)
    log(f"  quebec active cells {active}; unique titles {len(unique_ids)}; dropped {dict(dropped)}; file date {source_date}")
    if limit_pages is not None:
        total = con.execute("SELECT COUNT(*) AS n FROM titles WHERE province=?", (name,)).fetchone()["n"]
        return {"province": name, "titles": total, "limited": True, "active_cells": active}
    return _finish_ingest(con, name, active, started, pages=1, extra={
        "unique_title_ids": len(unique_ids),
        "dropped_status": dict(dropped),
        "source_date": source_date,
        "source": QC_ZIP_URL,
    })


def ingest_bc_wfs(con: sqlite3.Connection, name: str, refresh: bool, limit_pages: int | None) -> dict:
    spec = PROVINCES[name]
    started = time.time()
    con.execute("DELETE FROM titles WHERE province=?", (name,))
    con.commit()
    cache = spec["cache"]
    cache.mkdir(parents=True, exist_ok=True)
    hits_xml = http_bytes(spec["url"], {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": BC_TYPENAME,
        "resultType": "hits",
        "CQL_FILTER": BC_CQL,
    }).decode("utf-8", "replace")
    expected = wfs_number_matched(hits_xml)
    registry_xml = http_bytes(spec["url"], {
        "service": "WFS",
        "version": "2.0.0",
        "request": "GetFeature",
        "typeNames": BC_TYPENAME,
        "resultType": "hits",
    }).decode("utf-8", "replace")
    registry_total = wfs_number_matched(registry_xml)
    log(f"  british-columbia mineral claims+leases {expected}; registry total {registry_total}")
    _mark(con, "registry_total:british-columbia", str(registry_total))
    start = 0
    pages = 0
    seen = 0
    while start < expected:
        if limit_pages is not None and pages >= limit_pages:
            break
        cache_path = cache / f"page-{start}.json"
        if cache_path.is_file() and not refresh:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
        else:
            raw = http_bytes(spec["url"], {
                "service": "WFS",
                "version": "2.0.0",
                "request": "GetFeature",
                "typeNames": BC_TYPENAME,
                "outputFormat": "application/json",
                "srsName": "EPSG:4326",
                "count": str(spec["page"]),
                "startIndex": str(start),
                "sortBy": "OBJECTID",
                "CQL_FILTER": BC_CQL,
            })
            cache_path.write_bytes(raw)
            data = json.loads(raw.decode("utf-8"))
            time.sleep(SLEEP_S)
        features = data.get("features") or []
        if not features:
            break
        rows = []
        checked_axis = pages > 0
        for feat in features:
            props = feat.get("properties") or {}
            try:
                oid = int(props.get("OBJECTID"))
            except (TypeError, ValueError):
                continue
            title_id = props.get("TENURE_NUMBER_ID")
            if title_id in (None, ""):
                continue
            geom = feat.get("geometry")
            if not geom:
                continue
            if not checked_axis:
                box = ondb.geometry_bbox(geom)
                if not box or not (-141 <= box[0] <= -113 and 47 <= box[1] <= 61):
                    raise SystemExit(f"british-columbia geometry is not lon/lat: {box}")
                checked_axis = True
            area = props.get("AREA_IN_HECTARES")
            try:
                area = round(float(area), 2) if area not in (None, "") else ondb.geometry_area_ha(geom)
            except (TypeError, ValueError):
                area = ondb.geometry_area_ha(geom)
            rows.append(_title_tuple(
                name,
                oid,
                title_id,
                format_registry_holder(props.get("OWNER_NAME"), props.get("PERCENT_OWNERSHIP")),
                props.get("TENURE_SUB_TYPE_DESCRIPTION"),
                props.get("TITLE_TYPE_DESCRIPTION"),
                _ymd(props.get("ISSUE_DATE")),
                _ymd(props.get("GOOD_TO_DATE")),
                area,
                geom,
            ))
        con.executemany(INSERT_TITLE, rows)
        con.commit()
        seen += len(features)
        pages += 1
        returned = int(data.get("numberReturned") or len(features))
        start += returned
        if pages % 5 == 0 or start >= expected:
            log(f"  british-columbia pages={pages} features={seen} start={start}")
        if returned < spec["page"]:
            break
    if limit_pages is not None:
        total = con.execute("SELECT COUNT(*) AS n FROM titles WHERE province=?", (name,)).fetchone()["n"]
        return {"province": name, "titles": total, "limited": True, "live_count": expected, "registry_total": registry_total}
    return _finish_ingest(con, name, expected, started, pages, extra={
        "registry_total": registry_total,
        "kept": "mineral claims and leases",
        "source": BC_WFS,
    })


def ingest_province(con: sqlite3.Connection, name: str, refresh: bool, limit_pages: int | None) -> dict:
    spec = PROVINCES[name]
    if name == "ontario" and not refresh:
        copied = copy_legacy_ontario(con)
        done = con.execute("SELECT value FROM meta WHERE key='done:ontario'").fetchone()
        if copied and done and done["value"] == "1" and not limit_pages:
            return {"province": name, "titles": copied, "cached": True}
    if refresh:
        con.execute("DELETE FROM titles WHERE province=?", (name,))
        _mark(con, f"done:{name}", "0")
        con.commit()
    done = con.execute("SELECT value FROM meta WHERE key=?", (f"done:{name}",)).fetchone()
    if done and done["value"] == "1" and not refresh and not limit_pages:
        n = con.execute("SELECT COUNT(*) AS n FROM titles WHERE province=?", (name,)).fetchone()["n"]
        log(f"  {name} already complete: {n} titles")
        return {"province": name, "titles": n, "cached": True}
    source = spec.get("source") or "arcgis"
    if source == "gestim-shp":
        return ingest_gestim(con, name, refresh, limit_pages)
    if source == "bc-wfs":
        return ingest_bc_wfs(con, name, refresh, limit_pages)
    after_row = con.execute(
        "SELECT MAX(objectid) AS m FROM titles WHERE province=?", (name,)
    ).fetchone()
    after = int(after_row["m"] or 0)
    log(f"  {name} download from OBJECTID>{after} page={spec['page']}")
    try:
        expected = live_count(spec["url"], "1=1")
    except ArcGISError as exc:
        raise SystemExit(f"{name} count failed: {exc}") from exc
    log(f"  {name} live count {expected}")
    pages = 0
    inserted = 0
    complete = False
    t0 = time.time()
    cache = spec["cache"]
    cache.mkdir(parents=True, exist_ok=True)
    while True:
        if limit_pages is not None and pages >= limit_pages:
            break
        where = "1=1" if after <= 0 else f"OBJECTID>{after}"
        params = {
            "where": where,
            "outFields": spec["fields"],
            "outSR": 4326,
            "f": "geojson",
            "resultRecordCount": spec["page"],
            "orderByFields": "OBJECTID ASC",
            "maxAllowableOffset": OFFSET_DEG,
        }
        data = cached_get_json(spec["url"], params, cache, refresh and pages == 0 and after == 0)
        if data.get("error"):
            raise SystemExit(f"{name} page error: {data['error']}")
        batch = data.get("features") or []
        if not batch:
            complete = True
            break
        rows = []
        oids = []
        for feat in batch:
            props = feat.get("properties") or {}
            try:
                oid = int(props.get("OBJECTID") or feat.get("id"))
            except (TypeError, ValueError):
                continue
            oids.append(oid)
            title_id = _prop(props, spec["id_field"])
            if title_id in (None, ""):
                continue
            geom = feat.get("geometry")
            box = ondb.geometry_bbox(geom)
            rows.append((
                name,
                oid,
                str(title_id),
                _prop(props, spec["holder_field"]) or None,
                _prop(props, spec["status_field"]),
                _prop(props, spec["type_field"]),
                _date(_prop(props, spec["issue_field"])),
                _date(_prop(props, spec["anniversary_field"])),
                _date(_prop(props, spec["due_field"])),
                _date(_prop(props, spec["extension_field"])),
                _area(spec, props, geom),
                box[0] if box else None,
                box[1] if box else None,
                box[2] if box else None,
                box[3] if box else None,
                json.dumps(geom, separators=(",", ":")) if geom else None,
            ))
        con.executemany(
            """
            INSERT OR REPLACE INTO titles (
              province, objectid, title_id, holder, status, tenure_type,
              issue_date, anniversary_date, due_date, extension_date,
              area_ha, minx, miny, maxx, maxy, geom_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        con.commit()
        inserted += len(rows)
        pages += 1
        if oids:
            after = max(oids)
        if pages % 10 == 0:
            log(f"  {name} pages={pages} inserted={inserted} after={after}")
        if len(batch) < spec["page"] and not data.get("exceededTransferLimit"):
            complete = True
            break
        time.sleep(SLEEP_S)
    total = con.execute("SELECT COUNT(*) AS n FROM titles WHERE province=?", (name,)).fetchone()["n"]
    holders = con.execute(
        "SELECT COUNT(DISTINCT holder) AS n FROM titles WHERE province=?", (name,)
    ).fetchone()["n"]
    finished = complete and limit_pages is None
    _mark(con, f"done:{name}", "1" if finished else "0")
    _mark(con, f"ingested_at:{name}", ondb.now_iso())
    _mark(con, f"live_count:{name}", str(expected))
    con.commit()
    elapsed = round(time.time() - t0, 1)
    log(f"  {name} {total} titles, {holders} holders in {elapsed}s")
    if finished and total + 50 < expected:
        raise SystemExit(f"{name} ingest short: sqlite {total} live {expected}")
    return {
        "province": name,
        "titles": total,
        "holders": holders,
        "live_count": expected,
        "seconds": elapsed,
        "pages": pages,
    }


def holder_rows(con: sqlite3.Connection, names: list[str]) -> list[dict]:
    marks = ",".join("?" for _ in names)
    merged: dict[tuple[str, str], dict] = {}
    for row in con.execute(
        f"""
        SELECT province, holder, COUNT(*) AS n,
               MIN(minx) AS minx, MIN(miny) AS miny, MAX(maxx) AS maxx, MAX(maxy) AS maxy
        FROM titles
        WHERE province IN ({marks})
        GROUP BY province, holder
        """,
        names,
    ):
        key = (row["province"], ondb.holder_key(row["holder"]))
        slot = merged.setdefault(key, {
            "province": row["province"],
            "holder": key[1],
            "count": 0,
            "minx": None,
            "miny": None,
            "maxx": None,
            "maxy": None,
        })
        slot["count"] += row["n"]
        for edge, agg in (("minx", min), ("miny", min), ("maxx", max), ("maxy", max)):
            value = row[edge]
            if value is None:
                continue
            slot[edge] = value if slot[edge] is None else agg(slot[edge], value)
    rows = []
    for slot in merged.values():
        bbox = None
        if None not in (slot["minx"], slot["miny"], slot["maxx"], slot["maxy"]):
            bbox = [round(slot["minx"], 4), round(slot["miny"], 4), round(slot["maxx"], 4), round(slot["maxy"], 4)]
        rows.append({
            "province": slot["province"],
            "holder": slot["holder"],
            "count": slot["count"],
            "bbox": bbox,
        })
    rows.sort(key=lambda row: (row["province"], -row["count"], row["holder"]))
    return rows


def link_rows(rows: list[dict], use_jev: bool, harvest: bool) -> dict:
    t0 = time.time()
    book = ondb.load_company_book()
    aliases = ondb.load_aliases()
    exact, phrases, token_index = ondb.name_indexes(book)
    extract_map = ondb.harvest_extract_holders() if harvest else {}
    judge = ondb.jev_judge if use_jev else None
    linked = []
    jev_calls = 0
    for i, src in enumerate(rows):
        prepared = registry_holder_to_ontario(src["holder"])
        probe = ondb.match_holder(
            prepared, book, aliases, extract_map, exact, phrases, token_index, judge=None,
        )
        if use_jev and probe["ambiguous"]:
            jev_calls += 1
            if jev_calls <= 8 or jev_calls % 25 == 0:
                log(f"  jev {jev_calls}: {src['province']} {src['holder'][:80]}")
            matched = ondb.match_holder(
                prepared, book, aliases, extract_map, exact, phrases, token_index, judge=judge,
            )
        else:
            matched = probe
        matched["province"] = src["province"]
        matched["holder"] = src["holder"]
        matched["count"] = src["count"]
        matched["bbox"] = src["bbox"]
        linked.append(matched)
        if (i + 1) % 500 == 0:
            log(f"  matched {i + 1}/{len(rows)}")
    by_province = {}
    for name in PROVINCES:
        subset = [row for row in linked if row["province"] == name]
        if not subset:
            continue
        titles = sum(row["count"] for row in subset)
        linked_titles = sum(row["count"] for row in subset if row.get("company_id"))
        by_method: dict[str, int] = defaultdict(int)
        for row in subset:
            if row.get("method"):
                by_method[row["method"]] += row["count"]
        by_province[name] = {
            "name": PROVINCES[name]["name"],
            "titles": titles,
            "holders": len(subset),
            "linked_titles": linked_titles,
            "linked_holders": sum(1 for row in subset if row.get("company_id")),
            "coverage": round(linked_titles / titles, 6) if titles else 0,
            "by_method_titles": dict(sorted(by_method.items())),
        }
    titles = sum(row["count"] for row in linked)
    linked_titles = sum(row["count"] for row in linked if row.get("company_id"))
    watch = {}
    for row in linked:
        for company in row.get("companies") or []:
            cid = company["company_id"]
            if cid in {"banyan-gold", "sitka-gold", "seabridge-gold", "new-found-gold", "b2gold", "kinross"}:
                slot = watch.setdefault(cid, {"titles": 0, "holders": []})
                slot["titles"] += row["count"]
                if len(slot["holders"]) < 6:
                    slot["holders"].append({
                        "province": row["province"],
                        "holder": row["holder"],
                        "count": row["count"],
                        "method": company.get("method"),
                        "ticker": company.get("ticker"),
                    })
    payload = {
        "dataset": "Provincial mining titles linked to site companies. Not legal title.",
        "generated_at": ondb.now_iso(),
        "provinces": by_province,
        "titles": titles,
        "linked_titles": linked_titles,
        "coverage": round(linked_titles / titles, 6) if titles else 0,
        "holders": len(linked),
        "linked_holders": sum(1 for row in linked if row.get("company_id")),
        "jev": use_jev,
        "jev_ambiguous_holders": jev_calls,
        "watch": watch,
        "seconds": round(time.time() - t0, 1),
        "rows": linked,
    }
    for name, slot in by_province.items():
        log(f"  {name} coverage {slot['coverage']:.1%} ({slot['linked_titles']}/{slot['titles']})")
    return payload


def write_links(payload: dict) -> None:
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
            })
        rows.append({
            "province": row["province"],
            "holder": row["holder"],
            "count": row["count"],
            "bbox": row.get("bbox"),
            "company_id": row.get("company_id"),
            "method": row.get("method"),
            "companies": companies,
        })
    out = {k: v for k, v in payload.items() if k != "rows"}
    out["rows"] = rows
    ondb.atomic_write_json(LINKS_PATH, out, indent=1)
    log(f"  wrote {LINKS_PATH.relative_to(ROOT)} ({LINKS_PATH.stat().st_size / 1e6:.2f} MB)")


def write_search(payload: dict) -> dict[str, dict[str, int]]:
    holders = []
    for row in payload["rows"]:
        primary = (row.get("companies") or [None])[0]
        holders.append({
            "province": row["province"],
            "code": PROVINCES[row["province"]]["code"],
            "name": row["holder"],
            "count": row["count"],
            "bbox": row.get("bbox"),
            "company_id": row.get("company_id"),
            "company": primary.get("company") if primary else None,
            "ticker": primary.get("ticker") if primary else None,
        })
    body = {
        "generated_at": payload["generated_at"],
        "provinces": payload["provinces"],
        "titles": payload["titles"],
        "linked_titles": payload["linked_titles"],
        "coverage": payload["coverage"],
        "disclaimer": "Not legal title. Generalized registry polygons for viewing.",
        "holders": holders,
    }
    ondb.atomic_write_json(SEARCH_HOLDERS, body, indent=1)
    indexes: dict[str, dict[str, int]] = defaultdict(dict)
    for i, row in enumerate(holders):
        indexes[row["province"]][row["name"]] = i
    return indexes


def write_title_shards(con: sqlite3.Connection, indexes: dict[str, dict[str, int]], names: list[str]) -> dict:
    if SEARCH_TITLES.exists():
        for old in SEARCH_TITLES.glob("*.json"):
            old.unlink()
    manifest = {"generated_at": ondb.now_iso(), "provinces": {}}
    for name in names:
        code = PROVINCES[name]["code"]
        dest = SEARCH_TITLES / code
        if dest.exists():
            for old in dest.glob("*.json"):
                old.unlink()
        dest.mkdir(parents=True, exist_ok=True)
        shards: dict[str, dict[str, int]] = defaultdict(dict)
        duplicates = 0
        missing = 0
        seen = set()
        index = indexes.get(name) or {}
        for title_id, holder in con.execute(
            "SELECT title_id, holder FROM titles WHERE province=?", (name,)
        ):
            if title_id in seen:
                duplicates += 1
            seen.add(title_id)
            key = ondb.holder_key(holder)
            idx = index.get(key)
            if idx is None:
                missing += 1
                continue
            shards[ondb.shard_key(title_id)][str(title_id)] = idx
        shard_rows = []
        total_bytes = 0
        for shard_id in sorted(shards):
            path = dest / f"{shard_id}.json"
            ondb.atomic_write_json(path, shards[shard_id], indent=None)
            size = path.stat().st_size
            total_bytes += size
            shard_rows.append({
                "id": shard_id,
                "file": str(path.relative_to(ROOT)),
                "titles": len(shards[shard_id]),
                "bytes": size,
            })
        manifest["provinces"][name] = {
            "code": code,
            "titles": len(seen),
            "duplicate_title_ids": duplicates,
            "missing_holder": missing,
            "bytes": total_bytes,
            "shards": len(shard_rows),
        }
        ondb.atomic_write_json(dest / "index.json", manifest["provinces"][name], indent=1)
        log(f"  search {name} {len(shard_rows)} shards {total_bytes / 1e6:.2f} MB missing {missing}")
    ondb.atomic_write_json(SEARCH_TITLES / "index.json", manifest, indent=1)
    return manifest


def _assets_for(book: dict, needle: str) -> tuple[list[dict], list[dict]]:
    found = []
    missing = []
    seen = set()
    for cid, slot in book.items():
        for asset in slot.get("asset_rows") or []:
            region = (asset.get("region") or "").lower()
            if needle not in region:
                continue
            key = (cid, asset.get("id"))
            if key in seen:
                continue
            seen.add(key)
            row = {
                "mine_id": asset.get("id"),
                "company_id": cid,
                "name": asset.get("name") or asset.get("id"),
                "region": asset.get("region") or "",
                "ticker": slot.get("ticker"),
            }
            if asset.get("lat") is None or asset.get("lon") is None:
                missing.append(row)
                continue
            row["lat"] = asset["lat"]
            row["lon"] = asset["lon"]
            found.append(row)
    if needle == "ontario":
        found.sort(key=lambda mine: (0 if "sudbury" in f"{mine['mine_id']} {mine['name']}".lower() else 1, mine["name"].lower()))
    else:
        found.sort(key=lambda mine: mine["name"].lower())
    return found, missing


def write_around(con: sqlite3.Connection, payload: dict, names: list[str]) -> dict:
    book = ondb.load_company_book()
    holder_company = {}
    for row in payload["rows"]:
        primary = (row.get("companies") or [{}])[0]
        holder_company[(row["province"], row["holder"])] = {
            "company_id": row.get("company_id"),
            "ticker": primary.get("ticker"),
        }
    written = {}
    for name in names:
        spec = PROVINCES[name]
        mines, missing = _assets_for(book, spec["region_needle"])
        log(f"  {name} mines with coordinates: {len(mines)}; without: {len(missing)}")
        t0 = time.time()
        results = []
        for mine in mines:
            near = {}
            for km in (5, 10):
                near[str(km)] = _near(con, name, mine["lon"], mine["lat"], km, holder_company)
            results.append({**mine, "km": near})
            log(f"  {name} {mine['mine_id']}: {near['5']['titles']} within 5 km, {near['10']['titles']} within 10 km")
        body = {
            "dataset": f"{spec['name']} titles within 5 km and 10 km of beta mine coordinates. Not legal title.",
            "generated_at": ondb.now_iso(),
            "province": spec["name"],
            "province_id": name,
            "radii_km": [5, 10],
            "mines": len(results),
            "sudbury_first": name == "ontario",
            "unlocated": missing,
            "seconds": round(time.time() - t0, 1),
            "results": results,
        }
        filename = "ontario-mines.json" if name == "ontario" else f"{name}-mines.json"
        path = AROUND_DIR / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        ondb.atomic_write_json(path, body, indent=1)
        written[name] = {"bytes": path.stat().st_size, "mines": len(results), "unlocated": len(missing)}
        log(f"  wrote {path.relative_to(ROOT)} ({path.stat().st_size / 1e3:.0f} KB)")
    return written


def _near(con, province: str, lon: float, lat: float, km: float, holder_company: dict) -> dict:
    minx, miny, maxx, maxy = ondb._bbox_hits(lon, lat, km)
    by_holder: dict[str, int] = defaultdict(int)
    titles = 0
    for row in con.execute(
        """
        SELECT holder, geom_json FROM titles
        WHERE province = ?
          AND geom_json IS NOT NULL
          AND minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ?
        """,
        (province, maxx, minx, maxy, miny),
    ):
        try:
            geom = json.loads(row["geom_json"])
        except json.JSONDecodeError:
            continue
        dist = ondb.geometry_distance_km(lon, lat, geom)
        if dist is None or dist > km:
            continue
        titles += 1
        by_holder[ondb.holder_key(row["holder"])] += 1
    holders = []
    by_company: dict[str, int] = defaultdict(int)
    for holder, count in sorted(by_holder.items(), key=lambda item: -item[1]):
        link = holder_company.get((province, holder)) or {}
        cid = link.get("company_id")
        holders.append({
            "holder": holder,
            "count": count,
            "company_id": cid,
            "ticker": link.get("ticker"),
        })
        by_company[cid or "unlinked"] += count
    companies = [
        {"company_id": None if cid == "unlinked" else cid, "count": count}
        for cid, count in sorted(by_company.items(), key=lambda item: -item[1])
    ]
    return {"titles": titles, "by_holder": holders, "by_company": companies}


def export_geojsonl(con: sqlite3.Connection, holder_link: dict, province: str, dest: Path) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with dest.open("w", encoding="utf-8") as handle:
        for row in con.execute(
            "SELECT title_id, holder, geom_json FROM titles WHERE province=? AND geom_json IS NOT NULL",
            (province,),
        ):
            key = ondb.holder_key(row["holder"])
            link = holder_link.get((province, key)) or {}
            cid = link.get("company_id") or "unlinked"
            props = {
                "id": row["title_id"],
                "holder": key if key != "(blank)" else "",
                "company": cid,
                "ticker": link.get("ticker") or "",
                "province": PROVINCES[province]["code"],
                "color": ondb.company_color(None if cid == "unlinked" else cid),
            }
            handle.write(
                '{"type":"Feature","geometry":'
                + row["geom_json"]
                + ',"properties":'
                + json.dumps(props, ensure_ascii=False, separators=(",", ":"))
                + "}\n"
            )
            n += 1
    log(f"  {province} geojsonl {n} features ({dest.stat().st_size / 1e6:.1f} MB)")
    return n


def assert_under_limit(path: Path) -> int:
    size = path.stat().st_size
    if size >= GITHUB_FILE_LIMIT:
        raise SystemExit(
            f"{path} is {size} bytes, which is not under the 100 MiB GitHub file limit"
        )
    return size


TIPPECANOE_MISSING = (
    "tippecanoe was not found on PATH, and `wsl tippecanoe` is not available. "
    "Install tippecanoe and leave it on PATH, or install WSL and build tippecanoe "
    "inside the default distro (Ubuntu packages: build-essential, libsqlite3-dev, "
    "zlib1g-dev; then make install from https://github.com/felt/tippecanoe). "
    "go-pmtiles can serve an archive; it does not build one from the claims GeoJSON."
)

TIPPECANOE_ARGS = [
    "--force",
    "--read-parallel",
    "--no-feature-limit",
    "--no-tile-size-limit",
    "--drop-densest-as-needed",
    "--extend-zooms-if-still-dropping",
    "--minimum-zoom=2",
    "--maximum-zoom=12",
    "--full-detail=12",
    "--layer=claims",
]


def find_tippecanoe(which=None, run=None) -> str:
    """Return 'path' or 'wsl'. Fail when neither tippecanoe nor `wsl tippecanoe` works."""
    which = which or shutil_which
    run = run or subprocess.run
    if which("tippecanoe"):
        return "path"
    if which("wsl"):
        try:
            proc = run(
                ["wsl", "tippecanoe", "-v"],
                check=False,
                capture_output=True,
                text=True,
                timeout=20,
            )
        except (OSError, subprocess.TimeoutExpired):
            proc = None
        text = ""
        if proc is not None:
            text = ((proc.stdout or "") + (proc.stderr or "")).lower()
        if proc is not None and proc.returncode == 0 and "tippecanoe" in text:
            return "wsl"
    raise SystemExit(TIPPECANOE_MISSING)


def wsl_path(path: Path, run=None) -> str:
    run = run or subprocess.run
    proc = run(
        ["wsl", "wslpath", "-a", str(path.resolve())],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    converted = (proc.stdout or "").strip()
    if proc.returncode != 0 or not converted:
        detail = (proc.stderr or proc.stdout or "no output").strip()
        raise SystemExit(f"wslpath could not translate {path}: {detail}")
    return converted


def tippecanoe_command(geojsonl: Path, dest: Path, runner: str, translate=None) -> list[str]:
    if runner == "path":
        output = str(dest)
        source = str(geojsonl)
        return ["tippecanoe", "-o", output, *TIPPECANOE_ARGS, source]
    if runner == "wsl":
        translate = translate or wsl_path
        output = translate(dest)
        source = translate(geojsonl)
        return ["wsl", "tippecanoe", "-o", output, *TIPPECANOE_ARGS, source]
    raise SystemExit(TIPPECANOE_MISSING)


def build_one_tile(geojsonl: Path, dest: Path) -> dict:
    dest.parent.mkdir(parents=True, exist_ok=True)
    runner = find_tippecanoe()
    cmd = tippecanoe_command(geojsonl, dest, runner)
    log("  " + " ".join(cmd))
    t0 = time.time()
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        sys.stderr.write((proc.stderr or "")[-4000:])
        raise SystemExit(f"tippecanoe failed ({proc.returncode})")
    size = assert_under_limit(dest)
    elapsed = round(time.time() - t0, 1)
    log(f"  {dest.name} {size / 1e6:.2f} MB via {runner} tippecanoe in {elapsed}s")
    return {"bytes": size, "seconds": elapsed, "path": str(dest.relative_to(ROOT)), "runner": runner}


def shutil_which(name: str) -> str | None:
    from shutil import which
    return which(name)


def tile_needs_build(con: sqlite3.Connection, name: str, dest: Path) -> bool:
    """Rebuild when the archive is missing or the province was ingested after it."""
    if not dest.is_file() or dest.stat().st_size < 32:
        return True
    done = con.execute("SELECT value FROM meta WHERE key=?", (f"done:{name}",)).fetchone()
    if not done or done["value"] != "1":
        return True
    row = con.execute("SELECT value FROM meta WHERE key=?", (f"ingested_at:{name}",)).fetchone()
    if not row or not row["value"]:
        return False
    try:
        stamp = datetime.strptime(row["value"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return True
    return stamp > dest.stat().st_mtime + 1


def _part_codes(code: str, parts: int) -> list[str]:
    if parts <= 1:
        return [code]
    return [code if index == 0 else f"{code}-{index + 1}" for index in range(parts)]


def _split_lines(geojsonl: Path, parts: int) -> list[Path]:
    lines = geojsonl.read_text(encoding="utf-8").splitlines()
    if not lines:
        raise SystemExit(f"{geojsonl} has no features to split")
    chunk = (len(lines) + parts - 1) // parts
    written = []
    for index in range(parts):
        piece = lines[index * chunk:(index + 1) * chunk]
        if not piece:
            continue
        dest = geojsonl.with_name(f"{geojsonl.stem}-part{index + 1}.jsonl")
        dest.write_text("\n".join(piece) + "\n", encoding="utf-8")
        written.append(dest)
    return written


def _build_archive(geojsonl: Path, code: str, build_dir: Path) -> dict:
    scratch = build_dir / f"{code}.pmtiles"
    built = build_one_tile(geojsonl, scratch)
    dest = TILES / f"{code}{TILE_PUBLIC_SUFFIX}"
    scratch.replace(dest)
    return {
        "code": code,
        "file": f"claims/tiles/{code}{TILE_PUBLIC_SUFFIX}",
        "bytes": built["bytes"],
    }


def _build_under_limit(geojsonl: Path, code: str, build_dir: Path) -> list[dict]:
    scratch = build_dir / f"{code}.pmtiles"
    try:
        return [_build_archive(geojsonl, code, build_dir)]
    except SystemExit as exc:
        scratch.unlink(missing_ok=True)
        if "100 MiB" not in str(exc):
            raise
        log(f"  {code} is over 100 MiB; splitting the archive")
    for parts in (2, 4):
        pieces = _split_lines(geojsonl, parts)
        built = []
        try:
            for piece, part_code in zip(pieces, _part_codes(code, len(pieces))):
                built.append(_build_archive(piece, part_code, build_dir))
        except SystemExit as exc:
            for part_code in _part_codes(code, len(pieces)):
                (TILES / f"{part_code}{TILE_PUBLIC_SUFFIX}").unlink(missing_ok=True)
                (build_dir / f"{part_code}.pmtiles").unlink(missing_ok=True)
            if "100 MiB" not in str(exc) or parts == 4:
                raise
            log(f"  {code} still over 100 MiB at {parts} parts")
            continue
        else:
            return built
    raise SystemExit(f"{code} could not be split under 100 MiB")


def write_tiles(con: sqlite3.Connection, payload: dict, names: list[str]) -> dict:
    holder_link = {}
    for row in payload["rows"]:
        primary = (row.get("companies") or [{}])[0]
        holder_link[(row["province"], row["holder"])] = {
            "company_id": row.get("company_id"),
            "ticker": primary.get("ticker"),
        }
    TILES.mkdir(parents=True, exist_ok=True)
    built = {}
    listing = []
    build_dir = CLAIMS / ".build"
    selected = set(names)
    for name, spec in PROVINCES.items():
        code = spec["code"]
        dest = TILES / f"{code}{TILE_PUBLIC_SUFFIX}"
        part_paths = [dest] if dest.is_file() else []
        part_paths.extend(sorted(TILES.glob(f"{code}-[0-9]*{TILE_PUBLIC_SUFFIX}")))
        if name in selected and tile_needs_build(con, name, dest):
            for old in TILES.glob(f"{code}-[0-9]*{TILE_PUBLIC_SUFFIX}"):
                old.unlink()
            geojsonl = build_dir / f"{code}.jsonl"
            # tippecanoe picks PMTiles only when the output name ends in .pmtiles.
            # The published name ends in .png so GitHub Pages will not gzip it.
            export_geojsonl(con, holder_link, name, geojsonl)
            parts = _build_under_limit(geojsonl, code, build_dir)
            geojsonl.unlink(missing_ok=True)
            for piece in build_dir.glob(f"{code}-part*.jsonl"):
                piece.unlink()
            built[name] = {
                "bytes": sum(part["bytes"] for part in parts),
                "path": parts[0]["file"],
                "parts": len(parts),
            }
            part_rows = parts
        elif part_paths:
            part_rows = [{
                "code": path.name[: -len(TILE_PUBLIC_SUFFIX)],
                "file": str(path.relative_to(ROOT)),
                "bytes": path.stat().st_size,
            } for path in part_paths]
            log(f"  {name} tile reused ({sum(row['bytes'] for row in part_rows) / 1e6:.2f} MB)")
            built[name] = {
                "bytes": sum(row["bytes"] for row in part_rows),
                "path": part_rows[0]["file"],
                "parts": len(part_rows),
            }
        else:
            continue
        for part in part_rows:
            listing.append({
                "id": name,
                "code": part["code"],
                "name": spec["name"],
                "file": part["file"],
                "bytes": part["bytes"],
            })
    ondb.atomic_write_json(TILES / "index.json", {
        "generated_at": ondb.now_iso(),
        "source_layer": "claims",
        "max_bytes": GITHUB_FILE_LIMIT,
        "provinces": listing,
    }, indent=1)
    return built


def dir_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def probe_pages_ranges() -> dict:
    """Measure GitHub Pages range behavior. Text types get gzipped ranges; binary types do not."""
    samples = {
        "text": "https://gordojr1.github.io/quantity-capital/claims/overview.geojson",
        "binary": "https://gordojr1.github.io/quantity-capital/icons/icon-192.png",
    }
    out = {}
    for kind, url in samples.items():
        req = urllib.request.Request(url, headers={
            "User-Agent": "Quantity Capital gordojr@proton.me",
            "Range": "bytes=0-99",
            "Accept-Encoding": "gzip, deflate, br",
        })
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                body = response.read(120)
                out[kind] = {
                    "status": response.status,
                    "content_range": response.headers.get("Content-Range"),
                    "content_encoding": response.headers.get("Content-Encoding"),
                    "content_type": response.headers.get("Content-Type"),
                    "body_starts_with_pmtiles_or_png": body[:4] in (b"\x89PNG", b"PMTi"),
                    "body_looks_gzip": body[:2] == b"\x1f\x8b",
                }
        except Exception as exc:  # noqa: BLE001 — recorded, not fatal
            out[kind] = {"error": type(exc).__name__}
    out["note"] = (
        "A browser always sends Accept-Encoding: gzip. GitHub Pages returns 206, "
        "but for text types the range is taken from the gzip stream, so those bytes "
        "are not the file. image/png ranges are the raw file. .pmtiles is "
        "application/octet-stream and is gzip-sliced the same way text is, so published "
        "archives use a .pmtiles.png name. The viewer still checks the PMTiles magic and, "
        "if a range is compressed, reads the whole archive in memory."
    )
    return out


def write_report(payload: dict | None, tiles: dict | None, around: dict | None, ingests: list | None = None) -> dict:
    links = payload or (json.loads(LINKS_PATH.read_text(encoding="utf-8")) if LINKS_PATH.is_file() else {})
    tile_bytes = {}
    tile_files = {}
    for name, spec in PROVINCES.items():
        code = spec["code"]
        paths = []
        primary = TILES / f"{code}{TILE_PUBLIC_SUFFIX}"
        if primary.is_file():
            paths.append(primary)
        paths.extend(sorted(p for p in TILES.glob(f"{code}-[0-9]*{TILE_PUBLIC_SUFFIX}") if p.is_file()))
        for path in paths:
            tile_files[str(path.relative_to(ROOT))] = path.stat().st_size
            tile_bytes[name] = tile_bytes.get(name, 0) + path.stat().st_size
    if tiles:
        for name, stats in tiles.items():
            tile_bytes[name] = stats["bytes"]
    report = {
        "generated_at": ondb.now_iso(),
        "cadence": "monthly",
        "hosting": "github-pages-split-by-province",
        "provinces": links.get("provinces"),
        "titles": links.get("titles"),
        "linked_titles": links.get("linked_titles"),
        "coverage": links.get("coverage"),
        "watch": links.get("watch"),
        "jev_ambiguous_holders": links.get("jev_ambiguous_holders"),
        "sizes": {
            "sqlite_bytes": DB_PATH.stat().st_size if DB_PATH.is_file() else 0,
            "pmtiles_bytes": tile_bytes,
            "pmtiles_files": tile_files,
            "links_bytes": LINKS_PATH.stat().st_size if LINKS_PATH.is_file() else 0,
            "search_holders_bytes": SEARCH_HOLDERS.stat().st_size if SEARCH_HOLDERS.is_file() else 0,
            "search_titles_bytes": dir_bytes(SEARCH_TITLES),
            "around_bytes": dir_bytes(AROUND_DIR),
            "tiles_index_bytes": (TILES / "index.json").stat().st_size if (TILES / "index.json").is_file() else 0,
        },
        "around": around,
        "github_file_limit_bytes": GITHUB_FILE_LIMIT,
        "tiles_under_limit": all(size < GITHUB_FILE_LIMIT for size in tile_files.values()) if tile_files else False,
        "ingest": ingests or [],
        "range_probe": probe_pages_ranges(),
    }
    published = (
        report["sizes"]["links_bytes"]
        + report["sizes"]["search_holders_bytes"]
        + report["sizes"]["search_titles_bytes"]
        + report["sizes"]["around_bytes"]
        + report["sizes"]["tiles_index_bytes"]
        + sum(tile_bytes.values())
    )
    report["sizes"]["published_bytes"] = published
    ondb.atomic_write_json(REPORT_PATH, report, indent=1)
    log(f"  report {REPORT_PATH.relative_to(ROOT)}")
    return report


def stage_claims() -> None:
    import subprocess as sp
    proc = sp.run(["git", "add", "--", *STAGED], cwd=ROOT, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or "git add failed")
    status = sp.run(["git", "diff", "--cached", "--name-only"], cwd=ROOT, check=False, capture_output=True, text=True)
    names = [line for line in status.stdout.splitlines() if line.strip()]
    blocked = [
        line for line in names
        if line.startswith("claims/.db/")
        or line.startswith("claims/.build/")
        or line.startswith("claims/.cache/")
        or ".pmtiles" in line and not line.startswith("claims/tiles/")
        or line in {"backtest.json", "insider-follow.json", "insider-form4.json", "insider-repeatable.json"}
        or line.startswith("trades")
        or line.startswith("insider-trades")
    ]
    if blocked:
        raise SystemExit("refusing to stage " + ", ".join(blocked))
    log("  staged " + str(len(names)) + " claims paths")


def score_result(report: dict) -> dict:
    if not ondb.ensure_typesafe_key():
        raise SystemExit("TYPESAFE_API_KEY missing; cannot score the prototype")
    qc = SCRIPTS / "qc_sqlite"
    if str(qc) not in sys.path:
        sys.path.insert(0, str(qc))
    import jev  # noqa: WPS433
    from typesafe_sdk import Score

    provinces = report.get("provinces") or {}
    sizes = report.get("sizes") or {}
    state = {
        "cadence": "monthly",
        "hosting": "One PMTiles file per province committed under claims/tiles and served from GitHub Pages.",
        "provinces": {
            name: {
                "titles": slot.get("titles"),
                "coverage": slot.get("coverage"),
                "pmtiles_mb": round((sizes.get("pmtiles_bytes") or {}).get(name, 0) / 1e6, 2),
            }
            for name, slot in provinces.items()
        },
        "tiles_under_100mb": report.get("tiles_under_limit"),
        "tiles_tool": (
            "tippecanoe on PATH, otherwise `wsl tippecanoe` with wslpath. "
            "The build fails if neither works. go-pmtiles does not build the archive."
        ),
        "watch": report.get("watch"),
        "range_probe": report.get("range_probe"),
        "sqlite_mb": round((sizes.get("sqlite_bytes") or 0) / 1e6, 1),
    }
    questions = {
        "prototype": Score(
            instructions=(
                "Score this claims database for a static GitHub Pages site. It covers Quebec "
                "(GESTIM active titles), British Columbia (mineral claims and leases), "
                "Ontario, Yukon, Newfoundland and Labrador, and Nunavut. The SQLite "
                "database stays on a Windows desktop. The site commits one PMTiles file "
                "per province, refreshed monthly, each under 100 MB. A province may be "
                "split into numbered parts when one file would pass 100 MB."
            ),
            criteria=[
                "Do not publish claims this way.",
                "The provinces ingest and link, but the tile hosting or the coverage still blocks a monthly GitHub refresh.",
                "Sound monthly GitHub setup: local database, company links, one PMTiles file per province under 100 MB, and a viewer that can read them.",
            ],
        )
    }
    packed = jev.ask(state, questions, label="claims-db-monthly")
    jev.flush()
    answer = (packed.get("answers") or {}).get("prototype") or {}
    out = {
        "generated_at": ondb.now_iso(),
        "model": packed.get("model"),
        "score": answer.get("score"),
        "confidence": answer.get("confidence"),
        "probabilities": answer.get("probabilities"),
        "input_tokens": packed.get("input_tokens"),
        "output_tokens": packed.get("output_tokens"),
        "state": state,
    }
    ondb.atomic_write_json(SCORE_PATH, out, indent=1)
    log(f"  jev score {out['score']} confidence {out['confidence']} model {out['model']}")
    return out


def run_pipeline(names: list[str], refresh: bool, limit_pages: int | None, use_jev: bool, skip_tiles: bool, do_stage: bool) -> dict:
    con = connect()
    try:
        ingests = []
        for name in names:
            ingests.append(ingest_province(con, name, refresh, limit_pages))
        rows = holder_rows(con, names)
        if not rows:
            raise SystemExit("no titles ingested")
        payload = link_rows(rows, use_jev=use_jev, harvest=True)
        write_links(payload)
        tiles = None
        if skip_tiles:
            log("  tiles skipped")
        else:
            tiles = write_tiles(con, payload, names)
        indexes = write_search(payload)
        write_title_shards(con, indexes, names)
        around = write_around(con, payload, names)
    finally:
        con.close()
    report = write_report(payload, tiles, around, ingests)
    if do_stage:
        stage_claims()
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Monthly multi-province claims database")
    parser.add_argument("--all", action="store_true", help="Quebec, British Columbia, Ontario, Yukon, Newfoundland and Labrador, Nunavut")
    parser.add_argument("--publish", action="store_true", help="Run ingest, link, tiles, search, around, and stage claims outputs")
    parser.add_argument("--province", action="append", default=None)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--limit-pages", type=int, default=None)
    parser.add_argument("--no-jev", action="store_true")
    parser.add_argument("--skip-tiles", action="store_true")
    parser.add_argument("--score", action="store_true", help="Ask Jev to score the report, then exit")
    args = parser.parse_args(argv)
    if args.score and not args.publish and not args.all:
        score_result(json.loads(REPORT_PATH.read_text(encoding="utf-8")))
        return 0
    names = province_ids(args.province, args.all or args.publish or not args.province)
    if args.limit_pages is not None and (args.all or args.publish):
        raise SystemExit("--limit-pages is only for a single-province trial, not --all/--publish")
    report = run_pipeline(
        names,
        refresh=args.refresh,
        limit_pages=args.limit_pages,
        use_jev=not args.no_jev,
        skip_tiles=args.skip_tiles,
        do_stage=args.publish,
    )
    if args.score:
        score_result(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
