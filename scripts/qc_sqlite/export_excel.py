"""Excel export of the SQLite brain (attributes/links/calcs — no geometry)."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from paths import DB_PATH, QC_ROOT

OUT_BRAIN = QC_ROOT / "quantity-capital-brain.xlsx"
OUT_CLAIMS = QC_ROOT / "quantity-capital-claims.xlsx"


def log(msg: str) -> None:
    print(msg, flush=True)


def sheet(ws, headers, rows, max_rows=20000):
    from openpyxl.styles import Font

    bold = Font(bold=True)
    for i, h in enumerate(headers, 1):
        cell = ws.cell(1, i, h)
        cell.font = bold
    for r_i, row in enumerate(rows[:max_rows], 2):
        for c_i, val in enumerate(row, 1):
            if isinstance(val, (dict, list)):
                val = json.dumps(val)
            if isinstance(val, str) and len(val) > 32000:
                val = val[:32000]
            ws.cell(r_i, c_i, val)


def q(con, sql, args=()):
    return list(con.execute(sql, args))


def export(con: sqlite3.Connection) -> tuple[Path, Path]:
    try:
        from openpyxl import Workbook
    except ImportError:
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl"])
        from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "companies"
    rows = q(con, "SELECT company_id, name, holder, commodity, company_type, country, quebec_count, ontario_count, bc_count, claim_count, sources_json FROM companies ORDER BY (claim_count IS NULL), claim_count DESC, company_id")
    sheet(ws, ["company_id", "name", "holder", "commodity", "type", "country", "quebec", "ontario", "bc", "claim_count", "sources"], rows)

    ws = wb.create_sheet("tickers")
    rows = q(con, "SELECT ticker, name, industry, market_cap, px, cap_asof FROM tickers ORDER BY ticker LIMIT 20000")
    sheet(ws, ["ticker", "name", "industry", "market_cap", "px", "cap_asof"], rows)

    ws = wb.create_sheet("claim_titles")
    rows = q(con, "SELECT title_pk, pack_id, company_id, jurisdiction, role, claim_id, holder_raw, status, recorded_date, anniversary_or_expiry, area_ha, tenure_type FROM claim_titles LIMIT 20000")
    sheet(ws, ["title_pk", "pack_id", "company_id", "jurisdiction", "role", "claim_id", "holder", "status", "recorded", "expiry", "area_ha", "tenure_type"], rows)

    ws = wb.create_sheet("claim_company_links")
    rows = q(con, "SELECT pack_id, company_id, link_role, source, jev_outcome, jev_score, jev_confidence FROM claim_company_links")
    sheet(ws, ["pack_id", "company_id", "link_role", "source", "jev_outcome", "jev_score", "jev_confidence"], rows)

    ws = wb.create_sheet("trade_size_vs_cap")
    rows = q(con, "SELECT trade_id, ticker, company_id, filer, side, trade_date, amount_raw, amount_mid, market_cap, size_bps, jev_flag FROM trade_size_vs_cap WHERE size_bps IS NOT NULL ORDER BY size_bps DESC LIMIT 500")
    sheet(ws, ["trade_id", "ticker", "company_id", "filer", "side", "trade_date", "amount", "mid", "market_cap", "size_bps", "jev_flag"], rows)

    ws = wb.create_sheet("tell_hands")
    rows = q(con, "SELECT filer_id, name, chamber, scored, hits, rate, median, score, rank FROM tell_hands ORDER BY rank")
    sheet(ws, ["filer_id", "name", "chamber", "scored", "hits", "rate", "median", "score", "rank"], rows)

    ws = wb.create_sheet("analysis_book")
    rows = q(con, "SELECT ticker, name, action, score, why, heat_rank, list, jev_ship, jev_action, shipped FROM analysis_candidates WHERE shipped=1 ORDER BY list, score DESC")
    sheet(ws, ["ticker", "name", "action", "score", "why", "heat_rank", "list", "jev_ship", "jev_action", "shipped"], rows)

    ws = wb.create_sheet("jev_decisions")
    rows = q(con, "SELECT created_at, subject_type, subject_id, question_id, primitive, model, substr(answer_json,1,400) FROM jev_decisions ORDER BY id DESC LIMIT 2000")
    sheet(ws, ["created_at", "subject_type", "subject_id", "question_id", "primitive", "model", "answer_json"], rows)

    wb.save(OUT_BRAIN)
    log(f"Wrote {OUT_BRAIN}")

    claims = Workbook()
    cws = claims.active
    cws.title = "claim_titles"
    rows = q(con, "SELECT title_pk, pack_id, company_id, jurisdiction, role, claim_id, holder_raw, status, recorded_date, anniversary_or_expiry, area_ha, tenure_type, extract_path FROM claim_titles LIMIT 50000")
    sheet(cws, ["title_pk", "pack_id", "company_id", "jurisdiction", "role", "claim_id", "holder", "status", "recorded", "expiry", "area_ha", "type", "extract"], rows, max_rows=50000)
    cws2 = claims.create_sheet("links")
    rows = q(con, "SELECT pack_id, company_id, link_role, source, jev_outcome, jev_score FROM claim_company_links")
    sheet(cws2, ["pack_id", "company_id", "link_role", "source", "jev_outcome", "jev_score"], rows)
    cws3 = claims.create_sheet("companies")
    rows = q(con, "SELECT company_id, name, holder, quebec_count, ontario_count, bc_count, claim_count FROM companies WHERE ifnull(claim_count,0)+ifnull(quebec_count,0)+ifnull(ontario_count,0)+ifnull(bc_count,0)>0 ORDER BY claim_count DESC")
    sheet(cws3, ["company_id", "name", "holder", "quebec", "ontario", "bc", "claim_count"], rows)
    claims.save(OUT_CLAIMS)
    log(f"Wrote {OUT_CLAIMS}")
    return OUT_BRAIN, OUT_CLAIMS


def run(con: sqlite3.Connection) -> None:
    export(con)


def main() -> int:
    if not DB_PATH.exists():
        log(f"missing {DB_PATH}")
        return 1
    con = sqlite3.connect(str(DB_PATH))
    try:
        export(con)
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
