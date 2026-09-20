"""Build insider-analysis.json: landed officer buys, heat, TA, copy-trade returns."""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from atomic_io import atomic_write_json, require_json

SITE = Path(__file__).resolve().parent.parent
PRICES = SITE / "prices"
HALF = 10
WIN = 90


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_prices(code: str) -> list[tuple[str, float]]:
    path = PRICES / f"{code}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if data.get("missing") or not data.get("c"):
        return []
    return [(d, float(p)) for d, p in data["c"]]


def nearest(closes: list[tuple[str, float]], day: str) -> float | None:
    if not day:
        return None
    pick = None
    for d, p in closes:
        if d <= day:
            pick = p
        else:
            break
    if pick is None and closes:
        pick = closes[0][1]
    return pick


def later(closes: list[tuple[str, float]], day: str) -> tuple[str, float] | None:
    """First close on/after day. None if the tape has no session yet."""
    if not day:
        return None
    for d, p in closes:
        if d >= day:
            return d, p
    return None


FILL_VS_CLOSE = 0.20
PATHOLOGICAL_DIVERGENCE = 0.50
SAME_DAY_FILL_RATIO = 1.50


def parse_fill(val) -> float | None:
    try:
        px = float(val) if val not in (None, "") else None
    except (TypeError, ValueError):
        return None
    if px is None or px <= 0:
        return None
    return px


def parse_shares(val) -> float:
    try:
        sh = float(val or 0)
    except (TypeError, ValueError):
        return 0.0
    return sh if sh > 0 else 0.0


def fill_matches_close(
    fill: float | None, close: float, max_div: float = FILL_VS_CLOSE
) -> bool:
    """True when there is no fill, or fill sits within max_div of close."""
    if fill is None:
        return True
    if close <= 0:
        return False
    return abs(fill / close - 1.0) <= max_div


def fill_is_pathological(
    fill: float | None, close: float, max_div: float = PATHOLOGICAL_DIVERGENCE
) -> bool:
    """True when a reported fill is more than 50% from the prices/ close."""
    if fill is None or close <= 0:
        return False
    return abs(fill / close - 1.0) > max_div


def conflicted_fill_days(rows: list[dict]) -> set[tuple[str, str]]:
    """(ticker, trade_date) keys whose same-day fills disagree by more than 50%."""
    groups: dict[tuple[str, str], list[float]] = defaultdict(list)
    for t in rows:
        code = (t.get("ticker") or "").upper()
        day = t.get("trade_date") or ""
        fill = parse_fill(t.get("price"))
        if code and day and fill:
            groups[(code, day)].append(fill)
    bad: set[tuple[str, str]] = set()
    for key, fills in groups.items():
        if len(fills) >= 2 and max(fills) / min(fills) > SAME_DAY_FILL_RATIO:
            bad.add(key)
    return bad


def lot_px(
    closes: list[tuple[str, float]],
    day: str,
    fill: float | None,
    conflicted: bool = False,
) -> float | None:
    """Book a lot on the prices/ tape only.

    Entry, exit, and mark-to-market all use later closes from the same file.
    A reported fill is a universe check, not the book price. Mild gaps still
    book at the close; a fill more than 50% away is treated as dual-listed /
    ADR and skipped. Same-day fills that disagree by more than 50% keep only
    the cluster within 20% of the close.
    """
    hit = later(closes, day)
    if not hit or hit[1] <= 0:
        return None
    close = hit[1]
    if fill_is_pathological(fill, close):
        return None
    if conflicted and (fill is None or not fill_matches_close(fill, close)):
        return None
    return close


def rsi(vals: list[float], n: int = 14) -> float | None:
    if len(vals) < n + 1:
        return None
    gains = losses = 0.0
    for i in range(-n, 0):
        d = vals[i] - vals[i - 1]
        if d >= 0:
            gains += d
        else:
            losses -= d
    ag, al = gains / n, losses / n
    if al == 0:
        return 100.0
    return round(100 - 100 / (1 + ag / al), 1)


