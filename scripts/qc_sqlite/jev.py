"""TypeSafe / Jev helpers for schema, claim-company links, and calc flags.

Never prints or writes the API key. Cache is judgments only.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

from paths import JEV_CACHE, TYPESAFE_ENV

MODEL = "jev-latest"

LINK_LEVELS = [
    "They name two different companies or issuers.",
    (
        "They are related (subsidiary, project vehicle, former name, or overlapping "
        "holder) but not clearly the same listed issuer."
    ),
    (
        "They are the same listed issuer, or the claim holder is a wholly owned "
        "title vehicle for that issuer."
    ),
]
LINK_OUTCOME = {0: "leave_unlinked", 1: "curator", 2: "same_entity"}

_client = None
_client_lock = threading.Lock()
_cache_lock = threading.Lock()
_cache: dict[str, Any] | None = None


def load_typesafe_env() -> None:
    if os.environ.get("TYPESAFE_API_KEY"):
        return
    if not TYPESAFE_ENV.exists():
        return
    for line in TYPESAFE_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def key_present() -> bool:
    load_typesafe_env()
    return bool(os.environ.get("TYPESAFE_API_KEY"))


def _cache_load() -> dict[str, Any]:
    global _cache
    if _cache is not None:
        return _cache
    if JEV_CACHE.exists():
        _cache = json.loads(JEV_CACHE.read_text(encoding="utf-8"))
    else:
        _cache = {}
    return _cache


def _cache_save() -> None:
    JEV_CACHE.parent.mkdir(parents=True, exist_ok=True)
    tmp = JEV_CACHE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_cache_load(), indent=2), encoding="utf-8")
    tmp.replace(JEV_CACHE)


def _digest(state: Any, questions_label: str) -> str:
    blob = json.dumps({"state": state, "q": questions_label, "model": MODEL}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _answers_dict(response: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for qid, ans in response.answers.items():
        row: dict[str, Any] = {"type": getattr(ans, "type", None)}
        if hasattr(ans, "choice"):
            row["choice"] = ans.choice
            row["confidence"] = ans.confidence
            row["probabilities"] = dict(ans.probabilities)
        elif hasattr(ans, "noul"):
            row["noul"] = ans.noul
        elif hasattr(ans, "score"):
            row["score"] = ans.score
            row["confidence"] = ans.confidence
            row["probabilities"] = {str(k): v for k, v in dict(ans.probabilities).items()}
        out[qid] = row
    usage = getattr(response, "usage", None)
    return {
        "model": getattr(response, "model", MODEL),
        "answers": out,
        "input_tokens": getattr(usage, "input_tokens", None) if usage else None,
        "output_tokens": getattr(usage, "output_tokens", None) if usage else None,
    }


def _get_client():
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is not None:
            return _client
        load_typesafe_env()
        from typesafe_sdk import TypeSafeClient

        _client = TypeSafeClient(timeout=120.0)
        return _client


def ask(state: Any, questions: dict, *, label: str) -> dict[str, Any]:
    """One System One call, cached. `questions` is a map of Choice/Noul/Score."""
    key = _digest(state, label)
    cache = _cache_load()
    hit = cache.get(key)
    if hit:
        return hit
    client = _get_client()
    response = client.system_one(state=state, questions=questions, model=MODEL)
    packed = _answers_dict(response)
    with _cache_lock:
        cache[key] = packed
        _cache_save()
    return packed


def round_link_outcome(score_value: float) -> str:
    return LINK_OUTCOME[min(int(score_value + 0.5), len(LINK_LEVELS) - 1)]


def schema_questions():
    from typesafe_sdk import Choice

    return {
        "claims_grain": Choice(
            instructions=(
                "For a first SQLite brain of mining claims linked to listed companies, "
                "which grain should claim rows use given that GeoJSON polygons are huge "
                "and must stay out of the database?"
            ),
            criteria={
                "company_extract_summary": (
                    "One attribute row per producer extract (counts, bbox, extract path) "
                    "plus one row per neighbor holder. No polygons."
                ),
                "per_title_skip_polygons": (
                    "One row per GESTIM title with attribute fields only. Requires local "
                    "GeoJSON extracts; skip if those files are missing."
                ),
                "defer_claims": "Skip claims this session; companies and tickers only.",
            },
        ),
        "company_key": Choice(
            instructions="How should company identity be keyed in the first schema?",
            criteria={
                "slug_union": (
                    "One companies table keyed by mines/mcap/claims-map slug; tickers "
                    "in a company_tickers link table."
                ),
                "ticker_first": "Tickers as the primary entity; companies as aliases on tickers.",
                "split_sources": (
                    "Keep claims-map companies, insider companies, and mines issuers as "
                    "separate tables joined later."
                ),
            },
        ),
        "pilot_calc": Choice(
            instructions=(
                "Which single first trade calc is most useful on top of "
                "claims-companies-tickers for a static STOCK Act PWA?"
            ),
            criteria={
                "size_vs_cap": (
                    "Politician STOCK Act amount-band midpoint divided by issuer market "
                    "cap, in basis points. Flag unusually large prints."
                ),
                "cluster_buys": (
                    "Count politician purchases in mining tickers that have a claims pack."
                ),
                "insider_vs_pol": (
                    "Same ticker/day overlap between insider Form 4/SEDI and politician PTR."
                ),
            },
        ),
    }


def link_questions():
    from typesafe_sdk import Noul, Score

    return {
        "link_state": Score(
            instructions=(
                "How do the claims-map entity and the catalog issuer relate as companies?"
            ),
            criteria=LINK_LEVELS,
        ),
        "same_name": Noul(
            instructions="Do the two entities state the same company name (allowing legal suffixes)?",
        ),
        "holder_is_vehicle": Noul(
            instructions=(
                "Is `claims_entity.holder` a title-holding subsidiary or acquisition vehicle "
                "for `catalog_issuer` rather than a different listed company?"
            ),
        ),
    }


def calc_questions():
    from typesafe_sdk import Choice, Noul

    return {
        "flag": Choice(
            instructions=(
                "Given this politician STOCK Act trade versus the issuer market cap, "
                "which flag should the calc store?"
            ),
            criteria={
                "typical": (
                    "The amount-band midpoint is a normal disclosure size relative to this issuer."
                ),
                "large_vs_cap": (
                    "The midpoint is unusually large relative to the issuer market cap."
                ),
                "band_incoherent": (
                    "The disclosed band does not make sense for this issuer (for example a "
                    "multi-million band on a tiny cap, or missing/broken numbers)."
                ),
                "skip": "Too little information (no cap, no ticker, or no amount) to judge.",
            },
        ),
        "is_anomaly": Noul(
            instructions=(
                "Should an analyst treat this print as an anomaly worth a closer look "
                "because of size versus issuer cap, not because of politics?"
            ),
            criteria={
                "true": "Size versus cap is unusual enough to review.",
                "false": "Ordinary STOCK Act band for this issuer.",
            },
        ),
    }
