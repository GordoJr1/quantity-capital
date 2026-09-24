"""Shared TypeSafe Jev merge-gate runner for the run_*_gate.py scripts.

Each gate supplies its packet builder, question specs, and a
deterministic + Jev check function. This module owns the rules that
must be identical across gates:

- Deterministic checks always run, including `--packet-only` (CI).
- `ran` is true only when at least one call came back with answers.
- Once Jev was attempted, every packet question that has no answer is a
  failed check, and a run where Jev never answered exits 2.
- The committed judgments file is replaced only by a complete run (every
  call answered). Keyless, packet-only, or partial runs keep prior judgments.
- Errors are recorded as the exception type only, never the message.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
SQ_JEV = HERE / "qc_sqlite"
for _p in (HERE, SQ_JEV):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from qc_io import atomic_write_json  # noqa: E402

BOX_TYPESAFE_DIR = Path("/home/box/shared/typesafe")


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hydrate_questions(specs: dict[str, dict[str, Any]], names: list[str]) -> dict[str, Any]:
    from typesafe_sdk import Choice, Noul, Score

    builders = {"Choice": Choice, "Noul": Noul, "Score": Score}
    out = {}
    for name in names:
        spec = specs[name]
        kwargs: dict[str, Any] = {"instructions": spec["instructions"]}
        if "criteria" in spec:
            kwargs["criteria"] = spec["criteria"]
        out[name] = builders[spec["type"]](**kwargs)
    return out


def try_import_jev():
    try:
        import jev  # type: ignore

        return jev
    except ImportError:
        return None


def sdk_present() -> bool:
    try:
        import typesafe_sdk  # noqa: F401

        return True
    except ImportError:
        return False


def not_attempted(reason: str) -> dict[str, Any]:
    return {"ran": False, "attempted": False, "complete": False, "reason": reason, "model": None, "calls": []}


def run_jev(
    packet: dict[str, Any],
    specs: dict[str, dict[str, Any]],
    *,
    keep_state: bool = False,
    jev_module: Any = None,
) -> dict[str, Any]:
    injected = jev_module is not None
    jev = jev_module if injected else try_import_jev()
    have_sdk = sdk_present()
    if jev is None or not jev.key_present() or (not injected and not have_sdk):
        out = not_attempted("no_typesafe_key_or_sdk")
        out["environment"] = {
            "typesafe_sdk": have_sdk,
            "key_present": bool(jev and jev.key_present()) if jev else False,
            "box_typesafe_dir": str(BOX_TYPESAFE_DIR),
            "box_typesafe_dir_exists": BOX_TYPESAFE_DIR.exists(),
        }
        return out

    calls = packet.get("calls") or []
    results = []
    errors = 0
    model = None
    for call in calls:
        base = {"id": call["id"], "label": call["label"]}
        if keep_state:
            base["state"] = call.get("state")
        try:
            questions = (
                hydrate_questions(specs, call["questions"])
                if have_sdk
                else {name: specs[name] for name in call["questions"]}
            )
            packed = jev.ask(call["state"], questions, label=call["label"])
        except Exception as exc:
            errors += 1
            results.append({**base, "error": type(exc).__name__})
            continue
        if not (packed or {}).get("answers"):
            errors += 1
            results.append({**base, "error": "EmptyAnswers"})
            continue
        model = packed.get("model") or model
        results.append({**base, **packed})

    answered = len(results) - errors
    return {
        "ran": answered > 0,
        "attempted": True,
        "complete": bool(calls) and errors == 0,
        "reason": None if answered else "no_answers",
        "model": model,
        "n": answered,
        "errors": errors,
        "calls": results,
        "generated": utc_now(),
    }


def answers_by_id(judgments: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in judgments.get("calls") or [] if c.get("answers")}


class Lookup:
    def __init__(self, judgments: dict[str, Any]):
        self.by_id = answers_by_id(judgments)

    def _ans(self, cid: str, qid: str) -> dict[str, Any]:
        return ((self.by_id.get(cid) or {}).get("answers") or {}).get(qid) or {}

    def choice(self, cid: str, qid: str) -> str | None:
        return self._ans(cid, qid).get("choice")

    def noul(self, cid: str, qid: str) -> float | None:
        v = self._ans(cid, qid).get("noul")
        return float(v) if v is not None else None

    def score(self, cid: str, qid: str) -> float | None:
        v = self._ans(cid, qid).get("score")
        return float(v) if v is not None else None

    def has(self, cid: str, qid: str) -> bool:
        return qid in ((self.by_id.get(cid) or {}).get("answers") or {})


class Checks:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.items.append({"name": name, "ok": bool(ok), "detail": detail})

    def failed(self) -> list[dict[str, Any]]:
        return [c for c in self.items if not c["ok"]]


def evaluate(
    packet: dict[str, Any],
    judgments: dict[str, Any],
    deterministic: Callable[[dict[str, Any], Checks], None],
    jev_checks: Callable[[Lookup, Checks], None],
    *,
    skipped_note: str = "Run on the QC box to fill judgments.",
) -> dict[str, Any]:
    checks = Checks()
    deterministic(packet, checks)

    attempted = bool(judgments.get("attempted") or judgments.get("ran"))
    if not attempted:
        checks.add(
            "jev_api",
            True,
            "packet prepared; TypeSafe Jev API not called in this environment "
            f"({judgments.get('reason')}). {skipped_note}",
        )
        failed = checks.failed()
        return {"pass": not failed, "jev_ran": False, "checks": checks.items, "failed": [c["name"] for c in failed]}

    if not judgments.get("ran"):
        checks.add("jev_api", False, f"Jev was called but never answered ({judgments.get('reason')})")

    look = Lookup(judgments)
    for call in packet.get("calls") or []:
        for qid in call.get("questions") or []:
            if not look.has(call["id"], qid):
                checks.add(f"answer:{call['id']}:{qid}", False, "no Jev answer")
    jev_checks(look, checks)

    failed = checks.failed()
    return {
        "pass": not failed,
        "jev_ran": bool(judgments.get("ran")),
        "model": judgments.get("model"),
        "checks": checks.items,
        "failed": [c["name"] for c in failed],
    }


def load_prev(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def main(
    argv: list[str] | None,
    *,
    description: str,
    default_root: Path,
    packet_name: str,
    judgments_name: str,
    build_packet: Callable[[Path], dict[str, Any]],
    evaluate_gate: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    specs: dict[str, dict[str, Any]],
    summary: Callable[[dict[str, Any]], str],
    keep_state: bool = False,
    extra_fields: dict[str, Any] | None = None,
    jev_module: Any = None,
) -> int:
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--root", type=Path, default=default_root)
    parser.add_argument("--packet-only", action="store_true", help="Write packet and run deterministic checks; do not call TypeSafe")
    parser.add_argument("--require-jev", action="store_true", help="Exit 2 unless every Jev call answered")
    args = parser.parse_args(argv)
    root = args.root
    packet = build_packet(root)
    atomic_write_json(root / "scripts" / packet_name, packet)
    print(f"wrote {packet_name} {summary(packet)}")

    judgments_path = root / "scripts" / judgments_name
    prev = load_prev(judgments_path)

    if args.packet_only:
        gate = evaluate_gate(packet, not_attempted("packet_only"))
        if prev is None:
            atomic_write_json(judgments_path, {**not_attempted("packet_only"), "generated": utc_now()})
        print(json.dumps({"jev": {"ran": False, "reason": "packet_only"}, "gate": gate}, indent=2))
        return 0 if gate.get("pass") else 1

    judgments = run_jev(packet, specs, keep_state=keep_state, jev_module=jev_module)
    if extra_fields and judgments.get("attempted"):
        judgments.update(extra_fields)
    if judgments.get("complete"):
        atomic_write_json(judgments_path, judgments)
        print(f"wrote {judgments_name} n={judgments.get('n')}")
    elif prev is None and not judgments.get("attempted"):
        atomic_write_json(judgments_path, {**judgments, "generated": utc_now()})
    else:
        print(f"kept existing {judgments_name}; this run was not complete")

    gate = evaluate_gate(packet, judgments)
    print(
        json.dumps(
            {
                "jev": {
                    "ran": judgments.get("ran"),
                    "complete": judgments.get("complete"),
                    "errors": judgments.get("errors"),
                    "reason": judgments.get("reason"),
                    "model": judgments.get("model"),
                },
                "gate": gate,
            },
            indent=2,
        )
    )
    if judgments.get("attempted") and not judgments.get("ran"):
        print("Jev was called but never answered.", file=sys.stderr)
        return 2
    if args.require_jev and not judgments.get("complete"):
        print("Jev API did not answer every call (no typesafe-sdk / TYPESAFE_API_KEY, or call errors).", file=sys.stderr)
        return 2
    return 0 if gate.get("pass") else 1
