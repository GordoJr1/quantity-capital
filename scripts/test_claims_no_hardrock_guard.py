#!/usr/bin/env python3
"""Issuers with no hard-rock activity (beta commodity oil & gas, any spelling) and zero focus titles get no extracts."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import claims_catalog_maps as cm  # noqa: E402


def row(cid, count):
    return {"id": cid, "claim_count": count, "extract": f"claims/{cid}.geojson",
            "ontario_extract": f"claims/{cid}-ontario.geojson", "bc_extract": None,
            "quebec_count": count, "quebec_bbox": [0, 0, 1, 1] if count else None,
            "ontario_count": 0, "ontario_bbox": None, "bc_count": 0, "bc_bbox": None}


class NoHardrockGuard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "beta").mkdir()
        for cid, com in (("oilco", "oil & gas"), ("oilbig", "oil & gas"), ("goldco", "gold")):
            (self.root / "beta" / f"{cid}.json").write_text(json.dumps({"issuer": {"commodity": com}}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_oil_gas_zero_titles_dropped(self):
        r = row("oilco", 0)
        changes = cm.no_hardrock_guard(r, self.root)
        self.assertEqual(len(changes), 1)
        for key in ("extract", "ontario_extract", "bc_extract"):
            self.assertIsNone(r[key])

    def test_oil_gas_with_focus_titles_kept(self):
        r = row("oilbig", 29)
        self.assertEqual(cm.no_hardrock_guard(r, self.root), [])
        self.assertEqual(r["extract"], "claims/oilbig.geojson")

    def test_hardrock_zero_titles_kept(self):
        r = row("goldco", 0)
        self.assertEqual(cm.no_hardrock_guard(r, self.root), [])
        self.assertIsNotNone(r["extract"])

    def test_unknown_profile_kept(self):
        r = row("nobeta", 0)
        self.assertEqual(cm.no_hardrock_guard(r, self.root), [])

    def test_idempotent(self):
        r = row("oilco", 0)
        cm.no_hardrock_guard(r, self.root)
        self.assertEqual(cm.no_hardrock_guard(r, self.root), [])

    def test_commodity_variants(self):
        for text in ("oil & gas", "Oil & Gas", "oil and gas", "Oil And Gas", "oil&gas", "oil & gas, midstream",
                     "oil and gas; royalty", " oil  &  gas ", "oil/gas", "Oil / Gas", "natural gas", "oil & gas, helium"):
            self.assertTrue(cm.is_non_hardrock(text), text)
        for text in (None, "", "gold", "met coal", "oil & gas, copper", "copper, oil & gas", "oil", "gas",
                     "oil & gas, platinum", "oil & gas, manganese", "oil & gas, coal", "oil & gas, other"):
            self.assertFalse(cm.is_non_hardrock(text), text)

    def test_strict_every_part_must_be_non_hardrock(self):
        self.assertFalse(cm.is_non_hardrock("oil & gas, platinum"))
        self.assertFalse(cm.is_non_hardrock("oil & gas, manganese"))
        self.assertFalse(cm.is_non_hardrock("oil & gas, coal"))
        self.assertFalse(cm.is_non_hardrock("oil and gas; royalties"))
        self.assertTrue(cm.is_non_hardrock("oil/gas"))

    def test_variant_profile_dropped(self):
        (self.root / "beta" / "oilvar.json").write_text(json.dumps({"issuer": {"commodity": "Oil and Gas, midstream"}}), encoding="utf-8")
        r = row("oilvar", 0)
        self.assertEqual(len(cm.no_hardrock_guard(r, self.root)), 1)
        self.assertIsNone(r["extract"])

    def test_oil_gas_issuers_have_no_extracts(self):
        """imperial-oil, enterprise-products-partners and cardinal-energy titles were all shared-word mis-tags."""
        cat = {r["id"]: r for r in json.loads((ROOT / "claims" / "companies.json").read_text(encoding="utf-8"))["companies"]}
        for cid in ("black-stone-minerals", "imperial-oil", "enterprise-products-partners", "cardinal-energy"):
            for key in ("extract", "ontario_extract", "bc_extract"):
                self.assertIsNone(cat[cid].get(key), (cid, key))
            self.assertEqual(cat[cid]["claim_count"], 0, cid)
        eb = json.loads((ROOT / "claims" / "extract-bytes.json").read_text(encoding="utf-8"))["company_bytes"]
        for cid in ("black-stone-minerals", "imperial-oil", "enterprise-products-partners", "cardinal-energy"):
            self.assertNotIn(cid, eb)

    def test_committed_catalog_clean(self):
        cat = json.loads((ROOT / "claims" / "companies.json").read_text(encoding="utf-8"))
        for r in cat["companies"]:
            self.assertEqual(cm.no_hardrock_guard(dict(r), ROOT), [], r["id"])
            for key in ("extract", "ontario_extract", "bc_extract"):
                if r.get(key):
                    self.assertTrue((ROOT / r[key]).is_file(), r[key])


if __name__ == "__main__":
    unittest.main()
