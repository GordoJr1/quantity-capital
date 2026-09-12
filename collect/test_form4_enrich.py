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
            "trade_date": "2026-09-08",
            "source": "https://www.sec.gov/Archives/edgar/data/1548280/000154828026000014/wk.xml",
        }
        tx = f4.match_trade(trade, filing)
        self.assertIsNotNone(tx)
        ov = f4.trade_overlay(trade, filing, tx)
        self.assertTrue(ov["plan"])
        self.assertEqual(ov["why"], "10b5-1")
        self.assertEqual(ov["after"], 530086)
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
        self.assertEqual(ov["held"], 8159 + 5000)


class ClassifyText(unittest.TestCase):
    def test_plan_and_negation(self):
        self.assertEqual(f4.classify_text("effected pursuant to a Rule 10b5-1 selling plan"), "10b5-1")
        self.assertEqual(f4.classify_text("sell to cover tax withholding obligations"), "cover")
        self.assertIsNone(f4.classify_text("not pursuant to a Rule 10b5-1 trading plan"))


class FetchFallbacks(unittest.TestCase):
    def test_efts_runs_after_candidate_404s(self):
        xml = (FIXTURES / "nault-10b51.xml").read_text()

        def http404(url):
            return urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

        def fake_efts(accn, ua):
            self.assertEqual(accn, "0001062993-26-003632")
            return ["1720424"]

        seen = []

        def fake_bytes(url, ua, sleep_s):
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
            doc = f4.fetch_filing(agent, "test@example.com", 0)
        finally:
            f4.fetch_bytes = orig_fetch
            f4.efts_ciks = orig_efts
            f4.resolve_from_index = orig_index
        self.assertTrue(doc["plan"])
        self.assertTrue(any("1720424/000106299326003632" in u for u in seen))


if __name__ == "__main__":
    unittest.main()
