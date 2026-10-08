"""Build the 2026-10-07 claims vs insider-buys research files.

Read-only on the pinned snapshot and qc.sqlite (URI mode=ro). No network.
Writes only under this folder.
"""
from __future__ import annotations

import csv
import json
import math
import random
import sqlite3
import statistics as st
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import lib  # noqa: E402

GROKS = Path(r"C:\Users\gordo\Desktop\Groks folder")
SNAP = GROKS / "claims_snapshots" / "claims-20261002.sqlite"
HOLD_NEW = GROKS / "claims_snapshots" / "claims-20261002.holders.json"
COUNTS = GROKS / "claims_snapshots" / "claims-20261002.counts.json"
QC_DB = GROKS / "qc.sqlite"
OLD_A_COMMIT = "397823c4bf"
PRICES = REPO / "prices"

FOCUS_PRODUCERS = [
    "agnico-eagle", "alamos-gold", "anglogold", "aris-mining", "artemis-gold",
    "asante-gold", "b2gold", "barrick", "caledonia-mining", "centerra-gold",
    "dpm-metals", "drdgold", "eldorado-gold", "endeavour", "equinox-gold",
    "evolution", "fortuna-mining", "galiano-gold", "gold-fields", "harmony",
    "iamgold", "jaguar-mining", "k92-mining", "kinross", "lundin-gold",
    "mineros", "newmont", "northern-star", "oceanagold", "ssr-mining",
    "thor-explorations", "torex-gold", "wesdome-gold-mines",
]


PV_SHORT = {
    "ontario": "on",
    "quebec": "qc",
    "british-columbia": "bc",
    "yukon": "yt",
    "nunavut": "nu",
    "newfoundland": "nl",
    "all": "all",
}
VIEWER_COLS = ["co", "tk", "ty", "pv", "o", "n", "na", "oa", "a", "d", "lk", "bs", "g"]


def compact_viewer_payload(rows: list[dict]) -> dict:
    """Column-array JSON. Drops fields the browser can derive (change, pct, type)."""
    out = []
    for row in rows:
        out.append([
            row.get("company") or "",
            row.get("ticker") or "",
            row.get("company_type") or "",
            PV_SHORT.get(row.get("province") or "", row.get("province") or ""),
            int(row["old_claims"]),
            int(row["new_claims"]),
            row.get("new_area_ha") or "",
            row.get("old_area_ha") or "",
            row.get("added") if row.get("added") != "" else "",
            row.get("dropped") if row.get("dropped") != "" else "",
            "y" if row.get("linked") == "yes" else "n",
            "ids" if row.get("basis") == "claim_ids" else "cnt",
            row.get("id_gap") if row.get("id_gap") != "" else "",
        ])
    return {"v": 1, "cols": VIEWER_COLS, "rows": out}


def wcsv(path: Path, rows: list[dict], fieldnames: list[str] | None = None) -> None:
    if not rows and not fieldnames:
        return
    keys = fieldnames or list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        for row in rows:
            out = {}
            for key in keys:
                val = row.get(key)
                if isinstance(val, date):
                    val = val.isoformat()
                elif val is None:
                    val = ""
                out[key] = val
            writer.writerow(out)


