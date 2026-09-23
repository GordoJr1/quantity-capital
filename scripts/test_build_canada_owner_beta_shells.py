#!/usr/bin/env python3
"""Tests for Canada Principal Mines owner → beta shell coverage."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_canada_commodities as b
import build_canada_owner_beta_shells as s


class ResolveTests(unittest.TestCase):
    def test_vale_alias_to_existing_page(self) -> None:
        mines = [{"id": "voisey-s-bay", "name": "Voisey’s Bay", "owners": "Vale Canada Limited"}]
        plan = s.plan_owners(ROOT, mines)
        self.assertEqual(plan["owner_to_id"]["Vale Canada Limited"], "vale")
        row = next(r for r in plan["rows"] if r["owner"] == "Vale Canada Limited")
        self.assertTrue(row["exists"])
        self.assertFalse(row["mint"])

    def test_glencore_and_agnico_alias(self) -> None:
        mines = [
            {"id": "raglan", "name": "Raglan", "owners": "Glencore Canada Corporation"},
            {"id": "malartic", "name": "Canadian Malartic", "owners": "Agnico Eagle Mines Limited"},
        ]
        plan = s.plan_owners(ROOT, mines)
        self.assertEqual(plan["owner_to_id"]["Glencore Canada Corporation"], "glencore")
        self.assertEqual(plan["owner_to_id"]["Agnico Eagle Mines Limited"], "agnico-eagle")

    def test_does_not_bind_compass_to_compass_gold(self) -> None:
        mines = [{"id": "goderich", "name": "Goderich", "owners": "Compass Minerals Canada Corporation"}]
        plan = s.plan_owners(ROOT, mines)
        self.assertEqual(plan["owner_to_id"]["Compass Minerals Canada Corporation"], "compass-minerals")
        self.assertNotEqual(plan["owner_to_id"]["Compass Minerals Canada Corporation"], "compass-gold")

    def test_does_not_bind_imperial_metals_to_imperial_oil(self) -> None:
        mines = [{"id": "mount-polley", "name": "Mount Polley", "owners": "Imperial Metals Corporation"}]
        plan = s.plan_owners(ROOT, mines)
        self.assertEqual(plan["owner_to_id"]["Imperial Metals Corporation"], "imperial-metals")

    def test_canadian_royalties_not_royalties(self) -> None:
        mines = [{"id": "nunavik", "name": "Nunavik Nickel", "owners": "Canadian Royalties Inc."}]
        plan = s.plan_owners(ROOT, mines)
        self.assertEqual(plan["owner_to_id"]["Canadian Royalties Inc."], "canadian-royalties")

    def test_shell_has_map_assets_no_ounces(self) -> None:
        payload = s.new_shell_payload(
            beta_id="amrize",
            owner="Amrize Ltd.",
            name="Amrize Ltd.",
            mines=[
                {
                    "id": "exshaw",
                    "name": "Exshaw",
                    "location": "Exshaw, Alberta",
                    "city": "Exshaw",
                    "province": "Alberta",
                    "owners": "Amrize Ltd.",
                    "products": ["limestone"],
                    "layer": "nonmetals",
                    "lat": 51.0,
                    "lon": -115.0,
                }
            ],
            symbols=["AMRZ"],
            caps={"AMRZ": {"cap": 1, "cur": "USD", "shares": 2, "px": 3, "asof": "2026-09-18"}},
            insider=None,
        )
        self.assertEqual(payload["schema"], "qc-issuer-profile-v1")
        self.assertEqual(payload["production"], [])
        self.assertEqual(payload["kpis"]["attr_koz_2025"], None)
        self.assertEqual(len(payload["assets"]), 1)
        self.assertTrue(payload["assets"][0]["in_production"])
        self.assertNotIn("koz", payload["assets"][0])
        self.assertNotIn("tonnes", payload["assets"][0])
        self.assertIn("Do not invent ounces", payload["disclaimer"])

    def test_apply_beta_fields_sets_vale_href(self) -> None:
        payload = {
            "mines": [
                {"id": "voisey-s-bay", "name": "Voisey’s Bay", "owners": "Vale Canada Limited"},
                {"id": "unknown", "name": "Unknown Brook", "owners": "Not A Listed Issuer Inc."},
            ],
            "commodities": [
                {
                    "id": "nickel",
                    "mines": [
                        {"id": "voisey-s-bay", "name": "Voisey’s Bay", "owners": "Vale Canada Limited"}
                    ],
                }
            ],
        }
        n = s.apply_beta_fields(payload, {"Vale Canada Limited": "vale"})
        self.assertGreaterEqual(n, 1)
        self.assertEqual(payload["mines"][0]["beta_id"], "vale")
        self.assertEqual(payload["mines"][0]["beta_href"], "beta.html?id=vale")
        self.assertIsNone((payload["mines"][1].get("owner_links") or [{}])[0].get("beta_id"))


class CommoditiesLinkerTests(unittest.TestCase):
    def test_vale_claims_linked(self) -> None:
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
        self.assertEqual(linked.get("claims_href"), "claims.html?company=vale")
        b.apply_beta_owner_links([linked], ROOT)
        if (ROOT / "beta" / "vale.json").exists() and (HERE / "canada-owner-aliases.json").exists():
            aliases = json.loads((HERE / "canada-owner-aliases.json").read_text(encoding="utf-8"))
            mapped = any(
                row.get("owner") == "Vale Canada Limited" and row.get("company_id") == "vale"
                for row in aliases.get("aliases") or []
            )
            if mapped:
                self.assertEqual(linked.get("beta_id"), "vale")
                self.assertEqual(linked.get("beta_href"), "beta.html?id=vale")


class TempRootTests(unittest.TestCase):
    def test_mint_private_stub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            (root / "canada").mkdir()
            (root / "scripts").mkdir()
            vale = {
                "schema": "qc-issuer-profile-v1",
                "id": "vale",
                "kind": "producer",
                "stage": "producing",
                "disclaimer": "Insiders-watchlist shell. Do not invent ounces.",
                "issuer": {"name": "Vale S.A.", "short": "Vale", "tickers": []},
                "production": [],
                "assets": [],
                "meta": {"layer": "mcap-top200", "filing_backed": False},
            }
            (root / "beta" / "vale.json").write_text(json.dumps(vale), encoding="utf-8")
            mines = [
                {
                    "id": "voisey-s-bay",
                    "name": "Voisey’s Bay",
                    "owners": "Vale Canada Limited",
                    "location": "Voisey’s Bay, Newfoundland and Labrador",
                    "city": "Voisey’s Bay",
                    "province": "Newfoundland and Labrador",
                    "products": ["nickel"],
                    "layer": "metals",
                },
                {
                    "id": "graymont-pit",
                    "name": "Graymont Pit",
                    "owners": "Graymont Inc.",
                    "location": "Bedford, Nova Scotia",
                    "city": "Bedford",
                    "province": "Nova Scotia",
                    "products": ["lime"],
                    "layer": "nonmetals",
                },
            ]
            (root / "canada" / "commodities.json").write_text(
                json.dumps({"schema": "qc-canada-commodities-v1", "mines": mines, "commodities": []}),
                encoding="utf-8",
            )
            (root / "insider-companies.json").write_text(json.dumps({"companies": []}), encoding="utf-8")
            (root / "market-caps.json").write_text(json.dumps({"tickers": {}}), encoding="utf-8")
            rc = s.main(["--root", str(root)])
            self.assertEqual(rc, 0)
            self.assertTrue((root / "beta" / "graymont.json").exists())
            stub = json.loads((root / "beta" / "graymont.json").read_text(encoding="utf-8"))
            self.assertEqual(stub["production"], [])
            self.assertEqual(stub["assets"][0]["name"], "Graymont Pit")
            self.assertTrue(stub["assets"][0]["in_production"])
            aliases = json.loads((root / "scripts" / "canada-owner-aliases.json").read_text(encoding="utf-8"))
            mapped = {row["owner"]: row["company_id"] for row in aliases["aliases"]}
            self.assertEqual(mapped["Vale Canada Limited"], "vale")
            self.assertEqual(mapped["Graymont Inc."], "graymont")
            book = json.loads((root / "canada" / "commodities.json").read_text(encoding="utf-8"))
            vale_row = next(m for m in book["mines"] if m["owners"] == "Vale Canada Limited")
            self.assertEqual(vale_row["beta_href"], "beta.html?id=vale")

    def test_check_allows_filing_backed_production(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            (root / "canada").mkdir()
            (root / "scripts").mkdir()
            (root / "beta" / "vale.json").write_text(
                json.dumps({
                    "schema": "qc-issuer-profile-v1",
                    "id": "vale",
                    "kind": "producer",
                    "production": [{"year": 2025, "nickel_kt": 33.2, "asset_id": "voiseys-bay"}],
                    "assets": [{"id": "voiseys-bay", "name": "Voisey's Bay", "nickel_kt": 33.2}],
                    "meta": {
                        "layer": "mcap-top200",
                        "filing_backed": True,
                        "map_900a_assets": True,
                        "watchlist": "canada-map-900a",
                    },
                }),
                encoding="utf-8",
            )
            (root / "canada" / "commodities.json").write_text(
                json.dumps({
                    "schema": "qc-canada-commodities-v1",
                    "mines": [{
                        "id": "voisey-s-bay",
                        "name": "Voisey's Bay",
                        "owners": "Vale Canada Limited",
                        "beta_id": "vale",
                        "beta_href": "beta.html?id=vale",
                        "products": ["nickel"],
                    }],
                    "commodities": [],
                }),
                encoding="utf-8",
            )
            (root / "scripts" / "canada-owner-aliases.json").write_text(
                json.dumps({
                    "schema": "qc-canada-owner-aliases-v1",
                    "aliases": [{"owner": "Vale Canada Limited", "company_id": "vale"}],
                }),
                encoding="utf-8",
            )
            errors = s.check(root)
            self.assertFalse(
                any("has production" in e or "invented" in e for e in errors),
                errors,
            )


if __name__ == "__main__":
    unittest.main()
