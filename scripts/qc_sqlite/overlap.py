"""Politician vs insider same-week overlap board.

Reads politician_trades and insider_trades already in qc.sqlite. No network.
"""
from __future__ import annotations

import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

sys.path.insert(0, str(HERE.parent))
from qc_io import atomic_write_json  # noqa: E402

from paths import DB_PATH, EXPORT_DIR, QC_ROOT  # noqa: E402

OUT_NAME = "politician-insider-overlap.json"
WINDOW_MONTHS = 24
SHOW_CAP = 3
BAD_TICKERS = {"", "--", "N/A", "NA", "N.A.", "NONE", "NULL"}
# purchase/sale are open-market. sale_post is a post-exercise stock sale, still a sell.
POL_SIDE = {"purchase": "Buy", "sale": "Sell"}
INS_SIDE = {"purchase": "Buy", "sale": "Sell", "sale_post": "Sell"}
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
METHOD = (
    "One row per ticker and ISO week (Monday-Sunday) by trade date. "
    "Window is the 24 calendar months ending on the newest trade_date in either tape. "
    "Members of Congress only: chamber House or Senate (case-insensitive). "
    "White House and any other chamber drop. "
    "Politician sides: purchase=Buy, sale=Sell; exchange dropped. "
    "Insider sides: purchase=Buy, sale=Sell, sale_post=Sell; award, exercise, and exchange dropped. "
    "A politician row counts only when asset_type is exactly Stock "
    "(Stock Option, Non-Public Stock, bonds, municipals, and other types drop). "
    "Blank tickers and non-tickers (--, N/A, NA, NONE) drop. "
    "Each person is one line per side; the three largest on each side are kept."
)


def log(msg: str) -> None:
    print(msg, flush=True)


def shift_months(d: date, months: int) -> date:
    year, month = d.year, d.month + months
    while month <= 0:
        month += 12
        year -= 1
    while month > 12:
        month -= 12
        year += 1
    import calendar

    last = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last))


def parse_day(raw: Any) -> date | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def clean_ticker(raw: Any) -> str | None:
    if raw is None:
        return None
    code = str(raw).strip().upper()
    if code in BAD_TICKERS or not code[:1].isalpha():
        return None
    if len(code) > 10:
        return None
    for ch in code:
        if not (ch.isascii() and (ch.isalnum() or ch == ".")):
            return None
    return code


def week_monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def week_label(monday: date) -> str:
    return f"Week of {MONTHS[monday.month - 1]} {monday.day}, {monday.year}"


def as_float(raw: Any) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        num = float(raw)
    except (TypeError, ValueError):
        return None
    if num != num:
        return None
    return num


def json_num(num: float) -> int | float:
    rounded = round(num, 2)
    if abs(rounded - round(rounded)) < 1e-9:
        return int(round(rounded))
    return rounded


def text(raw: Any) -> str:
    return str(raw).strip() if raw is not None else ""


def anchor_day(con: sqlite3.Connection) -> date | None:
    row = con.execute(
        """
        SELECT MAX(trade_date) FROM (
            SELECT trade_date FROM politician_trades
            UNION ALL
            SELECT trade_date FROM insider_trades
        )
        """
    ).fetchone()
    return parse_day(row[0] if row else None)


def _merge_pol(group: dict[tuple[str, str], dict[str, Any]], row: tuple) -> None:
    name, filer_id, chamber, side, amount_raw, amount_mid, day = row
    ident = filer_id or ("name:" + name)
    key = (ident, side)
    size = amount_mid if amount_mid is not None else -1.0
    cur = group.get(key)
    if cur is None or size > cur["_size"] or (size == cur["_size"] and day > cur["trade_date"]):
        group[key] = {
            "id": filer_id,
            "name": name,
            "chamber": chamber,
            "side": side,
            "amount": amount_raw,
            "trade_date": day,
            "_size": size,
        }


def _merge_ins(group: dict[tuple[str, str], dict[str, Any]], row: tuple) -> None:
    name, filer_id, title, side, value, day = row
    ident = filer_id or ("name:" + name)
    key = (ident, side)
    cur = group.get(key)
    if cur is None:
        cur = {
            "id": filer_id,
            "name": name,
            "title": title,
            "side": side,
            "value": 0.0,
            "known": False,
            "trade_date": day,
            "_best": -1.0,
        }
        group[key] = cur
    if value is None:
        if not cur["known"] and day > cur["trade_date"]:
            cur["trade_date"] = day
            if title:
                cur["title"] = title
            if name:
                cur["name"] = name
        return
    cur["value"] += value
    cur["known"] = True
    if value >= cur["_best"]:
        cur["_best"] = value
        cur["trade_date"] = day
        if title:
            cur["title"] = title
        if name:
            cur["name"] = name


def _public_pol(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec["id"],
        "name": rec["name"],
        "chamber": rec["chamber"],
        "side": rec["side"],
        "amount": rec["amount"],
        "trade_date": rec["trade_date"],
    }


def _public_ins(rec: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": rec["id"],
        "name": rec["name"],
        "title": rec["title"],
        "side": rec["side"],
        "value": json_num(rec["value"]) if rec["known"] else None,
        "trade_date": rec["trade_date"],
    }


def match_of(pol_sides: set[str], ins_sides: set[str]) -> str:
    if pol_sides == {"Buy"} and ins_sides == {"Buy"}:
        return "both buy"
    if pol_sides == {"Sell"} and ins_sides == {"Sell"}:
        return "both sell"
    if len(pol_sides) == 1 and len(ins_sides) == 1 and pol_sides != ins_sides:
        return "opposite"
    return "mixed"


