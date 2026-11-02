#!/usr/bin/env python3
"""Regenerating extracts keeps the PR #244 shared-word retags (fake REST, no network)."""
from __future__ import annotations

import argparse
import io
import json
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import build_on_bc_extracts as m  # noqa: E402
import claims_extract_retags as r  # noqa: E402

RULES = ROOT / "claims" / "links" / "extract-retags.json"


def feat(tid, holder, x=-80.0):
    return {"type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [[[x, 48], [x - 0.01, 48], [x - 0.01, 48.01], [x, 48]]]},
            "properties": {"TENURE_NUMBER_ID": tid, "HOLDER": holder, "OWNER_NAME": holder}}


class Rest:
    def __init__(self, features):
        self.features = features

    def __call__(self, url, params):
        if params.get("returnCountOnly"):
            return {"count": len(self.features)}
        if params.get("where") == "1=1":
            return {"features": []}
        return {"features": list(self.features)}


def args(company):
    return argparse.Namespace(company=company, cache_dir=None, refresh=False,
                              sweep_holders=False, judge_holders=False, typesafe_env=None)


class Regenerate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "claims" / "links").mkdir(parents=True)
        shutil.copy(RULES, root / "claims" / "links" / RULES.name)
        catalog = {"companies": [
            {"id": "metal-energy", "names": ["Metal Energy"], "claim_count": 0, "ontario_count": 0, "bc_count": 0},
            {"id": "canada-nickel", "names": ["Canada Nickel"], "claim_count": 0, "ontario_count": 0, "bc_count": 0},
        ]}
        (root / "claims" / "companies.json").write_text(json.dumps(catalog), encoding="utf-8")
        self.saved = (m.ROOT, m.CLAIMS, m.get_json, m.ON_MATCH, m.BC_MATCH)
        m.ROOT, m.CLAIMS = root, root / "claims"

    def tearDown(self):
        m.ROOT, m.CLAIMS, m.get_json, m.ON_MATCH, m.BC_MATCH = self.saved
        self.tmp.cleanup()

    def regen(self, on, bc, rest, company):
        m.ON_MATCH, m.BC_MATCH, m.get_json = on, bc, rest
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = m.main(args(company))
        self.assertEqual(rc, 0)
        cat = json.loads((m.CLAIMS / "companies.json").read_text(encoding="utf-8"))
        return {c["id"]: c for c in cat["companies"]}

    def roles(self, name):
        data = json.loads((m.CLAIMS / name).read_text(encoding="utf-8"))
        return {f["properties"]["holder"]: f["properties"]["role"] for f in data["features"]}

    def test_ontario_regen_keeps_shared_word_retags(self):
        rest = Rest([feat("1", "(100) Canada Nickel Company Inc."),
                     feat("2", "(100) Fulcrum Metals Canada Ltd."),
                     feat("3", "(100) Panther Metals (Canada) Ltd.")])
        rows = self.regen({"canada-nickel": ["NICKEL", "METALS"]}, {}, rest, ["canada-nickel"])
        roles = self.roles("canada-nickel-ontario.geojson")
        self.assertEqual(roles["(100) Canada Nickel Company Inc."], "focus")
        self.assertEqual(roles["(100) Fulcrum Metals Canada Ltd."], "neighbor")
        self.assertEqual(roles["(100) Panther Metals (Canada) Ltd."], "neighbor")
        self.assertEqual(rows["canada-nickel"]["ontario_count"], 1)

    def test_focus_only_pattern_on_ontario_and_bc(self):
        rest = Rest([feat("1", "METAL ENERGY CORP."), feat("2", "(100) Nexus Metals Corp."),
                     feat("3", "THOMPSON CREEK METALS COMPANY INC.")])
        rows = self.regen({"metal-energy": ["METAL"]}, {"metal-energy": ["METAL"]}, rest, ["metal-energy"])
        for name in ("metal-energy-ontario.geojson", "metal-energy-bc.geojson"):
            roles = self.roles(name)
            self.assertEqual(roles["METAL ENERGY CORP."], "focus", name)
            self.assertEqual(roles["(100) Nexus Metals Corp."], "neighbor", name)
            self.assertEqual(roles["THOMPSON CREEK METALS COMPANY INC."], "neighbor", name)
        self.assertEqual(rows["metal-energy"]["ontario_count"], 1)
        self.assertEqual(rows["metal-energy"]["bc_count"], 1)

    def test_neighbors_only_reapplies_retags(self):
        dest = m.CLAIMS / "canada-nickel-ontario.geojson"
        feats = [m.map_on(feat("1", "(100) Canada Nickel Company Inc.")),
                 m.map_on(feat("2", "(100) FULCRUM METALS NO. 2 (CANADA) LTD."))]
        dest.write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")
        m.ON_MATCH, m.get_json = {"canada-nickel": ["NICKEL"]}, Rest([])
        with redirect_stdout(io.StringIO()):
            m.neighbors_only(["canada-nickel"])
        roles = self.roles("canada-nickel-ontario.geojson")
        # Retagged titles are not focus, so the pad and focus list come only from the real holder.
        self.assertNotEqual(roles.get("(100) FULCRUM METALS NO. 2 (CANADA) LTD."), "focus")
        self.assertEqual(roles, {"(100) Canada Nickel Company Inc.": "focus"})


class Rules(unittest.TestCase):
    def test_holder_compare_ignores_case_and_spaces(self):
        rules = r.load_rules(RULES)
        fs = [{"properties": {"holder": "(100)  core critical METALS corp.", "role": "focus"}}]
        self.assertEqual(r.apply_retags("critical-elements-lithium-ontario.geojson", fs, rules), 1)
        self.assertEqual(r.apply_retags("other-ontario.geojson", fs, rules), 0)

    def test_committed_244_extracts_already_agree(self):
        # metal-energy-bc.geojson is fixed by PR #246; every other file was retagged in PR #244.
        rules = r.load_rules(RULES)
        for name in sorted(rules):
            if name == "metal-energy-bc.geojson":
                continue
            data = json.loads((ROOT / "claims" / name).read_text(encoding="utf-8"))
            self.assertEqual(r.apply_retags(name, data["features"], rules), 0, name)


class WriteFormat(unittest.TestCase):
    def test_committed_extracts_round_trip_byte_for_byte(self):
        for name in sorted(r.load_rules(RULES)):
            text = (ROOT / "claims" / name).read_text(encoding="utf-8")
            self.assertEqual(r.dump_extract(json.loads(text)), text, name)

    def test_write_restoring_a_retag_is_a_zero_line_diff(self):
        name = "metal-energy-bc.geojson"
        orig = (ROOT / "claims" / name).read_bytes()
        with tempfile.TemporaryDirectory() as td:
            claims = Path(td) / "claims"
            (claims / "links").mkdir(parents=True)
            shutil.copy(RULES, claims / "links" / RULES.name)
            data = json.loads(orig.decode("utf-8"))
            flipped = next(f for f in data["features"] if f["properties"].get("role") == "neighbor")
            flipped["properties"]["role"] = "focus"
            (claims / name).write_text(r.dump_extract(data), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                self.assertEqual(r.main(["--check", "--claims-dir", str(claims)]), 1)
                self.assertEqual(r.main(["--write", "--claims-dir", str(claims)]), 0)
            self.assertEqual((claims / name).read_bytes(), orig)

    def test_write_keeps_non_ascii_unescaped(self):
        self.assertEqual(r.dump_extract({"holder": "Société Minière"}), '{"holder": "Société Minière"}')


if __name__ == "__main__":
    unittest.main()
