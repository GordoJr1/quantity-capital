"""Pull Ontario MLAS + BC MTA titles for every holder (attributes only).

Quebec GESTIM has no equivalent open REST in this repo — scaffolded only.
Resume with --resume. Use --limit-pages for a short run.

Pack ids: province:ontario / province:bc with company_id = _province_ontario / _province_bc.

Ontario holders often look like "(100) ACME INC" so letter prefixes on HOLDER
return empty — Ontario uses OBJECTID keyset pagination over 1=1 instead.
BC still walks OWNER_NAME letter prefixes (per-letter resume offsets).
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paths import DB_PATH, QC_ROOT

ON_URL = "https://ws.lioservices.lrc.gov.on.ca/arcgis1071a/rest/services/MLAS/mlas_op/MapServer/1/query"
BC_URL = "https://delivery.maps.gov.bc.ca/arcgis/rest/services/whse/bcgw_pub_whse_mineral_tenure/MapServer/36/query"
ON_FIELDS = "OBJECTID,TENURE_NUMBER_ID,HOLDER,TENURE_STATUS_DESC,ISSUE_DATE,ANNIVERSARY_DATE,CLAIM_DUE_DATE,TITLE_TYPE_DESC"
BC_FIELDS = "TENURE_NUMBER_ID,CLAIM_NAME,OWNER_NAME,TENURE_TYPE_DESCRIPTION,TITLE_TYPE_DESCRIPTION,ISSUE_DATE,GOOD_TO_DATE,TERMINATION_DATE,AREA_IN_HECTARES,TENURE_SUB_TYPE_DESCRIPTION"
ON_SOURCE = "Ontario MLAS operational claims (OGSEarth / LIO MapServer). Unofficial viewing data, not legal title."
BC_SOURCE = "BC MTA Mineral, Placer and Coal Tenure Spatial View (BCGW). Open Government Licence – British Columbia. Not legal title."
PAGE = 1000


def log(msg: str) -> None:
    print(msg, flush=True)


def get_json(url: str, params: dict) -> dict:
    q = urllib.parse.urlencode(params)
    req = urllib.request.Request(url + "?" + q, headers={"User-Agent": "quantity-capital-claims/1.0"})
    with urllib.request.urlopen(req, timeout=180) as r:
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


def ensure_province_companies(con: sqlite3.Connection) -> None:
    for cid, name, uni in (
        ("_province_ontario", "Ontario MLAS (all holders)", "province"),
        ("_province_bc", "British Columbia MTA (all holders)", "province"),
    ):
        con.execute(
            """
            INSERT OR IGNORE INTO companies(company_id, name, universe, sources_json, names_json)
            VALUES (?, ?, ?, '["province_rest"]', ?)
            """,
            (cid, name, uni, json.dumps([name])),
        )
        con.execute(
            """
            INSERT OR IGNORE INTO claim_packs(
              pack_id, company_id, role, holder, holder_company_id, claim_count,
              jurisdiction, source, as_of, extract_path, bbox_json, color
            ) VALUES (?, ?, 'focus', ?, ?, 0, ?, ?, ?, NULL, NULL, NULL)
            """,
            (
                "province:ontario" if cid.endswith("ontario") else "province:bc",
                cid,
                name,
                cid,
                "Ontario" if cid.endswith("ontario") else "British Columbia",
                ON_SOURCE if cid.endswith("ontario") else BC_SOURCE,
                datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            ),
        )


def props_on(attrs: dict) -> dict:
    return {
        "claim_id": attrs.get("TENURE_NUMBER_ID"),
        "claim_name": None,
        "holder": attrs.get("HOLDER"),
        "status": attrs.get("TENURE_STATUS_DESC"),
        "recorded_date": epoch_to_date(attrs.get("ISSUE_DATE")),
        "anniversary_or_expiry": epoch_to_date(attrs.get("ANNIVERSARY_DATE") or attrs.get("CLAIM_DUE_DATE")),
        "area_ha": None,
        "tenure_type": attrs.get("TITLE_TYPE_DESC"),
        "objectid": attrs.get("OBJECTID"),
    }


def props_bc(attrs: dict) -> dict:
    return {
        "claim_id": attrs.get("TENURE_NUMBER_ID") or attrs.get("TENURE_NUMBER"),
        "claim_name": attrs.get("CLAIM_NAME") or attrs.get("TENURE_NUMBER"),
        "holder": attrs.get("OWNER_NAME"),
        "status": attrs.get("TENURE_TYPE_DESCRIPTION"),
        "recorded_date": epoch_to_date(attrs.get("ISSUE_DATE")),
        "anniversary_or_expiry": epoch_to_date(attrs.get("GOOD_TO_DATE")),
        "area_ha": attrs.get("AREA_IN_HECTARES"),
        "tenure_type": attrs.get("TITLE_TYPE") or attrs.get("TENURE_TYPE_DESCRIPTION"),
    }


def ingest_features(con: sqlite3.Connection, pack_id: str, company_id: str, jurisdiction: str, source: str, feats: list[dict], mapper) -> int:
    as_of = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    rows = []
    for f in feats:
        attrs = f.get("attributes") or f.get("properties") or {}
        p = mapper(attrs)
        cid = p.get("claim_id")
        if cid is None or cid == "":
            continue
        title_pk = f"{pack_id}:focus:{cid}"
        rows.append(
            (
                title_pk,
                pack_id,
                company_id,
                None,
                jurisdiction,
                str(cid),
                p.get("claim_name"),
                p.get("holder"),
                p.get("status"),
                p.get("recorded_date"),
                p.get("anniversary_or_expiry"),
                p.get("area_ha"),
                p.get("tenure_type"),
                source,
                as_of,
                "focus",
                None,
            )
        )
    if rows:
        con.executemany(
            """
            INSERT OR REPLACE INTO claim_titles(
              title_pk, pack_id, company_id, holder_company_id, jurisdiction, claim_id,
              claim_name, holder_raw, status, recorded_date, anniversary_or_expiry,
              area_ha, tenure_type, source, as_of, role, extract_path
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)


