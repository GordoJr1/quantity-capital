#!/usr/bin/env python3
"""Unit tests for the Ontario claims prototype. No network."""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ontario_claims_db as db  # noqa: E402


def book() -> dict:
    rows = {
        "kinross": {
            "company_id": "kinross",
            "name": "Kinross Gold Corporation",
            "names": ["Kinross Gold Corporation", "kinross"],
            "ticker": "KGC",
            "tickers": ["KGC", "K"],
            "assets": ["paracatu", "tasiast"],
            "asset_rows": [],
            "norms": [],
            "tokens": set(),
        },
        "vale": {
            "company_id": "vale",
            "name": "Vale Canada Limited",
            "names": ["Vale Canada Limited", "Vale"],
            "ticker": "VALE",
            "tickers": ["VALE"],
            "assets": ["creighton-sudbury-operations"],
            "asset_rows": [],
            "norms": [],
            "tokens": set(),
        },
        "kenorland-minerals": {
            "company_id": "kenorland-minerals",
            "name": "Kenorland Minerals",
            "names": ["Kenorland Minerals"],
            "ticker": "KLD.V",
            "tickers": ["KLD.V"],
            "assets": ["south-ubchi"],
            "asset_rows": [],
            "norms": [],
            "tokens": set(),
        },
    }
    for slot in rows.values():
        slot["norms"] = []
        seen = set()
        for name in slot["names"]:
            norm = db.normalize_name(name)
            if norm and norm not in seen:
                seen.add(norm)
                slot["norms"].append(norm)
        slot["tokens"] = set()
        for norm in slot["norms"]:
            slot["tokens"] |= db.distinctive_tokens(norm)
    return rows


