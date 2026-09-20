"""Tells board calc — same rules as collect/build_tells.py.

Reads politician_trades + prices/*.json. Writes tell_* tables and tells.json.
Scoring is deterministic (no Jev) so the JSON contract can match the collector.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paths import DB_PATH, EXPORT_DIR, GROKS_PRICES, PRICES, SCHEMA_SQL, TELLS_JSON, TRADES, TRADES_LITE

SKIP = {
    "LP", "SPCX", "GOOGM", "GOOGN", "SPY", "QQQ", "QQQM", "VOO", "VTI", "IWM", "DIA",
    "IVV", "VEA", "VWO", "ARKK", "TLT", "BND", "AGG", "XLF", "XLK", "XLE", "XLV",
    "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "SMH", "SOXX", "IJR", "IJH", "RSP",
    "VGT", "VOOG", "VUG", "VTV",
}
BAD = {"LLC", "THE", "AND", "INC", "CORP", "CLASS", "NONE", "NA"}
RESERVED = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "LPT1", "LPT2", "LPT3"}
SPIKE = 0.20
FWD = 21
NEAR = 1.08
MIN_SCORED = 4
MIN_HITS = 2
RECENT = 90
OPEN_CAP = 0.15
HANDS_CAP = 16
NOW_CAP = 12

DISCLAIMER = (
    "Not a recommendation. A tell is an official stock buy within 8% of the prior "
    "20-session low that then rose 20% within 15 sessions. Past spikes do not mean "
    "the next buy works."
)


def log(msg: str) -> None:
    print(msg, flush=True)


def prices_dir() -> Path:
    if PRICES.exists():
        return PRICES
    return GROKS_PRICES


def chart(code: str) -> bool:
    c = (code or "").upper()
    return bool(c and re.match(r"^[A-Z][A-Z0-9.]{0,6}$", c) and c not in BAD and c not in SKIP and c not in RESERVED)


def is_opt(asset_type: str | None, asset: str | None) -> bool:
    typ = (asset_type or "").lower()
    text = (asset or "").lower()
    return "option" in typ or bool(re.search(r"exercised|call option|put option|strike pric|flex euro", text))


def is_bond(asset_type: str | None) -> bool:
    typ = (asset_type or "").lower()
    return "bond" in typ or "municipal" in typ or "other" in typ


def amount_high(amount: str) -> int:
    nums = re.findall(r"\$[\d,]+", amount or "")
    if not nums:
        return 0
    return int(nums[-1].replace("$", "").replace(",", ""))


def clean_name(name: str, code: str) -> str:
    s = re.sub(r"\s+", " ", name or "").strip()
    s = (s.split(">")[-1] or s).strip()
    s = re.sub(r"^(?:joint ownership\s+)?(?:lpl account)\s+", "", s, flags=re.I)
    s = re.sub(r"^(?:bank of america|morgan stanley|ubs)\s+", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*$", "", s)
    return s or code


class Series:
    __slots__ = ("d", "p")

    def __init__(self, rows: list[tuple[str, float]]):
        self.d = [r[0] for r in rows]
        self.p = [r[1] for r in rows]

    def at(self, day: str) -> int | None:
        i = bisect_right(self.d, day) - 1
        return i if i >= 0 else None


def load_one(code: str, root: Path) -> Series | None:
    path = root / f"{code}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if data.get("missing") or not data.get("c"):
        return None
    rows = [(d, float(px)) for d, px in data["c"] if px]
    if len(rows) < 30:
        return None
    return Series(rows)


def last_us_session_close(dates: list[str], today: str | None = None) -> str:
    today = today or datetime.now(timezone.utc).date().isoformat()
    best = None
    for d in dates:
        if not d:
            continue
        day = str(d)[:10]
        if day > today:
            continue
        try:
            dt = datetime.fromisoformat(day).date()
        except ValueError:
            continue
        if best is None or dt > best:
            best = dt
    if best is None:
        return ""
    while best.weekday() >= 5:
        best -= timedelta(days=1)
    return best.isoformat()


def peek_collected() -> str | None:
    for path in (TRADES_LITE, TRADES):
        if not path.exists():
            continue
        try:
            head = path.read_text(encoding="utf-8-sig")[:4000]
        except OSError:
            continue
        m = re.search(r'"collected"\s*:\s*"([^"]+)"', head)
        if m:
            return m.group(1)
    return None


def ensure_tell_tables(con: sqlite3.Connection) -> None:
    ddl = SCHEMA_SQL.read_text(encoding="utf-8")
    start = ddl.find("CREATE TABLE IF NOT EXISTS tell_events")
    if start < 0:
        return
    con.executescript(ddl[start:])


def ticker_names(con: sqlite3.Connection) -> dict[str, str]:
    return {r[0]: r[1] or r[0] for r in con.execute("SELECT ticker, name FROM tickers")}


def load_stock_buys(con: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    by_code: dict[str, list[dict[str, Any]]] = defaultdict(list)
    rows = con.execute(
        """
        SELECT trade_id, filer, filer_id, chamber, ticker, asset, asset_type, side,
               amount_raw, trade_date
        FROM politician_trades
        WHERE side = 'purchase' AND ticker IS NOT NULL AND ticker != ''
        """
    )
    for r in rows:
        if is_opt(r[6], r[5]) or is_bond(r[6]):
            continue
        code = (r[4] or "").upper()
        if not chart(code):
            continue
        by_code[code].append(
            {
                "id": r[0],
                "filer": r[1],
                "filer_id": r[2],
                "chamber": r[3],
                "ticker": code,
                "asset": r[5],
                "asset_type": r[6],
                "amount": r[8] or "",
                "trade_date": r[9] or "",
            }
        )
    return by_code


def compute_tells(con: sqlite3.Connection) -> dict[str, Any] | None:
    root = prices_dir()
    if not root.exists():
        log("No prices/ directory; skip tells.")
        return None

    lookup = ticker_names(con)
    by_code = load_stock_buys(con)
    log(f"tells tickers {len(by_code)}")
    today = datetime.now(timezone.utc).date()
    recent_cut = (today - timedelta(days=RECENT)).isoformat()

    stock_buys: list[dict] = []
    px: dict[str, Series] = {}
    scored: dict[str, int] = defaultdict(int)
    hits: dict[str, list[dict]] = defaultdict(list)
    names: dict[str, str] = {}
    chambers: dict[str, str] = {}

    for n, (code, rows_t) in enumerate(by_code.items(), 1):
        try:
            s = load_one(code, root)
            if not s:
                continue
            px[code] = s
            if n % 100 == 0:
                log(f"  prices {n} / {len(by_code)} {code}")
            stock_buys.extend(rows_t)
            for t in rows_t:
                day = t.get("trade_date") or ""
                i = s.at(day)
                if i is None or i < 8:
                    continue
                buy = s.p[i]
                if buy <= 0:
                    continue
                fid = t.get("filer_id") or ""
                names[fid] = t.get("filer") or fid
                chambers[fid] = t.get("chamber") or ""
                fwd_n = min(FWD, len(s.p) - i - 1)
                if fwd_n < 12:
                    continue
                scored[fid] += 1
                prior = s.p[max(0, i - 20) : i + 1]
                near = buy <= min(prior) * NEAR
                window = s.p[i + 1 : i + 1 + fwd_n]
                mx = max(window)
                ret = mx / buy - 1
                if not (ret >= SPIKE and near):
                    continue
                spike_px = buy * (1 + SPIKE)
                days = next((k for k, p in enumerate(window, 1) if p >= spike_px), 99)
                if days > 15:
                    continue
                hits[fid].append(
                    {
                        "code": code,
                        "name": clean_name(lookup.get(code) or code, code),
                        "day": day,
                        "buy": round(buy, 2),
                        "high": round(mx, 2),
                        "ret": round(ret, 3),
                        "days": days,
                        "amount": t.get("amount") or "",
                        "highEnd": amount_high(t.get("amount") or ""),
                    }
                )
        except Exception as exc:
            log(f"tells skip {code}: {exc}")

    raw_events = [(fid, x) for fid, xs in hits.items() for x in xs]

    hands = []
    for fid, n in scored.items():
        try:
            h = hits.get(fid) or []
            best: dict[str, dict] = {}
            for x in h:
                prev = best.get(x["code"])
                if not prev or x["ret"] > prev["ret"]:
                    best[x["code"]] = x
            h = list(best.values())
            months = {x["day"][:7] for x in h}
            whale = any(x["highEnd"] >= 500000 for x in h)
            if n < MIN_SCORED:
                continue
            if len(h) < MIN_HITS and not whale:
                continue
            if len(months) < 2 and len(h) < 5 and not whale:
                continue
            rate = len(h) / max(n, 1)
            if rate < 0.08 and not whale:
                continue
            rets = sorted(x["ret"] for x in h)
            med = rets[len(rets) // 2]
            score = rate * math.log(1 + len(h)) * (1 + med)
            if whale:
                score *= 1.35
            h.sort(key=lambda x: -x["ret"])
            hands.append(
                {
                    "id": fid,
                    "name": names.get(fid, fid),
                    "chamber": chambers.get(fid, ""),
                    "scored": n,
                    "hits": len(h),
                    "rate": round(rate, 3),
                    "median": round(med, 3),
                    "score": round(score, 3),
                    "whale": whale,
                    "tells": h[:4],
                    "all_tells": h,
                }
            )
        except Exception as exc:
            log(f"tells skip hand {fid}: {exc}")
    hands.sort(key=lambda x: -x["score"])
    hands = hands[:HANDS_CAP]
    hot_ids = {h["id"]: h for h in hands}

    now_map: dict[str, dict] = {}
    for t in stock_buys:
        fid = t.get("filer_id") or ""
        if fid not in hot_ids:
            continue
        day = t.get("trade_date") or ""
        if day < recent_cut:
            continue
        code = (t.get("ticker") or "").upper()
        s = px.get(code)
        if not s:
            continue
        i = s.at(day)
        if i is None:
            continue
        buy = s.p[i]
        last = s.p[-1]
        if buy <= 0:
            continue
        chg = last / buy - 1
        prior = s.p[max(0, i - 20) : i + 1]
        near = buy <= min(prior) * NEAR if prior else False
        rec = now_map.get(code) or {
            "code": code,
            "name": clean_name(lookup.get(code) or code, code),
            "last": round(last, 2),
            "pxAsof": s.d[-1],
            "lastBuy": day,
            "buyPx": round(buy, 2),
            "chg": chg,
            "nearLow": near,
            "hands": [],
            "highEnd": 0,
        }
        if day > rec["lastBuy"]:
            rec["lastBuy"] = day
            rec["buyPx"] = round(buy, 2)
            rec["chg"] = chg
            rec["nearLow"] = near
        rec["highEnd"] += amount_high(t.get("amount") or "")
        if not any(h["id"] == fid for h in rec["hands"]):
            rec["hands"].append(
                {
                    "id": fid,
                    "name": hot_ids[fid]["name"],
                    "rate": hot_ids[fid]["rate"],
                    "hits": hot_ids[fid]["hits"],
                }
            )
        now_map[code] = rec

    now_list = []
    for rec in now_map.values():
        try:
            hand_score = sum(hot_ids[h["id"]]["score"] for h in rec["hands"])
            recency = max(0, 1.2 - (today - datetime.fromisoformat(rec["lastBuy"]).date()).days / 50)
            rec["open"] = rec["chg"] < OPEN_CAP
            rec["score"] = round(
                hand_score * recency * (1.35 if rec["open"] else 0.55) * (1.2 if rec["nearLow"] else 1),
                3,
            )
            rec["chg"] = round(rec["chg"], 4)
            if rec["chg"] < 0:
                tail = " still under water"
            elif rec["open"]:
                tail = " has not run away yet"
            else:
                tail = " already ran after the print"
            rec["why"] = (
                ", ".join(h["name"] for h in rec["hands"][:3])
                + " timed spikes before; latest buy "
                + rec["lastBuy"]
                + tail
            )
            now_list.append(rec)
        except Exception as exc:
            log(f"tells skip now {rec.get('code')}: {exc}")
    now_list.sort(key=lambda x: -x["score"])
    now_list = now_list[:NOW_CAP]

    px_asof = last_us_session_close([s.d[-1] for s in px.values()])
    tape = None
    try:
        tape = con.execute("SELECT value FROM meta WHERE key='tape_collected'").fetchone()
    except sqlite3.Error:
        tape = None
    tape_collected = (tape[0] if tape else None) or peek_collected()

    persist(con, raw_events, hands, now_list)
    out = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "tapeCollected": tape_collected,
        "priceAsof": px_asof,
        "rules": {
            "spike": SPIKE,
            "sessions": FWD,
            "nearLow": 0.08,
            "openCap": OPEN_CAP,
        },
        "disclaimer": DISCLAIMER,
        "hands": [
            {k: h[k] for k in ("id", "name", "chamber", "scored", "hits", "rate", "median", "score", "tells")}
            for h in hands
        ],
        "now": now_list,
    }
    log(f"tells hands {len(hands)} now {len(now_list)} codes {len(px)}")
    return out


def persist(
    con: sqlite3.Connection,
    raw_events: list[tuple[str, dict]],
    hands: list[dict],
    now_list: list[dict],
) -> None:
    ensure_tell_tables(con)
    con.execute("DELETE FROM tell_now_hands")
    con.execute("DELETE FROM tell_now")
    con.execute("DELETE FROM tell_hand_tells")
    con.execute("DELETE FROM tell_hands")
    con.execute("DELETE FROM tell_events")
    con.executemany(
        """
        INSERT INTO tell_events(filer_id, ticker, name, day, buy, high, ret, days, amount_raw, high_end)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (fid, x["code"], x["name"], x["day"], x["buy"], x["high"], x["ret"], x["days"], x["amount"], x["highEnd"])
            for fid, x in raw_events
        ],
    )
    for rank, h in enumerate(hands, 1):
        con.execute(
            """
            INSERT INTO tell_hands(filer_id, name, chamber, scored, hits, rate, median, score, whale, rank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                h["id"],
                h["name"],
                h["chamber"],
                h["scored"],
                h["hits"],
                h["rate"],
                h["median"],
                h["score"],
                1 if h.get("whale") else 0,
                rank,
            ),
        )
        con.executemany(
            """
            INSERT INTO tell_hand_tells(
              filer_id, seq, ticker, name, day, buy, high, ret, days, amount_raw, high_end
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (h["id"], i, t["code"], t["name"], t["day"], t["buy"], t["high"], t["ret"], t["days"], t["amount"], t["highEnd"])
                for i, t in enumerate(h["tells"])
            ],
        )
    for rank, rec in enumerate(now_list, 1):
        con.execute(
            """
            INSERT INTO tell_now(
              ticker, name, last, px_asof, last_buy, buy_px, chg, near_low, open,
              high_end, score, why, rank
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rec["code"],
                rec["name"],
                rec["last"],
                rec["pxAsof"],
                rec["lastBuy"],
                rec["buyPx"],
                rec["chg"],
                1 if rec["nearLow"] else 0,
                1 if rec["open"] else 0,
                rec["highEnd"],
                rec["score"],
                rec["why"],
                rank,
            ),
        )
        con.executemany(
            """
            INSERT INTO tell_now_hands(ticker, filer_id, name, rate, hits)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(rec["code"], h["id"], h["name"], h["rate"], h["hits"]) for h in rec["hands"]],
        )


def write_tells_json(payload: dict[str, Any]) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2) + "\n"
    TELLS_JSON.write_text(text, encoding="utf-8")
    (EXPORT_DIR / "tells.json").write_text(text, encoding="utf-8")
    log(f"Wrote {TELLS_JSON}")


def run(con: sqlite3.Connection) -> dict[str, Any] | None:
    payload = compute_tells(con)
    if payload is None:
        return None
    write_tells_json(payload)
    return payload


def main() -> int:
    if not DB_PATH.exists():
        log(f"missing {DB_PATH}; run rebuild.py first")
        return 1
    con = sqlite3.connect(str(DB_PATH))
    con.row_factory = sqlite3.Row
    try:
        run(con)
        con.commit()
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
