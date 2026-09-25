#!/usr/bin/env python3
"""Build backtest.json: paper returns from STOCK Act filing dates.

Entry = first daily close strictly after filed_date (a PTR can post after
that day's close, so the filed-date close is not buyable).
Exit  = latest close in prices/, plus 90/180/365-day holds from that entry.
SPY excess uses the same entry and exit dates. Purchases only, equal-weight per leg.
Refiled / amended PTRs collapse to the earliest filing.
Skip bonds, options, junk tickers, and legs with no usable price.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from qc_common import (
    first_after,
    first_on_or_after,
    load_previous,
    load_prices,
    mean,
    median,
    round_ret,
    shrink_problems,
    utc_stamp,
    write_if_changed,
)

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "backtest.json"
BAD_TICKERS = {"LLC", "THE", "AND", "INC", "CORP", "CLASS", "NONE", "NA", "CMN", "COM", "NPV", "ETF", "FUND"}
OPT_RE = re.compile(
    r"exercised|call option|put option|strike pric|flex euro|\bcall/|\bput/|@\s*\d",
    re.I,
)
BOND_RE = re.compile(r"rate/coupon", re.I)
CHART_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,6}$")
# Weekend + holiday slack. A first bar far after filed_date is truncated history, not a real print.
MAX_ENTRY_LAG_DAYS = 10
# Fixed holds measured from the entry close, not the filed date.
HORIZON_DAYS = (90, 180, 365)
# Horizon exit and a missing SPY date may use the next bar only inside this window.
# Farther than that, the horizon (or the SPY leg) is absent. Do not substitute the last close.
MAX_MATCH_LAG_DAYS = 10


def is_bond(t: dict) -> bool:
    typ = (t.get("asset_type") or "").lower()
    asset = (t.get("asset") or "").lower()
    return "bond" in typ or "municipal" in typ or bool(BOND_RE.search(asset))


def is_option_like(t: dict) -> bool:
    typ = (t.get("asset_type") or "").lower()
    asset = (t.get("asset") or "").lower()
    return "option" in typ or bool(OPT_RE.search(asset))


def is_chart_ticker(code: str) -> bool:
    c = (code or "").upper()
    return bool(c and c != "—" and CHART_RE.match(c) and c not in BAD_TICKERS)


# Port of qc.js issuerName, plus PTR-row / account / share-class junk that
# pollutes tickers.json names. Prefer a real issuer over a glued filing line.
BROKER_ISSUERS = {
    "MS": re.compile(r"morgan stanley", re.I),
    "GS": re.compile(r"goldman sachs", re.I),
    "JPM": re.compile(r"jpmorgan|jp morgan", re.I),
    "BAC": re.compile(r"bank of america|merrill", re.I),
    "WFC": re.compile(r"wells fargo", re.I),
    "SCHW": re.compile(r"schwab", re.I),
    "C": re.compile(r"\bcitigroup\b|\bciti\b", re.I),
    "BLK": re.compile(r"blackrock", re.I),
    "UBS": re.compile(r"\bubs\b", re.I),
    "PNC": re.compile(r"\bpnc\b", re.I),
    "IBKR": re.compile(r"interactive brokers", re.I),
}
BROKERS = (
    r"morgan stanley|goldman sachs|fidelity(?: investments)?|vanguard|"
    r"charles schwab|\bschwab\b|bank of america|merrill lynch|\bmerrill\b|"
    r"jpmorgan(?: chase)?|jp ?morgan|wells fargo|\bubs\b|raymond james|"
    r"edward jones|ameriprise|e\*?trade|td ameritrade|interactive brokers|"
    r"\bchase\b|aperio group(?: llc)?"
)
ACCOUNT = (
    r"smith barney(?: llc)?|ira|roth ira|trust account|brokerage account|"
    r"\bbrokerage\b|select uma(?: account)?|unified management account|joint tbe"
)
SHARE_TAIL = re.compile(
    r"\s+(?:Common Stock.*|Class [A-Z].*|Ordinary Shares?.*|"
    r"American Depositary Shares?.*|\bADS\b.*|Registered Shares.*|"
    r"Common Shares.*|New York Registry Shares.*|"
    r"Common Units(?: Representing.*)?|\bVoting\b.*|Series [A-Z]\b.*|"
    r"\bCMN\b.*)$",
    re.I,
)
PTR_OTHER = re.compile(
    r"^.*\([A-Z]{1,6}\)(?:\s*\[ST\])?\s+[PS]\s+\d{1,2}/\d{1,2}/\d{2,4}"
    r".*?(?:\$[\d,]+(?:\s*-\s*\$[\d,]+)?)\s+"
)
PTR_ROW = re.compile(
    r"^[PS]\s+\d{1,2}/\d{1,2}/\d{2,4}.*?(?:\$[\d,]+(?:\s*-\s*\$[\d,]+)?)\s+"
)
JUNK_NAME = re.compile(r"\$[\d,]|\d{2}/\d{2}/\d{4}|\[ST\]|rate/coupon|matures:", re.I)


def issuer_name(code: str, raw: str) -> str:
    c = (code or "").upper()
    s = re.sub(r"\s+", " ", raw or "").strip()
    if not s or s == "—":
        return c
    s = PTR_OTHER.sub("", s)
    s = PTR_ROW.sub("", s)
    s = re.sub(r"\s+Option Type:.*$", "", s, flags=re.I)
    s = re.sub(r"\s*(?:Bond|Notes?|MTN)?\s*Rate/Coupon:.*$", "", s, flags=re.I)
    s = re.sub(r"\s+\b(?:Bond|Notes?|MTN)\s*$", "", s, flags=re.I)
    s = (s.split(">")[-1] if ">" in s else s).strip()
    broker_re = BROKER_ISSUERS.get(c)
    broker_stock = bool(
        broker_re
        and broker_re.search(s)
        and not re.search(
            r"\b(ira|roth|trust account|brokerage|select uma|unified management|joint tbe)\b",
            s,
            re.I,
        )
    )
    if broker_stock:
        s = SHARE_TAIL.sub("", s)
        s = re.sub(r"\s*-\s*$", "", s).strip()
        return s or c
    if c not in BROKER_ISSUERS:
        s = re.sub(rf"^(?:{BROKERS})\b[\s,:-]*", "", s, flags=re.I)
    s = re.sub(
        rf"^(?:{ACCOUNT}|uma(?: account)?|select uma(?: account)?)\b[\s,:-]*",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"^(?:account(?:\s*#\s*\d+)?|uma account(?:\s*#\s*\d+)?)\b[\s,:-]*",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"^#\s*\d+\s+", "", s)
    s = re.sub(r"^\d{2,5}\s+", "", s)
    s = re.sub(
        r"^[A-Za-z][A-Za-z.'-]*(?:\s+[A-Za-z][A-Za-z.'-]*)?\s+IRA\s+",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"^(?:tacs r3k)\s+", "", s, flags=re.I)
    s = re.sub(r"^\$[\d,]+(?:\.\d+)?\s+(?:F\s+S:\s*Amended\s+\S+\s+)?", "", s, flags=re.I)
    s = re.sub(
        r"^(?:CP\s*-?\s*INV|CRT\s*-?\s*Standard Unit Trust|Trust\s*-\s*\S+)\s+",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(
        r"^(?:D:\s*)?(?:Portfolio Rebalance|Account Closing|FULL LIQUIDATION\.?|"
        r"Professionally managed account|D/B/A)\s+",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"\b(?:D:\s*)?Portfolio Rebalance\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:investment account(?:\s*#\s*\d+)?)\b[\s,:-]*", "", s, flags=re.I)
    s = re.sub(r"^financial disclosure\.\s*", "", s, flags=re.I)
    s = re.sub(r"^active assets\s*\(\d+\)\s*", "", s, flags=re.I)
    s = re.sub(
        r"^.*\bD:\s*(?:professionally managed account\.?\s*|sold entire holding\.?\s*|own/operate\s+(?:mobile home park\s+)?)",
        "",
        s,
        flags=re.I,
    )
    s = re.sub(r"^C:\s*Sell to Open\s*[–—-]\s*(?:New\s+)?Covered Call Contract\s+", "", s)
    s = re.sub(r"^.*\bFamily Partnership\s+", "", s, flags=re.I)
    s = SHARE_TAIL.sub("", s)
    if c:
        s = re.sub(rf"\s*\({re.escape(c)}\)\s*$", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*Common\s+Sto.*$", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*$", "", s).strip()
    s = re.sub(r"\s+CMN\b.*$", "", s, flags=re.I).strip()
    s = re.sub(r"\s*S/ADR\s*$", "", s, flags=re.I).strip()
    if not s or re.match(r"^(common stock|class [a-z]|llc|inc|corp)$", s, re.I):
        return c
    if re.match(r"^[A-Z][A-Z0-9.]{0,6}$", s) and s.upper() != c:
        return c
    if JUNK_NAME.search(s):
        return c
    return s


def name_quality(code: str, s: str) -> int:
    if not s or s == code:
        return 0
    sl = s.lower()
    if len(s) > 90:
        return 0
    if JUNK_NAME.search(s):
        return 0
    if re.search(r"\b(uma account|brokerage account|select uma|investment account|financial disclosure|sell to open|professionally managed)\b", sl):
        return 0
    if re.search(r"\bD:\s|\bC:\s|\bL:\s", s):
        return 0
    q = 5
    if re.search(
        r"\b(inc|incorp|corp|corporation|ltd|limited|plc|llc|co|company|"
        r"group|holdings?|etf|n\.?v\.?)\b",
        sl,
    ):
        q += 6
    if " " in s:
        q += 3
    if re.search(r"\b(ira|roth|trust account|partnership|grandchildren)\b", sl):
        q -= 6
    if s.isupper() and len(s) > 24:
        q -= 2
    return q


def pick_name(code: str, cands: list[str]) -> str:
    best, best_q = code, -1
    seen = set()
    for s in cands:
        if not s or s in seen:
            continue
        seen.add(s)
        q = name_quality(code, s)
        if q > best_q or (q == best_q and q > 0 and len(s) < len(best)):
            best, best_q = s, q
    return best if best_q > 0 else code



def round_px(n) -> float:
    return round(float(n), 2)


def _iso_plus(iso: str, days: int) -> str | None:
    try:
        return (datetime.strptime(iso, "%Y-%m-%d") + timedelta(days=days)).strftime("%Y-%m-%d")
    except ValueError:
        return None


def _lag_days(start: str, end: str) -> int | None:
    try:
        return (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days
    except ValueError:
        return None


def match_bar(closes: list | None, date: str, slack: int = MAX_MATCH_LAG_DAYS):
    """First bar on or after `date`, if it lands within `slack` calendar days. Exact date wins."""
    if not closes or not date:
        return None
    bar = first_on_or_after(closes, date)
    if bar is None:
        return None
    bar_d, px = bar
    lag = _lag_days(date, bar_d)
    if lag is None or lag > slack:
        return None
    return bar_d, px


def horizon_exit(closes: list, entry_date: str, days: int):
    """Exit for a hold of `days` calendar days from the entry date, or None."""
    target = _iso_plus(entry_date, days)
    if target is None:
        return None
    return match_bar(closes, target)


def spy_pair(spy_closes, entry_date: str, exit_date: str, stock_ret: float) -> dict | None:
    """SPY return and excess (stock − SPY) on the same entry and exit dates."""
    entry = match_bar(spy_closes, entry_date)
    exit_bar = match_bar(spy_closes, exit_date)
    if entry is None or exit_bar is None:
        return None
    entry_px, exit_px = entry[1], exit_bar[1]
    if entry_px <= 0 or exit_px <= 0:
        return None
    spy_ret = exit_px / entry_px - 1.0
    return {"spy": round_ret(spy_ret), "xs": round_ret(float(stock_ret) - spy_ret)}


def leg_horizons(closes: list, entry_date: str, entry_px: float, spy_closes) -> dict:
    """Present horizons only. A missing 90/180/365 bar is omitted, not filled with the last close."""
    hz: dict = {}
    if not entry_px or entry_px <= 0:
        return hz
    for days in HORIZON_DAYS:
        bar = horizon_exit(closes, entry_date, days)
        if bar is None:
            continue
        exit_d, exit_px = bar
        if exit_px <= 0:
            continue
        ret = exit_px / entry_px - 1.0
        slot = {"ret": round_ret(ret), "out": round_px(exit_px), "outD": exit_d}
        spy = spy_pair(spy_closes, entry_date, exit_d, ret)
        if spy:
            slot.update(spy)
        hz[str(days)] = slot
    return hz


def price_leg(closes: list, entry_d: str, entry_px: float, spy_closes) -> dict:
    """To-last-close window plus any fixed horizons. Caller has already accepted the entry."""
    exit_d, exit_px = closes[-1]
    ret = exit_px / entry_px - 1.0
    leg = {
        "in": round_px(entry_px),
        "inD": entry_d,
        "out": round_px(exit_px),
        "outD": exit_d,
        "ret": round_ret(ret),
    }
    spy = spy_pair(spy_closes, entry_d, exit_d, ret)
    if spy:
        leg.update(spy)
    hz = leg_horizons(closes, entry_d, entry_px, spy_closes)
    if hz:
        leg["hz"] = hz
    return leg


def _spy_stats(slots: list[dict]) -> dict:
    spies: list[float] = []
    xss: list[float] = []
    for slot in slots:
        if "spy" in slot and "xs" in slot:
            spies.append(float(slot["spy"]))
            xss.append(float(slot["xs"]))
    if not spies:
        return {}
    return {
        "spyMed": round_ret(median(spies)),
        "spyAvg": round_ret(mean(spies)),
        "xsMed": round_ret(median(xss)),
        "xsAvg": round_ret(mean(xss)),
    }


def summarize_horizons(legs: list[dict]) -> dict:
    out: dict = {}
    for days in HORIZON_DAYS:
        key = str(days)
        slots = []
        for leg in legs:
            block = (leg.get("hz") or {}).get(key)
            if isinstance(block, dict) and "ret" in block:
                slots.append(block)
        if not slots:
            continue
        rets = [float(slot["ret"]) for slot in slots]
        row = {
            "n": len(slots),
            "med": round_ret(median(rets)),
            "avg": round_ret(mean(rets)),
            "win": sum(1 for r in rets if r > 0),
        }
        row.update(_spy_stats(slots))
        out[key] = row
    return out


def summarize_group(legs: list[dict]) -> dict:
    """To-last-close med/avg/win, SPY stats when any leg has them, and horizon aggregates."""
    rets = [float(leg["ret"]) for leg in legs]
    out = {
        "n": len(rets),
        "avg": round_ret(mean(rets)),
        "med": round_ret(median(rets)),
        "win": sum(1 for r in rets if r > 0),
    }
    out.update(_spy_stats(legs))
    hz = summarize_horizons(legs)
    if hz:
        out["hz"] = hz
    return out


def count_unpriced_filers(filers: dict) -> int:
    """Filer records that had an eligible purchase and ended with no priced leg."""
    n = 0
    for rec in filers.values():
        if int(rec.get("eligible") or 0) > 0 and not rec.get("legs"):
            n += 1
    return n


def doc_and_line(trade_id: str) -> tuple[str, int]:
    """Split '<chamber>-<doc>-<line>' into (doc, line). Ids without a line number are their own doc."""
    head, sep, tail = (trade_id or "").rpartition("-")
    if sep and tail.isdigit():
        return head, int(tail)
    return trade_id or "", 0


def duplicate_purchase_ids(trades: list[dict]) -> set[str]:
    """Ids of purchase rows that repeat an earlier-filed PTR (refiled or amended).

    Key is (filer, ticker, trade_date, amount, owner). Repeated lines inside one
    document can be genuine separate buys (amounts are ranges), so each key keeps
    as many rows as its largest single document holds, earliest filed first.
    """
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for t in trades:
        if t.get("side") != "purchase":
            continue
        trade = (t.get("trade_date") or "")[:10]
        code = (t.get("ticker") or "").upper()
        fid = t.get("filer_id") or t.get("filer") or ""
        if not trade or not code or not fid or not t.get("id"):
            continue
        key = (fid, code, trade, t.get("amount") or "", t.get("owner") or "")
        groups[key].append(t)
    drop: set[str] = set()
    for rows in groups.values():
        if len(rows) < 2:
            continue
        per_doc: dict[str, int] = defaultdict(int)
        for t in rows:
            per_doc[doc_and_line(t["id"])[0]] += 1
        allowed = max(per_doc.values())
        if allowed >= len(rows):
            continue
        rows.sort(key=lambda t: ((t.get("filed_date") or "9999")[:10], *doc_and_line(t["id"])))
        drop.update(t["id"] for t in rows[allowed:])
    return drop


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--force", action="store_true", help="Write even if the tape shrank below half the previous build")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    tape_path = ROOT / "trades-lite.json"
    if not tape_path.is_file():
        print("missing trades-lite.json", file=sys.stderr)
        return 1
    with tape_path.open() as f:
        tape = json.load(f)
    trades = tape.get("trades") or []

    name_cands: dict[str, list[str]] = {}
    tickers_path = ROOT / "tickers.json"
    if tickers_path.is_file():
        with tickers_path.open() as f:
            tickers_file = json.load(f)
        for code, meta in (tickers_file.get("tickers") or {}).items():
            c = str(code).upper()
            cleaned = issuer_name(c, (meta or {}).get("name") or "")
            if cleaned:
                name_cands.setdefault(c, []).append(cleaned)

    for t in trades:
        code = (t.get("ticker") or "").upper()
        if not is_chart_ticker(code):
            continue
        asset_name = issuer_name(code, t.get("asset") or "")
        if asset_name:
            name_cands.setdefault(code, []).append(asset_name)

    dup_ids = duplicate_purchase_ids(trades)
    price_cache: dict = {}
    spy_closes = load_prices("SPY", price_cache)
    stats = {
        "purchases": 0,
        "eligible": 0,
        "priced": 0,
        "skippedDuplicate": 0,
        "skippedNoTicker": 0,
        "skippedBondOpt": 0,
        "skippedNoPrice": 0,
        "skippedStale": 0,
    }
    filers = {}
    price_asof = ""

    for t in trades:
        if t.get("side") != "purchase":
            continue
        stats["purchases"] += 1
        if t.get("id") in dup_ids:
            stats["skippedDuplicate"] += 1
            continue
        if is_bond(t) or is_option_like(t):
            stats["skippedBondOpt"] += 1
            continue
        code = (t.get("ticker") or "").upper()
        if not is_chart_ticker(code):
            stats["skippedNoTicker"] += 1
            continue
        stats["eligible"] += 1
        fid = t.get("filer_id") or t.get("filer") or ""
        if not fid:
            stats["skippedNoTicker"] += 1
            continue
        rec = filers.get(fid)
        if rec is None:
            rec = {
                "id": fid,
                "name": t.get("filer") or fid,
                "chamber": t.get("chamber") or "",
                "eligible": 0,
                "skip": 0,
                "legs": [],
            }
            filers[fid] = rec
        rec["eligible"] += 1
        filed = (t.get("filed_date") or "")[:10]
        if len(filed) < 10:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        closes = load_prices(code, price_cache)
        if not closes:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        if closes[-1][0] > price_asof:
            price_asof = closes[-1][0]
        bar = first_after(closes, filed)
        if bar is None:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        entry_d, entry_px = bar
        try:
            filed_dt = datetime.strptime(filed, "%Y-%m-%d")
            entry_dt = datetime.strptime(entry_d, "%Y-%m-%d")
        except ValueError:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        lag = (entry_dt - filed_dt).days
        # Weekend/holiday slack only. A first bar far after filed_date is the
        # start of a truncated price file (missing history), not a real print.
        if lag < 1 or lag > MAX_ENTRY_LAG_DAYS:
            rec["skip"] += 1
            stats["skippedStale"] += 1
            continue
        if closes[-1][1] <= 0 or entry_px <= 0:
            rec["skip"] += 1
            stats["skippedNoPrice"] += 1
            continue
        leg = {
            "t": code,
            "name": pick_name(code, name_cands.get(code) or [code]),
            "filed": filed,
            "trade": (t.get("trade_date") or "")[:10],
            "amt": t.get("amount") or "",
        }
        leg.update(price_leg(closes, entry_d, entry_px, spy_closes))
        rec["legs"].append(leg)
        stats["priced"] += 1

    people = []
    ticker_acc = {}
    for rec in filers.values():
        legs = rec["legs"]
        if not legs:
            continue
        legs.sort(key=lambda x: (x["filed"], x["t"]), reverse=True)
        for leg in legs:
            leg.pop("name", None)
            if not leg.get("amt"):
                leg.pop("amt", None)
            if not leg.get("trade"):
                leg.pop("trade", None)
        summary = summarize_group(legs)
        person = {
            "id": rec["id"],
            "name": rec["name"],
            "chamber": rec["chamber"],
            "n": summary["n"],
            "skip": rec["skip"],
            "avg": summary["avg"],
            "med": summary["med"],
            "win": summary["win"],
        }
        for key in ("spyMed", "spyAvg", "xsMed", "xsAvg", "hz"):
            if key in summary:
                person[key] = summary[key]
        person["legs"] = legs
        people.append(person)
        for leg in legs:
            acc = ticker_acc.get(leg["t"])
            if acc is None:
                acc = {"legs": [], "people": set()}
                ticker_acc[leg["t"]] = acc
            acc["legs"].append(leg)
            acc["people"].add(rec["id"])

    people.sort(key=lambda r: (-r["med"], -r["n"], r["name"]))
    tickers = []
    for code, acc in ticker_acc.items():
        summary = summarize_group(acc["legs"])
        row = {
            "t": code,
            "name": pick_name(code, name_cands.get(code) or [code]),
            "n": summary["n"],
            "people": len(acc["people"]),
            "avg": summary["avg"],
            "med": summary["med"],
            "win": summary["win"],
        }
        for key in ("spyMed", "spyAvg", "xsMed", "xsAvg", "hz"):
            if key in summary:
                row[key] = summary[key]
        tickers.append(row)
    tickers.sort(key=lambda r: (-r["med"], -r["n"], r["t"]))

    out = {
        "generated": utc_stamp(),
        "tapeCollected": tape.get("collected") or "",
        "priceAsof": price_asof,
        "method": (
            "Purchases only. Equal-weight listed-stock legs, ranked by the median. "
            "Entry is the first daily close after filed_date (a PTR can post after "
            "that day's close; max "
            + str(MAX_ENTRY_LAG_DAYS)
            + " calendar days). "
            "The to-last-close exit is the latest close in prices/. "
            "Fixed holds of 90, 180, and 365 calendar days are measured from that entry date: "
            "the exit is the first daily close on or after entry plus N days, and only if that "
            "bar is within "
            + str(MAX_MATCH_LAG_DAYS)
            + " calendar days of the target. A horizon with no such bar is left out, not replaced "
            "by the last close. "
            "SPY excess is the stock return minus the SPY return on the same entry and exit dates "
            "(the SPY close on that date, or the next close within "
            + str(MAX_MATCH_LAG_DAYS)
            + " calendar days). "
            "A refiled or amended PTR repeating the same filer, ticker, trade date, "
            "amount, and owner counts once, at its earliest filed date. "
            "Bonds, options, and legs with missing prices are skipped. "
            "Official amounts are ranges, so size is not used."
        ),
        "disclaimer": (
            "Not investment advice. Hypothetical paper returns from public filing dates, "
            "not the politician's actual trade date or fill. Amounts are official ranges, "
            "not share counts. Returns are not annualized. Past copies do not mean the next filing works."
        ),
        "stats": {
            "purchases": stats["purchases"],
            "eligible": stats["eligible"],
            "priced": stats["priced"],
            "skippedDuplicate": stats["skippedDuplicate"],
            "skippedNoTicker": stats["skippedNoTicker"],
            "skippedBondOpt": stats["skippedBondOpt"],
            "skippedNoPrice": stats["skippedNoPrice"],
            "skippedStale": stats["skippedStale"],
            "unpricedFilers": count_unpriced_filers(filers),
            "filers": sum(1 for p in people if p["n"]),
            "tickers": len(tickers),
        },
        "filers": people,
        "tickers": tickers,
    }

    prev, prev_warn = load_previous(DEST)
    if prev_warn:
        print(prev_warn, file=sys.stderr)
    shrunk = shrink_problems(prev, out["stats"], ("purchases", "priced"))
    if shrunk and not args.force:
        print(
            "refusing to overwrite {path}: input shrank below half the previous build ({why}). "
            "Re-run with --force if this is intended.".format(path=DEST.name, why="; ".join(shrunk)),
            file=sys.stderr,
        )
        return 2
    dest = DEST
    wrote = write_if_changed(dest, out, prev)
    size = dest.stat().st_size
    print(
        "{verb} {path} ({kb:.0f} KB)  priced={priced} dup={dup} filers={filers} tickers={tickers} asof={asof}".format(
            verb="wrote" if wrote else "unchanged",
            dup=stats["skippedDuplicate"],
            path=dest.name,
            kb=size / 1024,
            priced=stats["priced"],
            filers=out["stats"]["filers"],
            tickers=len(tickers),
            asof=price_asof,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
