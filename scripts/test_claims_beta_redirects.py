"""Renamed beta ids still resolve, and the cleaned claims pages are consistent (needs pytest)."""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load(rel: str):
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def test_beta_html_redirects_old_ids_to_existing_profiles() -> None:
    html = (ROOT / "beta.html").read_text(encoding="utf-8")
    m = re.search(r"const BETA_ID_REDIRECTS = (\{[^}]*\});", html)
    assert m
    redirects = json.loads(m.group(1))
    assert redirects == {"kinross-kgc-v": "kestrel-gold-inc", "barrick-b-v": "bcm-resources-corporation"}
    ids = {r["id"] for r in _load("beta/explorers.json")["issuers"]} if "issuers" in _load("beta/explorers.json") else set()
    for old, new in redirects.items():
        assert not (ROOT / "beta" / f"{old}.json").exists()
        assert (ROOT / "beta" / f"{new}.json").is_file()
        if ids:
            assert new in ids and old not in ids
    assert "BETA_ID_REDIRECTS[rawId]" in html


def test_issuer_fixes() -> None:
    issuers = {r["id"]: r for r in _load("claims/links/public-issuers.json")["issuers"]}
    assert "Errington Metals Inc." in issuers["errington-metals-corp"]["names"]
    lab = issuers["labrador-gold"]
    assert "LTX.V" not in lab["tickers"]  # LTX.V is Labrador Resources Inc. (Alberta oil and gas)
    assert "labrador resources" not in lab["exact_norms"]
    assert "labrador iron mines" not in lab["exact_norms"]


def _focus(rel: str) -> list[dict]:
    path = ROOT / rel
    if not path.is_file():
        return []
    return [f for f in _load(rel)["features"] if (f.get("properties") or {}).get("role") != "neighbor"]


def test_retagged_pages_report_their_real_focus_counts() -> None:
    companies = {r["id"]: r for r in _load("claims/companies.json")["companies"]}
    for cid in ("metal-energy", "solstice-minerals"):
        row = companies[cid]
        quebec = len(_focus(f"claims/{cid}.geojson"))
        ontario = len(_focus(f"claims/{cid}-ontario.geojson"))
        bc = len(_focus(f"claims/{cid}-bc.geojson"))
        assert row.get("quebec_count", 0) == quebec
        assert row.get("ontario_count", 0) == ontario
        assert row.get("bc_count", 0) == bc
        assert row["claim_count"] == quebec + ontario + bc
        if not ontario:
            assert row.get("ontario_bbox") is None
        if not quebec:
            assert row.get("quebec_bbox") is None
    overview = _load("claims/overview.geojson")["features"]
    solstice = [f for f in overview if f["properties"].get("company_id") == "solstice-minerals"]
    metal = [f for f in overview if f["properties"].get("company_id") == "metal-energy"]
    assert not solstice
    assert sum(f["properties"]["claim_count"] for f in metal) == companies["metal-energy"]["bc_count"]
