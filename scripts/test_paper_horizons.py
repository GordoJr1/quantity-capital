#!/usr/bin/env python3
"""Paper horizons, SPY excess, unpriced filers, and the heat buy-dip line. No JSON writes."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SQLITE = ROOT / "scripts" / "qc_sqlite"
if str(SQLITE) not in sys.path:
    sys.path.insert(0, str(SQLITE))

from analysis import buy_dip_why  # noqa: E402


def load_backtest():
    path = ROOT / "build-backtest.py"
    spec = importlib.util.spec_from_file_location("qc_build_backtest", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bb = load_backtest()


def iso(day: datetime) -> str:
    return day.strftime("%Y-%m-%d")


def close_on(closes: list, day: str) -> float:
    for d, px in closes:
        if d == day:
            return px
    raise AssertionError(day)


class PaperHorizonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.filed_dt = datetime(2024, 1, 2)
        self.entry_dt = datetime(2024, 1, 3)
        self.filed = iso(self.filed_dt)
        self.entry = iso(self.entry_dt)
        self.t90 = self.entry_dt + timedelta(days=90)
        self.t180 = self.entry_dt + timedelta(days=180)
        self.t365 = self.entry_dt + timedelta(days=365)
        # filed+90 is one day before entry+90, and it is a bar. The 90-day hold
        # must not exit there. entry+90 itself is missing; the next day is the bar.
        self.filed90 = iso(self.filed_dt + timedelta(days=90))
        self.after90 = iso(self.entry_dt + timedelta(days=91))
        self.last = "2025-02-01"
        self.stock = [
            [self.filed, 90.0],
            [self.entry, 100.0],
            [self.filed90, 110.0],
            [self.after90, 120.0],
            [iso(self.t180), 150.0],
            [iso(self.t365), 180.0],
            [self.last, 200.0],
        ]
        self.spy = [
            [self.entry, 50.0],
            [self.after90, 55.0],
            [iso(self.t180), 60.0],
            [iso(self.t365), 40.0],
            [self.last, 70.0],
        ]

    def test_entry_is_the_day_after_the_filing_and_horizons_follow_it(self) -> None:
        self.assertEqual(iso(self.t90), "2024-04-02")
        self.assertEqual(iso(self.t180), "2024-07-01")
        self.assertEqual(iso(self.t365), "2025-01-02")
        self.assertEqual(self.filed90, "2024-04-01")
        bar = bb.first_after(self.stock, self.filed)
        self.assertIsNotNone(bar)
        self.assertEqual(bar[0], self.entry)
        self.assertNotEqual(bb.first_on_or_after(self.stock, self.filed)[0], self.entry)

        leg = bb.price_leg(self.stock, bar[0], bar[1], self.spy)
        self.assertEqual(leg["inD"], self.entry)
        self.assertEqual(leg["hz"]["90"]["outD"], self.after90)
        self.assertNotEqual(leg["hz"]["90"]["outD"], self.filed90)
        self.assertNotEqual(leg["hz"]["90"]["outD"], iso(self.t90))
        self.assertEqual(leg["hz"]["180"]["outD"], iso(self.t180))
        self.assertEqual(leg["hz"]["365"]["outD"], iso(self.t365))
        self.assertEqual(leg["outD"], self.last)

        short = [row for row in self.stock if row[0] <= iso(self.t180)]
        short_leg = bb.price_leg(short, self.entry, 100.0, self.spy)
        self.assertIn("90", short_leg["hz"])
        self.assertIn("180", short_leg["hz"])
        self.assertNotIn("365", short_leg["hz"])
        self.assertEqual(short_leg["outD"], iso(self.t180))

    def test_spy_excess_is_stock_minus_spy_on_the_same_dates(self) -> None:
        leg = bb.price_leg(self.stock, self.entry, 100.0, self.spy)
        for key, exit_d in (
            (None, leg["outD"]),
            ("90", leg["hz"]["90"]["outD"]),
            ("180", leg["hz"]["180"]["outD"]),
            ("365", leg["hz"]["365"]["outD"]),
        ):
            slot = leg if key is None else leg["hz"][key]
            stock_ret = close_on(self.stock, exit_d) / 100.0 - 1.0
            spy_ret = close_on(self.spy, exit_d) / close_on(self.spy, self.entry) - 1.0
            self.assertEqual(slot["ret"], bb.round_ret(stock_ret))
            self.assertEqual(slot["spy"], bb.round_ret(spy_ret))
            self.assertEqual(slot["xs"], bb.round_ret(stock_ret - spy_ret))

    def test_late_bar_is_absent_and_spy_slack_is_ten_days(self) -> None:
        target = self.entry_dt + timedelta(days=90)
        late = iso(target + timedelta(days=11))
        ok = iso(target + timedelta(days=10))
        self.assertEqual((datetime.strptime(late, "%Y-%m-%d") - target).days, 11)
        self.assertEqual((datetime.strptime(ok, "%Y-%m-%d") - target).days, 10)
        too_late = [[self.entry, 100.0], [late, 130.0], ["2024-12-01", 140.0]]
        self.assertIsNone(bb.horizon_exit(too_late, self.entry, 90))
        late_leg = bb.price_leg(too_late, self.entry, 100.0, None)
        self.assertNotIn("90", late_leg.get("hz") or {})
        self.assertEqual(late_leg["outD"], "2024-12-01")
        self.assertNotIn("spy", late_leg)
        self.assertEqual(bb.horizon_exit([[self.entry, 100.0], [ok, 130.0]], self.entry, 90)[0], ok)

        exit_d = self.after90
        near = [[self.entry, 50.0], ["2024-04-08", 58.0]]
        stock_ret = 0.2
        spy_ret = 58.0 / 50.0 - 1.0
        got = bb.spy_pair(near, self.entry, exit_d, stock_ret)
        self.assertIsNotNone(got)
        self.assertEqual(got["spy"], bb.round_ret(spy_ret))
        self.assertEqual(got["xs"], bb.round_ret(stock_ret - spy_ret))
        far = [[self.entry, 50.0], ["2024-04-14", 58.0]]
        self.assertIsNone(bb.spy_pair(far, self.entry, exit_d, stock_ret))

    def test_horizon_aggregate_counts_only_completed_legs(self) -> None:
        legs = [
            {
                "ret": 0.2,
                "spy": 0.1,
                "xs": 0.1,
                "hz": {
                    "90": {"ret": 0.2, "spy": 0.05, "xs": 0.15},
                    "180": {"ret": 0.4},
                },
            },
            {"ret": -0.1, "hz": {"90": {"ret": -0.2, "spy": 0.0, "xs": -0.2}}},
        ]
        summary = bb.summarize_group(legs)
        self.assertEqual(summary["n"], 2)
        self.assertEqual(summary["win"], 1)
        self.assertEqual(summary["med"], bb.round_ret(0.05))
        self.assertEqual(summary["spyMed"], bb.round_ret(0.1))
        self.assertEqual(summary["hz"]["90"]["n"], 2)
        self.assertEqual(summary["hz"]["180"]["n"], 1)
        self.assertNotIn("365", summary["hz"])
        self.assertIn("xsMed", summary["hz"]["90"])
        self.assertNotIn("spyMed", summary["hz"]["180"])

    def test_unpriced_filers_counts_eligible_with_no_legs(self) -> None:
        filers = {
            "priced": {"eligible": 3, "skip": 1, "legs": [{"ret": 0.1}]},
            "skipped": {"eligible": 2, "skip": 2, "legs": []},
            "never": {"eligible": 0, "skip": 0, "legs": []},
        }
        self.assertEqual(bb.count_unpriced_filers(filers), 1)


class BuyDipWhyTests(unittest.TestCase):
    def test_monthly_wording_follows_trend_not_the_intact_flag(self) -> None:
        self.assertIsNone(buy_dip_why(False, True, "UP"))
        self.assertEqual(
            buy_dip_why(True, True, "UP"),
            "Just landed, weekly washed out, monthly still up",
        )
        self.assertEqual(
            buy_dip_why(True, False, "UP"),
            "Just landed, weekly washed out, monthly still up",
        )
        for trend in ("DOWN", "FLAT", None, ""):
            text = buy_dip_why(True, True, trend)
            self.assertEqual(text, "Just landed, weekly washed out, monthly intact")
            self.assertNotIn("still up", text)
        for trend in ("DOWN", "FLAT", None, ""):
            text = buy_dip_why(True, False, trend)
            self.assertEqual(text, "Just landed, weekly washed out")
            self.assertNotIn("monthly", text)


if __name__ == "__main__":
    unittest.main()
