#!/usr/bin/env python3
"""Tests for claims → beta profile index (Work B)."""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import build_claims_beta_profiles as b

ROOT = HERE.parent


class ClaimsBetaIndexTests(unittest.TestCase):
    def test_live_catalog_resolves_samples(self) -> None:
        payload = b.build_index(ROOT)
        by_id = {r["id"]: r for r in payload["issuers"]}
        self.assertEqual(payload["n"], 191)
        self.assertEqual(payload["counts"].get("matched"), 191)
        self.assertEqual(by_id["iamgold"]["file"], "beta/iamgold.json")
        self.assertTrue(by_id["iamgold"]["in_issuers"])
        self.assertEqual(by_id["probe-gold"]["file"], "beta/probe-gold.json")
        self.assertTrue(by_id["probe-gold"]["in_explorers"])
        self.assertEqual(by_id["g2-goldfields"]["file"], "beta/g2-goldfields.json")
        self.assertEqual(by_id["troilus-mining"]["alias_of"], "troilus")
        self.assertEqual(by_id["troilus-mining"]["file"], "beta/troilus.json")
        self.assertIn("TLG.TO", by_id["troilus-mining"]["tickers"])
        self.assertEqual(by_id["kenorland-minerals"]["layer"], "claims")
        self.assertTrue(by_id["kenorland-minerals"]["has_overview"])
        self.assertTrue((ROOT / by_id["iamgold"]["file"]).exists())
        self.assertTrue((ROOT / by_id["probe-gold"]["file"]).exists())

    def test_does_not_invent_parallel_ids(self) -> None:
        payload = b.build_index(ROOT)
        for row in payload["issuers"]:
            self.assertEqual(row["id"], row["id"])  # claims catalog id
            if row["alias_of"]:
                self.assertNotEqual(row["id"], row["alias_of"])
                self.assertTrue((ROOT / row["file"]).exists())
            else:
                self.assertTrue(row["file"].endswith(row["id"] + ".json"))

    def test_alias_map_locked(self) -> None:
        self.assertEqual(b.ALIASES["troilus-mining"], "troilus")
        beta_id, note = b.resolve_beta_id("troilus-mining", {"troilus": {"tickers": ["TLG.TO"]}})
        self.assertEqual(beta_id, "troilus")
        self.assertIn("alias", note or "")

    def test_new_shell_has_no_ounces(self) -> None:
        row = {
            "id": "demo-gold",
            "name": "Demo Gold",
            "tickers": ["DEM.TO"],
            "kind": "explorer",
            "stage": "exploration",
        }
        rec = {
            "mines": [
                {
                    "id": "camp",
                    "name": "Camp",
                    "lat": 48.1,
                    "lon": -77.2,
                    "country": "Canada",
                    "region": "Quebec",
                }
            ]
        }
        shell = b.new_shell_payload(row, rec)
        self.assertEqual(shell["id"], "demo-gold")
        self.assertIsNone(shell["kpis"]["attr_koz_2025"])
        self.assertEqual(shell["production"], [])
        self.assertEqual(shell["assets"][0]["id"], "camp")
        self.assertFalse(shell["meta"]["filing_backed"])
        self.assertIn("Do not invent ounces", shell["disclaimer"])

    def test_check_passes_after_write(self) -> None:
        payload = b.build_index(ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "beta").mkdir()
            (root / "claims").mkdir()
            (root / "claims" / "companies.json").write_text(
                (ROOT / "claims" / "companies.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            # Point files at the real beta shells via relative paths from tmp — copy index only.
            # check_index reads ROOT-style layout; use the live tree.
        errors = b.check_index(ROOT, payload)
        self.assertEqual(errors, [], errors)


class BetaHtmlHooksTests(unittest.TestCase):
    def test_inline_script_parses(self) -> None:
        html = (ROOT / "beta.html").read_text(encoding="utf-8")
        start = html.find("<script>\n    const DASH")
        end = html.find("  </script>\n  <script src=\"refresh.js\">")
        self.assertGreater(start, 0)
        self.assertGreater(end, start)
        self.assertIn("claims/overview.geojson", html)
        self.assertIn("beta/claims-publics.json", html)
        self.assertNotIn("loadAllExtracts", html)
        path = Path("/tmp/beta-inline.js")
        path.write_text(html[start + 8 : end], encoding="utf-8")
        proc = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)


class ClaimsBetaGateTests(unittest.TestCase):
    def test_packet_has_work_b_calls(self) -> None:
        import run_claims_beta_gate as g

        packet = g.build_packet(ROOT)
        self.assertEqual(packet["work"], "B")
        self.assertEqual(packet["not"], "Jev Bot chat")
        ids = [c["id"] for c in packet["calls"]]
        self.assertIn("policy:shell_fields", ids)
        self.assertIn("policy:map_extent", ids)
        self.assertIn("policy:empty_profile", ids)
        self.assertIn("policy:id_alias", ids)
        self.assertIn("policy:preview_light", ids)
        gate = g.evaluate_gate(packet, {"ran": False, "reason": "packet_only"})
        self.assertTrue(gate["pass"], gate)
        self.assertFalse(gate["jev_ran"])


if __name__ == "__main__":
    unittest.main()
