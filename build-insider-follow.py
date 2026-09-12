#!/usr/bin/env python3
"""Build insider-follow.json: best-to-follow officers + new-print alerts.

Follow list = 90-day repeatable ranking (N=5 clustered open-market buys,
equal-weight names, copy as-of filed_date; scheduled 10b5-1 buys already
dropped) plus cheap trust filters:
  90d hit rate >= 70%
  average 90d return > 0
One-company officers still qualify.

Rank then down-weights officers whose remaining Form 4 market prints are
mostly scheduled-plan lots, and prefers size-vs-remaining-stake conviction.

Alerts fire when a follow-list officer has a new market buy or sale since
the last tapeCollected stamp. First run bootstraps the last 7 filed days
so the ship is not an empty board. A later run on the same tape keeps
those alerts (price refreshes must not wipe them).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPEATABLE = ROOT / "insider-repeatable.json"
TAPE = ROOT / "insider-trades-lite.json"
FORM4 = ROOT / "insider-form4.json"
DEST = ROOT / "insider-follow.json"

HORIZON = 90
MIN_HIT = 0.70
MIN_AVG = 0.0
BOOTSTRAP_DAYS = 7
# Officers at or above this Form 4 plan-print share sort below discretionary peers.
PLAN_HEAVY = 0.50

AWARD_CODES = {"A", "30", "45", "46"}
EXERCISE_CODES = {"M", "X", "51", "54", "57", "59", "71"}
AWARD_NATURE = re.compile(r"^(30|45|46)\b")
EXERCISE_NATURE = re.compile(r"^(51|54|57|59|71)\b")


def is_award(t: dict) -> bool:
    if t.get("side") == "award":
        return True
    code = str(t.get("code") or "").upper()
    nature = str(t.get("nature") or "")
    return code in AWARD_CODES or bool(AWARD_NATURE.match(nature))


def is_exercise(t: dict) -> bool:
    if t.get("side") == "exercise":
        return True
    code = str(t.get("code") or "").upper()
    nature = str(t.get("nature") or "")
    return code in EXERCISE_CODES or bool(EXERCISE_NATURE.match(nature))


def is_sale_post(t: dict) -> bool:
    if t.get("side") == "sale_post":
        return True
    return str(t.get("code") or "").upper() == "F"


def is_market_print(t: dict) -> bool:
    """Open-market buy or sale — the prints you would copy or fade."""
    if not t or is_award(t) or is_exercise(t):
        return False
    side = t.get("side")
    if side == "purchase":
        code = str(t.get("code") or "").upper()
        if code in {"G", "W", "D", "J", "U"}:
            return False
        origin = str(t.get("origin") or "").lower()
        nature = str(t.get("nature") or "")
        if origin == "sedi":
            return nature == "10" or nature.startswith("10")
        return code == "P"
    return side in {"sale", "sale_post"} or is_sale_post(t)


def cluster_key(t: dict) -> str:
    return "|".join([
        str(t.get("filer_id") or ""),
        str(t.get("ticker") or "").upper(),
        str(t.get("filed_date") or "")[:10],
        "sale" if (t.get("side") in {"sale", "sale_post"} or is_sale_post(t)) else "purchase",
    ])


def load_json(path: Path):
    with path.open() as f:
        return json.load(f)


def load_form4(path: Path | None = None) -> dict:
    p = path or FORM4
    if not p.is_file():
        return {}
    try:
        data = load_json(p)
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return data if isinstance(data, dict) else {}


def num(raw) -> float | None:
    if raw is None or raw == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def vs_stake(shares, after, acquired: bool) -> float | None:
    if shares is None or after is None or shares < 0 or after < 0:
        return None
    if acquired:
        return None if after <= 0 else shares / after
    prior = after + shares
    if prior <= 0:
        return 1.0 if shares else None
    return shares / prior


def overlay_for(t: dict, form4: dict) -> dict:
    trades = form4.get("trades") if form4 else None
    if not isinstance(trades, dict):
        return {}
    ov = trades.get(t.get("id") or "")
    return ov if isinstance(ov, dict) else {}


def trade_conviction(t: dict, form4: dict) -> float | None:
    ov = overlay_for(t, form4)
    if ov.get("vs") is not None:
        return num(ov.get("vs"))
    shares = num(t.get("shares"))
    after = num(ov.get("after"))
    if after is None:
        after = num(t.get("shares_after"))
    return vs_stake(shares, after, t.get("side") == "purchase")


def is_plan_print(t: dict, form4: dict) -> bool:
    return bool(overlay_for(t, form4).get("plan"))


def median(xs: list[float]) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    mid = len(ys) // 2
    if len(ys) % 2:
        return ys[mid]
    return (ys[mid - 1] + ys[mid]) / 2.0


def filer_form4(form4: dict, filer_id: str) -> dict:
    filers = form4.get("filers") if form4 else None
    if not isinstance(filers, dict):
        return {}
    row = filers.get(filer_id) or {}
    return row if isinstance(row, dict) else {}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tape", type=Path, default=TAPE)
    p.add_argument("--repeatable", type=Path, default=REPEATABLE)
    p.add_argument("--dest", type=Path, default=DEST)
    p.add_argument("--prev", type=Path, default=None, help="Previous follow JSON (default: dest)")
    p.add_argument("--bootstrap-days", type=int, default=BOOTSTRAP_DAYS)
    return p.parse_args(argv)


def pick_follow(repeatable: dict, tape_trades: list[dict] | None = None, form4: dict | None = None) -> list[dict]:
    form4 = form4 or {}
    tape_trades = tape_trades or []
    conv_by: dict[str, list[float]] = defaultdict(list)
    after_by: dict[str, tuple[str, float]] = {}
    for t in tape_trades:
        fid = t.get("filer_id") or ""
        if not fid:
            continue
        if is_plan_print(t, form4):
            continue
        if t.get("side") != "purchase":
            continue
        if is_award(t) or is_exercise(t):
            continue
        conv = trade_conviction(t, form4)
        if conv is not None:
            conv_by[fid].append(conv)
        after = overlay_for(t, form4).get("after")
        if after is None:
            after = t.get("shares_after")
        after_n = num(after)
        filed = str(t.get("filed_date") or "")[:10]
        if after_n is not None and filed >= after_by.get(fid, ("", 0.0))[0]:
            after_by[fid] = (filed, after_n)

    rows = []
    for raw in repeatable.get("filers") or []:
        w = raw.get(str(HORIZON))
        if not w:
            continue
        hit = w.get("hit")
        names = w.get("names")
        avg = w.get("avg")
        if hit is None or names is None or avg is None:
            continue
        if hit < MIN_HIT or avg <= MIN_AVG:
            continue
        fid = raw.get("id") or ""
        f4 = filer_form4(form4, fid)
        plan_share = f4.get("planShare")
        if plan_share is None:
            plan_share = 0.0
        conv = f4.get("conv")
        if conv is None:
            conv = median(conv_by.get(fid) or [])
        after = f4.get("after")
        if after is None and fid in after_by:
            after = after_by[fid][1]
        row = {
            "id": fid,
            "name": raw.get("name") or fid,
            "title": raw.get("title") or "",
            "firm": raw.get("firm") or "",
            "ticker": raw.get("ticker") or "",
            "n": w.get("n"),
            "names": names,
            "hit": hit,
            "avg": avg,
            "vs": w.get("vs"),
            "repRank": raw.get("rank"),
            "planShare": round(float(plan_share), 4),
            "plan": f4.get("plan") or 0,
            "disc": f4.get("disc") if f4.get("disc") is not None else None,
            "planHeavy": bool(float(plan_share) >= PLAN_HEAVY),
        }
        if conv is not None:
            row["conv"] = round(float(conv), 4)
        if after is not None:
            row["after"] = after
            row["shares_held"] = after
        if f4.get("held") is not None:
            row["held"] = f4.get("held")
        rows.append(row)
    rows.sort(key=lambda r: (
        1 if r.get("planHeavy") else 0,
        -(r.get("conv") if r.get("conv") is not None else -1),
        -(r.get("avg") or -99),
        -(r.get("hit") or 0),
        -(r.get("n") or 0),
        r.get("name") or "",
    ))
    for i, row in enumerate(rows, 1):
        row["rank"] = i
    return rows


def cluster_prints(trades: list[dict], follow_ids: set[str], form4: dict | None = None) -> dict[str, dict]:
    form4 = form4 or {}
    clusters: dict[str, dict] = {}
    for t in trades:
        fid = t.get("filer_id") or ""
        if fid not in follow_ids or not is_market_print(t):
            continue
        filed = str(t.get("filed_date") or "")[:10]
        ticker = str(t.get("ticker") or "").upper()
        if not fid or not ticker or len(filed) < 10:
            continue
        key = cluster_key(t)
        side = "sale" if (t.get("side") in {"sale", "sale_post"} or is_sale_post(t)) else "purchase"
        prev = clusters.get(key)
        shares = t.get("shares")
        value = t.get("value")
        try:
            shares_n = float(shares) if shares is not None else 0.0
        except (TypeError, ValueError):
            shares_n = 0.0
        try:
            value_n = float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            value_n = 0.0
        ov = overlay_for(t, form4)
        plan = bool(ov.get("plan"))
        after = ov.get("after")
        if after is None:
            after = t.get("shares_after")
        vs = trade_conviction(t, form4)
        if prev is None:
            clusters[key] = {
                "id": t.get("id") or key,
                "key": key,
                "filer_id": fid,
                "filer": t.get("filer") or fid,
                "title": t.get("title") or "",
                "ticker": ticker,
                "company": t.get("company") or "",
                "side": side,
                "filed_date": filed,
                "trade_date": str(t.get("trade_date") or "")[:10],
                "shares": shares_n or None,
                "value": value_n or None,
                "amount": t.get("amount") or "",
                "lots": 1,
                "plan": plan,
                "why": ov.get("why") or "",
                "after": after,
                "vs": vs,
            }
        else:
            prev["lots"] += 1
            if shares_n:
                prev["shares"] = (prev.get("shares") or 0) + shares_n
            if value_n:
                prev["value"] = (prev.get("value") or 0) + value_n
            if t.get("amount") and not prev.get("amount"):
                prev["amount"] = t.get("amount")
            if t.get("company") and not prev.get("company"):
                prev["company"] = t.get("company")
            if plan:
                prev["plan"] = True
                if ov.get("why") and not prev.get("why"):
                    prev["why"] = ov.get("why")
            if after is not None:
                prev["after"] = after
            if vs is not None:
                prev["vs"] = vs
    for row in clusters.values():
        if row.get("shares") is not None:
            row["shares"] = round(row["shares"], 4)
        if row.get("value") is not None:
            row["value"] = round(row["value"], 2)
        if row.get("vs") is not None:
            row["vs"] = round(float(row["vs"]), 4)
    return clusters


def last_filed_by_id(clusters: dict[str, dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in clusters.values():
        fid = row["filer_id"]
        filed = row["filed_date"]
        if filed > out.get(fid, ""):
            out[fid] = filed
    return out


def choose_alerts(
    clusters: dict[str, dict],
    prev: dict | None,
    tape_asof: str,
    bootstrap_days: int,
) -> list[dict]:
    if not prev:
        try:
            cut = (datetime.strptime(tape_asof, "%Y-%m-%d") - timedelta(days=bootstrap_days)).strftime("%Y-%m-%d")
        except ValueError:
            cut = "9999-12-31"
        alerts = [row for row in clusters.values() if row["filed_date"] >= cut]
        alerts.sort(key=lambda r: (r["filed_date"], 0 if r.get("plan") else 1, r["filer"], r["ticker"]), reverse=True)
        return alerts

    prev_tape = prev.get("tapeCollected") or ""
    prev_asof = str(prev.get("asof") or "")[:10]
    prev_seen = set(prev.get("seen") or [])
    prev_ids = {f.get("id") for f in (prev.get("follow") or []) if f.get("id")}

    # Same tape: keep the last alert set. Price jobs must not clear them.
    if prev_tape and prev.get("alerts") is not None:
        # Caller passes through when tapeCollected matches.
        pass

    alerts = []
    for key, row in clusters.items():
        if key in prev_seen:
            continue
        # Officer just joined the follow list: ignore older prints.
        if row["filer_id"] not in prev_ids and prev_asof and row["filed_date"] <= prev_asof:
            continue
        alerts.append(row)
    alerts.sort(key=lambda r: (r["filed_date"], 0 if r.get("plan") else 1, r["filer"], r["ticker"]), reverse=True)
    return alerts


def compact_alert(row: dict) -> dict:
    out = {
        "id": row["id"],
        "key": row["key"],
        "filer_id": row["filer_id"],
        "filer": row["filer"],
        "ticker": row["ticker"],
        "company": row.get("company") or "",
        "side": row["side"],
        "filed_date": row["filed_date"],
        "trade_date": row.get("trade_date") or "",
        "lots": row.get("lots") or 1,
    }
    if row.get("title"):
        out["title"] = row["title"]
    if row.get("shares") is not None:
        out["shares"] = row["shares"]
    if row.get("value") is not None:
        out["value"] = row["value"]
    if row.get("amount"):
        out["amount"] = row["amount"]
    if row.get("plan"):
        out["plan"] = True
        if row.get("why"):
            out["why"] = row["why"]
    if row.get("after") is not None:
        out["after"] = row["after"]
    if row.get("vs") is not None:
        out["vs"] = row["vs"]
    return out


def build(args: argparse.Namespace) -> dict:
    if not args.repeatable.is_file():
        raise FileNotFoundError(f"missing {args.repeatable}")
    if not args.tape.is_file():
        raise FileNotFoundError(f"missing {args.tape}")

    repeatable = load_json(args.repeatable)
    tape = load_json(args.tape)
    form4 = load_form4()
    prev_path = args.prev or args.dest
    prev = load_json(prev_path) if prev_path.is_file() else None

    follow = pick_follow(repeatable, tape.get("trades") or [], form4)
    follow_ids = {r["id"] for r in follow if r["id"]}
    clusters = cluster_prints(tape.get("trades") or [], follow_ids, form4)
    last_filed = last_filed_by_id(clusters)
    for row in follow:
        if last_filed.get(row["id"]):
            row["lastFiled"] = last_filed[row["id"]]

    tape_collected = tape.get("collected") or repeatable.get("tapeCollected") or ""
    tape_asof = (repeatable.get("asof") or tape_collected or "")[:10]
    same_tape = bool(prev and prev.get("tapeCollected") and prev.get("tapeCollected") == tape_collected)

    if same_tape:
        alerts = [a for a in (prev.get("alerts") or []) if a.get("key") in clusters or a.get("filer_id") in follow_ids]
    else:
        alerts = [compact_alert(a) for a in choose_alerts(clusters, prev, tape_asof, args.bootstrap_days)]

    seen = sorted(clusters.keys())
    method = (
        "Follow / Best is the 90-day repeatable ranking "
        "(N=5 clustered open-market buys, equal-weight per ticker, copy as-of filed_date; "
        "scheduled Form 4 10b5-1 / trading-plan buys already dropped) "
        f"plus trust filters: 90d hit rate ≥ {int(MIN_HIT * 100)}% "
        "and average 90d return > 0. One-company officers still qualify. "
        f"Officers with ≥{int(PLAN_HEAVY * 100)}% of remaining Form 4 market prints on a "
        "scheduled plan sort below discretionary peers. Rank then prefers "
        "size-versus-remaining-stake conviction (trade shares ÷ Table I end holdings). "
        "Plan prints still alert, but sort under discretionary lots on the same filed day. "
        f"First build keeps prints filed in the last {args.bootstrap_days} days."
    )
    out = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "+00:00",
        "tapeCollected": tape_collected,
        "repeatableGenerated": repeatable.get("generated") or "",
        "form4Generated": form4.get("generated") or "",
        "priceAsof": repeatable.get("priceAsof") or "",
        "planHeavy": PLAN_HEAVY,
        "asof": tape_asof,
        "horizon": HORIZON,
        "minBuys": repeatable.get("minBuys") or 5,
        "minHit": MIN_HIT,
        "minNames": 1,
        "minAvg": MIN_AVG,
        "bootstrapDays": args.bootstrap_days,
        "method": method,
        "disclaimer": (
            "Not investment advice. Hypothetical paper copies from the public print date. "
            "Past hit rates do not mean the next filing works."
        ),
        "stats": {
            "follow": len(follow),
            "alerts": len(alerts),
            "seen": len(seen),
            "repeatable90": (repeatable.get("stats") or {}).get("filers90"),
        },
        "follow": follow,
        "alerts": alerts,
        "seen": seen,
    }
    return out


def payload_key(data: dict) -> dict:
    """Ignore generated timestamp when deciding whether to rewrite."""
    return {k: v for k, v in data.items() if k != "generated"}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        out = build(args)
    except FileNotFoundError as err:
        print(str(err), file=sys.stderr)
        return 1
    prev_path = args.prev or args.dest
    if prev_path.is_file():
        try:
            prev = load_json(prev_path)
        except (OSError, json.JSONDecodeError):
            prev = None
        if prev and payload_key(prev) == payload_key(out):
            print(
                "unchanged {path}  follow={n}  alerts={a}  asof={asof}".format(
                    path=args.dest.name,
                    n=out["stats"]["follow"],
                    a=out["stats"]["alerts"],
                    asof=out.get("asof"),
                )
            )
            return 0
    args.dest.parent.mkdir(parents=True, exist_ok=True)
    with args.dest.open("w") as f:
        json.dump(out, f, separators=(",", ":"))
        f.write("\n")
    print(
        "wrote {path} ({kb:.1f} KB)  follow={n}  alerts={a}  seen={s}  "
        "hit>={hit:.0%} avg>0  asof={asof}".format(
            path=args.dest.name,
            kb=args.dest.stat().st_size / 1024,
            n=out["stats"]["follow"],
            a=out["stats"]["alerts"],
            s=out["stats"]["seen"],
            hit=MIN_HIT,
            asof=out.get("asof"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
