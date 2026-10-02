"""In-memory checks for the politician / insider same-week board."""
from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import overlap  # noqa: E402

DDL = """
CREATE TABLE politician_trades (
  trade_id TEXT PRIMARY KEY,
  filer TEXT,
  filer_id TEXT,
  chamber TEXT,
  ticker TEXT,
  asset_type TEXT,
  side TEXT,
  amount_raw TEXT,
  amount_mid REAL,
  trade_date TEXT
);
CREATE TABLE insider_trades (
  trade_id TEXT PRIMARY KEY,
  filer TEXT,
  filer_id TEXT,
  title TEXT,
  ticker TEXT,
  side TEXT,
  value REAL,
  trade_date TEXT
);
"""


class Overlap(unittest.TestCase):
    def setUp(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(DDL)
        self.n = 0

    def tearDown(self) -> None:
        self.con.close()

    def _pol(
        self,
        ticker: str,
        trade_date: str,
        *,
        side: str = "purchase",
        asset_type: str = "Stock",
        filer: str = "Ann Member",
        filer_id: str = "p1",
        chamber: str = "House",
        amount: str = "$1,001–$15,000",
        mid: float = 8000.5,
    ) -> None:
        self.n += 1
        self.con.execute(
            """
            INSERT INTO politician_trades(
              trade_id, filer, filer_id, chamber, ticker, asset_type, side,
              amount_raw, amount_mid, trade_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"p{self.n}", filer, filer_id, chamber, ticker, asset_type, side, amount, mid, trade_date),
        )

    def _ins(
        self,
        ticker: str,
        trade_date: str,
        *,
        side: str = "purchase",
        filer: str = "Pat Officer",
        filer_id: str = "i1",
        title: str = "CEO",
        value: float | None = 50000,
    ) -> None:
        self.n += 1
        self.con.execute(
            """
            INSERT INTO insider_trades(
              trade_id, filer, filer_id, title, ticker, side, value, trade_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (f"i{self.n}", filer, filer_id, title, ticker, side, value, trade_date),
        )

    def test_same_week_match(self) -> None:
        self._pol("AAPL", "2026-09-15", amount="$15,001–$50,000", mid=32500.5)
        self._pol("AAPL", "2026-09-16", filer="Ann Member", filer_id="p1", amount="$1,001–$15,000", mid=8000.5)
        self._ins("AAPL", "2026-09-16", value=20000)
        self._ins("AAPL", "2026-09-18", value=30000)
        payload = overlap.build(self.con)
        self.assertEqual(len(payload["rows"]), 1)
        row = payload["rows"][0]
        self.assertEqual(row["ticker"], "AAPL")
        self.assertEqual(row["week"], "2026-09-14")
        self.assertEqual(row["label"], "Week of Sep 14, 2026")
        self.assertEqual(row["match"], "both buy")
        self.assertEqual(len(row["politicians"]), 1)
        self.assertEqual(row["politicians"][0]["amount"], "$15,001–$50,000")
        self.assertEqual(row["politicians"][0]["side"], "Buy")
        self.assertEqual(row["insiders"][0]["title"], "CEO")
        self.assertEqual(row["insiders"][0]["value"], 50000)
        self.assertEqual(payload["stats"]["same_side"], 1)
        self.assertEqual(payload["window_start"], "2024-09-18")

    def test_different_week_no_match(self) -> None:
        self._pol("MSFT", "2026-09-08")
        self._ins("MSFT", "2026-09-16")
        self.assertEqual(overlap.build(self.con)["rows"], [])

    def test_sunday_monday_boundary(self) -> None:
        # 2026-09-07 is Monday. Sunday 2026-09-13 stays in that week.
        # Monday 2026-09-14 opens the next week and does not join the Sunday.
        self._pol("TSLA", "2026-09-13", side="sale")
        self._ins("TSLA", "2026-09-07", side="purchase", filer="Early", filer_id="early")
        self._ins("TSLA", "2026-09-14", side="purchase", filer="Next", filer_id="next")
        rows = overlap.build(self.con)["rows"]
        self.assertEqual([(r["ticker"], r["week"], r["match"]) for r in rows], [("TSLA", "2026-09-07", "opposite")])
        self.assertEqual(rows[0]["label"], "Week of Sep 7, 2026")
        self.assertEqual(rows[0]["insiders"][0]["id"], "early")

    def test_award_dropped(self) -> None:
        self._pol("AMZN", "2026-09-15")
        self._ins("AMZN", "2026-09-15", side="award", value=90000)
        self._ins("AMZN", "2026-09-16", side="exercise", filer_id="i2", filer="Opt", value=1000)
        self._ins("AMZN", "2026-09-16", side="exchange", filer_id="i3", filer="Swap", value=1000)
        self._pol("AMZN", "2026-09-15", side="exchange", filer_id="p2", filer="Ex")
        self.assertEqual(overlap.build(self.con)["rows"], [])

    def test_noise_tickers_and_non_stock_drop(self) -> None:
        self._pol("--", "2026-09-15")
        self._ins("--", "2026-09-15")
        self._pol("N/A", "2026-09-15", filer_id="p2")
        self._ins("N/A", "2026-09-15", filer_id="i2")
        self._pol("", "2026-09-15", filer_id="p3")
        self._ins("NVDA", "2026-09-15", filer_id="i3")
        self._pol("NVDA", "2026-09-15", asset_type="Stock Option", filer_id="p4")
        self._ins("NVDA", "2026-09-15", filer_id="i4", filer="Real")
        self._pol("IBM", "2026-09-16", asset_type="Corporate Bond", filer_id="p5")
        self._ins("IBM", "2026-09-16", filer_id="i5")
        self.assertEqual(overlap.build(self.con)["rows"], [])

    def test_sale_post_is_sell_and_newest_week_first(self) -> None:
        self._pol("NFLX", "2026-09-15", side="sale")
        self._ins("NFLX", "2026-09-16", side="sale_post", value=12000)
        self._pol("DIS", "2026-09-08", side="purchase", filer_id="p2")
        self._ins("DIS", "2026-09-09", side="sale", filer_id="i2", value=8000)
        rows = overlap.build(self.con)["rows"]
        self.assertEqual([(r["ticker"], r["match"]) for r in rows], [("NFLX", "both sell"), ("DIS", "opposite")])
        self.assertEqual(rows[0]["insiders"][0]["side"], "Sell")

    def test_caps_people_per_side(self) -> None:
        self._pol("CAP", "2026-09-15")
        for i in range(5):
            self._ins(
                "CAP",
                "2026-09-15",
                filer=f"Officer {i}",
                filer_id=f"i{i}",
                value=1000 * (i + 1),
            )
        row = overlap.build(self.con)["rows"][0]
        self.assertEqual(len(row["insiders"]), 3)
        self.assertEqual(row["insiders_more"], 2)
        self.assertEqual([p["id"] for p in row["insiders"]], ["i4", "i3", "i2"])

    def test_run_writes_and_failure_keeps_previous(self) -> None:
        self._pol("AAPL", "2026-09-15")
        self._ins("AAPL", "2026-09-15")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            export = root / "export"
            payload = overlap.run(self.con, root=root, export_dir=export)
            written = json.loads((root / overlap.OUT_NAME).read_text(encoding="utf-8"))
            self.assertEqual(written["stats"]["rows"], 1)
            self.assertEqual(payload["rows"][0]["ticker"], "AAPL")
            self.assertTrue((export / overlap.OUT_NAME).exists())
            prior = root / overlap.OUT_NAME
            prior.write_text('{"keep": true}\n', encoding="utf-8")
            bad = sqlite3.connect(":memory:")
            try:
                self.assertEqual(overlap.run(bad, root=root, export_dir=export), {})
            finally:
                bad.close()
            self.assertEqual(prior.read_text(encoding="utf-8"), '{"keep": true}\n')


if __name__ == "__main__":
    unittest.main()
