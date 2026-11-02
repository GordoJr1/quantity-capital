#!/usr/bin/env python3
"""Apply claims/links/extract-retags.json role overrides to claims extracts.

build_on_bc_extracts.py calls apply_retags() on every regenerated extract, so
mis-tagged holders (shared-word matches) stay role=neighbor. CLI:
  python scripts/claims_extract_retags.py --check   # exit 1 if a committed file disagrees
  python scripts/claims_extract_retags.py --write   # rewrite files that disagree
Touches only claims/*.geojson; never qc.sqlite.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RULES = ROOT / "claims" / "links" / "extract-retags.json"


def norm(value: str | None) -> str:
    return " ".join((value or "").lower().split())


def load_rules(path: Path = DEFAULT_RULES) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = {}
    for name, rule in (data.get("files") or {}).items():
        pat = rule.get("focus_only_holder_pattern")
        rules[name] = {
            "pattern": re.compile(pat) if pat else None,
            "neighbors": {norm(h) for h in rule.get("neighbor_holders") or []},
        }
    return rules


def should_be_neighbor(rule: dict, holder: str | None) -> bool:
    h = norm(holder)
    if h in rule["neighbors"]:
        return True
    return bool(rule["pattern"] is not None and not rule["pattern"].search(h))


def apply_retags(file_name: str, features: list[dict], rules: dict | None = None) -> int:
    """Set role=neighbor on focus features the rules say belong to another holder. Returns count."""
    rules = load_rules() if rules is None else rules
    rule = rules.get(file_name)
    if not rule:
        return 0
    changed = 0
    for feat in features:
        props = feat.setdefault("properties", {})
        if props.get("role") == "neighbor":
            continue
        if should_be_neighbor(rule, props.get("holder")):
            props["role"] = "neighbor"
            changed += 1
    return changed


def dump_extract(data: dict) -> str:
    """Serialize an extract in the committed format (json.dumps defaults, no trailing newline)."""
    return json.dumps(data, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--write", action="store_true")
    ap.add_argument("--claims-dir", type=Path, default=ROOT / "claims")
    a = ap.parse_args(argv)
    rules = load_rules(a.claims_dir / "links" / "extract-retags.json")
    bad = 0
    for name in sorted(rules):
        path = a.claims_dir / name
        if not path.exists():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        n = apply_retags(name, data.get("features") or [], rules)
        if n:
            bad += 1
            print(f"{name}: {n} focus features should be neighbor")
            if a.write:
                path.write_text(dump_extract(data), encoding="utf-8")
    return 1 if (bad and a.check) else 0


if __name__ == "__main__":
    sys.exit(main())
