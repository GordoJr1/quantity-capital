#!/usr/bin/env python3
"""Ingest company-disclosed 2025 mine production for canada.html.

National StatCan/NRCan totals stay in build_canada_commodities.py.
This job only attaches **cited** mine-level actuals from public issuer
reports (news/ops update, MD&A, AIF, annual, NI 43-101 actuals).

    python3 scripts/ingest_canada_mine_production.py            # fetch + write sidecar
    python3 scripts/ingest_canada_mine_production.py --apply    # also overlay commodities.json
    python3 scripts/ingest_canada_mine_production.py --offline  # fixtures only
    python3 scripts/ingest_canada_mine_production.py --check
    python3 scripts/ingest_canada_mine_production.py --apply-only

Never invent ounces. Never split AuEq / GEO into gold. Never split a
complex total across Map 900A pits. Leave the cell blank when the filing
does not name that mine's selected commodity.

Not a morning/evening tape bat. Not daily-update. SEDAR+ is paywalled —
use the issuer IR / EDGAR HTML (or PDF) URLs in the sources book.
"""
from __future__ import annotations

import argparse
import html
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_canada_commodities as b

SOURCES = HERE / "canada-mine-production-sources.json"
OUT = ROOT / "canada" / "mine-production.json"
FIXTURES = HERE / "fixtures" / "canada"
SCHEMA = "qc-canada-mine-production-v1"
YEAR = 2025
SLEEP_S = 0.2
FOOTNOTE = (
    "From company filings where disclosed; StatCan/NRCan do not publish "
    "mine-level output."
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
        converted = b.to_troy_oz(float(value), unit)
        if converted is None:
            return None, "", f"unknown_pm_unit:{unit}"
        return _num(converted), b.TROY_OZ_UNIT, None
    code, _label = b.unit_norm(unit)
    if unit_l in {"mlb", "mlbs", "million pounds", "million lb"}:
        return _num(float(value)), "Mlb", None
    if unit_l in {"lb", "lbs", "pound", "pounds"}:
        return _num(float(value)), "lb", None
    return _num(float(value)), code or unit_l or "t", None


def fetch_source_text(url: str, timeout: int = 90) -> str:
    return strip_markup(b.fetch(url, timeout=timeout))


def verify_extract(text: str, extract: dict[str, Any]) -> tuple[bool, str]:
    needles = list(extract.get("must_contain") or [])
    quote = (extract.get("quote") or "").strip()
    if quote:
        needles.append(quote)
    value = extract.get("source_value")
    if value is not None:
        raw = str(value)
        pretty = f"{value:,}" if isinstance(value, (int, float)) and float(value) >= 1000 else raw
        # 231 koz may appear as "231" without a comma.
        if pretty not in text and raw not in text and pretty.replace(",", "") not in text:
            # Allow 231000 written as 231,000
            if pretty.replace(",", "") + "000" not in text.replace(",", ""):
                pass  # quote / must_contain is the real gate
    if not needles:
        return False, "no_quote"
    if not contains_all(text, needles):
        return False, "quote_not_found"
    return True, "ok"


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
        if text is not None:
            ok, why = verify_extract(text, {**ext, "quote": quote or ext.get("quote")})
        elif not fetch_ok:
            # Keep a previously researched figure only when the source book
            # already carries a verbatim quote + URL. Monthly re-run should
            # re-verify; a transient 403 does not invent a new number.
            ok = bool(quote and src.get("url") and ext.get("source_value") is not None)
            why = "cached_quoted" if ok else (fetch_error or "no_text")
        if not ok:
            rec.setdefault("skipped", []).append({"commodity": cid, "why": why})
            continue
        value, unit, conv_block = convert_reported(
            ext.get("source_value"),
            ext.get("source_unit") or "oz",
            cid,
        )
        if conv_block or value is None:
            rec.setdefault("skipped", []).append({"commodity": cid, "why": conv_block})
            continue
        rec["commodities"][cid] = {
            "value": value,
            "unit": unit,
            "source_value": ext.get("source_value"),
            "source_unit": ext.get("source_unit") or "oz",
            "quote": quote or None,
        }
    if rec["commodities"]:
        rec["blocker"] = None
    elif not rec.get("blocker"):
        rec["blocker"] = "not_disclosed_or_unverified"
    return rec


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

    for src in rows:
        mid = src["mine_id"]
        text = None
        fetch_ok = False
        fetch_error = None
        if offline:
            text = fixture_text
            fetch_ok = bool(text)
        elif fetch_live and src.get("url") and src.get("extract"):
            try:
                time.sleep(SLEEP_S)
                text = fetch_source_text(src["url"])
                fetch_ok = True
            except Exception as exc:
                fetch_error = f"{type(exc).__name__}: {exc}"
                fetch_ok = False
        rec = record_for_source(src, text=text, fetch_ok=fetch_ok, fetch_error=fetch_error)
        mines[mid] = rec
        if rec.get("fetch_blocker"):
            blockers.append(f"{mid}: {rec['fetch_blocker']}")
        if rec.get("blocker") and not rec.get("commodities"):
            blockers.append(f"{mid}: {rec['blocker']}")

    n_fig = sum(1 for r in mines.values() if r.get("commodities"))
    return {
        "schema": SCHEMA,
        "generated": utc_now(),
        "year": YEAR,
        "disclaimer": FOOTNOTE,
        "unit_note": UNIT_NOTE,
        "sources_file": "scripts/canada-mine-production-sources.json",
        "n_sources": len(rows),
        "n_with_figure": n_fig,
        "n_blank": len(mines) - n_fig,
        "blockers": blockers,
        "mines": mines,
    }


def overlay_mines(mines: list[dict[str, Any]], book: dict[str, Any]) -> int:
    """Attach production fields onto Map 900A mine dicts. Returns hits."""
    by_id = book.get("mines") or {}
    hits = 0
    for mine in mines:
        rec = by_id.get(mine.get("id") or "")
        if not rec:
            continue
        comms = rec.get("commodities") or {}
        # Drop unsourced leftovers if we re-overlay a blank mine.
        for key in (
            "production_2025",
            "production_unit",
            "production_source",
            "production_source_title",
            "production_as_of",
            "production_quote",
            "production_blocker",
        ):
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


def overlay_payload(payload: dict[str, Any], book: dict[str, Any]) -> int:
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
            before = "production_2025" in mine
            overlay_mines([mine], book)
            if "production_2025" in mine or (not before and mine.get("production_blocker")):
                hits += 1
    payload["mine_production"] = {
        "year": book.get("year") or YEAR,
        "schema": book.get("schema"),
        "n_with_figure": book.get("n_with_figure"),
        "n_blank": book.get("n_blank"),
        "note": FOOTNOTE,
    }
    if FOOTNOTE not in (payload.get("disclaimer") or ""):
        extra = (
            " Mine-level 2025 production is from company filings where disclosed; "
            "StatCan/NRCan do not publish mine-level output. Blank is not zero."
        )
        payload["disclaimer"] = ((payload.get("disclaimer") or "").rstrip() + extra).strip()
    note = payload.get("unit_note") or ""
    if "company filings" not in note.lower():
        payload["unit_note"] = (note.rstrip() + " " + UNIT_NOTE).strip()
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
    n_fig = 0
    for mid, rec in mines.items():
        comms = rec.get("commodities") or {}
        if comms:
            n_fig += 1
            if not (rec.get("production_source") or "").startswith("http"):
                errors.append(f"{mid}: figure without URL")
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
    if n_fig < 5:
        errors.append(f"need several cited figures, have {n_fig}")
    return errors


def validate_overlay(payload: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    gold = next((c for c in payload.get("commodities") or [] if c.get("id") == "gold"), None)
    if not gold:
        return ["gold missing"]
    n = 0
    for mine in gold.get("mines") or []:
        prod = mine.get("production_2025")
        if not prod:
            continue
        n += 1
        if not (mine.get("production_source") or "").startswith("http"):
            errors.append(f"{mine.get('id')}: overlay without URL")
        if "gold" in prod and mine.get("production_unit", {}).get("gold") != b.TROY_OZ_UNIT:
            errors.append(f"{mine.get('id')}: gold overlay not troy oz")
        if "aueq" in prod or "gold-equivalent" in prod:
            errors.append(f"{mine.get('id')}: AuEq stored")
    if n < 5:
        errors.append(f"gold table needs several cited 2025 figures, have {n}")
    return errors


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--root", type=Path, default=ROOT)
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--sources", type=Path, default=SOURCES)
    p.add_argument("--offline", action="store_true")
    p.add_argument("--check", action="store_true")
    p.add_argument("--apply", action="store_true", help="Overlay canada/commodities.json after ingest")
    p.add_argument("--apply-only", action="store_true", help="Overlay from the committed sidecar; no fetch")
    p.add_argument("--no-fetch", action="store_true", help="Use curated quotes without re-fetching")
    args = p.parse_args(argv)

    out = args.out or (args.root / "canada" / "mine-production.json")
    comm_path = args.root / "canada" / "commodities.json"

    if args.check:
        book = json.loads(out.read_text(encoding="utf-8"))
        errors = validate_book(book)
        if comm_path.exists():
            payload = json.loads(comm_path.read_text(encoding="utf-8"))
            errors.extend(validate_overlay(payload))
        if errors:
            print("canada mine production check FAIL: " + "; ".join(errors), file=sys.stderr)
            return 1
        print(
            f"canada mine production ok schema={book.get('schema')} "
            f"figures={book.get('n_with_figure')} blank={book.get('n_blank')}"
        )
        return 0

    if args.apply_only:
        book = json.loads(out.read_text(encoding="utf-8"))
        payload = json.loads(comm_path.read_text(encoding="utf-8"))
        hits = overlay_payload(payload, book)
        errors = validate_book(book) + validate_overlay(payload)
        write_json(comm_path, payload)
        print(f"overlaid {hits} mine rows from {out}")
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
    write_json(out, book)
    print(
        f"wrote {out} figures={book['n_with_figure']} blank={book['n_blank']} "
        f"sources={book['n_sources']}"
    )
    if book.get("blockers"):
        print("blockers: " + " | ".join(book["blockers"][:8]))
        if len(book["blockers"]) > 8:
            print(f"... {len(book['blockers']) - 8} more")

    if args.apply:
        payload = json.loads(comm_path.read_text(encoding="utf-8"))
        hits = overlay_payload(payload, book)
        errors.extend(validate_overlay(payload))
        write_json(comm_path, payload)
        print(f"overlaid {hits} mine rows on {comm_path}")

    if errors:
        print("validate: " + "; ".join(errors), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
