#!/usr/bin/env python3
"""Pull recent War.gov contract announcements into contracts.sqlite and contracts.json.

Desktop collector only. Stdlib. No git. Fail-soft: a block, timeout, or parse
error keeps the previous rows and exits 0.

    python scripts/ingest_dod_announcements.py --self-check
    python scripts/ingest_dod_announcements.py
    python scripts/ingest_dod_announcements.py --simulate-status 403
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import ssl
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import dod_parse
from ingest_federal_contracts import (
    DEFAULT_SQLITE,
    GROKS_JSON,
    ROOT,
    cache_key_for,
    classify,
    clip_desc,
    connect,
    export_json,
    load_catalog,
    lookup_jev,
    utc_now,
)

UA_PAGE = "https://www.war.gov/News/Contracts/"
DAILY_BUDGET_S = 60
BACKFILL_BUDGET_S = 180
DOD_WINDOW_DAYS = 30
CTX = ssl.create_default_context()

# Research parent patterns. Match with re.search against the upper-cased name.
DOD_PATTERNS = [(r'LOCKHEED|SIKORSKY', 'LMT'), (r'RAYTHEON|PRATT (&|AND) WHITNEY|COLLINS AEROSPACE|ROCKWELL COLLINS', 'RTX'), (r'GENERAL DYNAMICS|ELECTRIC BOAT|BATH IRON|NATIONAL STEEL AND SHIP|GULFSTREAM', 'GD'),
       (r'NORTHROP', 'NOC'), (r'BOEING(?! *-? *BELL)(?!.*BELL BOEING)', 'BA'), (r'L3HARRIS|L3 TECHNOLOGIES|HARRIS CORP', 'LHX'), (r'HUNTINGTON INGALLS', 'HII'), (r'LEIDOS', 'LDOS'),
       (r'SCIENCE APPLICATIONS INTERNATIONAL', 'SAIC'), (r'\bCACI\b', 'CACI'), (r'BOOZ ALLEN', 'BAH'), (r'\bKBR\b|KELLOGG BROWN', 'KBR'), (r'PARSONS GOVERNMENT|PARSONS CORP', 'PSN'),
       (r'VECTRUS|\bV2X\b', 'VVX'), (r'\bBWX', 'BWXT'), (r'TEXTRON|BELL HELICOPTER|BELL TEXTRON', 'TXT'), (r'HONEYWELL', 'HON'), (r'GENERAL ELECTRIC|GE AVIATION|GE AEROSPACE', 'GE'),
       (r'OSHKOSH', 'OSK'), (r'AEROVIRONMENT', 'AVAV'), (r'KRATOS', 'KTOS'), (r'MERCURY SYSTEMS', 'MRCY'), (r'PALANTIR', 'PLTR'), (r'ACCENTURE FEDERAL', 'ACN'), (r'MCKESSON', 'MCK'),
       (r'HUMANA', 'HUM'), (r'HEALTH NET FEDERAL', 'CNC'), (r'FLUOR', 'FLR'), (r'JACOBS', 'J'), (r'\bAECOM', 'ACM'), (r'TETRA TECH', 'TTEK'), (r'AMENTUM', 'AMTM'), (r'CURTISS-WRIGHT', 'CW'),
       (r'LEONARDO DRS|\bDRS ', 'DRS'), (r'\bMOOG\b', 'MOG-A'), (r'\bAAR ', 'AIR'), (r'VIASAT', 'VSAT'), (r'IRIDIUM', 'IRDM'), (r'ROCKET LAB', 'RKLB'), (r'TELEDYNE', 'TDY'),
       (r'INTERNATIONAL BUSINESS MACHINES', 'IBM'), (r'MICROSOFT', 'MSFT'), (r'AMAZON WEB', 'AMZN'), (r'ORACLE', 'ORCL'), (r'DELL ', 'DELL'), (r'PFIZER', 'PFE'), (r'MODERNA', 'MRNA'),
       (r'EMERGENT BIO', 'EBS'), (r'SIGA TECH', 'SIGA'), (r'GM DEFENSE', 'GM'), (r'HEICO', 'HEI'), (r'CATERPILLAR', 'CAT'), (r'MOTOROLA SOLUTIONS', 'MSI'), (r'OLIN ', 'OLN'),
       (r'AXON ENTERPRISE', 'AXON'), (r'WOODWARD', 'WWD'), (r'DUCOMMUN', 'DCO'), (r'NATIONAL PRESTO|AMTEC', 'NPK'), (r'ASTRONICS', 'ATRO'), (r'\bVSE ', 'VSEC'), (r'EMCOR', 'EME'),
       (r'GRANITE CONSTRUCTION', 'GVA'), (r'TUTOR PERINI|PERINI MANAGEMENT', 'TPC'), (r'MASTEC', 'MTZ'), (r'ICF ', 'ICFI'), (r'MAXIMUS', 'MMS')]

PIT_WINDOWS = [
    ("RAYTHEON CO", "RTX", "2020-04-03"),
    ("L3 TECHNOLOGIES", "LHX", "2019-06-29"),
    ("AEROJET", "LHX", "2023-07-28"),
    ("JACOBS TECHNOLOGY", "AMTM", "2024-09-27"),
    ("ORBITAL ATK", "NOC", "2018-06-06"),
    ("ROCKWELL COLLINS", "RTX", "2018-11-26"),
    ("CSRA", "GD", "2018-04-03"),
    ("VERTEX AEROSPACE", "VVX", "2022-07-05"),
    ("KEYW", "J", "2019-06-12"),
]

REJECTED_NAMES = (
    "ACACIA CENTER",
    "CAE USA",
    "V2X AEROSPACE",
    "BWXT ORDNANCE",
)


class SoftFail(Exception):
    def __init__(self, code: Any) -> None:
        super().__init__(str(code))
        self.code = code


def norm_piid(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def phrase(value: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", (value or "").upper()).strip()


def no_safe_match(company: str) -> bool:
    name = (company or "").upper()
    if "JOINT VENTURE" in name or re.search(r"\bJV\b", name):
        return True
    if re.search(r"BELL[-\s]?BOEING", name):
        return True
    return any(banned in name for banned in REJECTED_NAMES)


def pattern_ticker(company: str) -> str | None:
    name = (company or "").upper()
    hits: list[str] = []
    for pat, ticker in DOD_PATTERNS:
        if re.search(pat, name) and ticker not in hits:
            hits.append(ticker)
    if len(hits) == 1:
        return hits[0]
    return None


def pit_ticker(company: str, publish_date: str) -> str | None:
    """Return '' to force a blank ticker, a ticker, or None when no window applies."""
    name = phrase(company)
    best: tuple[str, str, str] | None = None
    for raw, ticker, start in PIT_WINDOWS:
        key = phrase(raw)
        if key and re.search(rf"(^| ){re.escape(key)}( |$)", name):
            if best is None or len(key) > len(best[0]):
                best = (key, ticker, start)
    if best is None:
        return None
    _key, ticker, start = best
    if publish_date and publish_date < start:
        return ""
    return ticker


def map_company(
    company: str,
    publish_date: str,
    cat: Any,
    conn: sqlite3.Connection,
) -> tuple[str | None, str]:
    if no_safe_match(company):
        return None, "none"
    got = classify(company, cat)
    ticker: str | None = None
    how = "none"
    if got.get("ticker"):
        ticker = str(got["ticker"])
        how = "exact" if got.get("how") == "exact" else "parent"
    elif got.get("how") == "ambiguous":
        candidates = got.get("candidates") or []
        if candidates:
            chosen = lookup_jev(conn, cache_key_for(company, candidates), candidates)
            if chosen:
                ticker = chosen
                how = "jev_cache"
    else:
        patterned = pattern_ticker(company)
        if patterned:
            ticker = patterned
            how = "dod_pattern"
    pit = pit_ticker(company, publish_date)
    if pit is not None:
        if pit == "":
            return None, "none"
        if ticker != pit:
            ticker = pit
            how = "dod_pattern"
    if ticker == "DXC" and "PERSPECTA" in (company or "").upper():
        return None, "none"
    if not ticker or ticker not in cat.tape:
        return None, "none"
    return ticker, how


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def ensure_dod(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS dod_articles (
          article_id TEXT PRIMARY KEY,
          url TEXT,
          publish_date TEXT,
          fetched_at TEXT,
          n_rows INTEGER,
          status TEXT,
          attempts INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS dod_awards (
          article_id TEXT,
          para_idx INTEGER,
          awardee_idx INTEGER,
          publish_date TEXT,
          branch TEXT,
          company TEXT,
          amount REAL,
          contract TEXT,
          contract_norm TEXT,
          is_mod INTEGER,
          is_multi INTEGER,
          is_idiq INTEGER,
          is_fms INTEGER,
          obligated REAL,
          ticker TEXT,
          match_how TEXT,
          url TEXT,
          text_clip TEXT,
          n_awardees INTEGER,
          PRIMARY KEY (article_id, para_idx, awardee_idx)
        );
        """
    )
    cols = {row[1] for row in conn.execute("PRAGMA table_info(dod_articles)")}
    if "attempts" not in cols:
        conn.execute("ALTER TABLE dod_articles ADD COLUMN attempts INTEGER DEFAULT 0")
    award_cols = {row[1] for row in conn.execute("PRAGMA table_info(dod_awards)")}
    if "n_awardees" not in award_cols:
        conn.execute("ALTER TABLE dod_awards ADD COLUMN n_awardees INTEGER")
    conn.commit()


