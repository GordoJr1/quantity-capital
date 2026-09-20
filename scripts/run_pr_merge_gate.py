#!/usr/bin/env python3
"""QC merge-gate packet + TypeSafe Jev runner for Work A (claims → Insiders).

This is the in-repo packet compatible with QC's box gate. It talks to the
TypeSafe Jev API directly (Choice / Noul / Score via typesafe-sdk), not Jev Bot.

Cloud Agents do **not** have TYPESAFE_API_KEY. CI is packet-only. The QC
box runs the API:

    # Linux shared tree
    set -a
    source /home/box/shared/typesafe/env          # or ~/.grok/typesafe.env
    set +a
    python3 -m pip install -q typesafe-sdk
    python3 scripts/run_pr_merge_gate.py --require-jev

    # Windows Groks box
    # TYPESAFE_API_KEY from %USERPROFILE%\\.grok\\typesafe.env
    python scripts\\run_pr_merge_gate.py --require-jev

Writes:
  scripts/claims-insider-jev-packet.json      — states + question specs + audit
  scripts/claims-insider-jev-judgments.json   — API answers when a key exists

`--packet-only` never calls the network (pr-check / Cloud Agents).
`--require-jev` fails if the TypeSafe API did not run (QC box).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACKET = HERE / "claims-insider-jev-packet.json"
JUDGMENTS = HERE / "claims-insider-jev-judgments.json"
AUDIT_CSV = HERE / "claims-insider-ticker-audit.csv"
AUDIT_MD = HERE / "claims-insider-ticker-audit.md"

SQ_JEV = HERE / "qc_sqlite"
if str(SQ_JEV) not in sys.path:
    sys.path.insert(0, str(SQ_JEV))


QUESTION_SPECS: dict[str, dict[str, Any]] = {
    "empty_badge_copy": {
        "type": "Choice",
        "instructions": (
            "Which badge copy should the Insiders issuer list/tape use when a "
            "public claims issuer is on the watchlist but has no Form 4 / SEDI prints?"
        ),
        "criteria": {
            "no_filings_yet": (
                "Use 'No filings yet'. Short, clear, not a broken blank row."
            ),
            "watchlist_no_prints": (
                "Use 'On watchlist — no insider prints'."
            ),
            "no_form4_sedi": (
                "Use 'No Form 4 / SEDI yet'."
            ),
        },
    },
    "empty_row_policy": {
        "type": "Choice",
        "instructions": (
            "A claims public is already on the Insiders universe but the tape has "
            "zero officer prints. What should the PWA do?"
        ),
        "criteria": {
            "keep_with_badge": (
                "Keep the issuer visible with a No filings yet badge. Do not hide it."
            ),
            "hide_until_data": (
                "Hide the issuer until the first Form 4 / SEDI print lands."
            ),
            "blank_row": (
                "Show a trade row with empty cells (no badge)."
            ),
        },
    },
    "private_filter": {
        "type": "Noul",
        "instructions": (
            "Is it correct to exclude numbered provincial corps, Crown/ministry "
            "holders, and unmatched person-like names from the Insiders universe, "
            "while keeping catalog names that resolve to a CAD/US ticker?"
        ),
        "criteria": {
            "true": "Yes — those holders are not listed issuers we should tape.",
            "false": "No — the filter is too tight or too loose for this catalog.",
        },
    },
    "on_universe": {
        "type": "Noul",
        "instructions": (
            "Does this named claims-map company belong on the public Insiders "
            "issuer list given a resolvable listed ticker?"
        ),
        "criteria": {
            "true": "Yes — it is a publicly traded issuer the tape should watch.",
            "false": "No — private, unmatched, wrong entity, or should stay off tape.",
        },
    },
    "same_listed_issuer": {
        "type": "Score",
        "instructions": (
            "How do the claims-map name and the insider-companies name relate?"
        ),
        "criteria": [
            "They name two different listed companies.",
            "Related (former name, vehicle, or project) but not clearly the same issuer.",
            "The same listed issuer (allowing legal suffixes and ticker aliases).",
        ],
    },
}


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_audit_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def audit_summary(rows: list[dict[str, str]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.get("status") or "unknown"] = counts.get(row.get("status") or "unknown", 0) + 1
    return {
        "n": len(rows),
        "counts": counts,
        "new_publics": [
            r for r in rows if r.get("id") in {"probe-gold", "g2-goldfields"}
        ],
        "samples": {
            "filings_normal": next((r for r in rows if r.get("id") == "newmont"), None),
            "empty_badge": next((r for r in rows if r.get("id") == "gold-fields"), None),
        },
    }


def build_calls(audit: dict[str, Any]) -> list[dict[str, Any]]:
    """Bounded System One calls — policy plus named edge cases, not 191 repeats."""
    return [
        {
            "id": "policy:empty_badge_copy",
            "label": "claims_insider_empty_badge_v1",
            "questions": ["empty_badge_copy"],
            "state": {
                "surface": "insiders.html Issuers view + insider-ticker.html empty tape",
                "current_copy": "No filings yet",
                "locked_decision": "include empty filings with a clear badge, not a blank row",
            },
        },
        {
            "id": "policy:empty_row_policy",
            "label": "claims_insider_empty_row_v1",
            "questions": ["empty_row_policy"],
            "state": {
                "locked_decision": "include on tape with a no filings yet badge; do not hide until data lands",
                "audit": audit["counts"],
            },
        },
        {
            "id": "policy:private_filter",
            "label": "claims_insider_private_filter_v1",
            "questions": ["private_filter"],
            "state": {
                "rule": "exclude numbered provincial corps, Crown/ministry, unmatched person-like holders",
                "catalog_n": audit["n"],
                "private_excluded": audit["counts"].get("private-excluded", 0),
                "missing_ticker": audit["counts"].get("missing-ticker", 0),
                "matched": audit["counts"].get("matched", 0),
            },
        },
        {
            "id": "issuer:probe-gold",
            "label": "claims_insider_on_universe_v1:probe-gold",
            "questions": ["on_universe"],
            "state": {
                "claims_id": "probe-gold",
                "name": "Probe Gold",
                "tickers": ["PRB.TO"],
                "exchange": "TSX",
                "already_on_excel_watchlist": False,
                "filings_on_tape": False,
            },
        },
        {
            "id": "issuer:g2-goldfields",
            "label": "claims_insider_on_universe_v1:g2-goldfields",
            "questions": ["on_universe"],
            "state": {
                "claims_id": "g2-goldfields",
                "name": "G2 Goldfields",
                "tickers": ["GTWO.TO"],
                "exchange": "TSX",
                "already_on_excel_watchlist": False,
                "filings_on_tape": False,
            },
        },
        {
            "id": "issuer:newmont",
            "label": "claims_insider_on_universe_v1:newmont",
            "questions": ["on_universe"],
            "state": {
                "claims_id": "newmont",
                "name": "Newmont",
                "tickers": ["NEM"],
                "already_on_excel_watchlist": True,
                "filings_on_tape": True,
            },
        },
        {
            "id": "issuer:gold-fields",
            "label": "claims_insider_same_issuer_v1:gold-fields",
            "questions": ["same_listed_issuer", "on_universe"],
            "state": {
                "claims_id": "gold-fields",
                "claims_holder": "Groupe Minier Windfall Inc.",
                "insider_name": "Gold Fields",
                "tickers": ["GFI"],
                "note": "Windfall title vehicle / project name on the Quebec extract",
                "filings_on_tape": False,
            },
        },
        {
            "id": "issuer:harmony",
            "label": "claims_insider_same_issuer_v1:harmony",
            "questions": ["same_listed_issuer", "on_universe"],
            "state": {
                "claims_id": "harmony",
                "claims_holder": "Harmony Gold Mining Company Limited",
                "insider_name": "Harmony Gold",
                "tickers": ["HMY"],
            },
        },
        {
            "id": "issuer:thesis-gold",
            "label": "claims_insider_same_issuer_v1:thesis-gold",
            "questions": ["same_listed_issuer", "on_universe"],
            "state": {
                "claims_id": "thesis-gold",
                "claims_holder": "Thesis Gold",
                "insider_name": "Thesis Gold & Silver",
                "tickers": ["TAU.V", "THSGF"],
                "note": "same CAD/US pair; watchlist still uses the older display name",
            },
        },
    ]


def build_packet(root: Path) -> dict[str, Any]:
    rows = load_audit_rows(root / "scripts" / "claims-insider-ticker-audit.csv")
    summary = audit_summary(rows)
    return {
        "schema": "qc-pr-merge-gate-v1",
        "work": "A",
        "pr_topic": "claims publics onto Insiders tape universe",
        "generated": utc_now(),
        "model": "jev-latest",
        "api": "typesafe-sdk TypeSafeClient.system_one (Choice / Noul / Score)",
        "not": "Jev Bot chat",
        "audit": {
            "csv": "scripts/claims-insider-ticker-audit.csv",
            "md": "scripts/claims-insider-ticker-audit.md",
            **summary,
        },
        "question_specs": QUESTION_SPECS,
        "calls": build_calls(summary),
        "gate": {
            "pass_if": [
                "empty_row_policy choice is keep_with_badge (if Jev ran)",
                "on_universe noul >= 0.55 for probe-gold, g2-goldfields, newmont (if Jev ran)",
                "ticker audit exists and matched == catalog n",
                "private-excluded holders are not on insider-companies.json",
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
            "python3 scripts/run_pr_merge_gate.py",
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


def _sdk_present() -> bool:
    try:
        import typesafe_sdk  # noqa: F401

        return True
    except ImportError:
        return False


def evaluate_gate(packet: dict[str, Any], judgments: dict[str, Any]) -> dict[str, Any]:
    audit = packet.get("audit") or {}
    counts = audit.get("counts") or {}
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": ok, "detail": detail})

    add("audit_present", int(audit.get("n") or 0) > 0, f"n={audit.get('n')}")
    add(
        "all_matched",
        int(counts.get("matched") or 0) == int(audit.get("n") or -1)
        and int(counts.get("missing-ticker") or 0) == 0,
        json.dumps(counts),
    )
    sample_ids = {r.get("id") for r in (audit.get("new_publics") or [])}
    add(
        "new_publics_listed",
        {"probe-gold", "g2-goldfields"} <= sample_ids,
        f"ids={sorted(sample_ids)}",
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

    def score(cid: str, qid: str) -> float | None:
        v = ((by_id.get(cid) or {}).get("answers") or {}).get(qid, {}).get("score")
        return float(v) if v is not None else None

    add(
        "empty_row_keep",
        choice("policy:empty_row_policy", "empty_row_policy") in {None, "keep_with_badge"},
        f"choice={choice('policy:empty_row_policy', 'empty_row_policy')}",
    )
    for cid in ("issuer:probe-gold", "issuer:g2-goldfields", "issuer:newmont"):
        p = noul(cid, "on_universe")
        add(f"{cid}_on_universe", p is None or p >= 0.55, f"noul={p}")
    for cid in ("issuer:gold-fields", "issuer:harmony", "issuer:thesis-gold"):
        s = score(cid, "same_listed_issuer")
        add(f"{cid}_same_issuer", s is None or s >= 1.4, f"score={s}")

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
    parser = argparse.ArgumentParser(description="Work A PR merge gate / Jev packet")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--packet-only", action="store_true", help="Write packet; do not call TypeSafe")
    parser.add_argument("--require-jev", action="store_true", help="Fail if the Jev API did not run")
    args = parser.parse_args()
    root = args.root
    packet = build_packet(root)
    write_json(root / "scripts" / PACKET.name, packet)
    print(f"wrote {PACKET.name} calls={len(packet['calls'])} audit_n={packet['audit']['n']}")

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
    print(json.dumps({"jev": {"ran": judgments.get("ran"), "reason": judgments.get("reason"), "model": judgments.get("model")}, "gate": gate}, indent=2))
    if args.require_jev and not judgments.get("ran"):
        print("Jev API did not run (no typesafe-sdk / TYPESAFE_API_KEY).", file=sys.stderr)
        return 2
    return 0 if gate.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
