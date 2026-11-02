"""Issuer and extract data stays clean of known wrong links (needs pytest)."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ontario_claims_db as db  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def _issuers() -> dict[str, dict]:
    data = json.loads((ROOT / "claims" / "links" / "public-issuers.json").read_text(encoding="utf-8"))
    return {row["id"]: row for row in data["issuers"]}


def test_wrong_exact_norms_are_gone() -> None:
    issuers = _issuers()
    for cid, norm in [
        ("churchill-resources-inc", "churchill diamond"),
        ("borealis-mining", "borealis exploration"),
        ("equinox-gold", "equinox resources canada"),
        ("guardian-metal-resources", "guardian exploration"),
        ("labrador-gold", "labrador uranium"),
        ("selkirk-copper-mines", "selkirk mining"),
        ("solstice-minerals", "solstice gold"),
    ]:
        assert norm not in issuers[cid]["exact_norms"], (cid, norm)


def test_moved_tickers_sit_on_their_own_issuers() -> None:
    issuers = _issuers()
    owners: dict[str, list[str]] = {}
    for cid, row in issuers.items():
        for ticker in row.get("tickers") or []:
            owners.setdefault(ticker, []).append(cid)
    assert owners["GX.V"] == ["guardian-exploration-inc"]
    assert owners["SGC.V"] == ["solstice-gold-corp"]
    assert owners["KGC.V"] == ["kestrel-gold-inc"]
    assert owners["B.V"] == ["bcm-resources-corporation"]
    assert issuers["guardian-exploration-inc"]["exact_norms"] == ["guardian exploration"]
    assert issuers["solstice-gold-corp"]["exact_norms"] == ["solstice gold"]


def test_beta_profile_ids_match_file_names_and_index() -> None:
    index = json.loads((ROOT / "beta" / "explorers.json").read_text(encoding="utf-8"))
    rows = [row for value in index.values() if isinstance(value, list) for row in value if isinstance(row, dict) and "file" in row]
    for row in rows:
        path = ROOT / row["file"]
        assert path.is_file(), row["file"]
        assert path.stem == row["id"], row["file"]
        assert json.loads(path.read_text(encoding="utf-8"))["id"] == row["id"]
    ids = {row["id"] for row in rows}
    assert "kestrel-gold-inc" in ids and "bcm-resources-corporation" in ids
    assert "kinross-kgc-v" not in ids and "barrick-b-v" not in ids


def test_mis_tagged_extracts_have_no_foreign_focus_holders() -> None:
    owners = {"metal-energy": r"\bmetal energy\b", "solstice-minerals": r"\bsolstice minerals\b"}
    for cid, pattern in owners.items():
        for name in (f"{cid}.geojson", f"{cid}-ontario.geojson"):
            data = json.loads((ROOT / "claims" / name).read_text(encoding="utf-8"))
            for feat in data["features"]:
                props = feat["properties"]
                if props.get("role") != "neighbor":
                    assert re.search(pattern, props.get("holder") or "", re.I), (name, props.get("holder"))
    extract = db.harvest_extract_holders()
    for holder, cid in extract.items():
        low = holder.lower()
        for word, bad in [("go metals", "metal-energy"), ("solstice gold", "solstice-minerals"),
                          ("fulcrum metals", "canada-nickel"), ("panther metals", "canada-nickel"),
                          ("core critical metals", "critical-elements-lithium"), ("nexus metals", "nexus-gold"),
                          ("metals creek", "nickel-creek-platinum")]:
            assert not (word in low and cid == bad), (holder, cid)


def test_kestrel_and_bcm_resolve_to_one_company() -> None:
    book = db.load_company_book()
    exact, phrases, tokens = db.name_indexes(book)
    for holder, want in [("(100) KESTREL GOLD INC.", "kestrel-gold-inc"),
                         ("(100) BCM RESOURCES CORPORATION", "bcm-resources-corporation"),
                         ("(100) Solstice Gold Corp.", "solstice-gold-corp"),
                         ("Guardian Exploration Inc. (100%)", "guardian-exploration-inc")]:
        row = db.match_holder(holder, book, [], {}, exact, phrases, tokens, blocks=set())
        assert row["company_id"] == want, (holder, row)
