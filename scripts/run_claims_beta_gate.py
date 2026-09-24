#!/usr/bin/env python3
"""QC merge-gate packet + TypeSafe Jev runner for Work B (claims issuer profiles).

Talks to the TypeSafe Jev API (Choice / Noul / Score via typesafe-sdk), not Jev Bot.

Cloud Agents do **not** have TYPESAFE_API_KEY. CI is packet-only. The QC box
runs the API:

    set -a
    source /home/box/shared/typesafe/env          # or ~/.grok/typesafe.env
    set +a
    python3 -m pip install -q typesafe-sdk
    python3 scripts/run_claims_beta_gate.py --require-jev

Writes:
  scripts/claims-beta-jev-packet.json
  scripts/claims-beta-jev-judgments.json

`--packet-only` never calls the network but still runs the deterministic
checks (pr-check). `--require-jev` exits 2 unless every call answered.
Shared rules live in scripts/qc_gate.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACKET = HERE / "claims-beta-jev-packet.json"
JUDGMENTS = HERE / "claims-beta-jev-judgments.json"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import qc_gate  # noqa: E402
from qc_gate import utc_now  # noqa: E402


QUESTION_SPECS: dict[str, dict[str, Any]] = {
    "shell_fields": {
        "type": "Choice",
        "instructions": (
            "Which fields are required on a newly generated claims-public issuer "
            "shell when the company is not already filing-backed?"
        ),
        "criteria": {
            "claims_id_tickers_no_ounces": (
                "schema, claims/companies.json id, issuer name and tickers, null "
                "ounce KPIs, optional catalog mines. Do not invent ounces."
            ),
            "full_iamgold_rr": (
                "Full IAMGOLD-style reserves/resources/production even if unsourced."
            ),
            "tickers_only": (
                "Tickers and name only; omit schema, assets, and KPI stubs."
            ),
        },
    },
    "map_extent": {
        "type": "Choice",
        "instructions": (
            "What should the beta.html profile map preview fit to for a "
            "claims-map issuer?"
        ),
        "criteria": {
            "company_footprint": (
                "Fit that company's overview cells (and mines that fall in view). "
                "Not Canada-wide. Not per-company extracts."
            ),
            "all_mines_world": (
                "Fit every mine marker worldwide (IAMGOLD goes Africa + Canada)."
            ),
            "canada_default": (
                "Always start at a fixed Canada claims view."
            ),
        },
    },
    "empty_profile": {
        "type": "Choice",
        "instructions": (
            "How should a claims-public shell with no ounces or assets render?"
        ),
        "criteria": {
            "hide_empty_show_map": (
                "Hide empty mines / R&R tables. Show a claims note and the map "
                "preview when an overview footprint exists."
            ),
            "placeholder_dashes": (
                "Show ounce tables filled with dashes as if production exists."
            ),
            "error_page": (
                "Refuse to open the profile until a filing pass."
            ),
        },
    },
    "id_alias": {
        "type": "Choice",
        "instructions": (
            "The claims catalog id is troilus-mining. The filing-backed shell is "
            "beta/troilus.json (id troilus). How should beta.html?id=troilus-mining resolve?"
        ),
        "criteria": {
            "alias_to_existing": (
                "Resolve troilus-mining to the existing filing-backed "
                "beta/troilus.json. Do not invent a second ounce book."
            ),
            "new_stub": (
                "Write a new empty beta/troilus-mining.json shell."
            ),
            "ignore_claims_id": (
                "Only beta.html?id=troilus works; the claims id 404s."
            ),
        },
    },
    "preview_light": {
        "type": "Noul",
        "instructions": (
            "Is it correct that the profile map preview loads claims/overview.geojson "
            "plus catalog/profile mine markers and must not fetch per-company extracts "
            "or call loadAllExtracts?"
        ),
        "criteria": {
            "true": "Yes — overview cells / footprint / mines only.",
            "false": "No — the preview should load full province extracts.",
        },
    },
}


def load_index(root: Path) -> dict[str, Any]:
    path = root / "beta" / "claims-publics.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def index_summary(data: dict[str, Any]) -> dict[str, Any]:
    rows = data.get("issuers") or []
    by_id = {r.get("id"): r for r in rows}

    def sample(cid: str) -> dict[str, Any] | None:
        r = by_id.get(cid)
        if not r:
            return None
        return {
            "id": r.get("id"),
            "beta_id": r.get("beta_id"),
            "alias_of": r.get("alias_of"),
            "file": r.get("file"),
            "tickers": r.get("tickers"),
            "layer": r.get("layer"),
            "has_overview": r.get("has_overview"),
            "filing_backed": r.get("filing_backed"),
        }

    return {
        "n": data.get("n") or len(rows),
        "counts": data.get("counts") or {},
        "aliases": data.get("aliases") or {},
        "samples": {
            "iamgold": sample("iamgold"),
            "probe-gold": sample("probe-gold"),
            "troilus-mining": sample("troilus-mining"),
            "kenorland-minerals": sample("kenorland-minerals"),
        },
    }


def build_calls(summary: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "id": "policy:shell_fields",
            "label": "claims_beta_shell_fields_v1",
            "questions": ["shell_fields"],
            "state": {
                "locked_decision": "claims_id_tickers_no_ounces",
                "do_not": "invent ounces or regenerate existing shells",
                "index": summary["counts"],
            },
        },
        {
            "id": "policy:map_extent",
            "label": "claims_beta_map_extent_v1",
            "questions": ["map_extent"],
            "state": {
                "locked_decision": "company_footprint",
                "preview_loads": ["claims/overview.geojson", "catalog mines", "profile assets"],
                "must_not_load": ["per-company extracts", "loadAllExtracts"],
            },
        },
        {
            "id": "policy:empty_profile",
            "label": "claims_beta_empty_profile_v1",
            "questions": ["empty_profile"],
            "state": {
                "locked_decision": "hide_empty_show_map",
                "example": "kenorland-minerals mcap shell — no ounces, has overview cells",
            },
        },
        {
            "id": "policy:id_alias",
            "label": "claims_beta_id_alias_v1",
            "questions": ["id_alias"],
            "state": {
                "locked_decision": "alias_to_existing",
                "claims_id": "troilus-mining",
                "existing_file": "beta/troilus.json",
                "existing_id": "troilus",
            },
        },
        {
            "id": "policy:preview_light",
            "label": "claims_beta_preview_light_v1",
            "questions": ["preview_light"],
            "state": {
                "locked_decision": True,
                "overview_first_after": "261-geojson stall",
            },
        },
    ]


def build_packet(root: Path) -> dict[str, Any]:
    data = load_index(root)
    summary = index_summary(data)
    return {
        "schema": "qc-pr-merge-gate-v1",
        "work": "B",
        "pr_topic": "claims-map issuer profiles + light map preview",
        "generated": utc_now(),
        "model": "jev-latest",
        "api": "typesafe-sdk TypeSafeClient.system_one (Choice / Noul / Score)",
        "not": "Jev Bot chat",
        "index": {
            "json": "beta/claims-publics.json",
            **summary,
        },
        "question_specs": QUESTION_SPECS,
        "calls": build_calls(summary),
        "gate": {
            "pass_if": [
                "shell_fields choice is claims_id_tickers_no_ounces (if Jev ran)",
                "map_extent choice is company_footprint (if Jev ran)",
                "empty_profile choice is hide_empty_show_map (if Jev ran)",
                "id_alias choice is alias_to_existing (if Jev ran)",
                "preview_light noul >= 0.55 (if Jev ran)",
                "claims-publics.json lists all catalog ids; troilus-mining aliases to troilus",
                "beta.html map preview must not call loadAllExtracts or fetch extracts",
            ],
            "fail_closed_without_key": False,
            "fail_on_missing_answer": True,
            "note": (
                "Cloud agent prepares the packet. QC box with TYPESAFE_API_KEY "
                "runs the same script to fill judgments."
            ),
        },
        "box_commands": [
            "set -a && source /home/box/shared/typesafe/env && set +a",
            "python3 -m pip install -q typesafe-sdk",
            "python3 scripts/run_claims_beta_gate.py --require-jev",
        ],
    }


def _deterministic(packet: dict[str, Any], checks: qc_gate.Checks) -> None:
    idx = packet.get("index") or {}
    checks.add("index_present", int(idx.get("n") or 0) > 0, f"n={idx.get('n')}")
    samples = idx.get("samples") or {}
    checks.add("iamgold_listed", bool(samples.get("iamgold")), str(samples.get("iamgold")))
    checks.add("probe_gold_listed", bool(samples.get("probe-gold")), str(samples.get("probe-gold")))
    tro = samples.get("troilus-mining") or {}
    checks.add(
        "troilus_alias",
        tro.get("alias_of") == "troilus" and tro.get("file") == "beta/troilus.json",
        str(tro),
    )


EXPECTED_CHOICES = {
    "shell_fields": ("policy:shell_fields", "claims_id_tickers_no_ounces"),
    "map_extent": ("policy:map_extent", "company_footprint"),
    "empty_profile": ("policy:empty_profile", "hide_empty_show_map"),
    "id_alias": ("policy:id_alias", "alias_to_existing"),
}


def _jev_checks(look: qc_gate.Lookup, checks: qc_gate.Checks) -> None:
    for qid, (cid, want) in EXPECTED_CHOICES.items():
        got = look.choice(cid, qid)
        checks.add(qid, got in {None, want}, f"choice={got}")
    p = look.noul("policy:preview_light", "preview_light")
    checks.add("preview_light", p is None or p >= 0.55, f"noul={p}")


def evaluate_gate(packet: dict[str, Any], judgments: dict[str, Any]) -> dict[str, Any]:
    return qc_gate.evaluate(packet, judgments, _deterministic, _jev_checks)


def main(argv: list[str] | None = None, jev_module: Any = None) -> int:
    return qc_gate.main(
        argv,
        description="Work B PR merge gate / Jev packet",
        default_root=ROOT,
        packet_name=PACKET.name,
        judgments_name=JUDGMENTS.name,
        build_packet=build_packet,
        evaluate_gate=evaluate_gate,
        specs=QUESTION_SPECS,
        summary=lambda pk: f"calls={len(pk['calls'])} index_n={pk['index']['n']}",
        jev_module=jev_module,
    )


if __name__ == "__main__":
    raise SystemExit(main())
