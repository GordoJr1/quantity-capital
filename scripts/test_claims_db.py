#!/usr/bin/env python3
"""Unit tests for the multi-province claims builder. No network."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import claims_db as db  # noqa: E402


class HolderRewriteTests(unittest.TestCase):
    def test_ontario_percent_stays(self):
        raw = "(100) GREAT BEAR RESOURCES LTD."
        self.assertEqual(db.registry_holder_to_ontario(raw), raw)

    def test_trailing_percent(self):
        self.assertEqual(
            db.registry_holder_to_ontario("B2GOLD BACK RIVER CORP. (100%)"),
            "(100) B2GOLD BACK RIVER CORP.",
        )

    def test_slash_joint_venture(self):
        self.assertEqual(
            db.registry_holder_to_ontario("Labrador Uranium Inc. (66%)/Anthem Resources Inc. (34%)"),
            "(66) Labrador Uranium Inc., (34) Anthem Resources Inc.",
        )

    def test_yukon_dash_percent(self):
        self.assertEqual(
            db.registry_holder_to_ontario("Senoa Gold Corp - 100%"),
            "(100) Senoa Gold Corp",
        )


class TippecanoeTests(unittest.TestCase):
    def test_path_runner_calls_tippecanoe_directly(self):
        argv = db.tippecanoe_command(Path("in.jsonl"), Path("out.pmtiles"), "path")
        self.assertEqual(argv[0], "tippecanoe")
        self.assertEqual(argv[1:3], ["-o", "out.pmtiles"])
        self.assertEqual(argv[-1], "in.jsonl")

    def test_wsl_runner_uses_wslpath(self):
        def translate(path: Path) -> str:
            return "/mnt/c/claims/" + path.name

        argv = db.tippecanoe_command(
            Path("in.jsonl"), Path("out.pmtiles"), "wsl", translate=translate,
        )
        self.assertEqual(argv[:2], ["wsl", "tippecanoe"])
        self.assertEqual(argv[3], "/mnt/c/claims/out.pmtiles")
        self.assertEqual(argv[-1], "/mnt/c/claims/in.jsonl")

    def test_path_wins_over_wsl(self):
        def which(name: str):
            return "C:/tools/tippecanoe.exe" if name == "tippecanoe" else None

        self.assertEqual(db.find_tippecanoe(which=which, run=lambda *a, **k: (_ for _ in ()).throw(AssertionError("run"))), "path")

    def test_missing_both_explains_wsl(self):
        with self.assertRaises(SystemExit) as caught:
            db.find_tippecanoe(which=lambda name: None, run=lambda *a, **k: None)
        message = str(caught.exception)
        self.assertIn("tippecanoe was not found on PATH", message)
        self.assertIn("wsl tippecanoe", message)
        self.assertIn("go-pmtiles", message)


class LimitTests(unittest.TestCase):
    def test_file_limit_is_github_mib(self):
        self.assertEqual(db.GITHUB_FILE_LIMIT, 100 * 1024 * 1024)

    def test_public_tiles_use_png_suffix(self):
        self.assertIn("--minimum-zoom=2", db.TIPPECANOE_ARGS)
        self.assertEqual(db.TILE_PUBLIC_SUFFIX, ".pmtiles.png")
        text = (Path(__file__).resolve().parents[1] / "claims" / "tiles" / "index.json").read_text(encoding="utf-8")
        self.assertIn("claims/tiles/yt.pmtiles.png", text)
        self.assertNotIn('claims/tiles/yt.pmtiles"', text)

    def test_known_provinces(self):
        self.assertEqual(set(db.PROVINCES), {"ontario", "yukon", "newfoundland", "nunavut"})
        self.assertIn("OWNER_NAME", db.PROVINCES["yukon"]["fields"])
        self.assertIn("CLIENT_NAME", db.PROVINCES["newfoundland"]["fields"])
        self.assertIn("OWNERS", db.PROVINCES["nunavut"]["fields"])


if __name__ == "__main__":
    unittest.main()