class NormalizeTests(unittest.TestCase):
    def test_great_bear_phrase(self):
        self.assertEqual(db.normalize_name("GREAT BEAR RESOURCES LTD."), "great bear resources")
        self.assertEqual(db.normalize_name("(100) GREAT BEAR RESOURCES LTD."), "100 great bear resources")

    def test_parties(self):
        parties = db.parse_parties(
            "(50) GLENCORE CANADA CORPORATION, (50) VALE CANADA LIMITED VALE CANADA LIMITEE"
        )
        self.assertEqual(len(parties), 2)
        self.assertEqual(parties[1][0].split()[0], "VALE")
        self.assertEqual(parties[1][1], 50.0)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.book = book()
        self.exact, self.phrases, self.tokens = db.name_indexes(self.book)
        self.aliases = [
            {"phrase": "great bear resources", "company_id": "kinross", "note": ""},
            {"phrase": "kenorland", "company_id": "kenorland-minerals", "note": ""},
        ]

    def match(self, holder, extract=None, judge=None):
        return db.match_holder(
            holder, self.book, self.aliases, extract or {},
            self.exact, self.phrases, self.tokens, judge=judge,
        )

    def test_kinross_alias(self):
        row = self.match("(100) GREAT BEAR RESOURCES LTD.")
        self.assertEqual(row["company_id"], "kinross")
        self.assertEqual(row["method"], "alias")
        self.assertEqual(row["companies"][0]["ticker"], "KGC")
        self.assertIn("paracatu", row["companies"][0]["assets"])

    def test_vale_catalog_containment(self):
        row = self.match("(100) VALE CANADA LIMITED VALE CANADA LIMITEE")
        self.assertEqual(row["company_id"], "vale")
        self.assertEqual(row["method"], "catalog_name")

    def test_kenorland_token(self):
        row = self.match("(100) KENORLAND EXPLORATION LTD")
        self.assertEqual(row["company_id"], "kenorland-minerals")
        self.assertEqual(row["method"], "alias")

    def test_private_holder_unlinked(self):
        row = self.match("(100) Juno Corp.")
        self.assertIsNone(row["company_id"])

    def test_whole_name_is_the_first_word(self):
        self.book["glencore"] = {
            "company_id": "glencore", "name": "Glencore", "norms": ["glencore"],
            "tokens": {"glencore"}, "ticker": "GLEN.L", "tickers": ["GLEN.L"], "assets": ["sudbury"],
        }
        exact, phrases, tokens = db.name_indexes(self.book)
        row = db.match_holder(
            "(100) GLENCORE CANADA CORPORATION", self.book, self.aliases, {}, exact, phrases, tokens,
        )
        self.assertEqual(row["company_id"], "glencore")
        self.assertEqual(row["method"], "catalog_name")

    def test_extract_holder_sets_vale(self):
        raw = "(50) GLENCORE CANADA CORPORATION, (50) VALE CANADA LIMITED VALE CANADA LIMITEE"
        row = self.match(raw, extract={raw: "vale"})
        ids = {c["company_id"] for c in row["companies"]}
        self.assertIn("vale", ids)
        self.assertEqual(row["company_id"], "vale")

    def test_ambiguous_calls_judge_once_per_candidate_path(self):
        calls = []

        def judge(party, norm, candidates):
            calls.append([c["company_id"] for c in candidates])
            return [{
                "company_id": candidates[0]["company_id"],
                "jev_score": 2,
                "jev_confidence": 0.8,
                "jev_outcome": "same_entity",
            }]

        self.book["kinross"]["norms"] = ["sharedtoken one"]
        self.book["vale"]["norms"] = ["sharedtoken two"]
        self.tokens["sharedtoken"] = {"kinross", "vale"}
        row = self.match("(100) SHAREDTOKEN HOLDINGS LTD", judge=judge)
        self.assertTrue(calls)
        self.assertEqual(calls[0][0], "kinross")
        self.assertEqual(row["company_id"], "kinross")
        self.assertEqual(row["method"], "jev")

    def test_generic_and_middle_names_stay_unlinked(self):
        self.book["gold"] = {
            "company_id": "gold", "name": "NV Gold", "norms": ["nv gold"],
            "tokens": set(), "ticker": "NVX.V", "tickers": ["NVX.V"], "assets": [],
        }
        self.book["lake-resources"] = {
            "company_id": "lake-resources", "name": "Lake Resources", "norms": ["lake resources"],
            "tokens": set(), "ticker": None, "tickers": [], "assets": [],
        }
        self.book["tiger-calcium"] = {
            "company_id": "tiger-calcium", "name": "Tiger Calcium",
            "norms": ["tiger calcium services"], "tokens": {"calcium", "services"},
            "ticker": None, "tickers": [], "assets": [],
        }
        exact, phrases, tokens = db.name_indexes(self.book)
        tokens["services"] = {"tiger-calcium"}

        def match(holder):
            return db.match_holder(holder, self.book, self.aliases, {}, exact, phrases, tokens)

        self.assertIsNone(match("(100) LP Gold Corp.")["company_id"])
        self.assertIsNone(match("(100) COPPER LAKE RESOURCES LTD")["company_id"])
        self.assertIsNone(match("(100) Blackwidow Geological Services Inc.")["company_id"])
        self.assertIsNone(match("(100) Jamieson Scott Walker")["company_id"])

    def test_exact_norm_links_a_short_public_name(self):
        self.book["fnx-inc"] = {
            "company_id": "fnx-inc", "name": "FNX Inc.", "norms": ["fnx"],
            "exact_norms": ["fnx"], "tokens": set(), "ticker": "FNX.CN",
            "tickers": ["FNX.CN"], "assets": [], "exchanges": ["CSE"],
        }
        exact, phrases, tokens = db.name_indexes(self.book)
        row = db.match_holder(
            "(100) FNX INC.", self.book, self.aliases, {}, exact, phrases, tokens,
        )
        self.assertEqual(row["company_id"], "fnx-inc")
        self.assertEqual(row["companies"][0]["ticker"], "FNX.CN")
        mining = db.match_holder(
            "(100) FNX MINING COMPANY INC.", self.book, self.aliases, {}, exact, phrases, tokens,
        )
        self.assertIsNone(mining["company_id"])

    def test_named_exact_wins_over_another_issuers_pinned_norm(self):
        self.book["listed"] = {
            "company_id": "listed", "name": "Listed Mines", "norms": ["listed mines"],
            "exact_norms": [], "tokens": set(), "ticker": "LST.V", "tickers": ["LST.V"], "assets": [],
        }
        self.book["other"] = {
            "company_id": "other", "name": "Other", "norms": [],
            "exact_norms": ["listed mines"], "tokens": set(), "ticker": "OTH.V",
            "tickers": ["OTH.V"], "assets": [],
        }
        exact, phrases, tokens = db.name_indexes(self.book)
        row = db.match_holder("Listed Mines Inc.", self.book, [], {}, exact, phrases, tokens)
        self.assertEqual(row["company_id"], "listed")

    def test_minority_interest_does_not_claim_the_holder(self):
        row = self.match("(99) LAST RESORT RESOURCES LTD., (1) KENORLAND EXPLORATION LTD")
        self.assertIsNone(row["company_id"])


