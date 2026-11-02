"""Curated link blocks stop known wrong links (needs pytest)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ontario_claims_db as db  # noqa: E402


def _book() -> dict:
    book = {
        "churchill-resources-inc": {"names": ["Churchill Resources Inc."], "exact_norms": ["churchill diamond", "churchill resources"]},
        "metal-energy": {"names": ["Metal Energy"], "exact_norms": []},
        "go-metals-corp": {"names": ["Go Metals Corp."], "exact_norms": ["go metals"]},
    }
    for cid, slot in book.items():
        slot.update(company_id=cid, name=slot["names"][0], tickers=[], ticker=None, assets=[], asset_rows=[], exchanges=[])
        slot["norms"] = [db.normalize_name(n) for n in slot["names"]]
        slot["tokens"] = set()
    return book


def _match(holder: str, blocks: set, extract: dict | None = None) -> dict:
    book = _book()
    exact, phrases, tokens = db.name_indexes(book)
    return db.match_holder(holder, book, [], extract or {}, exact, phrases, tokens, blocks=blocks)


def test_block_stops_exact_norms_link() -> None:
    assert _match("(100) Churchill Diamond Corporation", set())["company_id"] == "churchill-resources-inc"
    blocks = {("churchill diamond", "churchill-resources-inc")}
    assert _match("(100) Churchill Diamond Corporation", blocks)["company_id"] is None
    assert _match("(100) Churchill Resources Inc.", blocks)["company_id"] == "churchill-resources-inc"


def test_block_stops_extract_link_and_catalog_name_wins() -> None:
    extract = {"(100) Go Metals Corp.": "metal-energy"}
    assert _match("(100) Go Metals Corp.", set(), extract)["company_id"] == "metal-energy"
    row = _match("(100) Go Metals Corp.", {("go metals", "metal-energy")}, extract)
    assert row["company_id"] == "go-metals-corp"
    assert row["method"] == "catalog_name"


def test_repo_block_file_parses() -> None:
    blocks = db.load_link_blocks()
    assert ("churchill diamond", "churchill-resources-inc") not in blocks  # subsidiary: now an alias
    assert len(blocks) == 9  # +2 fortune blocks (Ontario tag batch 1), -1 churchill (subsidiary)


def test_blocked_company_in_two_way_tie_links_the_valid_one() -> None:
    book = _book()
    # Both companies pin "churchill diamond" in exact_norms: a two-way tie.
    book["churchill-diamond-holdings"] = dict(
        book["churchill-resources-inc"], company_id="churchill-diamond-holdings",
        name="Churchill Diamond Holdings", names=["Churchill Diamond Holdings"],
        norms=[db.normalize_name("Churchill Diamond Holdings")], exact_norms=["churchill diamond"],
    )
    exact, phrases, tokens = db.name_indexes(book)
    holder = "(100) Churchill Diamond Corporation"
    tie = db.match_holder(holder, book, [], {}, exact, phrases, tokens, blocks=set())
    assert tie["company_id"] is None  # a pinned two-way tie links nothing
    row = db.match_holder(holder, book, [], {}, exact, phrases, tokens,
                          blocks={("churchill diamond", "churchill-resources-inc")})
    assert row["company_id"] == "churchill-diamond-holdings"
