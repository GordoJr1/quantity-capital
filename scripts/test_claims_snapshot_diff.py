"""Tests for claims_snapshot_diff. Two tiny temp sqlite databases, no network."""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import claims_snapshot_diff as diff


SCHEMA = """
CREATE TABLE titles (
  province TEXT NOT NULL,
  objectid INTEGER NOT NULL,
  title_id TEXT,
  holder TEXT,
  status TEXT,
  tenure_type TEXT,
  issue_date TEXT,
  anniversary_date TEXT,
  due_date TEXT,
  extension_date TEXT,
  area_ha REAL,
  minx REAL,
  miny REAL,
  maxx REAL,
  maxy REAL,
  geom_json TEXT,
  PRIMARY KEY (province, objectid)
);
"""


def _write_db(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO titles (province, objectid, title_id, holder, area_ha) VALUES (?, ?, ?, ?, ?)",
            rows,
        )
        con.commit()
    finally:
        con.close()


@pytest.fixture
def old_sqlite(tmp_path: Path) -> Path:
    path = tmp_path / "space dir" / "old.sqlite"
    _write_db(path, [
        ("ontario", 1, "T-LAPSE", "Early Holder", 1.0),
        ("ontario", 7, "T-LAPSE", "Lapsed Mines Inc.", 4.5),
        ("yukon", 1, "T-SAME", "not kept", 1.0),
        ("yukon", 4, "T-SAME", "Steady Resources", 8.0),
        ("nunavut", 1, "T-PCT", "Gamma Corp. (100%)", 3.0),
        ("nunavut", 2, "T-XFER", "Alpha Metals (100%)", 20.0),
        ("quebec", 1, "T-LEAD", "(100) Lead Gold", 2.0),
    ])
    return path


@pytest.fixture
def new_sqlite(tmp_path: Path) -> Path:
    path = tmp_path / "space dir" / "new.sqlite"
    _write_db(path, [
        ("ontario", 2, "T-NEW", "stale new holder", 1.0),
        ("ontario", 9, "T-NEW", "Brand New Mining (100%)", 12.0),
        ("yukon", 1, "T-SAME", "Steady Resources", 8.0),
        ("nunavut", 3, "T-PCT", "  gamma   corp. ", 3.5),
        ("nunavut", 2, "T-XFER", "Beta Metals Inc.", 21.0),
        ("quebec", 1, "T-LEAD", "Lead Gold", 2.0),
    ])
    return path


def _holders(path: Path, rows: list[dict]) -> Path:
    path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return path


@pytest.fixture
def holders_old(tmp_path: Path) -> Path:
    return _holders(tmp_path / "old.holders.json", [
        {"province": "ontario", "holder": "Lapsed Mines Inc.", "companies": [{"company": "Lapsed Mines", "ticker": "LMS"}]},
        {"province": "nunavut", "holder": "Alpha Metals (100%)", "companies": [{"company": "Alpha Metals", "ticker": "ALPH"}]},
        {"province": "nunavut", "holder": "Gamma Corp. (100%)", "companies": [{"company": "Gamma", "ticker": "GAM"}]},
        {"province": "yukon", "holder": "Steady Resources", "companies": [{"company": "Steady", "ticker": "STDY"}]},
        {"province": "quebec", "holder": "(100) Lead Gold", "companies": [{"company": "Lead Gold", "ticker": "LEAD"}]},
    ])


@pytest.fixture
def holders_new(tmp_path: Path) -> Path:
    return _holders(tmp_path / "new.holders.json", [
        {"province": "ontario", "holder": "Brand New Mining (100%)", "companies": [{"company": "Brand New", "ticker": "BRND"}]},
        {"province": "nunavut", "holder": "Beta Metals Inc.", "companies": [{"company": "Beta Metals", "ticker": "BETA"}]},
        {"province": "nunavut", "holder": "  gamma   corp. ", "companies": [{"company": "Gamma", "ticker": "GAM"}]},
        {"province": "yukon", "holder": "Steady Resources", "companies": [{"company": "Steady", "ticker": "STDY"}]},
        {"province": "quebec", "holder": "Lead Gold", "companies": [{"company": "Lead Gold", "ticker": "LEAD"}]},
    ])


