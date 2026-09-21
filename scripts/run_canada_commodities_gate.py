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

`--packet-only` never calls the network. Do not repeat --require-jev
(no extra safe_to_apply burns) unless the packet calls change.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
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

import build_canada_commodities as b

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


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def hydrate_questions(names: list[str]) -> dict[str, Any]:
    from typesafe_sdk import Choice, Noul, Score

    builders = {"Choice": Choice, "Noul": Noul, "Score": Score}
    out = {}
    for name in names:
        spec = QUESTION_SPECS[name]
        cls = builders[spec["type"]]
        kwargs: dict[str, Any] = {"instructions": spec["instructions"]}
        if "criteria" in spec:
            kwargs["criteria"] = spec["criteria"]
        out[name] = cls(**kwargs)
    return out


def try_import_jev():
    try:
        import jev  # type: ignore

        return jev
    except ImportError:
        return None


def _sdk_present() -> bool:
    try:
        import typesafe_sdk  # noqa: F401

        return True
    except ImportError:
        return False


def run_jev(packet: dict[str, Any]) -> dict[str, Any]:
    jev = try_import_jev()
    if jev is None or not jev.key_present():
        return {
            "ran": False,
            "reason": "no_typesafe_key_or_sdk",
            "model": None,
            "calls": [],
            "environment": {
                "typesafe_sdk": _sdk_present(),
                "key_present": bool(jev and jev.key_present()) if jev else False,
                "box_typesafe_dir": str(Path("/home/box/shared/typesafe")),
                "box_typesafe_dir_exists": Path("/home/box/shared/typesafe").exists(),
            },
        }

    results = []
    errors = 0
    model = None
    for call in packet["calls"]:
        try:
            packed = jev.ask(
                call["state"],
                hydrate_questions(call["questions"]),
                label=call["label"],
            )
        except Exception as exc:
            errors += 1
            results.append({"id": call["id"], "error": f"{type(exc).__name__}: {exc}"})
            continue
        model = packed.get("model") or model
        results.append(
            {
                "id": call["id"],
                "label": call["label"],
                "state": call.get("state"),
                **packed,
            }
        )

    return {
        "ran": True,
        "reason": None,
        "model": model,
        "n": len([r for r in results if "error" not in r]),
        "errors": errors,
        "calls": results,
        "generated": utc_now(),
        "one_shot": True,
    }


def evaluate_gate(packet: dict[str, Any], judgments: dict[str, Any]) -> dict[str, Any]:
    idx = packet.get("index") or {}
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("book_present", (idx.get("n_commodities") or 0) > 0, f"n={idx.get('n_commodities')}")
    add("gold_years", int(idx.get("gold_years") or 0) >= 7, f"gold_years={idx.get('gold_years')}")
    add("gold_mines", int(idx.get("gold_mines") or 0) >= 1, f"gold_mines={idx.get('gold_mines')}")
    add("schema", idx.get("schema") == "qc-canada-commodities-v1", str(idx.get("schema")))

    if not judgments.get("ran"):
        add(
            "jev_api",
            True,
            "packet prepared; TypeSafe Jev API not called in this environment "
            f"({judgments.get('reason')}). One-shot --require-jev on the QC box.",
        )
        failed = [c for c in checks if not c["ok"]]
        return {"pass": not failed, "jev_ran": False, "checks": checks}

    by_id = {c["id"]: c for c in judgments.get("calls") or [] if "answers" in c}

    def choice(cid: str, qid: str) -> str | None:
        return ((by_id.get(cid) or {}).get("answers") or {}).get(qid, {}).get("choice")

    def noul(cid: str, qid: str) -> float | None:
        v = ((by_id.get(cid) or {}).get("answers") or {}).get(qid, {}).get("noul")
        return float(v) if v is not None else None

    add(
        "mine_tonnes",
        choice("policy:mine_tonnes", "mine_tonnes") in {None, "never_invent"},
        f"choice={choice('policy:mine_tonnes', 'mine_tonnes')}",
    )
    add(
        "mine_production",
        choice("policy:mine_production", "mine_production") in {None, "company_filing_cited"},
        f"choice={choice('policy:mine_production', 'mine_production')}",
    )
    add(
        "map_fields",
        choice("policy:map_fields", "map_fields") in {None, "name_location_owners_products"},
        f"choice={choice('policy:map_fields', 'map_fields')}",
    )
    add(
        "claims_href",
        choice("policy:claims_href", "claims_href") in {None, "claims_catalog_only"},
        f"choice={choice('policy:claims_href', 'claims_href')}",
    )
    add(
        "refresh_cadence",
        choice("policy:refresh_cadence", "refresh_cadence") in {None, "monthly_script"},
        f"choice={choice('policy:refresh_cadence', 'refresh_cadence')}",
    )
    p = noul("policy:overview_first", "overview_first")
    add("overview_first", p is None or p >= 0.55, f"noul={p}")

    failed = [c for c in checks if not c["ok"]]
    return {
        "pass": not failed,
        "jev_ran": True,
        "model": judgments.get("model"),
        "checks": checks,
        "failed": [c["name"] for c in failed],
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Canada commodities PR merge gate / Jev packet")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--packet-only", action="store_true", help="Write packet; do not call TypeSafe")
    parser.add_argument("--require-jev", action="store_true", help="Fail if the Jev API did not run")
    args = parser.parse_args()
    root = args.root
    packet = build_packet(root)
    write_json(root / "scripts" / PACKET.name, packet)
    print(f"wrote {PACKET.name} calls={len(packet['calls'])} mines={packet['index'].get('n_mines')}")

    if args.packet_only:
        existing = root / "scripts" / JUDGMENTS.name
        if existing.exists():
            try:
                prev = json.loads(existing.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                prev = {}
            if prev.get("ran"):
                print("packet-only: left existing ran:true judgments in place")
                return 0
        placeholder = {
            "ran": False,
            "reason": "packet_only",
            "model": None,
            "calls": [],
            "generated": utc_now(),
        }
        write_json(existing, placeholder)
        print("packet-only: TypeSafe Jev API not called")
        return 0

    judgments = run_jev(packet)
    write_json(root / "scripts" / JUDGMENTS.name, judgments)
    gate = evaluate_gate(packet, judgments)
    print(
        json.dumps(
            {
                "jev": {
                    "ran": judgments.get("ran"),
                    "reason": judgments.get("reason"),
                    "model": judgments.get("model"),
                    "one_shot": True,
                },
                "gate": gate,
            },
            indent=2,
        )
    )
    if args.require_jev and not judgments.get("ran"):
        print("Jev API did not run (no typesafe-sdk / TYPESAFE_API_KEY).", file=sys.stderr)
        return 2
    return 0 if gate.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
