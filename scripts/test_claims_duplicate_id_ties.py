"""Duplicate watchlist ids no longer tie the holder (needs pytest)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ontario_claims_db as db  # noqa: E402


def test_duplicate_ids_link_to_the_main_id() -> None:
    book = db.load_company_book()
    aliases = db.load_aliases()
    exact, phrases, tokens = db.name_indexes(book)
    for holder, want, shell in [
        ("(100) CRH CANADA GROUP INC.", "crh", "crh-canada-group"),
        ("(100) DISCOVERY-CORP ENTERPRISES INC.", "discovery-corp-enterprises-inc", "discovery-enterprises"),
        ("(100) HEIDELBERG MATERIALS CANADA LIMITED", "heidelberg-materials", "heidelberg-materials-canada"),
    ]:
        assert shell in book  # the shell still exists; the alias picks the main id
        tie = db.match_holder(holder, book, [], {}, exact, phrases, tokens, blocks=set())
        assert tie["company_id"] is None and {want, shell} <= set(tie["ambiguous"])
        row = db.match_holder(holder, book, aliases, {}, exact, phrases, tokens, blocks=set())
        assert row["company_id"] == want and row["method"] == "alias", (holder, row)
