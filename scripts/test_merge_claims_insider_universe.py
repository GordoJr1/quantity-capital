#!/usr/bin/env python3
"""Matcher tests for claims → insider universe merge."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from merge_claims_insider_universe import (
    classify_ticker,
    index_insider,
    looks_private,
    match_claims_company,
    norm_name,
    slugify,
    split_tickers,
    sync_catalog_pins,
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


class CatalogPins(unittest.TestCase):
    PIN = {"id": "vale", "holder": "Vale Canada Limited", "ontario_count": 189}

    def _root(self, catalog: dict, pins: dict) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "claims").mkdir()
        (root / "claims" / "companies.json").write_text(json.dumps(catalog), encoding="utf-8")
        (root / "claims" / "pinned-companies.json").write_text(json.dumps(pins), encoding="utf-8")
        return root

    def _read(self, root: Path, name: str) -> dict:
        return json.loads((root / "claims" / name).read_text(encoding="utf-8"))

    def test_stale_catalog_restores_row_and_meta(self):
        root = self._root(
            {"disclaimer": "old", "companies": [{"id": "newmont"}], "on_bc_built_at": "2026-09-20T00:00:00Z"},
            {"on_bc_built_at": "2026-09-23T00:00:00Z", "meta": {"disclaimer": "new"}, "companies": [self.PIN]},
        )
        changes, problems = sync_catalog_pins(root, write=False)
        self.assertEqual(changes, [])
        self.assertTrue(any("vale" in p for p in problems))
        self.assertEqual(self._read(root, "companies.json")["disclaimer"], "old")

        changes, problems = sync_catalog_pins(root, write=True)
        self.assertEqual(problems, [])
        cat = self._read(root, "companies.json")
        self.assertEqual([r["id"] for r in cat["companies"]], ["newmont", "vale"])
        self.assertEqual(cat["disclaimer"], "new")
        self.assertEqual(cat["on_bc_built_at"], "2026-09-23T00:00:00Z")
        self.assertEqual(sync_catalog_pins(root, write=True), ([], []))

    def test_fresher_catalog_refreshes_pins(self):
        row = dict(self.PIN, ontario_count=200)
        root = self._root(
            {"disclaimer": "newer", "companies": [row], "on_bc_built_at": "2026-10-01T00:00:00Z"},
            {"on_bc_built_at": "2026-09-23T00:00:00Z", "meta": {"disclaimer": "new"}, "companies": [self.PIN]},
        )
        changes, _ = sync_catalog_pins(root, write=True)
        pins = self._read(root, "pinned-companies.json")
        self.assertEqual(pins["companies"][0]["ontario_count"], 200)
        self.assertEqual(pins["meta"]["disclaimer"], "newer")
        self.assertEqual(pins["on_bc_built_at"], "2026-10-01T00:00:00Z")
        self.assertEqual(self._read(root, "companies.json")["companies"], [row])
        self.assertTrue(changes)

    def test_fresher_catalog_without_row_unpins_instead_of_restoring(self):
        root = self._root(
            {"companies": [{"id": "newmont"}], "on_bc_built_at": "2026-10-01T00:00:00Z"},
            {"on_bc_built_at": "2026-09-23T00:00:00Z", "meta": {}, "companies": [self.PIN]},
        )
        self.assertEqual(sync_catalog_pins(root, write=False), ([], []))
        sync_catalog_pins(root, write=True)
        self.assertEqual([r["id"] for r in self._read(root, "companies.json")["companies"]], ["newmont"])
        self.assertEqual(self._read(root, "pinned-companies.json")["companies"], [])


if __name__ == "__main__":
    unittest.main()
