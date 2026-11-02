#!/usr/bin/env python3
"""Churchill Diamond Corporation is a wholly owned subsidiary of Churchill Resources Inc. since the 2021 RTO."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import claims_db as c  # noqa: E402
import ontario_claims_db as o  # noqa: E402


class ChurchillSubsidiary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = o.load_company_book()
        cls.aliases = o.load_aliases()
        cls.extract = o.harvest_extract_holders()
        cls.idx = o.name_indexes(cls.book)

    def link(self, holder):
        h = c.registry_holder_to_ontario(holder)
        return o.match_holder(h, self.book, self.aliases, self.extract, *self.idx).get("company_id")

    def test_alias_and_no_block(self):
        self.assertIn(("churchill diamond", "churchill-resources-inc"),
                      {(a["phrase"], a["company_id"]) for a in self.aliases})
        self.assertNotIn(("churchill diamond", "churchill-resources-inc"), o.load_link_blocks())

    def test_subsidiary_holders_link_to_parent(self):
        for holder in ("(100) CHURCHILL DIAMOND CORPORATION", "Churchill Diamond Corporation (100%)"):
            self.assertEqual(self.link(holder), "churchill-resources-inc", holder)

    def test_parent_and_other_churchill_holders_unchanged(self):
        self.assertEqual(self.link("Churchill Resources Incorporated"), "churchill-resources-inc")
        self.assertEqual(self.link("(100) Churchill Explorations Ltd."), "churchill-resources-inc")


if __name__ == "__main__":
    unittest.main()
