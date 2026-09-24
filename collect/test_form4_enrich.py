#!/usr/bin/env python3
"""Unit tests for Form 4 plan-footnote and Table I parsers."""
from __future__ import annotations

import json
import sys
import unittest
import urllib.error
from email.message import Message
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import form4_enrich as f4  # noqa: E402

FIXTURES = ROOT / "fixtures"


def load_fixture(name: str) -> dict:
    xml = (FIXTURES / name).read_text()
    return f4.parse_ownership_xml(xml, url=f"https://www.sec.gov/Archives/edgar/data/1/{name}")


class ParseAccession(unittest.TestCase):
    def test_url_and_trade_id(self):
        url = "https://www.sec.gov/Archives/edgar/data/1548280/000154828026000014/wk-form4.xml"
        self.assertEqual(f4.accession_from_url(url), "0001548280-26-000014")
        self.assertEqual(
            f4.accession_from_trade_id("form4-0001548280-26-000014-nault-casey-m-2026-09-08-S-10000"),
            "0001548280-26-000014",
        )

    def test_smashed_cik_path(self):
        smashed = "https://www.sec.gov/Archives/edgar/data/181274625000005/000181274625000005/primary_doc.xml"
        self.assertEqual(
            f4.canonical_form4_url(smashed),
            "https://www.sec.gov/Archives/edgar/data/1812746/000181274625000005/primary_doc.xml",
        )

    def test_urls_under_issuer_ciks(self):
        urls = f4.urls_under_ciks("0001062993-26-003632", ["1720424", "2122510"], "form4.xml")
        self.assertIn(
            "https://www.sec.gov/Archives/edgar/data/1720424/000106299326003632/form4.xml",
            urls,
        )
        self.assertIn(
            "https://www.sec.gov/Archives/edgar/data/2122510/000106299326003632/primary_doc.xml",
            urls,
        )

    def test_index_picks_wk_form4(self):
        index = {"directory": {"item": [
            {"name": "0001205268-26-000009-index.html"},
            {"name": "wk-form4_1783349662.xml"},
        ]}}
        urls = f4.xmls_from_index(index, "https://www.sec.gov/Archives/edgar/data/1205268/000120526826000009")
        self.assertEqual(urls, [
            "https://www.sec.gov/Archives/edgar/data/1205268/000120526826000009/wk-form4_1783349662.xml"
        ])


class ParsePlanAndHoldings(unittest.TestCase):
    def test_nault_scheduled_plan_sale(self):
        doc = load_fixture("nault-10b51.xml")
        self.assertTrue(doc["aff"])
        self.assertTrue(doc["plan"])
        self.assertEqual(len(doc["tx"]), 1)
        tx = doc["tx"][0]
        self.assertEqual(tx["code"], "S")
        self.assertEqual(tx["shares"], 10000)
        self.assertEqual(tx["after"], 530086)
        self.assertTrue(tx["plan"])
        self.assertEqual(tx["why"], "10b5-1")
        self.assertAlmostEqual(f4.vs_stake(10000, 530086, False), 10000 / 540086, places=5)

    def test_sousa_discretionary_sale(self):
        doc = load_fixture("sousa-discretionary.xml")
        self.assertFalse(doc["aff"])
        self.assertFalse(doc["plan"])
        tx = doc["tx"][0]
        self.assertEqual(tx["shares"], 53627)
        self.assertEqual(tx["after"], 125031)
        self.assertFalse(tx["plan"])
        self.assertEqual(tx["why"], "")

    def test_narayanadas_sell_to_cover_not_plan(self):
        doc = load_fixture("narayanadas-cover.xml")
        self.assertTrue(doc["aff"])
        tx = doc["tx"][0]
        self.assertFalse(tx["plan"])
        self.assertEqual(tx["why"], "cover")
        self.assertEqual(tx["after"], 8159)
        self.assertEqual(len(doc["hold"]), 1)
        self.assertEqual(doc["hold"][0]["after"], 5000)

    def test_courtis_discretionary_buys_and_end_holdings(self):
        doc = load_fixture("courtis-discretionary-buy.xml")
        self.assertFalse(doc["plan"])
        buys = [t for t in doc["tx"] if t["code"] == "P"]
        self.assertGreaterEqual(len(buys), 3)
        self.assertTrue(all(not t["plan"] for t in buys))
        last = buys[-1]
        self.assertIsNotNone(last["after"])
        self.assertGreater(last["after"], last["shares"])
        vs = f4.vs_stake(last["shares"], last["after"], True)
        self.assertIsNotNone(vs)
        self.assertLess(vs, 0.05)