def load_holders_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def load_old_a(repo: Path) -> dict:
    proc = subprocess.run(
        ["git", "show", f"{OLD_A_COMMIT}:claims/links/holders.json"],
        cwd=str(repo),
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace") or "git show failed")
    return json.loads(proc.stdout.decode("utf-8-sig"))


def load_qc(path: Path) -> dict:
    con = lib.connect_ro(path)
    try:
        names = {r["company_id"]: (r["name"] or r["company_id"]) for r in con.execute("SELECT company_id, name FROM companies")}
        types = {r["company_id"]: r["company_type"] for r in con.execute("SELECT company_id, company_type FROM companies")}
        primary = {}
        tick_co: dict[str, set[str]] = defaultdict(set)
        for row in con.execute("SELECT company_id, ticker, is_primary FROM company_tickers"):
            tick_co[row["ticker"]].add(row["company_id"])
            if row["is_primary"]:
                primary[row["company_id"]] = row["ticker"]
        mine_owners = {r[0] for r in con.execute("SELECT DISTINCT company_id FROM mines")}
        buys = []
        for row in con.execute(
            "SELECT trade_id, filer, filer_id, ticker, side, trade_date, filed_date, origin, value "
            "FROM insider_trades WHERE side='purchase'"
        ):
            td = lib.parse_iso_date(row["trade_date"])
            fd = lib.parse_iso_date(row["filed_date"]) or td
            if td is None:
                continue
            kind = lib.buy_kind(row["origin"], row["trade_id"])
            buys.append({
                "trade_id": row["trade_id"],
                "filer": row["filer_id"] or row["filer"],
                "ticker": row["ticker"],
                "td": td,
                "fd": fd,
                "origin": row["origin"],
                "kind": kind,
                "value": row["value"],
            })
        old_b = load_old_b(con)
    finally:
        con.close()
    return {
        "names": names,
        "types": types,
        "primary": primary,
        "tick_co": dict(tick_co),
        "mine_owners": mine_owners,
        "buys": buys,
        "old_b": old_b,
    }


def load_old_b(con: sqlite3.Connection) -> dict:
    parties: dict[str, set[str]] = defaultdict(set)
    for row in con.execute(
        "SELECT title_pk, holder_company_id FROM claim_title_parties WHERE holder_company_id IS NOT NULL"
    ):
        hid = row["holder_company_id"]
        if hid and not str(hid).startswith("_"):
            parties[row["title_pk"]].add(hid)
    titles: dict[tuple[str, str], dict] = {}
    raw_rows = Counter()
    for row in con.execute(
        "SELECT title_pk, jurisdiction, claim_id, holder_company_id, company_id, holder_raw, area_ha FROM claim_titles"
    ):
        province = lib.JURIS_TO_PROVINCE.get(row["jurisdiction"] or "")
        if not province:
            continue
        title_id = "" if row["claim_id"] is None else str(row["claim_id"])
        raw_rows[province] += 1
        key = (province, title_id)
        hs = set(parties.get(row["title_pk"], ()))
        for extra in (row["holder_company_id"],):
            if extra and not str(extra).startswith("_"):
                hs.add(extra)
        slot = titles.get(key)
        if slot is None:
            area = None if row["area_ha"] is None else float(row["area_ha"])
            titles[key] = {"companies": set(hs), "area": area}
        else:
            slot["companies"] |= hs
            if slot["area"] is None and row["area_ha"] is not None:
                slot["area"] = float(row["area_ha"])
    by_co: dict[tuple[str, str], set[str]] = defaultdict(set)
    area_co: dict[tuple[str, str], float] = defaultdict(float)
    universe: dict[str, set[str]] = defaultdict(set)
    for (province, title_id), slot in titles.items():
        universe[province].add(title_id)
        for cid in slot["companies"]:
            by_co[(cid, province)].add(title_id)
            if slot["area"] is not None:
                area_co[(cid, province)] += slot["area"]
    return {
        "raw_rows": dict(raw_rows),
        "distinct": {p: sum(1 for k in titles if k[0] == p) for p in raw_rows},
        "by_co": dict(by_co),
        "area_co": dict(area_co),
        "universe": {p: set(s) for p, s in universe.items()},
    }


def identity_from_row(row: dict, names: dict[str, str]) -> tuple[str, str, str, str, str]:
    """Return kind, key, display name, ticker, company_id."""
    cid, name, ticker = lib.primary_link(row)
    if cid:
        display = names.get(cid) or name or cid
        return "yes", cid, display, ticker, cid
    holder = (row.get("holder") or "").strip() or "(blank)"
    return "no", "raw:" + holder, holder, "", ""


def build_comparison(
    old_a: dict,
    new_h: dict,
    qc: dict,
    new_area: dict[tuple[str, str], float],
    new_title_ids: dict[tuple[str, str], set[str]],
) -> list[dict]:
    names = qc["names"]
    types = qc["types"]
    primary = qc["primary"]
    old_b = qc["old_b"]

    # Match on (province, holder), then roll up to company.
    rolled: dict[tuple[str, str], dict] = {}

    def slot_for(row: dict, province: str) -> dict:
        linked, key, display, ticker, cid = identity_from_row(row, names)
        # Prefer a linked identity if this holder is linked on either side.
        k = (key, province)
        rec = rolled.get(k)
        if rec is None:
            rec = {
                "key": key,
                "company": display,
                "ticker": ticker or primary.get(cid, ""),
                "company_id": cid,
                "linked": linked,
                "company_type": types.get(cid, ""),
                "province": province,
                "old_claims": 0,
                "new_claims": 0,
                "new_area_ha": 0.0,
            }
            rolled[k] = rec
        else:
            if linked == "yes" and rec["linked"] != "yes":
                rec["linked"] = "yes"
                rec["company"] = display
                rec["company_id"] = cid
                rec["company_type"] = types.get(cid, "")
                rec["ticker"] = ticker or primary.get(cid, rec["ticker"])
            if not rec["ticker"] and (ticker or primary.get(cid)):
                rec["ticker"] = ticker or primary.get(cid, "")
        return rec

    old_index = {(r.get("province"), (r.get("holder") or "").strip() or "(blank)"): r for r in old_a["rows"]}
    new_index = {(r.get("province"), (r.get("holder") or "").strip() or "(blank)"): r for r in new_h["rows"]}
    keys = set(old_index) | set(new_index)
    for (province, holder) in keys:
        old_row = old_index.get((province, holder))
        new_row = new_index.get((province, holder))
        src = None
        if new_row and new_row.get("company_id"):
            src = new_row
        elif old_row and old_row.get("company_id"):
            src = old_row
        else:
            src = new_row or old_row
        rec = slot_for(src, province)
        if old_row:
            rec["old_claims"] += int(old_row.get("count") or 0)
        if new_row:
            rec["new_claims"] += int(new_row.get("count") or 0)
            rec["new_area_ha"] += float(new_area.get((province, holder), 0.0))

    # Title-level added/dropped only when OLD B fully covers this company-province.
    for rec in rolled.values():
        cid = rec["company_id"]
        province = rec["province"]
        linked = rec["linked"] == "yes" and bool(cid)
        old_set = old_b["by_co"].get((cid, province), set()) if linked else set()
        new_set = new_title_ids.get((cid, province), set()) if linked else set()
        if linked and province in lib.OLD_B_PARTIAL:
            universe = old_b["universe"].get(province, set())
            new_set = new_set & universe
        fields = lib.title_diff_fields(
            rec["old_claims"],
            rec["new_claims"],
            len(old_set),
            province,
            linked,
            len(new_set - old_set) if linked else None,
            len(old_set - new_set) if linked else None,
        )
        rec.update(fields)
        if rec["basis"] == "claim_ids":
            area = old_b["area_co"].get((cid, province))
            rec["old_area_ha"] = area if area else None
        else:
            rec["old_area_ha"] = None

    # Company totals
    by_company: dict[str, list[dict]] = defaultdict(list)
    for rec in rolled.values():
        by_company[rec["key"]].append(rec)
    rows: list[dict] = []
    for key, parts in by_company.items():
        head = parts[0]
        total = {
            "key": key,
            "company": head["company"],
            "ticker": head["ticker"],
            "company_id": head["company_id"],
            "linked": head["linked"],
            "company_type": head["company_type"],
            "province": "all",
            "old_claims": sum(p["old_claims"] for p in parts),
            "new_claims": sum(p["new_claims"] for p in parts),
            "new_area_ha": sum(p["new_area_ha"] for p in parts),
            "old_area_ha": None,
            "added": None,
            "dropped": None,
            "basis": "counts_only",
            "old_source": "A",
            "id_gap": None,
        }
        active = [p for p in parts if p["old_claims"] or p["new_claims"]]
        bases = {p["basis"] for p in active} if active else {"counts_only"}
        if bases == {"claim_ids"}:
            adds = [p["added"] for p in active]
            drops = [p["dropped"] for p in active]
            total["basis"] = "claim_ids"
            total["old_source"] = "A+B"
            total["added"] = sum(adds)
            total["dropped"] = sum(drops)
            total["id_gap"] = (total["added"] - total["dropped"]) - (total["new_claims"] - total["old_claims"])
            areas = [p["old_area_ha"] for p in active if p["old_area_ha"] is not None]
            if areas:
                total["old_area_ha"] = sum(areas)
        parts.append(total)
        for rec in parts:
            rec["change"] = rec["new_claims"] - rec["old_claims"]
            rec["change_pct"] = lib.format_pct(lib.change_pct(rec["old_claims"], rec["new_claims"]))
            rec["change_type"] = lib.change_type(rec["old_claims"], rec["new_claims"])
            rec["new_area_ha"] = lib.format_num(rec["new_area_ha"], 1)
            rec["old_area_ha"] = lib.format_num(rec["old_area_ha"], 1) if rec["old_area_ha"] is not None else ""
            rec["added"] = "" if rec["added"] is None else rec["added"]
            rec["dropped"] = "" if rec["dropped"] is None else rec["dropped"]
            rec["id_gap"] = "" if rec["id_gap"] is None else rec["id_gap"]
            rec["basis"] = rec.get("basis") or "counts_only"
            rec["old_source"] = "A+B" if rec["basis"] == "claim_ids" else "A"
            rows.append(rec)
    rows.sort(key=lambda r: (r["company"].casefold(), r["province"] != "all", r["province"]))
    return rows


def scan_titles(holder_link: dict[tuple[str, str], dict], qc: dict) -> dict:
    """One pass over the new snapshot. Returns area, issue clusters inputs, boxes, title ids."""
    new_area: dict[tuple[str, str], float] = defaultdict(float)
    new_title_ids: dict[tuple[str, str], set[str]] = defaultdict(set)
    monthly: dict[str, Counter] = defaultdict(Counter)
    monthly_last: dict[str, dict[tuple[int, int], date]] = defaultdict(dict)
    monthly_prov: dict[str, dict[tuple[int, int], set[str]]] = defaultdict(lambda: defaultdict(set))
    boxes_j: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    boxes_m: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    junior_total: dict[tuple[str, str], int] = defaultdict(int)
    issue_kept = Counter()
    issue_drop = Counter()
    roles = {}
    mine_owners = qc["mine_owners"]
    types = qc["types"]

    con = lib.connect_ro(SNAP)
    try:
        cur = con.execute(
            "SELECT province, title_id, holder, issue_date, area_ha, minx, miny, maxx, maxy FROM titles"
        )
        for row in cur:
            province = row["province"] or ""
            holder = (row["holder"] or "").strip() or "(blank)"
            area = 0.0 if row["area_ha"] is None else float(row["area_ha"])
            new_area[(province, holder)] += area
            link = holder_link.get((province, holder))
            cid = link["company_id"] if link else ""
            if cid and province in lib.OLD_B_PROVINCES:
                new_title_ids[(cid, province)].add("" if row["title_id"] is None else str(row["title_id"]))
            parsed = lib.valid_issue_date(province, row["issue_date"])
            if parsed is None:
                issue_drop[province] += 1
            else:
                issue_kept[province] += 1
                if cid and lib.CLUSTER_WIN0 <= parsed <= lib.CLUSTER_WIN1:
                    ym = (parsed.year, parsed.month)
                    monthly[cid][ym] += 1
                    prev = monthly_last[cid].get(ym)
                    if prev is None or parsed > prev:
                        monthly_last[cid][ym] = parsed
                    monthly_prov[cid][ym].add(province)
            if not cid:
                continue
            if cid not in roles:
                roles[cid] = lib.role_for_company(types.get(cid), cid, mine_owners)
            role = roles[cid]
            if role in {"junior", "major"}:
                minx, miny, maxx, maxy = row["minx"], row["miny"], row["maxx"], row["maxy"]
                if minx is None or miny is None or maxx is None or maxy is None:
                    continue
                box = (float(minx), float(miny), float(maxx), float(maxy))
                if role == "junior":
                    boxes_j[province][cid].append(box)
                    junior_total[(cid, province)] += 1
                else:
                    boxes_m[province][cid].append(box)
    finally:
        con.close()
    return {
        "new_area": dict(new_area),
        "new_title_ids": dict(new_title_ids),
        "monthly": monthly,
        "monthly_last": monthly_last,
        "monthly_prov": monthly_prov,
        "boxes_j": boxes_j,
        "boxes_m": boxes_m,
        "junior_total": dict(junior_total),
        "issue_kept": dict(issue_kept),
        "issue_drop": dict(issue_drop),
        "roles": roles,
    }


def find_clusters(scan: dict, min_n: int, mult: float, months: list[tuple[int, int]]) -> list[dict]:
    n_months = len(months)
    clusters = []
    for cid, mm in scan["monthly"].items():
        mean = sum(mm.values()) / n_months
        for ym, n in mm.items():
            if not lib.cluster_qualifies(n, mean, min_n, mult):
                continue
            clusters.append({
                "company_id": cid,
                "month": f"{ym[0]}-{ym[1]:02d}",
                "new_claims": n,
                "company_mean_per_month": round(mean, 2),
                "anchor": scan["monthly_last"][cid][ym],
                "provinces": "/".join(sorted(scan["monthly_prov"][cid][ym])),
                "min_n": min_n,
                "mult": mult,
            })
    clusters.sort(key=lambda r: (r["company_id"], r["month"]))
    return clusters


def post_windows(clusters: list[dict]) -> dict[str, list[tuple[date, date]]]:
    post: dict[str, list[tuple[date, date]]] = defaultdict(list)
    for row in clusters:
        a = row["anchor"]
        post[row["company_id"]].append((a, a + timedelta(days=lib.POST_DAYS)))
    return dict(post)


def in_post(post: dict[str, list[tuple[date, date]]], cid: str, day: date) -> bool:
    for a, b in post.get(cid, ()):
        if lib.in_post_window(day, a, lib.POST_DAYS) and day <= b:
            return True
    return False


def rate_test(post, buys_by_co, kinds, r0: date, r1: date, names: dict, label: str) -> list[dict]:
    out = []
    ndays = (r1 - r0).days + 1
    for cid in sorted(post):
        days = {b["td"] for b in buys_by_co.get(cid, ()) if r0 <= b["td"] <= r1 and b["kind"] in kinds}
        postdays = sum(1 for i in range(ndays) if in_post(post, cid, r0 + timedelta(days=i)))
        if postdays == 0:
            continue
        observed = sum(1 for d in days if in_post(post, cid, d))
        out.append({
            "test": label,
            "company_id": cid,
            "name": names.get(cid, cid),
            "clusters": len(post[cid]),
            "post_days": postdays,
            "base_days": ndays - postdays,
            "buy_days_total": len(days),
            "buy_days_post": observed,
            "buy_days_base": len(days) - observed,
            "rate_post_per_100d": round(100 * observed / postdays, 2),
            "rate_base_per_100d": round(100 * (len(days) - observed) / (ndays - postdays), 2) if ndays > postdays else "",
        })
    return out


def summarize_rate(rows: list[dict], sims: int = 20000, seed: int = 7) -> dict:
    rows = [r for r in rows if r["base_days"] > 0]
    observed = sum(r["buy_days_post"] for r in rows)
    expected = sum(r["buy_days_total"] * r["post_days"] / (r["post_days"] + r["base_days"]) for r in rows)
    rnd = random.Random(seed)
    ge = 0
    for _ in range(sims):
        s = 0
        for r in rows:
            f = r["post_days"] / (r["post_days"] + r["base_days"])
            s += sum(1 for _ in range(r["buy_days_total"]) if rnd.random() < f)
        ge += s >= observed
    with_buys = [r for r in rows if r["buy_days_total"] > 0]
    up = sum(1 for r in with_buys if r["rate_post_per_100d"] > r["rate_base_per_100d"])
    dn = sum(1 for r in with_buys if r["rate_post_per_100d"] < r["rate_base_per_100d"])
    return {
        "companies": len(rows),
        "companies_with_buys": len(with_buys),
        "buy_days": sum(r["buy_days_total"] for r in rows),
        "observed_post": observed,
        "expected_post": round(expected, 1),
        "ratio": round(observed / expected, 2) if expected else None,
        "p_one_sided": round((ge + 1) / (sims + 1), 4),
        "companies_higher_post": up,
        "companies_lower_post": dn,
    }


def sign_p(k: int, n: int) -> float | None:
    from math import comb
    if not n:
        return None
    return round(min(1.0, sum(comb(n, i) for i in range(n + 1) if min(i, n - i) <= min(k, n - k)) / 2 ** n), 3)


def mwu(a: list[float], b: list[float]) -> dict:
    allv = sorted([(x, 0) for x in a] + [(x, 1) for x in b])
    i = 0
    ranks = [0.0] * len(allv)
    while i < len(allv):
        j = i
        while j + 1 < len(allv) and allv[j + 1][0] == allv[i][0]:
            j += 1
        for k in range(i, j + 1):
            ranks[k] = (i + j) / 2 + 1
        i = j + 1
    ra = sum(ranks[k] for k in range(len(allv)) if allv[k][1] == 0)
    n1, n2 = len(a), len(b)
    u = ra - n1 * (n1 + 1) / 2
    mu = n1 * n2 / 2
    sd = math.sqrt(n1 * n2 * (n1 + n2 + 1) / 12) if n1 and n2 else 0.0
    z = (u - mu) / sd if sd else 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    return {"n_after": n1, "n_other": n2, "U": round(u, 1), "z": round(z, 2), "p_two_sided": round(p, 4)}


class PriceCache:
    def __init__(self, root: Path):
        self.root = root
        self.cache: dict[str, dict | None] = {}

    def get(self, ticker: str | None) -> dict | None:
        if not ticker:
            return None
        if ticker in self.cache:
            return self.cache[ticker]
        path = self.root / f"{ticker}.json"
        result = None
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            series = data.get("c")
            if isinstance(series, list) and len(series) > 5:
                ds = [lib.parse_iso_date(x[0]) for x in series]
                px = [float(x[1]) for x in series]
                keep_d = []
                keep_p = []
                for d, p in zip(ds, px):
                    if d is not None:
                        keep_d.append(d)
                        keep_p.append(p)
                if len(keep_d) > 5:
                    result = {"t": ticker, "src": data.get("src") or ticker, "cur": data.get("cur") or "?", "ds": keep_d, "px": keep_p}
        self.cache[ticker] = result
        return result


def px_on_or_after(prices: dict, day: date):
    import bisect
    i = bisect.bisect_left(prices["ds"], day)
    if i < len(prices["ds"]):
        return prices["ds"][i], prices["px"][i]
    return None


def px_on_or_before(prices: dict, day: date):
    import bisect
    i = bisect.bisect_right(prices["ds"], day) - 1
    if i >= 0:
        return prices["ds"][i], prices["px"][i]
    return None


def ret(prices: dict | None, d0: date, d1: date) -> float | None:
    if not prices:
        return None
    a = px_on_or_after(prices, d0)
    b = px_on_or_before(prices, d1)
    if not a or not b or b[0] <= a[0] or a[1] <= 0:
        return None
    return b[1] / a[1] - 1


def price_ticker(cid: str, primary: dict, tick_co_rev: dict, cache: PriceCache) -> tuple[str | None, str]:
    t = primary.get(cid)
    if t and cache.get(t):
        return t, "primary"
    # fall back: any ticker mapped to this company that has prices
    for ticker, cos in tick_co_rev.items():
        if cid in cos and cache.get(ticker):
            return ticker, "fallback"
    return t, "none"


def juniors_near_majors(scan: dict, qc: dict, buys_by_co: dict, r0: date) -> list[dict]:
    names = qc["names"]
    types = qc["types"]
    primary = qc["primary"]
    rows = []
    cell = 0.1
    pad = 0.45  # ~5 km even at high latitude

    def cells_of(box, extra: float):
        minx, miny, maxx, maxy = box
        x0 = math.floor((minx - extra) / cell)
        x1 = math.floor((maxx + extra) / cell)
        y0 = math.floor((miny - extra) / cell)
        y1 = math.floor((maxy + extra) / cell)
        for ix in range(x0, x1 + 1):
            for iy in range(y0, y1 + 1):
                yield (ix, iy)

    for province, majors in scan["boxes_m"].items():
        grid: dict[tuple[int, int], list[tuple[str, tuple]]] = defaultdict(list)
        for mco, boxes in majors.items():
            for box in boxes:
                for c in cells_of(box, 0.0):
                    grid[c].append((mco, box))
        juniors = scan["boxes_j"].get(province, {})
        for jco, jboxes in juniors.items():
            best: dict[str, float] = {}
            within5: Counter = Counter()
            within2: Counter = Counter()
            for jbox in jboxes:
                here: dict[str, float] = {}
                seen: set[tuple] = set()
                for c in cells_of(jbox, pad):
                    for mco, mbox in grid.get(c, ()):
                        key = (mco, mbox)
                        if key in seen:
                            continue
                        seen.add(key)
                        d = lib.box_edge_distance_km(jbox, mbox)
                        if mco not in here or d < here[mco]:
                            here[mco] = d
                for mco, d in here.items():
                    if mco not in best or d < best[mco]:
                        best[mco] = d
                    if d <= 5.0:
                        within5[mco] += 1
                    if d <= 2.0:
                        within2[mco] += 1
            buy_days = len({b["td"] for b in buys_by_co.get(jco, ()) if b["td"] >= r0})
            total = scan["junior_total"].get((jco, province), len(jboxes))
            for mco, dist in best.items():
                if dist > 5.0:
                    continue
                rows.append({
                    "junior": names.get(jco, jco),
                    "junior_id": jco,
                    "junior_ticker": primary.get(jco, ""),
                    "junior_type": types.get(jco, ""),
                    "province": province,
                    "nearest_major": names.get(mco, mco),
                    "major_id": mco,
                    "major_ticker": primary.get(mco, ""),
                    "major_type": types.get(mco, ""),
                    "min_distance_km": round(dist, 2),
                    "junior_claims_within_5km": within5[mco],
                    "junior_claims_within_2km": within2[mco],
                    "junior_total_claims": total,
                    "insider_buy_days_since_2025-04-01": buy_days,
                })
    rows.sort(key=lambda r: (r["min_distance_km"], r["junior"].casefold(), r["province"], r["nearest_major"].casefold()))
    return rows


def ssum(events: list[dict], group: str) -> list[dict]:
    out = []
    for h in (30, 90, 180):
        v = [r for r in events if r.get(f"r{h}") is not None]
        def med(field):
            x = [r[field] for r in v if r.get(field) is not None]
            return round(100 * st.median(x), 1) if x else None
        def hit(field):
            x = [r[field] for r in v if r.get(field) is not None]
            return round(100 * sum(1 for y in x if y > 0) / len(x), 1) if x else None
        out.append({
            "group": group,
            "horizon_days": h,
            "n_events": len(v),
            "n_companies": len({r["company_id"] for r in v}),
            "median_return_pct": med(f"r{h}"),
            "hit_rate_pct": hit(f"r{h}"),
            "median_vs_spy_pct": med(f"x_spy{h}"),
            "beat_spy_pct": hit(f"x_spy{h}"),
            "median_vs_basket_pct": med(f"x_bsk{h}"),
            "beat_basket_pct": hit(f"x_bsk{h}"),
            "median_vs_gld_pct": med(f"x_gld{h}"),
            "beat_gld_pct": hit(f"x_gld{h}"),
        })
    return out


def write_note(path: Path, s: dict) -> None:
    r = s["rate_main_all"]
    om = s["rate_main_om"]
    pl = s["rate_main_pl"]
    f = { (row["group"], row["horizon_days"]): row for row in s["forward"] }
    after90 = f[("claims_linked|after_cluster|all", 90)]
    other90 = f[("claims_linked|other|all", 90)]
    after30 = f[("claims_linked|after_cluster|all", 30)]
    other30 = f[("claims_linked|other|all", 30)]
    after180 = f[("claims_linked|after_cluster|all", 180)]
    other180 = f[("claims_linked|other|all", 180)]
    noc90 = f[("not_claims_linked|all", 90)]
    sens = {row["cluster_rule"]: row for row in s["sensitivity"]}
    main = sens["10 claims, 2x mean (main)"]
    s25 = sens["25 claims, 2x mean"]
    s50 = sens["50 claims, 3x mean"]
    rob = s["robust_all"]
    mw = s["mw"]
    jn = s["juniors_summary"]
    top8 = s["window8_top"]
    new_cos = s["new_to_data"][:15]
    miss_cos = s["missing_from_data"][:15]

    def n(v):
        return v if v is not None else "—"

    lines = []
    a = lines.append
    a("# Mining claims vs insider buys — research note (2026-10-07)")
    a("")
    a("Not investment advice. Claims data is provincial viewing data, not legal title.")
    a("")
    a("## Bottom line")
    a("")
    a(
        f"1. Insiders buy a little more often in the 90 days after a company records a batch of new claims. "
        f"With the main rule, buy-days after a batch were {n(r['ratio'])}x what the company's own baseline predicts "
        f"({r['observed_post']} vs {r['expected_post']} expected; {r['buy_days']} buy-days in {r['companies']} companies). "
        f"The lift is larger for bigger batches ({n(s25['ratio_all'])}x at 25+ claims, {n(s50['ratio_all'])}x at 50+ claims). "
        f"A few companies drive it. Company by company the split is {r['companies_higher_post']} higher vs "
        f"{r['companies_lower_post']} lower."
    )
    a(
        f"2. Buys made after a claims batch did not do better. Over 90 days their median beat SPY by "
        f"{n(after90['median_vs_spy_pct'])} pts ({after90['n_events']} events, {after90['n_companies']} companies). "
        f"Other buys in claim-holding companies beat SPY by {n(other90['median_vs_spy_pct'])} pts "
        f"({other90['n_events']} events, {other90['n_companies']} companies; p = {mw[90]['p_two_sided']})."
    )
    a(
        f"3. Juniors next to majors: {jn['n_juniors']} junior companies have at least one claim within 5 km of a major "
        f"in the same province ({jn['n_rows']} junior-province-major rows; {jn['n_within_2km']} rows also within 2 km). "
        f"This uses the 2 Oct 2026 six-province snapshot, not the old Quebec-only neighbor extracts."
    )
    a("")
    a("## Data")
    a("")
    a("| Item | Source |")
    a("|---|---|")
    a("| New claims snapshot | `claims_snapshots/claims-20261002.sqlite` table `titles`, 898,104 rows. Ingested 2026-10-02 about 5:10–5:14 PM ET. Six provinces. |")
    a("| New holder links | `claims_snapshots/claims-20261002.holders.json` (2026-10-02T21:13:56Z; 5,840 holder rows). |")
    a("| Old holder counts (OLD A) | `claims/links/holders.json` at commit `397823c4bf` (2026-09-24T19:24:25Z; 898,668 titles; 5,842 rows). |")
    a("| Old titles, partial (OLD B) | `qc.sqlite` table `claim_titles`, 475,727 rows (Ontario 430,557; Quebec 35,077 extracts plus neighbors; BC 10,093 capped). Loaded 2026-09-16..20. |")
    a("| Companies, tickers, insider trades, mines | Same `qc.sqlite`, read-only. 21,895 insider rows; 6,778 purchases; last trade date 2026-10-07. |")
    a("| Prices | repo `prices/*.json` at commit `e22911357e` (clone of origin/main, 2026-10-07). |")
    a("| SPY | `prices/SPY.json`. |")
    a("| Gold miners stand-in | Equal-weight basket of the 33 gold producers in the claims catalog. No GDX or XGD file exists. |")
    a("")
    a("OLD A and the new snapshot share the same holder-count shape. Company-by-company counts are direct for all six provinces.")
    a("OLD B has title ids and some area. Use it only where it covers the land: Ontario in full; Quebec and BC in part.")
    a("Yukon, Nunavut, and Newfoundland have no title-level old snapshot. Count changes there vs OLD B would be coverage, not staking.")
    a("Quebec vs OLD B is the same trap: OLD B has 35,077 Quebec rows; the new snapshot has 253,955.")
    a("")
    a("**Province title counts**")
    a("")
    a("| Province | OLD A titles | New titles | New minus OLD A |")
    a("|---|---:|---:|---:|")
    for p, old_n, new_n in s["prov_totals"]:
        a(f"| {p} | {old_n:,} | {new_n:,} | {new_n - old_n:+,} |")
    a(f"| total | {s['old_a_titles']:,} | {s['new_titles']:,} | {s['new_titles'] - s['old_a_titles']:+,} |")
    a("")
    a("## Method")
    a("")
    a("- **Company.** A holders.json company link when present. Else the raw holder name. The `linked` flag records this.")
    a("- **Claims cluster.** A month with at least 10 new claims and at least 2x that company's mean monthly count. Months run Jul 2023 – Sep 2026. Zero months stay in the mean. The cluster date is the last issue date in that month. Sensitivity uses 25+ at 2x, and 50+ at 3x.")
    a("- **Issue dates.** From the new snapshot, all six provinces. Drop Ontario 2018-04-10 conversion dates. Drop non-ISO dates, years before 1990, and dates after 2026-10-02.")
    a("- **After-cluster window.** The 90 days after the cluster date. A buy counts as after a cluster only if the cluster date is on or before the trade date. No lookahead.")
    a("- **Insider buys.** `side = purchase` (SEDI + Form 4). Open market means SEDI code 10 or Form 4 P. Placement means SEDI codes 11, 15 and 16.")
    a("- **Rate test.** 1 Apr 2025 – 7 Oct 2026. Insider data is thin before Apr 2025. The unit is a buy-day (company + date). Expected counts use each company's own share of days inside windows. The p-value uses 20,000 simulations under “no link”.")
    a("- **Forward returns.** One event per company per filing date. Trades from 1 Apr 2025. File the trade within 30 days. Enter at the first close on or after the filing date (skip if that close is more than 5 days later). Horizons are 30, 90 and 180 calendar days.")
    a("- **Eight-day window.** OLD A (24 Sep 2026) to the new snapshot (2 Oct 2026) is 8 days. That is too short for returns. It is context only.")
    a("- **Juniors next to majors.** Major = company type Producer, Major or Producer, Mid-tier, or a company in the mines table. Junior = Explorer (any), Developer (any), Producer, Junior, or Land Banks. Royalty and Other are out. Distance is nearest edge-to-edge between claim boxes in the same province, in km. A row is one junior × province × major with min distance ≤ 5 km. The file also counts claims within 2 km of that major.")
    a("")
    a("## Part 1 — Company-by-company claims (new snapshot vs OLD A)")
    a("")
    a(f"The comparison file has {s['comparison_rows']:,} rows (one per company per province, plus a total row).")
    a(f"Linked companies: {s['n_linked_companies']}. Unlinked holders treated as their own name: {s['n_unlinked_companies']}.")
    a(f"New to the data (total row, linked only shown below): {s['n_new_to_data_linked']} linked companies.")
    a(f"Missing from the data (total row, linked): {s['n_missing_linked']} linked companies.")
    a("")
    a("Much of any Quebec, Yukon, Nunavut, or Newfoundland change versus OLD B is coverage, not real staking.")
    a("OLD B never held those full provinces at title level. Do not read title-level added/dropped there as new staking.")
    a("Holder-count changes versus OLD A are the fair six-province comparison.")
    a("")
    a("**Linked companies new to the data (up to 15, by new claims)**")
    a("")
    if new_cos:
        a("| Company | Ticker | Type | New claims | Provinces |")
        a("|---|---|---|---:|---|")
        for row in new_cos:
            a(f"| {row['company']} | {row['ticker']} | {row['company_type']} | {row['new_claims']:,} | {row['provinces']} |")
    else:
        a("None.")
    a("")
    a("**Linked companies missing from the data (up to 15, by old claims)**")
    a("")
    if miss_cos:
        a("| Company | Ticker | Type | Old claims |")
        a("|---|---|---|---:|")
        for row in miss_cos:
            a(f"| {row['company']} | {row['ticker']} | {row['company_type']} | {row['old_claims']:,} |")
    else:
        a("None.")
    a("")
    a("**Largest linked-company count changes, 24 Sep to 2 Oct 2026 (8 days; context only)**")
    a("")
    a("| Company | Ticker | Province | Old | New | Change |")
    a("|---|---|---|---:|---:|---:|")
    for row in top8:
        a(f"| {row['company']} | {row['ticker']} | {row['province']} | {row['old_claims']:,} | {row['new_claims']:,} | {row['change']:+,} |")
    a("")
    a("Eight days is too short to study stock returns after these changes. The cluster study below uses issue dates across years, not this 8-day gap.")
    a("")
    a("## Part 2a — Do insider buys rise after a claims cluster?")
    a("")
    a(
        f"Main rule (10+ claims, 2x mean): {s['n_clusters']} clusters across {s['n_cluster_companies']} companies. "
        f"Issue dates kept for clustering: {s['issue_kept_total']:,} titles. Dropped dates: {s['issue_drop_total']:,} "
        f"(conversion, junk, or after the snapshot)."
    )
    a("")
    a(f"Rate test on {r['companies']} companies with a window inside 1 Apr 2025 – 7 Oct 2026:")
    a("")
    a("| Buys | Buy-days | After-cluster, observed | Expected | Ratio | p (one-sided) | Companies higher / lower |")
    a("|---|---:|---:|---:|---:|---:|---|")
    a(f"| All | {r['buy_days']} | {r['observed_post']} | {r['expected_post']} | {n(r['ratio'])} | {r['p_one_sided']} | {r['companies_higher_post']} / {r['companies_lower_post']} |")
    a(f"| Open market | {om['buy_days']} | {om['observed_post']} | {om['expected_post']} | {n(om['ratio'])} | {om['p_one_sided']} | {om['companies_higher_post']} / {om['companies_lower_post']} |")
    a(f"| Placement | {pl['buy_days']} | {pl['observed_post']} | {pl['expected_post']} | {n(pl['ratio'])} | {pl['p_one_sided']} | {pl['companies_higher_post']} / {pl['companies_lower_post']} |")
    a("")
    a(f"- Top drivers (all buys): {rob['top3']}. Without the top 3, the ratio is {n(rob['ratio_drop_top3'])}.")
    a(f"- Sign test on companies with buys: {rob['companies_higher']} higher vs {rob['companies_lower']} lower (p = {rob['sign_p_two_sided']}).")
    a(f"- Placements are few ({pl['buy_days']} buy-days). Do not lean on that split.")
    a("")
    a("## Part 2b — Forward returns of insider buys (claim-holding companies)")
    a("")
    a("Medians in percent. Hit = share with a return above 0. Beat = share above the benchmark.")
    a("")
    a("| Group | Days | Events | Cos | Median return | Hit | Median vs SPY | Beat SPY | Median vs basket | Beat basket |")
    a("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for grp, label in (
        ("claims_linked|after_cluster|all", "After cluster"),
        ("claims_linked|other|all", "Other buys"),
        ("not_claims_linked|all", "No claims (context)"),
    ):
        for h in (30, 90, 180):
            row = f[(grp, h)]
            a(
                f"| {label} | {h} | {row['n_events']:,} | {row['n_companies']} | {n(row['median_return_pct'])} | "
                f"{n(row['hit_rate_pct'])} | {n(row['median_vs_spy_pct'])} | {n(row['beat_spy_pct'])} | "
                f"{n(row['median_vs_basket_pct'])} | {n(row['beat_basket_pct'])} |"
            )
    a("")
    a(
        f"- After-cluster vs other, excess over SPY (rank test p): "
        f"30 days {mw[30]['p_two_sided']}, 90 days {mw[90]['p_two_sided']}, 180 days {mw[180]['p_two_sided']}."
    )
    a(
        f"- The after-cluster sample is small: {after30['n_events']} events in {after30['n_companies']} companies at 30 days, "
        f"falling to {after180['n_events']} events in {after180['n_companies']} companies at 180 days."
    )
    a(f"- Other buys at 30 / 90 / 180 days: {other30['n_events']:,} / {other90['n_events']:,} / {other180['n_events']:,} events.")
    a(f"- Companies with no claims in this snapshot (context, 90 days): {noc90['n_events']:,} events in {noc90['n_companies']} companies.")
    a("- Open-market-only rows are in `forward-summary.csv`.")
    a("")
    a("## Sensitivity — cluster size")
    a("")
    a("| Rule | Clusters (cos) | Buy-rate ratio | Without top 3 | Cos higher/lower (p) | 90d vs SPY: after / other (p) | 180d vs SPY: after / other (p) |")
    a("|---|---|---:|---:|---|---|---|")
    for key in ("10 claims, 2x mean (main)", "25 claims, 2x mean", "50 claims, 3x mean"):
        row = sens[key]
        a(
            f"| {key} | {row['clusters']} ({row['companies']}) | {n(row['ratio_all'])} | {n(row['ratio_drop_top3'])} | "
            f"{row['cos_higher']}/{row['cos_lower']} ({n(row['sign_p'])}) | "
            f"{n(row['after_med_vs_spy_90'])} / {n(row['other_med_vs_spy_90'])} ({n(row['mwu_p_90'])}) | "
            f"{n(row['after_med_vs_spy_180'])} / {n(row['other_med_vs_spy_180'])} ({n(row['mwu_p_180'])}) |"
        )
    a("")
    a("Bigger batches: more insider buying afterwards, but the after-cluster return sample stays small.")
    a("These p-values treat each event as independent. They are not. Events bunch by company and by date. Read them as weak.")
    a("")
    a("## Part 3 — Juniors next to majors (all six provinces)")
    a("")
    a("Final rule used:")
    a("")
    a("1. Major: `companies.company_type` is Producer, Major or Producer, Mid-tier, or the company owns a mine in `mines`.")
    a("2. Junior: Explorer (any subtype), Developer (any subtype), Producer, Junior, or Land Banks.")
    a("3. Out: Royalty and Other. A mine owner is treated as a major even if typed as a junior or land bank.")
    a("4. Distance: nearest edge-to-edge between any junior claim box and any major claim box in the same province, in km.")
    a("5. Boxes are `minx, miny, maxx, maxy`. Lon degrees scale by cos(latitude). Lat degrees use 110.574 km.")
    a("6. Keep a row when min distance is 5 km or less. Also count that junior's claims within 2 km of that major.")
    a("7. Grain: one row per junior × province × major (not nearest-only).")
    a("")
    a(
        f"Result: {jn['n_rows']} rows, {jn['n_juniors']} juniors, {jn['n_majors']} majors, {jn['n_provinces']} provinces. "
        f"{jn['n_within_2km']} of those rows have at least one junior claim within 2 km. "
        f"{jn['n_juniors_with_buys']} juniors in the list have at least one insider buy-day since 1 Apr 2025."
    )
    a("")
    a("**Closest 15 junior-major pairs**")
    a("")
    a("| Junior | Ticker | Province | Major | Ticker | km | Claims ≤5 km | Claims ≤2 km | Buy-days |")
    a("|---|---|---|---|---|---:|---:|---:|---:|")
    for row in s["juniors_closest"][:15]:
        a(
            f"| {row['junior']} | {row['junior_ticker']} | {row['province']} | {row['nearest_major']} | "
            f"{row['major_ticker']} | {row['min_distance_km']} | {row['junior_claims_within_5km']} | "
            f"{row['junior_claims_within_2km']} | {row['insider_buy_days_since_2025-04-01']} |"
        )
    a("")
    a("Full list: `juniors.csv`. This is not the old Quebec ~2 km neighbor extract. It is box distance on the new snapshot.")
    a("")
    a("## Limits")
    a("")
    a("- **Not investment advice.** This is a research note on public filings and provincial viewing data.")
    a("- **No GDX/XGD file.** The producer basket is a stand-in. It is equal-weight and mixed currency.")
    a("- **Short insider history.** Dense SEDI data starts in Apr 2025. Form 4 covers few of these names.")
    a("- **One live snapshot.** Lapsed claims are gone, so older months look quieter than they were. Clusters lean toward 2025–2026.")
    a("- **Issue date is not a staking tape.** There is no transfer or lapse history in the snapshot.")
    a("- **Ontario conversion.** 127,149 titles carry issue date 2018-04-10. Those dates are not staking. They are dropped from clusters.")
    a("- **Yukon and Nunavut dates.** Some issue_date values are not ISO dates. They are dropped.")
    a("- **OLD B is partial.** Quebec is producer extracts plus neighbors. BC is capped. Title-level added/dropped outside that coverage is blank.")
    a("- **Holder links are partial.** About 48% of new titles link to a public company. Unlinked holders are private or unmatched.")
    a("- **Eight-day window.** 24 Sep to 2 Oct 2026 is too short for returns.")
    a("- **Currency.** Many stock returns are CAD. SPY is USD. Excess returns include CAD/USD moves.")
    a("- **Price rounding.** Penny stocks round to 2 decimals, so their returns are coarse.")
    a("- **Placements are not open-market buys.** SEDI purchase includes private placements. They often fund staking. They are split out.")
    a("- **Overlap.** One buy can follow more than one cluster. One claim with two holders counts for both.")
    a("- **Weak p-values.** They assume independent events. Events bunch by company and by date.")
    a("- **Distance is box-to-box, not polygon-to-polygon.** A box can be larger than the claim. 5 km is approximate.")
    a("- **Mines table is the catalog, not a live operating flag.** A company in `mines` is treated as a major.")
    a("")
    a("## Files")
    a("")
    a("All in `research/claims_insider_20261007/`:")
    a("")
    a("- `comparison.csv` / `comparison.json` — company × province vs OLD A, with OLD B title-level added/dropped where coverage exists")
    a("- `clusters.csv` — every cluster under the main rule")
    a("- `rate-by-company.csv`, `rate-summary.csv`, `rate-robustness.csv` — Part 2a")
    a("- `buy-events.csv` — claim-linked buy events with returns")
    a("- `forward-summary.csv`, `forward-by-company.csv` — Part 2b")
    a("- `sensitivity.csv` — cluster-size checks")
    a("- `juniors.csv` — juniors within 5 km of a major, all six provinces")
    a("- `sanity.json` — row-count checks")
    a("- `viewer.html` — browse the comparison (local server; not in the site nav)")
    a("- `run_research.py`, `lib.py`, `test_claims_insider.py` — code and tests")
    a("")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    print("loading holders and qc.sqlite (read-only)...")
    counts = json.loads(COUNTS.read_text(encoding="utf-8"))
    new_h = load_holders_json(HOLD_NEW)
    old_a = load_old_a(REPO)
    qc = load_qc(QC_DB)

    holder_link = {}
    for row in new_h["rows"]:
        cid, name, ticker = lib.primary_link(row)
        province = row.get("province") or ""
        holder = (row.get("holder") or "").strip() or "(blank)"
        holder_link[(province, holder)] = {
            "company_id": cid,
            "name": name,
            "ticker": ticker,
        }

    print("scanning new snapshot titles...")
    scan = scan_titles(holder_link, qc)
    print("building comparison...")
    comparison = build_comparison(old_a, new_h, qc, scan["new_area"], scan["new_title_ids"])

    # Sanity vs counts / OLD A
    new_by_prov = Counter()
    old_by_prov = Counter()
    for row in comparison:
        if row["province"] == "all":
            continue
        new_by_prov[row["province"]] += int(row["new_claims"])
        old_by_prov[row["province"]] += int(row["old_claims"])
    expected_new = {p: counts["provinces"][p]["rows"] for p in counts["provinces"]}
    expected_old = {p: old_a["provinces"][p]["titles"] for p in old_a["provinces"]}
    print("SANITY new_claims vs counts.json")
    for p in lib.PROVINCES:
        print(f"  {p}: csv={new_by_prov[p]} counts={expected_new[p]} ok={new_by_prov[p]==expected_new[p]}")
    print("SANITY old_claims vs OLD A")
    for p in lib.PROVINCES:
        print(f"  {p}: csv={old_by_prov[p]} oldA={expected_old[p]} ok={old_by_prov[p]==expected_old[p]}")
    assert sum(new_by_prov.values()) == counts["rows"] == 898104
    assert sum(old_by_prov.values()) == old_a["titles"] == 898668
    for p in lib.PROVINCES:
        if new_by_prov[p] != expected_new[p]:
            raise SystemExit(f"new_claims mismatch {p}: {new_by_prov[p]} != {expected_new[p]}")
        if old_by_prov[p] != expected_old[p]:
            raise SystemExit(f"old_claims mismatch {p}: {old_by_prov[p]} != {expected_old[p]}")

    months = lib.month_list(lib.CLUSTER_WIN0, lib.CLUSTER_WIN1)
    clusters_main = find_clusters(scan, 10, 2.0, months)
    clusters_25 = find_clusters(scan, 25, 2.0, months)
    clusters_50 = find_clusters(scan, 50, 3.0, months)
    for c in clusters_main:
        c["name"] = qc["names"].get(c["company_id"], c["company_id"])
        c["ticker"] = qc["primary"].get(c["company_id"], "")
        c["company_type"] = qc["types"].get(c["company_id"], "")

    buys_by_co: dict[str, list] = defaultdict(list)
    for b in qc["buys"]:
        for cid in qc["tick_co"].get(b["ticker"], ()):
            bb = dict(b)
            bb["co"] = cid
            buys_by_co[cid].append(bb)

    r0, r1 = lib.RATE_WIN0, date(2026, 10, 7)
    ALL = ("open_market", "placement_or_private")
    OM = ("open_market",)
    PL = ("placement_or_private",)

    def run_rate(clusters, tag):
        post = post_windows(clusters)
        rows = []
        summaries = []
        for kinds, kl in ((ALL, "all_buys"), (OM, "open_market"), (PL, "placements")):
            rr = rate_test(post, buys_by_co, kinds, r0, r1, qc["names"], f"{tag}|{kl}")
            rows.extend(rr)
            sm = summarize_rate(rr)
            sm["test"] = f"{tag}|{kl}"
            summaries.append(sm)
        return post, rows, summaries

    post_main, rate_rows, rate_sum = run_rate(clusters_main, "main_2025-04_to_2026-10")
    _, _, rate_25 = run_rate(clusters_25, "n25")
    _, _, rate_50 = run_rate(clusters_50, "n50")

    robust = []
    for kl in ("all_buys", "open_market", "placements"):
        rr = [r for r in rate_rows if r["test"].endswith(kl) and r["base_days"] > 0]
        for r in rr:
            r["_E"] = r["buy_days_total"] * r["post_days"] / (r["post_days"] + r["base_days"])
            r["_d"] = r["buy_days_post"] - r["_E"]
        rr.sort(key=lambda r: -r["_d"])
        def ratio(xs):
            e = sum(x["_E"] for x in xs)
            return round(sum(x["buy_days_post"] for x in xs) / e, 2) if e else None
        wb = [r for r in rr if r["buy_days_total"] > 0]
        up = sum(1 for r in wb if r["rate_post_per_100d"] > r["rate_base_per_100d"])
        dn = sum(1 for r in wb if r["rate_post_per_100d"] < r["rate_base_per_100d"])
        robust.append({
            "test": kl,
            "ratio": ratio(rr),
            "ratio_drop_top1": ratio(rr[1:]),
            "ratio_drop_top3": ratio(rr[3:]),
            "top3": ";".join(f"{r['company_id']}({r['buy_days_post']}/{r['_E']:.1f})" for r in rr[:3]),
            "companies_higher": up,
            "companies_lower": dn,
            "sign_p_two_sided": sign_p(up, up + dn),
        })
        for r in rr:
            r.pop("_E", None)
            r.pop("_d", None)

    print("loading prices and building forward returns...")
    cache = PriceCache(PRICES)
    spy = cache.get("SPY")
    gld = cache.get("GLD")
    if not spy:
        raise SystemExit("SPY prices missing")
    last = spy["ds"][-1]
    basket = []
    for cid in FOCUS_PRODUCERS:
        t, how = price_ticker(cid, qc["primary"], qc["tick_co"], cache)
        if t and cache.get(t):
            basket.append((cid, t))

    def basket_ret(d0, d1):
        rs = [ret(cache.get(t), d0, d1) for _, t in basket]
        rs = [x for x in rs if x is not None]
        return sum(rs) / len(rs) if rs else None

    claim_cos = set(scan["monthly"]) | {cid for cid, _p in scan["new_title_ids"]}
    # also any linked company with claims in new holders
    for row in new_h["rows"]:
        cid, _, _ = lib.primary_link(row)
        if cid:
            claim_cos.add(cid)

    def forward_for(post) -> list[dict]:
        ev = {}
        for cid, blist in buys_by_co.items():
            for b in blist:
                if b["td"] < lib.RATE_WIN0 or (b["fd"] - b["td"]).days > lib.FILING_LAG_MAX:
                    continue
                key = (cid, b["fd"])
                e = ev.setdefault(key, {"co": cid, "fd": b["fd"], "kinds": set(), "after": False, "filers": set()})
                e["kinds"].add(b["kind"])
                e["filers"].add(b["filer"])
                if in_post(post, cid, b["td"]):
                    e["after"] = True
        fwd = []
        for (cid, fd), e in sorted(ev.items()):
            t, how = price_ticker(cid, qc["primary"], qc["tick_co"], cache)
            if not t or not cache.get(t):
                continue
            P = cache.get(t)
            a = px_on_or_after(P, fd)
            if not a or (a[0] - fd).days > 5:
                continue
            row = {
                "company_id": cid,
                "name": qc["names"].get(cid, cid),
                "ticker": t,
                "ticker_choice": how,
                "price_src": P["src"],
                "currency": P["cur"],
                "filed_date": fd.isoformat(),
                "entry_date": a[0].isoformat(),
                "filers": len(e["filers"]),
                "kind": "open_market" if e["kinds"] == {"open_market"} else (
                    "placement_or_private" if e["kinds"] == {"placement_or_private"} else "mixed"
                ),
                "claim_linked": int(cid in claim_cos),
                "after_cluster": int(e["after"]),
            }
            for h in (30, 90, 180):
                end = a[0] + timedelta(days=h)
                if end > last:
                    row[f"r{h}"] = row[f"x_spy{h}"] = row[f"x_gld{h}"] = row[f"x_bsk{h}"] = None
                    continue
                rr = ret(P, a[0], end)
                ss = ret(spy, a[0], end)
                gg = ret(gld, a[0], end) if gld else None
                kk = basket_ret(a[0], end)
                row[f"r{h}"] = rr
                row[f"x_spy{h}"] = None if rr is None or ss is None else rr - ss
                row[f"x_gld{h}"] = None if rr is None or gg is None else rr - gg
                row[f"x_bsk{h}"] = None if rr is None or kk is None else rr - kk
            fwd.append(row)
        return fwd

    fwd = forward_for(post_main)
    cl = [r for r in fwd if r["claim_linked"]]
    fsum = []
    fsum += ssum([r for r in cl if r["after_cluster"]], "claims_linked|after_cluster|all")
    fsum += ssum([r for r in cl if not r["after_cluster"]], "claims_linked|other|all")
    fsum += ssum([r for r in cl if r["after_cluster"] and r["kind"] == "open_market"], "claims_linked|after_cluster|open_market")
    fsum += ssum([r for r in cl if not r["after_cluster"] and r["kind"] == "open_market"], "claims_linked|other|open_market")
    fsum += ssum([r for r in fwd if not r["claim_linked"]], "not_claims_linked|all")

    mw = {}
    for h in (30, 90, 180):
        a = [r[f"x_spy{h}"] for r in cl if r["after_cluster"] and r[f"x_spy{h}"] is not None]
        b = [r[f"x_spy{h}"] for r in cl if not r["after_cluster"] and r[f"x_spy{h}"] is not None]
        mw[h] = mwu(a, b) if a and b else {"n_after": 0, "n_other": 0, "U": None, "z": None, "p_two_sided": None}

    cos_sum = []
    for h in (30, 90, 180):
        for g in (1, 0):
            by = defaultdict(list)
            for r in cl:
                if r["after_cluster"] == g and r.get(f"x_spy{h}") is not None:
                    by[r["company_id"]].append(r[f"x_spy{h}"])
            m = [st.median(v) for v in by.values()]
            cos_sum.append({
                "horizon_days": h,
                "group": "after_cluster" if g else "other",
                "companies": len(m),
                "median_of_company_medians_vs_spy_pct": round(100 * st.median(m), 1) if m else None,
                "share_companies_beat_spy_pct": round(100 * sum(x > 0 for x in m) / len(m), 1) if m else None,
            })

    def pack_sens(label, clusters, summaries, post):
        all_s = next(x for x in summaries if x["test"].endswith("all_buys"))
        om_s = next(x for x in summaries if x["test"].endswith("open_market"))
        rr = rate_test(post, buys_by_co, ALL, r0, r1, qc["names"], "tmp")
        for r in rr:
            r["_E"] = r["buy_days_total"] * r["post_days"] / (r["post_days"] + r["base_days"]) if r["base_days"] else 0
            r["_d"] = r["buy_days_post"] - r["_E"]
        rr.sort(key=lambda r: -r["_d"])
        def ratio(xs):
            e = sum(x["_E"] for x in xs)
            return round(sum(x["buy_days_post"] for x in xs) / e, 2) if e else None
        wb = [r for r in rr if r["buy_days_total"] > 0]
        up = sum(1 for r in wb if r["rate_post_per_100d"] > r["rate_base_per_100d"])
        dn = sum(1 for r in wb if r["rate_post_per_100d"] < r["rate_base_per_100d"])
        fwd_s = forward_for(post)
        cl_s = [r for r in fwd_s if r["claim_linked"]]
        fs = ssum([r for r in cl_s if r["after_cluster"]], "a") + ssum([r for r in cl_s if not r["after_cluster"]], "o")
        fmap = {(row["group"], row["horizon_days"]): row for row in fs}
        mw_s = {}
        for h in (30, 90, 180):
            aa = [r[f"x_spy{h}"] for r in cl_s if r["after_cluster"] and r[f"x_spy{h}"] is not None]
            bb = [r[f"x_spy{h}"] for r in cl_s if not r["after_cluster"] and r[f"x_spy{h}"] is not None]
            mw_s[h] = mwu(aa, bb) if aa and bb else {"p_two_sided": None}
        return {
            "cluster_rule": label,
            "clusters": len(clusters),
            "companies": len({c["company_id"] for c in clusters}),
            "buy_days": all_s["buy_days"],
            "post_obs": all_s["observed_post"],
            "post_exp": all_s["expected_post"],
            "ratio_all": all_s["ratio"],
            "p_all": all_s["p_one_sided"],
            "ratio_open_market": om_s["ratio"],
            "ratio_drop_top3": ratio(rr[3:]),
            "cos_higher": up,
            "cos_lower": dn,
            "sign_p": sign_p(up, up + dn),
            "after_n_30": fmap[("a", 30)]["n_events"],
            "after_cos_30": fmap[("a", 30)]["n_companies"],
            "after_med_vs_spy_30": fmap[("a", 30)]["median_vs_spy_pct"],
            "other_med_vs_spy_30": fmap[("o", 30)]["median_vs_spy_pct"],
            "mwu_p_30": mw_s[30]["p_two_sided"],
            "after_n_90": fmap[("a", 90)]["n_events"],
            "after_cos_90": fmap[("a", 90)]["n_companies"],
            "after_med_vs_spy_90": fmap[("a", 90)]["median_vs_spy_pct"],
            "other_med_vs_spy_90": fmap[("o", 90)]["median_vs_spy_pct"],
            "mwu_p_90": mw_s[90]["p_two_sided"],
            "after_n_180": fmap[("a", 180)]["n_events"],
            "after_cos_180": fmap[("a", 180)]["n_companies"],
            "after_med_vs_spy_180": fmap[("a", 180)]["median_vs_spy_pct"],
            "other_med_vs_spy_180": fmap[("o", 180)]["median_vs_spy_pct"],
            "mwu_p_180": mw_s[180]["p_two_sided"],
        }

    print("sensitivity (25+ and 50+; extra forward passes)...")
    post_25 = post_windows(clusters_25)
    post_50 = post_windows(clusters_50)
    sensitivity = [
        pack_sens("10 claims, 2x mean (main)", clusters_main, rate_sum, post_main),
        pack_sens("25 claims, 2x mean", clusters_25, rate_25, post_25),
        pack_sens("50 claims, 3x mean", clusters_50, rate_50, post_50),
    ]

    print("juniors vs majors (bbox distance)...")
    juniors = juniors_near_majors(scan, qc, buys_by_co, lib.RATE_WIN0)

    # Comparison slices for the note
    totals = [r for r in comparison if r["province"] == "all" and r["linked"] == "yes"]
    new_to = [r for r in totals if r["change_type"] == "new_to_data"]
    missing = [r for r in totals if r["change_type"] == "missing_from_data"]
    new_to.sort(key=lambda r: -int(r["new_claims"]))
    missing.sort(key=lambda r: -int(r["old_claims"]))

    def provinces_for(company):
        return "/".join(
            sorted(r["province"] for r in comparison if r["company"] == company and r["province"] != "all" and r["new_claims"])
        )

    new_to_list = [{
        "company": r["company"], "ticker": r["ticker"], "company_type": r["company_type"],
        "new_claims": int(r["new_claims"]), "provinces": provinces_for(r["company"]),
    } for r in new_to]
    miss_list = [{
        "company": r["company"], "ticker": r["ticker"], "company_type": r["company_type"],
        "old_claims": int(r["old_claims"]),
    } for r in missing]

    linked_prov = [r for r in comparison if r["linked"] == "yes" and r["province"] != "all"]
    window8 = sorted(linked_prov, key=lambda r: -abs(int(r["change"])))[:12]
    window8_out = [{
        "company": r["company"], "ticker": r["ticker"], "province": r["province"],
        "old_claims": int(r["old_claims"]), "new_claims": int(r["new_claims"]), "change": int(r["change"]),
    } for r in window8]

    n_linked = len({r["key"] for r in comparison if r["linked"] == "yes"})
    n_unlinked = len({r["key"] for r in comparison if r["linked"] == "no"})

    jn = {
        "n_rows": len(juniors),
        "n_juniors": len({r["junior_id"] for r in juniors}),
        "n_majors": len({r["major_id"] for r in juniors}),
        "n_provinces": len({r["province"] for r in juniors}),
        "n_within_2km": sum(1 for r in juniors if r["junior_claims_within_2km"] > 0),
        "n_juniors_with_buys": len({r["junior_id"] for r in juniors if r["insider_buy_days_since_2025-04-01"] > 0}),
    }

    fields_cmp = [
        "company", "ticker", "company_type", "province", "old_claims", "new_claims", "change",
        "change_pct", "new_area_ha", "old_area_ha", "added", "dropped", "change_type",
        "old_source", "linked", "basis", "id_gap", "company_id",
    ]
    print("writing files...")
    wcsv(HERE / "comparison.csv", comparison, fields_cmp)
    compact = compact_viewer_payload(comparison)
    json_text = json.dumps(compact, separators=(",", ":"), ensure_ascii=False)
    json_bytes = len(json_text.encode("utf-8"))
    (HERE / "comparison.json").write_text(json_text, encoding="utf-8")
    print("comparison.json bytes", json_bytes, "limit", 1048576, "ok", json_bytes <= 1048576)

    wcsv(HERE / "clusters.csv", [
        {**c, "anchor": c["anchor"].isoformat()} for c in clusters_main
    ])
    wcsv(HERE / "rate-by-company.csv", rate_rows)
    wcsv(HERE / "rate-summary.csv", rate_sum)
    wcsv(HERE / "rate-robustness.csv", robust)
    # keep buy-events to claim-linked only (smaller)
    buy_events = []
    for r in cl:
        buy_events.append({k: (round(v, 6) if isinstance(v, float) else v) for k, v in r.items()})
    wcsv(HERE / "buy-events.csv", buy_events)
    wcsv(HERE / "forward-summary.csv", fsum)
    wcsv(HERE / "forward-by-company.csv", cos_sum)
    wcsv(HERE / "sensitivity.csv", sensitivity)
    jfields = [
        "junior", "junior_ticker", "province", "nearest_major", "major_ticker", "min_distance_km",
        "junior_claims_within_5km", "junior_claims_within_2km", "junior_total_claims",
        "insider_buy_days_since_2025-04-01", "junior_type", "major_type", "junior_id", "major_id",
    ]
    wcsv(HERE / "juniors.csv", juniors, jfields)
    unlinked_new = [
        {"company": r["company"], "ticker": r["ticker"], "company_type": r["company_type"],
         "old_claims": r["old_claims"], "new_claims": r["new_claims"], "change": r["change"], "linked": r["linked"]}
        for r in comparison
        if r["province"] == "all" and r["linked"] == "no" and r["change_type"] == "new_to_data"
    ]
    unlinked_miss = [
        {"company": r["company"], "ticker": r["ticker"], "company_type": r["company_type"],
         "old_claims": r["old_claims"], "new_claims": r["new_claims"], "change": r["change"], "linked": r["linked"]}
        for r in comparison
        if r["province"] == "all" and r["linked"] == "no" and r["change_type"] == "missing_from_data"
    ]
    unlinked_new.sort(key=lambda r: -int(r["new_claims"]))
    unlinked_miss.sort(key=lambda r: -int(r["old_claims"]))
    ul_keys = ["company", "ticker", "company_type", "old_claims", "new_claims", "change", "linked"]
    wcsv(HERE / "new-to-data.csv", unlinked_new, ul_keys)
    wcsv(HERE / "missing-from-data.csv", unlinked_miss, ul_keys)

    claim_id_rows = [r for r in comparison if r["basis"] == "claim_ids"]
    gap_rows = [r for r in claim_id_rows if r["id_gap"] != "" and int(r["id_gap"]) != 0]
    fq = next(
        (r for r in comparison if r["company"] == "First Quantum Minerals" and r["province"] == "ontario"),
        None,
    )
    print("claim_ids rows", len(claim_id_rows), "id_gap nonzero", len(gap_rows))
    print("First Quantum Ontario", fq)

    sanity = {
        "new_titles": 898104,
        "old_a_titles": 898668,
        "new_by_province": dict(new_by_prov),
        "old_by_province": dict(old_by_prov),
        "counts_json": expected_new,
        "old_a_provinces": expected_old,
        "comparison_rows": len(comparison),
        "comparison_json_rows": len(compact["rows"]),
        "comparison_json_bytes": json_bytes,
        "comparison_json_under_1mb": json_bytes <= 1048576,
        "juniors_rows": len(juniors),
        "clusters_main": len(clusters_main),
        "buy_events": len(buy_events),
        "new_equals_counts": dict(new_by_prov) == expected_new,
        "old_equals_old_a": dict(old_by_prov) == expected_old,
        "viewer_rows_match": len(compact["rows"]) == len(comparison),
        "claim_ids_rows": len(claim_id_rows),
        "claim_ids_gap_rows": len(gap_rows),
        "first_quantum_ontario": {
            "old_claims": fq["old_claims"] if fq else None,
            "new_claims": fq["new_claims"] if fq else None,
            "change": fq["change"] if fq else None,
            "added": fq["added"] if fq else None,
            "dropped": fq["dropped"] if fq else None,
            "basis": fq["basis"] if fq else None,
        },
    }
    (HERE / "sanity.json").write_text(json.dumps(sanity, indent=2), encoding="utf-8")

    rate_main_all = next(x for x in rate_sum if x["test"].endswith("all_buys"))
    rate_main_om = next(x for x in rate_sum if x["test"].endswith("open_market"))
    rate_main_pl = next(x for x in rate_sum if x["test"].endswith("placements"))
    stats = {
        "rate_main_all": rate_main_all,
        "rate_main_om": rate_main_om,
        "rate_main_pl": rate_main_pl,
        "forward": fsum,
        "sensitivity": sensitivity,
        "robust_all": next(x for x in robust if x["test"] == "all_buys"),
        "mw": mw,
        "juniors_summary": jn,
        "juniors_closest": juniors[:15],
        "window8_top": window8_out,
        "new_to_data": new_to_list,
        "missing_from_data": miss_list,
        "comparison_rows": len(comparison),
        "n_linked_companies": n_linked,
        "n_unlinked_companies": n_unlinked,
        "n_new_to_data_linked": len(new_to),
        "n_missing_linked": len(missing),
        "n_clusters": len(clusters_main),
        "n_cluster_companies": len({c["company_id"] for c in clusters_main}),
        "issue_kept_total": sum(scan["issue_kept"].values()),
        "issue_drop_total": sum(scan["issue_drop"].values()),
        "prov_totals": [(p, expected_old[p], expected_new[p]) for p in lib.PROVINCES],
        "old_a_titles": 898668,
        "new_titles": 898104,
    }
    # NOTE.md is curated from these stats in short sentences. Do not overwrite it here.
    print("comparison_rows", len(comparison))
    print("juniors_rows", len(juniors))
    print("clusters", len(clusters_main), "companies", stats["n_cluster_companies"])
    print("buy_events", len(buy_events))
    print("rate", rate_main_all)
    print("wrote", HERE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