def sma(vals: list[float], n: int) -> float | None:
    if len(vals) < n:
        return None
    return round(sum(vals[-n:]) / n, 2)


def ret_pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return round((new / old - 1) * 100, 1)


def resample(closes: list[tuple[str, float]], kind: str) -> list[float]:
    buckets: dict[str, float] = {}
    for d, p in closes:
        dt = datetime.strptime(d, "%Y-%m-%d")
        key = dt.strftime("%Y-%W") if kind == "W" else dt.strftime("%Y-%m")
        buckets[key] = p
    return [buckets[k] for k in sorted(buckets)]


def ta_block(closes: list[tuple[str, float]]) -> dict:
    if len(closes) < 30:
        return {}
    last_d, last = closes[-1]
    w = resample(closes, "W")
    m = resample(closes, "M")
    w20, w40 = sma(w, 20), sma(w, 40)
    m10 = sma(m, 10)
    wr, mr = rsi(w), rsi(m)
    r4 = ret_pct(w[-1], w[-5]) if len(w) >= 5 else None
    r13 = ret_pct(w[-1], w[-14]) if len(w) >= 14 else None
    hi20 = max(w[-20:]) if len(w) >= 20 else max(w)
    lo8 = min(w[-8:]) if len(w) >= 8 else min(w)
    from_hi = ret_pct(last, hi20)
    vs20 = ret_pct(last, w20)
    vs10m = ret_pct(last, m10)
    if w20 and w40 and last > w20 > w40:
        wtrend = "UP"
    elif w20 and last < w20:
        wtrend = "DOWN"
    else:
        wtrend = "MIX"
    mtrend = "UP" if (m10 and last > m10) else ("DOWN" if m10 and last < m10 else "MIX")
    return {
        "last": round(last, 2),
        "asof": last_d,
        "weekly": {
            "rsi": wr, "sma20": w20, "sma40": w40, "vs20": vs20,
            "ret4w": r4, "ret13w": r13, "fromHigh": from_hi, "trend": wtrend,
            "recentLow": round(lo8, 2),
        },
        "monthly": {"rsi": mr, "sma10": m10, "vs10": vs10m, "trend": mtrend},
        "invalidation": round(lo8, 2),
    }


def decay(days: float) -> float:
    return 2 ** (-days / HALF)


def ret_after(closes: list[tuple[str, float]], start: str, days: int) -> float | None:
    entry = later(closes, start)
    if not entry:
        return None
    target = (datetime.fromisoformat(entry[0]) + timedelta(days=days)).date().isoformat()
    px = nearest(closes, target)
    if not px or not entry[1]:
        return None
    return round((px / entry[1]) - 1, 4)