class MatchAndOverlay(unittest.TestCase):
    def test_match_nault_tape_row(self):
        parsed = load_fixture("nault-10b51.xml")
        filing = f4.compact_filing(parsed)
        trade = {
            "id": "form4-0001548280-26-000014-nault-casey-m-2026-09-08-S-10000",
            "origin": "form4",
            "side": "sale",
            "code": "S",
            "shares": 10000.0,
            "shares_after": 530086.0,
            "held_pct": 1.85,
            "trade_date": "2026-09-08",
            "source": "https://www.sec.gov/Archives/edgar/data/1548280/000154828026000014/wk.xml",
        }
        tx = f4.match_trade(trade, filing)
        self.assertIsNotNone(tx)
        ov = f4.trade_overlay(trade, filing, tx)
        self.assertTrue(ov["plan"])
        self.assertEqual(ov["why"], "10b5-1")
        self.assertEqual(ov["after"], 530086)
        self.assertEqual(ov["shares"], 10000)
        self.assertEqual(ov["shares_held"], 530086)
        self.assertEqual(ov["pct_held"], 1.85)
        self.assertAlmostEqual(ov["vs"], 10000 / 540086, places=4)

    def test_cover_does_not_inherit_aff_box(self):
        parsed = load_fixture("narayanadas-cover.xml")
        filing = f4.compact_filing(parsed)
        trade = {
            "id": "form4-0002146790-26-000012-narayanadas-vivek-2026-09-09-S-365",
            "origin": "form4",
            "side": "sale_post",
            "code": "S",
            "shares": 365.0,
            "shares_after": 8159.0,
            "trade_date": "2026-09-09",
        }
        tx = f4.match_trade(trade, filing)
        ov = f4.trade_overlay(trade, filing, tx)
        self.assertFalse(ov["plan"])
        self.assertEqual(ov["why"], "cover")
        self.assertEqual(ov["after"], 8159)
        self.assertEqual(ov["shares"], 365)
        self.assertEqual(ov["shares_held"], 8159)
        self.assertEqual(ov["held"], 8159 + 5000)


class ClassifyText(unittest.TestCase):
    def test_plan_and_negation(self):
        self.assertEqual(f4.classify_text("effected pursuant to a Rule 10b5-1 selling plan"), "10b5-1")
        self.assertEqual(f4.classify_text("sell to cover tax withholding obligations"), "cover")
        self.assertIsNone(f4.classify_text("not pursuant to a Rule 10b5-1 trading plan"))

    def test_espp_is_not_a_trading_plan(self):
        self.assertIsNone(f4.classify_text(
            "Shares acquired under the Issuer's Employee Stock Purchase Plan in a transaction exempt under Rule 16b-3."
        ))
        self.assertIsNone(f4.classify_text("Purchased through the company ESPP."))
        self.assertEqual(f4.classify_text(
            "ESPP shares sold pursuant to a Rule 10b5-1 trading plan adopted May 1, 2026."
        ), "10b5-1")
        self.assertEqual(f4.classify_text("Shares bought under a stock purchase plan adopted by the reporting person"), "10b5-1")

    def test_cover_fund_tax(self):
        self.assertEqual(f4.classify_text("Shares sold to fund the reporting person's tax liability on vesting."), "cover")


def http_error(url: str, code: int, retry_after: str | None = None) -> urllib.error.HTTPError:
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    return urllib.error.HTTPError(url, code, "err", headers, None)


