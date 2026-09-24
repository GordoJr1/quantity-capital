#!/usr/bin/env python3
"""Merge publicly traded claims-map companies into the Insiders tape universe.

Reads claims/companies.json (plus extract holder aliases and beta ticker
catalogs) and every public company already linked in claims/links/holders.json.
Resolves CAD/US tickers where possible. Appends missing publics to
insider-companies.json, deduped by ticker. Canadian additions carry the issuer
name the SEDI pass can search; US additions carry an SEC CIK when the SEC
ticker map has one. Writes a ticker audit (CSV + markdown).

The off-repo collector pushes stale copies of claims/companies.json and
insider-companies.json. Rows and on_bc metadata in claims/pinned-companies.json
are re-applied to the catalog first, and follow-alerts.yml reruns this script
after every collector push. Link publics do not need a pin: they are read from
claims/links, which the collector does not overwrite, and this script appends
them again on the next run.

Does not crawl provinces, does not touch qc.sqlite, does not rewrite the
off-repo tape.

Jev (TypeSafe System One, not Jev Bot) is the merge-gate companion:
  python3 scripts/run_pr_merge_gate.py            # calls API when TYPESAFE_API_KEY exists
  python3 scripts/run_pr_merge_gate.py --packet-only
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import re
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CLAIMS = ROOT / "claims"
CATALOG_PINS = "pinned-companies.json"
PIN_META_KEYS = ("disclaimer", "sources")
INSIDER_COMPANIES = ROOT / "insider-companies.json"
LINKS_HOLDERS = "links/holders.json"
AUDIT_CSV = ROOT / "scripts" / "claims-insider-ticker-audit.csv"
AUDIT_MD = ROOT / "scripts" / "claims-insider-ticker-audit.md"
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_UA = "Quantity Capital gordojr@proton.me"

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
PRIVATE_RE = re.compile(
    r"(?:"
    r"^\d{5,}"
    r"|\b\d{6,}\s+(ontario|british columbia|quebec|canada)\b"
    r"|\b(her|his)\s+majesty\b"
    r"|\b(government|ministry|province of|crown|municipalit)"
    r"|\bnumbered\b"
    r")",
    re.I,
)
PERSON_LIKE_RE = re.compile(r"^[A-Za-zÀ-ÿ' .\-]{3,60}$")
CORP_HINT_RE = re.compile(
    r"\b(inc|corp|ltd|limited|resources|mines|mining|gold|exploration|"
    r"corporation|plc|llc|minerals|metals|royalt|ventures|energy)\b",
    re.I,
)
CAD_SUFFIX = (".TO", ".V", ".CN", ".NE", ".TSX", ".TSXV")
OTHER_SUFFIX = (".AX", ".ASX", ".L", ".JO", ".HK", ".PA", ".F")


def slugify(name: str) -> str:
    s = (name or "").translate(ACCENT).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "unknown"


def norm_name(value: str) -> str:
    s = (value or "").translate(ACCENT).lower()
    s = LEGAL_RE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def looks_private(holder: str) -> bool:
    h = (holder or "").strip()
    if not h:
        return False
    if PRIVATE_RE.search(h):
        return True
    if CORP_HINT_RE.search(h):
        return False
    if re.search(r"\d{5,}", h):
        return True
    return bool(PERSON_LIKE_RE.match(h) and len(h.split()) <= 4 and " " in h)


def classify_ticker(sym: str) -> str:
    s = (sym or "").strip().upper()
    if not s:
        return "other"
    if s.endswith(CAD_SUFFIX):
        return "cad"
    if s.endswith(OTHER_SUFFIX) or "." in s:
        return "other"
    return "us"


def exchange_of(symbol: str) -> str:
    text = (symbol or "").strip().upper()
    if text.endswith(".TO"):
        return "TSX"
    if text.endswith(".V"):
        return "TSXV"
    if text.endswith(".CN"):
        return "CSE"
    if text.endswith(".NE"):
        return "NEO"
    if text.endswith(".AX"):
        return "ASX"
    if text and "." not in text:
        return "US"
    return ""


def exchanges_for(symbols: list[str]) -> str:
    found = []
    for symbol in symbols:
        name = exchange_of(symbol)
        if name and name not in found:
            found.append(name)
    return "; ".join(found)


def load_sec_ciks(cache: Path | None = None, fetch: bool = True) -> dict[str, str]:
    """Ticker → 10-digit SEC CIK. Empty when the map cannot be read."""
    cache = cache or Path("/tmp/sec-company-tickers.json")
    raw = ""
    if cache.is_file():
        raw = cache.read_text(encoding="utf-8")
    elif fetch:
        req = urllib.request.Request(
            SEC_TICKERS_URL,
            headers={"User-Agent": SEC_UA, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
        except (OSError, urllib.error.URLError):
            return {}
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(raw, encoding="utf-8")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    rows = data.values() if isinstance(data, dict) else data
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        cik = row.get("cik_str")
        if ticker and cik is not None:
            out[ticker] = str(int(cik)).zfill(10)
    return out


def attach_identifiers(row: dict, cik_index: dict[str, str]) -> dict:
    """SEDI searches Canadian issuers by name. US filers need a CIK."""
    tickers = [str(t).upper() for t in (row.get("tickers") or []) if t]
    if any(classify_ticker(t) == "cad" for t in tickers):
        row["sedi_name"] = row.get("name") or row.get("id") or ""
    for ticker in tickers:
        if classify_ticker(ticker) != "us":
            continue
        cik = cik_index.get(ticker)
        if cik:
            row["cik"] = cik
            break
    if not row.get("exchange"):
        row["exchange"] = exchanges_for(tickers)
    return row


def load_link_companies(root: Path) -> list[dict]:
    """One public company per claims/links company id that has a ticker."""
    path = root / "claims" / LINKS_HOLDERS
    if not path.is_file():
        return []
    data = load_json(path)
    grouped: dict[str, dict] = {}
    for row in data.get("rows") or []:
        parties = list(row.get("companies") or [])
        if not parties and row.get("company_id"):
            parties = [{"company_id": row.get("company_id"), "company": row.get("company"), "ticker": row.get("ticker"), "exchange": row.get("exchange")}]
        for party in parties:
            cid = (party.get("company_id") or "").strip()
            if not cid:
                continue
            slot = grouped.setdefault(cid, {"id": cid, "holder": "", "names": [], "tickers": [], "exchange": ""})
            name = (party.get("company") or "").strip()
            if name and not slot["holder"]:
                slot["holder"] = name
            if name and name not in slot["names"]:
                slot["names"].append(name)
            ticker = str(party.get("ticker") or "").strip().upper()
            if ticker and ticker not in slot["tickers"]:
                slot["tickers"].append(ticker)
            exchange = (party.get("exchange") or "").strip()
            if exchange and not slot["exchange"]:
                slot["exchange"] = exchange
    out = []
    for slot in grouped.values():
        if not slot["tickers"]:
            continue
        if not slot["holder"]:
            slot["holder"] = slot["id"].replace("-", " ")
        if slot["holder"] not in slot["names"]:
            slot["names"].insert(0, slot["holder"])
        if not slot["exchange"]:
            slot["exchange"] = exchanges_for(slot["tickers"])
        out.append(slot)
    out.sort(key=lambda rec: rec["id"])
    return out


def split_tickers(symbols: list[str]) -> dict[str, list[str]]:
    us, cad, other = [], [], []
    seen: set[str] = set()
    for raw in symbols:
        s = str(raw or "").strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        bucket = classify_ticker(s)
        if bucket == "cad":
            cad.append(s)
        elif bucket == "us":
            us.append(s)
        else:
            other.append(s)
    return {"us": us, "cad": cad, "other": other, "all": us + cad + other}


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def extract_symbols(rec: dict) -> list[str]:
    out: list[str] = []
    for key in ("all", "us", "cad", "other", "tickers"):
        for item in rec.get(key) or []:
            if isinstance(item, dict):
                sym = item.get("symbol") or item.get("ticker")
            else:
                sym = item
            if sym:
                out.append(str(sym).upper())
    return out


def beta_ticker_index(root: Path) -> dict[str, dict]:
    by_id: dict[str, dict] = {}
    for rel in ("beta/mcap.json", "beta/issuers.json", "beta/explorers.json"):
        path = root / rel
        if not path.exists():
            continue
        data = load_json(path)
        for rec in data.get("issuers") or []:
            cid = rec.get("id")
            if not cid:
                continue
            row = by_id.setdefault(cid, {"name": rec.get("name"), "tickers": [], "type": rec.get("type"), "commodity": rec.get("commodity"), "country": rec.get("country") or rec.get("hq")})
            for t in rec.get("tickers") or []:
                t = str(t).upper()
                if t not in row["tickers"]:
                    row["tickers"].append(t)
            for key in ("name", "type", "commodity", "country"):
                if rec.get(key) and not row.get(key):
                    row[key] = rec.get(key)
    for prof in (root / "beta").glob("*.json"):
        if prof.name in {"issuers.json", "explorers.json", "mcap.json"}:
            continue
        try:
            data = load_json(prof)
        except (OSError, json.JSONDecodeError):
            continue
        cid = data.get("id") or prof.stem
        issuer = data.get("issuer") or {}
        tks = []
        for item in issuer.get("tickers") or []:
            if isinstance(item, dict) and item.get("symbol"):
                tks.append(str(item["symbol"]).upper())
            elif isinstance(item, str):
                tks.append(item.upper())
        if not tks and not issuer:
            continue
        row = by_id.setdefault(cid, {"name": issuer.get("short") or issuer.get("name"), "tickers": [], "type": None, "commodity": issuer.get("commodity"), "country": issuer.get("hq")})
        for t in tks:
            if t not in row["tickers"]:
                row["tickers"].append(t)
        if issuer.get("short") or issuer.get("name"):
            row["name"] = issuer.get("short") or issuer.get("name")
    return by_id


def extract_holder_aliases(claims_dir: Path, cid: str, rec: dict) -> list[str]:
    names: list[str] = []
    paths = []
    for key in ("extract", "ontario_extract", "bc_extract"):
        rel = rec.get(key)
        if rel:
            paths.append(claims_dir.parent / rel if not str(rel).startswith("claims/") else claims_dir.parent / rel)
    # also conventional names
    for extra in (claims_dir / f"{cid}.geojson", claims_dir / f"{cid}-ontario.geojson", claims_dir / f"{cid}-bc.geojson"):
        if extra.exists():
            paths.append(extra)
    seen: set[str] = set()
    for path in paths:
        if not path.exists() or path.suffix != ".geojson":
            continue
        try:
            data = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        for feat in (data.get("features") or [])[:80]:
            props = feat.get("properties") or {}
            if props.get("role") == "neighbor":
                continue
            holder = (props.get("holder") or "").strip()
            if holder and holder not in seen:
                seen.add(holder)
                names.append(holder)
            if len(names) >= 8:
                return names
    return names


def index_insider(companies: list[dict]) -> tuple[dict[str, list[dict]], dict[str, list[dict]], dict[str, list[dict]]]:
    by_slug: dict[str, list[dict]] = defaultdict(list)
    by_norm: dict[str, list[dict]] = defaultdict(list)
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for rec in companies:
        name = rec.get("name") or ""
        if name:
            by_slug[slugify(name)].append(rec)
            nn = norm_name(name)
            if nn:
                by_norm[nn].append(rec)
        for t in extract_symbols(rec):
            by_ticker[t].append(rec)
    return by_slug, by_norm, by_ticker


def unique_recs(rows: list[dict]) -> list[dict]:
    out = []
    seen = set()
    for rec in rows:
        key = rec.get("name") or id(rec)
        if key in seen:
            continue
        seen.add(key)
        out.append(rec)
    return out


def match_claims_company(
    rec: dict,
    *,
    insider_by_slug: dict[str, list[dict]],
    insider_by_norm: dict[str, list[dict]],
    insider_by_ticker: dict[str, list[dict]],
    beta: dict[str, dict],
    extract_aliases: list[str] | None = None,
) -> dict:
    cid = rec["id"]
    names = []
    for n in [cid, rec.get("holder"), *(rec.get("names") or []), *(extract_aliases or [])]:
        if n and str(n) not in names:
            names.append(str(n))
    display = rec.get("holder") or (rec.get("names") or [cid])[0]
    beta_row = beta.get(cid) or {}
    tickers = [str(t).upper() for t in (beta_row.get("tickers") or []) if t]

    ticker_hits = unique_recs([h for t in tickers for h in (insider_by_ticker.get(t) or [])])
    catalog_names = []
    for n in [cid, rec.get("holder"), *(rec.get("names") or [])]:
        if n and str(n) not in catalog_names:
            catalog_names.append(str(n))
    name_hits: list[dict] = []
    for n in catalog_names:
        name_hits.extend(insider_by_slug.get(slugify(n)) or [])
        nn = norm_name(n)
        if nn:
            name_hits.extend(insider_by_norm.get(nn) or [])
    name_hits = unique_recs(name_hits)

    # Unique ticker owner wins — extract JV holders must not shadow it.
    if len(ticker_hits) == 1:
        uniq = ticker_hits
    elif len(name_hits) == 1:
        uniq = name_hits
    elif len(ticker_hits) > 1 or len(name_hits) > 1:
        amb = unique_recs(ticker_hits + name_hits)
        ticker_sets = [set(extract_symbols(h)) for h in amb]
        shared = set.intersection(*ticker_sets) if ticker_sets and all(ticker_sets) else set()
        if not shared:
            return {
                "status": "ambiguous",
                "id": cid,
                "name": display,
                "tickers": tickers,
                "insider_name": "; ".join(h.get("name") or "" for h in amb),
                "note": "multiple insider-companies matches",
            }
        uniq = [amb[0]]
    else:
        # last resort: extract holder aliases (focus titles only)
        alias_hits: list[dict] = []
        for n in extract_aliases or []:
            alias_hits.extend(insider_by_slug.get(slugify(n)) or [])
            nn = norm_name(n)
            if nn:
                alias_hits.extend(insider_by_norm.get(nn) or [])
        alias_hits = unique_recs(alias_hits)
        if len(alias_hits) == 1:
            uniq = alias_hits
        elif len(alias_hits) > 1:
            return {
                "status": "ambiguous",
                "id": cid,
                "name": display,
                "tickers": tickers,
                "insider_name": "; ".join(h.get("name") or "" for h in alias_hits),
                "note": "multiple insider-companies matches",
            }
        else:
            uniq = []

    if uniq:
        rec_tks = extract_symbols(uniq[0])
        merged = []
        for t in rec_tks + tickers:
            if t not in merged:
                merged.append(t)
        return {
            "status": "matched",
            "id": cid,
            "name": display,
            "tickers": merged,
            "insider_name": uniq[0].get("name"),
            "note": "already on insider-companies.json" if rec_tks else "name match; tickers from beta",
            "existing": uniq[0],
        }

    if tickers:
        return {
            "status": "matched",
            "id": cid,
            "name": beta_row.get("name") or display,
            "tickers": tickers,
            "insider_name": "",
            "note": "new public from claims + beta tickers",
            "new": True,
            "beta": beta_row,
        }

    if looks_private(display) or any(looks_private(n) for n in names):
        return {
            "status": "private-excluded",
            "id": cid,
            "name": display,
            "tickers": [],
            "insider_name": "",
            "note": "private or unmatched holder",
        }

    return {
        "status": "missing-ticker",
        "id": cid,
        "name": display,
        "tickers": [],
        "insider_name": "",
        "note": "public-looking catalog name with no resolvable CAD/US ticker",
    }


def company_record(row: dict) -> dict:
    beta = row.get("beta") or {}
    split = split_tickers(row.get("tickers") or [])
    name = row.get("name") or row["id"]
    commodity = (beta.get("commodity") or "Gold")
    if isinstance(commodity, str):
        commodity = commodity.replace("gold", "Gold").title() if commodity.islower() else commodity
    country = beta.get("country") or ""
    if isinstance(country, str) and "," in country:
        # hq strings like "Toronto, Ontario, Canada"
        parts = [p.strip() for p in country.split(",") if p.strip()]
        country = parts[-1] if parts else country
    if not country:
        country = "Canada" if split["cad"] else ("United States" if split["us"] else "")
    rec = {
        "name": name,
        "symbol_raw": ", ".join(split["all"]),
        "type": beta.get("type") or "Explorer",
        "commodity": commodity or "Gold",
        "country": country or "Canada",
        "us": split["us"],
        "cad": split["cad"],
        "other": split["other"],
        "all": split["all"],
        "claims_id": row["id"],
        "source": "claims",
    }
    exchange = row.get("exchange") or exchanges_for(split["all"])
    if exchange:
        rec["exchange"] = exchange
    sedi_name = row.get("sedi_name") or ""
    if sedi_name:
        rec["sedi_name"] = sedi_name
    cik = row.get("cik") or ""
    if cik:
        rec["cik"] = cik
    return rec


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    tmp.replace(path)


def write_json_atomic(path: Path, data: Any) -> None:
    write_text_atomic(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def sync_catalog_pins(root: Path, *, write: bool) -> tuple[list[str], list[str]]:
    """Re-apply claims/pinned-companies.json to claims/companies.json.

    Returns (changes, problems). With write=False nothing is written and every
    needed catalog change is reported as a problem. A catalog whose
    on_bc_built_at is newer than the pins refreshes the pins instead (including
    unpinning rows it no longer has), so real rebuilds win.
    """
    pins_path = root / "claims" / CATALOG_PINS
    catalog_path = root / "claims" / "companies.json"
    if not pins_path.exists():
        return [], []
    pins = load_json(pins_path)
    catalog = load_json(catalog_path)
    pin_at = str(pins.get("on_bc_built_at") or "")
    cat_at = str(catalog.get("on_bc_built_at") or "")
    catalog_fresher = cat_at > pin_at
    changes: list[str] = []
    pins_changed = False

    if cat_at < pin_at:
        for key in PIN_META_KEYS:
            if key in (pins.get("meta") or {}) and catalog.get(key) != pins["meta"][key]:
                catalog[key] = pins["meta"][key]
                changes.append(f"restored catalog {key}")
        catalog["on_bc_built_at"] = pin_at
        changes.append(f"restored on_bc_built_at {cat_at or '-'} -> {pin_at}")
    elif catalog_fresher:
        pins["meta"] = {k: catalog.get(k) for k in PIN_META_KEYS if k in catalog}
        pins["on_bc_built_at"] = cat_at
        pins_changed = True

    rows = catalog.setdefault("companies", [])
    by_id = {r.get("id"): i for i, r in enumerate(rows)}
    kept_pins = []
    for pin in pins.get("companies") or []:
        cid = pin.get("id")
        if cid in by_id:
            if catalog_fresher and rows[by_id[cid]] != pin:
                pin = rows[by_id[cid]]
                pins_changed = True
            kept_pins.append(pin)
            continue
        if catalog_fresher:
            # A newer real rebuild dropped it on purpose; stop pinning it.
            pins_changed = True
            continue
        kept_pins.append(pin)
        rows.append(pin)
        by_id[cid] = len(rows) - 1
        changes.append(f"restored catalog row {cid}")
    pins["companies"] = kept_pins

    if not write:
        return [], [f"claims/companies.json: {c} needed (run without --check)" for c in changes]
    if changes:
        write_json_atomic(catalog_path, catalog)
    if pins_changed:
        write_json_atomic(pins_path, pins)
        changes.append("refreshed claims/pinned-companies.json from a newer catalog")
    return changes, []


def write_audit(rows: list[dict], path_csv: Path, path_md: Path) -> None:
    fields = ["status", "id", "name", "tickers", "insider_name", "note"]
    buf = io.StringIO(newline="")
    w = csv.DictWriter(buf, fieldnames=fields)
    w.writeheader()
    for row in rows:
        w.writerow(
            {
                "status": row["status"],
                "id": row["id"],
                "name": row["name"],
                "tickers": " ".join(row.get("tickers") or []),
                "insider_name": row.get("insider_name") or "",
                "note": row.get("note") or "",
            }
        )
    old_csv = path_csv.read_bytes().decode("utf-8") if path_csv.exists() else None
    if old_csv != buf.getvalue():
        write_text_atomic(path_csv, buf.getvalue())

    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[row["status"]] += 1
    lines = [
        "# Claims → Insiders ticker audit",
        "",
        f"Generated `{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}`.",
        "Source: `claims/companies.json` plus public companies in `claims/links/holders.json`, extract holder aliases, and beta ticker catalogs.",
        "Private / unmatched holders are excluded from the Insiders universe.",
        "",
        "| Status | Count |",
        "| --- | ---: |",
        f"| matched | {counts.get('matched', 0)} |",
        f"| missing-ticker | {counts.get('missing-ticker', 0)} |",
        f"| ambiguous | {counts.get('ambiguous', 0)} |",
        f"| private-excluded | {counts.get('private-excluded', 0)} |",
        f"| **total** | {len(rows)} |",
        "",
        "Empty-filings policy: publics stay on the issuer list/tape with a **no filings yet** badge.",
        "They are not hidden until SEDI / Form 4 prints land.",
        "",
        "| Status | id | name | tickers | insider name | note |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        tks = ", ".join(row.get("tickers") or []) or "—"
        lines.append(
            f"| {row['status']} | `{row['id']}` | {row['name']} | {tks} | {row.get('insider_name') or '—'} | {row.get('note') or ''} |"
        )
    text = "\n".join(lines) + "\n"
    if path_md.exists():
        stamp = re.compile(r"^Generated `[^`]*`\.$", re.M)
        old = path_md.read_text(encoding="utf-8")
        if stamp.sub("", old) == stamp.sub("", text):
            return
    write_text_atomic(path_md, text)


def append_companies(path: Path, new_rows: list[dict]) -> int:
    """Append missing publics without rewriting the rest of the watchlist JSON."""
    if not new_rows:
        return 0
    data = load_json(path)
    existing_tks = {t for rec in data.get("companies") or [] for t in extract_symbols(rec)}
    existing_norms = {norm_name(rec.get("name") or "") for rec in data.get("companies") or []}
    to_add = []
    for row in new_rows:
        rec = company_record(row)
        tks = set(rec["all"])
        if tks & existing_tks or norm_name(rec["name"]) in existing_norms:
            continue
        to_add.append(rec)
        existing_tks |= tks
        existing_norms.add(norm_name(rec["name"]))
    if not to_add:
        return 0
    text = path.read_text(encoding="utf-8")
    # bump count in place (collector field)
    text, n_count = re.subn(
        r'("count":\s*)(\d+)',
        lambda m: f"{m.group(1)}{int(m.group(2)) + len(to_add)}",
        text,
        count=1,
    )
    if n_count != 1:
        raise RuntimeError("could not bump insider-companies.json count")
    insert_at = text.rstrip().rfind("\n  ]\n}")
    if insert_at < 0:
        raise RuntimeError("could not find companies array close")
    bodies = []
    for rec in to_add:
        body = json.dumps(rec, ensure_ascii=False, indent=2)
        body = "\n".join(("    " + line if line else line) for line in body.splitlines())
        bodies.append(body)
    block = ",\n".join(bodies)
    text = text[:insert_at] + ",\n" + block + text[insert_at:]
    if '"claims_merged"' not in text:
        text = text.replace(
            '"count":',
            f'"claims_merged": "{datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}",\n  "count":',
            1,
        )
    write_text_atomic(path, text)
    return len(to_add)


def classify_all(root: Path, use_extracts: bool = True, cik_index: dict[str, str] | None = None) -> list[dict]:
    claims = load_json(root / "claims" / "companies.json")
    insider = load_json(root / "insider-companies.json")
    by_slug, by_norm, by_ticker = index_insider(insider.get("companies") or [])
    beta = beta_ticker_index(root)
    link_by_id = {rec["id"]: rec for rec in load_link_companies(root)}
    for cid, link in link_by_id.items():
        slot = beta.setdefault(cid, {})
        if link["tickers"] and not slot.get("tickers"):
            slot["tickers"] = list(link["tickers"])
        if link.get("holder") and not slot.get("name"):
            slot["name"] = link["holder"]
    rows = []
    catalog_ids = set()
    for rec in claims.get("companies") or []:
        catalog_ids.add(rec["id"])
        aliases = extract_holder_aliases(root / "claims", rec["id"], rec) if use_extracts else []
        row = match_claims_company(
            rec,
            insider_by_slug=by_slug,
            insider_by_norm=by_norm,
            insider_by_ticker=by_ticker,
            beta=beta,
            extract_aliases=aliases,
        )
        if row.get("new"):
            attach_identifiers(row, cik_index or {})
        rows.append(row)
    # claims/links publics that are not already a catalog company. Ticker
    # dedup happens inside match_claims_company, so a second listing of an
    # insider ticker is "matched" and is not appended again.
    for rec in load_link_companies(root):
        if rec["id"] in catalog_ids:
            continue
        row = match_claims_company(
            rec,
            insider_by_slug=by_slug,
            insider_by_norm=by_norm,
            insider_by_ticker=by_ticker,
            beta=beta,
            extract_aliases=[],
        )
        row["from_links"] = True
        if not row.get("exchange"):
            row["exchange"] = rec.get("exchange") or exchanges_for(row.get("tickers") or [])
        if row.get("new"):
            attach_identifiers(row, cik_index or {})
        rows.append(row)
    rows.sort(key=lambda r: (r["status"], r["id"]))
    return rows


def check_universe(root: Path, rows: list[dict]) -> list[str]:
    insider = load_json(root / "insider-companies.json")
    by_ticker: dict[str, list[str]] = defaultdict(list)
    names = set()
    for rec in insider.get("companies") or []:
        names.add(norm_name(rec.get("name") or ""))
        for t in extract_symbols(rec):
            by_ticker[t].append(rec.get("name") or "")
    errors = []
    for row in rows:
        if row["status"] != "matched":
            continue
        tks = row.get("tickers") or []
        if not tks:
            errors.append(f"{row['id']}: matched without tickers")
            continue
        if not any(t in by_ticker for t in tks) and norm_name(row.get("name") or "") not in names:
            errors.append(f"{row['id']}: matched public missing from insider-companies.json ({' '.join(tks)})")
    for rec in insider.get("companies") or []:
        if rec.get("source") != "claims" or not rec.get("exchange"):
            continue
        cid = rec.get("claims_id") or rec.get("name")
        if rec.get("cad") and not rec.get("sedi_name"):
            errors.append(f"{cid}: Canadian claims public is missing sedi_name")
        if rec.get("cik") and not str(rec.get("cik")).isdigit():
            errors.append(f"{cid}: cik is not digits")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Merge claims publics into insider-companies.json")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true", help="Verify matched publics are on the universe; do not write JSON")
    parser.add_argument("--no-extracts", action="store_true", help="Skip extract holder aliases (faster tests)")
    parser.add_argument("--no-write-json", action="store_true", help="Write audit only")
    args = parser.parse_args(argv)
    root = args.root
    write = not args.check and not args.no_write_json
    pin_changes, pin_problems = sync_catalog_pins(root, write=write)
    for change in pin_changes:
        print(change)
    cik_index = load_sec_ciks(fetch=not args.check)
    rows = classify_all(root, use_extracts=not args.no_extracts, cik_index=cik_index)
    new_rows = [r for r in rows if r.get("new")]
    audit_csv = root / "scripts" / "claims-insider-ticker-audit.csv"
    audit_md = root / "scripts" / "claims-insider-ticker-audit.md"
    if not args.check and not write:
        write_audit(rows, audit_csv, audit_md)
    counts = defaultdict(int)
    for r in rows:
        counts[r["status"]] += 1
    print(
        json.dumps(
            {
                "claims": len(rows),
                "matched": counts["matched"],
                "missing-ticker": counts["missing-ticker"],
                "ambiguous": counts["ambiguous"],
                "private-excluded": counts["private-excluded"],
                "new_publics": [r["id"] for r in new_rows],
            },
            indent=2,
        )
    )
    if write:
        added = append_companies(root / "insider-companies.json", new_rows)
        print(f"appended {added} insider-companies")
        rows = classify_all(root, use_extracts=not args.no_extracts, cik_index=cik_index)
        write_audit(rows, audit_csv, audit_md)
    errors = pin_problems + check_universe(root, rows)
    if errors:
        print("check failed:", file=sys.stderr)
        for e in errors:
            print(" ", e, file=sys.stderr)
        return 1
    print("universe check ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
