#!/usr/bin/env python3
"""Matcher tests for claims → insider universe merge."""
from __future__ import annotations

import unittest

from merge_claims_insider_universe import (
    classify_ticker,
    index_insider,
    looks_private,
    match_claims_company,
    norm_name,
    slugify,
    split_tickers,
)


class Norm(unittest.TestCase):
    def test_slug_and_norm(self):
        self.assertEqual(slugify("Harmony Gold Mining Company Limited"), "harmony-gold-mining-company-limited")
        self.assertEqual(norm_name("Harmony Gold Mining Company Limited"), "harmony gold mining")
        self.assertEqual(norm_name("Probe Gold Inc."), "probe gold")

    def test_private(self):
        self.assertTrue(looks_private("1234567 Ontario Inc."))
        self.assertTrue(looks_private("Her Majesty the Queen in Right of Ontario"))
        self.assertFalse(looks_private("Probe Gold"))
        self.assertFalse(looks_private("Newmont Corporation"))

    def test_ticker_split(self):
        self.assertEqual(classify_ticker("PRB.TO"), "cad")
        self.assertEqual(classify_ticker("HMY"), "us")
        self.assertEqual(classify_ticker("NST.AX"), "other")
        split = split_tickers(["TAU.V", "THSGF", "TAU.V"])
        self.assertEqual(split["cad"], ["TAU.V"])
        self.assertEqual(split["us"], ["THSGF"])


class Match(unittest.TestCase):
    def setUp(self):
        self.book = [
            {"name": "Newmont", "all": ["NEM"], "us": ["NEM"], "cad": [], "other": []},
            {"name": "Harmony Gold", "all": ["HMY"], "us": ["HMY"], "cad": [], "other": []},
            {"name": "Thesis Gold & Silver", "all": ["TAU.V", "THSGF"], "us": ["THSGF"], "cad": ["TAU.V"], "other": []},
        ]
        self.by_slug, self.by_norm, self.by_ticker = index_insider(self.book)
        self.beta = {
            "newmont": {"name": "Newmont", "tickers": ["NEM"]},
            "harmony": {"name": "Harmony Gold Mining Company Limited", "tickers": ["HMY"]},
            "thesis-gold": {"name": "Thesis Gold", "tickers": ["TAU.V", "THSGF"]},
            "probe-gold": {"name": "Probe Gold", "tickers": ["PRB.TO"], "type": "Explorer", "commodity": "gold", "country": "Canada"},
            "mystery-private": {"name": "1234568 Quebec Inc.", "tickers": []},
        }

    def _match(self, rec):
        return match_claims_company(
            rec,
            insider_by_slug=self.by_slug,
            insider_by_norm=self.by_norm,
            insider_by_ticker=self.by_ticker,
            beta=self.beta,
            extract_aliases=[],
        )

    def test_existing_filings_name(self):
        row = self._match({"id": "newmont", "holder": "Newmont Corporation", "names": ["Newmont"]})
        self.assertEqual(row["status"], "matched")
        self.assertEqual(row["insider_name"], "Newmont")
        self.assertIn("NEM", row["tickers"])
        self.assertFalse(row.get("new"))

    def test_alias_via_ticker(self):
        row = self._match({"id": "harmony", "holder": "Harmony Gold Mining Company Limited", "names": ["harmony"]})
        self.assertEqual(row["status"], "matched")
        self.assertEqual(row["insider_name"], "Harmony Gold")
        self.assertFalse(row.get("new"))

    def test_renamed_issuer_same_tickers(self):
        row = self._match({"id": "thesis-gold", "holder": "Thesis Gold", "names": ["Thesis Gold"]})
        self.assertEqual(row["status"], "matched")
        self.assertEqual(row["insider_name"], "Thesis Gold & Silver")
        self.assertFalse(row.get("new"))

    def test_new_public(self):
        row = self._match({"id": "probe-gold", "holder": "Probe Gold", "names": ["Probe Gold"]})
        self.assertEqual(row["status"], "matched")
        self.assertTrue(row.get("new"))
        self.assertEqual(row["tickers"], ["PRB.TO"])

    def test_private_excluded(self):
        row = self._match({"id": "mystery-private", "holder": "1234568 Quebec Inc.", "names": []})
        self.assertEqual(row["status"], "private-excluded")

    def test_ticker_wins_over_extract_aliases(self):
        row = match_claims_company(
            {"id": "metal-energy", "holder": "Metal Energy", "names": ["Metal Energy"]},
            insider_by_slug=self.by_slug,
            insider_by_norm=self.by_norm,
            insider_by_ticker=self.by_ticker,
            beta={"metal-energy": {"tickers": ["HMY"]}},
            extract_aliases=["Osisko Metals", "Lomiko Metals"],
        )
        self.assertEqual(row["status"], "matched")
        self.assertEqual(row["insider_name"], "Harmony Gold")

    def test_ambiguous(self):
        book = self.book + [
            {"name": "Silver Storm A", "all": ["SVRS.V"], "us": [], "cad": ["SVRS.V"], "other": []},
            {"name": "Silver Storm B", "all": ["ZZZ.V"], "us": [], "cad": ["ZZZ.V"], "other": []},
        ]
        by_slug, by_norm, by_ticker = index_insider(book)
        by_slug["silver-storm"] = [book[-2], book[-1]]
        row = match_claims_company(
            {"id": "silver-storm", "holder": "Silver Storm", "names": ["Silver Storm"]},
            insider_by_slug=by_slug,
            insider_by_norm=by_norm,
            insider_by_ticker=by_ticker,
            beta={"silver-storm": {"tickers": []}},
            extract_aliases=[],
        )
        self.assertEqual(row["status"], "ambiguous")


if __name__ == "__main__":
    unittest.main()
