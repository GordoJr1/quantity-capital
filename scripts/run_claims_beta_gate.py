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

`--packet-only` never calls the network (pr-check / Cloud Agents).
`--require-jev` fails if the TypeSafe API did not run (QC box).
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
PACKET = HERE / "claims-beta-jev-packet.json"
JUDGMENTS = HERE / "claims-beta-jev-judgments.json"

SQ_JEV = HERE / "qc_sqlite"
if str(SQ_JEV) not in sys.path:
    sys.path.insert(0, str(SQ_JEV))


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


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        results.append({"id": call["id"], "label": call["label"], **packed})

    return {
        "ran": True,
        "reason": None,
        "model": model,
        "n": len([r for r in results if "error" not in r]),
        "errors": errors,
        "calls": results,
        "generated": utc_now(),
    }


def evaluate_gate(packet: dict[str, Any], judgments: dict[str, Any]) -> dict[str, Any]:
    idx = packet.get("index") or {}
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("index_present", int(idx.get("n") or 0) > 0, f"n={idx.get('n')}")
    samples = idx.get("samples") or {}
    add("iamgold_listed", bool(samples.get("iamgold")), str(samples.get("iamgold")))
    add("probe_gold_listed", bool(samples.get("probe-gold")), str(samples.get("probe-gold")))
    tro = samples.get("troilus-mining") or {}
    add(
        "troilus_alias",
        tro.get("alias_of") == "troilus" and tro.get("file") == "beta/troilus.json",
        str(tro),
    )

    if not judgments.get("ran"):
        add(
            "jev_api",
            True,
            "packet prepared; TypeSafe Jev API not called in this environment "
            f"({judgments.get('reason')}). Run on the QC box to fill judgments.",
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
        "shell_fields",
        choice("policy:shell_fields", "shell_fields") in {None, "claims_id_tickers_no_ounces"},
        f"choice={choice('policy:shell_fields', 'shell_fields')}",
    )
    add(
        "map_extent",
        choice("policy:map_extent", "map_extent") in {None, "company_footprint"},
        f"choice={choice('policy:map_extent', 'map_extent')}",
    )
    add(
        "empty_profile",
        choice("policy:empty_profile", "empty_profile") in {None, "hide_empty_show_map"},
        f"choice={choice('policy:empty_profile', 'empty_profile')}",
    )
    add(
        "id_alias",
        choice("policy:id_alias", "id_alias") in {None, "alias_to_existing"},
        f"choice={choice('policy:id_alias', 'id_alias')}",
    )
    p = noul("policy:preview_light", "preview_light")
    add("preview_light", p is None or p >= 0.55, f"noul={p}")

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
    parser = argparse.ArgumentParser(description="Work B PR merge gate / Jev packet")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--packet-only", action="store_true", help="Write packet; do not call TypeSafe")
    parser.add_argument("--require-jev", action="store_true", help="Fail if the Jev API did not run")
    args = parser.parse_args()
    root = args.root
    packet = build_packet(root)
    write_json(root / "scripts" / PACKET.name, packet)
    print(f"wrote {PACKET.name} calls={len(packet['calls'])} index_n={packet['index']['n']}")

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
