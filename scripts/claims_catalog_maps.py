#!/usr/bin/env python3
"""Re-apply curated maps to claims/companies.json after a catalog rebuild.

The catalog's counts come from qc.sqlite linked parties (via the off-repo
collector / scripts/qc_sqlite). A rebuild there can bring back:
  * beta ids that were renamed (DENIED_BETA_IDS in build_canada_owner_beta_shells.py, PR #246), and
  * per-extract counts that include titles claims/links/extract-retags.json moved to neighbor.
apply_catalog_maps() fixes both in memory; merge_claims_insider_universe.py calls it
right after sync_catalog_pins(), on every post-collector run. Touches only JSON; never qc.sqlite.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import claims_extract_retags  # noqa: E402

KINDS = (("quebec", "extract"), ("ontario", "ontario_extract"), ("bc", "bc_extract"))


def denied_ids() -> dict[str, str]:
    """Old beta id -> replacement id. Empty until PR #246 adds DENIED_BETA_IDS."""
    try:
        import build_canada_owner_beta_shells as shells  # noqa: WPS433
    except Exception:  # pragma: no cover - import side effects in odd envs
        return {}
    return dict(getattr(shells, "DENIED_BETA_IDS", {}) or {})


# Beta-profile commodities with no hard-rock activity. An issuer with one of these
# and zero focus titles gets no extracts (black-stone-minerals: US oil & gas
# royalty MLP whose extracts were all shared-word mis-tags, PR #253).
NON_HARDROCK_COMMODITIES = frozenset({"oil & gas"})


def issuer_commodity(root: Path, cid: str) -> str | None:
    path = root / "beta" / f"{cid}.json"
    if not path.is_file():
        return None
    try:
        issuer = json.loads(path.read_text(encoding="utf-8")).get("issuer") or {}
    except (OSError, ValueError):
        return None
    value = issuer.get("commodity")
    return str(value).strip().lower() if value else None


def no_hardrock_guard(row: dict, root: Path) -> list[str]:
    """Drop extracts from a non-hard-rock issuer with no focus titles. Mutates row."""
    keys = [key for _, key in KINDS if row.get(key)]
    if not keys or int(row.get("claim_count") or 0) != 0:
        return []
    if issuer_commodity(root, str(row.get("id") or "")) not in NON_HARDROCK_COMMODITIES:
        return []
    for kind, key in KINDS:
        row[key] = None
        if f"{kind}_count" in row:
            row[f"{kind}_count"] = 0
        if f"{kind}_bbox" in row:
            row[f"{kind}_bbox"] = None
    return [f"{row.get('id')}: dropped extracts {keys} (no hard-rock activity, 0 focus titles)"]


def bbox_of(features: list[dict]):
    xs: list[float] = []
    ys: list[float] = []

    def walk(c):
        if isinstance(c, (int, float)) or not c:
            return
        if isinstance(c[0], (int, float)) and len(c) >= 2:
            xs.append(c[0])
            ys.append(c[1])
            return
        for x in c:
            walk(x)

    for f in features:
        walk((f.get("geometry") or {}).get("coordinates"))
    return [min(xs), min(ys), max(xs), max(ys)] if xs else None


def union(boxes):
    boxes = [b for b in boxes if b]
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)]


def apply_catalog_maps(catalog: dict, root: Path, denied: dict[str, str] | None = None,
                       rules: dict | None = None) -> list[str]:
    """Mutate catalog in place. Returns human-readable changes (empty when already clean)."""
    denied = denied_ids() if denied is None else denied
    rules = claims_extract_retags.load_rules(root / "claims" / "links" / "extract-retags.json") if rules is None else rules
    changes: list[str] = []
    rows = catalog.setdefault("companies", [])
    ids = {r.get("id") for r in rows}
    kept = []
    for row in rows:
        cid = row.get("id")
        if cid in denied:
            new = denied[cid]
            if new in ids:
                changes.append(f"dropped denied catalog row {cid} ({new} exists)")
                continue
            row["id"] = new
            ids.add(new)
            changes.append(f"renamed denied catalog row {cid} -> {new}")
        kept.append(row)
    rows[:] = kept

    for row in rows:
        touched = False
        for kind, key in KINDS:
            rel = row.get(key)
            if not rel or Path(rel).name not in rules:
                continue
            path = root / rel
            if not path.is_file():
                continue
            feats = json.loads(path.read_text(encoding="utf-8")).get("features") or []
            claims_extract_retags.apply_retags(Path(rel).name, feats, rules)
            focus = [f for f in feats if (f.get("properties") or {}).get("role") != "neighbor"]
            count, box = len(focus), bbox_of(focus)
            if row.get(f"{kind}_count") != count or row.get(f"{kind}_bbox") != box:
                changes.append(f"{row.get('id')}: {kind}_count {row.get(f'{kind}_count')} -> {count} (extract retags)")
                row[f"{kind}_count"], row[f"{kind}_bbox"] = count, box
                touched = True
        if touched:
            total = sum(int(row.get(f"{k}_count") or 0) for k, _ in KINDS)
            if row.get("claim_count") != total:
                changes.append(f"{row.get('id')}: claim_count {row.get('claim_count')} -> {total}")
                row["claim_count"] = total
            if "bbox" in row:
                row["bbox"] = union([row.get(f"{k}_bbox") for k, _ in KINDS])
        changes.extend(no_hardrock_guard(row, root))
    return changes
