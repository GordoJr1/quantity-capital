"""Pure helpers for the 2026-10-07 claims vs insider-buys research.

No sqlite writes. No network. Safe to unit-test with tiny fixtures.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable

KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_EQ = 111.320

JURIS_TO_PROVINCE = {
    "Ontario": "ontario",
    "Quebec": "quebec",
    "British Columbia": "british-columbia",
}

PROVINCES = (
    "ontario",
    "quebec",
    "british-columbia",
    "yukon",
    "nunavut",
    "newfoundland",
)

OLD_B_PROVINCES = ("ontario", "quebec", "british-columbia")
OLD_B_FULL = frozenset({"ontario"})
OLD_B_PARTIAL = frozenset({"quebec", "british-columbia"})
# OLD B "fully covers" a company-province when its distinct title count
# is close to the OLD A holder count (not a neighbor sliver, not an extract dump).
COVER_MIN_RATIO = 0.9
COVER_MAX_RATIO = 1.25

SNAPSHOT_DAY = date(2026, 10, 2)
CLUSTER_WIN0 = date(2023, 7, 1)
CLUSTER_WIN1 = date(2026, 9, 30)
RATE_WIN0 = date(2025, 4, 1)
ON_CONVERSION = date(2018, 4, 10)
POST_DAYS = 90
FILING_LAG_MAX = 30

MAJOR_TYPES = frozenset({"Producer, Major", "Producer, Mid-tier"})
JUNIOR_EXACT = frozenset({"Producer, Junior", "Land Banks"})
EXCLUDED_TYPES = frozenset({"Royalty", "Other"})


def connect_ro(path: Path) -> sqlite3.Connection:
    """Open sqlite read-only. Never writes. URI mode=ro plus query_only."""
    if not path.is_file():
        raise FileNotFoundError(path)
    uri = path.resolve().as_uri() + "?mode=ro"
    con = sqlite3.connect(uri, uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON")
    return con


def month_list(start: date, end: date) -> list[tuple[int, int]]:
    """Inclusive year-month pairs from start through end."""
    months: list[tuple[int, int]] = []
    y, m = start.year, start.month
    last = (end.year, end.month)
    while (y, m) <= last:
        months.append((y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return months


def parse_iso_date(value: object) -> date | None:
    text = "" if value is None else str(value).strip()
    if len(text) < 10 or text[4] != "-" or text[7] != "-":
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def valid_issue_date(province: str, raw: object, snapshot_day: date = SNAPSHOT_DAY) -> date | None:
    """Keep real staking dates. Drop conversion, junk, and dates after the snapshot."""
    parsed = parse_iso_date(raw)
    if parsed is None:
        return None
    if province == "ontario" and parsed == ON_CONVERSION:
        return None
    if parsed.year < 1990:
        return None
    if parsed > snapshot_day:
        return None
    return parsed


def change_pct(old_claims: int, new_claims: int) -> float | None:
    if old_claims == 0:
        return None
    return 100.0 * (new_claims - old_claims) / old_claims


def title_diff_basis(
    old_claims: int,
    old_b_titles: int,
    province: str,
    linked: bool = True,
) -> str:
    """claim_ids only when OLD B fully covers this company in this province.

    Rule: linked company, province is in OLD B (ON/QC/BC), OLD A old_claims > 0,
    and OLD B distinct titles are between 90% and 125% of old_claims.
    Otherwise counts_only. Quebec and BC extracts still qualify if that
    company's own OLD B rows sit in the band. A neighbor sliver, an extract
    dump far above OLD A, or zero OLD B rows is not full coverage.
    """
    if not linked:
        return "counts_only"
    if province not in OLD_B_PROVINCES:
        return "counts_only"
    if old_claims <= 0 or old_b_titles <= 0:
        return "counts_only"
    if old_b_titles < COVER_MIN_RATIO * old_claims:
        return "counts_only"
    if old_b_titles > COVER_MAX_RATIO * old_claims:
        return "counts_only"
    return "claim_ids"


def title_diff_fields(
    old_claims: int,
    new_claims: int,
    old_b_titles: int,
    province: str,
    linked: bool,
    added: int | None,
    dropped: int | None,
) -> dict:
    """Fill basis, added, dropped, old_source, id_gap, change for one comparison row."""
    change = new_claims - old_claims
    basis = title_diff_basis(old_claims, old_b_titles, province, linked)
    if basis == "counts_only":
        return {
            "basis": "counts_only",
            "added": None,
            "dropped": None,
            "old_source": "A",
            "id_gap": None,
            "change": change,
        }
    a = 0 if added is None else int(added)
    d = 0 if dropped is None else int(dropped)
    return {
        "basis": "claim_ids",
        "added": a,
        "dropped": d,
        "old_source": "A+B",
        "id_gap": (a - d) - change,
        "change": change,
    }


def change_type(old_claims: int, new_claims: int) -> str:
    """added / dropped / new_to_data / missing_from_data / unchanged."""
    if old_claims <= 0 and new_claims > 0:
        return "new_to_data"
    if old_claims > 0 and new_claims <= 0:
        return "missing_from_data"
    if old_claims > 0 and new_claims > old_claims:
        return "added"
    if new_claims > 0 and new_claims < old_claims:
        return "dropped"
    return "unchanged"


def cluster_qualifies(count: int, mean: float, min_n: int, mult: float) -> bool:
    """A cluster month has at least min_n new claims and at least mult times the company mean."""
    if count < min_n:
        return False
    return count >= mult * mean


def in_post_window(trade_day: date, cluster_day: date, window_days: int = POST_DAYS) -> bool:
    """Buy is after a cluster only if the cluster date is on or before the trade date.

    Window is cluster_day through cluster_day + window_days, inclusive.
    """
    if cluster_day > trade_day:
        return False
    return trade_day <= cluster_day + timedelta(days=window_days)


def buy_kind(origin: str | None, trade_id: str | None) -> str:
    """Open-market vs placement. SEDI code is the last hyphen field; Form 4 purchase is P."""
    origin_s = (origin or "").strip().lower()
    tid = trade_id or ""
    if origin_s == "sedi":
        code = tid.rsplit("-", 1)[-1] if tid else ""
    else:
        code = "P"
    if code in {"10", "P"}:
        return "open_market"
    return "placement_or_private"


def is_major_type(company_type: str | None, company_id: str, mine_owners: Iterable[str]) -> bool:
    if company_id in set(mine_owners):
        return True
    return (company_type or "") in MAJOR_TYPES


def is_junior_type(company_type: str | None) -> bool:
    text = company_type or ""
    if not text or text in EXCLUDED_TYPES:
        return False
    if text.startswith("Explorer"):
        return True
    if text.startswith("Developer"):
        return True
    return text in JUNIOR_EXACT


def role_for_company(company_type: str | None, company_id: str, mine_owners: Iterable[str]) -> str:
    """major, junior, or exclude. A mine owner is major even if typed junior."""
    if is_major_type(company_type, company_id, mine_owners):
        return "major"
    if is_junior_type(company_type):
        return "junior"
    return "exclude"


def box_edge_distance_km(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
) -> float:
    """Nearest edge-to-edge distance in km between two lon/lat boxes (minx, miny, maxx, maxy).

    Overlap is 0. Degrees convert with a latitude-scaled lon factor.
    """
    a_minx, a_miny, a_maxx, a_maxy = a
    b_minx, b_miny, b_maxx, b_maxy = b
    if a_maxx < b_minx:
        dx_deg = b_minx - a_maxx
    elif b_maxx < a_minx:
        dx_deg = a_minx - b_maxx
    else:
        dx_deg = 0.0
    if a_maxy < b_miny:
        dy_deg = b_miny - a_maxy
    elif b_maxy < a_miny:
        dy_deg = a_miny - b_maxy
    else:
        dy_deg = 0.0
    lat = 0.5 * ((a_miny + a_maxy) * 0.5 + (b_miny + b_maxy) * 0.5)
    km_x = dx_deg * KM_PER_DEG_LON_EQ * math.cos(math.radians(lat))
    km_y = dy_deg * KM_PER_DEG_LAT
    return math.hypot(km_x, km_y)


def format_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return ""
    text = f"{value:.{digits}f}"
    if text == "-0.0":
        return "0.0"
    return text


def format_num(value: float | None, digits: int = 2) -> str:
    if value is None:
        return ""
    text = f"{float(value):.{digits}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in {"", "-0"}:
        return "0"
    return text


def holder_key(province: str, holder: str | None) -> tuple[str, str]:
    text = (holder or "").strip() or "(blank)"
    return (province, text)


def primary_link(row: dict) -> tuple[str, str, str]:
    """Return (company_id, company_name, ticker) from a holders.json row."""
    cid = str(row.get("company_id") or "").strip()
    companies = row.get("companies") or []
    name = ""
    ticker = ""
    if isinstance(companies, list):
        for company in companies:
            if not isinstance(company, dict):
                continue
            name = str(company.get("company") or "").strip()
            ticker = str(company.get("ticker") or "").strip()
            if not ticker:
                tickers = company.get("tickers") or []
                if isinstance(tickers, list) and tickers:
                    ticker = str(tickers[0] or "").strip()
            if not cid:
                cid = str(company.get("company_id") or "").strip()
            if name or ticker or cid:
                break
    if cid and not name:
        name = cid
    return cid, name, ticker
