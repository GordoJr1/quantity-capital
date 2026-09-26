#!/usr/bin/env python3
"""Pull USAspending prime contracts into a local sqlite file and a thin JSON.

Desktop collector only. Stdlib. No GitHub Action. The database stays beside
the collector and is not committed. Pages reads contracts.json.

Survey counts (do not re-download to recount): last 90 days at $1M is 5,077
awards; a trailing year is 29,208 rows. This job takes a short recent window.

    python scripts/ingest_federal_contracts.py --self-check
    python scripts/ingest_federal_contracts.py --days 14 --max-pages 2 --limit 40
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
GROKS = Path(r"C:\Users\gordo\Desktop\Groks folder")
COLLECT = GROKS / "collect"
DEFAULT_SQLITE = COLLECT / "contracts.sqlite"
DEFAULT_JSON = ROOT / "contracts.json"
GROKS_JSON = GROKS / "contracts.json"
STAMP = COLLECT / "raw" / "contracts-last.txt"
TYPESAFE_ENV = Path.home() / ".grok" / "typesafe.env"
API = "https://api.usaspending.gov/api/v2/search/spending_by_award/"
JEV_URL = "https://api.typesafe.ai/v1/systemone"
USER_AGENT = "quantity-capital-contracts/0.1"
MATCHED_FLOOR = 1_000_000
UNLINKED_FLOOR = 10_000_000
AWARD_TYPES = ("A", "B", "C", "D")
TRAILING_DAYS = 365
DESC_MAX = 160
RECENT_CAP = 150
UNLINKED_CAP = 80
JEV_CAP = 12

FIELDS = [
    "Award ID",
    "Recipient Name",
    "Award Amount",
    "Description",
    "Awarding Agency",
    "Award Type",
    "Base Obligation Date",
    "Start Date",
    "generated_internal_id",
    "naics_code",
]

LEGAL_RE = re.compile(
    r"\b(incorporated|inc|llc|l l c|corp|corporation|company|co|ltd|limited|"
    r"plc|lp|llp|holdings|holding|na|n a|the)\b"
)
WS_RE = re.compile(r"\s+")
GENERIC = {
    "general", "national", "american", "united", "first", "group", "systems",
    "services", "federal", "international", "technologies", "technology",
    "solutions", "energy", "health", "medical", "global", "advanced",
    "defense", "public", "capital", "financial", "partners", "industries",
    "north", "south", "west", "east", "pacific", "new", "air", "data",
    "science", "engineering", "management", "construction", "security",
    "logistics", "support", "technical", "consulting", "research",
    "products", "industrial", "motors", "electric", "power", "holdings",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm_name(value: str) -> str:
    text = (value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    text = LEGAL_RE.sub(" ", text)
    return WS_RE.sub(" ", text).strip()


def generic_only(name: str) -> bool:
    parts = name.split()
    return bool(parts) and all(part in GENERIC or len(part) < 4 for part in parts)


def short_agency(name: str) -> str:
    text = (name or "").strip()
    for prefix in ("Department of the ", "Department of "):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def clip_desc(value: str) -> str:
    text = WS_RE.sub(" ", (value or "").replace("\n", " ")).strip()
    if len(text) <= DESC_MAX:
        return text
    return text[: DESC_MAX - 1].rstrip() + "…"


def clean_display(name: str) -> str:
    return re.sub(r"\s+-\s*$", "", (name or "").strip())


def keep_award(amount: float | None, ticker: str | None) -> bool:
    if amount is None or amount < MATCHED_FLOOR:
        return False
    if ticker:
        return True
    return amount >= UNLINKED_FLOOR


class Catalog:
    def __init__(self) -> None:
        self.names: dict[str, dict[str, Any]] = {}
        self.tape: set[str] = set()

    def add(self, raw_name: str, symbol: str, display: str | None = None) -> None:
        key = norm_name(raw_name)
        symbol = (symbol or "").strip()
        if not key or not symbol or len(key) < 3:
            return
        row = self.names.setdefault(key, {"display": clean_display(display or raw_name), "symbols": set()})
        row["symbols"].add(symbol)
        if display and len(clean_display(display)) > len(row["display"]):
            row["display"] = clean_display(display)

    def prefer(self, symbols: set[str]) -> str | None:
        ordered = sorted(symbols)
        tape_plain = [s for s in ordered if s in self.tape and "." not in s and "-" not in s]
        if len(tape_plain) == 1:
            return tape_plain[0]
        plain = [s for s in ordered if "." not in s and "-" not in s]
        if len(plain) == 1:
            return plain[0]
        if len(ordered) == 1:
            return ordered[0]
        return None

    def candidate_rows(self, keys: list[str]) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for key in keys:
            row = self.names.get(key)
            if not row:
                continue
            for symbol in sorted(row["symbols"]):
                if symbol in seen:
                    continue
                seen.add(symbol)
                out.append({"ticker": symbol, "company": row["display"]})
        return out


def load_catalog(root: Path) -> Catalog:
    cat = Catalog()
    tickers_path = root / "tickers.json"
    if tickers_path.exists():
        payload = json.loads(tickers_path.read_text(encoding="utf-8"))
        book = payload.get("tickers") or {}
        cat.tape = set(book)
        for symbol, row in book.items():
            if not isinstance(row, dict):
                continue
            cat.add(str(row.get("name") or ""), str(symbol), str(row.get("name") or ""))
    insider_path = root / "insider-companies.json"
    if insider_path.exists():
        payload = json.loads(insider_path.read_text(encoding="utf-8"))
        for company in payload.get("companies") or []:
            if not isinstance(company, dict):
                continue
            name = str(company.get("name") or "")
            us = [str(s) for s in (company.get("us") or []) if s]
            tape_us = [s for s in us if s in cat.tape]
            symbol = (tape_us or us or [str(s) for s in (company.get("all") or []) if s] or [None])[0]
            if symbol:
                cat.add(name, symbol, name)
    return cat


def specific_hits(recipient_norm: str, cat: Catalog) -> list[str]:
    found: list[str] = []
    for name in cat.names:
        if name == recipient_norm or len(name) < 4 or generic_only(name):
            continue
        if recipient_norm.startswith(name + " "):
            found.append(name)
            continue
        if len(name) >= 6 and f" {name} " in f" {recipient_norm} ":
            found.append(name)
    specific: list[str] = []
    for name in found:
        if any(other.startswith(name + " ") for other in found if other != name):
            continue
        specific.append(name)
    return specific


def classify(recipient: str, cat: Catalog) -> dict[str, Any]:
    """Return ticker/how, or ambiguous candidate rows. No network."""
    key = norm_name(recipient)
    if not key:
        return {"ticker": None, "how": "blank", "candidates": []}
    if key in cat.names:
        row = cat.names[key]
        symbol = cat.prefer(row["symbols"])
        if symbol:
            return {"ticker": symbol, "how": "exact", "company": row["display"], "candidates": []}
        return {
            "ticker": None,
            "how": "ambiguous",
            "candidates": cat.candidate_rows([key]),
        }
    hits = specific_hits(key, cat)
    if len(hits) == 1:
        row = cat.names[hits[0]]
        symbol = cat.prefer(row["symbols"])
        if symbol:
            return {"ticker": symbol, "how": "parent", "company": row["display"], "candidates": []}
        return {
            "ticker": None,
            "how": "ambiguous",
            "candidates": cat.candidate_rows(hits),
        }
    if len(hits) > 1:
        return {"ticker": None, "how": "ambiguous", "candidates": cat.candidate_rows(hits)}
    return {"ticker": None, "how": "none", "candidates": []}


def self_check() -> None:
    cat = Catalog()
    cat.tape = {"LMT", "BA", "GD", "GE"}
    cat.add("Lockheed Martin Corp", "LMT", "Lockheed Martin")
    cat.add("Boeing Co", "BA", "Boeing")
    cat.add("General Dynamics Corp", "GD", "General Dynamics")
    cat.add("General Electric Co", "GE", "General Electric")
    cases = [
        ("LOCKHEED MARTIN AERONAUTICS COMPANY", "LMT", "parent"),
        ("LOCKHEED MARTIN CORPORATION", "LMT", "exact"),
        ("THE BOEING COMPANY", "BA", "exact"),
        ("GENERAL DYNAMICS MISSION SYSTEMS, INC.", "GD", "parent"),
        ("GENERAL ELECTRIC COMPANY", "GE", "exact"),
        ("GENERAL WIDGETS LLC", None, "none"),
        ("TRIWEST HEALTHCARE ALLIANCE CORP", None, "none"),
    ]
    for recipient, ticker, how in cases:
        got = classify(recipient, cat)
        if got.get("ticker") != ticker or (ticker and got.get("how") != how):
            raise SystemExit(f"self-check match failed for {recipient}: {got}")
        if ticker is None and got.get("how") not in ("none", "blank"):
            raise SystemExit(f"self-check expected no match for {recipient}: {got}")
    filters = [
        (500_000, "BA", False),
        (999_999, None, False),
        (1_000_000, None, False),
        (2_000_000, None, False),
        (2_000_000, "BA", True),
        (10_000_000, None, True),
        (12_000_000, None, True),
        (10_000_000, "LMT", True),
    ]
    for amount, ticker, expect in filters:
        if keep_award(amount, ticker) is not expect:
            raise SystemExit(f"self-check filter failed amount={amount} ticker={ticker}")
    print("self-check ok", flush=True)


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS awards (
          award_key TEXT PRIMARY KEY,
          award_id TEXT,
          recipient TEXT,
          amount REAL,
          agency TEXT,
          description TEXT,
          naics TEXT,
          obligation_date TEXT,
          ticker TEXT,
          company TEXT,
          match_how TEXT,
          kept INTEGER,
          fetched_at TEXT
        );
        CREATE TABLE IF NOT EXISTS jev_match (
          cache_key TEXT PRIMARY KEY,
          choice TEXT,
          confidence REAL,
          asked_at TEXT
        );
        """
    )
    return conn


