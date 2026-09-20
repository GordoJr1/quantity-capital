"""Fast public-issuer linker for qc.sqlite claim parties."""
from __future__ import annotations

import csv
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

DB = Path(r"C:\Users\gordo\Desktop\quantity-capital\qc.sqlite")
OUT = Path(r"C:\Users\gordo\Desktop\quantity-capital\scripts\qc_sqlite")
ALIAS_PATH = OUT / "public_issuer_aliases.csv"
REVIEW_PATH = OUT / "export" / "public_issuer_needs_review.csv"
REPORT_PATH = OUT / "export" / "public_issuer_link_report.json"

STOP = {
    "INC","INCORPORATED","CORP","CORPORATION","LTD","LIMITED","LLC","LLP","LP","PLC","CO","COMPANY",
    "THE","AND","OF","MINES","MINE","MINING","GOLD","RESOURCES","RESOURCE","EXPLORATION","EXPLORATIONS",
    "SOCIETE","MINIERE","MINIER","LTEE","LIMITEE",
}
PCT_PREFIX = re.compile(r"^\((\d{1,3})\)\s*")

def fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.upper()
    s = re.sub(r"[^A-Z0-9\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()

def strip_pct(s: str) -> tuple[str, float | None]:
    m = PCT_PREFIX.match(s or "")
    if not m:
        return (s or "").strip(), None
    return s[m.end():].strip(), float(m.group(1))

def tokens(s: str) -> list[str]:
    return [t for t in fold(s).split() if t and t not in STOP and not t.isdigit()]

def core_key(s: str) -> str:
    name, _ = strip_pct(s)
    toks = tokens(name)
    return " ".join(toks) if toks else fold(name)

BUILTIN = {
    "PRETIUM": "newmont", "PRETIUM RESOURCES": "newmont", "BW GOLD": "artemis-gold",
    "THOMPSON CREEK": "centerra-gold", "THOMPSON CREEK METALS": "centerra-gold",
    "GREENSTONE": "equinox-gold", "GREENSTONE GOLD": "equinox-gold", "MUSSELWHITE": "equinox-gold",
    "AGNICO EAGLE": "agnico-eagle", "AGNICO EAGLE MINES": "agnico-eagle",
    "AGNICO EAGLE ABITIBI ACQUISITION": "agnico-eagle",
    "IAMGOLD": "iamgold", "ALAMOS": "alamos-gold", "ALAMOS GOLD": "alamos-gold",
    "WESDOME": "wesdome-gold-mines", "WESDOME GOLD": "wesdome-gold-mines",
    "BARRICK": "barrick", "BARRICK GOLD": "barrick",
    "ELDORADO": "eldorado-gold", "ELDORADO GOLD": "eldorado-gold",
    "GOLD FIELDS": "gold-fields", "NEWMONT": "newmont", "CENTERRA": "centerra-gold",
    "ARTEMIS GOLD": "artemis-gold", "EVOLUTION": "evolution", "EVOLUTION MINING": "evolution",
    "EQUINOX GOLD": "equinox-gold",
}

def load_aliases():
    aliases = dict(BUILTIN)
    if ALIAS_PATH.exists():
        with ALIAS_PATH.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                k = core_key(row.get("holder_alias") or "")
                v = (row.get("company_id") or "").strip()
                if k and v:
                    aliases[k] = v
    return aliases

def ensure_indexes(con: sqlite3.Connection) -> None:
    con.execute("CREATE INDEX IF NOT EXISTS idx_ctp_title_pk ON claim_title_parties(title_pk)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ctp_holder_co ON claim_title_parties(holder_company_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ct_pack ON claim_titles(pack_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ct_holder_co ON claim_titles(holder_company_id)")
    con.commit()
    print("indexes ready", flush=True)

def seed_province_parties(con: sqlite3.Connection) -> int:
    # Anti-join once into a temp table (avoids per-row NOT EXISTS rescans).
    con.execute("DROP TABLE IF EXISTS _seed_need")
    print("building seed set…", flush=True)
    con.execute(
        """
        CREATE TEMP TABLE _seed_need AS
        SELECT t.title_pk, t.pack_id, t.holder_raw
        FROM claim_titles t
        LEFT JOIN claim_title_parties p ON p.title_pk = t.title_pk
        WHERE t.pack_id IN ('province:ontario','province:bc')
          AND t.holder_raw IS NOT NULL AND TRIM(t.holder_raw) != ''
          AND p.title_pk IS NULL
        """
    )
    con.commit()
    need = con.execute("SELECT COUNT(*) FROM _seed_need").fetchone()[0]
    print(f"seed_need={need}", flush=True)

    cur = con.execute("SELECT title_pk, pack_id, holder_raw FROM _seed_need")
    batch, n = [], 0
    while True:
        rows = cur.fetchmany(25000)
        if not rows:
            break
        for title_pk, pack_id, holder_raw in rows:
            name, pct = strip_pct(holder_raw)
            batch.append((title_pk, pack_id, name or holder_raw, pct, None, "province_seed"))
        con.executemany(
            """INSERT INTO claim_title_parties(title_pk, pack_id, holder_name, interest_pct, holder_company_id, source)
               VALUES (?,?,?,?,?,?)""",
            batch,
        )
        con.commit()
        n += len(batch)
        print(f"seeded {n}/{need}", flush=True)
        batch = []
    con.execute("DROP TABLE IF EXISTS _seed_need")
    con.commit()
    return n

def mirror_holder_company(con: sqlite3.Connection) -> int:
    print("mirroring to claim_titles…", flush=True)
    con.execute("DROP TABLE IF EXISTS _link_map")
    con.execute(
        """
        CREATE TEMP TABLE _link_map AS
        SELECT title_pk, MIN(holder_company_id) AS holder_company_id
        FROM claim_title_parties
        WHERE holder_company_id IS NOT NULL
        GROUP BY title_pk
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_link_map_pk ON _link_map(title_pk)")
    before = con.total_changes
    con.execute(
        """
        UPDATE claim_titles
        SET holder_company_id = (
          SELECT m.holder_company_id FROM _link_map m WHERE m.title_pk = claim_titles.title_pk
        )
        WHERE holder_company_id IS NULL
          AND title_pk IN (SELECT title_pk FROM _link_map)
        """
    )
    con.commit()
    changed = con.total_changes - before
    con.execute("DROP TABLE IF EXISTS _link_map")
    con.commit()
    print(f"mirrored_titles={changed}", flush=True)
    return changed

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "export").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=120)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA temp_store=MEMORY")
    con.execute("PRAGMA cache_size=-200000")
    ensure_indexes(con)
    seeded = seed_province_parties(con)
    print(f"seeded_province_parties={seeded}", flush=True)

    aliases = load_aliases()
    public_ids = {r[0] for r in con.execute("SELECT DISTINCT company_id FROM company_tickers")}
    caps = dict(con.execute(
        """SELECT ct.company_id, MAX(COALESCE(t.market_cap,0))
           FROM company_tickers ct LEFT JOIN tickers t ON t.ticker=ct.ticker GROUP BY ct.company_id"""
    ))

    exact = {}
    token_inv = defaultdict(set)
    for cid, name, holder in con.execute("SELECT company_id, name, holder FROM companies"):
        if cid not in public_ids:
            continue
        for field in (name, holder, cid.replace("-", " ")):
            if not field:
                continue
            ck = core_key(field)
            exact.setdefault(ck, set()).add(cid)
            exact.setdefault(fold(field), set()).add(cid)
            for t in tokens(field):
                if len(t) >= 5:
                    token_inv[t].add(cid)
    try:
        for cid, name in con.execute("SELECT company_id, name FROM company_names"):
            if cid in public_ids and name:
                exact.setdefault(core_key(name), set()).add(cid)
                for t in tokens(name):
                    if len(t) >= 5:
                        token_inv[t].add(cid)
    except sqlite3.OperationalError:
        pass

    unique_tokens = {t: next(iter(cos)) for t, cos in token_inv.items() if len(cos) == 1}

    already = con.execute("SELECT COUNT(*) FROM claim_title_parties WHERE holder_company_id IS NOT NULL").fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM claim_title_parties").fetchone()[0]
    unmatched = list(con.execute(
        "SELECT id, holder_name FROM claim_title_parties WHERE holder_company_id IS NULL"
    ))
    print(f"unmatched={len(unmatched)} total={total} already={already}", flush=True)

    updates = []
    no_match_names = Counter()
    amb_names = Counter()
    linked = 0

    for pid, name in unmatched:
        ck = core_key(name or "")
        cands = set()
        src = None
        if ck in aliases and aliases[ck] in public_ids:
            cands = {aliases[ck]}; src = "alias"
        elif ck in exact:
            cands = set(exact[ck]); src = "name_exact"
        else:
            toks = [t for t in tokens(strip_pct(name or "")[0]) if len(t) >= 5]
            hits = {unique_tokens[t] for t in toks if t in unique_tokens}
            if len(hits) == 1:
                cands = hits; src = "token_unique"

        if len(cands) == 1:
            updates.append((next(iter(cands)), f"public_py:{src}", pid))
            linked += 1
        elif len(cands) > 1:
            ranked = sorted(cands, key=lambda x: caps.get(x, 0), reverse=True)
            if caps.get(ranked[0], 0) > 3 * max(caps.get(ranked[1], 0), 1):
                updates.append((ranked[0], "public_py:name_mcap_tiebreak", pid))
                linked += 1
            else:
                amb_names[name or ""] += 1
        else:
            no_match_names[name or ""] += 1

        if len(updates) >= 25000:
            con.executemany(
                """UPDATE claim_title_parties SET holder_company_id=?, source=COALESCE(source,?)
                   WHERE id=? AND holder_company_id IS NULL""",
                updates,
            )
            con.commit()
            print(f"flushed {linked}", flush=True)
            updates = []

    if updates:
        con.executemany(
            """UPDATE claim_title_parties SET holder_company_id=?, source=COALESCE(source,?)
               WHERE id=? AND holder_company_id IS NULL""",
            updates,
        )
        con.commit()

    mirror_holder_company(con)

    linked_now = con.execute("SELECT COUNT(*) FROM claim_title_parties WHERE holder_company_id IS NOT NULL").fetchone()[0]
    unlinked = total - linked_now
    # refresh total in case seed grew it
    total = con.execute("SELECT COUNT(*) FROM claim_title_parties").fetchone()[0]
    unlinked = total - linked_now
    titles_total = con.execute("SELECT COUNT(*) FROM claim_titles").fetchone()[0]
    titles_linked = con.execute("SELECT COUNT(*) FROM claim_titles WHERE holder_company_id IS NOT NULL").fetchone()[0]
    dh = con.execute("SELECT COUNT(DISTINCT holder_name) FROM claim_title_parties").fetchone()[0]
    dh_l = con.execute("SELECT COUNT(DISTINCT holder_name) FROM claim_title_parties WHERE holder_company_id IS NOT NULL").fetchone()[0]

    with REVIEW_PATH.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["holder_name", "count", "reason"])
        w.writeheader()
        for name, n in (no_match_names + amb_names).most_common(150):
            reason = "ambiguous" if name in amb_names else "unlinked"
            w.writerow({"holder_name": name, "count": n, "reason": reason})

    report = {
        "seeded_province_parties": seeded,
        "party_rows_total": total,
        "already_linked_before": already,
        "newly_linked_this_pass": linked,
        "party_rows_linked_now": linked_now,
        "party_rows_unlinked_now": unlinked,
        "pct_parties_linked": round(100 * linked_now / total, 2) if total else 0,
        "claim_titles_total": titles_total,
        "claim_titles_linked": titles_linked,
        "pct_titles_linked": round(100 * titles_linked / titles_total, 2) if titles_total else 0,
        "distinct_holders": dh,
        "distinct_holders_linked": dh_l,
        "ambiguous_holder_names": len(amb_names),
        "unlinked_holder_names": len(no_match_names),
        "jev": "not used",
        "review_csv": str(REVIEW_PATH),
        "top_unlinked": no_match_names.most_common(12),
    }
    REPORT_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2), flush=True)

if __name__ == "__main__":
    main()