def test_normalize_holder_strips_percent_tags_case_and_punctuation() -> None:
    assert diff.normalize_holder("Gamma Corp. (100%)") == diff.normalize_holder("  gamma   corp. ")
    assert diff.normalize_holder("(100) Lead Gold") == diff.normalize_holder("Lead Gold")
    assert diff.normalize_holder("Alpha Metals (100%)") != diff.normalize_holder("Beta Metals Inc.")
    assert diff.normalize_holder("ACME, INC.") == diff.normalize_holder("Acme Inc")
    assert diff.normalize_holder("Foo (50%) (50%)") == "foo"
    assert diff.normalize_holder(None) == ""


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_diff_covers_new_lapsed_transferred_duplicates_and_percent_tags(
    old_sqlite: Path,
    new_sqlite: Path,
    holders_old: Path,
    holders_new: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    old_bytes = old_sqlite.read_bytes()
    new_bytes = new_sqlite.read_bytes()
    out = tmp_path / "out"
    code = diff.main([
        str(old_sqlite), str(new_sqlite),
        "--out-dir", str(out),
        "--holders-old", str(holders_old),
        "--holders-new", str(holders_new),
    ])
    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert old_sqlite.read_bytes() == old_bytes
    assert new_sqlite.read_bytes() == new_bytes
    assert not Path(str(old_sqlite) + "-wal").exists()
    assert not Path(str(new_sqlite) + "-wal").exists()

    company_path = out / "claims-diff-old-to-new-by-company.csv"
    detail_path = out / "claims-diff-old-to-new-detail.csv"
    report_path = out / "claims-diff-old-to-new.md"
    assert company_path.is_file()
    assert detail_path.is_file()
    assert report_path.is_file()

    companies = _read_csv(company_path)
    assert companies == [
        {
            "company": "Alpha Metals", "ticker": "ALPH", "province": "nunavut",
            "new": "0", "lapsed": "0", "transferred_in": "0", "transferred_out": "1",
            "new_area_ha": "0", "lapsed_area_ha": "0",
        },
        {
            "company": "Beta Metals", "ticker": "BETA", "province": "nunavut",
            "new": "0", "lapsed": "0", "transferred_in": "1", "transferred_out": "0",
            "new_area_ha": "0", "lapsed_area_ha": "0",
        },
        {
            "company": "Brand New", "ticker": "BRND", "province": "ontario",
            "new": "1", "lapsed": "0", "transferred_in": "0", "transferred_out": "0",
            "new_area_ha": "12", "lapsed_area_ha": "0",
        },
        {
            "company": "Lapsed Mines", "ticker": "LMS", "province": "ontario",
            "new": "0", "lapsed": "1", "transferred_in": "0", "transferred_out": "0",
            "new_area_ha": "0", "lapsed_area_ha": "4.5",
        },
    ]

    details = _read_csv(detail_path)
    assert [(row["change"], row["title_id"], row["old_holder"], row["new_holder"], row["company_old"], row["company_new"], row["area_ha"]) for row in details] == [
        ("new", "T-NEW", "", "Brand New Mining (100%)", "", "Brand New", "12"),
        ("lapsed", "T-LAPSE", "Lapsed Mines Inc.", "", "Lapsed Mines", "", "4.5"),
        ("transferred", "T-XFER", "Alpha Metals (100%)", "Beta Metals Inc.", "Alpha Metals", "Beta Metals", "21"),
    ]
    # The lower objectid duplicate must not survive, and percent-tag renames are not transfers.
    assert "Early Holder" not in detail_path.read_text(encoding="utf-8")
    assert "T-PCT" not in detail_path.read_text(encoding="utf-8")
    assert "T-LEAD" not in detail_path.read_text(encoding="utf-8")
    assert "T-SAME" not in detail_path.read_text(encoding="utf-8")

    report = report_path.read_text(encoding="utf-8")
    assert "Change totals: 1 new, 1 lapsed, 1 transferred, 3 unchanged." in report
    assert "5 distinct titles (7 rows read, 2 duplicate extras dropped)" in report
    assert "5 distinct titles (6 rows read, 1 duplicate extra dropped)" in report
    assert "| ontario | 2 | 2 | 1 | 1 | 1 | 1 | 1 | 1 | 0 |" in report
    assert "| nunavut | 2 | 2 | 2 | 2 | 0 | 0 | 0 | 0 | 1 |" in report
    assert "Alpha Metals (ALPH) -> Beta Metals (BETA): 1 title (nunavut 1)" in report
    assert "Brand New" in report
    assert "Lapsed Mines" in report

    assert "changes: new=1 lapsed=1 transferred=1 unchanged=3" in captured.out
    assert "dup_extras=2" in captured.out
    assert "dup_extras=1" in captured.out
    assert f"wrote: {company_path}" in captured.out
    assert "ontario" in captured.out
    # Per-province duplicate extras: ontario old has the lapsed pair.
    assert "ontario" in captured.out and "old_dupes" in captured.out


def test_without_holders_company_is_the_raw_holder(
    old_sqlite: Path,
    new_sqlite: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "raw"
    result = diff.run(old_sqlite, new_sqlite, out, fmt="both")
    details = _read_csv(out / "claims-diff-old-to-new-detail.csv")
    by_title = {row["title_id"]: row for row in details}
    assert by_title["T-XFER"]["company_old"] == "Alpha Metals (100%)"
    assert by_title["T-XFER"]["company_new"] == "Beta Metals Inc."
    assert by_title["T-NEW"]["company_new"] == "Brand New Mining (100%)"
    assert by_title["T-LAPSE"]["company_old"] == "Lapsed Mines Inc."
    assert "raw holder" in (out / "claims-diff-old-to-new.md").read_text(encoding="utf-8")
    assert result.outputs


def test_no_overwrite_adds_numeric_suffix(
    old_sqlite: Path,
    new_sqlite: Path,
    tmp_path: Path,
) -> None:
    out = tmp_path / "again"
    first = diff.run(old_sqlite, new_sqlite, out, fmt="both")
    original = (out / "claims-diff-old-to-new-by-company.csv").read_bytes()
    second = diff.run(old_sqlite, new_sqlite, out, fmt="both")
    third = diff.run(old_sqlite, new_sqlite, out, fmt="both")
    assert [path.name for path in first.outputs] == [
        "claims-diff-old-to-new-by-company.csv",
        "claims-diff-old-to-new-detail.csv",
        "claims-diff-old-to-new.md",
    ]
    assert [path.name for path in second.outputs] == [
        "claims-diff-old-to-new-by-company-2.csv",
        "claims-diff-old-to-new-detail-2.csv",
        "claims-diff-old-to-new-2.md",
    ]
    assert [path.name for path in third.outputs] == [
        "claims-diff-old-to-new-by-company-3.csv",
        "claims-diff-old-to-new-detail-3.csv",
        "claims-diff-old-to-new-3.md",
    ]
    assert (out / "claims-diff-old-to-new-by-company.csv").read_bytes() == original
    assert all(path.is_file() for path in third.outputs)


def test_format_csv_does_not_write_markdown(old_sqlite: Path, new_sqlite: Path, tmp_path: Path) -> None:
    out = tmp_path / "csv-only"
    result = diff.run(old_sqlite, new_sqlite, out, fmt="csv")
    names = {path.name for path in result.outputs}
    assert names == {
        "claims-diff-old-to-new-by-company.csv",
        "claims-diff-old-to-new-detail.csv",
    }
    assert not list(out.glob("*.md"))


def test_missing_database_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = diff.main([
        str(tmp_path / "missing-old.sqlite"),
        str(tmp_path / "missing-new.sqlite"),
        "--out-dir", str(tmp_path / "out"),
    ])
    captured = capsys.readouterr()
    assert code == 1
    assert "error:" in captured.err
    assert not list((tmp_path / "out").glob("*")) if (tmp_path / "out").exists() else True