def award_count(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "dod_awards"):
        return 0
    return int(conn.execute("SELECT COUNT(*) FROM dod_awards").fetchone()[0])


def linked_count(conn: sqlite3.Connection) -> int:
    if not table_exists(conn, "dod_awards"):
        return 0
    return int(conn.execute(
        "SELECT COUNT(*) FROM dod_awards WHERE ticker IS NOT NULL AND ticker != ''"
    ).fetchone()[0])


def read_window() -> tuple[str, str]:
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=13)).strftime("%Y-%m-%d")
    try:
        payload = json.loads(GROKS_JSON.read_text(encoding="utf-8"))
        window = payload.get("window") or {}
        if window.get("start") and window.get("end"):
            return str(window["start"]), str(window["end"])
    except (OSError, json.JSONDecodeError):
        pass
    return start, today


def file_fingerprint(path: Path) -> tuple[str, str, int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    awards = json.dumps(payload.get("awards"), sort_keys=True, separators=(",", ":"))
    dod = dict(payload.get("dod") or {})
    dod.pop("fetched", None)
    dod_text = json.dumps(dod, sort_keys=True, separators=(",", ":"))
    return (
        hashlib.sha256(awards.encode("utf-8")).hexdigest(),
        hashlib.sha256(dod_text.encode("utf-8")).hexdigest(),
        path.stat().st_size,
    )


def absolute_url(url: str) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        return "https://www.war.gov" + url
    return "https://www.war.gov/" + url.lstrip("/")


def looks_like(text: str, kind: str) -> bool:
    if kind == "listing":
        return len(text) >= 3000 and ("article-id" in text or "Contracts" in text[:8000])
    return len(text) >= 8000 and ("article-view" in text or 'class="maintitle"' in text)


def retry_wait(exc: urllib.error.HTTPError) -> float:
    header = ""
    if exc.headers:
        header = str(exc.headers.get("Retry-After") or "")
    if not header:
        return 2.0
    try:
        return min(max(float(header), 0.0), 30.0)
    except ValueError:
        return 2.0


def polite_get(
    url: str,
    referer: str,
    *,
    kind: str,
    simulate: str,
    sleeper,
    timeout: int = 25,
) -> str:
    if simulate == "parse":
        raise SoftFail("parse")
    headers = dict(dod_parse.FETCH_HEADERS)
    headers["Referer"] = referer
    last: Any = "fetch"
    for attempt in (1, 2):
        if simulate:
            last = simulate
            if attempt == 1:
                print(f"dod {simulate}; retry once", flush=True)
                sleeper(0)
                continue
            raise SoftFail(simulate)
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, context=CTX, timeout=timeout) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                raw = resp.read()
                hdrs = resp.headers
            if status in (403, 429, 500, 502, 503, 504):
                raise urllib.error.HTTPError(url, status, "status", hdrs=hdrs, fp=None)
            text = raw.decode("utf-8", "replace")
            if not looks_like(text, kind):
                raise urllib.error.HTTPError(url, 403, "blocked", hdrs=hdrs, fp=None)
            return text
        except urllib.error.HTTPError as exc:
            code = int(getattr(exc, "code", 0) or 0) or "fetch"
            last = code
            if attempt == 1:
                print(f"dod {code}; retry once", flush=True)
                sleeper(retry_wait(exc) if code in (403, 429) else 2.0)
                continue
            raise SoftFail(code)
        except (urllib.error.URLError, TimeoutError, ConnectionError, ssl.SSLError, TimeoutError):
            last = "timeout"
            if attempt == 1:
                print("dod timeout; retry once", flush=True)
                sleeper(2.0)
                continue
            raise SoftFail("timeout")
    raise SoftFail(last)


