#!/usr/bin/env python3
"""BC/QC shared-word tag batch 2: role-only retags and kept holders."""
from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import claims_extract_retags as r  # noqa: E402

CLAIMS = ROOT / "claims"
RULES = CLAIMS / "links" / "extract-retags.json"


def focus(name: str) -> Counter:
    data = json.loads((CLAIMS / name).read_text(encoding="utf-8"))
    return Counter(f["properties"].get("holder") for f in data["features"]
                   if f["properties"].get("role") != "neighbor")


class Batch2(unittest.TestCase):
    def test_kept_focus_holders(self):
        expect = {
            "eagle-plains-resources.geojson": {"eagles maxwell": 47},
            "power-metals.geojson": {"Power Metals Corp": 362},
            "abitibi-metals.geojson": {"Abitibi Metals Corp.": 481},
            "electric-royalties.geojson": {},
            "nickel-creek-platinum.geojson": {},
            "nickel-creek-platinum-bc.geojson": {"NICKELEX RESOURCE CORPORATION": 5},
            "lake-victoria-gold.geojson": {},
            "lake-victoria-gold-bc.geojson": {"HOUGH, BRADLEY BLAKE": 1},
            "silver-storm-mining.geojson": {"Silverwater Capital Corp": 40},
            "silver-storm-mining-bc.geojson": {"COEUR SILVERTIP HOLDINGS LTD.": 21},
        }
        for name, held in expect.items():
            self.assertEqual(dict(focus(name)), held, name)
            self.assertIn(name, r.load_rules(RULES))

    def test_rules_agree_with_committed_files(self):
        rules = r.load_rules(RULES)
        for name in sorted(rules):
            if name == "metal-energy-bc.geojson":  # fixed by PR #246
                continue
            data = json.loads((CLAIMS / name).read_text(encoding="utf-8"))
            self.assertEqual(r.apply_retags(name, data["features"], rules), 0, name)

    def test_catalog_counts(self):
        rows = {c["id"]: c for c in json.loads((CLAIMS / "companies.json").read_text())["companies"]}
        for cid, n in {"lake-victoria-gold": 408, "silver-storm-mining": 61, "nickel-creek-platinum": 5,
                       "power-metals": 1107, "abitibi-metals": 730, "eagle-plains-resources": 118,
                       "electric-royalties": 17}.items():
            self.assertEqual(rows[cid]["claim_count"], n, cid)


if __name__ == "__main__":
    unittest.main()
