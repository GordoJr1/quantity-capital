#!/usr/bin/env python3
"""Ingest company-disclosed 2025 mine production into qc.sqlite, then export.

SQLite is the store (canada_mines / canada_mine_production /
canada_mine_sources). Pages only gets a thin export: existing
beta/<issuer>.json production/by_asset plus canada/producer-join.json.
National StatCan/NRCan totals stay in build_canada_commodities.py.

    python3 scripts/ingest_canada_mine_production.py --sqlite qc.sqlite
    python3 scripts/ingest_canada_mine_production.py --sqlite qc.sqlite --apply
    python3 scripts/ingest_canada_mine_production.py --sqlite qc.sqlite --export-only
    python3 scripts/ingest_canada_mine_production.py --offline --sqlite /tmp/qc-test.sqlite
    python3 scripts/ingest_canada_mine_production.py --check

Never invent ounces. Never commit qc.sqlite. Never mint new issuer JSON.
Never split AuEq / GEO or a complex total. Match each producer file's
units.gold (koz on Newmont). SEDAR+ is paywalled — use IR / EDGAR URLs.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_canada_commodities as b
import canada_beta_production as beta

SQ_DIR = HERE / "qc_sqlite"
if str(SQ_DIR) not in sys.path:
    sys.path.insert(0, str(SQ_DIR))
import canada_mines as cmsql  # noqa: E402

SOURCES = HERE / "canada-mine-production-sources.json"
JOIN = ROOT / "canada" / "producer-join.json"
DEFAULT_SQLITE = ROOT / "qc.sqlite"
FIXTURES = HERE / "fixtures" / "canada"
SCHEMA = "qc-canada-mine-production-v1"
YEAR = 2025
YTD_YEAR = 2026
YTD_PERIOD = "2026-YTD"
SLEEP_S = 0.2
NEAR_WINDOW = 1500
FOOTNOTE = (
    "From company filings where disclosed; StatCan/NRCan do not publish "
    "mine-level output. Stored in qc.sqlite; Pages reads the thin export."
)
UNIT_NOTE = (
    "Company reports of gold, silver, platinum, palladium, and rhodium "
    "ounces are troy ounces (mining 'oz' = troy oz). koz is ×1,000. "
    "Kilograms convert at 1 troy oz = 31.1034768 g. Other commodities "
    "keep the source unit and label it. AuEq / GEO is never stored as gold."
)

AUEQ = re.compile(
    r"\b(aueq|au-eq|au eq|gold equivalent|geo|geos|oz eq)\b",
    re.I,
)
TAG = re.compile(r"<[^>]+>")
WS = re.compile(r"\s+")
# Compressed-PDF string dumps look like object soup, not filing prose.
PDF_SOUP = re.compile(r"\bendobj\b.*/Type\s*/Page", re.I | re.S)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_sources(path: Path = SOURCES) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def strip_markup(raw: bytes | str) -> str:
    if isinstance(raw, bytes):
        text = raw.decode("utf-8", "replace")
        if "PDF" in text[:8] or raw[:4] == b"%PDF":
            # Best-effort PDF string dump — enough to confirm a quote.
            chunks = re.findall(rb"[\x20-\x7e]{4,}", raw)
            text = " ".join(c.decode("ascii", "ignore") for c in chunks)
    else:
        text = raw
    text = html.unescape(TAG.sub(" ", text))
    return WS.sub(" ", text)


def contains_all(haystack: str, needles: list[str]) -> bool:
    folded = haystack.lower()
    return all((n or "").lower() in folded for n in needles if n)


def extract_looks_unusable(text: str | None) -> bool:
    """True when fetch returned bytes but not searchable filing text."""
    if text is None:
        return True
    stripped = text.strip()
    if len(stripped) < 40:
        return True
    if PDF_SOUP.search(stripped[:20000]) or ("endobj" in stripped and "/Type /Page" in stripped):
        return True
    return False


def convert_reported(
    value: float | None,
    unit: str,
    commodity: str,
) -> tuple[float | None, str, str | None]:
    """(canonical_value, canonical_unit, blocker)."""
    if value is None:
        return None, "", "no value"
    unit_l = (unit or "").strip().lower()
    cid = b.commodity_id(commodity)
    if AUEQ.search(unit_l) or AUEQ.search(commodity):
        return None, "", "aueq_not_split"
    def _num(x: float) -> float:
        return int(x) if float(x).is_integer() else float(x)

    if cid in b.TROY_OZ_IDS or cid in b.TROY_OZ_CANONICAL:
        if unit_l in {"koz", "koz t", "000 oz", "thousand ounces", "thousands of ounces"}:
            return _num(float(value) * 1000.0), b.TROY_OZ_UNIT, None
        if unit_l in {"moz", "million ounces"}:
            return _num(float(value) * 1_000_000.0), b.TROY_OZ_UNIT, None
        # Mining ounces of gold/silver/PGM are troy ounces.
        if unit_l in {
            "oz", "ozs", "ounce", "ounces", "oz t", "ozt", "troy oz",
            "troy ounce", "troy ounces",
        }:
            return _num(float(value)), b.TROY_OZ_UNIT, None
        converted = b.to_troy_oz(float(value), unit or "")
        if converted is None:
            return None, "", f"unknown_pm_unit:{unit}"
        return _num(converted), b.TROY_OZ_UNIT, None
    if not unit_l:
        return None, "", "unknown_unit:"
    code, _label = b.unit_norm(unit)
    if unit_l in {"mlb", "mlbs", "million pounds", "million lb"}:
        return _num(float(value)), "Mlb", None
    if unit_l in {"lb", "lbs", "pound", "pounds"}:
        return _num(float(value)), "lb", None
    if unit_l in {
        "mt", "million tonnes", "million t", "million metric tonnes",
        "million tonnes kcl", "wmt",
    }:
        return _num(float(value)), "Mt", None
    if unit_l in {"kt", "000 t", "000t", "thousand tonnes", "thousand t"}:
        return _num(float(value)), "kt", None
    if unit_l in {
        "kct", "000 carats", "000 cts", "thousand carats", "'000 carats",
    }:
        return _num(float(value)), "kct", None
    if unit_l in {"million carats", "mct", "m cts", "m carats"}:
        return _num(float(value) * 1000.0), "kct", None
    if unit_l in {"carat", "carats", "ct", "cts"}:
        return _num(float(value)), "ct", None
    if code in {"t", "kg", "g"}:
        return _num(float(value)), code, None
    return None, "", f"unknown_unit:{unit}"


def fetch_source_text(url: str, timeout: int = 90) -> str:
    return strip_markup(b.fetch(url, timeout=timeout))


def number_forms(value: Any) -> list[str]:
    """Ways a filing may print a figure: 192808, 192,808, 192 808; 3.0 / 3."""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return [str(value)] if value not in (None, "") else []
    forms = {str(value)}
    if num.is_integer():
        i = int(num)
        forms |= {str(i), f"{i:,}", f"{i:,}".replace(",", " "), f"{i:,}".replace(",", "\u00a0")}
    else:
        forms |= {repr(num), f"{num:,}"}
    return sorted(forms, key=len, reverse=True)


def number_near_anchor(text: str, value: Any, anchors: list[str], window: int = NEAR_WINDOW) -> bool:
    """True when `value` is printed within `window` chars of any anchor occurrence."""
    folded = text.lower()
    pats = [
        re.compile(r"(?<![\d.,])" + re.escape(f) + r"(?![\d]|[.,]\d)")
        for f in number_forms(value)
    ]
    if not pats:
        return False
    for anchor in anchors:
        a = (anchor or "").strip().lower()
        if not a:
            continue
        start = folded.find(a)
        while start >= 0:
            lo = max(0, start - window)
            hi = min(len(text), start + len(a) + window)
            span = text[lo:hi]
            if any(p.search(span) for p in pats):
                return True
            start = folded.find(a, start + 1)
    return False


def verify_extract(text: str, extract: dict[str, Any]) -> tuple[bool, str]:
    needles = list(extract.get("must_contain") or [])
    quote = (extract.get("quote") or "").strip()
    if quote:
        needles.append(quote)
    if not needles:
        return False, "no_quote"
    if not contains_all(text, needles):
        return False, "quote_not_found"
    value = extract.get("source_value")
    if value is not None:
        anchors = [quote] if quote else []
        anchors += [n for n in (extract.get("must_contain") or []) if n and not number_forms_match(n, value)]
        if not number_near_anchor(text, value, anchors):
            return False, "value_not_near_quote"
    return True, "ok"


def number_forms_match(needle: str, value: Any) -> bool:
    return needle.strip() in number_forms(value)


def record_for_source(
    src: dict[str, Any],
    *,
    text: str | None,
    fetch_ok: bool,
    fetch_error: str | None,
) -> dict[str, Any]:
    rec: dict[str, Any] = {
        "mine_id": src["mine_id"],
        "mine_name": src.get("mine_name") or src["mine_id"],
        "owner": src.get("owner"),
        "year": int(src.get("year") or YEAR),
        "kind": src.get("kind"),
        "period": src.get("period"),
        "through": src.get("through"),
        "period_label": src.get("period_label"),
        "production_source": src.get("url"),
        "production_source_title": src.get("title"),
        "production_as_of": src.get("as_of"),
        "commodities": {},
        "blocker": src.get("blocker"),
        "fetch_ok": fetch_ok,
    }
    if fetch_error:
        rec["fetch_blocker"] = fetch_error
    if src.get("blocker") and not src.get("extract"):
        return rec

    extracts = src.get("extract") or {}
    for cid, ext in extracts.items():
        if not isinstance(ext, dict):
            continue
        if AUEQ.search(cid) or AUEQ.search(str(ext.get("commodity") or "")):
            rec.setdefault("skipped", []).append({"commodity": cid, "why": "aueq_not_split"})
            continue
        quote = (ext.get("quote") or src.get("quote") or "").strip()
        ok = True
        why = "curated"
        usable = text is not None and not extract_looks_unusable(text)
        if usable:
            ok, why = verify_extract(text, {**ext, "quote": quote or ext.get("quote")})
        elif not fetch_ok or extract_looks_unusable(text):
            # Keep a previously researched figure only when the source book
            # already carries a verbatim quote + URL. Monthly re-run should
            # re-verify; a transient 403 or compressed-PDF dump does not invent.
            ok = bool(quote and src.get("url") and ext.get("source_value") is not None)
            why = "cached_quoted" if ok else (fetch_error or "no_text")
        if not ok:
            rec.setdefault("skipped", []).append({"commodity": cid, "why": why})
            continue
        value, unit, conv_block = convert_reported(
            ext.get("source_value"),
            ext.get("source_unit") or "",
            cid,
        )
        if conv_block or value is None:
            rec.setdefault("skipped", []).append({"commodity": cid, "why": conv_block})
            continue
        rec["commodities"][cid] = {
            "value": value,
            "unit": unit,
            "source_value": ext.get("source_value"),
            "source_unit": ext.get("source_unit"),
            "quote": quote or None,
        }
    if rec["commodities"]:
        rec["blocker"] = None
    elif not rec.get("blocker"):
        rec["blocker"] = "not_disclosed_or_unverified"
    rec = {k: v for k, v in rec.items() if v is not None or k in {"commodities", "blocker", "fetch_ok"}}
    return rec


def ytd_block_as_src(src: dict[str, Any]) -> dict[str, Any] | None:
    """Optional 2026 YTD sidecar. Never treated as a full calendar year."""
    ytd = src.get("ytd")
    if not isinstance(ytd, dict):
        return None
    if not (ytd.get("extract") or ytd.get("blocker")):
        return None
    out = {
        "mine_id": src["mine_id"],
        "mine_name": src.get("mine_name") or src["mine_id"],
        "owner": src.get("owner"),
        "year": int(ytd.get("year") or YTD_YEAR),
        "kind": ytd.get("kind") or "ytd",
        "period": ytd.get("period") or YTD_PERIOD,
        "through": ytd.get("through"),
        "period_label": ytd.get("period_label"),
        "url": ytd.get("url"),
        "title": ytd.get("title"),
        "as_of": ytd.get("as_of"),
        "extract": ytd.get("extract"),
        "blocker": ytd.get("blocker"),
    }
    return out


def _fetch_url_text(
    url: str,
    *,
    cache: dict[str, tuple[str | None, bool, str | None]],
    fetch_live: bool,
    offline: bool,
    fixture_text: str,
) -> tuple[str | None, bool, str | None]:
    if not url:
        return None, False, None
    if url in cache:
        return cache[url]
    if offline:
        cache[url] = (fixture_text, bool(fixture_text), None)
        return cache[url]
    if not fetch_live:
        cache[url] = (None, False, None)
        return cache[url]
    try:
        time.sleep(SLEEP_S)
        text = fetch_source_text(url)
        if extract_looks_unusable(text):
            cache[url] = (None, False, "unusable_extract")
        else:
            cache[url] = (text, True, None)
    except Exception as exc:
        cache[url] = (None, False, f"{type(exc).__name__}: {exc}")
    return cache[url]


def _ytd_sidecar(ytd_rec: dict[str, Any], ytd_src: dict[str, Any]) -> dict[str, Any]:
    side: dict[str, Any] = {
        "year": int(ytd_rec.get("year") or ytd_src.get("year") or YTD_YEAR),
        "period": ytd_src.get("period") or YTD_PERIOD,
        "kind": "ytd",
        "through": ytd_src.get("through") or ytd_rec.get("through"),
        "period_label": ytd_src.get("period_label") or ytd_rec.get("period_label"),
        "production_source": ytd_rec.get("production_source") or ytd_src.get("url"),
        "production_source_title": ytd_rec.get("production_source_title") or ytd_src.get("title"),
        "production_as_of": ytd_rec.get("production_as_of") or ytd_src.get("as_of"),
        "commodities": ytd_rec.get("commodities") or {},
        "fetch_ok": ytd_rec.get("fetch_ok"),
    }
    if ytd_rec.get("fetch_blocker"):
        side["fetch_blocker"] = ytd_rec["fetch_blocker"]
    if ytd_rec.get("blocker") and not side["commodities"]:
        side["blocker"] = ytd_rec["blocker"]
    elif side["commodities"]:
        side["blocker"] = None
    return {k: v for k, v in side.items() if v is not None or k in {"commodities"}}


def ingest(
    *,
    root: Path = ROOT,
    sources_path: Path | None = None,
    offline: bool = False,
    fetch_live: bool = True,
) -> dict[str, Any]:
    book = load_sources(sources_path or SOURCES)
    rows = book.get("mines") or []
    mines: dict[str, Any] = {}
    blockers: list[str] = list(book.get("blockers_global") or [])
    if offline:
        fetch_live = False
        fixture = FIXTURES / "mine_production_sample.html"
        fixture_text = fixture.read_text(encoding="utf-8") if fixture.exists() else ""
    else:
        fixture_text = ""
    cache: dict[str, tuple[str | None, bool, str | None]] = {}
    unit_blockers: list[str] = []

    for src in rows:
        mid = src["mine_id"]
        text, fetch_ok, fetch_error = None, False, None
        if src.get("url") and (src.get("extract") or offline):
            text, fetch_ok, fetch_error = _fetch_url_text(
                src["url"],
                cache=cache,
                fetch_live=fetch_live,
                offline=offline,
                fixture_text=fixture_text,
            )
        rec = record_for_source(src, text=text, fetch_ok=fetch_ok, fetch_error=fetch_error)
        unit_blockers.extend(_unit_blockers(mid, rec))
        ytd_src = ytd_block_as_src(src)
        if ytd_src:
            ytd_text, ytd_ok, ytd_err = None, False, None
            if ytd_src.get("url") and (ytd_src.get("extract") or offline):
                ytd_text, ytd_ok, ytd_err = _fetch_url_text(
                    ytd_src["url"],
                    cache=cache,
                    fetch_live=fetch_live,
                    offline=offline,
                    fixture_text=fixture_text,
                )
            ytd_rec = record_for_source(
                ytd_src, text=ytd_text, fetch_ok=ytd_ok, fetch_error=ytd_err
            )
            unit_blockers.extend(_unit_blockers(f"{mid} 2026-YTD", ytd_rec))
            rec["ytd"] = _ytd_sidecar(ytd_rec, ytd_src)
            if rec["ytd"].get("fetch_blocker"):
                blockers.append(f"{mid} 2026-YTD: {rec['ytd']['fetch_blocker']}")
            if rec["ytd"].get("blocker") and not rec["ytd"].get("commodities"):
                blockers.append(f"{mid} 2026-YTD: {rec['ytd']['blocker']}")
        mines[mid] = rec
        if rec.get("fetch_blocker"):
            blockers.append(f"{mid}: {rec['fetch_blocker']}")
        if rec.get("blocker") and not rec.get("commodities"):
            blockers.append(f"{mid}: {rec['blocker']}")

    n_fig = sum(1 for r in mines.values() if r.get("commodities"))
    n_ytd = sum(1 for r in mines.values() if (r.get("ytd") or {}).get("commodities"))
    return {
        "schema": SCHEMA,
        "generated": utc_now(),
        "year": YEAR,
        "disclaimer": FOOTNOTE,
        "unit_note": UNIT_NOTE,
        "sources_file": "scripts/canada-mine-production-sources.json",
        "n_sources": len(rows),
        "n_with_figure": n_fig,
        "n_with_ytd": n_ytd,
        "n_blank": len(mines) - n_fig,
        "blockers": blockers,
        "unit_blockers": unit_blockers,
        "mines": mines,
    }


def _unit_blockers(label: str, rec: dict[str, Any]) -> list[str]:
    return [
        f"{label}: {row['commodity']} {row['why']}"
        for row in rec.get("skipped") or []
        if str(row.get("why") or "").startswith(("unknown_unit", "unknown_pm_unit"))
    ]


def overlay_mines(mines: list[dict[str, Any]], book: dict[str, Any]) -> int:
    """Attach join pointers only. Figures live on beta/<issuer>.json."""
    if book.get("schema") == beta.JOIN_SCHEMA:
        return beta.attach_join_pointers(mines, book)
    # Legacy sidecar (tests): keep cited overlay for unit tests.
    by_id = book.get("mines") or {}
    hits = 0
    for mine in mines:
        rec = by_id.get(mine.get("id") or "")
        if not rec:
            continue
        comms = rec.get("commodities") or {}
        for key in beta.PROD_KEYS:
            mine.pop(key, None)
        if comms:
            mine["production_2025"] = {
                cid: row["value"] for cid, row in comms.items() if row.get("value") is not None
            }
            mine["production_unit"] = {
                cid: row["unit"] for cid, row in comms.items() if row.get("value") is not None
            }
            mine["production_source"] = rec.get("production_source")
            if rec.get("production_source_title"):
                mine["production_source_title"] = rec["production_source_title"]
            if rec.get("production_as_of"):
                mine["production_as_of"] = rec["production_as_of"]
            quotes = [row.get("quote") for row in comms.values() if row.get("quote")]
            if quotes:
                mine["production_quote"] = quotes[0]
            hits += 1
        elif rec.get("blocker"):
            mine["production_blocker"] = rec["blocker"]
            if rec.get("production_source"):
                mine["production_source"] = rec["production_source"]
    return hits


def overlay_payload(payload: dict[str, Any], join: dict[str, Any]) -> int:
    """Strip parallel canada-only figures; attach beta join pointers."""
    beta.strip_commodities_overlay(payload)
    seen: set[int] = set()
    hits = 0
    groups = [payload.get("mines") or []]
    for comm in payload.get("commodities") or []:
        groups.append(comm.get("mines") or [])
    for group in groups:
        for mine in group:
            ident = id(mine)
            if ident in seen:
                continue
            seen.add(ident)
            before = mine.get("beta_id")
            overlay_mines([mine], join)
            if mine.get("beta_id") and mine.get("beta_id") != before:
                hits += 1
    payload["mine_production"] = {
        "year": join.get("year") or YEAR,
        "schema": join.get("schema"),
        "n_linked": join.get("n_linked"),
        "n_blank": join.get("n_blank"),
        "note": FOOTNOTE,
        "source": "qc.sqlite via canada/producer-join.json",
    }
    return hits


def validate_book(book: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if book.get("schema") != SCHEMA:
        errors.append("bad schema")
    if book.get("year") != YEAR:
        errors.append("year must be 2025")
    mines = book.get("mines") or {}
    if not mines:
        errors.append("no mines")
    errors.extend(book.get("unit_blockers") or [])
    n_fig = 0
    for mid, rec in mines.items():
        comms = rec.get("commodities") or {}
        if comms:
            n_fig += 1
            if not (rec.get("production_source") or "").startswith("http"):
                errors.append(f"{mid}: figure without URL")
            if rec.get("year") not in (None, YEAR, str(YEAR)):
                errors.append(f"{mid}: annual rec year must be 2025")
            if rec.get("kind") == "ytd":
                errors.append(f"{mid}: 2025 rec must not be kind=ytd")
            for cid, row in comms.items():
                if AUEQ.search(cid):
                    errors.append(f"{mid}: stored AuEq as {cid}")
                if row.get("value") is None:
                    errors.append(f"{mid}: {cid} missing value")
                if cid in b.TROY_OZ_CANONICAL and row.get("unit") != b.TROY_OZ_UNIT:
                    errors.append(f"{mid}: {cid} must be troy oz")
                if not row.get("quote") and not rec.get("production_source_title"):
                    errors.append(f"{mid}: {cid} missing quote/citation")
        banned = ("tonnes", "koz", "quantity", "production_t", "mine_tonnes")
        for key in banned:
            if key in rec:
                errors.append(f"{mid}: banned field {key}")
        ytd = rec.get("ytd")
        if ytd:
            errors.extend(_validate_ytd(mid, ytd))
    if n_fig < 5:
        errors.append(f"need several cited figures, have {n_fig}")
    return errors


def _validate_ytd(mid: str, ytd: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if int(ytd.get("year") or 0) != YTD_YEAR:
        errors.append(f"{mid}: YTD year must be 2026")
    if (ytd.get("kind") or "ytd") != "ytd":
        errors.append(f"{mid}: YTD kind must be ytd, not a full year")
    if (ytd.get("period") or YTD_PERIOD) != YTD_PERIOD:
        errors.append(f"{mid}: YTD period must be {YTD_PERIOD}")
    if not ytd.get("through"):
        errors.append(f"{mid}: YTD missing through")
    if not ytd.get("period_label"):
        errors.append(f"{mid}: YTD missing period_label")
    comms = ytd.get("commodities") or {}
    if comms:
        if not (ytd.get("production_source") or "").startswith("http"):
            errors.append(f"{mid}: YTD figure without URL")
        for cid, row in comms.items():
            if AUEQ.search(cid):
                errors.append(f"{mid}: YTD stored AuEq as {cid}")
            if row.get("value") is None:
                errors.append(f"{mid}: YTD {cid} missing value")
            if cid in b.TROY_OZ_CANONICAL and row.get("unit") != b.TROY_OZ_UNIT:
                errors.append(f"{mid}: YTD {cid} must be troy oz")
    return errors


def validate_overlay(payload: dict[str, Any]) -> list[str]:
    """Commodities book must not be a parallel figure store."""
    errors: list[str] = []
    gold = next((c for c in payload.get("commodities") or [] if c.get("id") == "gold"), None)
    if not gold:
        return ["gold missing"]
    n_beta = 0
    for mine in gold.get("mines") or []:
        prod = mine.get("production_2025")
        if prod:
            errors.append(
                f"{mine.get('id')}: production_2025 belongs on beta/"
                " not canada/commodities.json"
            )
            if "aueq" in prod or "gold-equivalent" in prod:
                errors.append(f"{mine.get('id')}: AuEq stored")
        if mine.get("beta_id"):
            n_beta += 1
    if n_beta < 5:
        errors.append(f"gold table needs several beta join pointers, have {n_beta}")
    return errors


def offline_scratch_root(src_root: Path) -> Path:
    """Temp copy of the export targets (beta/, canada/) for fixture runs."""
    tmp = Path(tempfile.mkdtemp(prefix="qc-canada-offline-"))
    for sub in ("beta", "canada"):
        if (src_root / sub).is_dir():
            shutil.copytree(src_root / sub, tmp / sub)
    return tmp


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=None, help="Site root (default: repo; --offline: temp copy)")
    p.add_argument("--sqlite", type=Path, default=None, help="qc.sqlite path (gitignored; default ./qc.sqlite)")
    p.add_argument("--join", type=Path, default=None)
    p.add_argument("--sources", type=Path, default=SOURCES)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--apply", action="store_true", help="Export from sqlite; strip canada overlay")
    p.add_argument("--apply-only", action="store_true", help="Curated quotes → sqlite → export; no fetch")
    p.add_argument("--export-only", action="store_true", help="Export Pages JSON from an existing sqlite")
    p.add_argument("--no-fetch", action="store_true", help="Use curated quotes without re-fetching")
    args = p.parse_args(argv)

    if args.root is None:
        if args.offline and not args.check and not args.export_only:
            # Fixture books must never rewrite the committed beta/ + canada/ export.
            args.root = offline_scratch_root(ROOT)
            print(f"offline root {args.root} (pass --root to override)")
        else:
            args.root = ROOT
    db_path = args.sqlite or (args.root / "qc.sqlite")
    join_path = args.join or (args.root / "canada" / "producer-join.json")
    comm_path = args.root / "canada" / "commodities.json"

    if args.check:
        errors: list[str] = []
        if join_path.exists():
            join = json.loads(join_path.read_text(encoding="utf-8"))
            errors.extend(beta.validate_join(join, root=args.root))
        else:
            errors.append("missing canada/producer-join.json")
        if db_path.exists():
            con = cmsql.connect(db_path)
            try:
                errors.extend(cmsql.validate_db(con))
            finally:
                con.close()
        if comm_path.exists():
            payload = json.loads(comm_path.read_text(encoding="utf-8"))
            errors.extend(validate_overlay(payload))
        if errors:
            print("canada mine production check FAIL: " + "; ".join(errors), file=sys.stderr)
            return 1
        join = json.loads(join_path.read_text(encoding="utf-8"))
        print(
            f"canada mine production ok join={join.get('schema')} "
            f"figures={join.get('n_with_figure')} ytd={join.get('n_with_ytd')} "
            f"blank={join.get('n_blank')} "
            f"sqlite={'yes' if db_path.exists() else 'export-only'}"
        )
        return 0

    if args.apply_only:
        args.no_fetch = True
        args.apply = True

    if args.export_only:
        if not db_path.exists():
            print(f"missing sqlite {db_path}", file=sys.stderr)
            return 1
        con = cmsql.connect(db_path)
        try:
            cmsql.apply_schema(con)
            exported = cmsql.export_pages(con, root=args.root, join_path=join_path)
            errors = cmsql.validate_db(con)
        finally:
            con.close()
        join = exported["join"]
        stats = exported["stats"]
        print(
            f"exported {join_path} figures={join.get('n_with_figure')} "
            f"ytd={join.get('n_with_ytd')} blank={join.get('n_blank')} "
            f"beta={stats['n_written']}"
        )
        errors.extend(beta.validate_join(join, root=args.root))
        if comm_path.exists() and args.apply:
            payload = json.loads(comm_path.read_text(encoding="utf-8"))
            hits = overlay_payload(payload, join)
            write_json(comm_path, payload)
            errors.extend(validate_overlay(payload))
            print(f"joined {hits} mine rows on {comm_path}")
        if errors:
            print("validate: " + "; ".join(errors), file=sys.stderr)
            return 1
        return 0

    book = ingest(
        root=args.root,
        sources_path=args.sources,
        offline=args.offline,
        fetch_live=not args.no_fetch,
    )
    errors = validate_book(book)
    if book.get("blockers"):
        print("blockers: " + " | ".join(book["blockers"][:8]))
        if len(book["blockers"]) > 8:
            print(f"... {len(book['blockers']) - 8} more")
    if errors:
        print("validate: " + "; ".join(errors), file=sys.stderr)
        print(f"blocked before store: {db_path} and {join_path} left unchanged", file=sys.stderr)
        return 1
    sources_book = load_sources(args.sources)
    con = cmsql.connect(db_path)
    try:
        counts = cmsql.store_book(con, book, sources_book)
        exported = cmsql.export_pages(con, root=args.root, join_path=join_path)
        db_errors = cmsql.validate_db(con)
    finally:
        con.close()
    join = exported["join"]
    stats = exported["stats"]
    print(
        f"sqlite {db_path} mines={counts['n_mines']} "
        f"production={counts['n_production']} sources={counts['n_sources']}"
    )
    print(
        f"exported {join_path} figures={join.get('n_with_figure')} "
        f"ytd={join.get('n_with_ytd')} blank={join.get('n_blank')} "
        f"beta={stats['n_written']} "
        f"skipped_new={stats['skipped'] and len(stats['skipped'])}"
    )
    for line in (stats.get("unit_skipped") or [])[:8]:
        print(f"unit mismatch skipped: {line}", file=sys.stderr)
    for line in (stats.get("conflicts") or [])[:8]:
        print(f"kept filed figure: {line}", file=sys.stderr)
    errors.extend(db_errors)
    errors.extend(beta.validate_join(join, root=args.root))

    if args.apply and comm_path.exists():
        payload = json.loads(comm_path.read_text(encoding="utf-8"))
        hits = overlay_payload(payload, join)
        errors.extend(validate_overlay(payload))
        write_json(comm_path, payload)
        print(f"joined {hits} mine rows on {comm_path} (figures from sqlite export)")

    if errors:
        print("validate: " + "; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
