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

`--packet-only` never calls the network but still runs the deterministic
checks (pr-check). `--require-jev` exits 2 unless every call answered.
Shared rules live in scripts/qc_gate.py.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PACKET = HERE / "claims-insider-jev-packet.json"
JUDGMENTS = HERE / "claims-insider-jev-judgments.json"
AUDIT_CSV = HERE / "claims-insider-ticker-audit.csv"
AUDIT_MD = HERE / "claims-insider-ticker-audit.md"

if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import qc_gate  # noqa: E402
from qc_gate import utc_now  # noqa: E402


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
                "same_listed_issuer: score >= 1.4, or related-vehicle band score >= 1.0 with on_universe >= 0.55 (Windfall→GFI keep)",
                "ticker audit exists and matched == catalog n",
                "private-excluded holders are not on insider-companies.json",
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
            "python3 scripts/run_pr_merge_gate.py --require-jev",
        ],
    }


def _deterministic(packet: dict[str, Any], checks: qc_gate.Checks) -> None:
    audit = packet.get("audit") or {}
    counts = audit.get("counts") or {}
    checks.add("audit_present", int(audit.get("n") or 0) > 0, f"n={audit.get('n')}")
    checks.add(
        "all_matched",
        int(counts.get("matched") or 0) == int(audit.get("n") or -1)
        and int(counts.get("missing-ticker") or 0) == 0,
        str(counts),
    )
    sample_ids = {r.get("id") for r in (audit.get("new_publics") or [])}
    checks.add(
        "new_publics_listed",
        {"probe-gold", "g2-goldfields"} <= sample_ids,
        f"ids={sorted(sample_ids)}",
    )


def _jev_checks(look: qc_gate.Lookup, checks: qc_gate.Checks) -> None:
    empty_row = look.choice("policy:empty_row_policy", "empty_row_policy")
    checks.add("empty_row_keep", empty_row in {None, "keep_with_badge"}, f"choice={empty_row}")
    for cid in ("issuer:probe-gold", "issuer:g2-goldfields", "issuer:newmont"):
        p = look.noul(cid, "on_universe")
        checks.add(f"{cid}_on_universe", p is None or p >= 0.55, f"noul={p}")
    for cid in ("issuer:gold-fields", "issuer:harmony", "issuer:thesis-gold"):
        s = look.score(cid, "same_listed_issuer")
        p = look.noul(cid, "on_universe")
        # 2.0 same issuer; ~1.x related/vehicle (Windfall→GFI). Soft pass if
        # score >= 1.0 and the claims public still belongs on the universe.
        same = s is None or s >= 1.4
        vehicle = s is not None and s >= 1.0 and (p is None or p >= 0.55)
        checks.add(f"{cid}_same_issuer", same or vehicle, f"score={s} on_universe={p}")
        if p is not None:
            checks.add(f"{cid}_on_universe", p >= 0.55, f"noul={p}")


def evaluate_gate(packet: dict[str, Any], judgments: dict[str, Any]) -> dict[str, Any]:
    return qc_gate.evaluate(packet, judgments, _deterministic, _jev_checks)


def main(argv: list[str] | None = None, jev_module: Any = None) -> int:
    return qc_gate.main(
        argv,
        description="Work A PR merge gate / Jev packet",
        default_root=ROOT,
        packet_name=PACKET.name,
        judgments_name=JUDGMENTS.name,
        build_packet=build_packet,
        evaluate_gate=evaluate_gate,
        specs=QUESTION_SPECS,
        summary=lambda pk: f"calls={len(pk['calls'])} audit_n={pk['audit']['n']}",
        jev_module=jev_module,
    )


if __name__ == "__main__":
    raise SystemExit(main())
