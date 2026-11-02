"""Restyle-only holder edits are not transfers; suffix-twin company ids fold into one.

Needs pytest: run `python -m pytest scripts/test_claims_restyle.py`.
The __main__ block below is a no-pytest fallback.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import claims_snapshot_diff as diff  # noqa: E402

RESTYLES = [
    ("(100) Critical Mineral Properties", "(100) Critical Mineral Properties Inc."),
    ("(100) CANADA NICKEL COMPANY INC.", "(100) Canada Nickel Company Inc"),
    ("(100) Société Minière Nunavik Ltée", "(100) SOCIETE MINIERE NUNAVIK LTEE"),
    ("(100) Cynthia  Suworow", "(100) Cynthia Suworow"),
    ("(50) A Gold Corp., (50) B Metals", "(50) A Gold Corp, (50) B Metals Ltd."),
    ("Gold Strike Resources Corp. - 100%", "Gold Strike Resources Corp - 100%"),
    ("ACME, INC.", "Acme Inc"),
]
CHANGES = [
    ("(100) SRQ Resources Inc.", "(100) SRQ Exploration Inc."),
    ("(20) X, (15) Y LTD., (65) CANADA NICKEL COMPANY INC.", "(100) CANADA NICKEL COMPANY INC."),
    ("(100) PROBE GOLD INC.", "(100) FRESNILLO CANADA INC."),
    ("(100) Exploration Nunavik Ltée", "(100) Société minière Nunavik Ltée"),
    ("(100) Alpha Gold Ltd.", "(100) Alpha Gold Inc."),
    ("(100) Beta Mining Co.", "(100) Beta Mining Company"),
    ("(60) A Gold Corp., (40) B Metals", "(40) A Gold Corp., (60) B Metals"),
]


def test_restyles_are_no_change() -> None:
    for old, new in RESTYLES:
        assert diff.same_holder(old, new), (old, new)


def test_real_changes_stay_changes() -> None:
    for old, new in CHANGES:
        assert not diff.same_holder(old, new), (old, new)


def test_fold_suffix_twins_merges_ids_and_assets() -> None:
    import ontario_claims_db as ondb

    book = {
        "nuvau-minerals": {"names": ["Nuvau Minerals Inc."], "tickers": ["NMC.V"], "ticker": "NMC.V",
                           "assets": ["a2"], "asset_rows": [{"id": "a2"}]},
        "nuvau-minerals-inc": {"names": ["Nuvau Minerals Inc."], "tickers": [], "ticker": None,
                               "assets": ["a1"], "asset_rows": [{"id": "a1"}]},
    }
    assert ondb.fold_suffix_twins(book) == [("nuvau-minerals", "nuvau-minerals-inc")]
    assert list(book) == ["nuvau-minerals-inc"]
    kept = book["nuvau-minerals-inc"]
    assert kept["ticker"] == "NMC.V"
    assert kept["assets"] == ["a1", "a2"]
    assert [row["id"] for row in kept["asset_rows"]] == ["a1", "a2"]


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
