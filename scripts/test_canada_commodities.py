#!/usr/bin/env python3
"""Unit tests for Canadian commodity ingest + owner linker."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_canada_commodities as b

FIXTURES = HERE / "fixtures" / "canada"
ROOT = HERE.parent


class ParseTests(unittest.TestCase):
    def test_statcan_prefers_quantity_produced(self) -> None:
        text = (FIXTURES / "statcan_sample.csv").read_text(encoding="utf-8")
        rows = b.parse_statcan_csv(text)
        gold = [r for r in rows if r["commodity_id"] == "gold" and r["geo"] == "Canada"]
        self.assertTrue(gold)
        self.assertEqual(gold[-1]["value"], 186923)
        self.assertEqual(gold[-1]["status"], "p")
        self.assertTrue(all("dollar" not in (r["product"] + r["unit"]).lower() for r in rows))
        shipped = [r for r in rows if "shipped" in (r.get("product") or "").lower()]
        self.assertEqual(shipped, [])

    def test_nrcan_2006_gold_canada(self) -> None:
        html = (FIXTURES / "nrcan_2006.html").read_text(encoding="utf-8")
        rows = b.parse_nrcan_html(html, 2006)
        gold = next(r for r in rows if r["commodity_id"] == "gold")
        self.assertEqual(gold["value"], 103513)
        self.assertEqual(gold["provinces"]["Ontario"], 57340)
        self.assertNotIn("tonnes", gold)
        copper = next(r for r in rows if r["commodity_id"] == "copper")
        self.assertEqual(copper["value"], 100)
        # $000 rows dropped
        self.assertEqual(len(rows), 2)

    def test_nrcan_2025_skips_shipped_and_value(self) -> None:
        html = (FIXTURES / "nrcan_2025.html").read_text(encoding="utf-8")
        rows = b.parse_nrcan_html(html, 2025)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["value"], 186923)
        self.assertEqual(rows[0]["status"], "p")
        self.assertIsNone(rows[0]["provinces"].get("Saskatchewan"))  # confidential x


class LinkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = {
            "agnico-eagle": {
                "id": "agnico-eagle",
                "names": ["Agnico Eagle Mines Limited", "Agnico Eagle"],
                "mines": [{"id": "canadian-malartic", "name": "Canadian Malartic Complex"}],
            },
            "equinox-gold": {
                "id": "equinox-gold",
                "names": ["Equinox Gold", "Equinox Gold Corp."],
                "mines": [{"id": "valentine", "name": "Valentine"}],
            },
        }
        self.name_idx, self.mine_idx = b.build_indexes(self.catalog)

    def test_owner_and_mine_deep_link(self) -> None:
        raw = {
            "name": "Canadian Malartic",
            "owners": "Agnico Eagle Mines Limited",
            "province": "Quebec",
            "city": "Malartic",
            "product_ids": ["gold"],
            "lat": 48.13,
            "lon": -78.13,
            "layer": "metals",
        }
        linked = b.link_mine(raw, self.catalog, self.name_idx, self.mine_idx)
        self.assertEqual(linked["claims_company"], "agnico-eagle")
        self.assertEqual(linked["claims_asset"], "canadian-malartic")
        self.assertEqual(
            linked["claims_href"],
            "claims.html?company=agnico-eagle&asset=canadian-malartic",
        )
        self.assertNotIn("tonnes", linked)
        self.assertNotIn("koz", linked)

    def test_unknown_owner_stays_unlinked(self) -> None:
        raw = {
            "name": "Unknown Brook",
            "owners": "Not A Listed Issuer Inc.",
            "province": "Yukon",
            "city": "Dawson",
            "product_ids": ["gold"],
            "layer": "metals",
        }
        linked = b.link_mine(raw, self.catalog, self.name_idx, self.mine_idx)
        self.assertIsNone(linked["claims_company"])
        self.assertIsNone(linked["claims_href"])

    def test_vale_canada_does_not_steal_canada_nickel(self) -> None:
        catalog = b.load_company_catalog(ROOT)
        name_idx, mine_idx = b.build_indexes(catalog)
        linked = b.link_mine(
            {
                "name": "Thompson (T-1 and T-3)",
                "owners": "Vale Canada Limited",
                "province": "Manitoba",
                "city": "Thompson",
                "product_ids": ["nickel"],
                "layer": "metals",
            },
            catalog,
            name_idx,
            mine_idx,
        )
        self.assertNotEqual(linked.get("claims_company"), "canada-nickel")
        self.assertIsNone(linked.get("claims_href"))

    def test_builtin_alias(self) -> None:
        cid, how = b.match_owner("Equinox Gold Corp.", self.name_idx, self.catalog)
        self.assertEqual(cid, "equinox-gold")
        self.assertTrue(how)


class OfflineBuildTests(unittest.TestCase):
    def test_offline_payload_valid(self) -> None:
        payload = b.build_payload(root=ROOT, offline=True)
        errors = b.validate_payload(payload)
        self.assertEqual(errors, [])
        gold = next(c for c in payload["commodities"] if c["id"] == "gold")
        years = {p["year"]: p["canada"] for p in gold["series"] if p["canada"] is not None}
        self.assertEqual(years[2006], 103513)
        self.assertEqual(years[2025], 186923)
        self.assertGreaterEqual(len(gold["mines"]), 1)
        malartic = next(m for m in gold["mines"] if "Malartic" in m["name"])
        self.assertTrue(malartic["claims_href"].startswith("claims.html?company="))
        all_ops = next(c for c in payload["commodities"] if c["id"] == "all")
        names = {m["name"] for m in all_ops["mines"]}
        self.assertIn("Canadian Malartic", names)
        self.assertIn("Unknown Brook", names)
        self.assertIn("Vanscoy", names)
        unknown = next(m for m in all_ops["mines"] if m["name"] == "Unknown Brook")
        self.assertIsNone(unknown.get("claims_company"))
        self.assertEqual(len(all_ops["mines"]), payload["n_mines"])
        for comm in payload["commodities"]:
            for mine in comm["mines"]:
                self.assertNotIn("tonnes", mine)
                self.assertNotIn("quantity", mine)

    def test_check_roundtrip(self) -> None:
        payload = b.build_payload(root=ROOT, offline=True)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "commodities.json"
            b.write_json(out, payload)
            rc = b.main(["--check", "--out", str(out)])
            self.assertEqual(rc, 0)


class LiveCatalogSmoke(unittest.TestCase):
    def test_claims_catalog_indexes_agnico(self) -> None:
        catalog = b.load_company_catalog(ROOT)
        self.assertIn("agnico-eagle", catalog)
        name_idx, mine_idx = b.build_indexes(catalog)
        cid, mid, how = b.match_mine("Canadian Malartic", mine_idx)
        self.assertEqual(cid, "agnico-eagle")
        self.assertEqual(mid, "canadian-malartic")
        self.assertTrue(how)


if __name__ == "__main__":
    unittest.main()