def fetch_bc_page(where: str, offset: int) -> list[dict]:
    params = {
        "where": where,
        "outFields": BC_FIELDS,
        "outSR": 4326,
        "f": "json",
        "returnGeometry": "false",
        "resultRecordCount": PAGE,
        "resultOffset": offset,
    }
    data = get_json(BC_URL, params)
    if data.get("error"):
        log(f"  api error {data['error']}")
        return []
    return data.get("features") or []


def fetch_on_page(after_objectid: int) -> list[dict]:
    where = "1=1" if after_objectid <= 0 else f"OBJECTID>{after_objectid}"
    params = {
        "where": where,
        "outFields": ON_FIELDS,
        "f": "json",
        "returnGeometry": "false",
        "resultRecordCount": PAGE,
        "orderByFields": "OBJECTID ASC",
    }
    data = get_json(ON_URL, params)
    if data.get("error"):
        log(f"  api error {data['error']}")
        return []
    return data.get("features") or []


def pull_bc(con: sqlite3.Connection, limit_pages: int, resume: bool, letters: str) -> int:
    prefixes = list(letters) if letters else list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
    pack, company, juris, source = "province:bc", "_province_bc", "British Columbia", BC_SOURCE
    total = 0
    for ch in prefixes:
        where = f"UPPER(OWNER_NAME) LIKE '{ch}%'"
        log(f"bc prefix {ch}")
        meta_key = f"province_bc_offset_{ch}"
        offset = 0
        if resume:
            row = con.execute("SELECT value FROM meta WHERE key=?", (meta_key,)).fetchone()
            if row:
                try:
                    offset = int(row[0])
                except Exception:
                    offset = 0
        pages = 0
        letter_n = 0
        while True:
            if limit_pages and pages >= limit_pages:
                break
            log(f"bc offset={offset}")
            try:
                feats = fetch_bc_page(where, offset)
            except Exception as exc:
                log(f"bc fetch failed: {type(exc).__name__}: {exc}")
                break
            if not feats:
                log(f"bc done at offset {offset}")
                break
            n = ingest_features(con, pack, company, juris, source, feats, props_bc)
            letter_n += n
            total += n
            pages += 1
            offset += len(feats)
            con.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (meta_key, str(offset)),
            )
            con.execute(
                "UPDATE claim_packs SET claim_count = (SELECT COUNT(*) FROM claim_titles WHERE pack_id=?) WHERE pack_id=?",
                (pack, pack),
            )
            con.commit()
            if len(feats) < PAGE:
                log(f"bc last page at offset {offset}")
                break
            time.sleep(0.15)
        log(f"bc ingested {letter_n} this run for {ch}, next offset {offset}")
    return total