class RateLimit(unittest.TestCase):
    def setUp(self):
        self.sleeps: list[float] = []
        self.calls: list[str] = []
        self.orig = (f4.sec_request, f4.time.sleep, f4._consecutive_403)
        f4.time.sleep = self.sleeps.append
        f4._consecutive_403 = 0

    def tearDown(self):
        f4.sec_request, f4.time.sleep, f4._consecutive_403 = self.orig

    def script(self, *responses):
        queue = list(responses)

        def fake(url, ua, timeout=30.0):
            self.calls.append(url)
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

        f4.sec_request = fake

    def test_429_honors_retry_after_then_succeeds(self):
        self.script(http_error("u", 429, "7"), b"ok")
        self.assertEqual(f4.fetch_bytes("u", "ua"), b"ok")
        self.assertEqual(self.sleeps, [7.0])

    def test_backoff_starts_at_seconds_not_request_gap(self):
        self.script(http_error("u", 503), http_error("u", 503), b"ok")
        f4.fetch_bytes("u", "ua")
        self.assertEqual(self.sleeps, [f4.SEC_BACKOFF, f4.SEC_BACKOFF * 2])

    def test_404_is_not_retried(self):
        self.script(http_error("u", 404))
        with self.assertRaises(urllib.error.HTTPError):
            f4.fetch_bytes("u", "ua")
        self.assertEqual(len(self.calls), 1)

    def test_persistent_403_aborts(self):
        self.script(*[http_error("u", 403) for _ in range(f4.SEC_MAX_403)])
        with self.assertRaises(f4.SecBlocked):
            f4.fetch_bytes("u", "ua")
        self.assertEqual(len(self.calls), f4.SEC_MAX_403)

    def test_blocked_escapes_fetch_filing_fallbacks(self):
        self.script(*[http_error("u", 403) for _ in range(f4.SEC_MAX_403)])
        with self.assertRaises(f4.SecBlocked):
            f4.fetch_filing("https://www.sec.gov/Archives/edgar/data/1/000000000126000001/x.xml", "ua")

    def test_interval_floor_keeps_under_10_per_second(self):
        before = f4._request_interval
        try:
            self.assertGreaterEqual(f4.set_request_interval(0), 0.1)
        finally:
            f4.set_request_interval(before)


class MainExit(unittest.TestCase):
    def test_all_failed_exits_nonzero_but_writes_sidecar(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        tape = root / "tape.json"
        dest = root / "out.json"
        url = "https://www.sec.gov/Archives/edgar/data/1/000000000126000001/x.xml"
        tape.write_text(json.dumps({"trades": [{"origin": "form4", "source": url, "id": "t1"}]}))
        orig = f4.fetch_filing

        def boom(u, ua):
            raise RuntimeError("down")

        f4.fetch_filing = boom
        try:
            rc = f4.main(["--tape", str(tape), "--dest", str(dest)])
        finally:
            f4.fetch_filing = orig
        self.assertEqual(rc, 1)
        self.assertEqual(json.loads(dest.read_text())["stats"]["failed"], 1)

    def test_nothing_to_fetch_exits_zero(self):
        import tempfile

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        tape = root / "tape.json"
        tape.write_text(json.dumps({"trades": []}))
        self.assertEqual(f4.main(["--tape", str(tape), "--dest", str(root / "out.json")]), 0)


class FetchFallbacks(unittest.TestCase):
    def test_efts_runs_after_candidate_404s(self):
        xml = (FIXTURES / "nault-10b51.xml").read_text()

        def http404(url):
            return urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

        def fake_efts(accn, ua):
            self.assertEqual(accn, "0001062993-26-003632")
            return ["1720424"]

        seen = []

        def fake_bytes(url, ua):
            seen.append(url)
            if "1720424/000106299326003632/" in url and url.endswith(".xml"):
                return xml.encode("utf-8")
            raise http404(url)

        orig_fetch = f4.fetch_bytes
        orig_efts = f4.efts_ciks
        orig_index = f4.resolve_from_index
        f4.fetch_bytes = fake_bytes
        f4.efts_ciks = fake_efts
        f4.resolve_from_index = lambda *a, **k: []
        try:
            agent = "https://www.sec.gov/Archives/edgar/data/1062993/000106299326003632/form4.xml"
            doc = f4.fetch_filing(agent, "test@example.com")
        finally:
            f4.fetch_bytes = orig_fetch
            f4.efts_ciks = orig_efts
            f4.resolve_from_index = orig_index
        self.assertTrue(doc["plan"])
        self.assertTrue(any("1720424/000106299326003632" in u for u in seen))


if __name__ == "__main__":
    unittest.main()