def build(con: sqlite3.Connection, *, months: int = WINDOW_MONTHS, cap: int = SHOW_CAP) -> dict[str, Any]:
    anchor = anchor_day(con) or datetime.now(timezone.utc).date()
    start = shift_months(anchor, -months)
    start_s = start.isoformat()
    buckets: dict[tuple[str, str], dict[str, dict]] = {}

    def bucket(ticker: str, monday: date) -> dict[str, dict]:
        key = (ticker, monday.isoformat())
        found = buckets.get(key)
        if found is None:
            found = {"pol": {}, "ins": {}}
            buckets[key] = found
        return found

    for raw in con.execute(
        """
        SELECT filer, filer_id, chamber, ticker, asset_type, side,
               amount_raw, amount_mid, trade_date
        FROM politician_trades
        WHERE trade_date >= ?
          AND lower(trim(coalesce(chamber, ''))) IN ('house', 'senate')
          AND lower(trim(coalesce(asset_type, ''))) = 'stock'
          AND lower(trim(coalesce(side, ''))) IN ('purchase', 'sale')
        """,
        (start_s,),
    ):
        code = clean_ticker(raw[3])
        day = parse_day(raw[8])
        side = POL_SIDE.get(text(raw[5]).lower())
        if not code or day is None or not side or day < start:
            continue
        _merge_pol(
            bucket(code, week_monday(day))["pol"],
            (text(raw[0]), text(raw[1]), text(raw[2]), side, text(raw[6]), as_float(raw[7]), day.isoformat()),
        )

    for raw in con.execute(
        """
        SELECT filer, filer_id, title, ticker, side, value, trade_date
        FROM insider_trades
        WHERE trade_date >= ?
          AND lower(trim(coalesce(side, ''))) IN ('purchase', 'sale', 'sale_post')
        """,
        (start_s,),
    ):
        code = clean_ticker(raw[3])
        day = parse_day(raw[6])
        side = INS_SIDE.get(text(raw[4]).lower())
        if not code or day is None or not side or day < start:
            continue
        _merge_ins(
            bucket(code, week_monday(day))["ins"],
            (text(raw[0]), text(raw[1]), text(raw[2]), side, as_float(raw[5]), day.isoformat()),
        )

    rows: list[dict[str, Any]] = []
    for (ticker, monday_iso), groups in buckets.items():
        if not groups["pol"] or not groups["ins"]:
            continue
        pols = sorted(groups["pol"].values(), key=lambda rec: (-rec["_size"], rec["name"].lower(), rec["trade_date"]))
        ins = sorted(
            groups["ins"].values(),
            key=lambda rec: (-(rec["value"] if rec["known"] else -1.0), rec["name"].lower(), rec["trade_date"]),
        )
        monday = date.fromisoformat(monday_iso)
        rows.append(
            {
                "ticker": ticker,
                "week": monday_iso,
                "label": week_label(monday),
                "politicians": [_public_pol(rec) for rec in pols[:cap]],
                "insiders": [_public_ins(rec) for rec in ins[:cap]],
                "politicians_more": max(0, len(pols) - cap),
                "insiders_more": max(0, len(ins) - cap),
                "match": match_of({rec["side"] for rec in pols}, {rec["side"] for rec in ins}),
            }
        )
    rows.sort(key=lambda rec: rec["ticker"])
    rows.sort(key=lambda rec: rec["week"], reverse=True)
    same = sum(1 for rec in rows if rec["match"] in ("both buy", "both sell"))
    opposite = sum(1 for rec in rows if rec["match"] == "opposite")
    return {
        "generated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "window_start": start_s,
        "method": METHOD,
        "stats": {
            "rows": len(rows),
            "tickers": len({rec["ticker"] for rec in rows}),
            "same_side": same,
            "opposite": opposite,
            "mixed": len(rows) - same - opposite,
        },
        "rows": rows,
    }


def write_payload(payload: dict[str, Any], root: Path, export_dir: Path | None) -> None:
    atomic_write_json(root / OUT_NAME, payload)
    if export_dir is not None:
        export_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_json(export_dir / OUT_NAME, payload)


def run(
    con: sqlite3.Connection,
    *,
    root: Path | None = None,
    export_dir: Path | None = None,
) -> dict[str, Any]:
    """Build the board and write JSON. On failure, leave the previous file in place."""
    own_root = root is None
    root = QC_ROOT if root is None else Path(root)
    if export_dir is None and own_root:
        export_dir = EXPORT_DIR
    started = time.perf_counter()
    try:
        payload = build(con)
        write_payload(payload, root, export_dir)
    except Exception as exc:
        elapsed = time.perf_counter() - started
        log(
            f"Overlap board failed after {elapsed:.2f}s: {type(exc).__name__}: {exc}; keeping previous JSON"
        )
        return {}
    elapsed = time.perf_counter() - started
    stats = payload["stats"]
    log(
        f"Overlap board {elapsed:.2f}s rows={stats['rows']} tickers={stats['tickers']} "
        f"same={stats['same_side']} opposite={stats['opposite']}"
    )
    return payload


def main() -> int:
    if not DB_PATH.exists():
        log(f"missing {DB_PATH}")
        return 1
    con = sqlite3.connect(str(DB_PATH))
    try:
        run(con)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