class GeometryTests(unittest.TestCase):
    def test_square_area(self):
        ring = [[0, 0], [0.01, 0], [0.01, 0.01], [0, 0.01], [0, 0]]
        area = db.geometry_area_ha({"type": "Polygon", "coordinates": [ring]})
        self.assertAlmostEqual(area, 123.92, delta=0.2)

    def test_inside_and_outside(self):
        geom = {"type": "Polygon", "coordinates": [[[-81.2, 46.45], [-81.15, 46.45], [-81.15, 46.50], [-81.2, 46.50], [-81.2, 46.45]]]}
        self.assertEqual(db.geometry_distance_km(-81.175, 46.475, geom), 0.0)
        far = db.geometry_distance_km(-81.175, 46.60, geom)
        self.assertGreater(far, 5)
        self.assertLess(far, 20)

    def test_shard_key(self):
        self.assertEqual(db.shard_key("102905"), "10")
        self.assertEqual(db.shard_key("7"), "07")
        self.assertEqual(db.shard_key("Y 61893"), "Y_")


class RepoTests(unittest.TestCase):
    def test_gitignore(self):
        text = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("claims/.db/", text)
        self.assertIn("claims/.build/", text)

    def test_viewer_does_not_replace_claims_page(self):
        html = (ROOT / "claims-db.html").read_text(encoding="utf-8")
        live = (ROOT / "claims.html").read_text(encoding="utf-8")
        self.assertIn("pmtiles", html)
        viewer = (ROOT / "claims-db.js").read_text(encoding="utf-8")
        self.assertIn("Holder", viewer)
        self.assertIn("looksLikeGzip", viewer)
        self.assertIn("zoom: 3,", viewer)
        self.assertNotIn("claims-db", live)
        for box in ("ly-yukon", "ly-nunavut", "ly-nl"):
            self.assertIn(box, live)
        script = (ROOT / "claims-map.js").read_text(encoding="utf-8")
        self.assertIn('params.get("company")', script)
        self.assertIn('".pmtiles.png"', script)
        self.assertIn("ly-yukon", script)
        self.assertIn('code: "qc"', script)
        self.assertIn('code: "bc"', script)
        sw = (ROOT / "sw.js").read_text(encoding="utf-8")
        self.assertIn("/claims/tiles/", sw)
        self.assertIn("qc-shell-v222", sw)
        self.assertIn("claims-map.js?v=22", sw)
        self.assertIn("claims-db.js?v=5", sw)

    def test_public_issuer_links_keep_site_ids(self):
        aliases = json.loads((ROOT / "claims" / "links" / "aliases.json").read_text(encoding="utf-8"))
        phrases = {row["phrase"]: row["company_id"] for row in aliases["aliases"]}
        self.assertEqual(phrases["exploration azimut"], "azimut-exploration")
        self.assertEqual(phrases["alexco"], "hecla-mining")
        self.assertEqual(phrases["exploration midland"], "midland-exploration")
        self.assertEqual(phrases["kaminak"], "newmont")
        issuers = json.loads((ROOT / "claims" / "links" / "public-issuers.json").read_text(encoding="utf-8"))
        by_id = {row["id"]: row for row in issuers["issuers"]}
        self.assertEqual(by_id["azimut-exploration"]["tickers"][0], "AZM.V")
        self.assertEqual(by_id["hecla-mining"]["tickers"], ["HL"])
        self.assertEqual(by_id["fnx-inc"]["tickers"], ["FNX.CN"])
        self.assertNotIn("senoa-gold", by_id)
        catalog = json.loads((ROOT / "claims" / "companies.json").read_text(encoding="utf-8"))
        catalog_ids = {row["id"] for row in catalog["companies"]}
        off = [row["id"] for row in issuers["issuers"] if not row["on_site"]]
        self.assertTrue(off)
        self.assertFalse(set(off) & catalog_ids)


if __name__ == "__main__":
    unittest.main()
