"""Congress filing notes: code-computed chips plus a separate Jev judgment cache.

`run()` is the daily path (called from rebuild.run_boards). It never calls Jev
or any network. It writes QC_ROOT/filing-notes.json from politician_trades and
the on-disk judgment cache.

`judge` is the only path that calls Jev. One system_one call per filing, five
questions batched, through jev_common.ask. Judgments live outside the git
worktree so a throwaway publish worktree can still read them.

Congress filings only (House and Senate). Insider Form 4 rows are context for
the "same week" chip, never judged here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import statistics
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import jev_common
from paths import BIOS, DB_PATH, QC_ROOT, TYPESAFE_ENV

ET = ZoneInfo("America/New_York")
QUESTION_VERSION = "filing-notes-v1"
JEV_MODEL = jev_common.JEV_MODEL
# Committee chip only at high confidence: P(level 3 direct jurisdiction).
COMMITTEE_P3_MIN = 0.85
NOUL_MIN = 0.80
ASSET_MISMATCH_MAX = 0.20
LATE_DAYS = 45
CLUSTER_DAYS = 7
USUAL_MIN = 2.0
CONGRESS = frozenset({"house", "senate"})
NOTES_NAME = "filing-notes.json"

COMMITTEE_LEVELS = [
    "0 no plausible link between the member's committees and the company's business",
    "1 tangential",
    "2 related industry under a committee's broad area",
    "3 direct jurisdiction: a committee the member sits on directly regulates, funds, or oversees this company's core business",
]

_COMMITTEE_PREFIXES = (
    "senate committee on the ",
    "house committee on the ",
    "senate committee on ",
    "house committee on ",
    "committee on the ",
    "committee on ",
)

_CACHE_SQL = """
CREATE TABLE IF NOT EXISTS judgments (
  cache_key TEXT PRIMARY KEY,
  trade_id TEXT NOT NULL,
  fields_sha TEXT NOT NULL,
  question_version TEXT NOT NULL,
  model TEXT NOT NULL,
  committee_score REAL,
  committee_confidence REAL,
  committee_p0 REAL,
  committee_p1 REAL,
  committee_p2 REAL,
  committee_p3 REAL,
  asset_matches_company REAL,
  is_broad_fund REAL,
  is_derivative REAL,
  home_state_industry REAL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_judgments_trade ON judgments(trade_id);
"""

_TRANSIENT_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504})
_TRANSIENT_TYPES = frozenset({
    "TypeSafeRateLimitError",
    "TypeSafeInternalServerError",
    "TypeSafeAPIConnectionError",
    "TypeSafeAPITimeoutError",
    "TimeoutError",
    "ConnectionError",
})


def judgments_path() -> Path:
    """Persistent cache. Env override, else Groks .cache when that folder exists."""
    override = os.environ.get("QC_FILING_JUDGMENTS")
    if override:
        return Path(override)
    groks = Path.home() / "Desktop" / "Groks folder"
    if groks.is_dir():
        return groks / ".cache" / "filing_judgments.sqlite"
    return HERE / ".cache" / "filing_judgments.sqlite"


def load_typesafe_env() -> None:
    """Same file as jev.py. Never prints or logs the key."""
    if os.environ.get("TYPESAFE_API_KEY"):
        return
    if not TYPESAFE_ENV.exists():
        return
    for line in TYPESAFE_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def lag_days(trade_date: str | None, filed_date: str | None) -> int | None:
    traded, filed = parse_day(trade_date), parse_day(filed_date)
    if traded is None or filed is None:
        return None
    return (filed - traded).days


def short_committee(name: str) -> str:
    text = " ".join(str(name or "").split())
    lowered = text.casefold()
    for prefix in _COMMITTEE_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    for dash in (" — ", " – ", " - "):
        if dash in text:
            text = text.split(dash, 1)[0].strip()
    return text


def committee_names(person: dict | None) -> list[str]:
    if not isinstance(person, dict):
        return []
    found: list[str] = []
    seen: set[str] = set()
    for key in ("committees", "subcommittees"):
        for item in person.get(key) or []:
            if isinstance(item, str):
                name = item.strip()
            elif isinstance(item, dict):
                name = str(item.get("name") or "").strip()
            else:
                name = ""
            if name and name not in seen:
                seen.add(name)
                found.append(name)
    found.sort()
    return found


def committee_label(committees: Iterable[str]) -> str:
    shorts: list[str] = []
    for name in committees:
        short = short_committee(name)
        if short and short not in shorts:
            shorts.append(short)
    if len(shorts) == 1:
        return f"Committee link: {shorts[0]}"
    if len(shorts) >= 2:
        joined = ", ".join(shorts[:2])
        text = f"Committee link: {joined}"
        if len(text) <= 48:
            return text
        return f"Committee link: {shorts[0]}"
    return "Committee link"


def usual_label(ratio: float | None) -> str | None:
    if ratio is None or ratio < USUAL_MIN:
        return None
    rounded = round(ratio)
    if abs(ratio - rounded) <= 0.05:
        return f"{int(rounded)}x usual size"
    return f"{ratio:.1f}x usual size"


def cluster_label(own: int, others: int) -> str | None:
    bits: list[str] = []
    if own >= 2:
        bits.append(f"{own} trades ±7d")
    if others >= 2:
        bits.append(f"{others} other members ±7d")
    if not bits:
        return None
    return "Cluster: " + ", ".join(bits)


def code_chips(fact: dict[str, Any]) -> list[str]:
    """Templated chip text. Jev never writes these strings."""
    chips: list[str] = []
    lag = fact.get("lag")
    if isinstance(lag, int) and lag > LATE_DAYS:
        chips.append(f"Filed {lag}d late")
    ticker = str(fact.get("ticker") or "").strip()
    if fact.get("first") and ticker:
        chips.append(f"First {ticker} trade")
    largest = fact.get("largest")
    if largest == "purchase":
        chips.append("Largest buy on record")
    elif largest == "sale":
        chips.append("Largest sale on record")
    usual = usual_label(fact.get("ratio"))
    if usual:
        chips.append(usual)
    cluster = cluster_label(int(fact.get("own") or 0), int(fact.get("others") or 0))
    if cluster:
        chips.append(cluster)
    if fact.get("insiders"):
        chips.append("Insiders bought same week")
    return chips


def _noul_yes(value: Any) -> bool:
    if value is None:
        return False
    try:
        return float(value) >= NOUL_MIN
    except (TypeError, ValueError):
        return False


def jev_chips(judgment: dict[str, Any] | None, committees: Iterable[str]) -> list[str]:
    if not judgment:
        return []
    chips: list[str] = []
    try:
        p3 = float(judgment["committee_p3"]) if judgment.get("committee_p3") is not None else None
    except (TypeError, ValueError):
        p3 = None
    if p3 is not None and p3 >= COMMITTEE_P3_MIN:
        chips.append(committee_label(committees))
    if _noul_yes(judgment.get("is_broad_fund")):
        chips.append("Broad fund")
    if _noul_yes(judgment.get("is_derivative")):
        chips.append("Option/derivative")
    if _noul_yes(judgment.get("home_state_industry")):
        chips.append("Home-state company")
    try:
        asset = float(judgment["asset_matches_company"]) if judgment.get("asset_matches_company") is not None else None
    except (TypeError, ValueError):
        asset = None
    if asset is not None and asset <= ASSET_MISMATCH_MAX:
        chips.append("Asset may not match ticker")
    return chips


def canonical_state(fields: dict[str, Any]) -> dict[str, Any]:
    """Fields sent to Jev. No dates, lags, or numbers to compute."""
    committees = fields.get("committees") or []
    if not isinstance(committees, list):
        committees = []
    return {
        "member": str(fields.get("member") or ""),
        "chamber": str(fields.get("chamber") or ""),
        "state": str(fields.get("state") or ""),
        "party": str(fields.get("party") or ""),
        "committees": [str(c) for c in committees],
        "ticker": str(fields.get("ticker") or ""),
        "company": str(fields.get("company") or ""),
        "sector": str(fields.get("sector") or ""),
        "industry": str(fields.get("industry") or ""),
        "asset": str(fields.get("asset") or ""),
        "transaction": str(fields.get("transaction") or ""),
        "amount_range": str(fields.get("amount_range") or ""),
    }


def fields_sha(fields: dict[str, Any]) -> str:
    blob = json.dumps(canonical_state(fields), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_key(
    trade_id: str,
    digest: str,
    *,
    question_version: str = QUESTION_VERSION,
    model: str = JEV_MODEL,
) -> str:
    raw = f"{trade_id}\n{digest}\n{question_version}\n{model}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def load_bios(path: Path | None = None) -> dict[str, Any]:
    bios_path = path if path is not None else BIOS
    if not bios_path.is_file():
        return {}
    try:
        data = json.loads(bios_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    people = data.get("people") if isinstance(data, dict) else None
    return people if isinstance(people, dict) else {}


def _congress_rows(con: sqlite3.Connection) -> list[sqlite3.Row]:
    con.row_factory = sqlite3.Row
    return list(
        con.execute(
            """
            SELECT
              t.trade_id, t.filer, t.filer_id, t.chamber, t.ticker, t.asset,
              t.side, t.amount_raw, t.amount_mid, t.trade_date, t.filed_date,
              p.name AS person_name, p.display AS person_display,
              p.state AS person_state, p.party AS person_party,
              k.name AS company_name, k.industry AS industry
            FROM politician_trades t
            LEFT JOIN people p ON p.filer_id = t.filer_id
            LEFT JOIN tickers k ON k.ticker = t.ticker
            WHERE lower(trim(coalesce(t.chamber, ''))) IN ('house', 'senate')
            """
        )
    )


def _insider_buys(con: sqlite3.Connection) -> dict[str, list[int]]:
    by_ticker: dict[str, list[int]] = defaultdict(list)
    try:
        rows = con.execute(
            """
            SELECT ticker, trade_date FROM insider_trades
            WHERE lower(coalesce(side, '')) = 'purchase'
              AND ticker IS NOT NULL AND ticker != ''
              AND trade_date IS NOT NULL AND trade_date != ''
            """
        )
    except sqlite3.OperationalError:
        return {}
    for ticker, trade_date in rows:
        ordinal = parse_day(trade_date)
        if ordinal is None or not ticker:
            continue
        by_ticker[str(ticker).upper()].append(ordinal.toordinal())
    for ticker in by_ticker:
        by_ticker[ticker].sort()
    return by_ticker


def _member(row: sqlite3.Row, person: dict | None) -> str:
    for value in (
        (person or {}).get("display") if isinstance(person, dict) else None,
        (person or {}).get("name") if isinstance(person, dict) else None,
        row["person_display"],
        row["person_name"],
        row["filer"],
    ):
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _state_of(row: sqlite3.Row, person: dict | None) -> str:
    if isinstance(person, dict) and str(person.get("state") or "").strip():
        return str(person.get("state") or "").strip()
    return str(row["person_state"] or "").strip()


def _party_of(row: sqlite3.Row, person: dict | None) -> str:
    if isinstance(person, dict) and str(person.get("party") or "").strip():
        return str(person.get("party") or "").strip()
    return str(row["person_party"] or "").strip()


def build_filings(con: sqlite3.Connection, bios: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    people = bios if bios is not None else load_bios()
    rows = _congress_rows(con)
    filings: list[dict[str, Any]] = []
    for row in rows:
        trade_id = str(row["trade_id"] or "").strip()
        if not trade_id:
            continue
        person = people.get(row["filer_id"]) if row["filer_id"] else None
        ticker = str(row["ticker"] or "").strip().upper()
        fields = canonical_state(
            {
                "member": _member(row, person if isinstance(person, dict) else None),
                "chamber": str(row["chamber"] or "").strip(),
                "state": _state_of(row, person if isinstance(person, dict) else None),
                "party": _party_of(row, person if isinstance(person, dict) else None),
                "committees": committee_names(person if isinstance(person, dict) else None),
                "ticker": ticker,
                "company": str(row["company_name"] or "").strip(),
                "sector": "",
                "industry": str(row["industry"] or "").strip(),
                "asset": str(row["asset"] or "").strip(),
                "transaction": str(row["side"] or "").strip(),
                "amount_range": str(row["amount_raw"] or "").strip(),
            }
        )
        digest = fields_sha(fields)
        filings.append(
            {
                "trade_id": trade_id,
                "filer_key": str(row["filer_id"] or row["filer"] or "").strip(),
                "ticker": ticker,
                "side": str(row["side"] or "").strip().casefold(),
                "amount_mid": row["amount_mid"],
                "trade_ord": parse_day(row["trade_date"]).toordinal() if parse_day(row["trade_date"]) else None,
                "trade_date": str(row["trade_date"] or ""),
                "filed_date": str(row["filed_date"] or ""),
                "lag": lag_days(row["trade_date"], row["filed_date"]),
                "fields": fields,
                "digest": digest,
                "cache_key": cache_key(trade_id, digest),
            }
        )
    return filings


def _code_facts(filings: list[dict[str, Any]], insider_ords: dict[str, list[int]]) -> dict[str, dict[str, Any]]:
    import bisect

    by_filer_side: dict[tuple[str, str], list[float]] = defaultdict(list)
    by_filer_mids: dict[str, list[float]] = defaultdict(list)
    first: dict[tuple[str, str], str] = {}
    by_ticker: dict[str, list[tuple[int, str, str]]] = defaultdict(list)

    ordered = sorted(
        filings,
        key=lambda item: (item["trade_date"], item["filed_date"], item["trade_id"]),
    )
    for item in ordered:
        mid = item["amount_mid"]
        if mid is not None:
            try:
                mid_f = float(mid)
            except (TypeError, ValueError):
                mid_f = None
            if mid_f is not None:
                by_filer_mids[item["filer_key"]].append(mid_f)
                if item["side"] in ("purchase", "sale"):
                    by_filer_side[(item["filer_key"], item["side"])].append(mid_f)
        if item["ticker"] and item["filer_key"]:
            key = (item["filer_key"], item["ticker"])
            if key not in first:
                first[key] = item["trade_id"]
        if item["ticker"] and item["trade_ord"] is not None and item["filer_key"]:
            by_ticker[item["ticker"]].append((item["trade_ord"], item["filer_key"], item["trade_id"]))

    for ticker in by_ticker:
        by_ticker[ticker].sort()

    medians = {
        filer: statistics.median(mids)
        for filer, mids in by_filer_mids.items()
        if mids
    }
    max_side = {
        key: max(mids)
        for key, mids in by_filer_side.items()
        if mids
    }
    side_counts = {key: len(mids) for key, mids in by_filer_side.items()}

    facts: dict[str, dict[str, Any]] = {}
    for item in filings:
        own = 0
        others = 0
        insiders = False
        ticker = item["ticker"]
        ordinal = item["trade_ord"]
        if ticker and ordinal is not None:
            window = by_ticker.get(ticker) or []
            lo = bisect.bisect_left(window, (ordinal - CLUSTER_DAYS,))
            hi = bisect.bisect_right(window, (ordinal + CLUSTER_DAYS, "\uffff", "\uffff"))
            filers: set[str] = set()
            for _ord, filer_key, _trade_id in window[lo:hi]:
                if filer_key == item["filer_key"]:
                    own += 1
                else:
                    filers.add(filer_key)
            others = len(filers)
            buys = insider_ords.get(ticker) or []
            left = bisect.bisect_left(buys, ordinal - CLUSTER_DAYS)
            right = bisect.bisect_right(buys, ordinal + CLUSTER_DAYS)
            insiders = right > left
        ratio = None
        median = medians.get(item["filer_key"])
        mid = item["amount_mid"]
        if mid is not None and median:
            try:
                mid_f = float(mid)
                med_f = float(median)
                if med_f > 0:
                    ratio = mid_f / med_f
            except (TypeError, ValueError):
                ratio = None
        largest = None
        if item["side"] in ("purchase", "sale") and mid is not None and item["filer_key"]:
            key = (item["filer_key"], item["side"])
            if side_counts.get(key, 0) >= 2:
                try:
                    top = float(max_side[key])
                    mid_f = float(mid)
                    # Unique maximum only. Tied top-band prints are not "largest ever".
                    if abs(mid_f - top) <= 1e-6 and sum(
                        1 for value in by_filer_side[key] if abs(float(value) - top) <= 1e-6
                    ) == 1:
                        largest = item["side"]
                except (TypeError, ValueError):
                    largest = None
        facts[item["trade_id"]] = {
            "lag": item["lag"],
            "ticker": ticker,
            "first": bool(ticker) and first.get((item["filer_key"], ticker)) == item["trade_id"],
            "largest": largest,
            "ratio": ratio,
            "own": own,
            "others": others,
            "insiders": insiders,
        }
    return facts


def _open_cache_rw(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=60)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=60000")
    con.executescript(_CACHE_SQL)
    return con


def _read_judgments(keys: set[str]) -> dict[str, dict[str, Any]]:
    path = judgments_path()
    if not path.is_file() or not keys:
        return {}
    uri = "file:" + path.as_posix() + "?mode=ro"
    try:
        con = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error:
        return {}
    found: dict[str, dict[str, Any]] = {}
    try:
        con.row_factory = sqlite3.Row
        chunk: list[str] = []
        pending = list(keys)
        for i in range(0, len(pending), 400):
            chunk = pending[i:i + 400]
            marks = ",".join("?" for _ in chunk)
            try:
                rows = con.execute(
                    f"""
                    SELECT cache_key, committee_score, committee_confidence,
                           committee_p0, committee_p1, committee_p2, committee_p3,
                           asset_matches_company, is_broad_fund, is_derivative,
                           home_state_industry
                    FROM judgments WHERE cache_key IN ({marks})
                    """,
                    chunk,
                )
            except sqlite3.OperationalError:
                return found
            for row in rows:
                found[row["cache_key"]] = {k: row[k] for k in row.keys() if k != "cache_key"}
    finally:
        con.close()
    return found


def _cached_keys(con: sqlite3.Connection) -> set[str]:
    try:
        return {row[0] for row in con.execute("SELECT cache_key FROM judgments")}
    except sqlite3.OperationalError:
        return set()


def assemble_chips(fact: dict[str, Any], judgment: dict[str, Any] | None, committees: Iterable[str]) -> list[str]:
    return code_chips(fact) + jev_chips(judgment, committees)


def run(
    con: sqlite3.Connection,
    *,
    out_path: Path | None = None,
    bios: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write filing-notes.json. No Jev and no network. Missing cache is code-only chips."""
    filings = build_filings(con, bios)
    insider_ords = _insider_buys(con)
    facts = _code_facts(filings, insider_ords)
    cached = _read_judgments({item["cache_key"] for item in filings})
    notes: dict[str, list[str]] = {}
    judgments_applied = 0
    committee_links = 0
    for item in filings:
        judgment = cached.get(item["cache_key"])
        if judgment:
            judgments_applied += 1
        chips = assemble_chips(facts[item["trade_id"]], judgment, item["fields"]["committees"])
        if not chips:
            continue
        notes[item["trade_id"]] = chips
        if any(chip.startswith("Committee link") for chip in chips):
            committee_links += 1
    ordered = {trade_id: notes[trade_id] for trade_id in sorted(notes)}
    meta = {
        "generated_at": datetime.now(ET).isoformat(timespec="seconds"),
        "question_version": QUESTION_VERSION,
        "model": JEV_MODEL,
        "committee_p3_min": COMMITTEE_P3_MIN,
        "noul_min": NOUL_MIN,
        "asset_mismatch_max": ASSET_MISMATCH_MAX,
        "filings": len(filings),
        "with_chips": len(ordered),
        "judgments_applied": judgments_applied,
        "high_confidence_committee_links": committee_links,
    }
    payload = {"meta": meta, "notes": ordered}
    dest = out_path or (QC_ROOT / NOTES_NAME)
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    tmp.replace(dest)
    return meta


def questions() -> dict[str, Any]:
    from typesafe_sdk import Noul, Score

    return {
        "committee_overlap": Score(
            instructions=(
                "How directly do this member's congressional committees oversee the company's core business? "
                "Use only the committee list, company, sector, and industry given. "
                "Do not infer from dates, lags, or trade size."
            ),
            criteria=COMMITTEE_LEVELS,
        ),
        "asset_matches_company": Noul(
            instructions="The filed asset description is the same issuer as the ticker's company.",
        ),
        "is_broad_fund": Noul(
            instructions="The asset is an ETF, index fund, mutual fund, or broad basket, not a single operating company.",
        ),
        "is_derivative": Noul(
            instructions="The asset is an option, warrant, or other derivative, not plain shares or bonds.",
        ),
        "home_state_industry": Noul(
            instructions="The company has headquarters or major operations in the member's home state.",
        ),
    }


def _is_transient(exc: BaseException) -> bool:
    if type(exc).__name__ in _TRANSIENT_TYPES:
        return True
    status = getattr(exc, "status", None)
    return isinstance(status, int) and status in _TRANSIENT_STATUS


def _pack_answers(response: Any) -> dict[str, Any]:
    answers = response.answers
    committee = answers["committee_overlap"]
    probs = {str(k): float(v) for k, v in dict(committee.probabilities).items()}

    def noul(qid: str) -> float:
        return float(answers[qid].noul)

    return {
        "committee_score": float(committee.score),
        "committee_confidence": float(committee.confidence),
        "committee_p0": probs.get("0"),
        "committee_p1": probs.get("1"),
        "committee_p2": probs.get("2"),
        "committee_p3": probs.get("3"),
        "asset_matches_company": noul("asset_matches_company"),
        "is_broad_fund": noul("is_broad_fund"),
        "is_derivative": noul("is_derivative"),
        "home_state_industry": noul("home_state_industry"),
        "model": str(getattr(response, "model", None) or JEV_MODEL),
    }


def _store(con: sqlite3.Connection, item: dict[str, Any], packed: dict[str, Any]) -> None:
    con.execute(
        """
        INSERT OR REPLACE INTO judgments(
          cache_key, trade_id, fields_sha, question_version, model,
          committee_score, committee_confidence, committee_p0, committee_p1,
          committee_p2, committee_p3, asset_matches_company, is_broad_fund,
          is_derivative, home_state_industry, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item["cache_key"],
            item["trade_id"],
            item["digest"],
            QUESTION_VERSION,
            packed.get("model") or JEV_MODEL,
            packed.get("committee_score"),
            packed.get("committee_confidence"),
            packed.get("committee_p0"),
            packed.get("committee_p1"),
            packed.get("committee_p2"),
            packed.get("committee_p3"),
            packed.get("asset_matches_company"),
            packed.get("is_broad_fund"),
            packed.get("is_derivative"),
            packed.get("home_state_industry"),
            datetime.now(ET).isoformat(timespec="seconds"),
        ),
    )


def judge(
    *,
    backfill: bool = False,
    limit: int = 0,
    workers: int = 12,
    max_usd: float = 3.0,
    con: sqlite3.Connection | None = None,
    bios: dict[str, Any] | None = None,
    client: Any = None,
    question_set: dict[str, Any] | None = None,
    recent_days: int = 120,
) -> dict[str, Any]:
    """Judge uncached Congress filings. The only function that calls Jev."""
    owns_con = con is None
    if owns_con:
        if not DB_PATH.is_file():
            raise SystemExit(f"qc.sqlite missing at {DB_PATH}")
        uri = "file:" + DB_PATH.as_posix() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
    assert con is not None
    owns_client = False
    filings = build_filings(con, bios)
    cache = _open_cache_rw(judgments_path())
    have = _cached_keys(cache)
    pending = [item for item in filings if item["cache_key"] not in have]
    if not backfill and recent_days > 0:
        dates = [parse_day(item["filed_date"]) for item in filings]
        dates = [day for day in dates if day is not None]
        if dates:
            cutoff = max(dates).toordinal() - int(recent_days)
            pending = [
                item for item in pending
                if item["trade_ord"] is None or item["filed_date"] and (parse_day(item["filed_date"]) or date.min).toordinal() >= cutoff
            ]
    pending.sort(key=lambda item: (item["filed_date"], item["trade_id"]), reverse=True)
    if limit and limit > 0:
        pending = pending[:limit]

    workers = max(1, min(int(workers), 16))
    if not pending:
        summary = {
            "backfill": bool(backfill),
            "filings": len(filings),
            "queued": 0,
            "calls": 0,
            "stored": 0,
            "errors": 0,
            "skipped": 0,
            "input_tokens": 0,
            "cost_usd": 0.0,
            "stopped_for_cap": False,
            "cache": str(judgments_path()),
            "question_version": QUESTION_VERSION,
            "model": JEV_MODEL,
            "committee_p3_min": COMMITTEE_P3_MIN,
        }
        print(
            f"judge backfill={int(backfill)} uncached=0 workers={workers} max_usd={max_usd}",
            flush=True,
        )
        print("judge_done " + json.dumps(summary, sort_keys=True), flush=True)
        cache.close()
        if owns_con:
            con.close()
        return summary

    if client is None:
        owns_client = True
        load_typesafe_env()
        if not os.environ.get("TYPESAFE_API_KEY"):
            cache.close()
            if owns_con:
                con.close()
            raise SystemExit("TYPESAFE_API_KEY missing")
        from typesafe_sdk import TypeSafeClient

        client = TypeSafeClient(model=JEV_MODEL, timeout=120.0)
    qset = question_set if question_set is not None else questions()
    stop = threading.Event()
    log_lock = threading.Lock()
    original_log = jev_common.log_usage

    def locked_log(*args: Any, **kwargs: Any) -> dict[str, Any]:
        with log_lock:
            return original_log(*args, **kwargs)

    jev_common.log_usage = locked_log

    def work(item: dict[str, Any]) -> dict[str, Any]:
        if stop.is_set():
            return {"ok": False, "skipped": True, "trade_id": item["trade_id"]}
        last_name = "error"
        for attempt in range(5):
            if stop.is_set():
                return {"ok": False, "skipped": True, "trade_id": item["trade_id"]}
            try:
                response = jev_common.ask(
                    item["fields"],
                    qset,
                    script="qc_filing_notes",
                    tag=item["trade_id"],
                    client=client,
                )
                packed = _pack_answers(response)
                usage = getattr(response, "usage", None)
                inp = int(getattr(usage, "input_tokens", 0) or 0)
                return {
                    "ok": True,
                    "item": item,
                    "packed": packed,
                    "cost": inp * jev_common.USD_PER_INPUT_TOKEN,
                    "input_tokens": inp,
                }
            except Exception as exc:
                last_name = type(exc).__name__
                if not _is_transient(exc) or attempt == 4:
                    return {"ok": False, "error": last_name, "trade_id": item["trade_id"]}
                delay = 2 ** attempt
                retry_after = getattr(exc, "retry_after_ms", None)
                if isinstance(retry_after, (int, float)) and retry_after > 0:
                    delay = max(delay, min(retry_after / 1000.0, 60.0))
                time.sleep(delay)
        return {"ok": False, "error": last_name, "trade_id": item["trade_id"]}

    calls = 0
    errors = 0
    skipped = 0
    spent = 0.0
    input_tokens = 0
    stored = 0
    summary: dict[str, Any] = {}
    try:
        print(
            f"judge backfill={int(backfill)} uncached={len(pending)} workers={workers} max_usd={max_usd}",
            flush=True,
        )
        index = 0
        inflight: dict[Any, dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=workers) as pool:
            while index < len(pending) or inflight:
                while index < len(pending) and len(inflight) < workers and not stop.is_set() and spent < max_usd:
                    item = pending[index]
                    index += 1
                    inflight[pool.submit(work, item)] = item
                if not inflight:
                    break
                done, _ = wait(set(inflight), return_when=FIRST_COMPLETED)
                for fut in done:
                    inflight.pop(fut, None)
                    result = fut.result()
                    if result.get("skipped"):
                        skipped += 1
                        continue
                    if not result.get("ok"):
                        errors += 1
                        print(f"judge error {result.get('trade_id')} {result.get('error')}", flush=True)
                        continue
                    calls += 1
                    spent += float(result.get("cost") or 0.0)
                    input_tokens += int(result.get("input_tokens") or 0)
                    _store(cache, result["item"], result["packed"])
                    stored += 1
                    if stored % 25 == 0:
                        cache.commit()
                    if calls % 200 == 0:
                        print(
                            f"judge calls={calls} stored={stored} spent_usd={spent:.4f} errors={errors}",
                            flush=True,
                        )
                    if spent >= max_usd:
                        stop.set()
            cache.commit()
        cache.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cache.commit()
        summary = {
            "backfill": bool(backfill),
            "filings": len(filings),
            "queued": len(pending),
            "calls": calls,
            "stored": stored,
            "errors": errors,
            "skipped": skipped,
            "input_tokens": input_tokens,
            "cost_usd": round(spent, 6),
            "stopped_for_cap": bool(stop.is_set()),
            "cache": str(judgments_path()),
            "question_version": QUESTION_VERSION,
            "model": JEV_MODEL,
            "committee_p3_min": COMMITTEE_P3_MIN,
        }
        print("judge_done " + json.dumps(summary, sort_keys=True), flush=True)
        return summary
    finally:
        jev_common.log_usage = original_log
        cache.close()
        if owns_client and client is not None and hasattr(client, "close"):
            client.close()
        if owns_con:
            con.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Congress filing notes")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Write filing-notes.json from qc.sqlite and the judgment cache. No network.")
    judge_parser = sub.add_parser("judge", help="Judge uncached Congress filings with Jev.")
    judge_parser.add_argument("--backfill", action="store_true", help="All past Congress filings, not a recent window.")
    judge_parser.add_argument("--limit", type=int, default=0, help="Cap how many uncached filings are sent this run.")
    judge_parser.add_argument("--workers", type=int, default=12, help="Thread pool size, clamped to 1..16.")
    judge_parser.add_argument("--max-usd", type=float, default=3.0, dest="max_usd", help="Stop scheduling new calls at this input-token cost.")
    args = parser.parse_args(argv)
    if args.cmd == "run":
        if not DB_PATH.is_file():
            print(f"qc.sqlite missing at {DB_PATH}", file=sys.stderr)
            return 1
        uri = "file:" + DB_PATH.as_posix() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True)
        try:
            meta = run(con)
        finally:
            con.close()
        print(json.dumps(meta, sort_keys=True))
        return 0
    judge(
        backfill=args.backfill,
        limit=args.limit,
        workers=args.workers,
        max_usd=args.max_usd,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