def pull_ontario(con: sqlite3.Connection, limit_pages: int, resume: bool) -> int:
    pack, company, juris, source = "province:ontario", "_province_ontario", "Ontario", ON_SOURCE
    meta_key = "province_ontario_objectid"
    after = 0
    if resume:
        row = con.execute("SELECT value FROM meta WHERE key=?", (meta_key,)).fetchone()
        if row:
            try:
                after = int(row[0])
            except Exception:
                after = 0
    pages = 0
    total = 0
    log(f"ontario keyset after OBJECTID={after}")
    while True:
        if limit_pages and pages >= limit_pages:
            break
        try:
            feats = fetch_on_page(after)
        except Exception as exc:
            log(f"ontario fetch failed: {type(exc).__name__}: {exc}")
            break
        if not feats:
            log(f"ontario done after OBJECTID={after}")
            break
        n = ingest_features(con, pack, company, juris, source, feats, props_on)
        total += n
        pages += 1
        # advance keyset
        oids = []
        for f in feats:
            attrs = f.get("attributes") or f.get("properties") or {}
            if attrs.get("OBJECTID") is not None:
                oids.append(int(attrs["OBJECTID"]))
        if not oids:
            log("ontario page missing OBJECTID — stop")
            break
        after = max(oids)
        con.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (meta_key, str(after)),
        )
        if pages % 10 == 0 or len(feats) < PAGE:
            con.execute(
                "UPDATE claim_packs SET claim_count = (SELECT COUNT(*) FROM claim_titles WHERE pack_id=?) WHERE pack_id=?",
                (pack, pack),
            )
            con.commit()
            log(f"ontario pages={pages} ingested={total} after_oid={after} pack_rows={con.execute('SELECT COUNT(*) FROM claim_titles WHERE pack_id=?', (pack,)).fetchone()[0]}")
        else:
            con.commit()
        if len(feats) < PAGE:
            log(f"ontario last page after OBJECTID={after}")
            break
        time.sleep(0.12)
    con.execute(
        "UPDATE claim_packs SET claim_count = (SELECT COUNT(*) FROM claim_titles WHERE pack_id=?) WHERE pack_id=?",
        (pack, pack),
    )
    con.commit()
    log(f"ontario ingested {total} this run, resume OBJECTID>{after}")
    return total


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--ontario", action="store_true")
    p.add_argument("--bc", action="store_true")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--limit-pages", type=int, default=0)
    p.add_argument("--letters", default="ABCDEFGHIJKLMNOPQRSTUVWXYZ", help="BC owner prefixes only")
    args = p.parse_args()
    if not args.ontario and not args.bc:
        args.ontario = args.bc = True
    if not DB_PATH.exists():
        log(f"missing {DB_PATH}")
        return 1
    con = sqlite3.connect(str(DB_PATH))
    try:
        ensure_province_companies(con)
        con.commit()
        if args.ontario:
            pull_ontario(con, args.limit_pages, args.resume)
        if args.bc:
            pull_bc(con, args.limit_pages, args.resume, args.letters)
        log("Quebec GESTIM full-province: no open REST in-repo. Keep producer extracts.")
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
