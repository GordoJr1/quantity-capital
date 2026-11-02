#!/usr/bin/env python3
"""'entreprises minieres globex' links the Quebec French name to globex-mining-enterprises, and nothing similar."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import claims_db as c  # noqa: E402
import ontario_claims_db as o  # noqa: E402


class GlobexAlias(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.book = o.load_company_book()
        cls.aliases = o.load_aliases()
        cls.extract = o.harvest_extract_holders()
        cls.idx = o.name_indexes(cls.book)

    def link(self, holder):
        h = c.registry_holder_to_ontario(holder)
        return o.match_holder(h, self.book, self.aliases, self.extract, *self.idx).get("company_id")

    def test_alias_row(self):
        rows = [a for a in self.aliases if a["phrase"] == "entreprises minieres globex"]
        self.assertEqual([a["company_id"] for a in rows], ["globex-mining-enterprises"])

    def test_quebec_french_name_links(self):
        for holder in ("(100) Entreprises Minières Globex Inc", "(80) Entreprises Minières Globex Inc"):
            self.assertEqual(self.link(holder), "globex-mining-enterprises", holder)

    def test_english_name_still_links(self):
        self.assertEqual(self.link("(100) GLOBEX MINING ENTERPRISES INC."), "globex-mining-enterprises")

    def test_similar_names_do_not_link_to_globex(self):
        for holder in ("(100) Cibles Minières Critiques inc.", "(100) Ressources Minières Radisson Inc.",
                       "(32) Ressources Minières Augyva inc. (Augyva Mining Resources)", "(100) Entreprises Minières Globe Inc",
                       "(100) GLENPARK ENTERPRISES LTD."):
            self.assertNotEqual(self.link(holder), "globex-mining-enterprises", holder)


if __name__ == "__main__":
    unittest.main()
