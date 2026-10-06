#!/usr/bin/env python3
"""qc_sqlite regression tests — temp databases, no network, no TypeSafe."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import paper  # noqa: E402
import rebuild  # noqa: E402
from paths import SCHEMA_SQL  # noqa: E402


def fresh(path: Path) -> sqlite3.Connection:
    con = rebuild.connect(path)
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return con


def seed_province(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO companies(company_id, name) VALUES ('_province_bc', 'BC all holders')")
    con.execute("INSERT INTO companies(company_id, name) VALUES ('teck', 'Teck')")
    con.execute("INSERT INTO companies(company_id, name) VALUES ('gone-issuer', 'Gone')")
    con.execute(
        "INSERT INTO claim_packs(pack_id, company_id, role, holder, holder_company_id, claim_count) "
        "VALUES ('province:bc', '_province_bc', 'focus', 'BC', '_province_bc', 2)"
    )
    for pk, holder_cid in (("province:bc:focus:1", "teck"), ("province:bc:focus:2", "gone-issuer")):
        con.execute(
            "INSERT INTO claim_titles(title_pk, pack_id, company_id, holder_company_id, jurisdiction, claim_id, as_of) "
            "VALUES (?, 'province:bc', '_province_bc', ?, 'British Columbia', ?, '2026-09-01')",
            (pk, holder_cid, pk[-1]),
        )
        con.execute(
            "INSERT INTO claim_title_parties(title_pk, pack_id, holder_name, holder_company_id, source) "
            "VALUES (?, 'province:bc', 'X', ?, 'province_seed')",
            (pk, holder_cid),
        )
    con.execute("INSERT INTO meta(key, value) VALUES ('province_bc_offset_A', '4000')")
    con.commit()


class CarryForward(unittest.TestCase):
    def test_province_rows_survive_full_rebuild(self):
        with tempfile.TemporaryDirectory() as d:
            old_path, new_path = Path(d) / "qc.sqlite", Path(d) / "qc.sqlite.tmp"
            old = fresh(old_path)
            seed_province(old)
            old.close()
            new = fresh(new_path)
            new.execute("INSERT INTO companies(company_id, name) VALUES ('teck', 'Teck Resources')")
            new.commit()
            with redirect_stdout(io.StringIO()):
                counts = rebuild.carry_forward_side_tables(new, old_path)
            self.assertEqual(counts["claim_titles"], 2)
            self.assertEqual(counts["claim_title_parties"], 2)
            self.assertEqual(new.execute("SELECT value FROM meta WHERE key='province_bc_offset_A'").fetchone()[0], "4000")
            self.assertEqual(new.execute("SELECT name FROM companies WHERE company_id='teck'").fetchone()[0], "Teck Resources")
            self.assertEqual(new.execute("PRAGMA foreign_key_check").fetchall(), [])
            new.close()

    def test_missing_old_db_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            new = fresh(Path(d) / "new.sqlite")
            self.assertEqual(rebuild.carry_forward_side_tables(new, Path(d) / "absent.sqlite"), {})
            new.close()


class Paper(unittest.TestCase):
    def test_reads_board_and_never_writes_json(self):
        board = {
            "generated": "2026-09-24T00:00:00+00:00",
            "filers": [{"id": "a", "name": "A", "chamber": "House", "n": 1, "skip": 0, "avg": 0.1, "med": 0.1, "win": 1,
                        "legs": [{"t": "NVDA", "filed": "2026-01-02", "in": 1.0, "out": 1.1, "outD": "2026-09-01", "ret": 0.1}]}],
            "tickers": [{"t": "NVDA", "name": "Nvidia", "n": 1, "people": 1, "avg": 0.1, "med": 0.1, "win": 1}],
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "backtest.json"
            text = json.dumps(board)
            path.write_text(text, encoding="utf-8")
            con = sqlite3.connect(":memory:")
            con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
            with redirect_stdout(io.StringIO()):
                stats = paper.compute_paper(con, skip_jev=True, path=path)
            self.assertFalse(stats["ran"])
            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertEqual(con.execute("SELECT filer_id, ticker, entry_date FROM paper_legs").fetchall(),
                             [("a", "NVDA", "2026-01-02")])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["backtest.json"])

    def test_paper_has_no_json_writer(self):
        src = (HERE / "paper.py").read_text(encoding="utf-8")
        self.assertNotIn("write_text", src)
        self.assertNotIn("_paper_lib", src)


class JevCache(unittest.TestCase):
    def setUp(self):
        import jev

        self.jev = jev
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (jev.JEV_CACHE, jev._cache, jev._dirty)
        jev.JEV_CACHE = Path(self.tmp.name) / "jev.json"
        jev._cache = None

    def tearDown(self):
        self.jev.JEV_CACHE, self.jev._cache, self.jev._dirty = self.saved
        self.tmp.cleanup()

    def test_question_text_changes_key(self):
        a = self.jev._digest({"x": 1}, "lbl", {"q": {"type": "noul", "instructions": "old"}})
        b = self.jev._digest({"x": 1}, "lbl", {"q": {"type": "noul", "instructions": "new"}})
        self.assertNotEqual(a, b)

    def test_corrupt_cache_starts_empty(self):
        self.jev.JEV_CACHE.write_text("{not json", encoding="utf-8")
        with redirect_stdout(io.StringIO()), __import__("contextlib").redirect_stderr(io.StringIO()):
            self.assertEqual(self.jev._cache_load(), {})
        self.assertTrue(self.jev.JEV_CACHE.with_suffix(".corrupt.json").exists())


class AnalysisErrors(unittest.TestCase):
    def test_errored_rows_keep_rules_verdict(self):
        import analysis
        import jev

        book = [
            {"code": "AAA", "action": "watch", "score": 3.0, "flags": []},
            {"code": "BBB", "action": "buy-dip", "score": 2.0, "flags": []},
        ]
        saved = (jev.key_present, analysis.apply_jev_row, jev.store_decision)

        def fake_apply(row):
            if row["code"] == "BBB":
                raise RuntimeError("boom")
            return {"code": row["code"], "packed": {"model": "m", "answers": {"ship": {"choice": "ship"}}}}

        jev.key_present = lambda: True
        analysis.apply_jev_row = fake_apply
        jev.store_decision = lambda *a, **k: None
        try:
            with redirect_stdout(io.StringIO()):
                play, avoid, stats = analysis.gate_with_jev(None, book, skip_jev=False)
        finally:
            jev.key_present, analysis.apply_jev_row, jev.store_decision = saved
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(sorted(r["code"] for r in play), ["AAA", "BBB"])
        self.assertTrue(next(r for r in play if r["code"] == "BBB")["jev_error"])



class AnalysisNames(unittest.TestCase):
    """clean_name strips filing prefixes but keeps real company names."""

    CASES = [
        ("ATRC", "Morgan Stanley - Select UMA Account # 1 AtriCure, Inc. -", "AtriCure, Inc."),
        ("ATRC", "- Select UMA Account # 1 AtriCure, Inc.", "AtriCure, Inc."),
        ("ATRC", "150 Main Street Trust > Bank of America AtriCure, Inc. - Common Stock", "AtriCure, Inc."),
        ("UAL", "Daniel Goldman Grandchildren 1986 Trust > TACS R3K United Airlines Holdings, Inc.", "United Airlines Holdings, Inc."),
        ("HQY", "Daniel Goldman 12/9/2011 Trust > Aperio Group LLC HealthEquity, Inc.", "HealthEquity, Inc."),
        ("KMX", "150 Main Street Trust > Wells Fargo Advisors CarMax Inc", "CarMax Inc"),
        ("TYL", "Fidelity Rollover IRA Tyler Technologies, Inc.", "Tyler Technologies, Inc."),
        ("NYT", "Charles Schwab Brokerage Account 924 New York Times Company", "New York Times Company"),
        ("AEP", "D: Corporate bond American Electric Power Company, Inc.", "American Electric Power Company, Inc."),
        ("AAPL", "Apple Inc. D: sold entire holding", "Apple Inc."),
        ("AAPL", "Smith Family Trust - Apple Inc.", "Apple Inc."),
        ("VOO", "1989 Trust Vanguard S&P 500 ETF", "Vanguard S&P 500 ETF"),
        ("MRNA", "Daniel Goldman Grandchildren 1986 Trust > TLH", "MRNA"),
        # real names must survive
        ("FIS", "Fidelity National Information Services, Inc.", "Fidelity National Information Services, Inc."),
        ("BAC", "Bank of America Corporation", "Bank of America Corporation"),
        ("NTRS", "Northern Trust Corporation", "Northern Trust Corporation"),
        ("HD", "Home Depot, Inc. (The)", "Home Depot, Inc. (The)"),
        ("MDY", "S&P Midcap 400 SPDR", "S&P Midcap 400 SPDR"),
        ("MS", "Morgan Stanley", "Morgan Stanley"),
        ("VNO", "L: US D:", "VNO"),
        ("FQAL", "L: US D: mutual fund Fidelity Quality Factor ETF", "Fidelity Quality Factor ETF"),
        ("IGSB", "D: 11/3/23 Buy 247 shares of EOG Resources, Inc, cusip 26875P101, ticker EOG. iShares 1-5 Year ETF", "iShares 1-5 Year ETF"),
        ("SCHD", "Schwab U.S. Dividend Equity ETF", "Schwab U.S. Dividend Equity ETF"),
        ("VTI", "Fidelity Rollover IRA Vanguard Total Stock Market ETF", "Vanguard Total Stock Market ETF"),
        # Reviewer Bot FAIL on #216 (a1ab6b1): the company comes AFTER a mid-name "D:" note; never the holder.
        ("LAZ", "J French Hill - Revocable Trust D: FULL LIQUIDATION. Lazard, Inc.", "Lazard, Inc."),
        ("LAZ", "J French Hill - Revocable Trust D: FULL LIQUIDATION. Lazard, Inc. Common Stock", "Lazard, Inc."),
        ("BCE", "John Marshall Collins Rollover IRA D: Corporate bond BCE, Inc.", "BCE, Inc."),
        ("WBK", "John Marshall Collins Rollover IRA D: Corporate bond Westpac Banking Corporation", "Westpac Banking Corporation"),
        ("AI", "Trust One D: 1000 shares/loss C3.ai, Inc.", "C3.ai, Inc."),
        ("AI", "Trust One D: 1000 shares/loss C3.ai, Inc. Class A", "C3.ai, Inc."),
        ("AI", "Investment Fund 1 D: Fetal Monitoring Medical Equipment Manufacturing; Palo Alto, CA Rhoda", "AI"),
        ("DBRG", "Trust One D: 60.8 shares DigitalBridge Group, Inc.", "DigitalBridge Group, Inc."),
        ("ICLR", "Kevin Hern Insurance Trust D: Sell to close. ICON plc - Ordinary Shares", "ICON plc"),
        ("ICLR", "Kevin Hern Insurance Trust D: Sell to close. ICON plc -", "ICON plc"),
        ("ICLR", "Kevin Hern Traditional IRA ICON plc - Ordinary Shares", "ICON plc"),
        ("ICLR", "John A. James Children\u2019s Trust ICON plc - Ordinary Shares", "ICON plc"),
        ("BKE", "CRT - Standard Unit Trust D: Account Closing Buckle, Inc.", "Buckle, Inc."),
        ("BKE", "CRT - Standard Unit Trust D: Portfolio Rebalance Buckle, Inc.", "Buckle, Inc."),
        ("PRI", "CRT - Standard Unit Trust D: Account Closing Primerica, Inc. Common Stock", "Primerica, Inc."),
        ("THG", "CRT - Standard Unit Trust D: Portfolio Rebalance Hanover Insurance Group Inc", "Hanover Insurance Group Inc"),
        # bond-only notes: no clean company, so fall back to the ticker (never "CRT - Standard Unit Trust")
        ("FRN", "CRT - Standard Unit Trust D: Account Closing PNC Finl Svc 6.875 10/20/34 '33", "FRN"),
        ("MTN", "CRT - Standard Unit Trust D: Account Closing Wells Fargo 6.491 10/23/34 '33", "MTN"),
        ("MTN", "D: Ticker 8035 JP Vail Resorts, Inc. Common Stock", "Vail Resorts, Inc."),
        ("GP", "L: Caldwell, ID, US D: Own/operate mobile home park Kent Street Group", "GP"),
        ("AEO", "D: Account Closing American Eagle Outfitters, Inc.", "American Eagle Outfitters, Inc."),
        ("AEO", "D: Portfolio Rebalance American Eagle Outfitters, Inc. Common Stock", "American Eagle Outfitters, Inc."),
        ("NVDA", "D: Sold 10,000 shares. NVIDIA Corporation - Common Stock", "NVIDIA Corporation"),
        ("NVDA", "Trust One D: UBS Trust 300 NVIDIA Corporation", "NVIDIA Corporation"),
        ("NVDA", "Trust One D: 20 shares Schwab + 20 more Schwab NVIDIA Corporation", "NVDA"),
        # short real names must not become the ticker (no build-backtest issuer_name)
        ("ACM", "AECOM", "AECOM"),
        ("ACM", "AECOM Common Stock", "AECOM"),
        ("AXAHY", "AXA", "AXA"),
        ("AXAHY", "AXA ADR (AXAHY)", "AXA"),
        ("NVDA", "NVIDIA", "NVIDIA"),
        ("NVDA", "NVIDIA CORPORATION CMN ;", "NVIDIA CORPORATION"),
        # broker words that are part of the real name
        ("CCF", "Chase Corporation", "Chase Corporation"),
        ("FXAIX", "Fidelity 500 Index Fund (FXAIX)", "Fidelity 500 Index Fund"),
        ("FXAIX", "Fidelity 500 Index Fund", "Fidelity 500 Index Fund"),
        ("VFIAX", "Vanguard 500 Index Fund", "Vanguard 500 Index Fund"),
        ("VOO", "Vanguard 500 Index Fund ETF Shares (VOO)", "Vanguard 500 Index Fund ETF Shares"),
        ("UBS", "UBS Group AG Registered Ordinary Shares", "UBS Group AG"),
        ("UBS", "UBS Group AG", "UBS Group AG"),
        ("UBS", "Fisher IRA UBS Group AG Registered Ordinary Shares", "UBS Group AG"),
        ("SCHW", "Charles Schwab Corporation", "Charles Schwab Corporation"),
        # broker / sleeve prefixes
        ("NVDA", "Charles Schwab 401K > Schwab 824 NVIDIA Corporation - Common Stock", "NVIDIA Corporation"),
        ("NVDA", "Rockefeller Capital Management (2) NVIDIA Corporation - Common Stock", "NVIDIA Corporation"),
        ("NVDA", "Merrill Lynch- Advisor Discretion Account- IRA NVIDIA Corporation - Common Stock", "NVIDIA Corporation"),
        ("NVDA", "Morgan Stanley Active Assets (1) NVIDIA Corporation - Common Stock", "NVIDIA Corporation"),
        ("AXAHY", "Daniel Goldman Grandchildren 1986 Trust > TLH ADR AXA SA Sponsored ADR", "AXA SA"),
        ("AAPL", "P 01/02/202501/03/2025$1,001 - $15,000 Apple Inc. - Common Stock", "Apple Inc."),
        ("NVDA", "NextEra Energy, Inc. (NEE) [ST] P 03/08/202203/08/2022$1,001 - $15,000 NVIDIA Corporation", "NVIDIA Corporation"),
        ("ALB", "LIVTR 2000079934SP Albemarle Corporation", "Albemarle Corporation"),
        ("JNJ", "LIVTR Johnson & Johnson Common Stock", "Johnson & Johnson"),
        ("MS", "P 05/14/202505/15/2025$1,001 - $15,000 Morgan Stanley Common Stock", "Morgan Stanley"),
        ("RH", "P 04/04/202504/07/2025$1,001 - $15,000 RH Common Stock", "RH"),
        ("TOIXX", "TOIXX [GS] P 04/16/202505/30/2025$1,001 - $15,000", "TOIXX"),
        ("FAS", "S 06/01/202606/01/2026$1,001 - $15,000", "FAS"),
        ("NSA", "National Storage Affiliates Trust Common Shares of Beneficial Interest D: Exchange of National Storage Affiliates (NSA) for Public Storage (PSA) following acquisition. NVIDIA Corporation - Common Stock (NVDA) [ST] P 07/17/202608/14/2026$1,001 - $15,000 150 Main Street Trust > Bank of America Palantir Technologies Inc. - Class A Common Stock (PLTR) [ST] P", "NSA"),
        ("CHTR", "Charter Communications, Inc. - Class A Common Stock D: Shares received thru merger Liberty Broadband Corporation - Class C Common Stock (LBRDK) [ST] E 08/20/202609/11/2026$1,001 - $15,000 Roth IRA D: Shares surrendered thru merger", "Charter Communications, Inc."),
        ("TSLA", "Shares (TME) [ST] Tesla, Inc.", "Tesla, Inc."),
        ("PTC", "(PAYX) [ST] PTC Inc. - Common Stock", "PTC Inc."),
        ("GIL", "Kean Family Partnership Gildan Activewear, Inc. Class A Sub. Vot. Common Stock", "Gildan Activewear, Inc."),
        ("ACN", "Trust 1 Accenture plc Class A Ordinary Shares D: Asset acquired through a S&P Global (SPGI) spinoff.", "Accenture plc"),
        # idempotence regressions found while fixing
        ("AZO", "AUTOZONE, INC. CMN- _", "AUTOZONE, INC."),
        ("CBRE", "CBRE GROUP, INC.CMN CLASS A", "CBRE GROUP, INC."),
    ]

    def test_cases(self):
        import analysis

        for code, raw, want in self.CASES:
            with self.subTest(code=code, raw=raw):
                self.assertEqual(analysis.clean_name(raw, code), want)

    def test_idempotent(self):
        import analysis

        for code, raw, _want in self.CASES:
            once = analysis.clean_name(raw, code)
            with self.subTest(code=code, raw=raw):
                self.assertEqual(analysis.clean_name(once, code), once)

    def test_never_outputs_holder(self):
        import analysis

        for code, raw, _want in self.CASES:
            out = analysis.clean_name(raw, code)
            with self.subTest(code=code, raw=raw):
                self.assertIsNone(analysis._HOLDER_LEFT.search(out), out)

    def test_no_build_backtest_dependency(self):
        import analysis

        src = Path(analysis.__file__).read_text(encoding="utf-8")
        self.assertNotIn("issuer_name", src)
        self.assertNotIn("spec_from_file_location", src)
        self.assertNotIn("HERE.parents[1]", src)


class AnalysisRefill(unittest.TestCase):
    """Dropped play names are replaced by the next-ranked non-avoid names, with a call cap."""

    def _run(self, book, drop):
        import analysis
        import jev

        asked = []
        saved = (jev.key_present, analysis.apply_jev_row, jev.store_decision)

        def fake_apply(row):
            asked.append(row["code"])
            choice = "drop" if drop(row["code"]) else "ship"
            return {"code": row["code"], "packed": {"model": "m", "answers": {"ship": {"choice": choice}}}}

        jev.key_present = lambda: True
        analysis.apply_jev_row = fake_apply
        jev.store_decision = lambda *a, **k: None
        try:
            with redirect_stdout(io.StringIO()):
                play, avoid, stats = analysis.gate_with_jev(None, book, skip_jev=False)
        finally:
            jev.key_present, analysis.apply_jev_row, jev.store_decision = saved
        return play, avoid, stats, asked

    @staticmethod
    def _book(n_play, n_avoid):
        rows = [{"code": f"P{i:02d}", "action": "watch", "score": 100.0 - i, "flags": []} for i in range(n_play)]
        rows += [{"code": f"A{i:02d}", "action": "avoid", "score": 50.0 - i, "flags": []} for i in range(n_avoid)]
        rows.sort(key=lambda r: -r["score"])
        return rows

    def test_no_drops_no_refill(self):
        play, avoid, stats, asked = self._run(self._book(30, 10), lambda c: False)
        self.assertEqual(stats["refill"], 0)
        self.assertEqual(len(asked), 18)
        self.assertEqual(len(play), 6)
        self.assertEqual(len(avoid), 5)

    def test_refill_until_target_kept(self):
        # The first 8 play names are dropped; refill should review P10.. until 10 play names pass.
        dropped = {f"P{i:02d}" for i in range(8)}
        play, avoid, stats, asked = self._run(self._book(30, 10), lambda c: c in dropped)
        self.assertEqual(stats["refill"], 8)
        self.assertEqual(stats["refill_codes"], [f"P{i:02d}" for i in range(10, 18)])
        self.assertTrue(all(c.startswith("P") for c in stats["refill_codes"]))  # never refills with avoid names
        self.assertEqual([r["code"] for r in play], ["P08", "P09", "P10", "P11", "P12", "P13"])
        self.assertEqual(len(avoid), 5)

    def test_refill_respects_call_cap(self):
        import analysis

        play, avoid, stats, asked = self._run(self._book(60, 10), lambda c: c.startswith("P"))
        self.assertEqual(stats["refill"], analysis.JEV_REFILL_MAX)
        self.assertEqual(len(asked), 18 + analysis.JEV_REFILL_MAX)
        self.assertEqual(play, [])

    def test_refill_stops_when_list_runs_out(self):
        play, avoid, stats, asked = self._run(self._book(13, 10), lambda c: c in {"P00", "P01", "P02", "P03", "P04"})
        self.assertEqual(stats["refill_codes"], ["P10", "P11", "P12"])
        self.assertEqual(len(play), 6)


def _notes_db() -> sqlite3.Connection:
    con = sqlite3.connect(":memory:")
    con.executescript(
        """
        CREATE TABLE people (
          filer_id TEXT PRIMARY KEY, name TEXT, display TEXT, chamber TEXT,
          state TEXT, party TEXT, district TEXT, bioguide TEXT, opensecrets TEXT
        );
        CREATE TABLE tickers (
          ticker TEXT PRIMARY KEY, name TEXT, industry TEXT, sic TEXT,
          market_cap REAL, shares REAL, px REAL, cap_asof TEXT, cap_currency TEXT, cap_src TEXT
        );
        CREATE TABLE politician_trades (
          trade_id TEXT PRIMARY KEY, filer TEXT, filer_id TEXT, chamber TEXT, ticker TEXT,
          asset TEXT, asset_type TEXT, side TEXT, amount_raw TEXT, amount_low REAL,
          amount_high REAL, amount_mid REAL, trade_date TEXT, filed_date TEXT,
          owner TEXT, source TEXT, added TEXT
        );
        CREATE TABLE insider_trades (
          trade_id TEXT PRIMARY KEY, filer TEXT, filer_id TEXT, title TEXT, ticker TEXT,
          company TEXT, side TEXT, trade_date TEXT, filed_date TEXT
        );
        """
    )
    con.execute("INSERT INTO people VALUES ('a', 'Ann Example', 'Ann Example', 'House', 'TX', 'D', '', '', '')")
    con.execute("INSERT INTO tickers(ticker, name, industry) VALUES ('NVDA', 'NVIDIA Corp', 'Semiconductors')")
    return con


def _trade(con, trade_id, day, filed, mid, side="purchase", ticker="NVDA", chamber="House"):
    con.execute(
        """
        INSERT INTO politician_trades(
          trade_id, filer, filer_id, chamber, ticker, asset, side, amount_raw, amount_mid, trade_date, filed_date
        ) VALUES (?, 'Ann Example', 'a', ?, ?, 'NVIDIA Corp Common Stock', ?, '$1,001-$15,000', ?, ?, ?)
        """,
        (trade_id, chamber, ticker, side, mid, day, filed),
    )


class FilingNotes(unittest.TestCase):
    def test_cache_key_is_stable(self):
        import filing_notes

        fields = filing_notes.canonical_state({
            "member": "Ann Example",
            "chamber": "House",
            "state": "TX",
            "party": "D",
            "committees": ["Senate Committee on Armed Services", "House Committee on Energy and Commerce"],
            "ticker": "NVDA",
            "company": "NVIDIA Corp",
            "industry": "Semiconductors",
            "asset": "NVIDIA Corp Common Stock",
            "transaction": "purchase",
            "amount_range": "$1,001-$15,000",
        })
        digest = filing_notes.fields_sha(fields)
        self.assertEqual(filing_notes.cache_key("t1", digest), filing_notes.cache_key("t1", digest))
        self.assertNotEqual(filing_notes.cache_key("t1", digest), filing_notes.cache_key("t2", digest))
        changed = dict(fields)
        changed["asset"] = "Some Other Issuer"
        self.assertNotEqual(digest, filing_notes.fields_sha(changed))
        self.assertNotIn("trade_date", fields)
        self.assertNotIn("lag", fields)
        self.assertNotIn("amount_mid", fields)

    def test_chip_templates_and_committee_threshold(self):
        import filing_notes

        chips = filing_notes.code_chips({
            "lag": 52,
            "ticker": "NVDA",
            "first": True,
            "largest": "purchase",
            "ratio": 3.02,
            "own": 4,
            "others": 2,
            "insiders": True,
        })
        self.assertEqual(chips, [
            "Filed 52d late",
            "First NVDA trade",
            "Largest buy on record",
            "3x usual size",
            "Cluster: 4 trades ±7d, 2 other members ±7d",
            "Insiders bought same week",
        ])
        self.assertIsNone(filing_notes.usual_label(1.9))
        self.assertEqual(filing_notes.code_chips({"lag": 45, "ticker": "NVDA", "first": False}) , [])
        self.assertEqual(
            filing_notes.code_chips({"largest": "sale", "ticker": "XOM", "first": True, "lag": 10}),
            ["First XOM trade", "Largest sale on record"],
        )

        low = {"committee_p3": 0.849, "is_broad_fund": 0.79, "is_derivative": 0.8, "home_state_industry": 0.8,
               "asset_matches_company": 0.21}
        self.assertEqual(
            filing_notes.jev_chips(low, ["Senate Committee on Armed Services"]),
            ["Option/derivative", "Home-state company"],
        )
        high = dict(low)
        high["committee_p3"] = filing_notes.COMMITTEE_P3_MIN
        high["is_broad_fund"] = filing_notes.NOUL_MIN
        high["asset_matches_company"] = filing_notes.ASSET_MISMATCH_MAX
        self.assertEqual(
            filing_notes.jev_chips(high, ["Senate Committee on Armed Services"]),
            [
                "Committee link: Armed Services",
                "Broad fund",
                "Option/derivative",
                "Home-state company",
                "Asset may not match ticker",
            ],
        )
        self.assertEqual(filing_notes.COMMITTEE_P3_MIN, 0.85)
        self.assertTrue(filing_notes.jev_chips({"committee_p3": 0.85}, [])[0].startswith("Committee link"))
        self.assertFalse(filing_notes.jev_chips({"committee_p3": 0.8499}, ["Armed Services"]))

    def test_run_writes_templates_and_makes_no_network_call(self):
        import filing_notes
        import jev_common

        def boom(*_a, **_k):
            raise AssertionError("jev_common.ask must not run on the daily path")

        saved = jev_common.ask
        jev_common.ask = boom
        con = _notes_db()
        _trade(con, "t1", "2020-01-01", "2020-01-10", 10000)
        _trade(con, "t2", "2021-01-01", "2021-01-10", 10000)
        _trade(con, "t3", "2022-01-01", "2022-01-10", 10000)
        _trade(con, "t4", "2024-01-01", "2024-02-22", 100000)
        con.execute(
            "INSERT INTO insider_trades(trade_id, ticker, side, trade_date) VALUES ('i1', 'NVDA', 'purchase', '2024-01-03')"
        )
        con.execute(
            "INSERT INTO politician_trades(trade_id, filer, filer_id, chamber, ticker, side, amount_mid, trade_date, filed_date) "
            "VALUES ('wh', 'Staff', 's', 'White House', 'NVDA', 'purchase', 1, '2024-01-01', '2024-01-02')"
        )
        try:
            with tempfile.TemporaryDirectory() as d:
                dest = Path(d) / "filing-notes.json"
                meta = filing_notes.run(con, out_path=dest, bios={})
                payload = json.loads(dest.read_text(encoding="utf-8"))
        finally:
            jev_common.ask = saved
            con.close()
        self.assertEqual(meta["filings"], 4)
        self.assertEqual(meta["committee_p3_min"], 0.85)
        self.assertNotIn("wh", payload["notes"])
        self.assertEqual(payload["notes"]["t1"], ["First NVDA trade"])
        self.assertIn("Filed 52d late", payload["notes"]["t4"])
        self.assertIn("Largest buy on record", payload["notes"]["t4"])
        self.assertIn("10x usual size", payload["notes"]["t4"])
        self.assertIn("Insiders bought same week", payload["notes"]["t4"])
        self.assertNotIn("First NVDA trade", payload["notes"]["t4"])

    def test_second_judge_run_makes_no_calls(self):
        import filing_notes
        import jev_common
        from types import SimpleNamespace

        calls = []

        def fake_ask(state, questions, **kwargs):
            calls.append(kwargs.get("tag"))
            committee = SimpleNamespace(score=0.2, confidence=0.4, probabilities={"0": 0.7, "1": 0.1, "2": 0.1, "3": 0.1})
            noul = SimpleNamespace(noul=0.05)
            answers = {
                "committee_overlap": committee,
                "asset_matches_company": noul,
                "is_broad_fund": noul,
                "is_derivative": noul,
                "home_state_industry": noul,
            }
            return SimpleNamespace(model="jev-1.13.0", answers=answers, usage=SimpleNamespace(input_tokens=12, output_tokens=1))

        saved_ask = jev_common.ask
        saved_env = os.environ.get("QC_FILING_JUDGMENTS")
        con = _notes_db()
        _trade(con, "t1", "2024-01-01", "2024-01-20", 8000)
        _trade(con, "t2", "2024-06-01", "2024-06-20", 8000)
        jev_common.ask = fake_ask
        try:
            with tempfile.TemporaryDirectory() as d:
                os.environ["QC_FILING_JUDGMENTS"] = str(Path(d) / "filing_judgments.sqlite")
                with redirect_stdout(io.StringIO()):
                    first = filing_notes.judge(
                        backfill=True, workers=2, max_usd=3, con=con, bios={}, client=object(),
                        question_set={"stub": True},
                    )
                    second = filing_notes.judge(backfill=True, workers=2, max_usd=3, con=con, bios={}, client=object())
        finally:
            jev_common.ask = saved_ask
            if saved_env is None:
                os.environ.pop("QC_FILING_JUDGMENTS", None)
            else:
                os.environ["QC_FILING_JUDGMENTS"] = saved_env
            con.close()
        self.assertEqual(first["calls"], 2)
        self.assertEqual(second["calls"], 0)
        self.assertEqual(sorted(calls), ["t1", "t2"])

    def test_run_boards_calls_filing_notes(self):
        import filing_notes

        saved = filing_notes.run
        hit = {}

        def fake(con, **_k):
            hit["con"] = con
            return {"filings": 0}

        filing_notes.run = fake
        con = sqlite3.connect(":memory:")
        try:
            with redirect_stdout(io.StringIO()):
                rebuild.run_boards(
                    con,
                    skip_jev=True,
                    skip_tells=True,
                    skip_analysis=True,
                    skip_paper=True,
                    skip_insider=True,
                    skip_overlap=True,
                    excel=False,
                )
        finally:
            filing_notes.run = saved
            con.close()
        self.assertIs(hit.get("con"), con)


if __name__ == "__main__":
    unittest.main()
