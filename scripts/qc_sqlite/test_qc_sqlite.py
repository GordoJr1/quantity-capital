#!/usr/bin/env python3
"""qc_sqlite regression tests — temp databases, no network, no TypeSafe."""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import paper  # noqa: E402
import rebuild  # noqa: E402
from paths import SCHEMA_SQL  # noqa: E402


def fresh(path: Path) -> sqlite3.Connection:
    con = rebuild.connect(path)
    con.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
    return con


def seed_province(con: sqlite3.Connection) -> None:
    con.execute("INSERT INTO companies(company_id, name) VALUES ('_province_bc', 'BC all holders')")
    con.execute("INSERT INTO companies(company_id, name) VALUES ('teck', 'Teck')")
    con.execute("INSERT INTO companies(company_id, name) VALUES ('gone-issuer', 'Gone')")
    con.execute(
        "INSERT INTO claim_packs(pack_id, company_id, role, holder, holder_company_id, claim_count) "
        "VALUES ('province:bc', '_province_bc', 'focus', 'BC', '_province_bc', 2)"
    )
    for pk, holder_cid in (("province:bc:focus:1", "teck"), ("province:bc:focus:2", "gone-issuer")):
        con.execute(
            "INSERT INTO claim_titles(title_pk, pack_id, company_id, holder_company_id, jurisdiction, claim_id, as_of) "
            "VALUES (?, 'province:bc', '_province_bc', ?, 'British Columbia', ?, '2026-09-01')",
            (pk, holder_cid, pk[-1]),
        )
        con.execute(
            "INSERT INTO claim_title_parties(title_pk, pack_id, holder_name, holder_company_id, source) "
            "VALUES (?, 'province:bc', 'X', ?, 'province_seed')",
            (pk, holder_cid),
        )
    con.execute("INSERT INTO meta(key, value) VALUES ('province_bc_offset_A', '4000')")
    con.commit()


class CarryForward(unittest.TestCase):
    def test_province_rows_survive_full_rebuild(self):
        with tempfile.TemporaryDirectory() as d:
            old_path, new_path = Path(d) / "qc.sqlite", Path(d) / "qc.sqlite.tmp"
            old = fresh(old_path)
            seed_province(old)
            old.close()
            new = fresh(new_path)
            new.execute("INSERT INTO companies(company_id, name) VALUES ('teck', 'Teck Resources')")
            new.commit()
            with redirect_stdout(io.StringIO()):
                counts = rebuild.carry_forward_side_tables(new, old_path)
            self.assertEqual(counts["claim_titles"], 2)
            self.assertEqual(counts["claim_title_parties"], 2)
            self.assertEqual(new.execute("SELECT value FROM meta WHERE key='province_bc_offset_A'").fetchone()[0], "4000")
            self.assertEqual(new.execute("SELECT name FROM companies WHERE company_id='teck'").fetchone()[0], "Teck Resources")
            self.assertEqual(new.execute("PRAGMA foreign_key_check").fetchall(), [])
            new.close()

    def test_missing_old_db_is_noop(self):
        with tempfile.TemporaryDirectory() as d:
            new = fresh(Path(d) / "new.sqlite")
            self.assertEqual(rebuild.carry_forward_side_tables(new, Path(d) / "absent.sqlite"), {})
            new.close()


class Paper(unittest.TestCase):
    def test_reads_board_and_never_writes_json(self):
        board = {
            "generated": "2026-09-24T00:00:00+00:00",
            "filers": [{"id": "a", "name": "A", "chamber": "House", "n": 1, "skip": 0, "avg": 0.1, "med": 0.1, "win": 1,
                        "legs": [{"t": "NVDA", "filed": "2026-01-02", "in": 1.0, "out": 1.1, "outD": "2026-09-01", "ret": 0.1}]}],
            "tickers": [{"t": "NVDA", "name": "Nvidia", "n": 1, "people": 1, "avg": 0.1, "med": 0.1, "win": 1}],
        }
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "backtest.json"
            text = json.dumps(board)
            path.write_text(text, encoding="utf-8")
            con = sqlite3.connect(":memory:")
            con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)")
            with redirect_stdout(io.StringIO()):
                stats = paper.compute_paper(con, skip_jev=True, path=path)
            self.assertFalse(stats["ran"])
            self.assertEqual(path.read_text(encoding="utf-8"), text)
            self.assertEqual(con.execute("SELECT filer_id, ticker, entry_date FROM paper_legs").fetchall(),
                             [("a", "NVDA", "2026-01-02")])
            self.assertEqual(sorted(p.name for p in Path(d).iterdir()), ["backtest.json"])

    def test_paper_has_no_json_writer(self):
        src = (HERE / "paper.py").read_text(encoding="utf-8")
        self.assertNotIn("write_text", src)
        self.assertNotIn("_paper_lib", src)


class JevCache(unittest.TestCase):
    def setUp(self):
        import jev

        self.jev = jev
        self.tmp = tempfile.TemporaryDirectory()
        self.saved = (jev.JEV_CACHE, jev._cache, jev._dirty)
        jev.JEV_CACHE = Path(self.tmp.name) / "jev.json"
        jev._cache = None

    def tearDown(self):
        self.jev.JEV_CACHE, self.jev._cache, self.jev._dirty = self.saved
        self.tmp.cleanup()

    def test_question_text_changes_key(self):
        a = self.jev._digest({"x": 1}, "lbl", {"q": {"type": "noul", "instructions": "old"}})
        b = self.jev._digest({"x": 1}, "lbl", {"q": {"type": "noul", "instructions": "new"}})
        self.assertNotEqual(a, b)

    def test_corrupt_cache_starts_empty(self):
        self.jev.JEV_CACHE.write_text("{not json", encoding="utf-8")
        with redirect_stdout(io.StringIO()), __import__("contextlib").redirect_stderr(io.StringIO()):
            self.assertEqual(self.jev._cache_load(), {})
        self.assertTrue(self.jev.JEV_CACHE.with_suffix(".corrupt.json").exists())


class AnalysisErrors(unittest.TestCase):
    def test_errored_rows_keep_rules_verdict(self):
        import analysis
        import jev

        book = [
            {"code": "AAA", "action": "watch", "score": 3.0, "flags": []},
            {"code": "BBB", "action": "buy-dip", "score": 2.0, "flags": []},
        ]
        saved = (jev.key_present, analysis.apply_jev_row, jev.store_decision)

        def fake_apply(row):
            if row["code"] == "BBB":
                raise RuntimeError("boom")
            return {"code": row["code"], "packed": {"model": "m", "answers": {"ship": {"choice": "ship"}}}}

        jev.key_present = lambda: True
        analysis.apply_jev_row = fake_apply
        jev.store_decision = lambda *a, **k: None
        try:
            with redirect_stdout(io.StringIO()):
                play, avoid, stats = analysis.gate_with_jev(None, book, skip_jev=False)
        finally:
            jev.key_present, analysis.apply_jev_row, jev.store_decision = saved
        self.assertEqual(stats["errors"], 1)
        self.assertEqual(sorted(r["code"] for r in play), ["AAA", "BBB"])
        self.assertTrue(next(r for r in play if r["code"] == "BBB")["jev_error"])


if __name__ == "__main__":
    unittest.main()
