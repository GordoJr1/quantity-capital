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


class UnitBlockTests(unittest.TestCase):
    def test_empty_unit_is_unknown(self) -> None:
        self.assertEqual(b.unit_norm(""), ("", ""))
        self.assertIsNone(b.to_troy_oz(100, ""))

    def test_scaled_tonnes_keep_their_scale(self) -> None:
        self.assertEqual(b.unit_norm("thousand tonnes")[0], "kt")
        self.assertEqual(b.unit_norm("kt")[0], "kt")
        self.assertEqual(b.unit_norm("million tonnes")[0], "Mt")
        self.assertEqual(b.unit_norm("Metric tonnes")[0], "t")
        self.assertAlmostEqual(b.to_troy_oz(1, "thousand tonnes"), b.to_troy_oz(1000, "t"), places=3)
        self.assertIsNone(b.to_troy_oz(1, "thousands of tonnes"))

    def test_non_mass_units_do_not_become_ounces(self) -> None:
        for unit in ("lb", "Mlb", "widgets", "tons"):
            self.assertIsNone(b.to_troy_oz(100, unit), unit)
        with self.assertRaises(b.UnknownUnitError):
            b.convert_number_to_troy_oz(100, "lb")

    def test_unknown_pm_unit_blocks_write(self) -> None:
        rows = [{
            "year": 2025, "geo": "Canada", "product": "Gold", "commodity_id": "gold",
            "unit": "lb", "value": 100, "status": None, "source": "statcan",
        }]
        with self.assertRaises(b.UnknownUnitError):
            b.rows_to_commodities(rows, [], 2006)

    def test_failed_live_fetch_keeps_existing_file(self) -> None:
        saved = (b.fetch_statcan, b.fetch_nrcan_year, b.fetch_map900a)

        def boom(*_a, **_k):
            raise OSError("offline")

        b.fetch_statcan = b.fetch_nrcan_year = b.fetch_map900a = boom
        try:
            with tempfile.TemporaryDirectory() as tmp:
                out = Path(tmp) / "commodities.json"
                out.write_text('{"keep": true}\n', encoding="utf-8")
                rc = b.main(["--root", str(ROOT), "--out", str(out), "--years", "2"])
                self.assertEqual(rc, 1)
                self.assertEqual(json.loads(out.read_text(encoding="utf-8")), {"keep": True})
        finally:
            b.fetch_statcan, b.fetch_nrcan_year, b.fetch_map900a = saved

    def test_offline_never_writes_default_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "canada").mkdir()
            b.main(["--root", str(root), "--offline"])
            self.assertFalse((root / "canada" / "commodities.json").exists())

    def test_fetch_does_not_retry_404(self) -> None:
        import urllib.error
        calls = []
        saved = b.urllib.request.urlopen

        def fake(req, timeout=0):
            calls.append(req.full_url)
            raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)

        b.urllib.request.urlopen = fake
        try:
            with self.assertRaises(urllib.error.HTTPError):
                b.fetch("https://example.test/missing")
        finally:
            b.urllib.request.urlopen = saved
        self.assertEqual(len(calls), 1)

    def test_validate_reads_claims_from_root(self) -> None:
        mine = {"id": "m", "name": "M", "claims_company": "elsewhere"}
        payload = {
            "schema": b.SCHEMA,
            "commodities": [
                {"id": "gold", "unit": b.TROY_OZ_UNIT, "series": [{"year": 2025, "canada": 1.0}], "mines": [mine]},
                {"id": "all", "mines": [mine]},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "claims").mkdir()
            (root / "claims" / "companies.json").write_text(
                json.dumps({"companies": [{"id": "only-this"}]}), encoding="utf-8"
            )
            errors = b.validate_payload(payload, root=root)
            self.assertIn("claims href for non-catalog id elsewhere", errors)


class ParseTests(unittest.TestCase):
    def test_statcan_prefers_quantity_produced(self) -> None:
        text = (FIXTURES / "statcan_sample.csv").read_text(encoding="utf-8")
        rows = b.parse_statcan_csv(text)
        gold = [r for r in rows if r["commodity_id"] == "gold" and r["geo"] == "Canada"]
        self.assertTrue(gold)
        self.assertEqual(gold[-1]["value"], 186923)
        self.assertEqual(gold[-1]["status"], "p")
        self.assertTrue(all("dollar" not in (r["product"] + r["unit"]).lower() for r in rows))
        silver = [r for r in rows if r["commodity_id"] == "silver" and r["geo"] == "Canada"]
        self.assertEqual(silver[-1]["value"], 356052)
        plat = [r for r in rows if r["commodity_id"] == "platinum" and r["geo"] == "Canada"]
        self.assertEqual(plat[-1]["value"], 5801459)
        self.assertEqual(plat[-1]["unit"].lower(), "grams")
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
        silver = next(r for r in rows if r["commodity_id"] == "silver")
        self.assertEqual(silver["value"], 970)
        self.assertEqual(silver["unit"].lower(), "tonnes")
        # $000 rows dropped
        self.assertEqual(len(rows), 3)

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

    def test_vale_canada_links_vale_not_canada_nickel(self) -> None:
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
        self.assertEqual(linked.get("claims_company"), "vale")
        self.assertEqual(linked.get("claims_href"), "claims.html?company=vale")

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
        self.assertEqual(gold["unit"], "troy oz")
        self.assertEqual(gold["unit_label"], "troy oz")
        self.assertAlmostEqual(years[2006], b.to_troy_oz(103513, "kg"), places=3)
        self.assertAlmostEqual(years[2025], b.to_troy_oz(186923, "kg"), places=3)
        silver = next(c for c in payload["commodities"] if c["id"] == "silver")
        sy = {p["year"]: p["canada"] for p in silver["series"] if p["canada"] is not None}
        self.assertEqual(silver["unit"], "troy oz")
        self.assertAlmostEqual(sy[2006], b.to_troy_oz(970, "t"), places=3)
        self.assertAlmostEqual(sy[2025], b.to_troy_oz(356052, "kg"), places=3)
        plat = next(c for c in payload["commodities"] if c["id"] == "platinum")
        self.assertEqual(plat["unit"], "troy oz")
        self.assertAlmostEqual(plat["series"][-1]["canada"], b.to_troy_oz(5801459, "g"), places=3)
        copper = next(c for c in payload["commodities"] if c["id"] == "copper")
        cy = {p["year"]: p["canada"] for p in copper["series"] if p["canada"] is not None}
        self.assertEqual(copper["unit"], "t")
        self.assertEqual(cy[2006], 100)
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


class TroyOzTests(unittest.TestCase):
    def test_factor_and_mass_units(self) -> None:
        self.assertEqual(b.TROY_OZ_GRAMS, 31.1034768)
        self.assertAlmostEqual(b.to_troy_oz(31.1034768, "g"), 1.0, places=6)
        self.assertAlmostEqual(b.to_troy_oz(0.0311034768, "kg"), 1.0, places=6)
        self.assertAlmostEqual(b.to_troy_oz(31.1034768 / 1_000_000, "t"), 1.0, places=6)
        self.assertEqual(b.to_troy_oz(None, "kg"), None)
        self.assertEqual(b.to_troy_oz(12.5, "troy oz"), 12.5)

    def test_nrcan_silver_tonnes_not_treated_as_kg(self) -> None:
        self.assertEqual(b.published_unit_for_stored_point(
            "silver", {"year": 2006, "source": "nrcan"}, "kg"
        ), "t")
        self.assertEqual(b.published_unit_for_stored_point(
            "silver", {"year": 2025, "source": "statcan"}, "kg"
        ), "kg")
        self.assertEqual(b.published_unit_for_stored_point(
            "platinum-group", {"year": 2018, "source": "nrcan"}, "g"
        ), "kg")

    def test_convert_payload_is_idempotent(self) -> None:
        payload = {
            "schema": b.SCHEMA,
            "commodities": [
                {
                    "id": "silver",
                    "name": "Silver",
                    "unit": "kg",
                    "unit_label": "kilograms",
                    "series": [
                        {"year": 2006, "canada": 970.0, "source": "nrcan", "provinces": {"Ontario": 178.0}},
                        {"year": 2025, "canada": 356052.0, "source": "statcan"},
                    ],
                    "mines": [{"name": "Keno Hill"}],
                },
                {
                    "id": "platinum-recoverable",
                    "name": "Platinum",
                    "unit": "g",
                    "series": [{"year": 2025, "canada": 5801459.0, "source": "statcan"}],
                    "mines": [],
                },
                {
                    "id": "copper",
                    "name": "Copper",
                    "unit": "t",
                    "series": [{"year": 2025, "canada": 499896.0, "source": "statcan"}],
                    "mines": [],
                },
            ],
        }
        once = b.convert_payload_to_troy_oz(payload)
        silver = next(c for c in once["commodities"] if c["id"] == "silver")
        plat = next(c for c in once["commodities"] if c["id"] == "platinum")
        copper = next(c for c in once["commodities"] if c["id"] == "copper")
        self.assertEqual(silver["unit"], "troy oz")
        self.assertAlmostEqual(silver["series"][0]["canada"], b.to_troy_oz(970, "t"), places=3)
        self.assertAlmostEqual(silver["series"][0]["provinces"]["Ontario"], b.to_troy_oz(178, "t"), places=3)
        self.assertAlmostEqual(silver["series"][1]["canada"], b.to_troy_oz(356052, "kg"), places=3)
        self.assertEqual(plat["id"], "platinum")
        self.assertAlmostEqual(plat["series"][0]["canada"], b.to_troy_oz(5801459, "g"), places=3)
        self.assertEqual(copper["unit"], "t")
        self.assertEqual(copper["series"][0]["canada"], 499896.0)
        self.assertEqual(silver["mines"][0]["name"], "Keno Hill")
        twice = b.convert_payload_to_troy_oz(once)
        silver2 = next(c for c in twice["commodities"] if c["id"] == "silver")
        self.assertEqual(silver2["series"][0]["canada"], silver["series"][0]["canada"])


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
