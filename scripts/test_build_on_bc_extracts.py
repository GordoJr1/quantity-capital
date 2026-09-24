#!/usr/bin/env python3
"""build_on_bc_extracts.py safety rules — fake REST, no network."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import build_on_bc_extracts as m  # noqa: E402

FEATURE = {
    "type": "Feature",
    "geometry": {"type": "Polygon", "coordinates": [[[-80, 48], [-80.01, 48], [-80.01, 48.01], [-80, 48]]]},
    "properties": {"TENURE_NUMBER_ID": "123", "HOLDER": "(100) IAMGOLD CORPORATION"},
}


def args(company):
    return argparse.Namespace(company=company, cache_dir=None, refresh=False,
                              sweep_holders=False, judge_holders=False, typesafe_env=None)


class FakeRest:
    def __init__(self, features=None, count=0, error=False):
        self.features = features or []
        self.count = count
        self.error = error
        self.calls = []

    def __call__(self, url, params):
        self.calls.append(params)
        if self.error:
            raise m.ArcGISError("ArcGIS error code=400")
        if params.get("returnCountOnly"):
            return {"count": self.count}
        if params.get("where") == "1=1":
            return {"features": []}
        return {"features": list(self.features)}


class Extracts(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        (root / "claims").mkdir()
        self.saved = (m.ROOT, m.CLAIMS, m.get_json)
        m.ROOT, m.CLAIMS = root, root / "claims"
        self.extract = root / "claims" / "iamgold-ontario.geojson"
        self.extract.write_text('{"type":"FeatureCollection","features":[{"old":1}]}', encoding="utf-8")
        catalog = {"companies": [
            {"id": "iamgold", "names": ["IAMGOLD"], "claim_count": 10, "quebec_count": 5,
             "ontario_count": 5, "ontario_extract": "claims/iamgold-ontario.geojson", "bc_count": 0},
            {"id": "someone-else", "names": ["X"], "claim_count": 7, "quebec_count": 0,
             "ontario_count": 7, "ontario_extract": "claims/someone-else-ontario.geojson", "bc_count": 0},
        ]}
        (root / "claims" / "companies.json").write_text(json.dumps(catalog), encoding="utf-8")

    def tearDown(self):
        m.ROOT, m.CLAIMS, m.get_json = self.saved
        self.tmp.cleanup()

    def run_main(self, fake, company=("iamgold",)):
        m.get_json = fake
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            rc = m.main(args(list(company)))
        catalog = json.loads((m.CLAIMS / "companies.json").read_text(encoding="utf-8"))
        return rc, {c["id"]: c for c in catalog["companies"]}

    def test_error_keeps_extract_and_counts(self):
        rc, rows = self.run_main(FakeRest(error=True))
        self.assertEqual(rc, 1)
        self.assertTrue(self.extract.exists())
        self.assertEqual(rows["iamgold"]["ontario_count"], 5)
        self.assertEqual(rows["iamgold"]["ontario_extract"], "claims/iamgold-ontario.geojson")

    def test_zero_features_but_live_count_keeps_extract(self):
        rc, rows = self.run_main(FakeRest(features=[], count=42))
        self.assertEqual(rc, 1)
        self.assertTrue(self.extract.exists())
        self.assertEqual(rows["iamgold"]["ontario_count"], 5)

    def test_clean_zero_count_removes_extract(self):
        rc, rows = self.run_main(FakeRest(features=[], count=0))
        self.assertEqual(rc, 0)
        self.assertFalse(self.extract.exists())
        self.assertEqual(rows["iamgold"]["ontario_count"], 0)
        self.assertIsNone(rows["iamgold"]["ontario_extract"])
        self.assertEqual(rows["iamgold"]["claim_count"], 5)

    def test_success_writes_extract_and_leaves_unmatched_rows(self):
        rc, rows = self.run_main(FakeRest(features=[FEATURE]))
        self.assertEqual(rc, 0)
        data = json.loads(self.extract.read_text(encoding="utf-8"))
        self.assertEqual(len(data["features"]), 1)
        self.assertEqual(rows["iamgold"]["ontario_count"], 1)
        self.assertEqual(rows["someone-else"]["ontario_count"], 7)
        self.assertEqual(rows["someone-else"]["ontario_extract"], "claims/someone-else-ontario.geojson")

    def test_paging_is_ordered(self):
        fake = FakeRest(features=[FEATURE])
        self.run_main(fake)
        self.assertTrue(all(p.get("orderByFields") == "OBJECTID ASC" for p in fake.calls if "resultOffset" in p))


class Cache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self.tmp.name)
        self.saved = (m.get_json, m.CACHE_MAX_AGE_S)

    def tearDown(self):
        m.get_json, m.CACHE_MAX_AGE_S = self.saved
        self.tmp.cleanup()

    def test_error_body_never_cached(self):
        def boom(url, params):
            raise m.ArcGISError("ArcGIS error code=400")

        m.get_json = boom
        with self.assertRaises(m.ArcGISError):
            m.cached_get_json("u", {"a": 1}, self.cache, False)
        self.assertFalse(any(self.cache.rglob("*.json")))

    def test_cached_error_body_is_refetched(self):
        key = m.page_cache_key("u", {"a": 1})
        slot = self.cache / "pages" / (key + ".json")
        slot.parent.mkdir(parents=True)
        slot.write_text('{"error": {"code": 400}}', encoding="utf-8")
        m.get_json = lambda url, params: {"features": [1]}
        self.assertEqual(m.cached_get_json("u", {"a": 1}, self.cache, False), {"features": [1]})

    def test_stale_cache_expires(self):
        m.get_json = lambda url, params: {"features": ["fresh"]}
        m.cached_get_json("u", {"a": 1}, self.cache, False)
        slot = next(self.cache.rglob("*.json"))
        slot.write_text('{"features": ["old"]}', encoding="utf-8")
        self.assertEqual(m.cached_get_json("u", {"a": 1}, self.cache, False)["features"], ["old"])
        old = time.time() - 3 * 24 * 3600
        os.utime(slot, (old, old))
        self.assertEqual(m.cached_get_json("u", {"a": 1}, self.cache, False)["features"], ["fresh"])


class ValeHolders(unittest.TestCase):
    def test_approved_holders_drive_where(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            self.assertEqual(m.ontario_where("vale", ["VALE CANADA LIMITED"], cache),
                             m.like_where("HOLDER", ["VALE CANADA LIMITED"]))
            (cache / m.VALE_JUDGMENTS).write_text(json.dumps({
                "(100) VALE CANADA LIMITED": {"vale": True},
                "(50) GLENCORE, (50) VALE CANADA LIMITED": {"vale": True},
                "(100) VALEMOUNT O'BRIEN LTD": {"vale": False},
            }), encoding="utf-8")
            where = m.ontario_where("vale", ["VALE CANADA LIMITED"], cache)
            self.assertTrue(where.startswith("HOLDER IN ("))
            self.assertIn("'(100) VALE CANADA LIMITED'", where)
            self.assertNotIn("VALEMOUNT", where)
            self.assertEqual(m.in_where("H", ["O'B"]), "H IN ('O''B')")


if __name__ == "__main__":
    unittest.main()
