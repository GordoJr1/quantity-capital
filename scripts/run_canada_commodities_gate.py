#!/usr/bin/env python3
"""QC merge-gate packet + one-shot TypeSafe Jev for Canada commodities.

Deterministic owner/mine linker first. Jev is only for leftover fuzzy
Map 900A owner → claims-catalog company pairs. Cloud Agents / pr-check
are packet-only (no TYPESAFE_API_KEY). The QC box runs one shot:

    set -a
    source /home/box/shared/typesafe/env          # or ~/.grok/typesafe.env
    set +a                                        # Windows: %USERPROFILE%\\.grok\\typesafe.env
    python3 -m pip install -q typesafe-sdk
    python3 scripts/run_canada_commodities_gate.py --require-jev

Writes:
  scripts/canada-commodities-jev-packet.json
  scripts/canada-commodities-jev-judgments.json

`--packet-only` never calls the network but still runs the deterministic
checks. Shared rules live in scripts/qc_gate.py. Do not repeat --require-jev
(no extra safe_to_apply burns) unless the packet calls change.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACKET = HERE / "canada-commodities-jev-packet.json"
JUDGMENTS = HERE / "canada-commodities-jev-judgments.json"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
SQ_JEV = HERE / "qc_sqlite"
if str(SQ_JEV) not in sys.path:
    sys.path.insert(0, str(SQ_JEV))

import build_canada_commodities as b  # noqa: E402
import qc_gate  # noqa: E402
from qc_gate import utc_now  # noqa: E402

QUESTION_SPECS: dict[str, dict[str, Any]] = {
    "mine_tonnes": {
        "type": "Choice",
        "instructions": (
            "Map 900A and StatCan/NRCan do not publish mine-level tonnes. "
            "What should canada.html store on each mine row?"
        ),
        "criteria": {
            "never_invent": (
                "Name, location, owners, products, optional claims deep link. "
                "Never invent per-mine tonnes, koz, or split AuEq. Company-"
                "disclosed 2025 production may be stored only with a filing URL."
            ),
            "split_national": (
                "Divide the Canada total equally across Map 900A mines."
            ),
            "guess_43_101": (
                "Fill mine tonnes from memory or unsourced 43-101 guesses."
            ),
        },
    },
    "map_fields": {
        "type": "Choice",
        "instructions": "Which Map 900A fields belong on the principal-mine roster?",
        "criteria": {
            "name_location_owners_products": (
                "operation_name, city/province, operator_owners, product(s), lat/lon."
            ),
            "plus_invented_tonnes": (
                "Those fields plus an invented annual tonne column."
            ),
        },
    },
    "claims_href": {
        "type": "Choice",
        "instructions": (
            "When should a mine row link to claims.html?company=…?"
        ),
        "criteria": {
            "claims_catalog_only": (
                "Only when the owner/mine matches a claims/companies.json id "
                "(optional qc.sqlite aliases for that same id). Do not mint "
                "hrefs for beta/mcap-only names."
            ),
            "any_beta_id": (
                "Link any beta/mcap slug even if it is not on the claims map."
            ),
        },
    },
    "mine_production": {
        "type": "Choice",
        "instructions": (
            "When may a principal-mine row show a 2025 production figure?"
        ),
        "criteria": {
            "company_filing_cited": (
                "Only when a public company report (MD&A, AIF, annual, ops "
                "update, NI 43-101 actuals) states that mine's output for the "
                "selected commodity, stored in qc.sqlite and exported to "
                "canada/producer-join.json (and existing beta/<company>.json "
                "production[] / by_asset) with URL/citation. Canada table "
                "reads that export. Blank if not disclosed or only AuEq / "
                "a complex total."
            ),
            "split_or_guess": (
                "Split Canada totals, convert AuEq to gold, or guess ounces."
            ),
        },
    },
    "refresh_cadence": {
        "type": "Choice",
        "instructions": "How should this book refresh?",
        "criteria": {
            "monthly_script": (
                "python3 scripts/build_canada_commodities.py and "
                "python3 scripts/ingest_canada_mine_production.py "
                "--sqlite qc.sqlite --apply (ingest → qc.sqlite → existing "
                "beta/<issuer>.json + canada/producer-join.json) "
                "on a monthly job. Not a morning/evening tape bat. Not daily-update."
            ),
            "morning_bat": (
                "Add the NRCan/StatCan crawl to the twice-daily Windows bats."
            ),
        },
    },
    "overview_first": {
        "type": "Noul",
        "instructions": (
            "Is it correct that canada.html must not call loadAllExtracts or "
            "fetch per-company claim GeoJSON, and mine clicks use the existing "
            "overview-first claims.html?company= deep link?"
        ),
        "criteria": {
            "true": "Yes — Claims stays overview-first.",
            "false": "No — Canada should load every extract.",
        },
    },
    "link_state": {
        "type": "Score",
        "instructions": (
            "How do the Map 900A owner and the claims-catalog issuer relate "
            "as companies? Use this only when the deterministic linker left "
            "the owner unmatched."
        ),
        "criteria": [
            "They name two different companies or issuers.",
            (
                "They are related (subsidiary, project vehicle, former name) "
                "but not clearly the same listed issuer."
            ),
            (
                "They are the same listed issuer, or the owner is a wholly "
                "owned title vehicle for that issuer."
            ),
        ],
    },
}


def unique_mines(payload: dict[str, Any]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for comm in payload.get("commodities") or []:
        for mine in comm.get("mines") or []:
            key = (mine.get("name") or "", mine.get("owners") or "")
            if key in seen:
                continue
            seen.add(key)
            out.append(mine)
    return out


def fuzzy_owner_calls(root: Path, limit: int = 8) -> list[dict[str, Any]]:
    """Unlinked owners with 1–2 distinctive claims-catalog candidates."""
    catalog = b.load_company_catalog(root)
    claims = {cid: rec for cid, rec in catalog.items() if rec.get("in_claims")}
    payload_path = root / "canada" / "commodities.json"
    if not payload_path.exists():
        return []
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    calls: list[dict[str, Any]] = []
    used_owners: set[str] = set()
    for mine in unique_mines(payload):
        if mine.get("claims_company") or not mine.get("owners"):
            continue
        owner = mine["owners"]
        folded = b.fold(owner)
        if folded in used_owners:
            continue
        ck = b.core_key(owner)
        if len(ck) < 5:
            continue
        cands: list[dict[str, str]] = []
        for cid, rec in claims.items():
            names = " ".join(rec.get("names") or [])
            if b.core_key(cid.replace("-", " ")) == ck or b.fold(cid.replace("-", " ")) == folded:
                cands.append({"id": cid, "name": (rec["names"][0] if rec["names"] else cid)})
                continue
            if ck and ck in b.core_key(names):
                cands.append({"id": cid, "name": (rec["names"][0] if rec["names"] else cid)})
        # unique by id
        uniq: list[dict[str, str]] = []
        seen: set[str] = set()
        for c in cands:
            if c["id"] not in seen:
                seen.add(c["id"])
                uniq.append(c)
        if not (1 <= len(uniq) <= 2):
            continue
        used_owners.add(folded)
        cand = uniq[0]
        calls.append(
            {
                "id": "owner:" + b.slugify(owner),
                "label": f"canada_owner_v1:{folded}:{cand['id']}",
                "questions": ["link_state"],
                "state": {
                    "owner": owner,
                    "mine": mine.get("name"),
                    "catalog_id": cand["id"],
                    "catalog_name": cand["name"],
                    "alt_candidates": [c["id"] for c in uniq[1:]],
                    "in_claims": True,
                    "do_not": "invent mine tonnes",
                },
            }
        )
        if len(calls) >= limit:
            break
    return calls


def build_calls(root: Path) -> list[dict[str, Any]]:
    policy = [
        {
            "id": "policy:mine_tonnes",
            "label": "canada_mine_tonnes_v1",
            "questions": ["mine_tonnes"],
            "state": {"locked_decision": "never_invent", "source": "Map 900A / StatCan / NRCan"},
        },
        {
            "id": "policy:map_fields",
            "label": "canada_map_fields_v1",
            "questions": ["map_fields"],
            "state": {"locked_decision": "name_location_owners_products"},
        },
        {
            "id": "policy:claims_href",
            "label": "canada_claims_href_v1",
            "questions": ["claims_href"],
            "state": {"locked_decision": "claims_catalog_only"},
        },
        {
            "id": "policy:mine_production",
            "label": "canada_mine_production_v1",
            "questions": ["mine_production"],
            "state": {
                "locked_decision": "company_filing_cited",
                "do_not": ["invent ounces", "split AuEq", "split complex totals"],
            },
        },
        {
            "id": "policy:refresh_cadence",
            "label": "canada_refresh_cadence_v1",
            "questions": ["refresh_cadence"],
            "state": {"locked_decision": "monthly_script", "not": ["morning bat", "daily-update"]},
        },
        {
            "id": "policy:overview_first",
            "label": "canada_overview_first_v1",
            "questions": ["overview_first"],
            "state": {"locked_decision": True, "must_not": ["loadAllExtracts", "per-company extracts"]},
        },
    ]
    return policy + fuzzy_owner_calls(root)


def book_index(root: Path) -> dict[str, Any]:
    path = root / "canada" / "commodities.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    gold = next((c for c in data.get("commodities") or [] if c.get("id") == "gold"), None)
    mines = unique_mines(data) if data else []
    return {
        "json": "canada/commodities.json",
        "schema": data.get("schema"),
        "n_commodities": len(data.get("commodities") or []),
        "n_mines": data.get("n_mines") or len(mines),
        "n_linked": data.get("n_linked"),
        "latest_year": data.get("latest_year"),
        "years_available": data.get("years_available"),
        "gold_years": len((gold or {}).get("series") or []),
        "gold_mines": len((gold or {}).get("mines") or []),
    }


def build_packet(root: Path) -> dict[str, Any]:
    idx = book_index(root)
    return {
        "schema": "qc-pr-merge-gate-v1",
        "work": "C",
        "pr_topic": "canadian commodity production + Map 900A mines",
        "generated": utc_now(),
        "model": "jev-latest",
        "api": "typesafe-sdk TypeSafeClient.system_one (Choice / Noul / Score)",
        "not": "Jev Bot chat",
        "index": idx,
        "question_specs": QUESTION_SPECS,
        "calls": build_calls(root),
        "gate": {
            "pass_if": [
                "mine_tonnes choice is never_invent (if Jev ran)",
                "mine_production choice is company_filing_cited (if Jev ran)",
                "map_fields choice is name_location_owners_products (if Jev ran)",
                "claims_href choice is claims_catalog_only (if Jev ran)",
                "refresh_cadence choice is monthly_script (if Jev ran)",
                "overview_first noul >= 0.55 (if Jev ran)",
                "gold series covers a 20-year (else 10-year) window",
                "no invented mine-level tonnes",
                "claims hrefs only for claims/companies.json ids",
                "canada.html must not call loadAllExtracts",
            ],
            "fail_closed_without_key": False,
            "fail_on_missing_answer": True,
            "note": (
                "Cloud agent prepares the packet. QC box with TYPESAFE_API_KEY "
                "runs this script once to fill judgments. Do not re-burn "
                "safe_to_apply unless the packet calls change."
            ),
        },
        "box_commands": [
            "set -a && source /home/box/shared/typesafe/env && set +a",
            "python3 -m pip install -q typesafe-sdk",
            "python3 scripts/run_canada_commodities_gate.py --require-jev",
        ],
        "desktop_key": [
            "TYPESAFE_API_KEY",
            r"C:\Users\gordo\.grok\typesafe.env",
            "~/.grok/typesafe.env",
        ],
    }


def _deterministic(packet: dict[str, Any], checks: qc_gate.Checks) -> None:
    idx = packet.get("index") or {}
    checks.add("book_present", (idx.get("n_commodities") or 0) > 0, f"n={idx.get('n_commodities')}")
    checks.add("gold_years", int(idx.get("gold_years") or 0) >= 7, f"gold_years={idx.get('gold_years')}")
    checks.add("gold_mines", int(idx.get("gold_mines") or 0) >= 1, f"gold_mines={idx.get('gold_mines')}")
    checks.add("schema", idx.get("schema") == "qc-canada-commodities-v1", str(idx.get("schema")))


EXPECTED_CHOICES = {
    "mine_tonnes": ("policy:mine_tonnes", "never_invent"),
    "mine_production": ("policy:mine_production", "company_filing_cited"),
    "map_fields": ("policy:map_fields", "name_location_owners_products"),
    "claims_href": ("policy:claims_href", "claims_catalog_only"),
    "refresh_cadence": ("policy:refresh_cadence", "monthly_script"),
}


def _jev_checks(look: qc_gate.Lookup, checks: qc_gate.Checks) -> None:
    for qid, (cid, want) in EXPECTED_CHOICES.items():
        got = look.choice(cid, qid)
        checks.add(qid, got in {None, want}, f"choice={got}")
    p = look.noul("policy:overview_first", "overview_first")
    checks.add("overview_first", p is None or p >= 0.55, f"noul={p}")


def evaluate_gate(packet: dict[str, Any], judgments: dict[str, Any]) -> dict[str, Any]:
    return qc_gate.evaluate(
        packet, judgments, _deterministic, _jev_checks,
        skipped_note="One-shot --require-jev on the QC box.",
    )


def main(argv: list[str] | None = None, jev_module: Any = None) -> int:
    return qc_gate.main(
        argv,
        description="Canada commodities PR merge gate / Jev packet",
        default_root=ROOT,
        packet_name=PACKET.name,
        judgments_name=JUDGMENTS.name,
        build_packet=build_packet,
        evaluate_gate=evaluate_gate,
        specs=QUESTION_SPECS,
        summary=lambda pk: f"calls={len(pk['calls'])} mines={pk['index'].get('n_mines')}",
        keep_state=True,
        extra_fields={"one_shot": True},
        jev_module=jev_module,
    )


if __name__ == "__main__":
    raise SystemExit(main())