def load_typesafe_key() -> str:
    if os.environ.get("QC_SKIP_JEV") == "1":
        return ""
    if os.environ.get("TYPESAFE_API_KEY"):
        return os.environ["TYPESAFE_API_KEY"]
    if not TYPESAFE_ENV.exists():
        return ""
    for line in TYPESAFE_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return os.environ.get("TYPESAFE_API_KEY") or ""


def jev_choose(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Score ambiguous recipient/candidate sets. Never prints the key."""
    key = load_typesafe_key()
    if not key or not items:
        return {}
    questions: dict[str, Any] = {}
    state_rows = []
    for i, item in enumerate(items):
        cands = item["candidates"][:8]
        state_rows.append({"name": item["recipient"], "candidates": cands})
        criteria = {
            "none": "None of the candidates is the same company or an obvious parent. A shared generic word is not enough.",
        }
        for cand in cands:
            ticker = cand["ticker"]
            criteria[ticker] = f"{cand['company']} ({ticker}) is the same company or the obvious parent."
        questions[f"r{i}"] = {
            "type": "choice",
            "instructions": (
                f"Which listed company, if any, is the same firm or an obvious parent of "
                f"`rows[{i}].name`? Use only `rows[{i}].candidates`. "
                "Choose none when the overlap is a generic word, a different company, or unclear."
            ),
            "criteria": criteria,
        }
    body = {
        "model": "jev-latest",
        "state": {"rows": state_rows},
        "questions": questions,
    }
    req = urllib.request.Request(
        JEV_URL,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        print(f"jev http {exc.code}", flush=True)
        return {}
    except urllib.error.URLError as exc:
        print(f"jev unreachable {exc.reason}", flush=True)
        return {}
    answers = data.get("answers") or {}
    out: dict[str, dict[str, Any]] = {}
    print(f"jev model {data.get('model')}", flush=True)
    for i, item in enumerate(items):
        ans = answers.get(f"r{i}") or {}
        choice = ans.get("choice")
        probs = ans.get("probabilities") or {}
        allowed = {c["ticker"] for c in item["candidates"]}
        if choice not in allowed:
            choice = "none"
        prob = float(probs.get(choice) or 0)
        if choice != "none" and prob < 0.55:
            choice = "none"
        out[item["cache_key"]] = {
            "choice": choice,
            "confidence": ans.get("confidence"),
            "prob": prob,
        }
    return out


def apply_jev(conn: sqlite3.Connection, pending: list[dict[str, Any]]) -> None:
    if not pending:
        print("jev: no ambiguous names", flush=True)
        return
    fresh: list[dict[str, Any]] = []
    for item in pending:
        cached = conn.execute(
            "SELECT choice FROM jev_match WHERE cache_key = ?", (item["cache_key"],)
        ).fetchone()
        if cached:
            continue
        fresh.append(item)
    fresh = fresh[:JEV_CAP]
    decided = jev_choose(fresh)
    now = utc_now()
    for cache_key, row in decided.items():
        conn.execute(
            "INSERT OR REPLACE INTO jev_match (cache_key, choice, confidence, asked_at) VALUES (?, ?, ?, ?)",
            (cache_key, row["choice"], row.get("confidence"), now),
        )
    conn.commit()
    print(f"jev: cached {len(decided)} new, pending {len(pending)}", flush=True)


def lookup_jev(conn: sqlite3.Connection, cache_key: str, candidates: list[dict[str, str]]) -> str | None:
    row = conn.execute("SELECT choice FROM jev_match WHERE cache_key = ?", (cache_key,)).fetchone()
    if not row:
        return None
    choice = row["choice"]
    allowed = {c["ticker"] for c in candidates}
    if choice in allowed:
        return choice
    return None


def company_for(cat: Catalog, ticker: str) -> str:
    for row in cat.names.values():
        if ticker in row["symbols"]:
            return row["display"]
    return ticker


def cache_key_for(recipient: str, candidates: list[dict[str, str]]) -> str:
    tickers = ",".join(sorted({c["ticker"] for c in candidates}))
    return norm_name(recipient) + "|" + tickers


def resolve_row(recipient: str, cat: Catalog, conn: sqlite3.Connection) -> tuple[str | None, str, str]:
    got = classify(recipient, cat)
    if got.get("ticker"):
        return got["ticker"], got.get("company") or company_for(cat, got["ticker"]), got["how"]
    candidates = got.get("candidates") or []
    if got.get("how") == "ambiguous" and candidates:
        key = cache_key_for(recipient, candidates)
        chosen = lookup_jev(conn, key, candidates)
        if chosen:
            return chosen, company_for(cat, chosen), "jev"
        return None, "", "ambiguous"
    return None, "", got.get("how") or "none"


def rematch(conn: sqlite3.Connection, cat: Catalog) -> None:
    rows = conn.execute("SELECT award_key, recipient, amount FROM awards").fetchall()
    pending = []
    seen: set[str] = set()
    for row in rows:
        got = classify(row["recipient"], cat)
        if got.get("how") == "ambiguous" and got.get("candidates"):
            key = cache_key_for(row["recipient"], got["candidates"])
            if key not in seen:
                seen.add(key)
                pending.append({
                    "cache_key": key,
                    "recipient": row["recipient"],
                    "candidates": got["candidates"],
                })
    apply_jev(conn, pending)
    for row in rows:
        ticker, company, how = resolve_row(row["recipient"], cat, conn)
        kept = 1 if keep_award(row["amount"], ticker) else 0
        conn.execute(
            "UPDATE awards SET ticker=?, company=?, match_how=?, kept=? WHERE award_key=?",
            (ticker, company, how, kept, row["award_key"]),
        )
    conn.commit()


def stale(hours: float) -> bool:
    if hours <= 0 or not STAMP.exists():
        return False
    try:
        then = datetime.fromisoformat(STAMP.read_text(encoding="utf-8").strip())
    except ValueError:
        return False
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - then < timedelta(hours=hours)


def mark_stamp() -> None:
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(datetime.now(timezone.utc).isoformat(), encoding="utf-8")


def fetch_pages(start: str, end: str, limit: int, max_pages: int, sleep_s: float) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    sort = "Base Obligation Date"
    for page in range(1, max_pages + 1):
        filters: dict[str, Any] = {
            "time_period": [{"start_date": start, "end_date": end, "date_type": "date_signed"}],
            "award_type_codes": list(AWARD_TYPES),
            "award_amounts": [{"lower_bound": MATCHED_FLOOR}],
        }
        body: dict[str, Any] = {
            "filters": filters,
            "fields": FIELDS,
            "limit": limit,
            "page": page,
            "sort": sort,
            "order": "desc",
            "subawards": False,
        }
        payload, status = _post(body)
        if status == 400 and sort != "Award Amount":
            sort = "Award Amount"
            body["sort"] = sort
            payload, status = _post(body)
        if status in (429, 503):
            print(f"usaspending {status}; retry once", flush=True)
            time.sleep(max(sleep_s, 2))
            payload, status = _post(body)
        if status == 429:
            print("usaspending 429; stopping", flush=True)
            break
        if status != 200 or payload is None:
            print(f"usaspending http {status}; stopping", flush=True)
            break
        batch = payload.get("results") or []
        added = 0
        for row in batch:
            key = str(row.get("generated_internal_id") or row.get("Award ID") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            rows.append(row)
            added += 1
        meta = payload.get("page_metadata") or {}
        print(f"page {page} rows {len(batch)} new {added} sort {sort}", flush=True)
        if not meta.get("hasNext") or added == 0:
            break
        if page < max_pages:
            time.sleep(sleep_s)
    return rows


def _post(body: dict[str, Any]) -> tuple[dict[str, Any] | None, int]:
    req = urllib.request.Request(
        API,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode()), resp.status
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        print(f"usaspending error {exc.code} {detail}", flush=True)
        return None, exc.code


def amount_of(row: dict[str, Any]) -> float | None:
    raw = row.get("Award Amount")
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def date_of(row: dict[str, Any]) -> str:
    for key in ("Base Obligation Date", "Start Date"):
        value = str(row.get(key) or "")[:10]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
            return value
    return ""


def store_rows(conn: sqlite3.Connection, rows: list[dict[str, Any]], cat: Catalog) -> None:
    pending = []
    seen: set[str] = set()
    for row in rows:
        recipient = str(row.get("Recipient Name") or "")
        got = classify(recipient, cat)
        if got.get("how") == "ambiguous" and got.get("candidates"):
            key = cache_key_for(recipient, got["candidates"])
            if key not in seen:
                seen.add(key)
                pending.append({
                    "cache_key": key,
                    "recipient": recipient,
                    "candidates": got["candidates"],
                })
    apply_jev(conn, pending)
    now = utc_now()
    for row in rows:
        recipient = str(row.get("Recipient Name") or "")
        amount = amount_of(row)
        award_key = str(row.get("generated_internal_id") or row.get("Award ID") or "")
        if not award_key:
            continue
        ticker, company, how = resolve_row(recipient, cat, conn)
        kept = 1 if keep_award(amount, ticker) else 0
        naics = row.get("naics_code")
        conn.execute(
            """
            INSERT INTO awards (
              award_key, award_id, recipient, amount, agency, description, naics,
              obligation_date, ticker, company, match_how, kept, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(award_key) DO UPDATE SET
              award_id=excluded.award_id,
              recipient=excluded.recipient,
              amount=excluded.amount,
              agency=excluded.agency,
              description=excluded.description,
              naics=excluded.naics,
              obligation_date=excluded.obligation_date,
              ticker=excluded.ticker,
              company=excluded.company,
              match_how=excluded.match_how,
              kept=excluded.kept,
              fetched_at=excluded.fetched_at
            """,
            (
                award_key,
                str(row.get("Award ID") or ""),
                recipient,
                amount,
                short_agency(str(row.get("Awarding Agency") or "")),
                clip_desc(str(row.get("Description") or "")),
                "" if naics is None else str(naics),
                date_of(row),
                ticker,
                company,
                how,
                kept,
                now,
            ),
        )
    conn.commit()


def export_json(conn: sqlite3.Connection, dests: list[Path], start: str, end: str) -> dict[str, Any]:
    end_dt = datetime.strptime(end, "%Y-%m-%d")
    trail_from = (end_dt - timedelta(days=TRAILING_DAYS - 1)).strftime("%Y-%m-%d")
    kept = conn.execute(
        """
        SELECT award_id, obligation_date, agency, recipient, ticker, company,
               amount, description, naics, match_how
        FROM awards
        WHERE kept = 1 AND obligation_date >= ? AND obligation_date <= ?
        ORDER BY obligation_date DESC, amount DESC
        """,
        (trail_from, end),
    ).fetchall()
    awards = []
    unlinked = []
    for row in kept:
        item = {
            "date": row["obligation_date"],
            "agency": row["agency"],
            "recipient": row["recipient"],
            "ticker": row["ticker"] or None,
            "amount": row["amount"],
            "description": row["description"],
        }
        if row["naics"]:
            item["naics"] = row["naics"]
        if row["award_id"]:
            item["award_id"] = row["award_id"]
        if row["ticker"] and len(awards) < RECENT_CAP:
            awards.append(item)
        elif not row["ticker"] and len(unlinked) < UNLINKED_CAP:
            unlinked.append({k: item[k] for k in ("date", "agency", "recipient", "amount", "description")})
    companies_rows = conn.execute(
        """
        SELECT ticker, company, COUNT(*) AS n, SUM(amount) AS total
        FROM awards
        WHERE kept = 1 AND ticker IS NOT NULL AND ticker != ''
          AND obligation_date >= ? AND obligation_date <= ?
        GROUP BY ticker
        ORDER BY total DESC
        """,
        (trail_from, end),
    ).fetchall()
    companies = [
        {
            "ticker": row["ticker"],
            "name": row["company"] or row["ticker"],
            "awards": row["n"],
            "trailing_total": row["total"],
        }
        for row in companies_rows
    ]
    matched = sum(1 for row in kept if row["ticker"])
    payload = {
        "generated": utc_now(),
        "source": "USAspending API v2 spending_by_award",
        "date_type": "date_signed",
        "award_types": list(AWARD_TYPES),
        "floors": {"matched": MATCHED_FLOOR, "unlinked": UNLINKED_FLOOR},
        "window": {"start": start, "end": end},
        "trailing_days": TRAILING_DAYS,
        "counts": {
            "kept": len(kept),
            "matched": matched,
            "unlinked": len(kept) - matched,
        },
        "awards": awards,
        "companies": companies,
        "unlinked": unlinked,
    }
    text = json.dumps(payload, indent=2)
    for dest in dests:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(dest)
        print(f"wrote {dest}", flush=True)
    return payload


def count_report(conn: sqlite3.Connection) -> dict[str, int]:
    def n(sql: str) -> int:
        return int(conn.execute(sql).fetchone()[0])

    return {
        "sqlite_rows": n("SELECT COUNT(*) FROM awards"),
        "kept": n("SELECT COUNT(*) FROM awards WHERE kept = 1"),
        "matched": n("SELECT COUNT(*) FROM awards WHERE kept = 1 AND ticker IS NOT NULL AND ticker != ''"),
        "unlinked": n("SELECT COUNT(*) FROM awards WHERE kept = 1 AND (ticker IS NULL OR ticker = '')"),
        "dropped_unmatched_under_10m": n(
            "SELECT COUNT(*) FROM awards WHERE kept = 0 AND amount >= 1000000 AND amount < 10000000"
        ),
        "dropped_under_1m": n("SELECT COUNT(*) FROM awards WHERE amount < 1000000"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingest USAspending contracts for Quantity Capital.")
    parser.add_argument("--sqlite", type=Path, default=DEFAULT_SQLITE)
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON)
    parser.add_argument("--days", type=int, default=14)
    parser.add_argument("--start", default="")
    parser.add_argument("--end", default="")
    parser.add_argument("--limit", type=int, default=40)
    parser.add_argument("--max-pages", type=int, default=2)
    parser.add_argument("--sleep", type=float, default=1.2)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--rematch", action="store_true")
    parser.add_argument("--no-jev", action="store_true")
    parser.add_argument("--if-stale-hours", type=float, default=0)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    self_check()
    if args.self_check:
        return
    if args.no_jev:
        os.environ["QC_SKIP_JEV"] = "1"
    if not args.force and not args.skip_fetch and not args.rematch and stale(args.if_stale_hours):
        print(f"contracts fresh within {args.if_stale_hours}h; skip", flush=True)
        return
    end = args.end or datetime.now().strftime("%Y-%m-%d")
    start = args.start or (datetime.now() - timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
    cat = load_catalog(ROOT)
    print(f"catalog names {len(cat.names)} tape {len(cat.tape)}", flush=True)
    conn = connect(args.sqlite)
    try:
        if not args.skip_fetch:
            rows = fetch_pages(start, end, args.limit, args.max_pages, args.sleep)
            print(f"fetched {len(rows)}", flush=True)
            store_rows(conn, rows, cat)
            mark_stamp()
        elif args.rematch:
            rematch(conn, cat)
        payload = export_json(conn, [args.json, GROKS_JSON], start, end)
        report = count_report(conn)
        report["json_awards"] = len(payload["awards"])
        report["json_companies"] = len(payload["companies"])
        report["json_unlinked"] = len(payload["unlinked"])
        print("COUNTS " + json.dumps(report), flush=True)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
