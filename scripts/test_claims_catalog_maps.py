#!/usr/bin/env python3
"""A rebuilt companies.json (stale qc.sqlite counts) gets the deny map and extract retags back."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import claims_catalog_maps as m  # noqa: E402
import merge_claims_insider_universe as merge  # noqa: E402

SQ = [[[-80, 48], [-80.01, 48], [-80.01, 48.01], [-80, 48]]]


def feat(holder, role="focus", x=-80.0):
    ring = [[[x, 48], [x - 0.01, 48], [x - 0.01, 48.01], [x, 48]]]
    return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": ring},
            "properties": {"holder": holder, "role": role}}


class Sample(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        claims = self.root / "claims"
        (claims / "links").mkdir(parents=True)
        (claims / "links" / "extract-retags.json").write_text(json.dumps({"files": {
            "metal-energy.geojson": {"focus_only_holder_pattern": "\\bmetal energy\\b"},
            "metal-energy-bc.geojson": {"focus_only_holder_pattern": "\\bmetal energy\\b"},
            "solstice-minerals-ontario.geojson": {"focus_only_holder_pattern": "\\bsolstice minerals\\b"},
            "canada-nickel-ontario.geojson": {"neighbor_holders": ["(100) Fulcrum Metals Canada Ltd."]},
        }}), encoding="utf-8")

        def fc(name, feats):
            (claims / name).write_text(json.dumps({"type": "FeatureCollection", "features": feats}), encoding="utf-8")

        # Extracts as a regeneration would leave them: wrong holders still focus.
        fc("metal-energy.geojson", [feat("GO METALS CORP."), feat("Nexus Metals Corp.")])
        fc("metal-energy-bc.geojson", [feat("METAL ENERGY CORP.", x=-121.0), feat("THOMPSON CREEK METALS", x=-122.0)])
        fc("solstice-minerals-ontario.geojson", [feat("(100) Solstice Gold Corp.")])
        fc("canada-nickel-ontario.geojson", [feat("(100) CANADA NICKEL COMPANY INC."),
                                              feat("(100) Fulcrum Metals Canada Ltd."),
                                              feat("(100) Someone", role="neighbor")])
        fc("iamgold-ontario.geojson", [feat("(100) IAMGOLD CORPORATION"), feat("(100) X", role="neighbor")])
        # Catalog as a qc.sqlite rebuild writes it: stale counts and a denied id.
        self.catalog = {"companies": [
            {"id": "metal-energy", "claim_count": 4, "extract": "claims/metal-energy.geojson", "quebec_count": 2,
             "bc_extract": "claims/metal-energy-bc.geojson", "bc_count": 2, "ontario_count": 0,
             "quebec_bbox": [0, 0, 1, 1], "bc_bbox": [0, 0, 1, 1]},
            {"id": "solstice-minerals", "claim_count": 1, "ontario_extract": "claims/solstice-minerals-ontario.geojson",
             "ontario_count": 1, "ontario_bbox": [0, 0, 1, 1], "quebec_count": 0, "bc_count": 0, "bbox": [0, 0, 1, 1]},
            {"id": "canada-nickel", "claim_count": 3, "ontario_extract": "claims/canada-nickel-ontario.geojson",
             "ontario_count": 3, "quebec_count": 0, "bc_count": 0},
            {"id": "iamgold", "claim_count": 77, "ontario_extract": "claims/iamgold-ontario.geojson", "ontario_count": 77},
            {"id": "kinross-kgc-v", "claim_count": 5},
            {"id": "kestrel-gold-inc", "claim_count": 5},
            {"id": "barrick-b-v", "claim_count": 1},
        ]}
        (claims / "companies.json").write_text(json.dumps(self.catalog), encoding="utf-8")
        self.denied = {"kinross-kgc-v": "kestrel-gold-inc", "barrick-b-v": "bcm-resources-corporation"}

    def tearDown(self):
        self.tmp.cleanup()

    def rows(self, catalog):
        return {r["id"]: r for r in catalog["companies"]}

    def test_rebuild_cannot_bring_back_old_counts_or_ids(self):
        changes = m.apply_catalog_maps(self.catalog, self.root, denied=self.denied)
        rows = self.rows(self.catalog)
        me = rows["metal-energy"]
        self.assertEqual((me["quebec_count"], me["bc_count"], me["claim_count"]), (0, 1, 1))
        self.assertIsNone(me["quebec_bbox"])
        self.assertEqual(me["bc_bbox"], [-121.01, 48, -121.0, 48.01])
        so = rows["solstice-minerals"]
        self.assertEqual((so["ontario_count"], so["claim_count"], so["ontario_bbox"], so["bbox"]), (0, 0, None, None))
        self.assertEqual(rows["canada-nickel"]["ontario_count"], 1)
        self.assertEqual(rows["canada-nickel"]["claim_count"], 1)
        self.assertEqual(rows["iamgold"]["ontario_count"], 77)  # no rule: untouched
        self.assertNotIn("kinross-kgc-v", rows)
        self.assertNotIn("barrick-b-v", rows)
        self.assertIn("bcm-resources-corporation", rows)  # renamed (no existing row)
        self.assertEqual(rows["kestrel-gold-inc"]["claim_count"], 5)  # kept, duplicate dropped
        self.assertTrue(changes)
        self.assertEqual(m.apply_catalog_maps(self.catalog, self.root, denied=self.denied), [])  # idempotent

    def test_merge_step_writes_and_check_only_warns(self):
        saved = m.denied_ids
        m.denied_ids = lambda: self.denied
        try:
            warn = merge.sync_catalog_maps(self.root, write=False)
            self.assertTrue(warn and all(w.startswith("warning: catalog maps needed") for w in warn))
            on_disk = json.loads((self.root / "claims" / "companies.json").read_text(encoding="utf-8"))
            self.assertEqual(self.rows(on_disk)["metal-energy"]["claim_count"], 4)  # check mode writes nothing
            self.assertTrue(merge.sync_catalog_maps(self.root, write=True))
            on_disk = json.loads((self.root / "claims" / "companies.json").read_text(encoding="utf-8"))
            self.assertEqual(self.rows(on_disk)["metal-energy"]["claim_count"], 1)
            self.assertNotIn("kinross-kgc-v", self.rows(on_disk))
            self.assertEqual(merge.sync_catalog_maps(self.root, write=True), [])
        finally:
            m.denied_ids = saved

    def test_real_deny_map_is_used_when_present(self):
        import build_canada_owner_beta_shells as shells
        self.assertEqual(m.denied_ids(), dict(getattr(shells, "DENIED_BETA_IDS", {}) or {}))


if __name__ == "__main__":
    unittest.main()
