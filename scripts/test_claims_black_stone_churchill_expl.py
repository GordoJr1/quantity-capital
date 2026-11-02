#!/usr/bin/env python3
"""black-stone-minerals (oil & gas) has no extracts; Churchill Explorations Ltd. is not Churchill Resources Inc."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import claims_db as c  # noqa: E402
import claims_extract_retags as r  # noqa: E402
import ontario_claims_db as o  # noqa: E402

BSM_FILES = ("black-stone-minerals.geojson", "black-stone-minerals-ontario.geojson", "black-stone-minerals-bc.geojson")


class BlackStoneChurchillExpl(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = o.load_company_book()
        cls.aliases = o.load_aliases()
        cls.extract = o.harvest_extract_holders()
        cls.idx = o.name_indexes(cls.book)

    def link(self, holder):
        h = c.registry_holder_to_ontario(holder)
        return o.match_holder(h, self.book, self.aliases, self.extract, *self.idx).get("company_id")

    def test_bsm_has_no_extracts(self):
        rules = r.load_rules()
        for name in BSM_FILES:
            self.assertNotIn(name, rules)
            self.assertFalse((ROOT / "claims" / name).exists(), name)
        cat = json.loads((ROOT / "claims" / "companies.json").read_text(encoding="utf-8"))
        row = next(x for x in cat["companies"] if x["id"] == "black-stone-minerals")
        for key in ("extract", "ontario_extract", "bc_extract"):
            self.assertIsNone(row[key], key)

    def test_catalog_counts_zero(self):
        cat = json.loads((ROOT / "claims" / "companies.json").read_text(encoding="utf-8"))
        row = next(x for x in cat["companies"] if x["id"] == "black-stone-minerals")
        for f in ("claim_count", "ontario_count", "bc_count", "quebec_count"):
            self.assertEqual(row[f], 0, f)

    def test_mis_tagged_holders_no_longer_link_to_bsm(self):
        for holder in ("(100) Blackwidow Geological Services Inc.", "(100) Neal E Blackmore", "Neal Blackmore",
                       "(100) GRAHAM LAURENCE STONE", "(100) Infinity Stone Ventures Corp.",
                       "(50) Churchill Explorations Ltd., (50) Infinity Stone Ventures Corp."):
            self.assertNotEqual(self.link(holder), "black-stone-minerals", holder)

    def test_churchill_explorations_not_cri(self):
        issuer = next(x for x in json.loads((ROOT / "claims/links/public-issuers.json").read_text(encoding="utf-8"))["issuers"]
                      if x["id"] == "churchill-resources-inc")
        self.assertNotIn("churchill explorations", issuer["exact_norms"])
        self.assertIn(("churchill explorations", "churchill-resources-inc"), o.load_link_blocks())
        for holder in ("(100) Churchill Explorations Ltd.", "(50) Churchill Explorations Ltd., (50) Infinity Stone Ventures Corp."):
            self.assertNotEqual(self.link(holder), "churchill-resources-inc", holder)

    def test_cri_and_subsidiary_still_link(self):
        self.assertEqual(self.link("Churchill Resources Incorporated"), "churchill-resources-inc")
        self.assertEqual(self.link("(100) CHURCHILL DIAMOND CORPORATION"), "churchill-resources-inc")


if __name__ == "__main__":
    unittest.main()
