#!/usr/bin/env python3
"""Synthetic prices for the repeatable since-filing vs SPY scorecard. No network."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import repeatable_scorecard as rs  # noqa: E402


class ScoreWindow(unittest.TestCase):
    def test_excess_math_and_strictly_after_filing(self):
        closes = [["2026-01-02", 10.0], ["2026-01-05", 11.0], ["2026-01-06", 12.0]]
        spy = {"2026-01-05": 100.0, "2026-01-06": 110.0}
        row = rs.score_window(closes, spy, "2026-01-02", max_lag=10)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["entry_date"], "2026-01-05")
        self.assertEqual(row["entry_px"], 11.0)
        self.assertEqual(row["exit_date"], "2026-01-06")
        self.assertEqual(row["exit_px"], 12.0)
        self.assertAlmostEqual(row["ret"], rs.round_ret(12.0 / 11.0 - 1.0))
        self.assertAlmostEqual(row["spy_ret"], 0.1)
        self.assertAlmostEqual(row["excess"], rs.round_ret((12.0 / 11.0 - 1.0) - 0.1))

    def test_missing_price_is_none(self):
        spy = {"2026-01-05": 100.0, "2026-01-06": 110.0}
        self.assertIsNone(rs.score_window(None, spy, "2026-01-02"))
        self.assertIsNone(rs.score_window([], spy, "2026-01-02"))
        # Only the filing-day close is on file: that bar is not an entry.
        self.assertIsNone(rs.score_window([["2026-01-02", 10.0]], spy, "2026-01-02"))

    def test_missing_spy_on_the_same_dates_is_none(self):
        closes = [["2026-01-05", 11.0], ["2026-01-06", 12.0]]
        self.assertIsNone(rs.score_window(closes, {"2026-01-05": 100.0}, "2026-01-02"))
        self.assertIsNone(rs.score_window(closes, {"2026-01-06": 110.0}, "2026-01-02"))

    def test_entry_beyond_lag_cap_is_none(self):
        closes = [["2026-02-01", 11.0], ["2026-02-02", 12.0]]
        spy = {"2026-02-01": 100.0, "2026-02-02": 101.0}
        self.assertIsNone(rs.score_window(closes, spy, "2026-01-02", max_lag=10))


class Summary(unittest.TestCase):
    def test_average_median_hit(self):
        summary = rs.summarize([0.10, -0.05, 0.02], missing=4)
        self.assertEqual(summary["n"], 3)
        self.assertEqual(summary["beat"], 2)
        self.assertEqual(summary["missing"], 4)
        self.assertAlmostEqual(summary["avg"], rs.round_ret((0.10 - 0.05 + 0.02) / 3))
        self.assertAlmostEqual(summary["median"], 0.02)
        self.assertAlmostEqual(summary["hit"], rs.round_ret(2 / 3))

    def test_empty(self):
        summary = rs.summarize([], missing=2)
        self.assertEqual(summary["n"], 0)
        self.assertIsNone(summary["avg"])
        self.assertIsNone(summary["median"])
        self.assertIsNone(summary["hit"])
        self.assertEqual(summary["beat"], 0)
        self.assertEqual(summary["missing"], 2)


class LatestBuy(unittest.TestCase):
    def test_reuses_board_open_market_rule(self):
        is_open = rs.load_builder().is_open_market_buy
        trades = [
            {"id": "1", "filer_id": "a", "ticker": "aaa", "side": "purchase", "code": "P", "filed_date": "2026-01-02"},
            {"id": "2", "filer_id": "a", "ticker": "BBB", "side": "purchase", "code": "P", "filed_date": "2026-03-01"},
            {"id": "3", "filer_id": "a", "ticker": "CCC", "side": "purchase", "code": "P", "filed_date": "2026-04-01"},
            {"id": "4", "filer_id": "a", "ticker": "DDD", "side": "purchase", "code": "A", "filed_date": "2026-05-01"},
            {"id": "5", "filer_id": "a", "ticker": "EEE", "side": "sale", "code": "S", "filed_date": "2026-06-01"},
            {"id": "6", "filer_id": "b", "ticker": "FFF", "side": "purchase", "code": "P", "origin": "sedi", "nature": "10", "filed_date": "2026-02-02"},
            {"id": "7", "filer_id": "a", "ticker": "MMM", "side": "purchase", "code": "P", "filed_date": "2026-03-01"},
            {"id": "8", "filer_id": "a", "ticker": "ZZZ", "side": "purchase", "code": "P", "filed_date": "2026-03-01"},
            {"id": "9", "filer_id": "c", "ticker": "OLD", "side": "purchase", "code": "P", "filed_date": "2024-01-01"},
        ]
        form4 = {"3": {"plan": True}}
        latest = rs.latest_open_market_buys(trades, form4, {"a", "b", "c"}, "2025-04-01", is_open)
        # Same filed date: higher ticker wins, matching pick_firm. Plan, award, and sale are out.
        self.assertEqual(latest["a"], {"filer_id": "a", "ticker": "ZZZ", "filed": "2026-03-01"})
        self.assertEqual(latest["b"]["ticker"], "FFF")
        self.assertNotIn("c", latest)


if __name__ == "__main__":
    unittest.main()
