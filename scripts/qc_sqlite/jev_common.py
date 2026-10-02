"""Shared Jev (TypeSafe System One) helpers for the QC triage scripts.

Every triage script calls Jev through `ask()` so that:
  * the model is pinned to JEV_MODEL (jev-1.13.0), never an alias;
  * all of a script's questions go out in ONE system_one call;
  * each call appends one JSON line (model + token usage + cost) to jev_usage.log.

Never print or log TYPESAFE_API_KEY.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

try:
    from typesafe_sdk import Choice, Noul, Score, TypeSafeClient
except ImportError:  # pragma: no cover
    Choice = Noul = Score = TypeSafeClient = None  # type: ignore

ET = ZoneInfo("America/New_York")
HERE = Path(__file__).resolve().parent
JEV_MODEL = "jev-1.13.0"
USAGE_LOG = HERE / "jev_usage.log"
USD_PER_INPUT_TOKEN = 0.042 / 1_000_000  # docs.typesafe.ai/models: $0.042 per Mtok input, output free


def require_sdk() -> None:
    if TypeSafeClient is None:
        raise ImportError(
            "typesafe-sdk required: use /home/box/shared/typesafe/.venv/bin/python"
        )
    if not os.environ.get("TYPESAFE_API_KEY"):
        raise RuntimeError(
            "TYPESAFE_API_KEY missing: set -a; source /home/box/shared/typesafe/env; set +a"
        )


def log_usage(script: str, response: Any, *, n_questions: int, tag: str = "") -> dict[str, Any]:
    usage = getattr(response, "usage", None)
    inp = getattr(usage, "input_tokens", None)
    out = getattr(usage, "output_tokens", None)
    row = {
        "ts": datetime.now(ET).isoformat(timespec="seconds"),
        "script": script,
        "tag": tag,
        "model_requested": JEV_MODEL,
        "model": getattr(response, "model", None),
        "n_questions": n_questions,
        "input_tokens": inp,
        "output_tokens": out,
        "cost_usd": round((inp or 0) * USD_PER_INPUT_TOKEN, 8),
    }
    with USAGE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    return row


def ask(
    state: Any,
    questions: Mapping[str, Any],
    *,
    script: str,
    tag: str = "",
    client: Any = None,
) -> Any:
    """One pinned System One call for all of a script's questions; logs usage."""
    owns = client is None
    if owns:
        require_sdk()
        client = TypeSafeClient(model=JEV_MODEL)
    try:
        resp = client.system_one(state, dict(questions), model=JEV_MODEL)
    finally:
        if owns and hasattr(client, "close"):
            client.close()
    log_usage(script, resp, n_questions=len(questions), tag=tag)
    return resp


def usage_totals(path: Path | None = None) -> dict[str, Any]:
    p = path or USAGE_LOG
    calls = inp = out = 0
    cost = 0.0
    models: set[str] = set()
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            calls += 1
            inp += int(r.get("input_tokens") or 0)
            out += int(r.get("output_tokens") or 0)
            cost += float(r.get("cost_usd") or 0.0)
            if r.get("model"):
                models.add(str(r["model"]))
    return {"calls": calls, "input_tokens": inp, "output_tokens": out,
            "cost_usd": round(cost, 6), "models": sorted(models)}


if __name__ == "__main__":
    print(json.dumps(usage_totals(), indent=2))
