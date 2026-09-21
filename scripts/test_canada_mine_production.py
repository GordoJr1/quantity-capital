#!/usr/bin/env python3
"""Tests for cited 2025 mine-production ingest — no live IR fetch required."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_canada_commodities as b
import canada_beta_production as beta
import ingest_canada_mine_production as ing

SQ_DIR = HERE / "qc_sqlite"
if str(SQ_DIR) not in sys.path:
    sys.path.insert(0, str(SQ_DIR))
import canada_mines as cmsql  # noqa: E402

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

    def test_keeps_kt_mt_kct_scale(self) -> None:
        kt, kt_unit, kt_err = ing.convert_reported(33.2, "kt", "nickel")
        self.assertIsNone(kt_err)
        self.assertEqual(kt, 33.2)
        self.assertEqual(kt_unit, "kt")
        mt, mt_unit, mt_err = ing.convert_reported(4.64, "million tonnes", "potash")
        self.assertIsNone(mt_err)
        self.assertEqual(mt, 4.64)
        self.assertEqual(mt_unit, "Mt")
        kct, kct_unit, kct_err = ing.convert_reported(2210, "000 carats", "diamonds")
        self.assertIsNone(kct_err)
        self.assertEqual(kct, 2210)
        self.assertEqual(kct_unit, "kct")
        mct, mct_unit, mct_err = ing.convert_reported(4.4, "million carats", "diamonds")
        self.assertIsNone(mct_err)
        self.assertEqual(mct, 4400)
        self.assertEqual(mct_unit, "kct")


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

    def test_pdf_object_soup_is_unusable(self) -> None:
        soup = "1 0 obj /Type /Page /Contents 2 0 R endobj 2 0 obj (stream)"
        self.assertTrue(ing.extract_looks_unusable(soup))
        rec = ing.record_for_source(
            {
                "mine_id": "brucejack",
                "mine_name": "Brucejack",
                "url": "https://example.test/brucejack.pdf",
                "extract": {
                    "gold": {
                        "source_value": 231,
                        "source_unit": "koz",
                        "quote": "Brucejack, Canada. Gold production decreased 10%",
                    }
                },
            },
            text=soup,
            fetch_ok=False,
            fetch_error="unusable_extract",
        )
        self.assertEqual(rec["commodities"]["gold"]["value"], 231000)
        self.assertNotIn("gold", {s.get("commodity") for s in rec.get("skipped") or []})


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


class BetaProducerTests(unittest.TestCase):
    def test_koz_roundtrip(self) -> None:
        profile = {"units": {"gold": "koz"}}
        koz = beta.troy_to_profile_gold(192808, profile)
        self.assertEqual(koz, 192.808)
        self.assertEqual(beta.profile_gold_to_troy(koz, profile), 192808)

    def test_figure_prefers_100pct(self) -> None:
        profile = {
            "units": {"gold": "koz"},
            "production": [{
                "period": "2025",
                "kind": "annual",
                "by_asset": {
                    "red-chris": {"attr_koz": 62, "koz_100pct": 89, "ownership_pct": 70},
                },
            }],
        }
        fig = beta.figure_for_table(profile, "red-chris", "gold")
        self.assertEqual(fig["value"], 89000)
        self.assertEqual(fig["unit"], "troy oz")

    def test_write_does_not_overwrite_newmont_brucejack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            newmont = json.loads((ROOT / "beta" / "newmont.json").read_text(encoding="utf-8"))
            (root / "beta" / "newmont.json").write_text(
                json.dumps(newmont), encoding="utf-8"
            )
            book = {
                "mines": {
                    "brucejack": {
                        "mine_name": "Brucejack",
                        "commodities": {
                            "gold": {"value": 231000, "unit": "troy oz", "quote": "231"}
                        },
                        "production_source": "https://example.test/nem",
                        "production_source_title": "Newmont stats",
                    }
                }
            }
            sources = {"mines": [{
                "mine_id": "brucejack",
                "mine_name": "Brucejack",
                "beta_id": "newmont",
                "asset_id": "brucejack",
                "url": "https://example.test/nem",
                "title": "Newmont stats",
            }]}
            beta.write_beta_profiles(book, sources, root=root)
            after = json.loads((root / "beta" / "newmont.json").read_text(encoding="utf-8"))
            rec = beta.annual_2025(after)
            self.assertEqual(rec["by_asset"]["brucejack"]["attr_koz"], 231)
            self.assertIn("lihir", rec["by_asset"])
            self.assertEqual(rec["attr_koz"], newmont["production"][0]["attr_koz"])

    def test_write_fills_empty_shell(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            shell = json.loads((ROOT / "beta" / "hudbay-minerals.json").read_text(encoding="utf-8"))
            (root / "beta" / "hudbay-minerals.json").write_text(
                json.dumps(shell), encoding="utf-8"
            )
            book = {
                "mines": {
                    "copper-mountain": {
                        "mine_name": "Copper Mountain",
                        "commodities": {
                            "gold": {"value": 20001, "unit": "troy oz", "quote": "20,001"},
                            "copper": {"value": 23784, "unit": "t", "quote": "23,784"},
                        },
                        "production_source": "https://example.test/hbm",
                        "production_source_title": "Hudbay 2025",
                    }
                }
            }
            sources = {"mines": [{
                "mine_id": "copper-mountain",
                "mine_name": "Copper Mountain",
                "url": "https://example.test/hbm",
                "title": "Hudbay 2025",
            }]}
            beta.write_beta_profiles(book, sources, root=root)
            after = json.loads((root / "beta" / "hudbay-minerals.json").read_text(encoding="utf-8"))
            rec = beta.annual_2025(after)
            row = rec["by_asset"]["copper-mountain"]
            self.assertEqual(row["attr_koz"], 20.001)
            self.assertEqual(row["copper_t"], 23784)
            self.assertTrue(any(a["id"] == "copper-mountain" for a in after["assets"]))
            fig = beta.figure_for_table(after, "copper-mountain", "gold")
            self.assertEqual(fig["value"], 20001)
            self.assertEqual(beta.figure_for_table(after, "copper-mountain", "copper")["unit"], "t")

    def test_join_has_no_ounces(self) -> None:
        book = {
            "mines": {
                "blackwater": {
                    "mine_name": "Blackwater",
                    "commodities": {"gold": {"value": 192808, "unit": "troy oz"}},
                    "production_source": "https://example.test/artg",
                },
                "elk": {"mine_name": "Elk", "blocker": "fiscal year", "commodities": {}},
            }
        }
        sources = {"mines": [
            {"mine_id": "blackwater", "mine_name": "Blackwater", "url": "https://example.test/artg"},
            {"mine_id": "elk", "mine_name": "Elk", "blocker": "fiscal year"},
        ]}
        join = beta.build_join(book, sources)
        self.assertEqual(join["schema"], beta.JOIN_SCHEMA)
        self.assertEqual(join["mines"]["blackwater"]["beta_id"], "artemis-gold")
        self.assertEqual(join["mines"]["blackwater"]["asset_id"], "blackwater")
        self.assertNotIn("attr_koz", join["mines"]["blackwater"])
        self.assertNotIn("value", join["mines"]["blackwater"])
        self.assertTrue(join["mines"]["elk"]["blocker"])

    def test_complex_total_not_written_to_pit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            alamos = json.loads((ROOT / "beta" / "alamos-gold.json").read_text(encoding="utf-8"))
            (root / "beta" / "alamos-gold.json").write_text(json.dumps(alamos), encoding="utf-8")
            book = {
                "mines": {
                    "island-gold-island-gold-district": {
                        "mine_name": "Island Gold",
                        "blocker": "complex total",
                        "commodities": {},
                    }
                }
            }
            sources = {"mines": [{
                "mine_id": "island-gold-island-gold-district",
                "mine_name": "Island Gold",
                "blocker": "complex total",
            }]}
            beta.write_beta_profiles(book, sources, root=root)
            after = json.loads((root / "beta" / "alamos-gold.json").read_text(encoding="utf-8"))
            rec = beta.annual_2025(after)
            self.assertNotIn("island-gold-island-gold-district", rec["by_asset"])
            self.assertIn("island-gold-district", rec["by_asset"])

    def test_join_export_carries_sqlite_commodities(self) -> None:
        book = {
            "mines": {
                "blackwater": {
                    "mine_name": "Blackwater",
                    "commodities": {
                        "gold": {"value": 192808, "unit": "troy oz", "quote": "192,808"}
                    },
                    "production_source": "https://example.test/artg",
                },
                "elk": {"mine_name": "Elk", "blocker": "fiscal year", "commodities": {}},
            }
        }
        sources = {"mines": [
            {"mine_id": "blackwater", "mine_name": "Blackwater", "url": "https://example.test/artg"},
            {"mine_id": "elk", "mine_name": "Elk", "blocker": "fiscal year"},
        ]}
        join = beta.build_join(book, sources, include_figures=True)
        gold = join["mines"]["blackwater"]["commodities"]["gold"]
        self.assertEqual(gold["value"], 192808)
        self.assertEqual(gold["unit"], "troy oz")
        self.assertNotIn("attr_koz", join["mines"]["blackwater"])
        self.assertNotIn("commodities", join["mines"]["elk"])

    def test_write_does_not_mint_missing_issuer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            book = {
                "mines": {
                    "elk": {
                        "mine_name": "Elk",
                        "blocker": "fiscal year",
                        "commodities": {},
                    }
                }
            }
            sources = {"mines": [{
                "mine_id": "elk",
                "mine_name": "Elk",
                "beta_id": "gold-mountain-mining",
                "asset_id": "elk",
                "blocker": "fiscal year",
            }]}
            stats = beta.write_beta_profiles(book, sources, root=root, create=False)
            self.assertIn("elk", stats["skipped"])
            self.assertFalse((root / "beta" / "gold-mountain-mining.json").exists())


class SqliteStoreTests(unittest.TestCase):
    def _book(self, n_fig: int = 5, n_blank: int = 5) -> tuple[dict, dict]:
        mines: dict[str, Any] = {}
        src_rows: list[dict] = []
        for i in range(n_fig):
            mid = f"mine-{i}"
            mines[mid] = {
                "mine_id": mid,
                "mine_name": mid.title(),
                "year": 2025,
                "kind": "ops-update",
                "production_source": f"https://example.test/{mid}",
                "production_source_title": f"{mid} 2025",
                "production_as_of": "2026-02-01",
                "commodities": {
                    "gold": {
                        "value": 1000 * (i + 1),
                        "unit": "troy oz",
                        "source_value": 1000 * (i + 1),
                        "source_unit": "oz",
                        "quote": f"{1000 * (i + 1)} ounces of gold",
                    }
                },
                "fetch_ok": True,
            }
            src_rows.append({
                "mine_id": mid,
                "mine_name": mid.title(),
                "beta_id": "artemis-gold" if i == 0 else None,
                "asset_id": "blackwater" if i == 0 else mid,
                "url": f"https://example.test/{mid}",
                "title": f"{mid} 2025",
                "as_of": "2026-02-01",
                "kind": "ops-update",
            })
        for i in range(n_blank):
            mid = f"blank-{i}"
            mines[mid] = {
                "mine_id": mid,
                "mine_name": mid.title(),
                "year": 2025,
                "commodities": {},
                "blocker": "not disclosed",
                "fetch_ok": False,
            }
            src_rows.append({
                "mine_id": mid,
                "mine_name": mid.title(),
                "blocker": "not disclosed",
            })
        book = {
            "schema": ing.SCHEMA,
            "year": 2025,
            "mines": mines,
            "n_with_figure": n_fig,
            "n_blank": n_blank,
            "n_sources": n_fig + n_blank,
        }
        return book, {"year": 2025, "mines": src_rows}

    def test_store_roundtrip_and_export(self) -> None:
        book, sources = self._book()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            (root / "canada").mkdir()
            artemis = json.loads((ROOT / "beta" / "artemis-gold.json").read_text(encoding="utf-8"))
            (root / "beta" / "artemis-gold.json").write_text(json.dumps(artemis), encoding="utf-8")
            db = root / "qc.sqlite"
            con = cmsql.connect(db)
            try:
                counts = cmsql.store_book(con, book, sources)
                self.assertEqual(counts["n_mines"], 10)
                self.assertEqual(counts["n_production"], 5)
                errors = cmsql.validate_db(con)
                self.assertEqual(errors, [])
                exported = cmsql.export_pages(con, root=root)
            finally:
                con.close()
            join = exported["join"]
            self.assertEqual(join["built_from"], "qc.sqlite")
            self.assertEqual(join["mines"]["mine-0"]["commodities"]["gold"]["value"], 1000)
            self.assertEqual(join["mines"]["mine-0"]["commodities"]["gold"]["unit"], "troy oz")
            self.assertTrue((root / "canada" / "producer-join.json").exists())
            # Existing issuer updated; no new issuer files.
            self.assertTrue((root / "beta" / "artemis-gold.json").exists())
            self.assertEqual(len(list((root / "beta").glob("*.json"))), 1)
            again = cmsql.connect(db)
            try:
                rebuilt = cmsql.book_from_db(again)
            finally:
                again.close()
            self.assertEqual(rebuilt["mines"]["mine-0"]["commodities"]["gold"]["value"], 1000)
            self.assertFalse(rebuilt["mines"]["blank-0"].get("commodities"))

    def test_cli_export_only_from_sqlite(self) -> None:
        book, sources = self._book()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            (root / "canada").mkdir()
            db = root / "qc.sqlite"
            con = cmsql.connect(db)
            try:
                cmsql.store_book(con, book, sources)
            finally:
                con.close()
            rc = ing.main([
                "--root", str(root),
                "--sqlite", str(db),
                "--export-only",
            ])
            self.assertEqual(rc, 0)
            join = json.loads((root / "canada" / "producer-join.json").read_text(encoding="utf-8"))
            self.assertEqual(join["schema"], beta.JOIN_SCHEMA)
            self.assertGreaterEqual(join["n_with_figure"], 5)
            self.assertEqual(join["built_from"], "qc.sqlite")


if __name__ == "__main__":
    unittest.main()