def articles_known(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    if not table_exists(conn, "dod_articles"):
        return {}
    rows = conn.execute(
        "SELECT article_id, url, publish_date, status, attempts FROM dod_articles"
    ).fetchall()
    return {str(row["article_id"]): row for row in rows}


def store_article(
    conn: sqlite3.Connection,
    article: dict[str, Any],
    html: str,
    page_url: str,
    cat: Any,
) -> int:
    _title, pub, paragraphs = dod_parse.parse_article_html(html)
    publish = pub or article.get("publish_date") or ""
    url = absolute_url(page_url)
    conn.execute("DELETE FROM dod_awards WHERE article_id = ?", (article["article_id"],))
    kept = 0
    for para_idx, paragraph in enumerate(paragraphs):
        text = paragraph.get("text") or ""
        awardees = dod_parse.split_awardees(text, paragraph.get("branch") or "")
        multi = any(row.get("is_multiple_award") for row in awardees)
        for awardee_idx, row in enumerate(awardees):
            company = row.get("company") or ""
            contract = row.get("contract_number") or ""
            ticker, how = map_company(company, publish, cat, conn)
            is_multi = 1 if multi else 0
            conn.execute(
                """
                INSERT OR REPLACE INTO dod_awards (
                  article_id, para_idx, awardee_idx, publish_date, branch, company,
                  amount, contract, contract_norm, is_mod, is_multi, is_idiq, is_fms,
                  obligated, ticker, match_how, url, text_clip, n_awardees
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    article["article_id"],
                    para_idx,
                    awardee_idx,
                    publish,
                    row.get("branch") or "",
                    company,
                    row.get("amount_usd"),
                    contract,
                    norm_piid(contract),
                    1 if row.get("is_modification") else 0,
                    is_multi,
                    1 if dod_parse.is_idiq_text(text, row.get("contract_type") or "") else 0,
                    1 if row.get("is_foreign_military_sales") else 0,
                    dod_parse.obligated_amount(text, bool(is_multi)),
                    ticker,
                    how,
                    url,
                    clip_desc(dod_parse.award_blurb(text)),
                    int(row.get("n_awardees") or (len(awardees) if multi else 1)),
                ),
            )
            kept += 1
    conn.execute(
        """
        INSERT OR REPLACE INTO dod_articles (
          article_id, url, publish_date, fetched_at, n_rows, status, attempts
        ) VALUES (?, ?, ?, ?, ?, 'ok', 0)
        """,
        (article["article_id"], url, publish, utc_now(), kept),
    )
    conn.commit()
    return kept


def mark_failed(conn: sqlite3.Connection, article: dict[str, Any], prev: sqlite3.Row | None) -> None:
    attempts = int(prev["attempts"] or 0) + 1 if prev is not None else 1
    conn.execute(
        """
        INSERT OR REPLACE INTO dod_articles (
          article_id, url, publish_date, fetched_at, n_rows, status, attempts
        ) VALUES (?, ?, ?, ?, ?, 'failed', ?)
        """,
        (
            article["article_id"],
            absolute_url(article.get("url") or ""),
            article.get("publish_date") or "",
            utc_now(),
            0,
            attempts,
        ),
    )
    conn.commit()


def select_articles(
    listed: list[dict[str, Any]],
    known: dict[str, sqlite3.Row],
    *,
    backfill: bool,
    cutoff: str,
) -> list[dict[str, Any]]:
    chosen = []
    seen = set()
    for row in listed:
        aid = str(row.get("article_id") or "")
        if not aid or aid in seen:
            continue
        seen.add(aid)
        published = row.get("publish_date") or ""
        if backfill and published and published < cutoff:
            continue
        prev = known.get(aid)
        if prev is not None and prev["status"] == "ok":
            continue
        if prev is not None and prev["status"] == "failed" and int(prev["attempts"] or 0) >= 3:
            continue
        chosen.append(row)
    return chosen


def run(args: argparse.Namespace, sleeper=time.sleep) -> int:
    simulate = (args.simulate_status or "").strip()
    before_rows = 0
    conn = connect(args.sqlite)
    try:
        before_rows = award_count(conn)
        before_linked = linked_count(conn)
        json_before = None
        primary = args.json or (ROOT / "contracts.json")
        if primary.exists() and simulate:
            json_before = primary.read_bytes()
        if simulate == "parse":
            print("dod blocked parse; kept previous rows", flush=True)
            print(f"dod_awards_before {before_rows}", flush=True)
            print(f"dod_awards_after {award_count(conn)}", flush=True)
            return 0
        if not table_exists(conn, "dod_articles"):
            empty = True
        else:
            empty = int(conn.execute("SELECT COUNT(*) FROM dod_articles").fetchone()[0]) == 0
        backfill = empty or args.backfill_days is not None
        days = 45 if args.backfill_days is None else int(args.backfill_days)
        days = min(max(days, 1), 60)
        max_pages = 6 if backfill else 1
        today = datetime.now().date()
        cutoff = (today - timedelta(days=days)).isoformat()
        budget = args.budget if args.budget and args.budget > 0 else (BACKFILL_BUDGET_S if backfill else DAILY_BUDGET_S)
        deadline = time.monotonic() + budget
        referer = UA_PAGE
        listed: list[dict[str, Any]] = []
        pages = 0
        try:
            for page in range(1, max_pages + 1):
                if time.monotonic() >= deadline:
                    print("dod budget reached", flush=True)
                    break
                if pages:
                    sleeper(args.sleep)
                url = dod_parse.LIST_URL.format(page=page)
                html = polite_get(
                    url,
                    referer,
                    kind="listing",
                    simulate=simulate if page == 1 else "",
                    sleeper=sleeper,
                )
                pages += 1
                batch = dod_parse.parse_listing(html)
                if not batch:
                    break
                listed.extend(batch)
                dates = [row.get("publish_date") or "" for row in batch if row.get("publish_date")]
                if backfill and dates and min(dates) < cutoff:
                    break
        except SoftFail as exc:
            print(f"dod blocked {exc.code}; kept previous rows", flush=True)
            print(f"listing_pages {pages}", flush=True)
            print(f"dod_awards_before {before_rows}", flush=True)
            print(f"dod_awards_after {award_count(conn)}", flush=True)
            print(f"linked_rows {linked_count(conn)}", flush=True)
            if json_before is not None and primary.exists() and primary.read_bytes() != json_before:
                print("contracts.json changed during a blocked run", flush=True)
                return 0
            return 0

        ensure_dod(conn)
        known = articles_known(conn)
        todo = select_articles(listed, known, backfill=backfill, cutoff=cutoff if backfill else "")
        fetched = 0
        for article in todo:
            if time.monotonic() >= deadline:
                print("dod budget reached", flush=True)
                break
            sleeper(args.sleep)
            url = absolute_url(article.get("url") or "")
            try:
                html = polite_get(url, referer, kind="article", simulate="", sleeper=sleeper)
                store_article(conn, article, html, url, args.catalog)
                fetched += 1
            except SoftFail as exc:
                print(f"dod article {article.get('article_id')} failed {exc.code}", flush=True)
                mark_failed(conn, article, known.get(str(article.get("article_id"))))
                if str(exc.code) in {"403", "429"}:
                    break
            except Exception as exc:
                print(f"dod article {article.get('article_id')} failed parse", flush=True)
                mark_failed(conn, article, known.get(str(article.get("article_id"))))
        start, end = read_window()
        dests = [primary]
        if not args.no_groks and GROKS_JSON.resolve() != primary.resolve():
            dests.append(GROKS_JSON)
        payload = export_json(
            conn,
            dests,
            start,
            end,
            dod_run={"status": "ok", "fetched": utc_now()},
        )
        after_rows = award_count(conn)
        exported = sum(1 for row in payload.get("awards") or [] if row.get("source") == "dod_announcement")
        print(f"listing_pages {pages}", flush=True)
        print(f"articles_listed {len(listed)}", flush=True)
        print(f"articles_fetched {fetched}", flush=True)
        print(f"dod_awards_before {before_rows}", flush=True)
        print(f"dod_awards_after {after_rows}", flush=True)
        print(f"dod_awards_added {after_rows - before_rows}", flush=True)
        print(f"linked_rows_before {before_linked}", flush=True)
        print(f"linked_rows {linked_count(conn)}", flush=True)
        print(f"exported_dod {exported}", flush=True)
        for dest in dests:
            awards_sha, dod_sha, size = file_fingerprint(dest)
            print(f"wrote_bytes {size} {dest}", flush=True)
            print(f"awards_sha {awards_sha}", flush=True)
            print(f"dod_sha {dod_sha}", flush=True)
        dod = payload.get("dod") or {}
        print(
            f"dod_block latest={dod.get('latest')} status={dod.get('status')} rows={dod.get('rows')}",
            flush=True,
        )
        return 0
    finally:
        conn.close()


def self_check() -> None:
    passed, failed, lines = dod_parse.run_self_test()
    for line in lines:
        print(line, flush=True)
    if failed:
        raise SystemExit(f"self-check parser {passed} pass {failed} fail")
    print(f"parser {passed}/{passed + failed}", flush=True)

    multi = (
        "LATA-CTI JV LLC,* Albuquerque, New Mexico (FA8903-26-D-0062); "
        "Los Alamos Technical Associates Inc.,* Albuquerque, New Mexico "
        "(FA8903-26-D-0063) were awarded a maximum $3,500,000,000 firm-fixed-price, "
        "indefinite-delivery/indefinite-quantity, multiple award contract."
    )
    parts = dod_parse.split_awardees(multi, "AIR FORCE")
    if len(parts) != 2 or parts[0]["contract_number"] != "FA8903-26-D-0062":
        raise SystemExit(f"self-check multi split {parts}")
    if parts[1]["contract_number"] != "FA8903-26-D-0063" or parts[0]["n_awardees"] != 2:
        raise SystemExit("self-check multi contract")
    if not dod_parse.is_idiq_text(multi, parts[0].get("contract_type") or ""):
        raise SystemExit("self-check ceiling")
    if dod_parse.obligated_amount(multi, True) is not None:
        raise SystemExit("self-check multi obligated")

    lmt = (
        "Lockheed Martin Corp., Grand Prairie, Texas, was awarded a $16,572,366 modification "
        "(P00060) to contract (W31P4Q-24-C-0007). Fiscal 2025 funds in the amount of "
        "$9,911,910 were obligated at the time of the award."
    )
    if dod_parse.obligated_amount(lmt, False) != 9911910:
        raise SystemExit("self-check obligated")
    boat = (
        "Electric Boat Corp., Groton, Connecticut, is awarded $40,000,000 for delivery order "
        "(N00104-26-F-0024). Fiscal 2026 funds in the amount of $19,600,000 (49%) will be "
        "obligated at time of award."
    )
    if dod_parse.obligated_amount(boat, False) != 19600000:
        raise SystemExit("self-check obligated paren")
    if dod_parse.obligated_amount("No funds will be obligated at the time of award. $1.", False) != 0:
        raise SystemExit("self-check no funds")

    import tempfile
    from ingest_federal_contracts import Catalog

    cat = Catalog()
    cat.tape = {"RTX", "LMT", "GD", "AIR", "LHX", "AMTM", "J"}
    cat.add("Raytheon Co", "RTX", "RTX")
    cat.add("Lockheed Martin Corp", "LMT", "Lockheed Martin")
    with tempfile.TemporaryDirectory(prefix="qc-dod-") as folder:
        folder_path = Path(folder)
        conn = connect(folder_path / "t.sqlite")
        try:
            ticker, how = map_company("LATA-CTI JV LLC", "2026-09-22", cat, conn)
            if ticker or how != "none":
                raise SystemExit(f"self-check jv {ticker} {how}")
            ticker, how = map_company("Raytheon Co.", "2019-12-01", cat, conn)
            if ticker:
                raise SystemExit(f"self-check raytheon pit {ticker} {how}")
            ticker, how = map_company("Raytheon Co.", "2026-09-24", cat, conn)
            if ticker != "RTX":
                raise SystemExit(f"self-check raytheon now {ticker}")
            ticker, how = map_company("Sikorsky Aircraft Corp.", "2026-09-25", cat, conn)
            if ticker != "LMT" or how != "dod_pattern":
                raise SystemExit(f"self-check sikorsky {ticker} {how}")
            ticker, how = map_company("Bell-Boeing Joint Project Office", "2026-09-25", cat, conn)
            if ticker:
                raise SystemExit("self-check bell boeing")
            ensure_dod(conn)
            conn.execute(
                """
                INSERT INTO dod_awards (
                  article_id, para_idx, awardee_idx, publish_date, branch, company,
                  amount, contract, contract_norm, is_mod, is_multi, is_idiq, is_fms,
                  obligated, ticker, match_how, url, text_clip, n_awardees
                ) VALUES ('a', 0, 0, '2026-09-20', 'NAVY', 'Example', 10,
                  'N00019-24-C-0061', 'N0001924C0061', 0, 0, 0, 0, NULL, 'LMT',
                  'exact', 'https://www.war.gov/News/Contracts/example', 'is awarded', 1)
                """
            )
            conn.commit()
            awards = [{
                "date": "2026-09-18",
                "agency": "Defense",
                "recipient": "Example",
                "ticker": "LMT",
                "amount": 10,
                "description": "base",
                "award_id": "N0001924C0061",
                "source": "usaspending",
            }]
            merged, block = __import__("ingest_federal_contracts", fromlist=["attach_dod"]).attach_dod(
                conn, awards, "2026-09-26", {"status": "ok", "fetched": "2026-09-26T00:00:00Z"}
            )
            dod_left = [row for row in merged if row.get("source") == "dod_announcement"]
            usa = [row for row in merged if row.get("source") == "usaspending"]
            if dod_left or len(usa) != 1 or usa[0].get("dod_url") is None or usa[0].get("branch") != "Navy":
                raise SystemExit(f"self-check dedupe {merged}")
            if block.get("rows") != 0:
                raise SystemExit("self-check dedupe rows")
        finally:
            conn.close()

        marker = folder_path / "contracts.json"
        marker.write_text('{"awards":[],"marker":1}\n', encoding="utf-8")
        db_path = folder_path / "live.sqlite"
        hold = connect(db_path)
        try:
            ensure_dod(hold)
            hold.execute(
                """
                INSERT INTO dod_awards (
                  article_id, para_idx, awardee_idx, publish_date, branch, company,
                  amount, contract, contract_norm, is_mod, is_multi, is_idiq, is_fms,
                  obligated, ticker, match_how, url, text_clip, n_awardees
                ) VALUES ('keep', 0, 0, '2026-09-25', 'ARMY', 'Keep', 5,
                  'W1', 'W1', 0, 0, 0, 0, NULL, 'LMT', 'exact', 'https://example.test', 'x', 1)
                """
            )
            hold.commit()
            before = hold.execute("SELECT COUNT(*) FROM dod_awards").fetchone()[0]
        finally:
            hold.close()
        blob = marker.read_bytes()
        ns = argparse.Namespace(
            simulate_status="403",
            sqlite=db_path,
            json=marker,
            no_groks=True,
            backfill_days=None,
            sleep=0,
            budget=5,
            catalog=cat,
        )
        code = run(ns, sleeper=lambda _seconds: None)
        if code != 0:
            raise SystemExit("self-check 403 exit")
        after_conn = connect(db_path)
        try:
            after = after_conn.execute("SELECT COUNT(*) FROM dod_awards").fetchone()[0]
        finally:
            after_conn.close()
        if after != before or marker.read_bytes() != blob:
            raise SystemExit("self-check 403 changed rows")
    print("self-check ok", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest DoD contract announcements.")
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--no-groks", action="store_true")
    parser.add_argument("--backfill-days", type=int, default=None)
    parser.add_argument("--sleep", type=float, default=2.0)
    parser.add_argument("--budget", type=float, default=0)
    parser.add_argument("--simulate-status", default="")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    if args.self_check:
        self_check()
        return
    args.catalog = load_catalog(ROOT)
    print(f"catalog names {len(args.catalog.names)} tape {len(args.catalog.tape)}", flush=True)
    raise SystemExit(run(args))


if __name__ == "__main__":
    main()