def copy_leaders(trades: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """FIFO copy book on prices/ closes: first close on/after each trade date."""
    px_cache: dict[str, list[tuple[str, float]]] = {}

    def closes(code: str) -> list[tuple[str, float]]:
        if code not in px_cache:
            px_cache[code] = load_prices(code)
        return px_cache[code]

    by_filer: dict[str, list[dict]] = defaultdict(list)
    by_ticker: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if t.get("side") not in ("purchase", "sale", "sale_post"):
            continue
        fid = t.get("filer_id") or ""
        code = (t.get("ticker") or "").upper()
        if not fid or not code:
            continue
        by_filer[fid].append(t)
        by_ticker[code].append(t)

    hands = []
    for fid, rows in by_filer.items():
        rows = sorted(rows, key=lambda r: ((r.get("trade_date") or ""), 0 if r.get("side") == "purchase" else 1))
        lots: dict[str, list[dict]] = defaultdict(list)
        realized = invested = 0.0
        n_buy = n_sell = 0
        last = ""
        name = rows[0].get("filer") or fid
        title = rows[0].get("title") or ""
        tickers: set[str] = set()
        conflicted = conflicted_fill_days(rows)
        for t in rows:
            code = (t.get("ticker") or "").upper()
            day = t.get("trade_date") or ""
            c = closes(code)
            fill = parse_fill(t.get("price"))
            px = lot_px(c, day, fill, (code, day) in conflicted)
            sh = parse_shares(t.get("shares"))
            if not px or sh <= 0:
                continue
            tickers.add(code)
            if t.get("side") == "purchase":
                lots[code].append({"shares": sh, "px": float(px)})
                invested += sh * float(px)
                n_buy += 1
            else:
                n_sell += 1
                left = sh
                while left > 0 and lots[code]:
                    lot = lots[code][0]
                    take = min(left, lot["shares"])
                    realized += (float(px) - lot["px"]) * take
                    lot["shares"] -= take
                    left -= take
                    if lot["shares"] <= 1e-9:
                        lots[code].pop(0)
            if day > last:
                last = day
        if invested < 1000 or n_buy == 0:
            continue
        unreal = 0.0
        open_n = 0
        for code, ls in lots.items():
            c = closes(code)
            if not c:
                continue
            last_px = c[-1][1]
            for lot in ls:
                if lot["shares"] > 0:
                    unreal += (last_px - lot["px"]) * lot["shares"]
                    open_n += 1
        total = realized + unreal
        hands.append({
            "id": fid,
            "name": name,
            "title": title,
            "nBuy": n_buy,
            "nSell": n_sell,
            "n": n_buy + n_sell,
            "nTickers": len(tickers),
            "invested": round(invested),
            "realized": round(realized),
            "unrealized": round(unreal),
            "pnl": round(total),
            "ret": round(total / invested, 4) if invested else 0.0,
            "last": last,
            "open": open_n,
        })
    hands.sort(key=lambda r: (-r["pnl"], -r["ret"]))
    for i, row in enumerate(hands, 1):
        row["rank"] = i

    active_filers = sorted(hands, key=lambda r: (-r["n"], -r["invested"]))
    for i, row in enumerate(active_filers, 1):
        row = dict(row)
        row["rank"] = i
        active_filers[i - 1] = row

    names = []
    for code, rows in by_ticker.items():
        buys = [t for t in rows if t.get("side") == "purchase"]
        sells = [t for t in rows if t.get("side") == "sale"]
        buy_val = sum(float(t.get("value") or 0) for t in buys)
        sell_val = sum(float(t.get("value") or 0) for t in sells)
        people = {(t.get("filer_id"), t.get("filer")) for t in rows}
        last = max((t.get("trade_date") or "") for t in rows)
        names.append({
            "code": code,
            "name": (rows[0].get("company") or code),
            "n": len(rows),
            "nBuy": len(buys),
            "nSell": len(sells),
            "nFilers": len(people),
            "buyHigh": round(buy_val),
            "sellHigh": round(sell_val),
            "last": last,
            "filers": [{"id": i, "name": n} for i, n in list(people)[:6] if i],
        })
    names.sort(key=lambda r: (-r["n"], -r["buyHigh"]))
    for i, row in enumerate(names, 1):
        row["rank"] = i
    return hands, active_filers, names


def parse_dt(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        try:
            return datetime.fromisoformat(iso[:10])
        except ValueError:
            return None


def main() -> None:
    tape = require_json(SITE / "insider-trades.json")
    now = datetime.now(timezone.utc)
    today = now.date()
    cut = (today - timedelta(days=WIN)).isoformat()
    trades = tape.get("trades") or []

    by: dict[str, dict] = {}
    for t in trades:
        if t.get("side") not in ("purchase", "sale", "sale_post"):
            continue
        code = (t.get("ticker") or "").upper()
        if not code:
            continue
        rec = by.get(code) or {
            "code": code,
            "name": t.get("company") or code,
            "commodity": t.get("commodity") or "",
            "buys": [],
            "sells": [],
            "landed_buys": [],
        }
        if t.get("company"):
            rec["name"] = t["company"]
        td = t.get("trade_date") or ""
        if td >= cut:
            if t.get("side") == "purchase":
                rec["buys"].append(t)
            else:
                rec["sells"].append(t)
        added = parse_dt(t.get("added") or "")
        if t.get("side") == "purchase" and added and (now - added) <= timedelta(hours=72):
            rec["landed_buys"].append(t)
        by[code] = rec

    heat_rows = []
    for code, rec in by.items():
      try:
        stock_buys = rec["buys"]
        if not stock_buys:
            continue
        filers: dict[str, dict] = {}
        buy_val = sell_val = 0.0
        ceo = False
        for t in rec["sells"]:
            sell_val += float(t.get("value") or 0)
        for t in stock_buys:
            val = float(t.get("value") or 0)
            buy_val += val
            if t.get("ceo"):
                ceo = True
            f = filers.get(t.get("filer_id") or "") or {
                "id": t.get("filer_id"), "name": t.get("filer"),
                "title": t.get("title") or "", "high": 0.0, "last": "", "n": 0, "ceo": False,
            }
            f["high"] += val
            f["n"] += 1
            f["ceo"] = f["ceo"] or bool(t.get("ceo"))
            if t.get("title"):
                f["title"] = t["title"]
            if (t.get("trade_date") or "") > f["last"]:
                f["last"] = t.get("trade_date") or ""
            filers[t.get("filer_id") or ""] = f
        buyers = [f for f in filers.values() if f["high"] > 0 or f["n"] > 0]
        if not buyers:
            continue
        recency = 0.0
        for b in buyers:
            last = b["last"]
            try:
                days = max(0, (today - datetime.fromisoformat(last).date()).days)
            except ValueError:
                days = 90
            recency += math.log10(1 + max(b["high"], 1) / 1000) * decay(days)
            if b.get("ceo"):
                recency *= 1.25
        n_buyers = len(buyers)
        last_buy = max(b["last"] for b in buyers if b["last"]) if buyers else ""
        last_filed = ""
        for t in stock_buys:
            if (t.get("trade_date") or "") == last_buy:
                fd = t.get("filed_date") or ""
                if fd > last_filed:
                    last_filed = fd
        mixed = 0.85 if sell_val > buy_val * 0.5 else 1
        cluster = n_buyers >= 2
        heat = recency * (1 + math.log(1 + n_buyers)) * mixed * (1.2 if cluster else 1)
        landed_at = max((t.get("added") or "") for t in rec["landed_buys"]) if rec["landed_buys"] else ""
        heat_rows.append({
            "code": code,
            "name": rec["name"],
            "commodity": rec["commodity"],
            "heat": heat,
            "nBuyers": n_buyers,
            "buyHigh": round(buy_val),
            "sellHigh": round(sell_val),
            "lastBuy": last_buy,
            "lastFiled": last_filed,
            "buyers": sorted(buyers, key=lambda x: -x["high"]),
            "ceo": ceo,
            "whale": buy_val >= 250000,
            "landed": bool(rec["landed_buys"]),
            "landedAt": landed_at,
            "cluster": cluster,
        })
      except Exception as exc:
        print(f"insider analysis skip heat {code}: {exc}", flush=True)
    heat_rows.sort(key=lambda r: (-r["heat"], r["lastBuy"]))
    rank_of = {r["code"]: i + 1 for i, r in enumerate(heat_rows)}

    book = []
    performance = []
    asof_dates = []
    for row in heat_rows:
      try:
        code = row["code"]
        closes = load_prices(code)
        try:
            tech = ta_block(closes) if closes else {}
        except Exception:
            tech = {}
        last = tech.get("last") if tech else (closes[-1][1] if closes else None)
        px_asof = tech.get("asof") if tech else (closes[-1][0] if closes else "")
        if px_asof:
            asof_dates.append(px_asof)
        buy_px = nearest(closes, row["lastBuy"]) if closes else None
        chg = round((last - buy_px) / buy_px, 4) if last and buy_px else None
        copy_day = row.get("lastFiled") or (
            row.get("landedAt")[:10] if row.get("landedAt") else row["lastBuy"]
        )
        copy_entry = later(closes, copy_day) if closes else None
        ret7 = ret_after(closes, row["lastBuy"], 7) if closes else None
        ret30 = ret_after(closes, row["lastBuy"], 30) if closes else None
        ret90 = ret_after(closes, row["lastBuy"], 90) if closes else None
        copy_ret = None
        if copy_entry and last and copy_entry[1]:
            copy_ret = round((last / copy_entry[1]) - 1, 4)

        performance.append({
            "code": code,
            "name": row["name"],
            "lastBuy": row["lastBuy"],
            "buyPx": buy_px,
            "last": last,
            "ret7": ret7,
            "ret30": ret30,
            "ret90": ret90,
            "retSince": chg,
            "copyRet": copy_ret,
            "nBuyers": row["nBuyers"],
            "buyHigh": row["buyHigh"],
            "ceo": row["ceo"],
            "cluster": row["cluster"],
            "landed": row["landed"],
        })

        if not tech:
            continue
        w, mth = tech["weekly"], tech["monthly"]
        wr, mr = w.get("rsi"), mth.get("rsi")
        vs20, vs10 = w.get("vs20"), mth.get("vs10")
        lag = None
        if row["landedAt"] and row["lastBuy"]:
            try:
                ad = parse_dt(row["landedAt"])
                tr = datetime.fromisoformat(row["lastBuy"])
                if ad:
                    lag = max(0, (ad.date() - tr.date()).days)
            except ValueError:
                lag = None

        score = 0.0
        bits = []
        if row["landed"]:
            score += 2.6
            bits.append("Landed")
        if row["whale"]:
            score += 0.9
            bits.append("Size")
        if row["ceo"]:
            score += 1.4
            bits.append("CEO")
        if row["cluster"]:
            score += 1.3
            bits.append("Cluster")
        top = heat_rows[0]["heat"] if heat_rows else 1
        score += min(2.2, (row["heat"] / max(top, 0.001)) * 2.2)
        try:
            days_ago = max(0, (today - datetime.fromisoformat(row["lastBuy"]).date()).days)
        except ValueError:
            days_ago = 90
        score += max(0, 1.4 - days_ago / 40)

        monthly_ok = (mr or 0) >= 45 or (vs10 is not None and vs10 >= -8)
        oversold = wr is not None and 20 <= wr <= 48 and monthly_ok
        extended = wr is not None and wr >= 72
        chase = chg is not None and chg > 0.12 and w.get("trend") == "DOWN"
        if oversold:
            score += 2.0
            bits.append("Weekly washout")
        if monthly_ok:
            score += 0.5
            bits.append("Monthly intact")
        if chase:
            score -= 2.2
            bits.append("Already ran")
        if extended:
            score -= 1.6
            bits.append("Weekly hot")
        if sell_heavy := (row["sellHigh"] > row["buyHigh"] * 1.5):
            score -= 1.1
            bits.append("Net selling")

        if score >= 5.2 and not chase and not extended:
            action = "buy-dip"
            why = ", ".join(bits[:4]) or "Officer buying"
        elif chase or extended or sell_heavy:
            action = "avoid"
            why = ", ".join(bits[:4]) or "Extended or mixed"
        else:
            action = "watch"
            why = ", ".join(bits[:4]) or "Officer flow"

        book.append({
            "code": code,
            "name": row["name"],
            "action": action,
            "score": round(score, 3),
            "why": why,
            "last": last,
            "pxAsof": px_asof,
            "buyPx": buy_px,
            "chgSinceBuy": chg,
            "lastBuy": row["lastBuy"],
            "nBuyers": row["nBuyers"],
            "buyHigh": row["buyHigh"],
            "heatRank": rank_of.get(code),
            "heat": round(row["heat"], 3),
            "landed": row["landed"],
            "landedAt": row["landedAt"],
            "lagDays": lag,
            "ceo": row["ceo"],
            "whale": row["whale"],
            "cluster": row["cluster"],
            "buyers": [{"name": b["name"], "id": b["id"], "title": b.get("title") or ""} for b in row["buyers"][:8]],
            "weekly": w,
            "monthly": mth,
            "invalidation": tech.get("invalidation"),
            "flags": bits,
            "commodity": row["commodity"],
        })
      except Exception as exc:
        print(f"insider analysis skip book {row.get('code')}: {exc}", flush=True)

    book.sort(key=lambda r: (-r["score"], r["code"]))
    for i, row in enumerate(book, 1):
        row["rank"] = i

    performance = [p for p in performance if (p.get("buyHigh") or 0) >= 5000]
    performance.sort(key=lambda r: (
        -(r["copyRet"] if r["copyRet"] is not None else -99),
        -(r["retSince"] if r["retSince"] is not None else -99),
    ))
    for i, row in enumerate(performance, 1):
        row["rank"] = i

    heat_out = []
    for i, row in enumerate(heat_rows, 1):
        heat_out.append({
            "rank": i,
            "code": row["code"],
            "name": row["name"],
            "heat": round(row["heat"], 3),
            "nBuyers": row["nBuyers"],
            "buyHigh": row["buyHigh"],
            "lastBuy": row["lastBuy"],
            "buyers": [{"name": b["name"], "id": b["id"], "title": b.get("title") or ""} for b in row["buyers"][:8]],
            "ceo": row["ceo"],
            "whale": row["whale"],
            "landed": row["landed"],
            "cluster": row["cluster"],
            "commodity": row["commodity"],
        })

    from fetch_prices import last_us_session_close
    price_asof = last_us_session_close(asof_dates) if asof_dates else ""
    leaders, active_filers, active_names = copy_leaders(trades)

    out = {
        "generated": iso_now(),
        "tapeCollected": tape.get("collected") or "",
        "priceAsof": price_asof,
        "windowDays": WIN,
        "disclaimer": (
            "Not a recommendation. US prints are Form 4 open-market P/S "
            "(awards, exercises, and sale-post-exercise are excluded from buy heat). "
            "Canadian names use public-market SEDI prints (nature code 10). "
            "Copy return is from the first close on/after the filing date. "
            "Leaders copy book prices every lot from the same prices/ closes: "
            "entry and exit are the first close on/after the trade date; leftovers "
            "mark to the last close. Reported fills are a universe check — prints "
            "more than 50% from that close are dropped (dual-listed / ADR). "
            "Same-day fills that disagree by more than 50% keep only the cluster "
            "within 20% of the close."
        ),
        "book": book,
        "heat": heat_out,
        "performance": performance,
        "leaders": leaders,
        "activeFilers": active_filers,
        "activeNames": active_names,
        "stats": {
            "names": len(heat_rows),
            "landed": sum(1 for r in heat_rows if r["landed"]),
            "clusters": sum(1 for r in heat_rows if r["cluster"]),
            "ceoBuys": sum(1 for r in heat_rows if r["ceo"]),
            "trades": len(trades),
            "buys": sum(1 for t in trades if t.get("side") == "purchase"),
        },
    }
    atomic_write_json(SITE / "insider-analysis.json", out, ensure_ascii=False)
    print(
        f"insider analysis book={len(book)} heat={len(heat_out)} "
        f"perf={len(performance)} leaders={len(leaders)} asof={price_asof}",
        flush=True,
    )


if __name__ == "__main__":
    main()
