#!/usr/bin/env python3
"""Tests for cited 2025 mine-production ingest — no live IR fetch required."""
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
import ingest_canada_mine_production as ing

ROOT = HERE.parent


class ConvertTests(unittest.TestCase):
    def test_mining_ounces_are_troy(self) -> None:
        val, unit, err = ing.convert_reported(192808, "oz", "gold")
        self.assertIsNone(err)
        self.assertEqual(unit, "troy oz")
        self.assertEqual(val, 192808)

    def test_koz_times_thousand(self) -> None:
        val, unit, err = ing.convert_reported(231, "koz", "gold")
        self.assertIsNone(err)
        self.assertEqual(val, 231000)
        self.assertEqual(unit, "troy oz")

    def test_kg_uses_troy_factor(self) -> None:
        val, unit, err = ing.convert_reported(31.1034768, "kg", "gold")
        self.assertIsNone(err)
        self.assertAlmostEqual(val, 1000.0, places=3)
        self.assertEqual(unit, "troy oz")

    def test_rejects_aueq(self) -> None:
        val, _unit, err = ing.convert_reported(40000, "oz", "gold equivalent")
        self.assertIsNone(val)
        self.assertEqual(err, "aueq_not_split")

    def test_copper_keeps_source_unit(self) -> None:
        val, unit, err = ing.convert_reported(23_784, "t", "copper")
        self.assertIsNone(err)
        self.assertEqual(val, 23784)
        self.assertEqual(unit, "t")
        mlb, mlb_unit, mlb_err = ing.convert_reported(50.1, "Mlb", "copper")
        self.assertIsNone(mlb_err)
        self.assertEqual(mlb, 50.1)
        self.assertEqual(mlb_unit, "Mlb")


class VerifyTests(unittest.TestCase):
    def test_quote_must_appear(self) -> None:
        html = (HERE / "fixtures" / "canada" / "mine_production_sample.html").read_text(encoding="utf-8")
        text = ing.strip_markup(html)
        ok, why = ing.verify_extract(
            text,
            {"quote": "bringing full year 2025 production to 192,808 ounces of gold"},
        )
        self.assertTrue(ok, why)
        bad, _why = ing.verify_extract(text, {"quote": "invented 999,999 ounces of gold"})
        self.assertFalse(bad)


class OverlayTests(unittest.TestCase):
    def test_requires_url_and_skips_blank(self) -> None:
        book = {
            "mines": {
                "blackwater": {
                    "commodities": {"gold": {"value": 192808, "unit": "troy oz", "quote": "192,808"}},
                    "production_source": "https://example.test/artemis",
                    "production_as_of": "2026-01-14",
                },
                "elk": {"blocker": "fiscal year, not calendar 2025", "commodities": {}},
            }
        }
        mines = [
            {"id": "blackwater", "name": "Blackwater"},
            {"id": "elk", "name": "Elk"},
            {"id": "unknown-brook", "name": "Unknown Brook"},
        ]
        ing.overlay_mines(mines, book)
        self.assertEqual(mines[0]["production_2025"]["gold"], 192808)
        self.assertEqual(mines[0]["production_unit"]["gold"], "troy oz")
        self.assertTrue(mines[0]["production_source"].startswith("http"))
        self.assertEqual(mines[1].get("production_blocker"), "fiscal year, not calendar 2025")
        self.assertNotIn("production_2025", mines[1])
        self.assertNotIn("production_2025", mines[2])

    def test_validate_book_rejects_unsourced(self) -> None:
        book = {
            "schema": ing.SCHEMA,
            "year": 2025,
            "mines": {
                "x": {"commodities": {"gold": {"value": 1, "unit": "troy oz"}}, "production_source": ""}
            },
        }
        errors = ing.validate_book(book)
        self.assertTrue(any("URL" in e for e in errors))


class SourcesBookTests(unittest.TestCase):
    def test_curated_sources_have_urls_and_no_aueq_gold(self) -> None:
        data = json.loads((HERE / "canada-mine-production-sources.json").read_text(encoding="utf-8"))
        self.assertEqual(data["year"], 2025)
        n_extract = 0
        for row in data["mines"]:
            if row.get("extract"):
                n_extract += 1
                self.assertTrue(str(row.get("url") or "").startswith("http"), row["mine_id"])
                self.assertNotIn("aueq", row["extract"])
                self.assertNotIn("gold-equivalent", row["extract"])
            if row.get("blocker") and row.get("extract"):
                self.fail(f"{row['mine_id']} has both extract and blocker")
        self.assertGreaterEqual(n_extract, 8)


class OfflineIngestTests(unittest.TestCase):
    def test_offline_blackwater_from_fixture(self) -> None:
        # Restrict to the two fixture-backed gold rows so --offline can verify.
        slim = {
            "schema": "qc-canada-mine-production-sources-v1",
            "year": 2025,
            "mines": [
                {
                    "mine_id": "blackwater",
                    "mine_name": "Blackwater",
                    "year": 2025,
                    "url": "https://www.artemisgoldinc.com/news/media-releases/artemis-gold-announces-record-q4-2025-production-results-and-2026-guidance",
                    "title": "Artemis fixture",
                    "as_of": "2026-01-14",
                    "extract": {
                        "gold": {
                            "source_value": 192808,
                            "source_unit": "oz",
                            "quote": "bringing full year 2025 production to 192,808 ounces of gold",
                        }
                    },
                },
                {
                    "mine_id": "elk",
                    "mine_name": "Elk",
                    "blocker": "fiscal year, not calendar 2025",
                    "url": "https://example.test/elk",
                },
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "sources.json"
            src.write_text(json.dumps(slim), encoding="utf-8")
            book = ing.ingest(root=ROOT, sources_path=src, offline=True, fetch_live=False)
        gold = book["mines"]["blackwater"]["commodities"]["gold"]
        self.assertEqual(gold["value"], 192808)
        self.assertEqual(gold["unit"], "troy oz")
        self.assertFalse(book["mines"]["elk"].get("commodities"))
        self.assertIn("fiscal year", book["mines"]["elk"]["blocker"])


class BuildOverlayHook(unittest.TestCase):
    def test_offline_commodities_accept_sourced_production(self) -> None:
        payload = b.build_payload(root=ROOT, offline=True)
        book = {
            "mines": {
                "valentine": {
                    "commodities": {"gold": {"value": 23816, "unit": "troy oz", "quote": "23,816"}},
                    "production_source": "https://www.equinoxgold.com/example",
                    "production_as_of": "2026-02-18",
                    "production_source_title": "Equinox MD&A",
                }
            }
        }
        ing.overlay_mines(payload["mines"], book)
        valentine = next(m for m in payload["mines"] if m["id"] == "valentine")
        self.assertEqual(valentine["production_2025"]["gold"], 23816)
        errors = b.validate_payload(payload)
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
