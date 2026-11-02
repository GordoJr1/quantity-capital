#!/usr/bin/env python3
"""Ontario shared-word tag batch 1: role-only retags, kept holders, and link guards."""
from __future__ import annotations

import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import claims_extract_retags as r  # noqa: E402
import ontario_claims_db as o  # noqa: E402

CLAIMS = ROOT / "claims"
RULES = CLAIMS / "links" / "extract-retags.json"


def focus(cid: str) -> Counter:
    data = json.loads((CLAIMS / f"{cid}-ontario.geojson").read_text(encoding="utf-8"))
    return Counter(f["properties"].get("holder") for f in data["features"]
                   if f["properties"].get("role") != "neighbor")


class Batch1(unittest.TestCase):
    def test_no_wrong_company_left_as_focus(self):
        for cid in ("eagle-plains-resources", "nickel-creek-platinum", "silver-storm-mining",
                    "lucara-diamond", "critical-elements-lithium"):
            self.assertEqual(sum(focus(cid).values()), 0, cid)

    def test_lake_victoria_keeps_only_partial_word_holders(self):
        self.assertEqual(set(focus("lake-victoria-gold")), {
            "(100) GREAT LAKES KAOLIN INC.", "(66) ROY  ANNETT, (34) GREAT LAKES NICKEL LIMITED",
            "(67) GREAT LAKES NICKEL LIMITED, (33) ROBIN  LOWE", "(100) GERALD ANTOINE BLAKELY",
            "(50) DARREN M DAVIS, (50) BLAKE  DAVIS"})

    def test_canada_nickel_keeps_own_titles_and_doubtful_numbered_companies(self):
        held = focus("canada-nickel")
        own = sum(n for h, n in held.items() if "canada nickel" in h.lower())
        self.assertEqual(own, 3603)
        others = [h for h in held if "canada nickel" not in h.lower()]
        self.assertEqual(len(others), 17)
        self.assertTrue(all(any(ch.isdigit() for ch in h.split(")", 1)[1]) for h in others), others)

    def test_subsidiary_and_jv_files_untouched(self):
        for cid, n in {"kenorland-minerals": 60268, "equinox-gold": 2714,
                       "generation-mining": 1623, "impala-platinum-holdings": 581}.items():
            self.assertEqual(sum(focus(cid).values()), n, cid)
            self.assertNotIn(f"{cid}-ontario.geojson", r.load_rules(RULES))

    def test_rules_agree_with_committed_files(self):
        rules = r.load_rules(RULES)
        for name in sorted(rules):
            if name == "metal-energy-bc.geojson":  # fixed by PR #246
                continue
            data = json.loads((CLAIMS / name).read_text(encoding="utf-8"))
            self.assertEqual(r.apply_retags(name, data["features"], rules), 0, name)


class Links(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = o.load_company_book()
        cls.idx = o.name_indexes(cls.book)
        cls.aliases = o.load_aliases()
        cls.extract = o.harvest_extract_holders()

    def link(self, holder):
        return o.match_holder(holder, self.book, self.aliases, self.extract, *self.idx)["company_id"]

    def test_wrong_links_removed(self):
        for holder, bad in {
            "(100) AGNICO EAGLE MINES LIMITED": "eagle-plains-resources",
            "(100) CHURCHILL DIAMOND CORPORATION": "lucara-diamond",
            "(100) EAST TIMMINS NICKEL LTD.": "nickel-creek-platinum",
            "(100) LAKE SHORE GOLD CORP.": "lake-victoria-gold",
            "(100) LANDORE RESOURCES CANADA INC.": "canada-nickel",
            "(100) Fortune Nickel and Gold Inc.": "fortune-minerals",
            "(100) Patriot Lithium (Canada) Inc.": "patriot-gold-corp",
            "(100) Great Red Lake Gold Corp.": "great-pacific-gold",
            "(100) First Class Metals Canada Inc.": "first-quantum-minerals",
        }.items():
            self.assertNotEqual(self.link(holder), bad, holder)

    def test_right_links_kept_or_gained(self):
        for holder, cid in {
            "(100) CANADA NICKEL COMPANY INC.": "canada-nickel",
            "(100) Great Red Lake Gold Corp.": "great-red-lake-gold-corp",
            "(100) STORM EXPLORATION INC.": "storm-exploration-inc",
            "(100) NorthX Nickel Corp.": "northx-nickel-corp",
            "(100) IMPALA CANADA LTD.": "impala-platinum-holdings",
        }.items():
            self.assertEqual(self.link(holder), cid, holder)


if __name__ == "__main__":
    unittest.main()
