#!/usr/bin/env python3
"""Issuers with no hard-rock activity (beta commodity oil & gas) and zero focus titles get no extracts."""
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

    def test_committed_catalog_clean(self):
        cat = json.loads((ROOT / "claims" / "companies.json").read_text(encoding="utf-8"))
        for r in cat["companies"]:
            self.assertEqual(cm.no_hardrock_guard(dict(r), ROOT), [], r["id"])
            for key in ("extract", "ontario_extract", "bc_extract"):
                if r.get(key):
                    self.assertTrue((ROOT / r[key]).is_file(), r[key])


if __name__ == "__main__":
    unittest.main()
